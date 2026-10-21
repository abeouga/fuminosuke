"""Export all confirmed pages using their accepted source and explicit cell ownership."""

from __future__ import annotations

import copy
import hashlib
from dataclasses import dataclass

from sqlalchemy import text

from app.db.models import PagedConfirmation
from app.db.session import Database
from app.domain.ocr_models import OcrDocument
from app.domain.print_layout import print_error
from app.ocr.deterministic_mapper import get_mapped_reading
from app.services.paged_document_snapshot import load_document_snapshot
from app.services.paged_review_service import PagedReviewService
from app.services.paged_workbook import render_workbook
from app.storage.local_storage import LocalStorageService


@dataclass(frozen=True)
class PagedSpreadsheet:
    content: bytes
    file_name: str
    revision: int


class PagedOutputService:
    def __init__(self, database: Database, storage: LocalStorageService):
        self.database, self.storage = database, storage

    def render(self, job_id: str, revision: int) -> PagedSpreadsheet:
        reviews = PagedReviewService(self.database)
        with self.database.session() as session:
            # A WAL read transaction sees one state without locking correction writers.
            session.execute(text("BEGIN"))
            job, pages, stored = reviews._load(session, job_id)
            if job.status != "CONFIRMED" or stored.revision != revision:
                raise print_error(
                    "確認済みの内容を再取得してください。", "PAGE_OUTPUT_REVISION_CHANGED", 409
                )
            confirmed = session.get(PagedConfirmation, (job_id, revision))
            assert confirmed is not None  # _load verifies the full confirmed snapshot.
            snapshot = load_document_snapshot(job, self.storage)
            results = copy.deepcopy(confirmed.pages_json)
            name = confirmed.document_name
            accepted_sha = confirmed.snapshot_sha256
            pages = copy.deepcopy(pages)
        content = self.storage.read_bytes(snapshot["sourceWorkbook"]["relativePath"])
        if hashlib.sha256(content).hexdigest() != snapshot["sourceWorkbook"]["sha256"]:
            raise print_error("受付原本のSHAが一致しません。", "PAGE_OUTPUT_SOURCE_CHANGED", 409)
        documents = {
            p["pageId"]: OcrDocument.model_validate(results[p["pageId"]]["result"]) for p in pages
        }
        readings = []
        for binding in snapshot["outputBindings"]:
            reading = get_mapped_reading(documents[binding["pageId"]], binding["targetPath"])
            if reading is None or reading.source_region_id != binding["mappingId"]:
                raise print_error(
                    "確定値のセル所有権が一致しません。", "PAGE_OUTPUT_MAPPING_INVALID", 409
                )
            value = (
                None
                if reading.status == "blank"
                else (reading.normalized if reading.normalized is not None else reading.raw)
            )
            readings.append({"binding": binding, "value": value})
        output = render_workbook(content, snapshot, readings)
        with self.database.session() as session:
            session.execute(text("BEGIN"))
            job, _, stored = reviews._load(session, job_id)
            if (
                job.status != "CONFIRMED"
                or stored.revision != revision
                or stored.snapshot_sha256 != accepted_sha
            ):
                raise print_error(
                    "生成中に確認状態が変わりました。", "PAGE_OUTPUT_REVISION_CHANGED", 409
                )
        return PagedSpreadsheet(output, f"{name}.xlsx", revision)
