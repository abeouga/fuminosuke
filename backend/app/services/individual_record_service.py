from __future__ import annotations

from typing import Any

from app.core.errors import AppError, FieldError
from app.db.session import Database
from app.domain.individual_id import normalize_individual_id
from app.repositories.ocr_result_repository import OcrResultRepository


class IndividualRecordService:
    def __init__(self, database: Database) -> None:
        self._database = database

    def search(self, query: str) -> dict[str, Any]:
        try:
            individual_id = normalize_individual_id(query)
        except ValueError as exc:
            raise AppError(
                status=422,
                code="INVALID_INDIVIDUAL_ID",
                title="Invalid individual identification number",
                detail="個体識別番号はハイフンを除く10桁の数字で入力してください。",
                field_errors=[
                    FieldError(
                        field="individualId",
                        message="10桁の数字で入力してください。",
                    )
                ],
            ) from exc

        with self._database.session() as session:
            stored = OcrResultRepository(session).list_confirmed_by_individual_id(
                individual_id
            )
            records = [
                {
                    "recordId": item.id,
                    "individualId": individual_id,
                    "sourceJobId": item.job_id,
                    "ocrResultRevision": item.ocr_result_revision,
                    "confirmedAt": item.confirmed_at,
                    "data": item.confirmed_json,
                }
                for item in stored
            ]
        return {
            "query": query,
            "individualId": individual_id,
            "count": len(records),
            "records": records,
        }
