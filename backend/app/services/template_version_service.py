from __future__ import annotations

import copy
import hashlib
import re
from contextlib import suppress
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.db.models import (
    JobArtifact,
    JobInput,
    OutputLayoutTemplateRevision,
    ProcessingJob,
    TemplateDefinitionRevision,
    TemplateVersion,
)
from app.db.session import Database
from app.domain.generic_template_mapping import generic_field_locations
from app.domain.template_config import parse_template_config
from app.domain.template_definition import TemplateDefinition
from app.layout_analysis.models import LayoutDefinition
from app.repositories.template_repository import TemplateRepository
from app.services.template_field_identity import bind, identities
from app.services.template_mutation import begin_immediate, template_mutation_lock
from app.storage.immutable_artifact import publish_config
from app.storage.local_storage import LocalStorageService


def remap(value: Any, replacements: dict[str, str], reference: bool = False) -> Any:
    """Rewrite exact references, excluding user text and semantic/output paths."""
    if isinstance(value, list):
        return [remap(item, replacements, reference) for item in value]
    if isinstance(value, dict):
        return {
            key: (
                copy.deepcopy(item)
                if key
                in {
                    "text",
                    "label",
                    "name",
                    "title",
                    "displayName",
                    "value",
                    "literal",
                    "printedLabelText",
                    "fieldLabel",
                    "targetPath",
                    "collectionPath",
                    "recordKeyValue",
                }
                else remap(
                    item,
                    replacements,
                    key == "id"
                    or key.endswith(("Id", "Ids", "Path"))
                    or key
                    in {
                        "left",
                        "right",
                        "above",
                        "below",
                        "nearestLeft",
                        "nearestRight",
                        "nearestAbove",
                        "nearestBelow",
                        "sameRowCells",
                        "sameColumnCells",
                        "containedCells",
                        "containingCell",
                    },
                )
            )
            for key, item in value.items()
        }
    return replacements.get(value, value) if reference and isinstance(value, str) else value


