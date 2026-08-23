from __future__ import annotations

import logging
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from threading import Lock
from typing import Any
from urllib.parse import quote, urlencode

from google.auth.transport.requests import AuthorizedSession
from google.oauth2.credentials import Credentials
from requests import HTTPError

from app.core.config import GoogleSheetsSettings
from app.core.errors import AppError
from app.domain.field_targets import (
    CALVING_FIELDS,
    DOCUMENT_FIELDS,
    PEDIGREE_FIELDS,
    SALE_CALF_FIELDS,
)

logger = logging.getLogger(__name__)

_API_ROOT = "https://sheets.googleapis.com/v4/spreadsheets"
SpreadsheetTargetProvider = Callable[[], tuple[str | None, str | None]]
SpreadsheetTargetSaver = Callable[[str, str], None]
OutputLayoutProvider = Callable[[str | None], dict[str, Any] | None]
DocumentTargetProvider = Callable[[str | None], tuple[str | None, str | None]]
DocumentTargetCreator = Callable[[str, str], tuple[str, str]]
SpreadsheetTargetsProvider = Callable[[], list[tuple[str, str]]]
DocumentSyncReporter = Callable[[str, str, str | None], None]

_OUTPUT_SHEET_NAME = "帳票レイアウト"
_BINDING_NOTE_PREFIX = "llm-ocr:field:"
_TEMPLATE_NOTE_PREFIX = "llm-ocr:template-version:"
_MAX_LAYOUT_ROWS = 500
_MAX_LAYOUT_COLUMNS = 100


@dataclass(frozen=True, slots=True)
class SpreadsheetSyncStatus:
    enabled: bool
    status: str
    spreadsheet_url: str | None
    last_error: str | None = None

    def payload(self) -> dict[str, object]:
        return {
            "enabled": self.enabled,
            "status": self.status,
            "spreadsheetUrl": self.spreadsheet_url,
            "lastError": self.last_error,
        }


@dataclass(frozen=True, slots=True)
class SpreadsheetEdit:
    queue_row: int
    edit_id: str
    job_id: str
    record_id: str
    field_id: str
    previous_value: str | None
    new_value: str | None
    base_revision: int
    source_sheet: str
    source_cell: str
    spreadsheet_id: str | None = None


