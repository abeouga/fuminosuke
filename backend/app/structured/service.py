"""Confirmed documents of one tenant database, projected read-only.

Confirmation is decided exactly as the review/export paths decide it (job status plus the
revision/snapshot match); a surviving confirmed row alone is never enough. Nothing here
writes, and no value is normalized, deduplicated or merged across documents.
"""

from __future__ import annotations

import builtins
import unicodedata
from collections.abc import Iterator
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.db.models import PagedConfirmation, PagedReview, ProcessingJob
from app.db.session import Database
from app.domain.enums import JobStatus, JobType
from app.domain.field_targets import field_key
from app.domain.ocr_models import OcrDocument
from app.editing.errors import reject
from app.repositories.ocr_result_repository import OcrResultRepository
from app.services.document_series import DocumentSeries
from app.services.paged_document_snapshot import load_document_snapshot
from app.structured.contracts import SCHEMA_VERSION
from app.structured.projection import project_values

SUMMARY_KEYS = (
    "jobId",
    "kind",
    "templateId",
    "templateVersionId",
    "templateName",
    "documentName",
    "confirmedAt",
    "confirmedRevision",
    "identity",
    "seriesId",
    "seriesVersion",
)


def _fold(value: str) -> str:
    return unicodedata.normalize("NFKC", value).casefold()


