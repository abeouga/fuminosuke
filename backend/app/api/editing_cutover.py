from fastapi import Request

from app.api.dependencies import get_app_settings
from app.core.errors import AppError


def legacy_edit_guard(request: Request) -> None:
    """Close superseded writes only when hand and voice share the new contract."""
    if request.method not in {"GET", "HEAD", "OPTIONS"} and get_app_settings(
        request
    ).app.editing_api_enabled:
        raise AppError(
            status=410,
            code="EDITING_CONTRACT_REPLACED",
            title="Editing contract replaced",
            detail="編集方式が切り替わりました。未保存の入力を控えてから画面を再読み込みしてください。",
        )
