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
        '=HYPERLINK("http://127.0.0.1:5173/?job=job-1&field=calvings.3.outcome&edit=1","修正")'
    )


def test_document_id_is_stable_while_hidden_job_id_tracks_latest_scan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = service(tmp_path)
    document_rows: list[list[object]] = []
    detail_keys: list[str] = []
    monkeypatch.setattr(
        target,
        "_upsert_single",
        lambda _sheet, _key, row: document_rows.append(row),
    )
    monkeypatch.setattr(
        target,
        "_replace_detail_rows",
        lambda _sheet, key, _rows, _width: detail_keys.append(key),
    )
    monkeypatch.setattr(target, "_render_output_layout_locked", lambda *_args: None)
    monkeypatch.setattr(target, "_default_output_layout", lambda *_args, **_kwargs: {})

    target._sync_locked(
        job_id="latest-scan",
        document_id="stable-document",
        revision=3,
        document={"document": {}, "pedigree": {}, "saleCalves": [], "calvings": []},
        confirmed=True,
        template_version_id=None,
    )

    assert document_rows[0][0] == "stable-document"
    assert document_rows[0][-3:] == ["latest-scan", 3, "document"]
    assert detail_keys == ["stable-document", "stable-document"]


def test_initialization_moves_document_sheet_to_third_position(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = service(tmp_path)
    monkeypatch.setattr(
        target,
        "_load_or_create_spreadsheet",
        lambda _definitions: {
            "sheets": [
                {"properties": {"sheetId": 11, "title": "帳票一覧"}},
                {"properties": {"sheetId": 12, "title": "売却子牛一覧"}},
                {"properties": {"sheetId": 13, "title": "繁殖履歴"}},
                {"properties": {"sheetId": 14, "title": "_OCR編集キュー"}},
            ],
        },
    )
    calls: list[dict[str, object]] = []

    def request(_method: str, _url: str, **kwargs: object) -> dict[str, Any]:
        calls.append(kwargs)
        return {}

    monkeypatch.setattr(target, "_request", request)

    target._initialize_locked()

    order_requests = [
        request["updateSheetProperties"]
        for call in calls
        for request in call.get("json", {}).get("requests", [])  # type: ignore[union-attr]
        if request.get("updateSheetProperties", {}).get("fields") == "index"
    ]
    assert [item["properties"] for item in order_requests] == [
        {"sheetId": 12, "index": 0},
        {"sheetId": 13, "index": 1},
        {"sheetId": 11, "index": 2},
    ]


def test_initialization_removes_default_sheet_from_new_document_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = service(tmp_path)
    target._active_document_id = "document-1"
    monkeypatch.setattr(
        target,
        "_load_or_create_spreadsheet",
        lambda _definitions: {
            "sheets": [{"properties": {"sheetId": 99, "title": "Sheet1"}}],
        },
    )
    calls: list[dict[str, object]] = []

    def request(_method: str, _url: str, **kwargs: object) -> dict[str, Any]:
        calls.append(kwargs)
        requests = kwargs.get("json", {}).get("requests", [])  # type: ignore[union-attr]
        return {
            "replies": [
                {
                    "addSheet": {
                        "properties": {
                            "sheetId": index,
                            "title": item["addSheet"]["properties"]["title"],
                        }
                    }
                }
                for index, item in enumerate(requests, 1)
                if "addSheet" in item
            ]
        }

    monkeypatch.setattr(target, "_request", request)
    target._initialize_locked()

    first_requests = calls[0]["json"]["requests"]  # type: ignore[index]
    assert {"deleteSheet": {"sheetId": 99}} in first_requests
    assert "Sheet1" not in target._sheet_ids


def test_reads_pending_sheet_edits_without_exposing_processed_rows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = service(tmp_path)
    monkeypatch.setattr(target, "_initialize_locked", lambda: None)
    monkeypatch.setattr(
        target,
        "_get_values",
        lambda _range: [
            [
                "edit-1",
                "PENDING",
                "2026-08-17T12:00:00+09:00",
                "job-1",
                "document",
                "document.cattleName",
                "1238",
                "1236",
                4,
                "帳票一覧",
                "D2",
            ],
            ["edit-2", "APPLIED"],
        ],
    )

    edits = target.fetch_pending_edits()

    assert len(edits) == 1
    assert edits[0].edit_id == "edit-1"
    assert edits[0].field_id == "document.cattleName"
    assert edits[0].new_value == "1236"
    assert edits[0].base_revision == 4


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
        ("売却子牛一覧", target.SALE_HEADERS),
        ("繁殖履歴", target.CALVING_HEADERS),
        ("帳票一覧", target.DOCUMENT_HEADERS),
    )

    created = target._load_or_create_spreadsheet(definitions)

    assert created["spreadsheetId"] == "user-owned-sheet"
    assert calls[0][0:2] == ("POST", "https://sheets.googleapis.com/v4/spreadsheets")
    assert calls[0][2]["json"] == {
        "properties": {"title": "繁殖台帳 OCR テスト"},
        "sheets": [
            {"properties": {"title": "売却子牛一覧"}},
            {"properties": {"title": "繁殖履歴"}},
            {"properties": {"title": "帳票一覧"}},
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


def test_default_output_layout_keeps_field_bindings_and_template_identity(
    tmp_path: Path,
) -> None:
    target = service(tmp_path)
    document = {
        "document": {
            "cattleName": {
                "raw": "花子",
                "normalized": "花子",
            }
        },
        "pedigree": {},
        "saleCalves": [],
        "calvings": [],
        "customFields": {},
    }

    layout = target._default_output_layout(
        document,
        template_version_id="template-version-1",
    )

    notes = [cell.get("note") for cell in layout["cells"]]
    assert "llm-ocr:template-version:template-version-1" in notes
    assert "llm-ocr:field:document.cattleName" in notes
    cattle_cell = next(
        cell for cell in layout["cells"] if cell.get("note") == "llm-ocr:field:document.cattleName"
    )
    assert cattle_cell["value"] == {"stringValue": "花子"}
    assert layout["bindingCount"] > 0


def test_captures_user_edited_output_layout_with_formats_and_bindings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = service(tmp_path)
    payload = {
        "sheets": [
            {
                "properties": {
                    "sheetId": 31,
                    "title": "帳票レイアウト",
                    "gridProperties": {
                        "frozenRowCount": 1,
                        "frozenColumnCount": 0,
                        "hideGridlines": True,
                    },
                },
                "merges": [
                    {
                        "sheetId": 31,
                        "startRowIndex": 0,
                        "endRowIndex": 1,
                        "startColumnIndex": 0,
                        "endColumnIndex": 2,
                    }
                ],
                "data": [
                    {
                        "startRow": 0,
                        "startColumn": 0,
                        "rowData": [
                            {
                                "values": [
                                    {
                                        "userEnteredValue": {"stringValue": "固定見出し"},
                                        "userEnteredFormat": {"horizontalAlignment": "CENTER"},
                                        "note": ("llm-ocr:template-version:template-version-1"),
                                    },
                                    {
                                        "userEnteredValue": {"stringValue": "花子"},
                                        "note": "llm-ocr:field:document.cattleName",
                                    },
                                ]
                            }
                        ],
                        "rowMetadata": [{"pixelSize": 32}],
                        "columnMetadata": [{"pixelSize": 220}, {"pixelSize": 160}],
                    }
                ],
            }
        ]
    }
    monkeypatch.setattr(target, "_request", lambda *_args, **_kwargs: payload)

    layout = target._capture_output_layout_locked()

    assert layout["bindingCount"] == 1
    assert layout["sourceTemplateVersionId"] == "template-version-1"
    assert layout["rowHeights"] == {"0": 32}
    assert layout["columnWidths"] == {"0": 220, "1": 160}
    assert layout["merges"] == [
        {
            "startRowIndex": 0,
            "endRowIndex": 1,
            "startColumnIndex": 0,
            "endColumnIndex": 2,
        }
    ]
