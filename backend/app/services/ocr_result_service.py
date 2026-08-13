from __future__ import annotations

import csv
import io
import json
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.db.models import ConfirmedRecordField, ConfirmedResult, ProcessingJob
from app.db.session import Database
from app.domain.enums import JobStatus, JobType
from app.domain.individual_id import individual_id_from_document
from app.domain.numeric_values import normalize_document_numbers
from app.domain.ocr_models import (
    Calving,
    DocumentIdentity,
    FieldReading,
    OcrDocument,
    Pedigree,
    ReviewItem,
    SaleCalf,
)
from app.domain.template_config import load_template_config
from app.repositories.job_repository import JobRepository
from app.repositories.ocr_result_repository import OcrResultRepository
from app.storage.local_storage import LocalStorageService
from app.validation.business_validators import DeterministicValidator


@dataclass(frozen=True, slots=True)
class CorrectionValues:
    base_revision: int
    document: DocumentIdentity
    sale_calves: list[SaleCalf]
    pedigree: Pedigree
    calvings: list[Calving]
    custom_fields: dict[str, FieldReading]
    review_items: list[ReviewItem]


class OcrResultService:
    def __init__(self, database: Database, storage: LocalStorageService) -> None:
        self._database = database
        self._storage = storage
        self._validator = DeterministicValidator()

    def get(self, job_id: str) -> dict[str, Any]:
        with self._database.session() as session:
            job = JobRepository(session).get(job_id)
            if job is None or job.job_type != JobType.EXTRACT_DOCUMENT.value:
                raise self._not_found()
            if job.status not in {
                JobStatus.REVIEW_REQUIRED.value,
                JobStatus.CONFIRMED.value,
            }:
                raise AppError(
                    status=409,
                    code="RESULT_NOT_READY",
                    title="OCR result is not ready",
                    detail="OCR結果はレビュー準備完了後に取得できます。",
                )
            repository = OcrResultRepository(session)
            stored = repository.get_by_job(job_id)
            if stored is None:
                raise self._not_found()
            is_confirmed = job.status == JobStatus.CONFIRMED.value
            confirmed = repository.get_confirmed(job_id) if is_confirmed else None
            return self._response(
                stored,
                job=job,
                confirmed=is_confirmed,
                individual_id=confirmed.individual_id if confirmed else None,
            )

    def correct(self, job_id: str, values: CorrectionValues) -> dict[str, Any]:
        with self._database.session() as session:
            self._begin_mutation(session)
            jobs = JobRepository(session)
            job = jobs.get(job_id)
            if job is None or job.job_type != JobType.EXTRACT_DOCUMENT.value:
                raise self._not_found()
            if job.status != JobStatus.REVIEW_REQUIRED.value:
                raise AppError(
                    status=409,
                    code="INVALID_STATE_TRANSITION",
                    title="OCR result cannot be edited",
                    detail="REVIEW_REQUIRED状態のOCR結果だけを修正できます。",
                )
            repository = OcrResultRepository(session)
            stored = repository.get_by_job(job_id)
            if stored is None:
                raise self._not_found()
            current = OcrDocument.model_validate(stored.current_corrected_json)
            candidate = current.model_copy(
                update={
                    "document": values.document,
                    "sale_calves": values.sale_calves,
                    "pedigree": values.pedigree,
                    "calvings": values.calvings,
                    "custom_fields": values.custom_fields,
                    "review_items": values.review_items,
                },
                deep=True,
            )
            candidate = OcrDocument.model_validate(
                candidate.model_dump(mode="json", by_alias=True)
            )
            candidate = normalize_document_numbers(candidate)
            report = self._validator.validate(
                candidate,
                configured_paths=self._configured_paths(job),
            )
            candidate = self._validator.merge_review_items(candidate, report)
            serialized = candidate.model_dump(mode="json", by_alias=True)
            updated = repository.save_correction(
                result=stored,
                base_revision=values.base_revision,
                candidate=serialized,
                report=report,
            )
            if updated is None:
                raise AppError(
                    status=409,
                    code="REVISION_CONFLICT",
                    title="OCR result revision conflict",
                    detail="別の修正が保存されています。最新結果を再取得してください。",
                )
            return self._response(updated, job=job, confirmed=False)

    def confirm(self, job_id: str, *, base_revision: int) -> dict[str, Any]:
        with self._database.session() as session:
            self._begin_mutation(session)
            jobs = JobRepository(session)
            job = jobs.get(job_id)
            if job is None or job.job_type != JobType.EXTRACT_DOCUMENT.value:
                raise self._not_found()
            repository = OcrResultRepository(session)
            if job.status != JobStatus.REVIEW_REQUIRED.value or repository.get_confirmed(
                job_id
            ):
                raise AppError(
                    status=409,
                    code="INVALID_STATE_TRANSITION",
                    title="OCR result cannot be confirmed",
                    detail="このOCR結果は確定できません。",
                )
            stored = repository.get_by_job(job_id)
            if stored is None:
                raise self._not_found()
            if stored.revision != base_revision:
                raise AppError(
                    status=409,
                    code="REVISION_CONFLICT",
                    title="OCR result revision conflict",
                    detail="別の修正が保存されています。最新結果を再取得してください。",
                )
            original_document = OcrDocument.model_validate(stored.current_corrected_json)
            document = normalize_document_numbers(original_document)
            report = self._validator.validate(
                document,
                configured_paths=self._configured_paths(job),
            )
            document = self._validator.merge_review_items(document, report)
            has_unresolved_error = any(
                item.severity == "error" for item in document.review_items
            )
            if not report.valid or has_unresolved_error:
                raise AppError(
                    status=409,
                    code="UNRESOLVED_REVIEW_ITEMS",
                    title="OCR result has unresolved errors",
                    detail="errorレベルのレビュー項目を解消してから確定してください。",
                )
            try:
                individual_id = individual_id_from_document(document)
            except ValueError as exc:
                raise AppError(
                    status=409,
                    code="INDIVIDUAL_ID_REQUIRED",
                    title="Individual identification number is required",
                    detail="個体識別番号を10桁の数字で確定してから保存してください。",
                ) from exc
            serialized = document.model_dump(mode="json", by_alias=True)
            if serialized != stored.current_corrected_json:
                updated = repository.save_correction(
                    result=stored,
                    base_revision=base_revision,
                    candidate=serialized,
                    report=report,
                    commit=False,
                )
                if updated is None:
                    raise AppError(
                        status=409,
                        code="REVISION_CONFLICT",
                        title="OCR result revision conflict",
                        detail="別の修正が保存されています。最新結果を再取得してください。",
                    )
                stored = updated
            json_relative = self._storage.write_text(
                f"exports/{job_id}/confirmed_result.json",
                json.dumps(serialized, ensure_ascii=False, indent=2),
            )
            self._storage.write_text(
                f"exports/{job_id}/confirmed_result.csv", self._to_csv(serialized)
            )
            confirmed_at = datetime.now(UTC)
            confirmed_result = ConfirmedResult(
                job_id=job_id,
                ocr_result_revision=stored.revision,
                individual_id=individual_id,
                confirmed_json=serialized,
                confirmed_at=confirmed_at,
                export_relative_path=json_relative,
            )
            session.add(confirmed_result)
            session.flush()
            self._index_record_fields(
                session,
                confirmed_result=confirmed_result,
                document=serialized,
                raw_structure=job.config_snapshot_json.get("recordStructure"),
            )
            job.status = JobStatus.CONFIRMED.value
            job.confirmed_at = confirmed_at
            session.commit()
            return self._response(
                stored,
                job=job,
                confirmed=True,
                individual_id=individual_id,
            )

    @staticmethod
    def _begin_mutation(session: Session) -> None:
        bind = session.get_bind()
        if bind.dialect.name == "sqlite":
            # The local application uses SQLite. Taking the write lock before
            # reading status/revision serializes edits and confirmation across
            # multiple browser tabs, so the confirmed snapshot cannot lag
            # behind the latest accepted correction.
            session.execute(text("BEGIN IMMEDIATE"))

    def export_path(self, job_id: str, format_name: str) -> tuple[str, str, str]:
        if format_name not in {"json", "csv"}:
            raise AppError(
                status=422,
                code="VALIDATION_FAILED",
                title="Unsupported export format",
                detail="formatはjsonまたはcsvを指定してください。",
            )
        with self._database.session() as session:
            job = JobRepository(session).get(job_id)
            confirmed = OcrResultRepository(session).get_confirmed(job_id)
            if job is None or job.status != JobStatus.CONFIRMED.value or confirmed is None:
                raise AppError(
                    status=409,
                    code="INVALID_STATE_TRANSITION",
                    title="Export is not available",
                    detail="確定済みのOCR結果だけをエクスポートできます。",
                )
        relative = f"exports/{job_id}/confirmed_result.{format_name}"
        if not self._storage.exists(relative):
            raise self._not_found()
        media_type = "application/json" if format_name == "json" else "text/csv"
        return str(self._storage.resolve(relative)), media_type, f"confirmed_result.{format_name}"

    def _response(
        self,
        stored: Any,
        *,
        job: ProcessingJob,
        confirmed: bool,
        individual_id: str | None = None,
    ) -> dict[str, Any]:
        snapshot_individual_id = job.config_snapshot_json.get("motherIndividualId")
        resolved_individual_id = individual_id or (
            snapshot_individual_id
            if isinstance(snapshot_individual_id, str)
            else None
        )
        return {
            "jobId": stored.job_id,
            "revision": stored.revision,
            "result": stored.current_corrected_json,
            "validation": stored.validation_json,
            "provider": stored.provider,
            "modelId": stored.model_id,
            "promptVersion": stored.prompt_version,
            "apiCallCount": stored.api_call_count,
            "inputTokens": stored.input_tokens,
            "outputTokens": stored.output_tokens,
            "estimatedCost": float(stored.estimated_cost)
            if stored.estimated_cost is not None
            else None,
            "confirmed": confirmed,
            "individualId": resolved_individual_id,
            "fieldPreviews": self._field_previews(
                job,
                stored.current_corrected_json,
            ),
            "recordStructure": self._record_structure(job),
        }

    @staticmethod
    def _field_previews(
        job: ProcessingJob,
        document: dict[str, Any],
    ) -> list[dict[str, object]]:
        snapshot = dict(job.config_snapshot_json)
        raw_mappings = snapshot.get("fieldMappings")
        raw_structure = snapshot.get("recordStructure")
        binding_by_mapping: dict[str, dict[str, object]] = {}
        if isinstance(raw_structure, dict):
            raw_bindings = raw_structure.get("fieldBindings")
            if isinstance(raw_bindings, list):
                binding_by_mapping = {
                    mapping_id: raw
                    for raw in raw_bindings
                    if isinstance(raw, dict)
                    and isinstance((mapping_id := raw.get("mappingId")), str)
                }
        previews: list[dict[str, object]] = []
        current_path_by_mapping: dict[str, str] = {}
        current_box_by_mapping: dict[str, dict[str, object] | None] = {}

        def collect_current_readings(value: object, path: str) -> None:
            if isinstance(value, list):
                for index, child in enumerate(value):
                    collect_current_readings(child, f"{path}.{index}")
                return
            if not isinstance(value, dict):
                return
            source_region_id = value.get("sourceRegionId")
            if "status" in value and isinstance(source_region_id, str):
                if source_region_id != "unmapped":
                    current_path_by_mapping[source_region_id] = path
                    box = value.get("boundingBox")
                    current_box_by_mapping[source_region_id] = (
                        box if isinstance(box, dict) else None
                    )
                return
            for key, child in value.items():
                collect_current_readings(child, f"{path}.{key}" if path else key)

        for root in (
            "document",
            "saleCalves",
            "pedigree",
            "calvings",
            "customFields",
        ):
            collect_current_readings(document.get(root), root)

        if isinstance(raw_mappings, list):
            for raw in raw_mappings:
                if not isinstance(raw, dict):
                    continue
                mapping_id = raw.get("mappingId")
                target_path = raw.get("targetPath")
                if not isinstance(mapping_id, str) or not isinstance(target_path, str):
                    continue
                binding = binding_by_mapping.get(mapping_id, {})
                current_box = current_box_by_mapping.get(mapping_id)
                previews.append(
                    {
                        # The reading's source cell is authoritative. This also
                        # remains correct if a collection row is moved or
                        # compacted after the template snapshot was created.
                        "targetPath": current_path_by_mapping.get(
                            mapping_id, target_path
                        ),
                        "mappingId": mapping_id,
                        "artifactUrl": (
                            f"/api/v1/jobs/{job.id}/artifacts/"
                            f"fieldCrop.{mapping_id}.alignedColor"
                        ),
                        "boundingBox": current_box
                        if mapping_id in current_box_by_mapping
                        else {
                            "x": raw.get("x"),
                            "y": raw.get("y"),
                            "width": raw.get("width"),
                            "height": raw.get("height"),
                        },
                        "recordGroupId": binding.get("recordGroupId"),
                        "fieldKey": binding.get("fieldKey"),
                    }
                )
            if previews:
                return previews
        return OcrResultService._legacy_field_previews(job.id, document)

    @staticmethod
    def _record_structure(job: ProcessingJob) -> dict[str, object]:
        raw = job.config_snapshot_json.get("recordStructure")
        return dict(raw) if isinstance(raw, dict) else {
            "layoutSchemaVersion": 3,
            "verified": False,
            "canonicalWidth": 0,
            "canonicalHeight": 0,
            "sections": [],
            "groups": [],
            "fieldBindings": [],
        }

    @staticmethod
    def _configured_paths(job: ProcessingJob) -> set[str] | None:
        raw_mappings = job.config_snapshot_json.get("fieldMappings")
        if not isinstance(raw_mappings, list):
            return None
        paths = {
            target_path
            for raw in raw_mappings
            if isinstance(raw, dict)
            and isinstance((target_path := raw.get("targetPath")), str)
        }
        return paths

    @staticmethod
    def _index_record_fields(
        session: Any,
        *,
        confirmed_result: ConfirmedResult,
        document: dict[str, Any],
        raw_structure: object,
    ) -> None:
        section_titles = {
            "saleCalves": "売却子牛一覧",
            "calvings": "繁殖履歴",
        }
        group_keys: dict[tuple[str, int], str] = {}
        if isinstance(raw_structure, dict):
            raw_sections = raw_structure.get("sections")
            raw_groups = raw_structure.get("groups")
            collection_by_section: dict[object, str] = {}
            if isinstance(raw_sections, list):
                for section in raw_sections:
                    if not isinstance(section, dict):
                        continue
                    section_id = section.get("id")
                    collection = section.get("collectionPath")
                    title = section.get("title")
                    if isinstance(section_id, str) and isinstance(collection, str):
                        collection_by_section[section_id] = collection
                        if isinstance(title, str) and title.strip():
                            section_titles[collection] = title.strip()
            if isinstance(raw_groups, list):
                for group in raw_groups:
                    if not isinstance(group, dict):
                        continue
                    collection = collection_by_section.get(group.get("sectionId"))
                    order = group.get("order")
                    value = group.get("recordKeyValue")
                    if (
                        isinstance(collection, str)
                        and isinstance(order, int)
                        and isinstance(value, str)
                        and value.strip()
                    ):
                        group_keys[(collection, order)] = value.strip()
        for collection in ("saleCalves", "calvings"):
            records = document.get(collection)
            if not isinstance(records, list):
                continue
            for index, record in enumerate(records):
                if not isinstance(record, dict):
                    continue
                record_key = group_keys.get((collection, index + 1))
                parity = record.get("parity")
                if record_key is None and isinstance(parity, dict):
                    key_value = parity.get("normalized") or parity.get("raw")
                    if isinstance(key_value, str) and key_value.strip():
                        record_key = key_value.strip()
                for field_key, reading in record.items():
                    if not isinstance(reading, dict):
                        continue
                    raw_value = reading.get("raw")
                    normalized_value = reading.get("normalized")
                    raw_text = raw_value if isinstance(raw_value, str) else None
                    selected_text = (
                        normalized_value
                        if isinstance(normalized_value, str)
                        else raw_text
                    )
                    if selected_text is None:
                        continue
                    compact = selected_text.replace(",", "").strip()
                    try:
                        numeric_value = Decimal(compact)
                    except InvalidOperation:
                        numeric_value = None
                    session.add(
                        ConfirmedRecordField(
                            confirmed_result_id=confirmed_result.id,
                            section_key=collection,
                            section_title=section_titles[collection],
                            record_order=index + 1,
                            record_key=record_key,
                            field_key=field_key,
                            raw_value=raw_text,
                            normalized_value=unicodedata.normalize(
                                "NFKC", selected_text
                            ).strip().casefold(),
                            numeric_value=numeric_value,
                        )
                    )

    @staticmethod
    def _legacy_field_previews(
        job_id: str,
        document: dict[str, Any],
    ) -> list[dict[str, object]]:
        previews: list[dict[str, object]] = []

        def visit(value: object, path: str) -> None:
            if not isinstance(value, dict):
                return
            source_region_id = value.get("sourceRegionId")
            box = value.get("boundingBox")
            if (
                "status" in value
                and isinstance(source_region_id, str)
                and source_region_id != "unmapped"
            ):
                previews.append(
                    {
                        "targetPath": path,
                        "mappingId": source_region_id,
                        "artifactUrl": (
                            f"/api/v1/jobs/{job_id}/artifacts/"
                            f"fieldCrop.{source_region_id}.alignedColor"
                        ),
                        "boundingBox": box if isinstance(box, dict) else None,
                        "recordGroupId": None,
                        "fieldKey": None,
                    }
                )
                return
            for key, child in value.items():
                child_path = f"{path}.{key}" if path else key
                if isinstance(child, list):
                    for index, item in enumerate(child):
                        visit(item, f"{child_path}.{index}")
                else:
                    visit(child, child_path)

        for root in ("document", "saleCalves", "pedigree", "calvings", "customFields"):
            value = document.get(root)
            if isinstance(value, list):
                for index, item in enumerate(value):
                    visit(item, f"{root}.{index}")
            else:
                visit(value, root)
        return previews

    @staticmethod
    def _to_csv(document: dict[str, Any]) -> str:
        configured = load_template_config().export.csv_columns
        bases = [path.removesuffix(".raw") for path in configured]
        columns = [f"{base}.{leaf}" for base in bases for leaf in ("raw", "normalized", "status")]
        row = {
            column: OcrResultService._csv_safe(
                OcrResultService._value_at(document, column)
            )
            for column in columns
        }
        output = io.StringIO(newline="")
        writer = csv.DictWriter(output, fieldnames=columns, lineterminator="\n")
        writer.writeheader()
        writer.writerow(row)
        return "\ufeff" + output.getvalue()

    @staticmethod
    def _value_at(document: dict[str, Any], dotted: str) -> object:
        value: object = document
        for key in dotted.split("."):
            if not isinstance(value, dict):
                return ""
            value = value.get(key, "")
        return "" if value is None else value

    @staticmethod
    def _csv_safe(value: object) -> object:
        if not isinstance(value, str):
            return value
        visible = value.lstrip(" \t\r\n")
        if visible.startswith(("=", "+", "-", "@")):
            return f"'{value}"
        return value

    @staticmethod
    def _not_found() -> AppError:
        return AppError(
            status=404,
            code="OCR_RESULT_NOT_FOUND",
            title="OCR result not found",
            detail="指定されたOCR結果が見つかりません。",
        )