class GoogleSheetsSyncService:
    DOCUMENT_HEADERS = (
        "書類ID",
        "状態",
        "個体識別番号",
        "牛名",
        "生年月日",
        "登録番号",
        "毛色",
        "繁殖者",
        "所有者",
        "父",
        "母",
        "父方祖父",
        "父方祖母",
        "母方祖父",
        "母方祖母",
        "要確認件数",
        "最終更新日時",
        "修正",
        "OCRジョブID",
        "__revision",
        "__recordId",
    )
    SALE_HEADERS = (
        "書類ID",
        "明細番号",
        "状態",
        "個体識別番号",
        "牛名",
        "産次",
        "日齢",
        "体重kg",
        "価格円",
        "種雄牛名",
        "売却日",
        "備考",
        "最終更新日時",
        "修正",
        "OCRジョブID",
        "__revision",
        "__recordId",
    )
    CALVING_HEADERS = (
        "書類ID",
        "明細番号",
        "状態",
        "個体識別番号",
        "牛名",
        "産歴番号",
        "分娩年月日",
        "性別",
        "分娩状況",
        "備考",
        "最終更新日時",
        "修正",
        "OCRジョブID",
        "__revision",
        "__recordId",
    )
    EDIT_QUEUE_HEADERS = (
        "editId",
        "status",
        "createdAt",
        "jobId",
        "recordId",
        "fieldId",
        "previousValue",
        "newValue",
        "baseRevision",
        "sourceSheet",
        "sourceCell",
        "processedAt",
        "error",
        "appliedRevision",
    )
    DOCUMENT_WIDTHS = (
        280,
        80,
        120,
        120,
        120,
        110,
        70,
        120,
        120,
        110,
        110,
        110,
        110,
        110,
        110,
        90,
        190,
        70,
        280,
        90,
        180,
    )
    SALE_WIDTHS = (
        280,
        70,
        80,
        120,
        120,
        70,
        70,
        80,
        100,
        120,
        110,
        160,
        190,
        70,
        280,
        90,
        180,
    )
    CALVING_WIDTHS = (
        280,
        70,
        80,
        120,
        120,
        80,
        120,
        70,
        110,
        160,
        190,
        70,
        280,
        90,
        180,
    )
    EDIT_QUEUE_WIDTHS = (250, 90, 190, 280, 180, 260, 160, 160, 100, 140, 90, 190, 260, 100)

    def __init__(
        self,
        settings: GoogleSheetsSettings,
        credentials_provider: Callable[[], Credentials] | None = None,
        spreadsheet_target_provider: SpreadsheetTargetProvider | None = None,
        spreadsheet_target_saver: SpreadsheetTargetSaver | None = None,
        output_layout_provider: OutputLayoutProvider | None = None,
        document_target_provider: DocumentTargetProvider | None = None,
        document_target_creator: DocumentTargetCreator | None = None,
        spreadsheet_targets_provider: SpreadsheetTargetsProvider | None = None,
        document_sync_reporter: DocumentSyncReporter | None = None,
    ) -> None:
        self._settings = settings
        self._credentials_provider = credentials_provider
        self._spreadsheet_target_provider = spreadsheet_target_provider
        self._spreadsheet_target_saver = spreadsheet_target_saver
        self._output_layout_provider = output_layout_provider
        self._document_target_provider = document_target_provider
        self._document_target_creator = document_target_creator
        self._spreadsheet_targets_provider = spreadsheet_targets_provider
        self._document_sync_reporter = document_sync_reporter
        self._active_document_id: str | None = None
        self._session: AuthorizedSession | None = None
        self._lock = Lock()
        self._statuses: dict[str, SpreadsheetSyncStatus] = {}
        self._sheet_ids: dict[str, int] = {}
        self._sheet_grid_sizes: dict[str, tuple[int, int]] = {}
        self._spreadsheet_id: str | None = (
            settings.spreadsheet_id if spreadsheet_target_provider is None else None
        )
        self._spreadsheet_url: str | None = (
            self._url_for(settings.spreadsheet_id)
            if settings.spreadsheet_id and spreadsheet_target_provider is None
            else None
        )
        self._initialized = False

    @property
    def enabled(self) -> bool:
        return self._settings.enabled

    @property
    def spreadsheet_url(self) -> str | None:
        self._load_spreadsheet_target()
        return self._spreadsheet_url

    def status(self, job_id: str, document_id: str | None = None) -> SpreadsheetSyncStatus:
        if not self.enabled:
            return SpreadsheetSyncStatus(False, "disabled", None)
        if job_id not in self._statuses and document_id and self._document_target_provider:
            _, url = self._document_target_provider(document_id)
            return SpreadsheetSyncStatus(True, "ready", url)
        return self._statuses.get(
            job_id,
            SpreadsheetSyncStatus(True, "ready", self.spreadsheet_url),
        )

    def target_status(self) -> SpreadsheetSyncStatus:
        """Return the existing Sheet target without writing any OCR rows."""
        if not self.enabled:
            return SpreadsheetSyncStatus(False, "disabled", None)
        return SpreadsheetSyncStatus(
            True,
            "ready",
            None if self._document_target_provider is not None else self.spreadsheet_url,
        )

    def initialize(self) -> SpreadsheetSyncStatus:
        if not self.enabled:
            return SpreadsheetSyncStatus(False, "disabled", None)
        with self._lock:
            self._initialize_locked()
        return SpreadsheetSyncStatus(True, "ready", self.spreadsheet_url)

    def capture_output_layout(self, *, document_id: str | None = None) -> dict[str, Any]:
        if not self.enabled:
            raise AppError(
                status=409,
                code="GOOGLE_SHEETS_DISABLED",
                title="Google Sheets is disabled",
                detail="Googleスプレッドシート連携が無効です。",
            )
        with self._lock:
            self._select_document_target(document_id, create=False)
            self._initialize_locked()
            return self._capture_output_layout_locked()

    def reset_authorization(self) -> None:
        with self._lock:
            self._session = None
            self._sheet_ids = {}
            self._sheet_grid_sizes = {}
            self._spreadsheet_id = (
                self._settings.spreadsheet_id if self._spreadsheet_target_provider is None else None
            )
            self._spreadsheet_url = (
                self._url_for(self._settings.spreadsheet_id)
                if self._settings.spreadsheet_id and self._spreadsheet_target_provider is None
                else None
            )
            self._initialized = False

    def sync(
        self,
        *,
        job_id: str,
        revision: int,
        document: dict[str, Any],
        confirmed: bool,
        template_version_id: str | None = None,
        document_id: str | None = None,
    ) -> SpreadsheetSyncStatus:
        if not self.enabled:
            return SpreadsheetSyncStatus(False, "disabled", None)
        try:
            with self._lock:
                self._select_document_target(document_id, create=bool(document_id))
                self._initialize_locked()
                self._sync_locked(
                    job_id=job_id,
                    revision=revision,
                    document=document,
                    confirmed=confirmed,
                    template_version_id=template_version_id,
                    document_id=document_id,
                )
            status = SpreadsheetSyncStatus(True, "synced", self.spreadsheet_url)
        except Exception as exc:  # The local OCR result must remain available.
            logger.exception("Google Sheets sync failed", extra={"jobId": job_id})
            status = SpreadsheetSyncStatus(
                True,
                "failed",
                self.spreadsheet_url,
                self._safe_error(exc),
            )
        self._statuses[job_id] = status
        if document_id and self._document_sync_reporter is not None:
            self._document_sync_reporter(document_id, status.status, status.last_error)
        return status

    def _select_document_target(self, document_id: str | None, *, create: bool) -> None:
        if self._document_target_provider is None:
            return
        spreadsheet_id, spreadsheet_url = self._document_target_provider(document_id)
        if not spreadsheet_id and document_id and create:
            if self._document_target_creator is None:
                raise RuntimeError("台帳別スプレッドシート作成機能が設定されていません。")
            spreadsheet_id, spreadsheet_url = self._document_target_creator(
                document_id,
                self._settings.spreadsheet_title,
            )
        if not spreadsheet_id:
            if document_id:
                raise RuntimeError("この台帳のスプレッドシートはまだ作成されていません。")
            return
        if spreadsheet_id == self._spreadsheet_id:
            self._active_document_id = document_id
            return
        self._spreadsheet_id = spreadsheet_id
        self._spreadsheet_url = spreadsheet_url or self._url_for(spreadsheet_id)
        self._active_document_id = document_id
        self._sheet_ids = {}
        self._sheet_grid_sizes = {}
        self._initialized = False

    def _authorized_session(self) -> AuthorizedSession:
        if self._session is not None:
            return self._session
        if self._credentials_provider is None:
            raise RuntimeError("Googleアカウントが接続されていません。")
        credentials = self._credentials_provider()
        self._session = AuthorizedSession(credentials)  # type: ignore[no-untyped-call]
        return self._session

    def _initialize_locked(self) -> None:
        if self._initialized:
            return
        definitions = (
            (self._settings.sale_sheet_name, self.SALE_HEADERS),
            (self._settings.calving_sheet_name, self.CALVING_HEADERS),
            (self._settings.document_sheet_name, self.DOCUMENT_HEADERS),
            (self._settings.edit_queue_sheet_name, self.EDIT_QUEUE_HEADERS),
        )
        sheet_definitions = (*definitions, (_OUTPUT_SHEET_NAME, ()))
        response = self._load_or_create_spreadsheet(sheet_definitions)
        existing = {
            item["properties"]["title"]: int(item["properties"]["sheetId"])
            for item in response.get("sheets", [])
            if isinstance(item, dict) and isinstance(item.get("properties"), dict)
        }
        self._sheet_grid_sizes = {
            item["properties"]["title"]: (
                int(item["properties"].get("gridProperties", {}).get("rowCount", 1000)),
                int(item["properties"].get("gridProperties", {}).get("columnCount", 26)),
            )
            for item in response.get("sheets", [])
            if isinstance(item, dict)
            and isinstance(item.get("properties"), dict)
            and isinstance(item["properties"].get("title"), str)
        }
        missing = [title for title, _ in sheet_definitions if title not in existing]
        disposable_default_sheet_id: int | None = None
        if (
            self._active_document_id is not None
            and len(existing) == 1
            and len(missing) == len(sheet_definitions)
        ):
            default_title, default_sheet_id = next(iter(existing.items()))
            if default_title in {"Sheet1", "シート1"}:
                disposable_default_sheet_id = default_sheet_id
        if missing:
            requests: list[dict[str, Any]] = [
                {"addSheet": {"properties": {"title": title}}} for title in missing
            ]
            if disposable_default_sheet_id is not None:
                requests.append({"deleteSheet": {"sheetId": disposable_default_sheet_id}})
            created = self._request(
                "POST",
                f"{_API_ROOT}/{self._required_spreadsheet_id()}:batchUpdate",
                json={"requests": requests},
            )
            for reply in created.get("replies", []):
                properties = reply.get("addSheet", {}).get("properties", {})
                title = properties.get("title")
                sheet_id = properties.get("sheetId")
                if isinstance(title, str) and isinstance(sheet_id, int):
                    existing[title] = sheet_id
                    self._sheet_grid_sizes[title] = (
                        int(properties.get("gridProperties", {}).get("rowCount", 1000)),
                        int(properties.get("gridProperties", {}).get("columnCount", 26)),
                    )
            if disposable_default_sheet_id is not None:
                existing.pop(default_title, None)
                self._sheet_grid_sizes.pop(default_title, None)
        self._sheet_ids = existing
        self._write_headers(definitions)
        self._initialized = True

    def _load_or_create_spreadsheet(
        self,
        definitions: tuple[tuple[str, tuple[str, ...]], ...],
    ) -> dict[str, Any]:
        self._load_spreadsheet_target()
        if self._spreadsheet_id:
            return self._request(
                "GET",
                f"{_API_ROOT}/{self._spreadsheet_id}",
                params={
                    "fields": (
                        "spreadsheetId,spreadsheetUrl,"
                        "sheets.properties(sheetId,title,gridProperties(rowCount,columnCount))"
                    )
                },
            )
        created = self._request(
            "POST",
            _API_ROOT,
            params={
                "fields": (
                    "spreadsheetId,spreadsheetUrl,"
                    "sheets.properties(sheetId,title,gridProperties(rowCount,columnCount))"
                )
            },
            json={
                "properties": {"title": self._settings.spreadsheet_title},
                "sheets": [{"properties": {"title": title}} for title, _ in definitions],
            },
        )
        spreadsheet_id = created.get("spreadsheetId")
        spreadsheet_url = created.get("spreadsheetUrl")
        if not isinstance(spreadsheet_id, str) or not spreadsheet_id:
            raise RuntimeError("Googleスプレッドシートの作成結果にIDがありません。")
        if not isinstance(spreadsheet_url, str) or not spreadsheet_url:
            spreadsheet_url = self._url_for(spreadsheet_id)
        self._spreadsheet_id = spreadsheet_id
        self._spreadsheet_url = spreadsheet_url
        if self._spreadsheet_target_saver is not None:
            self._spreadsheet_target_saver(spreadsheet_id, spreadsheet_url)
        logger.info("Created a spreadsheet for the connected Google account")
        return created

    def _load_spreadsheet_target(self) -> None:
        if self._spreadsheet_id is not None or self._spreadsheet_target_provider is None:
            return
        spreadsheet_id, spreadsheet_url = self._spreadsheet_target_provider()
        if not spreadsheet_id:
            return
        self._spreadsheet_id = spreadsheet_id
        self._spreadsheet_url = spreadsheet_url or self._url_for(spreadsheet_id)

    def _write_headers(self, definitions: tuple[tuple[str, tuple[str, ...]], ...]) -> None:
        spreadsheet_id = self._required_spreadsheet_id()
        data: list[dict[str, object]] = []
        format_requests: list[dict[str, object]] = []
        widths_by_title = {
            self._settings.document_sheet_name: self.DOCUMENT_WIDTHS,
            self._settings.sale_sheet_name: self.SALE_WIDTHS,
            self._settings.calving_sheet_name: self.CALVING_WIDTHS,
            self._settings.edit_queue_sheet_name: self.EDIT_QUEUE_WIDTHS,
        }
        for title, headers in definitions:
            data.append(
                {
                    "range": f"{self._quote_sheet(title)}!A1:{self._column_name(len(headers))}1",
                    "values": [list(headers)],
                }
            )
            sheet_id = self._sheet_ids[title]
            if title != self._settings.edit_queue_sheet_name:
                desired_index = (
                    self._settings.sale_sheet_name,
                    self._settings.calving_sheet_name,
                    self._settings.document_sheet_name,
                ).index(title)
                format_requests.append(
                    {
                        "updateSheetProperties": {
                            "properties": {"sheetId": sheet_id, "index": desired_index},
                            "fields": "index",
                        }
                    }
                )
            format_requests.extend(
                [
                    {
                        "updateSheetProperties": {
                            "properties": {
                                "sheetId": sheet_id,
                                "gridProperties": {"frozenRowCount": 1},
                            },
                            "fields": "gridProperties.frozenRowCount",
                        }
                    },
                    {
                        "repeatCell": {
                            "range": {
                                "sheetId": sheet_id,
                                "startRowIndex": 0,
                                "endRowIndex": 1,
                                "startColumnIndex": 0,
                                "endColumnIndex": len(headers),
                            },
                            "cell": {
                                "userEnteredFormat": {
                                    "backgroundColor": {
                                        "red": 0.16,
                                        "green": 0.29,
                                        "blue": 0.24,
                                    },
                                    "textFormat": {
                                        "foregroundColor": {
                                            "red": 1,
                                            "green": 1,
                                            "blue": 1,
                                        },
                                        "bold": True,
                                    },
                                    "horizontalAlignment": "CENTER",
                                }
                            },
                            "fields": (
                                "userEnteredFormat.backgroundColor,"
                                "userEnteredFormat.textFormat,"
                                "userEnteredFormat.horizontalAlignment"
                            ),
                        }
                    },
                ]
            )
            format_requests.extend(self._column_width_requests(sheet_id, widths_by_title[title]))
            if title == self._settings.edit_queue_sheet_name:
                format_requests.append(
                    {
                        "updateSheetProperties": {
                            "properties": {"sheetId": sheet_id, "hidden": True},
                            "fields": "hidden",
                        }
                    }
                )
            else:
                format_requests.append(
                    {
                        "updateDimensionProperties": {
                            "range": {
                                "sheetId": sheet_id,
                                "dimension": "COLUMNS",
                                "startIndex": len(headers) - 3,
                                "endIndex": len(headers),
                            },
                            "properties": {"hiddenByUser": True},
                            "fields": "hiddenByUser",
                        }
                    }
                )
        self._request(
            "POST",
            f"{_API_ROOT}/{spreadsheet_id}/values:batchUpdate",
            json={"valueInputOption": "USER_ENTERED", "data": data},
        )
        self._request(
            "POST",
            f"{_API_ROOT}/{spreadsheet_id}:batchUpdate",
            json={"requests": format_requests},
        )

    @staticmethod
    def _column_width_requests(
        sheet_id: int,
        widths: tuple[int, ...],
    ) -> list[dict[str, object]]:
        requests: list[dict[str, object]] = []
        start = 0
        for index in range(1, len(widths) + 1):
            if index < len(widths) and widths[index] == widths[start]:
                continue
            requests.append(
                {
                    "updateDimensionProperties": {
                        "range": {
                            "sheetId": sheet_id,
                            "dimension": "COLUMNS",
                            "startIndex": start,
                            "endIndex": index,
                        },
                        "properties": {"pixelSize": widths[start]},
                        "fields": "pixelSize",
                    }
                }
            )
            start = index
        return requests

    def _sync_locked(
        self,
        *,
        job_id: str,
        revision: int,
        document: dict[str, Any],
        confirmed: bool,
        template_version_id: str | None,
        document_id: str | None,
    ) -> None:
        row_key = document_id or job_id
        status = "確定済み" if confirmed else "要確認"
        timestamp = datetime.now(UTC).astimezone().isoformat(timespec="seconds")
        individual_id = self._reading(document, "document", "unifiedEarTag")
        cattle_name = self._reading(document, "document", "cattleName")
        review_count = len(document.get("reviewItems", []))
        document_row = [
            row_key,
            status,
            self._identifier(individual_id),
            cattle_name,
            self._reading(document, "document", "birthDate"),
            self._reading(document, "document", "registrationNumber"),
            self._reading(document, "document", "coatColor"),
            self._reading(document, "document", "breeder"),
            self._reading(document, "document", "owner"),
            self._reading(document, "pedigree", "father"),
            self._reading(document, "pedigree", "mother"),
            self._reading(document, "pedigree", "paternalGrandfather"),
            self._reading(document, "pedigree", "paternalGrandmother"),
            self._reading(document, "pedigree", "maternalGrandfather"),
            self._reading(document, "pedigree", "maternalGrandmother"),
            review_count,
            timestamp,
            self._link_formula(job_id, "document.unifiedEarTag"),
            job_id,
            revision,
            "document",
        ]
        sale_rows = [
            [
                row_key,
                index + 1,
                status,
                self._identifier(individual_id),
                cattle_name,
                self._reading(row, "parity"),
                self._reading(row, "ageDays"),
                self._reading(row, "weightKg"),
                self._reading(row, "priceYen"),
                self._reading(row, "sireName"),
                self._reading(row, "saleDate"),
                self._reading(row, "notes"),
                timestamp,
                self._link_formula(job_id, f"saleCalves.{index}.parity"),
                job_id,
                revision,
                f"saleCalves.{index}",
            ]
            for index, row in enumerate(document.get("saleCalves", []))
            if isinstance(row, dict) and self._row_has_value(row, ignored={"parity"})
        ]
        calving_rows = [
            [
                row_key,
                index + 1,
                status,
                self._identifier(individual_id),
                cattle_name,
                self._reading(row, "parity"),
                self._reading(row, "calvingDate"),
                self._reading(row, "sex"),
                self._reading(row, "outcome"),
                self._reading(row, "notes"),
                timestamp,
                self._link_formula(job_id, f"calvings.{index}.parity"),
                job_id,
                revision,
                f"calvings.{index}",
            ]
            for index, row in enumerate(document.get("calvings", []))
            if isinstance(row, dict) and self._row_has_value(row, ignored={"parity"})
        ]
        self._upsert_single(self._settings.document_sheet_name, row_key, document_row)
        self._replace_detail_rows(
            self._settings.sale_sheet_name, row_key, sale_rows, len(self.SALE_HEADERS)
        )
        self._replace_detail_rows(
            self._settings.calving_sheet_name,
            row_key,
            calving_rows,
            len(self.CALVING_HEADERS),
        )
        saved_layout = (
            self._output_layout_provider(template_version_id)
            if self._output_layout_provider is not None
            else None
        )
        output_layout = saved_layout or self._default_output_layout(
            document,
            template_version_id=template_version_id,
        )
        self._render_output_layout_locked(output_layout, document)
        logger.info(
            "Google Sheets sync completed",
            extra={"jobId": job_id, "revision": revision, "confirmed": confirmed},
        )

    def _default_output_layout(
        self,
        document: dict[str, Any],
        *,
        template_version_id: str | None,
    ) -> dict[str, Any]:
        cells: list[dict[str, Any]] = []
        merge_rows: list[int] = []
        row_heights: dict[str, int] = {}
        current_row = 0
        max_columns = 2

        title_format = self._layout_format("1F4E78", bold=True, foreground="FFFFFF", size=14)
        section_format = self._layout_format("D9EAF7", bold=True, foreground="173F5D")
        header_format = self._layout_format(
            "5B9BD5", bold=True, foreground="FFFFFF", horizontal="CENTER"
        )
        label_format = self._layout_format("F3F6F8", bold=True, foreground="25333D")
        value_format = self._layout_format("FFFFFF", foreground="172431")

        def add_cell(
            row: int,
            column: int,
            value: object,
            cell_format: dict[str, Any],
            binding: str | None = None,
        ) -> None:
            cell: dict[str, Any] = {
                "row": row,
                "column": column,
                "value": self._user_entered_value(value),
                "format": cell_format,
            }
            if binding:
                cell["note"] = f"{_BINDING_NOTE_PREFIX}{binding}"
            cells.append(cell)

        def add_title(value: str) -> None:
            nonlocal current_row
            add_cell(current_row, 0, value, title_format)
            if template_version_id:
                cells[-1]["note"] = f"{_TEMPLATE_NOTE_PREFIX}{template_version_id}"
            merge_rows.append(current_row)
            row_heights[str(current_row)] = 30
            current_row += 2

        def add_key_value_section(
            title: str,
            fields: list[tuple[str, str]],
        ) -> None:
            nonlocal current_row
            add_cell(current_row, 0, title, section_format)
            merge_rows.append(current_row)
            row_heights[str(current_row)] = 24
            current_row += 1
            add_cell(current_row, 0, "項目", header_format)
            add_cell(current_row, 1, "読み取り結果", header_format)
            current_row += 1
            for label, binding in fields:
                add_cell(current_row, 0, label, label_format)
                add_cell(
                    current_row,
                    1,
                    self._binding_value(document, binding),
                    value_format,
                    binding,
                )
                current_row += 1
            current_row += 1

        def add_record_section(
            title: str,
            headers: list[str],
            rows: list[list[tuple[object, str | None]]],
        ) -> None:
            nonlocal current_row, max_columns
            if not headers:
                return
            max_columns = max(max_columns, len(headers))
            add_cell(current_row, 0, title, section_format)
            merge_rows.append(current_row)
            row_heights[str(current_row)] = 24
            current_row += 1
            for column, header in enumerate(headers):
                add_cell(current_row, column, header, header_format)
            current_row += 1
            for values in rows:
                for column, (value, binding) in enumerate(values):
                    add_cell(current_row, column, value, value_format, binding)
                current_row += 1
            current_row += 1

        generic_data = document.get("genericData")
        if isinstance(generic_data, dict):
            title = generic_data.get("templateName")
            add_title(str(title) if isinstance(title, str) and title else "帳票読み取り結果")
            standalone = generic_data.get("standaloneFields")
            standalone_fields: list[tuple[str, str]] = []
            if isinstance(standalone, list):
                for field in standalone:
                    if not isinstance(field, dict):
                        continue
                    binding = self._generic_field_binding(document, field)
                    if binding:
                        standalone_fields.append(
                            (str(field.get("name") or field.get("key")), binding)
                        )
            if standalone_fields:
                add_key_value_section("基本項目", standalone_fields)
            sections = generic_data.get("sections")
            if isinstance(sections, list):
                for section in sections:
                    if not isinstance(section, dict):
                        continue
                    records = section.get("records")
                    record_items = (
                        [item for item in records if isinstance(item, dict)]
                        if isinstance(records, list)
                        else []
                    )
                    first_fields = record_items[0].get("fields") if record_items else []
                    header_fields = (
                        [item for item in first_fields if isinstance(item, dict)]
                        if isinstance(first_fields, list)
                        else []
                    )
                    headers = [
                        str(field.get("name") or field.get("key")) for field in header_fields
                    ]
                    rows: list[list[tuple[object, str | None]]] = []
                    for record in record_items:
                        raw_fields = record.get("fields")
                        fields = (
                            [item for item in raw_fields if isinstance(item, dict)]
                            if isinstance(raw_fields, list)
                            else []
                        )
                        values: list[tuple[object, str | None]] = []
                        for field in fields:
                            binding = self._generic_field_binding(document, field)
                            values.append((self._binding_value(document, binding), binding))
                        rows.append(values)
                    add_record_section(
                        str(section.get("name") or section.get("key") or "明細"),
                        headers,
                        rows,
                    )
        else:
            add_title("帳票読み取り結果")
            add_key_value_section(
                "基本情報",
                [(label, f"document.{key}") for key, label in DOCUMENT_FIELDS.items()],
            )
            add_key_value_section(
                "血統",
                [(label, f"pedigree.{key}") for key, label in PEDIGREE_FIELDS.items()],
            )
            for collection, title, labels in (
                ("saleCalves", "売却子牛一覧", SALE_CALF_FIELDS),
                ("calvings", "繁殖履歴", CALVING_FIELDS),
            ):
                raw_rows = document.get(collection)
                records = (
                    [item for item in raw_rows if isinstance(item, dict)]
                    if isinstance(raw_rows, list)
                    else []
                )
                output_rows: list[list[tuple[object, str | None]]] = [
                    [
                        (
                            self._binding_value(document, f"{collection}.{index}.{key}"),
                            f"{collection}.{index}.{key}",
                        )
                        for key in labels
                    ]
                    for index, _record in enumerate(records)
                ]
                add_record_section(title, list(labels.values()), output_rows)
            custom_fields = document.get("customFields")
            if isinstance(custom_fields, dict) and custom_fields:
                add_key_value_section(
                    "追加項目",
                    [(key, f"customFields.{key}") for key in custom_fields],
                )

        merges = [
            {
                "startRowIndex": row,
                "endRowIndex": row + 1,
                "startColumnIndex": 0,
                "endColumnIndex": max_columns,
            }
            for row in merge_rows
            if max_columns > 1
        ]
        return {
            "schemaVersion": 1,
            "sheetName": _OUTPUT_SHEET_NAME,
            "rowCount": max(current_row, 1),
            "columnCount": max_columns,
            "frozenRowCount": 1,
            "frozenColumnCount": 0,
            "hideGridlines": True,
            "cells": cells,
            "merges": merges,
            "rowHeights": row_heights,
            "columnWidths": {"0": 220, **{str(index): 140 for index in range(1, max_columns)}},
            "bindingCount": sum(
                1 for cell in cells if str(cell.get("note", "")).startswith(_BINDING_NOTE_PREFIX)
            ),
        }

    def _render_output_layout_locked(
        self,
        raw_layout: dict[str, Any],
        document: dict[str, Any],
    ) -> None:
        layout = deepcopy(raw_layout)
        row_count = self._bounded_dimension(layout.get("rowCount"), _MAX_LAYOUT_ROWS, "行")
        column_count = self._bounded_dimension(layout.get("columnCount"), _MAX_LAYOUT_COLUMNS, "列")
        sheet_id = self._sheet_ids[_OUTPUT_SHEET_NAME]
        existing_rows, existing_columns = self._sheet_grid_sizes.get(_OUTPUT_SHEET_NAME, (1000, 26))
        grid_rows = max(existing_rows, row_count)
        grid_columns = max(existing_columns, column_count)
        rows: list[dict[str, Any]] = [{"values": []} for _ in range(row_count)]
        raw_cells = layout.get("cells")
        cells = raw_cells if isinstance(raw_cells, list) else []
        for raw_cell in cells:
            if not isinstance(raw_cell, dict):
                continue
            row = raw_cell.get("row")
            column = raw_cell.get("column")
            if not isinstance(row, int) or not isinstance(column, int):
                continue
            if row < 0 or column < 0 or row >= row_count or column >= column_count:
                continue
            while len(rows[row]["values"]) <= column:
                rows[row]["values"].append({})
            note = raw_cell.get("note")
            value = deepcopy(raw_cell.get("value"))
            if isinstance(note, str) and note.startswith(_BINDING_NOTE_PREFIX):
                binding = note.removeprefix(_BINDING_NOTE_PREFIX)
                value = self._user_entered_value(self._binding_value(document, binding))
            cell_data: dict[str, Any] = {}
            if isinstance(value, dict) and value:
                cell_data["userEnteredValue"] = value
            cell_format = raw_cell.get("format")
            if isinstance(cell_format, dict) and cell_format:
                cell_data["userEnteredFormat"] = cell_format
            if isinstance(note, str) and note:
                cell_data["note"] = note
            data_validation = raw_cell.get("dataValidation")
            if isinstance(data_validation, dict) and data_validation:
                cell_data["dataValidation"] = data_validation
            text_runs = raw_cell.get("textFormatRuns")
            if isinstance(text_runs, list) and text_runs:
                cell_data["textFormatRuns"] = text_runs
            rows[row]["values"][column] = cell_data

        full_range = {
            "sheetId": sheet_id,
            "startRowIndex": 0,
            "endRowIndex": grid_rows,
            "startColumnIndex": 0,
            "endColumnIndex": grid_columns,
        }
        requests: list[dict[str, Any]] = [
            {"unmergeCells": {"range": full_range}},
            {
                "updateCells": {
                    "range": full_range,
                    "fields": (
                        "userEnteredValue,userEnteredFormat,note,dataValidation,textFormatRuns"
                    ),
                }
            },
            {
                "updateSheetProperties": {
                    "properties": {
                        "sheetId": sheet_id,
                        "gridProperties": {
                            "rowCount": grid_rows,
                            "columnCount": grid_columns,
                            "frozenRowCount": self._non_negative_int(layout.get("frozenRowCount")),
                            "frozenColumnCount": self._non_negative_int(
                                layout.get("frozenColumnCount")
                            ),
                            "hideGridlines": bool(layout.get("hideGridlines", True)),
                        },
                    },
                    "fields": (
                        "gridProperties.rowCount,gridProperties.columnCount,"
                        "gridProperties.frozenRowCount,gridProperties.frozenColumnCount,"
                        "gridProperties.hideGridlines"
                    ),
                }
            },
            {
                "updateDimensionProperties": {
                    "range": {
                        "sheetId": sheet_id,
                        "dimension": "ROWS",
                        "startIndex": 0,
                        "endIndex": grid_rows,
                    },
                    "properties": {"pixelSize": 21, "hiddenByUser": False},
                    "fields": "pixelSize,hiddenByUser",
                }
            },
            {
                "updateDimensionProperties": {
                    "range": {
                        "sheetId": sheet_id,
                        "dimension": "COLUMNS",
                        "startIndex": 0,
                        "endIndex": grid_columns,
                    },
                    "properties": {"pixelSize": 100, "hiddenByUser": False},
                    "fields": "pixelSize,hiddenByUser",
                }
            },
            {
                "updateCells": {
                    "start": {"sheetId": sheet_id, "rowIndex": 0, "columnIndex": 0},
                    "rows": rows,
                    "fields": (
                        "userEnteredValue,userEnteredFormat,note,dataValidation,textFormatRuns"
                    ),
                }
            },
        ]
        for merge in self._valid_merges(layout.get("merges"), row_count, column_count):
            requests.append(
                {"mergeCells": {"range": {"sheetId": sheet_id, **merge}, "mergeType": "MERGE_ALL"}}
            )
        requests.extend(
            self._dimension_override_requests(
                sheet_id,
                "ROWS",
                layout.get("rowHeights"),
                row_count,
            )
        )
        requests.extend(
            self._dimension_override_requests(
                sheet_id,
                "COLUMNS",
                layout.get("columnWidths"),
                column_count,
            )
        )
        self._request(
            "POST",
            f"{_API_ROOT}/{self._required_spreadsheet_id()}:batchUpdate",
            json={"requests": requests},
        )
        self._sheet_grid_sizes[_OUTPUT_SHEET_NAME] = (grid_rows, grid_columns)

    def _capture_output_layout_locked(self) -> dict[str, Any]:
        spreadsheet_id = self._required_spreadsheet_id()
        response = self._request(
            "GET",
            f"{_API_ROOT}/{spreadsheet_id}",
            params={
                "includeGridData": "true",
                "ranges": _OUTPUT_SHEET_NAME,
                "fields": (
                    "sheets(properties(sheetId,title,gridProperties),merges,"
                    "data(startRow,startColumn,rowData.values(userEnteredValue,"
                    "userEnteredFormat,note,dataValidation,textFormatRuns),"
                    "rowMetadata(pixelSize,hiddenByUser),"
                    "columnMetadata(pixelSize,hiddenByUser)))"
                ),
            },
        )
        sheets = response.get("sheets")
        sheet = (
            next(
                (
                    item
                    for item in sheets
                    if isinstance(item, dict)
                    and isinstance(item.get("properties"), dict)
                    and item["properties"].get("title") == _OUTPUT_SHEET_NAME
                ),
                None,
            )
            if isinstance(sheets, list)
            else None
        )
        if not isinstance(sheet, dict):
            raise AppError(
                status=409,
                code="OUTPUT_LAYOUT_SHEET_NOT_FOUND",
                title="Output layout sheet was not found",
                detail="先にOCR結果をGoogleスプレッドシートへ出力してください。",
            )
        cells: list[dict[str, Any]] = []
        row_heights: dict[str, int] = {}
        column_widths: dict[str, int] = {}
        max_row = 0
        max_column = 0
        data_blocks = sheet.get("data")
        for block in data_blocks if isinstance(data_blocks, list) else []:
            if not isinstance(block, dict):
                continue
            start_row = self._non_negative_int(block.get("startRow"))
            start_column = self._non_negative_int(block.get("startColumn"))
            row_data = block.get("rowData")
            if isinstance(row_data, list):
                for row_offset, row in enumerate(row_data):
                    if not isinstance(row, dict):
                        continue
                    values = row.get("values")
                    if not isinstance(values, list):
                        continue
                    for column_offset, cell in enumerate(values):
                        if not isinstance(cell, dict):
                            continue
                        captured = self._capture_cell(cell)
                        if not captured:
                            continue
                        row_index = start_row + row_offset
                        column_index = start_column + column_offset
                        self._assert_layout_coordinate(row_index, column_index)
                        captured.update({"row": row_index, "column": column_index})
                        cells.append(captured)
                        max_row = max(max_row, row_index + 1)
                        max_column = max(max_column, column_index + 1)
            for key, destination in (
                ("rowMetadata", row_heights),
                ("columnMetadata", column_widths),
            ):
                metadata = block.get(key)
                if not isinstance(metadata, list):
                    continue
                start = start_row if key == "rowMetadata" else start_column
                for offset, properties in enumerate(metadata):
                    if not isinstance(properties, dict):
                        continue
                    size = properties.get("pixelSize")
                    if isinstance(size, int) and size > 0:
                        destination[str(start + offset)] = size

        merges = self._valid_merges(sheet.get("merges"), _MAX_LAYOUT_ROWS, _MAX_LAYOUT_COLUMNS)
        for merge in merges:
            max_row = max(max_row, merge["endRowIndex"])
            max_column = max(max_column, merge["endColumnIndex"])
        row_count = max(max_row, 1)
        column_count = max(max_column, 1)
        self._bounded_dimension(row_count, _MAX_LAYOUT_ROWS, "行")
        self._bounded_dimension(column_count, _MAX_LAYOUT_COLUMNS, "列")
        properties = sheet.get("properties")
        grid = properties.get("gridProperties") if isinstance(properties, dict) else {}
        binding_count = sum(
            1
            for cell in cells
            if isinstance(cell.get("note"), str) and cell["note"].startswith(_BINDING_NOTE_PREFIX)
        )
        source_template_version_id = next(
            (
                cell["note"].removeprefix(_TEMPLATE_NOTE_PREFIX)
                for cell in cells
                if isinstance(cell.get("note"), str)
                and cell["note"].startswith(_TEMPLATE_NOTE_PREFIX)
            ),
            None,
        )
        return {
            "schemaVersion": 1,
            "sheetName": _OUTPUT_SHEET_NAME,
            "rowCount": row_count,
            "columnCount": column_count,
            "frozenRowCount": self._non_negative_int(
                grid.get("frozenRowCount") if isinstance(grid, dict) else 0
            ),
            "frozenColumnCount": self._non_negative_int(
                grid.get("frozenColumnCount") if isinstance(grid, dict) else 0
            ),
            "hideGridlines": bool(grid.get("hideGridlines", False))
            if isinstance(grid, dict)
            else False,
            "cells": cells,
            "merges": merges,
            "rowHeights": {
                key: value for key, value in row_heights.items() if int(key) < row_count
            },
            "columnWidths": {
                key: value for key, value in column_widths.items() if int(key) < column_count
            },
            "bindingCount": binding_count,
            "sourceTemplateVersionId": source_template_version_id,
        }

    def fetch_pending_edits(self, *, limit: int = 100) -> list[SpreadsheetEdit]:
        if not self.enabled:
            return []
        pending: list[SpreadsheetEdit] = []
        targets = self._spreadsheet_targets_provider() if self._spreadsheet_targets_provider else []
        if not targets and self._spreadsheet_id:
            targets = [
                (self._spreadsheet_id, self._spreadsheet_url or self._url_for(self._spreadsheet_id))
            ]
        for spreadsheet_id, spreadsheet_url in targets:
            with self._lock:
                if spreadsheet_id != self._spreadsheet_id:
                    self._spreadsheet_id = spreadsheet_id
                    self._spreadsheet_url = spreadsheet_url
                    self._sheet_ids = {}
                    self._sheet_grid_sizes = {}
                    self._initialized = False
                self._initialize_locked()
                rows = self._get_values(
                    f"{self._quote_sheet(self._settings.edit_queue_sheet_name)}!A2:N"
                )
            for offset, raw_row in enumerate(rows):
                row = [*raw_row, *([""] * (len(self.EDIT_QUEUE_HEADERS) - len(raw_row)))]
                if str(row[1]).strip().upper() != "PENDING":
                    continue
                try:
                    base_revision = int(str(row[8]))
                except (TypeError, ValueError):
                    base_revision = 0
                pending.append(
                    SpreadsheetEdit(
                        queue_row=offset + 2,
                        edit_id=str(row[0]).strip(),
                        job_id=str(row[3]).strip(),
                        record_id=str(row[4]).strip(),
                        field_id=str(row[5]).strip(),
                        previous_value=self._optional_text(row[6]),
                        new_value=self._optional_text(row[7]),
                        base_revision=base_revision,
                        source_sheet=str(row[9]).strip(),
                        source_cell=str(row[10]).strip(),
                        spreadsheet_id=spreadsheet_id,
                    )
                )
                if len(pending) >= limit:
                    return pending
        return pending

    def mark_edit_processed(
        self,
        *,
        queue_row: int,
        status: str,
        error: str | None,
        applied_revision: int | None,
        spreadsheet_id: str | None = None,
    ) -> None:
        with self._lock:
            if spreadsheet_id and spreadsheet_id != self._spreadsheet_id:
                self._spreadsheet_id = spreadsheet_id
                self._spreadsheet_url = self._url_for(spreadsheet_id)
                self._sheet_ids = {}
                self._sheet_grid_sizes = {}
                self._initialized = False
            timestamp = datetime.now(UTC).astimezone().isoformat(timespec="seconds")
            sheet = self._quote_sheet(self._settings.edit_queue_sheet_name)
            target_id = self._required_spreadsheet_id()
            self._request(
                "POST",
                f"{_API_ROOT}/{target_id}/values:batchUpdate",
                json={
                    "valueInputOption": "RAW",
                    "data": [
                        {"range": f"{sheet}!B{queue_row}", "values": [[status]]},
                        {
                            "range": f"{sheet}!L{queue_row}:N{queue_row}",
                            "values": [[timestamp, error or "", applied_revision or ""]],
                        },
                    ],
                },
            )

    def _upsert_single(self, sheet: str, job_id: str, row: list[object]) -> None:
        rows = self._get_values(f"{self._quote_sheet(sheet)}!A2:A")
        target = next(
            (
                index + 2
                for index, existing in enumerate(rows)
                if existing and existing[0] == job_id
            ),
            len(rows) + 2,
        )
        self._update_rows(sheet, target, [row])

    def _replace_detail_rows(
        self,
        sheet: str,
        job_id: str,
        rows: list[list[object]],
        column_count: int,
    ) -> None:
        existing = self._get_values(f"{self._quote_sheet(sheet)}!A2:B")
        matching = [index + 2 for index, row in enumerate(existing) if row and row[0] == job_id]
        available = matching[: len(rows)]
        if available and available == list(range(available[0], available[0] + len(available))):
            self._update_rows(sheet, available[0], rows[: len(available)])
        else:
            for row_number, value in zip(available, rows, strict=False):
                self._update_rows(sheet, row_number, [value])
        if len(rows) > len(available):
            start = max(len(existing) + 2, (max(matching) + 1 if matching else 2))
            self._update_rows(sheet, start, rows[len(available) :])
        stale = matching[len(rows) :]
        if stale:
            self._request(
                "POST",
                f"{_API_ROOT}/{self._required_spreadsheet_id()}/values:batchClear",
                json={
                    "ranges": [
                        f"{self._quote_sheet(sheet)}!A{row}:{self._column_name(column_count)}{row}"
                        for row in stale
                    ]
                },
            )

    def _update_rows(self, sheet: str, start_row: int, rows: list[list[object]]) -> None:
        if not rows:
            return
        end_row = start_row + len(rows) - 1
        end_column = self._column_name(max(len(row) for row in rows))
        target_range = f"{self._quote_sheet(sheet)}!A{start_row}:{end_column}{end_row}"
        self._request(
            "PUT",
            f"{_API_ROOT}/{self._required_spreadsheet_id()}/values/{quote(target_range, safe='')}",
            params={"valueInputOption": "USER_ENTERED"},
            json={"range": target_range, "majorDimension": "ROWS", "values": rows},
        )

    def _get_values(self, target_range: str) -> list[list[object]]:
        response = self._request(
            "GET",
            f"{_API_ROOT}/{self._required_spreadsheet_id()}/values/{quote(target_range, safe='')}",
            params={"majorDimension": "ROWS"},
        )
        values = response.get("values", [])
        return values if isinstance(values, list) else []

    def _request(self, method: str, url: str, **kwargs: object) -> dict[str, Any]:
        response = self._authorized_session().request(  # type: ignore[no-untyped-call]
            method, url, timeout=30, **kwargs
        )
        response.raise_for_status()
        if not response.content:
            return {}
        payload = response.json()
        return payload if isinstance(payload, dict) else {}

    @staticmethod
    def _layout_format(
        background: str,
        *,
        bold: bool = False,
        foreground: str,
        size: int = 10,
        horizontal: str = "LEFT",
    ) -> dict[str, Any]:
        def color(value: str) -> dict[str, float]:
            return {
                "red": int(value[0:2], 16) / 255,
                "green": int(value[2:4], 16) / 255,
                "blue": int(value[4:6], 16) / 255,
            }

        border = {"style": "SOLID", "color": color("D7E0E7")}
        return {
            "backgroundColor": color(background),
            "textFormat": {
                "foregroundColor": color(foreground),
                "bold": bold,
                "fontSize": size,
                "fontFamily": "Yu Gothic UI",
            },
            "horizontalAlignment": horizontal,
            "verticalAlignment": "MIDDLE",
            "wrapStrategy": "WRAP",
            "borders": {"top": border, "bottom": border, "left": border, "right": border},
        }

    @staticmethod
    def _user_entered_value(value: object) -> dict[str, object]:
        if value is None:
            return {}
        if isinstance(value, bool):
            return {"boolValue": value}
        if isinstance(value, int | float) and not isinstance(value, bool):
            return {"numberValue": value}
        return {"stringValue": str(value)}

    @classmethod
    def _binding_value(cls, document: dict[str, Any], binding: str | None) -> object:
        if not binding:
            return None
        value: object = document
        for part in binding.split("."):
            if isinstance(value, dict):
                value = value.get(part)
            elif isinstance(value, list) and part.isdigit():
                index = int(part)
                value = value[index] if index < len(value) else None
            else:
                return None
        if isinstance(value, dict) and ("raw" in value or "normalized" in value):
            normalized = value.get("normalized")
            raw = value.get("raw")
            return normalized if normalized not in {None, ""} else raw
        return value if isinstance(value, str | int | float | bool) else None

    @staticmethod
    def _generic_field_binding(
        document: dict[str, Any],
        field: dict[str, Any],
    ) -> str | None:
        reading = field.get("reading")
        source_region_id = reading.get("sourceRegionId") if isinstance(reading, dict) else None
        custom_fields = document.get("customFields")
        if not isinstance(source_region_id, str) or not isinstance(custom_fields, dict):
            return None
        for key, candidate in custom_fields.items():
            if (
                isinstance(key, str)
                and isinstance(candidate, dict)
                and candidate.get("sourceRegionId") == source_region_id
            ):
                return f"customFields.{key}"
        return None

    @staticmethod
    def _bounded_dimension(value: object, maximum: int, label: str) -> int:
        if not isinstance(value, int) or isinstance(value, bool) or value < 1 or value > maximum:
            raise AppError(
                status=422,
                code="OUTPUT_LAYOUT_SIZE_INVALID",
                title="Output layout size is invalid",
                detail=f"帳票レイアウトの{label}数は1から{maximum}までにしてください。",
            )
        return value

    @staticmethod
    def _non_negative_int(value: object) -> int:
        return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0

    @classmethod
    def _valid_merges(
        cls,
        raw_merges: object,
        row_count: int,
        column_count: int,
    ) -> list[dict[str, int]]:
        merges: list[dict[str, int]] = []
        if not isinstance(raw_merges, list):
            return merges
        for raw in raw_merges:
            if not isinstance(raw, dict):
                continue
            start_row = cls._non_negative_int(raw.get("startRowIndex"))
            end_row = cls._non_negative_int(raw.get("endRowIndex"))
            start_column = cls._non_negative_int(raw.get("startColumnIndex"))
            end_column = cls._non_negative_int(raw.get("endColumnIndex"))
            if (
                start_row < end_row <= row_count
                and start_column < end_column <= column_count
                and (end_row - start_row > 1 or end_column - start_column > 1)
            ):
                merges.append(
                    {
                        "startRowIndex": start_row,
                        "endRowIndex": end_row,
                        "startColumnIndex": start_column,
                        "endColumnIndex": end_column,
                    }
                )
        return merges

    @staticmethod
    def _dimension_override_requests(
        sheet_id: int,
        dimension: str,
        raw_sizes: object,
        dimension_count: int,
    ) -> list[dict[str, Any]]:
        if not isinstance(raw_sizes, dict):
            return []
        requests: list[dict[str, Any]] = []
        for raw_index, raw_size in raw_sizes.items():
            try:
                index = int(raw_index)
            except (TypeError, ValueError):
                continue
            if not isinstance(raw_size, int) or raw_size < 2 or not 0 <= index < dimension_count:
                continue
            requests.append(
                {
                    "updateDimensionProperties": {
                        "range": {
                            "sheetId": sheet_id,
                            "dimension": dimension,
                            "startIndex": index,
                            "endIndex": index + 1,
                        },
                        "properties": {"pixelSize": raw_size},
                        "fields": "pixelSize",
                    }
                }
            )
        return requests

    @staticmethod
    def _capture_cell(cell: dict[str, Any]) -> dict[str, Any]:
        captured: dict[str, Any] = {}
        for source, target in (
            ("userEnteredValue", "value"),
            ("userEnteredFormat", "format"),
            ("note", "note"),
            ("dataValidation", "dataValidation"),
            ("textFormatRuns", "textFormatRuns"),
        ):
            value = cell.get(source)
            if value not in (None, {}, []):
                captured[target] = value
        return captured

    @staticmethod
    def _assert_layout_coordinate(row: int, column: int) -> None:
        if row >= _MAX_LAYOUT_ROWS or column >= _MAX_LAYOUT_COLUMNS:
            raise AppError(
                status=422,
                code="OUTPUT_LAYOUT_SIZE_INVALID",
                title="Output layout size is invalid",
                detail=(
                    f"帳票レイアウトは{_MAX_LAYOUT_ROWS}行、"
                    f"{_MAX_LAYOUT_COLUMNS}列以内にしてください。"
                ),
            )

    def _link_formula(self, job_id: str, field_path: str) -> str:
        query = urlencode({"job": job_id, "field": field_path, "edit": "1"})
        url = f"{self._settings.frontend_base_url}?{query}"
        return f'=HYPERLINK("{url}","修正")'

    @classmethod
    def _row_has_value(
        cls,
        row: dict[str, object],
        *,
        ignored: set[str] | None = None,
    ) -> bool:
        return any(cls._reading(row, key) for key in row if ignored is None or key not in ignored)

    @staticmethod
    def _reading(container: dict[str, Any], *path: str) -> str:
        value: object = container
        for part in path:
            if not isinstance(value, dict):
                return ""
            value = value.get(part)
        if not isinstance(value, dict):
            return ""
        normalized = value.get("normalized")
        raw = value.get("raw")
        if isinstance(normalized, str):
            return normalized
        return raw if isinstance(raw, str) else ""

    @staticmethod
    def _identifier(value: str) -> str:
        return f"'{value}" if value else ""

    @staticmethod
    def _optional_text(value: object) -> str | None:
        if value is None:
            return None
        text = str(value)
        return text if text else None

    @staticmethod
    def _quote_sheet(title: str) -> str:
        return "'" + title.replace("'", "''") + "'"

    @staticmethod
    def _column_name(count: int) -> str:
        result = ""
        value = count
        while value:
            value, remainder = divmod(value - 1, 26)
            result = chr(65 + remainder) + result
        return result

    def _required_spreadsheet_id(self) -> str:
        self._load_spreadsheet_target()
        if not self._spreadsheet_id:
            raise RuntimeError("GoogleスプレッドシートIDが設定されていません。")
        return self._spreadsheet_id

    @staticmethod
    def _url_for(spreadsheet_id: str) -> str:
        return f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/edit"

    @staticmethod
    def _safe_error(exc: Exception) -> str:
        if isinstance(exc, HTTPError) and exc.response is not None:
            status = exc.response.status_code
            reason: str | None = None
            try:
                payload = exc.response.json()
                details = payload.get("error", {}).get("details", [])
                if isinstance(details, list):
                    for detail in details:
                        if not isinstance(detail, dict):
                            continue
                        metadata = detail.get("metadata")
                        if isinstance(metadata, dict) and isinstance(metadata.get("reason"), str):
                            reason = metadata["reason"]
                            break
                        if isinstance(detail.get("reason"), str):
                            reason = detail["reason"]
                            break
            except (TypeError, ValueError):
                pass
            if reason == "SERVICE_DISABLED":
                return "Google CloudプロジェクトでGoogle Sheets APIが無効です。"
            if status == 401:
                return "Google認証の有効期限が切れました。Googleへ再ログインしてください。"
            if status == 403:
                return (
                    "ログイン中のGoogleアカウントに、対象スプレッドシートの編集権限がありません。"
                )
            if status == 404:
                return "対象のGoogleスプレッドシートが見つかりません。"
            if status == 429:
                return "Google Sheets APIの利用上限に達しました。時間をおいて再試行してください。"
            if status >= 500:
                return "Google Sheets APIが一時的に利用できません。時間をおいて再試行してください。"
        name = type(exc).__name__
        if name in {"ConnectionError", "Timeout"}:
            return "Googleスプレッドシートへ接続できませんでした。"
        return f"Googleスプレッドシートへの同期に失敗しました ({name})。"
