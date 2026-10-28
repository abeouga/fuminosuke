from typing import Any

from fastapi import APIRouter, Request

from app.api.dependencies import get_database
from app.core.errors import AppError
from app.services.correction_rate_service import CorrectionRateService

router = APIRouter(tags=["quality"])


@router.get("/correction-rates")
def correction_rates(request: Request) -> dict[str, Any]:
    """The correction rate of this installation's confirmed documents.

    In the cloud each user's runtime only has its own documents; the admin reads everyone's
    through /api/admin/correction-rates instead, so a user cannot read it here.
    """
    if getattr(request.app.state, "cloud_authenticated", False):
        raise AppError(status=404, code="NOT_FOUND", title="Not found", detail="見つかりません。")
    summary = CorrectionRateService(get_database(request)).summary()
    return {"users": [{"user": "この環境", **summary}]}
