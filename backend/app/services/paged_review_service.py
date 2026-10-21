"""Page-qualified review/correction; no single-page fallback or implicit confirmation."""

from __future__ import annotations

import copy
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.db.models import PagedConfirmation, PagedReview, PagedReviewRevision, ProcessingJob
from app.db.session import Database
from app.domain.ocr_models import FieldReading, OcrDocument
from app.domain.paged_document_metadata import proposed_name
from app.domain.paged_review import PageConfirmation, PageCorrection, PageReopen
from app.domain.print_layout import print_error
from app.domain.spreadsheet_output_name import normalize_spreadsheet_output_name
from app.domain.template_config import TemplateConfig
from app.ocr.deterministic_mapper import assign_mapped_reading, get_mapped_reading
from app.services.paged_document_snapshot import load_document_snapshot
from app.services.paged_workbook import ADAPTER as OUTPUT_ADAPTER
from app.services.print_layout_service import digest
from app.services.template_mutation import begin_immediate
from app.validation.business_validators import DeterministicValidator


class PagedReviewService:
    def __init__(self, database: Database):
        self.database = database

    @staticmethod
    def _validate_readings(document: OcrDocument, page: dict[str, Any]) -> None:
        mappings = page["fieldMappings"]
        paths = {m["targetPath"] for m in mappings}
        if len(paths) != len(mappings) or len({m["mappingId"] for m in mappings}) != len(mappings):
            raise print_error("項目の所有権が重複しています。", "PAGE_RESULT_CHANGED", 409)
        for mapping in mappings:
            reading = get_mapped_reading(document, mapping["targetPath"])
            padding = mapping["padding"]
            expected_box = {
                "x": mapping["x"] - padding,
                "y": mapping["y"] - padding,
                "width": mapping["width"] + 2 * padding,
                "height": mapping["height"] + 2 * padding,
            }
            if (
                reading is None
                or reading.source_region_id != mapping["mappingId"]
                or reading.bounding_box is None
                or reading.bounding_box.model_dump() != expected_box
            ):
                raise print_error("項目と切り出し領域が一致しません。", "PAGE_RESULT_CHANGED", 409)

        def check_other_readings(value: Any, path: str) -> None:
            if isinstance(value, list):
                for index, child in enumerate(value):
                    check_other_readings(child, f"{path}.{index}")
            elif isinstance(value, dict):
                if "sourceRegionId" in value and "status" in value:
                    if path not in paths and (
                        value.get("raw") is not None or value.get("normalized") is not None
                    ):
                        raise print_error(
                            "別項目の値が混入しています。", "PAGE_RESULT_CHANGED", 409
                        )
                else:
                    for key, child in value.items():
                        check_other_readings(child, f"{path}.{key}")

        raw = document.model_dump(mode="json", by_alias=True)
        for root in ("fields",):
            check_other_readings(raw.get(root), root)

    @staticmethod
    def _job(session: Session, job_id: str) -> tuple[ProcessingJob, list[dict[str, Any]]]:
        job = session.get(ProcessingJob, job_id)
        if job is None or job.job_type != "EXTRACT_DOCUMENT":
            raise print_error("ページ結果がありません。", "PAGE_RESULT_NOT_FOUND", 404)
        bundle = job.config_snapshot_json.get("jobPages")
        if not isinstance(bundle, dict) or digest(bundle) != job.config_snapshot_json.get(
            "jobPagesSha256"
        ):
            raise print_error(
                "受付ページの対応を確認できません。", "JOB_PAGE_SNAPSHOT_INVALID", 409
            )
        pages = bundle["pages"]
        load_document_snapshot(job)
        if not pages or len({p["pageId"] for p in pages}) != len(pages):
            raise print_error(
                "受付ページが欠落または重複しています。", "JOB_PAGE_SNAPSHOT_INVALID", 409
            )
        return job, pages

    def initialize(self, job_id: str, results: list[dict[str, Any]]) -> None:
        """All-or-nothing publication. Retry must never reset a user's corrections."""
        with self.database.session() as session:
            begin_immediate(session)
            job, pages = self._job(session, job_id)
            if job.cancel_requested or job.status not in {
                "QUEUED",
                "PROCESSING",
                "REVIEW_REQUIRED",
            }:
                raise print_error(
                    "この状態ではページ結果を保存できません。", "INVALID_STATE_TRANSITION", 409
                )
            by_id = {p["pageId"]: p for p in results}
            if len(by_id) != len(results) or set(by_id) != {p["pageId"] for p in pages}:
                raise print_error("全ページの結果が必要です。", "PAGE_RESULT_CHANGED", 409)
            raw = {}
            for page in pages:
                result = by_id[page["pageId"]]
                if (
                    result.get("status") != "SUCCESS"
                    or result.get("pageSnapshotSha256") != digest(page)
                    or result.get("inputId") != page["inputId"]
                    or result.get("inputRevision") != page["inputRevision"]
                    or result.get("referenceImageSha256")
                    != page["templateSnapshot"]["cleanTemplateSha256"]
                    or result.get("fields") != page["fields"]
                ):
                    raise print_error(
                        "ページ結果の物理的対応が一致しません。", "PAGE_RESULT_CHANGED", 409
                    )
                document = OcrDocument.model_validate(result["extraction"]["result"])
                self._validate_readings(document, page)
                raw[page["pageId"]] = copy.deepcopy(result["extraction"])
            stored = session.get(PagedReview, job_id)
            snapshot_sha = job.config_snapshot_json["jobPagesSha256"]
            if stored is not None:
                if stored.snapshot_sha256 != snapshot_sha or stored.raw_pages_json != raw:
                    raise print_error(
                        "保存済み結果を別のOCR結果で置換できません。", "PAGE_RESULT_CHANGED", 409
                    )
                return
            session.add(
                PagedReview(
                    job_id=job_id,
                    snapshot_sha256=snapshot_sha,
                    raw_pages_json=raw,
                    current_pages_json=copy.deepcopy(raw),
                    revision=1,
                )
            )
            session.flush()
            session.add(
                PagedReviewRevision(job_id=job_id, revision=1, source="OCR", pages_json=raw)
            )
            session.commit()

    def _load(
        self, session: Session, job_id: str
    ) -> tuple[ProcessingJob, list[dict[str, Any]], PagedReview]:
        job, pages = self._job(session, job_id)
        if job.status not in {"REVIEW_REQUIRED", "CONFIRMED"} or job.cancel_requested:
            raise print_error(
                "全ページの処理完了後にレビューできます。", "PAGE_RESULT_NOT_READY", 409
            )
        stored = session.get(PagedReview, job_id)
        if stored is None:
            raise print_error("ページレビュー結果がありません。", "PAGE_RESULT_NOT_READY", 409)
        if stored.snapshot_sha256 != job.config_snapshot_json["jobPagesSha256"] or set(
            stored.current_pages_json
        ) != {p["pageId"] for p in pages}:
            raise print_error("保存結果と受付ページが一致しません。", "PAGE_RESULT_CHANGED", 409)
        for page in pages:
            self._validate_readings(
                OcrDocument.model_validate(stored.current_pages_json[page["pageId"]]["result"]),
                page,
            )
        if job.status == "CONFIRMED":
            confirmed = session.get(PagedConfirmation, (job_id, stored.revision))
            if (
                confirmed is None
                or confirmed.snapshot_sha256 != stored.snapshot_sha256
                or confirmed.pages_json != stored.current_pages_json
                or confirmed.document_name != stored.document_name
                or job.confirmed_at is None
            ):
                raise print_error("確定snapshotが一致しません。", "CONFIRMED_RESULT_MISSING", 409)
        return job, pages, stored

    @staticmethod
    def _response(
        job: ProcessingJob, pages: list[dict[str, Any]], stored: PagedReview
    ) -> dict[str, Any]:
        result_pages = []
        readings_by_path = {}
        identity_fields: list[dict[str, Any]] = []
        for page in pages:
            current = stored.current_pages_json[page["pageId"]]
            document = OcrDocument.model_validate(current["result"])
            identity_fields.extend(
                {**field, "pageId": page["pageId"]}
                for field in page.get("documentContext", {}).get("identityFields", [])
            )
            fields = []
            for mapping in page["fieldMappings"]:
                reading = get_mapped_reading(document, mapping["targetPath"])
                if reading is not None:
                    readings_by_path[mapping["targetPath"]] = reading.model_dump(
                        mode="json", by_alias=True
                    )
                fields.append(
                    {
                        "mappingId": mapping["mappingId"],
                        "targetPath": mapping["targetPath"],
                        "label": mapping.get("label", mapping["mappingId"]),
                        "mappingType": mapping.get("mappingType", "text"),
                        "markRecognitionMode": mapping.get("markRecognitionMode"),
                        "markSelectionMode": mapping.get("markSelectionMode"),
                        "markOptions": mapping.get("markOptions", []),
                        "reading": reading.model_dump(mode="json", by_alias=True)
                        if reading
                        else None,
                        "cropUrl": f"/api/v1/jobs/{job.id}/artifacts/"
                        + quote(
                            f"page.{page['pageId']}.fieldCrop.{mapping['mappingId']}.alignedColor",
                            safe="",
                        ),
                    }
                )
            result_pages.append(
                {
                    "pageId": page["pageId"],
                    "pageNumber": page["pageNumber"],
                    "pageRange": page["pageRange"],
                    "inputId": page["inputId"],
                    "inputRevision": page["inputRevision"],
                    "inputSha256": page["inputSha256"],
                    "referenceImageSha256": page["templateSnapshot"]["cleanTemplateSha256"],
                    "pageImageUrl": f"/api/v1/jobs/{job.id}/artifacts/"
                    + quote(f"page.{page['pageId']}.alignedSource", safe=""),
                    "fields": fields,
                    "result": current["result"],
                    "validation": current["validation"],
                }
            )
        snapshot = job.config_snapshot_json["jobPages"].get("documentSnapshot", {})
        metadata = {}
        if snapshot.get("documentMetadataAdapter"):
            config = TemplateConfig.model_validate(snapshot["config"])
            metadata = {
                "documentMetadata": {
                    "identityFields": identity_fields,
                    "proposedName": proposed_name(config, readings_by_path),
                }
            }
        return {
            **metadata,
            "jobId": job.id,
            "revision": stored.revision,
            "reviewStructure": snapshot.get("reviewStructure"),
            "pages": result_pages,
            "confirmed": job.status == "CONFIRMED",
            "documentName": stored.document_name,
            "confirmAvailable": job.status == "REVIEW_REQUIRED"
            and job.config_snapshot_json["jobPages"].get("schemaVersion") == 2,
            "outputAvailable": job.status == "CONFIRMED"
            and job.config_snapshot_json["jobPages"]
            .get("documentSnapshot", {})
            .get("outputAdapter")
            == OUTPUT_ADAPTER,
        }

    def get(self, job_id: str) -> dict[str, Any]:
        with self.database.session() as session:
            return self._response(*self._load(session, job_id))

    def correct(self, job_id: str, page_id: str, correction: PageCorrection) -> dict[str, Any]:
        with self.database.session() as session:
            begin_immediate(session)
            job, pages, stored = self._load(session, job_id)
            if job.status != "REVIEW_REQUIRED":
                raise print_error(
                    "確定済みです。再編集してから補正してください。", "PAGE_RESULT_LOCKED", 409
                )
            if correction.base_revision != stored.revision:
                raise print_error(
                    "別の変更が保存されています。再取得してください。", "REVISION_CONFLICT", 409
                )
            page = next((p for p in pages if p["pageId"] == page_id), None)
            if page is None:
                raise print_error("この文書のページではありません。", "PAGE_RESULT_NOT_FOUND", 404)
            mappings = {m["mappingId"]: m for m in page["fieldMappings"]}
            ids = [f.mapping_id for f in correction.fields]
            if len(set(ids)) != len(ids) or not set(ids) <= set(mappings):
                raise print_error(
                    "このページの項目を重複なく指定してください。", "PAGE_FIELD_INVALID", 409
                )
            current = copy.deepcopy(stored.current_pages_json)
            payload = current[page_id]
            candidate = OcrDocument.model_validate(payload["result"])
            edited_paths = set()
            for edit in correction.fields:
                path = mappings[edit.mapping_id]["targetPath"]
                original = get_mapped_reading(candidate, path)
                if original is None:
                    raise print_error("補正元の項目がありません。", "PAGE_RESULT_CHANGED", 409)
                reading = FieldReading.model_validate(
                    {
                        **original.model_dump(mode="json", by_alias=True),
                        **edit.model_dump(mode="json", by_alias=True, exclude={"mapping_id"}),
                    }
                )
                assign_mapped_reading(candidate, path, reading)
                edited_paths.add(path)
            from app.domain.checklist import apply_write_ins

            apply_write_ins(candidate, page["fieldMappings"])
            candidate.review_items = [
                i for i in candidate.review_items if i.path not in edited_paths
            ]
            validator = DeterministicValidator()
            report = validator.validate(
                candidate,
                image_quality=payload.get("quality"),
                configured_paths={m["targetPath"] for m in mappings.values()},
            )
            candidate = validator.merge_review_items(candidate, report)
            self._validate_readings(candidate, page)
            payload["result"] = candidate.model_dump(mode="json", by_alias=True)
            payload["validation"] = report.to_dict()
            next_revision = stored.revision + 1
            changed = session.execute(
                update(PagedReview)
                .where(
                    PagedReview.job_id == job_id,
                    PagedReview.revision == correction.base_revision,
                )
                .values(current_pages_json=current, revision=next_revision)
            )
            if changed.rowcount != 1:
                raise print_error("別の変更が保存されています。", "REVISION_CONFLICT", 409)
            session.add(
                PagedReviewRevision(
                    job_id=job_id,
                    revision=next_revision,
                    source="USER_EDIT",
                    page_id=page_id,
                    pages_json=current,
                )
            )
            session.commit()
            session.refresh(stored)
            return self._response(job, pages, stored)

    def confirm(self, job_id: str, body: PageConfirmation) -> dict[str, Any]:
        with self.database.session() as session:
            begin_immediate(session)
            job, pages, stored = self._load(session, job_id)
            self._require_revision(stored, body.base_revision)
            if job.status != "REVIEW_REQUIRED":
                raise print_error("この結果は既に確定しています。", "INVALID_STATE_TRANSITION", 409)
            load_document_snapshot(job)
            if len(set(body.reviewed_page_ids)) != len(body.reviewed_page_ids) or set(
                body.reviewed_page_ids
            ) != {p["pageId"] for p in pages}:
                raise print_error(
                    "全ページを確認してから確定してください。", "PAGE_REVIEW_INCOMPLETE", 409
                )
            if not body.document_name.strip():
                raise print_error("書類名を入力してください。", "DOCUMENT_NAME_REQUIRED", 422)
            name = normalize_spreadsheet_output_name(body.document_name)
            validator = DeterministicValidator()
            for page in pages:
                payload = stored.current_pages_json[page["pageId"]]
                document = OcrDocument.model_validate(payload["result"])
                from app.domain.checklist import require_resolved_checklists
                require_resolved_checklists(document, page["fieldMappings"])
                report = validator.validate(
                    document,
                    image_quality=payload.get("quality"),
                    configured_paths={m["targetPath"] for m in page["fieldMappings"]},
                )
                if not report.valid or any(i.severity == "error" for i in document.review_items):
                    raise print_error(
                        "errorレベルの確認事項を解消してください。", "UNRESOLVED_REVIEW_ITEMS", 409
                    )
            now = datetime.now(UTC)
            self._advance_state(session, stored, "CONFIRM")
            stored.document_name = name
            session.add(
                PagedConfirmation(
                    job_id=job_id,
                    revision=stored.revision,
                    snapshot_sha256=stored.snapshot_sha256,
                    pages_json=copy.deepcopy(stored.current_pages_json),
                    document_name=name,
                    confirmed_at=now,
                )
            )
            job.status = "CONFIRMED"
            job.confirmed_at = now
            session.commit()
            return self._response(job, pages, stored)

    def reopen(self, job_id: str, body: PageReopen) -> dict[str, Any]:
        with self.database.session() as session:
            begin_immediate(session)
            job, pages, stored = self._load(session, job_id)
            self._require_revision(stored, body.base_revision)
            if job.status != "CONFIRMED":
                raise print_error(
                    "確定済みの結果だけを再編集できます。", "INVALID_STATE_TRANSITION", 409
                )
            self._advance_state(session, stored, "REOPEN")
            job.status = "REVIEW_REQUIRED"
            job.confirmed_at = None
            session.commit()
            return self._response(job, pages, stored)

    @staticmethod
    def _require_revision(stored: PagedReview, base_revision: int) -> None:
        if stored.revision != base_revision:
            raise print_error(
                "別の変更が保存されています。再取得してください。", "REVISION_CONFLICT", 409
            )

    @staticmethod
    def _advance_state(session: Session, stored: PagedReview, source: str) -> None:
        # State changes are also revisions, preventing stale requests after reopen.
        stored.revision += 1
        session.add(
            PagedReviewRevision(
                job_id=stored.job_id,
                revision=stored.revision,
                source=source,
                pages_json=copy.deepcopy(stored.current_pages_json),
            )
        )
