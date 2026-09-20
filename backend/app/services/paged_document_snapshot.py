"""Document-wide review/output ownership frozen alongside the accepted page bundle."""

from __future__ import annotations

import copy
import hashlib
from typing import Any
from uuid import uuid4

from openpyxl.utils.cell import range_boundaries  # type: ignore[import-untyped]
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import JobArtifact, ProcessingJob, TemplateDefinitionRevision, TemplateVersion
from app.domain.paged_document_metadata import ADAPTER as DOCUMENT_ADAPTER
from app.domain.paged_document_metadata import has_document_metadata, page_context
from app.domain.print_layout import print_error
from app.domain.template_config import TemplateConfig
from app.domain.template_quality import has_approved_generic_definition
from app.layout_analysis.models import LayoutDefinition
from app.services.paged_workbook import ADAPTER as OUTPUT_ADAPTER
from app.services.print_layout_service import digest
from app.services.review_display_structure import build_review_display_structure
from app.services.spreadsheet_template_output_service import SpreadsheetTemplateOutputService
from app.services.template_field_identity import identities
from app.storage.local_storage import LocalStorageService


def output_bindings(pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Use accepted source ranges and explicit bindings, never label/geometry fallback."""
    result = []
    seen: set[str] = set()
    all_mappings = [m for p in pages for m in p["fieldMappings"]]
    if len({m["mappingId"] for m in all_mappings}) != len(all_mappings) or len(
        {m["targetPath"] for m in all_mappings}
    ) != len(all_mappings):
        raise print_error("出力項目の所有権が重複しています。", "PAGE_OUTPUT_MAPPING_INVALID", 409)
    for page in pages:
        mappings = {m["mappingId"]: m for m in page["fieldMappings"]}
        page_seen: set[str] = set()
        pc1, pr1, pc2, pr2 = range_boundaries(page["pageRange"])
        for field in page["fields"]:
            c1, r1, c2, r2 = range_boundaries(field["sourceRange"])
            if not (pc1 <= c1 <= c2 <= pc2 and pr1 <= r1 <= r2 <= pr2):
                raise print_error(
                    "出力先が保存ページの範囲外です。", "PAGE_OUTPUT_MAPPING_INVALID", 409
                )
            for binding in field.get("bindings", []):
                mapping_id = binding["mappingId"]
                mapping = mappings.get(mapping_id)
                if (
                    mapping_id in seen
                    or mapping is None
                    or mapping["targetPath"] != binding["targetPath"]
                ):
                    raise print_error(
                        "出力項目のページ所有権が一致しません。", "PAGE_OUTPUT_MAPPING_INVALID", 409
                    )
                seen.add(mapping_id)
                page_seen.add(mapping_id)
                result.append(
                    {
                        "pageId": page["pageId"],
                        "mappingId": mapping_id,
                        "targetPath": binding["targetPath"],
                        "cellId": field["cellId"],
                        "sourceRange": field["sourceRange"],
                        "sourceParts": copy.deepcopy(field.get("sourceParts", [])),
                        "anchorCell": field["sourceRange"].split(":")[0],
                    }
                )
        if page_seen != set(mappings):
            raise print_error(
                "このページの出力先が欠落しています。", "PAGE_OUTPUT_MAPPING_INVALID", 409
            )
    expected = {m["mappingId"] for m in all_mappings}
    if seen != expected:
        raise print_error("全項目の出力先を確認できません。", "PAGE_OUTPUT_MAPPING_INVALID", 409)
    return result


def capture_document_snapshot(
    session: Session,
    storage: LocalStorageService,
    *,
    job_id: str,
    version: TemplateVersion,
    config: TemplateConfig,
    pages: list[dict[str, Any]],
) -> dict[str, Any]:
    layout = LayoutDefinition.model_validate(version.layout_json)
    generic_definition = None
    if has_approved_generic_definition(version.quality_json):
        revision = version.quality_json.get("templateDefinitionRevision")
        row = session.scalar(
            select(TemplateDefinitionRevision).where(
                TemplateDefinitionRevision.template_version_id == version.id,
                TemplateDefinitionRevision.revision == revision,
                TemplateDefinitionRevision.source == "APPROVED",
            )
        )
        if row is None:
            raise print_error(
                "承認済み汎用定義を取得できません。", "TEMPLATE_DEFINITION_REQUIRED", 409
            )
        generic_definition = copy.deepcopy(row.definition_json)
    mappings = [m for m in config.field_mappings if m.enabled]
    output = SpreadsheetTemplateOutputService(storage).snapshot(session, version=version)
    if output is None:
        raise print_error(
            "複数ページ出力には元XLSXが必要です。", "PAGE_OUTPUT_SNAPSHOT_REQUIRED", 409
        )
    source = output["sourceWorkbook"]
    assert isinstance(source, dict)
    assert isinstance(source["sha256"], str)
    plan = version.quality_json["printLayout"]
    if source["sha256"] != plan["sourceSha256"] or output["sourceSheetName"] != plan["sheetName"]:
        raise print_error("印刷元と出力元が一致しません。", "PAGE_OUTPUT_SOURCE_CHANGED", 409)
    artifact = session.get(JobArtifact, source["artifactId"])
    if artifact is None:
        raise print_error("元XLSXがありません。", "PAGE_OUTPUT_SNAPSHOT_REQUIRED", 409)
    content = storage.read_bytes(artifact.relative_path)
    if hashlib.sha256(content).hexdigest() != source["sha256"]:
        raise print_error("元XLSXのSHAが一致しません。", "PAGE_OUTPUT_SOURCE_CHANGED", 409)
    path = storage.write_bytes(f"uploads/{job_id}/template_snapshot/source.xlsx", content)
    accepted_artifact_id = str(uuid4())
    session.add(
        JobArtifact(
            id=accepted_artifact_id,
            job_id=job_id,
            attempt_number=1,
            artifact_name="acceptedSourceWorkbook",
            relative_path=path,
            sha256=source["sha256"],
            media_type=artifact.media_type,
        )
    )
    serialized_mappings = [m.model_dump(mode="json", by_alias=True) for m in mappings]
    return {
        "schemaVersion": 1,
        "outputAdapter": OUTPUT_ADAPTER,
        **({"documentMetadataAdapter": DOCUMENT_ADAPTER} if has_document_metadata(config) else {}),
        "templateId": version.template_id,
        "templateVersionId": version.id,
        "templateMode": version.mode,
        "config": config.model_dump(mode="json", by_alias=True),
        # Whole layout preserves headings, repeated rows, merges and authoring metadata.
        # Canonical geometry here is not a replacement for page-local OCR geometry.
        "layout": copy.deepcopy(version.layout_json),
        "reviewStructure": build_review_display_structure(layout, serialized_mappings),
        "logicalFieldIds": identities(session, version, mappings),
        "genericTemplateDefinition": generic_definition,
        "spreadsheetOutput": output,
        "outputBindings": output_bindings(pages),
        "sourceWorkbook": {
            "artifactId": accepted_artifact_id,
            "relativePath": path,
            "sha256": source["sha256"],
            "sourceIdentity": copy.deepcopy(source),
        },
    }


def load_document_snapshot(
    job: ProcessingJob,
    storage: LocalStorageService | None = None,
) -> dict[str, Any]:
    """No current TemplateVersion/source-job reads, including after deletion or retry."""
    bundle = job.config_snapshot_json.get("jobPages")
    if not isinstance(bundle, dict) or digest(bundle) != job.config_snapshot_json.get(
        "jobPagesSha256"
    ):
        raise print_error("受付snapshotのSHAが一致しません。", "JOB_PAGE_SNAPSHOT_INVALID", 409)
    snapshot = bundle.get("documentSnapshot")
    if (
        bundle.get("schemaVersion") != 2
        or not isinstance(snapshot, dict)
        or snapshot.get("schemaVersion") != 1
        or snapshot.get("templateVersionId") != job.template_version_id
    ):
        raise print_error(
            "受付時の文書・出力snapshotがありません。", "PAGE_OUTPUT_SNAPSHOT_REQUIRED", 409
        )
    if snapshot.get("outputBindings") != output_bindings(bundle["pages"]):
        raise print_error("受付時の出力先が一致しません。", "PAGE_OUTPUT_MAPPING_INVALID", 409)
    try:
        config = TemplateConfig.model_validate(snapshot["config"])
        if has_document_metadata(config):
            if snapshot.get("documentMetadataAdapter") != DOCUMENT_ADAPTER:
                raise ValueError("Document metadata adapter is missing")
            for page in bundle["pages"]:
                if page.get("documentContext") != page_context(config, page["fieldMappings"]):
                    raise ValueError("Page document metadata changed")
        elif snapshot.get("documentMetadataAdapter") is not None or any(
            "documentContext" in page for page in bundle["pages"]
        ):
            raise ValueError("Unexpected document metadata")
    except (ValueError, KeyError, TypeError) as exc:
        raise print_error(
            "受付時の文書識別設定とページ所有権が一致しません。", "JOB_PAGE_SNAPSHOT_INVALID", 409
        ) from exc
    output = snapshot["spreadsheetOutput"]
    source = snapshot["sourceWorkbook"]
    plan = bundle["printLayout"]
    full_mappings = {
        m["mappingId"]: m for m in snapshot["config"]["fieldMappings"] if m.get("enabled", True)
    }
    local_mappings = {m["mappingId"]: m for p in bundle["pages"] for m in p["fieldMappings"]}

    def without_page_rectangle(mapping: dict[str, Any]) -> dict[str, Any]:
        return {k: v for k, v in mapping.items() if k not in {"x", "y", "width", "height"}}

    if (
        source["sha256"] != plan["sourceSha256"]
        or source["sourceIdentity"] != output["sourceWorkbook"]
        or source["sha256"] != output["sourceWorkbook"]["sha256"]
        or output["sourceSheetName"] != plan["sheetName"]
        or digest(snapshot["layout"]) != plan["layoutSha256"]
        or {k: without_page_rectangle(v) for k, v in full_mappings.items()}
        != {k: without_page_rectangle(v) for k, v in local_mappings.items()}
    ):
        raise print_error(
            "文書とページの受付情報が一致しません。", "JOB_PAGE_SNAPSHOT_INVALID", 409
        )
    if storage is not None:
        try:
            path = storage.resolve(source["relativePath"])
            path.relative_to(storage.resolve(f"uploads/{job.id}/template_snapshot"))
            if hashlib.sha256(path.read_bytes()).hexdigest() != source["sha256"]:
                raise ValueError("source digest changed")
        except (OSError, ValueError) as exc:
            raise print_error(
                "受付時の元XLSXを検証できません。", "PAGE_OUTPUT_SOURCE_CHANGED", 409
            ) from exc
    return copy.deepcopy(snapshot)
