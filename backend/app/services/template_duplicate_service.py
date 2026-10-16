"""Create an independently owned editable copy of a published template."""

from __future__ import annotations

import copy
import hashlib
from contextlib import suppress
from pathlib import Path
from typing import Any
from uuid import uuid4

import yaml
from sqlalchemy import select

from app.core.errors import AppError
from app.db.models import (
    JobArtifact,
    JobInput,
    OutputLayoutTemplateRevision,
    ProcessingJob,
    Template,
    TemplateCandidateBinding,
    TemplateCandidateSet,
    TemplateDefinitionRevision,
    TemplateVersion,
)
from app.domain.generic_template_mapping import generic_field_locations
from app.domain.template_config import TemplateConfig, parse_template_config
from app.domain.template_definition import TemplateDefinition
from app.layout_analysis.models import LayoutDefinition
from app.repositories.template_repository import TemplateRepository
from app.services.template_field_identity import bind, identities
from app.services.template_mutation import begin_immediate, template_mutation_lock
from app.services.template_version_service import (
    TemplateVersionService,
    fork_layout,
    remap,
    without_current_qualification,
)
from app.storage.immutable_artifact import publish_config


def _generic_targets(value: Any, replacements: dict[str, str], reference: bool = False) -> Any:
    if isinstance(value, dict):
        return {
            key: _generic_targets(item, replacements, key in {"targetPath", "fieldPaths"})
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_generic_targets(item, replacements, reference) for item in value]
    return replacements.get(value, value) if reference and isinstance(value, str) else value


