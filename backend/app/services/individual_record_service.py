from __future__ import annotations

import re
import unicodedata
from typing import Any

from app.core.errors import AppError, FieldError
from app.db.session import Database
from app.domain.individual_id import normalize_individual_id
from app.domain.numeric_values import normalize_integer
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
            records = []
            for item in stored:
                warnings = self._confirmed_quality_warnings(item.confirmed_json)
                records.append({
                    "recordId": item.id,
                    "individualId": individual_id,
                    "sourceJobId": item.job_id,
                    "ocrResultRevision": item.ocr_result_revision,
                    "confirmedAt": item.confirmed_at,
                    "data": item.confirmed_json,
                    "needsReview": bool(warnings),
                    "reviewWarnings": warnings,
                })
        return {
            "query": query,
            "individualId": individual_id,
            "count": len(records),
            "records": records,
        }

    def list_individuals(self) -> dict[str, Any]:
        with self._database.session() as session:
            stored = OcrResultRepository(session).list_all_confirmed()

            summaries: dict[str, dict[str, Any]] = {}
            for confirmed in stored:
                individual_id = confirmed.individual_id
                if not individual_id:
                    continue
                existing = summaries.get(individual_id)
                warnings = self._confirmed_quality_warnings(confirmed.confirmed_json)
                if existing is None:
                    summaries[individual_id] = {
                        "individualId": individual_id,
                        "cattleName": self._cattle_name(confirmed.confirmed_json),
                        "recordCount": 1,
                        "lastConfirmedAt": confirmed.confirmed_at,
                        "needsReview": bool(warnings),
                    }
                else:
                    existing["recordCount"] += 1
                    existing["needsReview"] = existing["needsReview"] or bool(warnings)

        items = list(summaries.values())
        return {"count": len(items), "items": items}

    @staticmethod
    def _cattle_name(document: dict[str, Any]) -> str | None:
        identity = document.get("document")
        if not isinstance(identity, dict):
            return None
        reading = identity.get("cattleName")
        if not isinstance(reading, dict):
            return None
        value = reading.get("normalized") or reading.get("raw")
        return value.strip() if isinstance(value, str) and value.strip() else None

    def search_sale_calves_by_sire(self, query: str) -> dict[str, Any]:
        normalized = unicodedata.normalize("NFKC", query).strip().casefold()
        if not normalized:
            raise AppError(
                status=422,
                code="INVALID_SIRE_NAME",
                title="Sire name is required",
                detail="種雄牛名を入力してください。",
                field_errors=[
                    FieldError(
                        field="sireName",
                        message="種雄牛名を入力してください。",
                    )
                ],
            )
        with self._database.session() as session:
            matches = OcrResultRepository(session).list_record_field_matches(
                section_key="saleCalves",
                field_key="sireName",
                normalized_value=normalized,
            )
            rows = []
            indexed_confirmed_ids: set[str] = set()
            for field, confirmed in matches:
                indexed_confirmed_ids.add(confirmed.id)
                sale_calves = confirmed.confirmed_json.get("saleCalves")
                record_data: dict[str, object] = {}
                if (
                    isinstance(sale_calves, list)
                    and 0 <= field.record_order - 1 < len(sale_calves)
                    and isinstance(sale_calves[field.record_order - 1], dict)
                ):
                    record_data = sale_calves[field.record_order - 1]
                rows.append(
                    {
                        "recordFieldId": field.id,
                        "confirmedResultId": confirmed.id,
                        "individualId": confirmed.individual_id,
                        "sourceJobId": confirmed.job_id,
                        "confirmedAt": confirmed.confirmed_at,
                        "sectionKey": field.section_key,
                        "sectionTitle": field.section_title,
                        "recordOrder": field.record_order,
                        "recordKey": field.record_key,
                        "matchedValue": field.raw_value,
                        "data": record_data,
                        "reviewWarnings": self._sale_calf_quality_warnings(
                            record_data,
                            field.record_order,
                        ),
                    }
                )
            for confirmed in OcrResultRepository(session).list_all_confirmed():
                if confirmed.id in indexed_confirmed_ids:
                    continue
                sale_calves = confirmed.confirmed_json.get("saleCalves")
                if not isinstance(sale_calves, list):
                    continue
                for index, record_data in enumerate(sale_calves):
                    if not isinstance(record_data, dict):
                        continue
                    sire = record_data.get("sireName")
                    if not isinstance(sire, dict):
                        continue
                    raw_sire = sire.get("normalized") or sire.get("raw")
                    if (
                        not isinstance(raw_sire, str)
                        or normalized
                        not in unicodedata.normalize(
                            "NFKC", raw_sire
                        ).strip().casefold()
                    ):
                        continue
                    parity = record_data.get("parity")
                    record_key = None
                    if isinstance(parity, dict):
                        raw_key = parity.get("normalized") or parity.get("raw")
                        if isinstance(raw_key, str):
                            record_key = raw_key
                    rows.append(
                        {
                            "recordFieldId": f"legacy:{confirmed.id}:{index + 1}",
                            "confirmedResultId": confirmed.id,
                            "individualId": confirmed.individual_id,
                            "sourceJobId": confirmed.job_id,
                            "confirmedAt": confirmed.confirmed_at,
                            "sectionKey": "saleCalves",
                            "sectionTitle": "売却子牛一覧",
                            "recordOrder": index + 1,
                            "recordKey": record_key,
                            "matchedValue": raw_sire,
                            "data": record_data,
                            "reviewWarnings": self._sale_calf_quality_warnings(
                                record_data,
                                index + 1,
                            ),
                        }
                    )
        return {
            "query": query,
            "normalizedQuery": normalized,
            "count": len(rows),
            "matches": rows,
        }

    @classmethod
    def _confirmed_quality_warnings(cls, document: dict[str, Any]) -> list[str]:
        sale_calves = document.get("saleCalves")
        if not isinstance(sale_calves, list):
            return []
        warnings: list[str] = []
        for index, record in enumerate(sale_calves, start=1):
            if isinstance(record, dict):
                warnings.extend(cls._sale_calf_quality_warnings(record, index))
        return warnings

    @staticmethod
    def _sale_calf_quality_warnings(
        record: dict[str, object],
        record_order: int,
    ) -> list[str]:
        warnings: list[str] = []
        parity = IndividualRecordService._reading_value(record.get("parity"))
        if parity and normalize_integer(parity) is None:
            warnings.append(
                f"売却子牛{record_order}行目の産次が数値ではありません。"
            )
        sire_name = IndividualRecordService._reading_value(record.get("sireName"))
        if sire_name and not re.search(
            r"[0-9A-Za-zぁ-んァ-ヶ一-龯々〆ヵヶ]",
            sire_name,
        ):
            warnings.append(
                f"売却子牛{record_order}行目の種雄牛名を再確認してください。"
            )
        return warnings

    @staticmethod
    def _reading_value(value: object) -> str | None:
        if not isinstance(value, dict):
            return None
        candidate = value.get("normalized") or value.get("raw")
        return candidate.strip() if isinstance(candidate, str) and candidate.strip() else None
