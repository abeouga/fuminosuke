from __future__ import annotations

import re

from app.core.errors import AppError

_XLSX_EXTENSION = re.compile(r"\.xlsx$", re.IGNORECASE)
_INVALID_OUTPUT_NAME = re.compile(r'[\\/:*?"<>|\[\]\x00-\x1f]')
_MAX_EXCEL_SHEET_NAME_LENGTH = 31


def normalize_spreadsheet_output_name(value: str) -> str:
    """Return the single logical name used by both XLSX files and result sheets."""
    normalized = _XLSX_EXTENSION.sub("", value.strip()).strip()
    if not normalized:
        raise AppError(
            status=422,
            code="OUTPUT_FILE_NAME_REQUIRED",
            title="Output file name is required",
            detail="出力ファイル名を入力してください。",
        )
    if len(normalized) > _MAX_EXCEL_SHEET_NAME_LENGTH:
        raise AppError(
            status=422,
            code="OUTPUT_FILE_NAME_TOO_LONG",
            title="Output file name is too long",
            detail=(
                "XLSXファイル名とExcelシート名を一致させるため、"
                "書類名は31文字以内で入力してください。"
            ),
        )
    if _INVALID_OUTPUT_NAME.search(normalized):
        raise AppError(
            status=422,
            code="OUTPUT_FILE_NAME_INVALID",
            title="Output file name is invalid",
            detail=(
                "XLSXファイル名とExcelシート名を一致させるため、"
                '\\ / : * ? " < > | [ ] は使用できません。'
            ),
        )
    return normalized