class TemplateDuplicateService(TemplateVersionService):
    def duplicate(
        self, version_id: str, name: str, *, activate: bool = False,
    ) -> tuple[str, str]:
        cleaned_name = name.strip()
        if not cleaned_name or len(cleaned_name) > 200:
            raise AppError(
                status=422, code="INVALID_TEMPLATE_NAME", title="Invalid template name",
                detail="テンプレート名は1文字以上200文字以内で指定してください。",
            )
        created_paths: list[str] = []
        try:
            template_id, copied_version_id = self._duplicate_locked(
                version_id, cleaned_name, created_paths, activate=activate,
            )
        except Exception:
            for path in reversed(created_paths):
                with suppress(OSError):
                    self.storage.delete_file(path)
            raise
        if activate:
            try:
                self._activate_copy(copied_version_id)
            except Exception:
                with self.database.session() as session:
                    published = session.get(TemplateVersion, copied_version_id)
                    owner = session.get(Template, template_id)
                    if (
                        published is not None and owner is not None
                        and published.status == "ACTIVE"
                        and owner.active_version_id == copied_version_id
                    ):
                        return template_id, copied_version_id
                self._discard_unpublished_copy(template_id, copied_version_id)
                raise
        return template_id, copied_version_id

    def _discard_unpublished_copy(self, template_id: str, version_id: str) -> None:
        """Remove only this newly created, still-unpublished copy after validation fails."""
        with template_mutation_lock(), self.database.session() as session:
            begin_immediate(session)
            template = session.get(Template, template_id)
            version = session.get(TemplateVersion, version_id)
            if (
                template is None or version is None
                or version.template_id != template_id
                or template.active_version_id is not None
                or version.status != "DRAFT"
            ):
                return
            job = session.get(ProcessingJob, version.source_job_id)
            if job is None or job.template_version_id != version_id:
                return
            paths = {version.config_relative_path}
            paths.update(item.relative_path for item in session.scalars(
                select(JobArtifact).where(JobArtifact.job_id == job.id)
            ))
            paths.update(item.relative_path for item in session.scalars(
                select(JobInput).where(JobInput.job_id == job.id)
            ))
            for key in ("spreadsheetSourcePath", "spreadsheetPrintablePdfPath"):
                path = version.quality_json.get(key)
                if isinstance(path, str):
                    paths.add(path)
            allowed = (
                f"jobs/{job.id}/", f"uploads/{job.id}/",
                f"artifacts/{job.id}/", f"templates/{template_id}/",
            )
            paths = {path for path in paths if path.startswith(allowed)}
            session.delete(version)
            session.flush()
            session.delete(template)
            session.flush()
            session.delete(job)
            session.commit()
        for path in paths:
            with suppress(OSError):
                self.storage.delete_file(path)

    def _activate_copy(self, version_id: str) -> None:
        from app.api.v1.routes.templates import _mapping_validation
        from app.services.candidate_readiness_service import CandidateReadinessService
        from app.services.layout_service import LayoutService
        from app.services.print_layout_service import PrintLayoutService
        from app.services.reference_qualification_service import ReferenceQualificationService
        from app.services.template_management_service import TemplateManagementService

        with self.database.session() as session:
            version = TemplateRepository(session).get_version(version_id)
            if version is None:
                raise self._conflict("TEMPLATE_NOT_FOUND", "複製先のテンプレートがありません。")
            paged = version.quality_json.get("printLayout") is not None
            revision = PrintLayoutService.revision(version) if paged else None

        if paged:
            assert revision is not None
            CandidateReadinessService(self.database, self.storage).prepare_for_ocr(
                version_id, revision,
            )
        else:
            LayoutService(self.database, self.storage).verify(version_id)
            (_, _, mappings, identity_fields, document_name) = (
                TemplateManagementService(self.database, self.storage)
                .get_mapping_configuration(version_id)
            )
            valid, warnings = _mapping_validation(
                mappings, identity_fields, document_name,
                require_identity=True,
            )
            if not valid:
                raise self._conflict(
                    "TEMPLATE_FIELD_MAPPING_OUT_OF_RANGE", warnings[0],
                )

        with self.database.session() as session:
            TemplateRepository(session).approve(
                version_id,
                expected_revision=revision,
                qualification_service=(
                    ReferenceQualificationService(self.database, self.storage)
                    if paged else None
                ),
            )

    def _duplicate_locked(
        self, version_id: str, name: str, created_paths: list[str], *, activate: bool,
    ) -> tuple[str, str]:
        with template_mutation_lock(), self.database.session() as session:
            begin_immediate(session)
            repository = TemplateRepository(session)
            source = repository.get_version(version_id)
            if source is None:
                raise AppError(
                    status=404, code="TEMPLATE_NOT_FOUND", title="Template not found",
                    detail="複製元のテンプレート版がありません。",
                )
            owner = repository.get_template(source.template_id)
            if (
                owner is None or owner.archived_at is not None
                or owner.active_version_id != source.id or source.status != "ACTIVE"
            ):
                raise AppError(
                    status=409, code="INVALID_STATE_TRANSITION",
                    title="Active template required",
                    detail="現在の有効版から複製してください。",
                )
            if repository.name_exists(name):
                raise repository._name_conflict()
            source_job = session.get(ProcessingJob, source.source_job_id)
            if source_job is None:
                raise self._conflict("SOURCE_IMAGE_NOT_READY", "複製元の準備ジョブがありません。")
            _, source_sha = self._source_identity(session, source)
            source_plan = source.quality_json.get("printLayout")
            if source_plan is not None:
                from app.services.print_layout_service import digest

                if (
                    not isinstance(source_plan, dict)
                    or source_plan.get("layoutSha256") != digest(source.layout_json)
                    or not isinstance(source_plan.get("pages"), list)
                    or not source_plan["pages"]
                ):
                    raise self._conflict(
                        "PRINT_LAYOUT_STALE", "複製元のページ設定が現在のlayoutと一致しません。"
                    )
                for page in source_plan["pages"]:
                    reference = page.get("referenceImage") if isinstance(page, dict) else None
                    if not isinstance(reference, dict) or not isinstance(
                        reference.get("artifactId"), str
                    ):
                        raise self._conflict(
                            "PRINT_REFERENCE_NOT_VALIDATED",
                            "複製元のページ基準画像が保存版と一致しません。",
                        )
                    artifact = session.get(JobArtifact, reference["artifactId"])
                    if (
                        artifact is None
                        or artifact.relative_path != reference.get("relativePath")
                        or artifact.sha256 != reference.get("sha256")
                    ):
                        raise self._conflict(
                            "PRINT_REFERENCE_NOT_VALIDATED",
                            "複製元のページ基準画像が保存版と一致しません。",
                        )
                    self._artifact_bytes(artifact, owner_job=source_job)
            try:
                config = parse_template_config(
                    self.storage.read_bytes(source.config_relative_path).decode("utf-8")
                )
            except (OSError, UnicodeDecodeError, ValueError) as exc:
                raise self._conflict(
                    "TEMPLATE_CONFIG_INVALID", "複製元の設定を検証できません。"
                ) from exc
            if (
                config.template_id != owner.id
                or config.template_version != source.version_number
                or config.canonical_width != source.canonical_width
                or config.canonical_height != source.canonical_height
            ):
                raise self._conflict(
                    "TEMPLATE_CONFIG_MISMATCH", "複製元の設定と版が一致しません。"
                )

            template = Template(name=name, document_type=owner.document_type)
            session.add(template)
            session.flush()
            new_id, job_id = str(uuid4()), str(uuid4())
            base = f"templates/{template.id}/versions/1"
            config_path = f"{base}/template.yaml"

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
            definition_rows = list(session.scalars(
                select(TemplateDefinitionRevision).where(
                    TemplateDefinitionRevision.template_version_id == source.id,
                )
            ))
            definition_copies: dict[str, dict[str, Any]] = {}
            generic_targets: dict[str, str] = {}
            for row in definition_rows:
                if "standaloneFields" not in row.definition_json:
                    continue
                _, definition_ids = fork_layout(row.definition_json)
                definition_ids.update(replacements)
                replacements.update(definition_ids)
                original_definition = TemplateDefinition.model_validate(row.definition_json)
                copied_definition = TemplateDefinition.model_validate(
                    remap(row.definition_json, replacements)
                )
                new_locations = {
                    (
                        location.field.id,
                        location.section.id if location.section else None,
                        location.record.id if location.record else None,
                    ): location.mapping_key
                    for location in generic_field_locations(copied_definition)
                }
                for location in generic_field_locations(original_definition):
                    new_key = new_locations[(
                        replacements.get(location.field.id, location.field.id),
                        replacements.get(location.section.id, location.section.id)
                        if location.section else None,
                        replacements.get(location.record.id, location.record.id)
                        if location.record else None,
                    )]
                    mapping_replacements[location.mapping_key] = new_key
                    generic_targets[f"fields.{location.mapping_key}"] = (
                        f"fields.{new_key}"
                    )
                definition_copies[row.id] = copied_definition.model_dump(
                    mode="json", by_alias=True,
                )
            for mapping in config.field_mappings:
                mapping_replacements.setdefault(mapping.mapping_id, "m_" + uuid4().hex)
            replacements.update(mapping_replacements)
            replacements.update({
                source.id: new_id,
                source_job.id: job_id,
                owner.id: template.id,
            })
            config_data = _generic_targets(
                remap(config.model_dump(mode="json", by_alias=True), replacements),
                generic_targets,
            )
            config_data.update({"templateId": template.id, "templateVersion": 1, "name": name})
            TemplateConfig.model_validate(config_data)
            layout = _generic_targets(layout, generic_targets)

            job = ProcessingJob(
                id=job_id, job_type=source_job.job_type, status="REVIEW_REQUIRED",
                mode=source_job.mode,
                request_hash=hashlib.sha256(f"{template.id}:{new_id}".encode()).hexdigest(),
                config_snapshot_json={}, attempt_number=1,
                progress_stage="TEMPLATE_DRAFT_READY", progress_percent=100,
            )
            session.add(job)
            session.flush()
            path_map: dict[str, str] = {}
            artifact_map: dict[str, str] = {}
            for artifact in session.scalars(select(JobArtifact).where(
                JobArtifact.job_id == source_job.id,
                JobArtifact.attempt_number == source_job.attempt_number,
            )):
                clone_id = str(uuid4())
                content, verified_sha = self._artifact_bytes(artifact, owner_job=source_job)
                target_path, copied_sha = self._write_verified(
                    f"jobs/{job_id}/1/{clone_id}{Path(artifact.relative_path).suffix}",
                    content, created_paths,
                )
                if verified_sha != copied_sha:
                    raise self._conflict(
                        "TEMPLATE_ARTIFACT_COPY_CHANGED", "複製後artifactの内容が一致しません。"
                    )
                session.add(JobArtifact(
                    id=clone_id, job_id=job_id, attempt_number=1,
                    artifact_name=artifact.artifact_name, media_type=artifact.media_type,
                    relative_path=target_path, sha256=copied_sha,
                    width=artifact.width, height=artifact.height,
                ))
                artifact_map[artifact.id] = clone_id
                path_map[artifact.relative_path] = target_path
            for input_row in session.scalars(
                select(JobInput).where(JobInput.job_id == source_job.id)
            ):
                try:
                    content = self.storage.read_bytes(input_row.relative_path)
                except OSError as exc:
                    raise self._conflict(
                        "TEMPLATE_ARTIFACT_SOURCE_MISSING", "複製元inputがありません。"
                    ) from exc
                if hashlib.sha256(content).hexdigest() != input_row.sha256:
                    raise self._conflict(
                        "TEMPLATE_ARTIFACT_SOURCE_CHANGED", "複製元inputの内容が変更されています。"
                    )
                target_path, copied_sha = self._write_verified(
                    f"uploads/{job_id}/{input_row.input_index}_{Path(input_row.relative_path).name}",
                    content, created_paths,
                )
                if copied_sha != input_row.sha256:
                    raise self._conflict(
                        "TEMPLATE_ARTIFACT_COPY_CHANGED", "複製後inputの内容が一致しません。"
                    )
                path_map[input_row.relative_path] = target_path
                session.add(JobInput(
                    job_id=job_id, input_index=input_row.input_index,
                    media_type=input_row.media_type, format=input_row.format,
                    width=input_row.width, height=input_row.height,
                    file_bytes=input_row.file_bytes, sha256=copied_sha,
                    relative_path=target_path, content_bytes=input_row.content_bytes,
                ))
            if source.clean_template_artifact_id not in artifact_map:
                raise self._conflict("SOURCE_IMAGE_NOT_READY", "複製元画像がありません。")
            quality = without_current_qualification(remap(
                source.quality_json, {**replacements, **artifact_map, **path_map},
            ))
            if quality.get("printLayout") is not None:
                from app.services.print_layout_service import digest

                quality["printLayout"]["layoutSha256"] = digest(layout)
                if (
                    activate and isinstance(source_plan, dict)
                    and isinstance(source_plan.get("printFiles"), dict)
                ):
                    # Printed files are immutable bytes owned by the copied job.
                    # Candidate qualification is never copied; the new version
                    # must issue its own readiness record after validation.
                    quality["printLayout"]["printFiles"] = remap(
                        source_plan["printFiles"], {**artifact_map, **path_map},
                    )
            for key in ("spreadsheetSourcePath", "spreadsheetPrintablePdfPath"):
                path = quality.get(key)
                if isinstance(path, str) and path not in path_map.values():
                    try:
                        content = self.storage.read_bytes(path)
                    except OSError as exc:
                        raise self._conflict(
                            "TEMPLATE_ARTIFACT_SOURCE_MISSING", "複製元ファイルがありません。"
                        ) from exc
                    if key == "spreadsheetSourcePath" and (
                        hashlib.sha256(content).hexdigest() != source_sha
                    ):
                        raise self._conflict(
                            "TEMPLATE_DRAFT_SOURCE_MISMATCH",
                            "複製元XLSXと保存済み原本の内容が一致しません。",
                        )
                    copied_path, _ = self._write_verified(
                        f"{base}/inherited/{key}/{Path(path).name}",
                        content, created_paths,
                    )
                    quality[key] = copied_path
            quality["layoutVerified"] = False
            for key in (
                "forkParentVersionId", "protectedPhysicalIds", "layoutVerificationFingerprint",
                "editedMaskSha256", "editedMaskArtifactId",
            ):
                quality.pop(key, None)
            config_text = yaml.safe_dump(config_data, allow_unicode=True)
            config_path = publish_config(self.storage, config_text)
            version = TemplateVersion(
                id=new_id, template_id=template.id, version_number=1,
                mode=source.mode, status="DRAFT",
                canonical_width=source.canonical_width,
                canonical_height=source.canonical_height,
                source_job_id=job_id,
                clean_template_artifact_id=artifact_map[source.clean_template_artifact_id],
                mask_artifact_id=artifact_map.get(source.mask_artifact_id or ""),
                config_relative_path=config_path,
                layout_json=layout, quality_json=quality,
            )
            session.add(version)
            session.flush()
            for key in identities(session, source, config.field_mappings):
                mapping_id, separator, subfield_id = key.partition(":")
                new_key = mapping_replacements[mapping_id]
                if separator:
                    new_key += ":" + replacements.get(subfield_id, subfield_id)
                bind(session, version, new_key, str(uuid4()))
            for stored_revision in definition_rows:
                values = {
                    column.name: copy.deepcopy(getattr(stored_revision, column.name))
                    for column in TemplateDefinitionRevision.__table__.columns
                    if column.name not in {"id", "created_at"}
                }
                values["template_version_id"] = new_id
                definition = _generic_targets(remap(
                    definition_copies.get(stored_revision.id, values["definition_json"]),
                    replacements,
                ), generic_targets)
                definition["templateName"] = name
                values["definition_json"] = definition
                values["validation_json"] = remap(values["validation_json"], replacements)
                session.add(TemplateDefinitionRevision(**values))
            for output_revision in session.scalars(select(OutputLayoutTemplateRevision).where(
                OutputLayoutTemplateRevision.template_version_id == source.id,
            )):
                values = {
                    column.name: copy.deepcopy(getattr(output_revision, column.name))
                    for column in OutputLayoutTemplateRevision.__table__.columns
                    if column.name not in {"id", "created_at"}
                }
                values["template_version_id"] = new_id
                values["source_job_id"] = job_id
                values["layout_json"] = _generic_targets(
                    remap(values["layout_json"], replacements), generic_targets,
                )
                session.add(OutputLayoutTemplateRevision(**values))
            candidate_ids: dict[str, str] = {}
            for source_set in session.scalars(select(TemplateCandidateSet).where(
                TemplateCandidateSet.template_id == owner.id,
            )):
                candidate = TemplateCandidateSet(
                    template_id=template.id, name=source_set.name,
                    normalized_name=source_set.normalized_name,
                    values_json=copy.deepcopy(source_set.values_json),
                    allowed_suffixes_json=copy.deepcopy(source_set.allowed_suffixes_json),
                )
                session.add(candidate)
                session.flush()
                candidate_ids[source_set.id] = candidate.id
            for source_binding in session.scalars(select(TemplateCandidateBinding).where(
                TemplateCandidateBinding.template_id == owner.id,
            )):
                session.add(TemplateCandidateBinding(
                    template_id=template.id,
                    candidate_set_id=candidate_ids[source_binding.candidate_set_id],
                    target_pattern=generic_targets.get(source_binding.target_pattern,
                                                       source_binding.target_pattern),
                    field_label=source_binding.field_label,
                ))
            snapshot = without_current_qualification(remap(
                source_job.config_snapshot_json, {**replacements, **path_map, **artifact_map},
            ))
            snapshot.update({
                "name": name, "templateId": template.id,
                "templateVersionId": new_id,
            })
            snapshot.pop("forkParentVersionId", None)
            snapshot.pop("reprocessTemplateVersionId", None)
            if quality.get("printLayout") is not None:
                snapshot["printLayout"] = copy.deepcopy(quality["printLayout"])
            job.template_version_id = new_id
            job.config_snapshot_json = snapshot
            session.commit()
            return template.id, new_id
