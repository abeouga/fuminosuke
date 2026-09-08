from __future__ import annotations

import copy
import hashlib
import re
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import yaml
from sqlalchemy import select

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


class TemplateVersionService:
    def __init__(self, database: Database, storage: LocalStorageService) -> None:
        self.database = database
        self.storage = storage

    def edit(self, version_id: str) -> TemplateVersion:
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
            if source.status == "DRAFT":
                return source
            if source.id != template.active_version_id or source.status != "ACTIVE":
                raise AppError(
                    status=409,
                    code="INVALID_STATE_TRANSITION",
                    title="Active template required",
                    detail="現在の有効版から編集してください。",
                )
            if len(drafts) > 1:
                raise AppError(
                    status=409,
                    code="TEMPLATE_DRAFT_AMBIGUOUS",
                    title="Multiple drafts exist",
                    detail="複数の下書きがあります。履歴から編集対象を選択してください。",
                )
            if drafts:
                return drafts[0]
            source_job = session.get(ProcessingJob, source.source_job_id)
            if source_job is None:
                raise AppError(
                    status=409,
                    code="SOURCE_IMAGE_NOT_READY",
                    title="Source missing",
                    detail="元ジョブがありません。",
                )
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
                    generic_targets[f"customFields.{loc.mapping_key}"] = f"customFields.{new_key}"
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
                content = self.storage.read_bytes(artifact.relative_path)
                target = f"jobs/{job_id}/1/{Path(artifact.relative_path).name}"
                self.storage.write_bytes(target, content)
                clone_id = str(uuid4())
                session.add(
                    JobArtifact(
                        id=clone_id,
                        job_id=job_id,
                        attempt_number=1,
                        artifact_name=artifact.artifact_name,
                        media_type=artifact.media_type,
                        relative_path=target,
                        sha256=hashlib.sha256(content).hexdigest(),
                        width=artifact.width,
                        height=artifact.height,
                    )
                )
                artifact_map[artifact.id] = clone_id
                path_map[artifact.relative_path] = target
            for item in session.scalars(select(JobInput).where(JobInput.job_id == source_job.id)):
                target = f"uploads/{job_id}/{item.input_index}_{Path(item.relative_path).name}"
                self.storage.write_bytes(target, self.storage.read_bytes(item.relative_path))
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
            quality = remap(source.quality_json, {**replacements, **artifact_map, **path_map})
            if quality.get("printLayout") is not None:
                from app.services.print_layout_service import digest

                quality["printLayout"]["layoutSha256"] = digest(layout)
            # Copy source files referenced by quality but not by an artifact.
            for key in ("spreadsheetSourcePath", "spreadsheetPrintablePdfPath"):
                path = quality.get(key)
                if isinstance(path, str) and path not in path_map.values():
                    target = f"{base}/{Path(path).name}"
                    self.storage.write_bytes(target, self.storage.read_bytes(path))
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
            self.storage.write_text(config_path, yaml.safe_dump(config_data, allow_unicode=True))
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
                **remap(source_job.config_snapshot_json, {**path_map, **replacements}),
                "templateId": template.id,
                "templateVersionId": new_id,
                "forkParentVersionId": source.id,
            }
            job.config_snapshot_json.pop("reprocessTemplateVersionId", None)
            if quality.get("printLayout") is not None:
                job.config_snapshot_json["printLayout"] = copy.deepcopy(quality["printLayout"])
            session.commit()
            return version