class StructuredDocumentService:
    def __init__(self, database: Database) -> None:
        self._database = database

    def get(self, job_id: str) -> dict[str, Any]:
        with self._database.session() as session:
            job = session.get(ProcessingJob, job_id)
            if job is None or job.job_type != JobType.EXTRACT_DOCUMENT.value:
                raise reject(
                    "STRUCTURED_DOCUMENT_NOT_FOUND", "指定された読み取り結果が見つかりません。", 404
                )
            _, versions = DocumentSeries(session).view()
            return self._with_series(self._project(job), versions)

    def list(
        self, *, limit: int, template_id: str | None = None, query: str | None = None
    ) -> list[dict[str, Any]]:
        if not 1 <= limit <= 1000:
            raise reject("INVALID_ARGUMENT", "取得件数は1〜1000で指定してください。", 422)
        needle = _fold(query.strip()) if query and query.strip() else None
        items: list[dict[str, Any]] = []
        with self._database.session() as session:
            # Only the newest confirmed version of a document series is listed.
            superseded, versions = DocumentSeries(session).view()
            for job in self._confirmed_jobs(session):
                if job.id in superseded:
                    continue
                try:
                    document = self._with_series(self._project(job), versions)
                except AppError:
                    continue  # Not confirmed by the authoritative check; never listed.
                if template_id and document["templateId"] != template_id:
                    continue
                if needle is not None and not self._matches(document, needle):
                    continue
                items.append({key: document[key] for key in SUMMARY_KEYS})
                if len(items) >= limit:
                    break
        return items

    def field_names(
        self, template_id: str, *, documents: int = 20
    ) -> builtins.list[dict[str, Any]]:
        """Field and column names of a template, taken from its newest confirmed documents.

        Only names the accepted structure already carries; nothing is inferred. A name is
        listed once per (kind, table title, label), in first-seen order.
        """
        seen: set[tuple[str, str | None, str]] = set()
        names: builtins.list[dict[str, Any]] = []

        def add(kind: str, table_title: str | None, label: str) -> None:
            key = (kind, table_title, label)
            if key not in seen:
                seen.add(key)
                names.append({"label": label, "kind": kind, "tableTitle": table_title})

        for summary in self.list(limit=documents, template_id=template_id):
            try:
                document = self.get(summary["jobId"])
            except AppError:
                continue  # Reopened between listing and reading.
            for reading in document["fields"]:
                if reading["tableId"] is None:
                    add("field", None, reading["label"])
            for table in document["tables"]:
                for column in table["columns"]:
                    add("column", table["title"], column["label"])
        return names

    @staticmethod
    def _confirmed_jobs(session: Session) -> Iterator[ProcessingJob]:
        statement = (
            select(ProcessingJob)
            .where(
                ProcessingJob.job_type == JobType.EXTRACT_DOCUMENT.value,
                ProcessingJob.status == JobStatus.CONFIRMED.value,
            )
            .order_by(
                ProcessingJob.confirmed_at.desc(),
                ProcessingJob.created_at.desc(),
                ProcessingJob.id.desc(),
            )
        )
        yield from session.scalars(statement)

    @staticmethod
    def _matches(document: dict[str, Any], needle: str) -> bool:
        haystack = [document.get("documentName")]
        haystack.extend(item.get("value") for item in document["identity"])
        return any(isinstance(text, str) and needle in _fold(text) for text in haystack)

    @staticmethod
    def _with_series(
        document: dict[str, Any], versions: dict[str, tuple[str, int]]
    ) -> dict[str, Any]:
        series_id, version = versions.get(document["jobId"], (document["jobId"], 1))
        return {**document, "seriesId": series_id, "seriesVersion": version}

    def _project(self, job: ProcessingJob) -> dict[str, Any]:
        if "jobPages" in job.config_snapshot_json:
            return self._paged(job)
        return self._single(job)

    @staticmethod
    def _not_confirmed() -> AppError:
        return reject("STRUCTURED_NOT_CONFIRMED", "確定済みの結果だけ取得できます。", 409)

    def _single(self, job: ProcessingJob) -> dict[str, Any]:
        session = Session.object_session(job)
        assert session is not None
        repository = OcrResultRepository(session)
        stored = repository.get_by_job(job.id)
        confirmed = repository.get_confirmed(job.id)
        if (
            job.status != JobStatus.CONFIRMED.value
            or stored is None
            or confirmed is None
            or confirmed.ocr_result_revision != stored.revision
        ):
            raise self._not_confirmed()
        snapshot = job.config_snapshot_json
        template_snapshot = snapshot.get("templateSnapshot")
        template_snapshot = template_snapshot if isinstance(template_snapshot, dict) else {}
        config = template_snapshot.get("config")
        config = config if isinstance(config, dict) else {}
        document = OcrDocument.model_validate(confirmed.confirmed_json).model_dump(
            mode="json", by_alias=True
        )
        body = project_values(
            document,
            mappings=snapshot.get("fieldMappings"),
            structure=snapshot.get("reviewStructure"),
            logical_ids=snapshot.get("logicalFieldIds"),
            identity_fields=config.get("documentIdentityFields"),
        )
        return {
            "schemaVersion": SCHEMA_VERSION,
            "jobId": job.id,
            "kind": "single",
            "templateId": str(snapshot.get("templateId") or document["templateId"]),
            "templateVersionId": job.template_version_id or snapshot.get("templateVersionId"),
            "templateName": str(config.get("name") or ""),
            "documentName": confirmed.document_name,
            "confirmedAt": confirmed.confirmed_at,
            "confirmedRevision": confirmed.ocr_result_revision,
            **body,
        }

    def _paged(self, job: ProcessingJob) -> dict[str, Any]:
        session = Session.object_session(job)
        assert session is not None
        stored = session.get(PagedReview, job.id)
        if job.status != JobStatus.CONFIRMED.value or stored is None:
            raise self._not_confirmed()
        confirmed = session.get(PagedConfirmation, (job.id, stored.revision))
        if (
            confirmed is None
            or confirmed.snapshot_sha256 != stored.snapshot_sha256
            or confirmed.pages_json != stored.current_pages_json
            or confirmed.document_name != stored.document_name
            or job.confirmed_at is None
        ):
            raise self._not_confirmed()
        snapshot = load_document_snapshot(job)
        pages = job.config_snapshot_json["jobPages"]["pages"]
        page_by_mapping: dict[str, str] = {}
        readings: dict[str, Any] = {}
        labels: dict[str, str] = {}
        for page in pages:
            page_id = page["pageId"]
            payload = confirmed.pages_json.get(page_id)
            if not isinstance(payload, dict):
                raise self._not_confirmed()
            document = OcrDocument.model_validate(payload["result"]).model_dump(
                mode="json", by_alias=True
            )
            for mapping in page["fieldMappings"]:
                key = field_key(mapping["targetPath"])
                reading = document["fields"].get(key) if key is not None else None
                if key is None or reading is None or key in readings:
                    raise reject(
                        "STRUCTURED_SNAPSHOT_INVALID", "ページの項目所有権を確認できません。", 409
                    )
                page_by_mapping[mapping["mappingId"]] = page_id
                readings[key] = reading
                label = document["fieldLabels"].get(key)
                if isinstance(label, str):
                    labels[key] = label
        config = snapshot["config"]
        body = project_values(
            {"fields": readings, "fieldLabels": labels},
            mappings=[m for m in config["fieldMappings"] if m.get("enabled", True)],
            structure=snapshot.get("reviewStructure"),
            logical_ids=snapshot.get("logicalFieldIds"),
            identity_fields=config.get("documentIdentityFields"),
            page_by_mapping=page_by_mapping,
        )
        return {
            "schemaVersion": SCHEMA_VERSION,
            "jobId": job.id,
            "kind": "paged",
            "templateId": str(snapshot.get("templateId") or ""),
            "templateVersionId": snapshot.get("templateVersionId"),
            "templateName": str(config.get("name") or ""),
            "documentName": confirmed.document_name,
            "confirmedAt": confirmed.confirmed_at,
            "confirmedRevision": confirmed.revision,
            **body,
        }
