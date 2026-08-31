from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable

import anyio

from app.core.errors import AppError
from app.services.google_sheets_sync_service import GoogleSheetsSyncService, SpreadsheetEdit
from app.services.ocr_result_service import OcrResultService

logger = logging.getLogger(__name__)


class GoogleSheetsEditWorker:
    def __init__(
        self,
        sheets: GoogleSheetsSyncService,
        results: OcrResultService,
        *,
        poll_interval_seconds: int,
        mutation_notifier: Callable[[], None] | None = None,
    ) -> None:
        self._sheets = sheets
        self._results = results
        self._poll_interval_seconds = poll_interval_seconds
        self._mutation_notifier = mutation_notifier
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if not self._sheets.enabled or self._task is not None:
            return
        self._task = asyncio.create_task(self._run(), name="google-sheets-edit-worker")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        await asyncio.gather(self._task, return_exceptions=True)
        self._task = None

    @property
    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def _run(self) -> None:
        while True:
            try:
                edits = await anyio.to_thread.run_sync(self._sheets.fetch_pending_edits)
                for edit in edits:
                    await self._process(edit)
            except asyncio.CancelledError:
                raise
            except AppError as exc:
                if exc.code not in {
                    "GOOGLE_ACCOUNT_REQUIRED",
                    "GOOGLE_AUTH_REAUTHORIZATION_REQUIRED",
                    "GOOGLE_AUTH_REFRESH_FAILED",
                }:
                    logger.warning(
                        "Google Sheets edit polling is temporarily unavailable",
                        exc_info=True,
                    )
            except Exception:
                logger.warning(
                    "Google Sheets edit polling is temporarily unavailable",
                    exc_info=True,
                )
            await asyncio.sleep(self._poll_interval_seconds)

    async def _process(self, edit: SpreadsheetEdit) -> None:
        outcome = await anyio.to_thread.run_sync(self._results.apply_spreadsheet_edit, edit)
        if outcome.status == "APPLIED" and self._mutation_notifier is not None:
            self._mutation_notifier()
        try:
            await anyio.to_thread.run_sync(
                lambda: self._sheets.mark_edit_processed(
                    queue_row=edit.queue_row,
                    status=outcome.status,
                    error=outcome.error_message,
                    applied_revision=outcome.revision,
                    spreadsheet_id=edit.spreadsheet_id,
                    expected_subject=edit.google_subject,
                )
            )
        except Exception:
            logger.exception(
                "Could not publish Google Sheets edit status",
                extra={"editId": edit.edit_id, "jobId": edit.job_id},
            )
