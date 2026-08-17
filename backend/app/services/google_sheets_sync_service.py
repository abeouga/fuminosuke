from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from threading import Lock
from typing import Any
from urllib.parse import quote, urlencode

from google.auth.transport.requests import AuthorizedSession
from google.oauth2.credentials import Credentials
from requests import HTTPError

from app.core.config import GoogleSheetsSettings

logger = logging.getLogger(__name__)

_API_ROOT = "https://sheets.googleapis.com/v4/spreadsheets"
SpreadsheetTargetProvider = Callable[[], tuple[str | None, str | None]]
SpreadsheetTargetSaver = Callable[[str, str], None]


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


class GoogleSheetsSyncService:
    DOCUMENT_HEADERS = (
        "OCRジョブID",
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
    )
    SALE_HEADERS = (
        "OCRジョブID",
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
    )
    CALVING_HEADERS = (
        "OCRジョブID",
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
    )

    def __init__(
        self,
        settings: GoogleSheetsSettings,
        credentials_provider: Callable[[], Credentials] | None = None,
        spreadsheet_target_provider: SpreadsheetTargetProvider | None = None,
        spreadsheet_target_saver: SpreadsheetTargetSaver | None = None,
    ) -> None:
        self._settings = settings
        self._credentials_provider = credentials_provider
        self._spreadsheet_target_provider = spreadsheet_target_provider
        self._spreadsheet_target_saver = spreadsheet_target_saver
        self._session: AuthorizedSession | None = None
        self._lock = Lock()
        self._statuses: dict[str, SpreadsheetSyncStatus] = {}
        self._sheet_ids: dict[str, int] = {}
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

    def status(self, job_id: str) -> SpreadsheetSyncStatus:
        if not self.enabled:
            return SpreadsheetSyncStatus(False, "disabled", None)
        return self._statuses.get(
            job_id,
            SpreadsheetSyncStatus(True, "ready", self.spreadsheet_url),
        )

    def initialize(self) -> SpreadsheetSyncStatus:
        if not self.enabled:
            return SpreadsheetSyncStatus(False, "disabled", None)
        with self._lock:
            self._initialize_locked()
        return SpreadsheetSyncStatus(True, "ready", self.spreadsheet_url)

    def reset_authorization(self) -> None:
        with self._lock:
            self._session = None
            self._sheet_ids = {}
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
    ) -> SpreadsheetSyncStatus:
        if not self.enabled:
            return SpreadsheetSyncStatus(False, "disabled", None)
        try:
            with self._lock:
                self._initialize_locked()
                self._sync_locked(
                    job_id=job_id,
                    revision=revision,
                    document=document,
                    confirmed=confirmed,
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
        return status

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
            (self._settings.document_sheet_name, self.DOCUMENT_HEADERS),
            (self._settings.sale_sheet_name, self.SALE_HEADERS),
            (self._settings.calving_sheet_name, self.CALVING_HEADERS),
        )
        response = self._load_or_create_spreadsheet(definitions)
        existing = {
            item["properties"]["title"]: int(item["properties"]["sheetId"])
            for item in response.get("sheets", [])
            if isinstance(item, dict) and isinstance(item.get("properties"), dict)
        }
        missing = [title for title, _ in definitions if title not in existing]
        if missing:
            created = self._request(
                "POST",
                f"{_API_ROOT}/{self._required_spreadsheet_id()}:batchUpdate",
                json={
                    "requests": [
                        {"addSheet": {"properties": {"title": title}}} for title in missing
                    ]
                },
            )
            for reply in created.get("replies", []):
                properties = reply.get("addSheet", {}).get("properties", {})
                title = properties.get("title")
                sheet_id = properties.get("sheetId")
                if isinstance(title, str) and isinstance(sheet_id, int):
                    existing[title] = sheet_id
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
                params={"fields": "spreadsheetId,spreadsheetUrl,sheets.properties(sheetId,title)"},
            )
        created = self._request(
            "POST",
            _API_ROOT,
            params={"fields": "spreadsheetId,spreadsheetUrl,sheets.properties(sheetId,title)"},
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
        }
        for title, headers in definitions:
            data.append(
                {
                    "range": f"{self._quote_sheet(title)}!A1:{self._column_name(len(headers))}1",
                    "values": [list(headers)],
                }
            )
            sheet_id = self._sheet_ids[title]
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
    ) -> None:
        status = "確定済み" if confirmed else "要確認"
        timestamp = datetime.now(UTC).astimezone().isoformat(timespec="seconds")
        individual_id = self._reading(document, "document", "unifiedEarTag")
        cattle_name = self._reading(document, "document", "cattleName")
        review_count = len(document.get("reviewItems", []))
        document_row = [
            job_id,
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
        ]
        sale_rows = [
            [
                job_id,
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
            ]
            for index, row in enumerate(document.get("saleCalves", []))
            if isinstance(row, dict) and self._row_has_value(row, ignored={"parity"})
        ]
        calving_rows = [
            [
                job_id,
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
            ]
            for index, row in enumerate(document.get("calvings", []))
            if isinstance(row, dict) and self._row_has_value(row, ignored={"parity"})
        ]
        self._upsert_single(self._settings.document_sheet_name, job_id, document_row)
        self._replace_detail_rows(
            self._settings.sale_sheet_name, job_id, sale_rows, len(self.SALE_HEADERS)
        )
        self._replace_detail_rows(
            self._settings.calving_sheet_name,
            job_id,
            calving_rows,
            len(self.CALVING_HEADERS),
        )
        logger.info(
            "Google Sheets sync completed",
            extra={"jobId": job_id, "revision": revision, "confirmed": confirmed},
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

    def _link_formula(self, job_id: str, field_path: str) -> str:
        query = urlencode({"job": job_id, "field": field_path})
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
