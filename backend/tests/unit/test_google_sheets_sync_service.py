import json
from pathlib import Path
from typing import Any

import pytest
from requests import HTTPError, Response

from app.core.config import GoogleSheetsSettings
from app.services.google_sheets_sync_service import GoogleSheetsSyncService


def service(tmp_path: Path) -> GoogleSheetsSyncService:
    return GoogleSheetsSyncService(
        GoogleSheetsSettings(
            enabled=True,
            spreadsheet_id="sheet-id",
            credentials_path=tmp_path / "service-account.json",
            frontend_base_url="http://127.0.0.1:5173/",
        )
    )


def test_replace_detail_rows_reuses_job_rows_and_clears_stale_rows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = service(tmp_path)
    updates: list[tuple[str, int, list[list[object]]]] = []
    requests: list[tuple[str, str, dict[str, object]]] = []
    monkeypatch.setattr(
        target,
        "_get_values",
        lambda _range: [["other", 1], ["job-1", 1], ["job-1", 2], ["job-1", 3]],
    )
    monkeypatch.setattr(
        target,
        "_update_rows",
        lambda sheet, row, values: updates.append((sheet, row, values)),
    )

    def request(method: str, url: str, **kwargs: object) -> dict[str, Any]:
        requests.append((method, url, kwargs))
        return {}

    monkeypatch.setattr(target, "_request", request)

    target._replace_detail_rows(
        "売却子牛一覧",
        "job-1",
        [["job-1", 1, "要確認"], ["job-1", 2, "要確認"]],
        14,
    )

    assert updates == [
        (
            "売却子牛一覧",
            3,
            [["job-1", 1, "要確認"], ["job-1", 2, "要確認"]],
        ),
    ]
    assert requests[0][0] == "POST"
    assert requests[0][2]["json"] == {"ranges": ["'売却子牛一覧'!A5:N5"]}


def test_parity_only_placeholder_is_not_exported(tmp_path: Path) -> None:
    target = service(tmp_path)
    parity = {"normalized": "12", "raw": "12"}
    blank = {"normalized": None, "raw": None}

    assert not target._row_has_value(
        {"parity": parity, "calvingDate": blank, "notes": blank},
        ignored={"parity"},
    )
    assert target._row_has_value(
        {"parity": parity, "calvingDate": blank, "notes": {"normalized": "双", "raw": "双"}},
        ignored={"parity"},
    )


def test_correction_link_targets_the_requested_review_cell(tmp_path: Path) -> None:
    target = service(tmp_path)

    assert target._link_formula("job-1", "calvings.3.outcome") == (
        '=HYPERLINK("http://127.0.0.1:5173/?job=job-1&field=calvings.3.outcome","修正")'
    )


def test_column_width_requests_group_adjacent_equal_widths() -> None:
    assert GoogleSheetsSyncService._column_width_requests(12, (230, 80, 80, 120)) == [
        {
            "updateDimensionProperties": {
                "range": {
                    "sheetId": 12,
                    "dimension": "COLUMNS",
                    "startIndex": 0,
                    "endIndex": 1,
                },
                "properties": {"pixelSize": 230},
                "fields": "pixelSize",
            }
        },
        {
            "updateDimensionProperties": {
                "range": {
                    "sheetId": 12,
                    "dimension": "COLUMNS",
                    "startIndex": 1,
                    "endIndex": 3,
                },
                "properties": {"pixelSize": 80},
                "fields": "pixelSize",
            }
        },
        {
            "updateDimensionProperties": {
                "range": {
                    "sheetId": 12,
                    "dimension": "COLUMNS",
                    "startIndex": 3,
                    "endIndex": 4,
                },
                "properties": {"pixelSize": 120},
                "fields": "pixelSize",
            }
        },
    ]


@pytest.mark.parametrize(
    ("status", "payload", "message"),
    [
        (
            403,
            {
                "error": {
                    "details": [
                        {"metadata": {"reason": "SERVICE_DISABLED"}},
                    ]
                }
            },
            "Google CloudプロジェクトでGoogle Sheets APIが無効です。",
        ),
        (
            403,
            {"error": {"status": "PERMISSION_DENIED"}},
            "ログイン中のGoogleアカウントに、対象スプレッドシートの編集権限がありません。",
        ),
    ],
)
def test_http_errors_are_explained_safely(
    status: int,
    payload: dict[str, object],
    message: str,
) -> None:
    response = Response()
    response.status_code = status
    response._content = json.dumps(payload).encode()
    error = HTTPError(response=response)

    assert GoogleSheetsSyncService._safe_error(error) == message


def test_creates_and_saves_a_spreadsheet_for_the_connected_account(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = GoogleSheetsSettings(
        enabled=True,
        spreadsheet_id="legacy-fixed-sheet",
        credentials_path=tmp_path / "unused.json",
        frontend_base_url="http://127.0.0.1:5173/",
        spreadsheet_title="繁殖台帳 OCR テスト",
    )
    saved: list[tuple[str, str]] = []
    target = GoogleSheetsSyncService(
        settings,
        spreadsheet_target_provider=lambda: (None, None),
        spreadsheet_target_saver=lambda spreadsheet_id, url: saved.append((spreadsheet_id, url)),
    )
    calls: list[tuple[str, str, dict[str, object]]] = []

    def request(method: str, url: str, **kwargs: object) -> dict[str, Any]:
        calls.append((method, url, kwargs))
        return {
            "spreadsheetId": "user-owned-sheet",
            "spreadsheetUrl": "https://docs.google.com/spreadsheets/d/user-owned-sheet/edit",
            "sheets": [
                {"properties": {"sheetId": index, "title": title}}
                for index, title in enumerate(("帳票一覧", "売却子牛一覧", "繁殖履歴"), 1)
            ],
        }

    monkeypatch.setattr(target, "_request", request)
    definitions = (
        ("帳票一覧", target.DOCUMENT_HEADERS),
        ("売却子牛一覧", target.SALE_HEADERS),
        ("繁殖履歴", target.CALVING_HEADERS),
    )

    created = target._load_or_create_spreadsheet(definitions)

    assert created["spreadsheetId"] == "user-owned-sheet"
    assert calls[0][0:2] == ("POST", "https://sheets.googleapis.com/v4/spreadsheets")
    assert calls[0][2]["json"] == {
        "properties": {"title": "繁殖台帳 OCR テスト"},
        "sheets": [
            {"properties": {"title": "帳票一覧"}},
            {"properties": {"title": "売却子牛一覧"}},
            {"properties": {"title": "繁殖履歴"}},
        ],
    }
    assert saved == [
        (
            "user-owned-sheet",
            "https://docs.google.com/spreadsheets/d/user-owned-sheet/edit",
        )
    ]
    assert target.spreadsheet_url == (
        "https://docs.google.com/spreadsheets/d/user-owned-sheet/edit"
    )
