from typing import Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict

from app.api.dependencies import get_database

router = APIRouter(tags=["system"])


class HealthResponse(BaseModel):
    model_config = ConfigDict(
        alias_generator=lambda value: value.split("_")[0]
        + "".join(part.capitalize() for part in value.split("_")[1:]),
        populate_by_name=True,
    )

    status: Literal["ok"]
    database: Literal["ok"]
    job_worker: Literal["running", "not_started"]
    ocr_provider: Literal["openai", "mock", "disabled"]


@router.get("/health", response_model=HealthResponse, response_model_by_alias=True)
def health(request: Request) -> HealthResponse:
    get_database(request).check_connection()
    return HealthResponse(
        status="ok",
        database="ok",
        job_worker=request.app.state.job_worker_status,
        ocr_provider=request.app.state.settings.ocr.provider,
    )