def fork_layout(raw: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
    replacements: dict[str, str] = {}

    def visit(value: Any) -> None:
        if isinstance(value, list):
            for item in value:
                visit(item)
        elif isinstance(value, dict):
            identity = value.get("id")
            if isinstance(identity, str) and identity not in replacements:
                # XLSX coordinates are output addresses, not physical identity.
                coordinate = re.match(r"^xlsx_([A-Z]{1,3}[1-9]\d*)(?:$|_)", identity)
                prefix = f"xlsx_{coordinate[1]}_" if coordinate else "c_"
                replacements[identity] = prefix + uuid4().hex[:20]
            for item in value.values():
                visit(item)

    visit(raw)
    return remap(raw, replacements), replacements


_VERSION_BOUND_QUALITY_KEYS = {
    "candidateReadiness",
    "activationCertificate",
    "environmentQualificationCertificate",
    "environmentQualified",
    "geometryQualificationCertificate",
    "geometryQualified",
    "productionQualified",
    "qualification",
    "qualified",
    "referenceQualification",
    "rendererEnvironmentQualified",
}


def without_current_qualification(raw: dict[str, Any]) -> dict[str, Any]:
    """Keep authoring state while requiring the fork to prepare and qualify itself."""
    value = copy.deepcopy(raw)
    for key in _VERSION_BOUND_QUALITY_KEYS:
        value.pop(key, None)
    plan = value.get("printLayout")
    if isinstance(plan, dict):
        for key in (
            "environmentEvidence",
            "geometryQualification",
            "preparedArtifactGraphSha256",
            "printFiles",
            "qualification",
            "referenceArtifacts",
            "rendererEvidence",
        ):
            plan.pop(key, None)
        for page in plan.get("pages", []):
            if isinstance(page, dict):
                page.pop("rendererEvidence", None)
    return value


class TemplateVersionService:
    def __init__(self, database: Database, storage: LocalStorageService) -> None:
        self.database = database
        self.storage = storage

    @staticmethod
    def _conflict(code: str, detail: str) -> AppError:
        return AppError(status=409, code=code, title="Template draft conflict", detail=detail)

    def _check_revision(self, version: TemplateVersion, expected: str | None) -> None:
        if version.quality_json.get("printLayout") is None:
            return
        if expected is None:
            raise self._conflict(
                "TEMPLATE_VERSION_REVISION_REQUIRED",
                "複数ページ版の編集には現在のrevisionが必要です。",
            )
        from app.services.print_layout_service import PrintLayoutService

        if PrintLayoutService.revision(version) != expected:
            raise self._conflict(
                "TEMPLATE_VERSION_CONFLICT",
                "更新元の内容が変更されています。再取得してから編集してください。",
            )

    def _artifact_bytes(
        self,
        artifact: JobArtifact,
        *,
        owner_job: ProcessingJob,
    ) -> tuple[bytes, str]:
        if (
            artifact.job_id != owner_job.id
            or artifact.attempt_number != owner_job.attempt_number
        ):
            raise self._conflict(
                "TEMPLATE_ARTIFACT_OWNERSHIP_MISMATCH",
                "複製元artifactのjob/attempt所有関係が一致しません。",
            )
        try:
            content = self.storage.read_bytes(artifact.relative_path)
        except OSError as exc:
            raise self._conflict(
                "TEMPLATE_ARTIFACT_SOURCE_MISSING",
                "複製元artifactが保存先にありません。",
            ) from exc
        actual = hashlib.sha256(content).hexdigest()
        if actual != artifact.sha256:
            raise self._conflict(
                "TEMPLATE_ARTIFACT_SOURCE_CHANGED",
                "複製元artifactの保存SHAと実ファイルSHAが一致しません。",
            )
        return content, actual

    def _source_identity(
        self,
        session: Session,
        version: TemplateVersion,
    ) -> tuple[str, str]:
        job = session.get(ProcessingJob, version.source_job_id)
        if job is None:
            raise self._conflict("SOURCE_IMAGE_NOT_READY", "元ジョブがありません。")
        if version.mode == "SPREADSHEET_TEMPLATE":
            artifact = session.scalar(
                select(JobArtifact).where(
                    JobArtifact.job_id == job.id,
                    JobArtifact.attempt_number == job.attempt_number,
                    JobArtifact.artifact_name == "sourceWorkbook",
                )
            )
            if artifact is None:
                raise self._conflict(
                    "TEMPLATE_SOURCE_WORKBOOK_MISSING", "版所有の元XLSXがありません。"
                )
        else:
            artifact = session.get(JobArtifact, version.clean_template_artifact_id)
            if artifact is None:
                raise self._conflict("SOURCE_IMAGE_NOT_READY", "版所有の元画像がありません。")
        _, actual = self._artifact_bytes(artifact, owner_job=job)
        plan = version.quality_json.get("printLayout")
        declared = plan.get("sourceSha256") if isinstance(plan, dict) else None
        if declared is not None and declared != actual:
            raise self._conflict(
                "TEMPLATE_DRAFT_SOURCE_MISMATCH",
                "版のsource bindingと所有する元XLSXのSHAが一致しません。",
            )
        return artifact.id, actual

    def _validate_resume(
        self,
        session: Session,
        source: TemplateVersion,
        draft: TemplateVersion,
    ) -> None:
        if (
            draft.status != "DRAFT"
            or draft.template_id != source.template_id
            or draft.mode != source.mode
        ):
            raise self._conflict(
                "TEMPLATE_DRAFT_LINEAGE_MISMATCH",
                "既存DRAFTの系列、mode、statusが更新元と一致しません。",
            )
        if (
            draft.quality_json.get("forkParentVersionId") != source.id
            or draft.version_number <= source.version_number
            or draft.source_job_id == source.source_job_id
        ):
            raise self._conflict(
                "TEMPLATE_DRAFT_LINEAGE_MISMATCH",
                "既存DRAFTは指定した更新元versionから派生した版ではありません。",
            )
        draft_job = session.get(ProcessingJob, draft.source_job_id)
        snapshot = draft_job.config_snapshot_json if draft_job is not None else {}
        if (
            draft_job is None
            or draft_job.template_version_id != draft.id
            or snapshot.get("templateId") != source.template_id
            or snapshot.get("templateVersionId") != draft.id
            or snapshot.get("forkParentVersionId") != source.id
        ):
            raise self._conflict(
                "TEMPLATE_DRAFT_LINEAGE_MISMATCH",
                "既存DRAFTのjob snapshotとversion lineageが一致しません。",
            )
        _, source_sha = self._source_identity(session, source)
        _, draft_sha = self._source_identity(session, draft)
        if source_sha != draft_sha or any(
            source.quality_json.get(key) != draft.quality_json.get(key)
            for key in ("sourceFileName", "sourceSheetName")
        ):
            raise self._conflict(
                "TEMPLATE_DRAFT_SOURCE_MISMATCH",
                "既存DRAFTは更新元と同じsource workbookを所有していません。",
            )

    def _write_verified(
        self,
        target: str,
        content: bytes,
        created_paths: list[str],
    ) -> tuple[str, str]:
        normalized = Path(target).as_posix()
        if normalized in created_paths or self.storage.exists(normalized):
            raise self._conflict(
                "TEMPLATE_ARTIFACT_DESTINATION_COLLISION",
                "artifact複製先が既存pathと衝突しました。",
            )
        created_paths.append(normalized)
        stored = self.storage.write_bytes(normalized, content)
        stored = Path(stored).as_posix()
        if stored != normalized:
            created_paths.append(stored)
            raise self._conflict(
                "TEMPLATE_ARTIFACT_DESTINATION_COLLISION",
                "artifact保存先が要求した一意pathと一致しません。",
            )
        expected = hashlib.sha256(content).hexdigest()
        try:
            actual = hashlib.sha256(self.storage.read_bytes(stored)).hexdigest()
        except OSError as exc:
            raise self._conflict(
                "TEMPLATE_ARTIFACT_COPY_CHANGED",
                "複製後artifactを再読取できません。",
            ) from exc
        if actual != expected:
            raise self._conflict(
                "TEMPLATE_ARTIFACT_COPY_CHANGED",
                "複製元と複製先の内容SHAが一致しません。",
            )
        return stored, actual

    def edit(
        self,
        version_id: str,
        *,
        expected_revision: str | None = None,
    ) -> TemplateVersion:
        created_paths: list[str] = []
        try:
            return self._edit_locked(version_id, expected_revision, created_paths)
        except Exception:
            for path in reversed(created_paths):
                with suppress(OSError):
                    self.storage.delete_file(path)
            raise

    def _edit_locked(
        self,
        version_id: str,
        expected_revision: str | None,
        created_paths: list[str],
    ) -> TemplateVersion:
        with template_mutation_lock(), self.database.session() as session:
            begin_immediate(session)
            repository = TemplateRepository(session)
            source = repository.get_version(version_id)
            if source is None:
                raise AppError(
                    status=404,
                    code="TEMPLATE_NOT_FOUND",
                    title="Template not found",
                    detail="テンプレート版がありません。",
                )
            template = repository.get_template(source.template_id)
            if template is None or template.archived_at is not None:
                raise AppError(
                    status=409,
                    code="INVALID_STATE_TRANSITION",
                    title="Template is archived",
                    detail="削除済みテンプレートです。",
                )
            drafts = [v for v in repository.list_versions(template.id) if v.status == "DRAFT"]
            self._check_revision(source, expected_revision)
            if len(drafts) > 1:
                raise AppError(
                    status=409,
                    code="TEMPLATE_DRAFT_AMBIGUOUS",
                    title="Multiple drafts exist",
                    detail="複数の下書きがあります。履歴から編集対象を選択してください。",
                )
            if source.status == "DRAFT":
                parent_id = source.quality_json.get("forkParentVersionId")
                if isinstance(parent_id, str):
                    parent = repository.get_version(parent_id)
                    if parent is None:
                        raise self._conflict(
                            "TEMPLATE_DRAFT_LINEAGE_MISMATCH",
                            "DRAFTの更新元versionがありません。",
                        )
                    self._validate_resume(session, parent, source)
                else:
                    self._source_identity(session, source)
                return source
            if source.id != template.active_version_id or source.status != "ACTIVE":
                raise AppError(
                    status=409,
                    code="INVALID_STATE_TRANSITION",
                    title="Active template required",
                    detail="現在の有効版から編集してください。",
                )
            if drafts:
                self._validate_resume(session, source, drafts[0])
                return drafts[0]
            source_job = session.get(ProcessingJob, source.source_job_id)
            if source_job is None:
                raise AppError(
                    status=409,
                    code="SOURCE_IMAGE_NOT_READY",
                    title="Source missing",
                    detail="元ジョブがありません。",
                )
            self._source_identity(session, source)
            config = parse_template_config(
                self.storage.read_bytes(source.config_relative_path).decode("utf-8")
            )
            layout, replacements = fork_layout(source.layout_json)
            LayoutDefinition.model_validate(layout)
            for region in config.regions:
                replacements.setdefault(region.region_id, "r_" + uuid4().hex)
            for mark in config.marks:
                replacements.setdefault(mark.mark_id, "k_" + uuid4().hex)
            for mapping in config.field_mappings:
                for option in mapping.mark_options:
                    replacements.setdefault(option.id, "o_" + uuid4().hex[:20])
                for subfield in mapping.composite_subfields:
                    replacements.setdefault(subfield.id, "o_" + uuid4().hex[:20])
            mapping_replacements: dict[str, str] = {}
            for cell in source.layout_json.get("cells", []):
                old_cell, new_cell = cell["id"], replacements[cell["id"]]
                mapping_replacements[f"layout_{old_cell}"] = f"layout_{new_cell}"
                for mark in cell.get("markSets", []):
                    mapping_replacements[f"layout_{old_cell}_{mark['id']}"[:100]] = (
                        f"layout_{new_cell}_{replacements[mark['id']]}"[:100]
                    )
            definition_copies: dict[str, dict[str, Any]] = {}
            definition_rows = list(
                session.scalars(
                    select(TemplateDefinitionRevision).where(
                        TemplateDefinitionRevision.template_version_id == source.id,
                    )
                )
            )
            generic_targets: dict[str, str] = {}
            for row in definition_rows:
                if "standaloneFields" not in row.definition_json:
                    continue
                _, definition_ids = fork_layout(row.definition_json)
                definition_ids.update(replacements)
                replacements.update(definition_ids)
                old_definition = TemplateDefinition.model_validate(row.definition_json)
                copied = remap(row.definition_json, replacements)
                new_definition = TemplateDefinition.model_validate(copied)
                new_locations = {
                    (
                        loc.field.id,
                        loc.section.id if loc.section else None,
                        loc.record.id if loc.record else None,
                    ): loc.mapping_key
                    for loc in generic_field_locations(new_definition)
                }
                for loc in generic_field_locations(old_definition):
                    new_key = new_locations[
                        (
                            replacements.get(loc.field.id, loc.field.id),
                            replacements.get(loc.section.id, loc.section.id)
                            if loc.section
                            else None,
                            replacements.get(loc.record.id, loc.record.id) if loc.record else None,
                        )
                    ]
                    mapping_replacements[loc.mapping_key] = new_key
                    generic_targets[f"fields.{loc.mapping_key}"] = f"fields.{new_key}"
                definition_copies[row.id] = copied
            for m in config.field_mappings:
                if m.mapping_id not in mapping_replacements:
                    mapping_replacements[m.mapping_id] = "m_" + uuid4().hex
            replacements.update(mapping_replacements)
            next_number = max(v.version_number for v in repository.list_versions(template.id)) + 1
            new_id, job_id = str(uuid4()), str(uuid4())
            base = f"templates/{template.id}/versions/{next_number}"
            config_data = remap(config.model_dump(mode="json", by_alias=True), replacements)

            def update_generic_targets(value: Any, reference: bool = False) -> Any:
                if isinstance(value, dict):
                    return {
                        k: update_generic_targets(v, k in {"targetPath", "fieldPaths"})
                        for k, v in value.items()
                    }
                if isinstance(value, list):
                    return [update_generic_targets(v, reference) for v in value]
                return (
                    generic_targets.get(value, value)
                    if reference and isinstance(value, str)
                    else value
                )

            config_data = update_generic_targets(config_data)
            layout = update_generic_targets(layout)
            config_data["templateVersion"] = next_number
            config_path = f"{base}/template.yaml"
            job = ProcessingJob(
                id=job_id,
                job_type=source_job.job_type,
                status="REVIEW_REQUIRED",
                mode=source_job.mode,
                request_hash=source_job.request_hash,
                config_snapshot_json={},
                attempt_number=1,
                progress_stage="TEMPLATE_DRAFT_READY",
                progress_percent=100,
            )
            session.add(job)
            session.flush()
            path_map: dict[str, str] = {}
            artifact_map: dict[str, str] = {}
            artifacts = list(
                session.scalars(
                    select(JobArtifact).where(
                        JobArtifact.job_id == source_job.id,
                        JobArtifact.attempt_number == source_job.attempt_number,
                    )
                )
            )
            for artifact in artifacts:
                clone_id = str(uuid4())
                content, source_sha = self._artifact_bytes(artifact, owner_job=source_job)
                target = f"jobs/{job_id}/1/{clone_id}{Path(artifact.relative_path).suffix}"
                target, destination_sha = self._write_verified(
                    target, content, created_paths
                )
                if destination_sha != source_sha:
                    raise self._conflict(
                        "TEMPLATE_ARTIFACT_COPY_CHANGED",
                        "複製元と複製先のartifact SHAが一致しません。",
                    )
                session.add(
                    JobArtifact(
                        id=clone_id,
                        job_id=job_id,
                        attempt_number=1,
                        artifact_name=artifact.artifact_name,
                        media_type=artifact.media_type,
                        relative_path=target,
                        sha256=source_sha,
                        width=artifact.width,
                        height=artifact.height,
                    )
                )
                artifact_map[artifact.id] = clone_id
                path_map[artifact.relative_path] = target
            for item in session.scalars(select(JobInput).where(JobInput.job_id == source_job.id)):
                target = f"uploads/{job_id}/{item.input_index}_{Path(item.relative_path).name}"
                content = self.storage.read_bytes(item.relative_path)
                if hashlib.sha256(content).hexdigest() != item.sha256:
                    raise self._conflict(
                        "TEMPLATE_ARTIFACT_SOURCE_CHANGED",
                        "複製元inputの保存SHAと実ファイルSHAが一致しません。",
                    )
                target, destination_sha = self._write_verified(
                    target, content, created_paths
                )
                if destination_sha != item.sha256:
                    raise self._conflict(
                        "TEMPLATE_ARTIFACT_COPY_CHANGED",
                        "複製元と複製先のinput SHAが一致しません。",
                    )
                path_map[item.relative_path] = target
                session.add(
                    JobInput(
                        job_id=job_id,
                        input_index=item.input_index,
                        media_type=item.media_type,
                        format=item.format,
                        width=item.width,
                        height=item.height,
                        file_bytes=item.file_bytes,
                        sha256=item.sha256,
                        relative_path=target,
                        content_bytes=item.content_bytes,
                    )
                )
            quality = without_current_qualification(
                remap(source.quality_json, {**replacements, **artifact_map, **path_map})
            )
            if quality.get("printLayout") is not None:
                from app.services.print_layout_service import digest

                quality["printLayout"]["layoutSha256"] = digest(layout)
            # Copy source files referenced by quality but not by an artifact.
            for key in ("spreadsheetSourcePath", "spreadsheetPrintablePdfPath"):
                path = quality.get(key)
                if isinstance(path, str) and path not in path_map.values():
                    target = f"{base}/inherited/{key}/{Path(path).name}"
                    target, _ = self._write_verified(
                        target, self.storage.read_bytes(path), created_paths
                    )
                    quality[key] = target
            quality.update({"forkParentVersionId": source.id, "layoutVerified": False})
            quality["protectedPhysicalIds"] = sorted(
                {
                    *source.quality_json.get("protectedPhysicalIds", []),
                    *replacements.keys(),
                }
            )
            quality.pop("layoutVerificationFingerprint", None)
            quality.pop("editedMaskSha256", None)
            quality.pop("editedMaskArtifactId", None)
            if source.clean_template_artifact_id not in artifact_map:
                raise AppError(
                    status=409,
                    code="SOURCE_IMAGE_NOT_READY",
                    title="Source artifact missing",
                    detail="元画像を複製できません。",
                )
            config_text = yaml.safe_dump(config_data, allow_unicode=True)
            config_path = publish_config(self.storage, config_text)
            version = TemplateVersion(
                id=new_id,
                template_id=template.id,
                version_number=next_number,
                mode=source.mode,
                status="DRAFT",
                canonical_width=source.canonical_width,
                canonical_height=source.canonical_height,
                source_job_id=job_id,
                clean_template_artifact_id=artifact_map[source.clean_template_artifact_id],
                mask_artifact_id=artifact_map.get(source.mask_artifact_id or ""),
                config_relative_path=config_path,
                layout_json=layout,
                quality_json=quality,
            )
            session.flush()
            session.add(version)
            session.flush()
            source_identities = identities(session, source, config.field_mappings)
            for key, logical_id in source_identities.items():
                bind(session, source, key, logical_id)
                mapping_id, separator, subfield_id = key.partition(":")
                new_key = mapping_replacements[mapping_id]
                if separator:
                    new_key += ":" + replacements.get(subfield_id, subfield_id)
                bind(session, version, new_key, logical_id)
            for model, json_column in (
                (TemplateDefinitionRevision, "definition_json"),
                (OutputLayoutTemplateRevision, "layout_json"),
            ):
                for stored_revision in session.scalars(
                    select(model).where(model.template_version_id == source.id)
                ):
                    revision_id = cast(
                        TemplateDefinitionRevision | OutputLayoutTemplateRevision, stored_revision
                    ).id
                    values = {
                        c.name: copy.deepcopy(getattr(stored_revision, c.name))
                        for c in model.__table__.columns
                        if c.name not in {"id", "created_at"}
                    }
                    values["template_version_id"] = new_id
                    values[json_column] = update_generic_targets(
                        remap(
                            definition_copies.get(revision_id, values[json_column]),
                            {
                                **replacements,
                                source.id: new_id,
                                source_job.id: job_id,
                            },
                        )
                    )
                    if "source_job_id" in values:
                        values["source_job_id"] = job_id
                    session.add(model(**values))
            job.template_version_id = new_id
            job.config_snapshot_json = {
                **without_current_qualification(
                    remap(source_job.config_snapshot_json, {**path_map, **replacements})
                ),
                "templateId": template.id,
                "templateVersionId": new_id,
                "forkParentVersionId": source.id,
            }
            job.config_snapshot_json.pop("reprocessTemplateVersionId", None)
            if quality.get("printLayout") is not None:
                job.config_snapshot_json["printLayout"] = copy.deepcopy(quality["printLayout"])
            session.commit()
            return version
