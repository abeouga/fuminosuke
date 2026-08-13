from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import asdict, dataclass
from decimal import Decimal
from typing import Any, ClassVar

from app.domain.individual_id import individual_id_from_reading, normalize_individual_id
from app.domain.numeric_values import (
    decimal_value,
    normalize_decimal,
    normalize_integer,
    normalize_yen,
)
from app.domain.ocr_models import FieldReading, OcrDocument, ReadingStatus, ReviewItem


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    code: str
    path: str
    message: str
    severity: str
    source_region_id: str | None = None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ValidationReport:
    valid: bool
    issues: list[ValidationIssue]

    def to_dict(self) -> dict[str, object]:
        return {"valid": self.valid, "issues": [item.to_dict() for item in self.issues]}


class DeterministicValidator:
    DATE_PATTERN = re.compile(r"^\d{1,4}[./年・-]\d{1,2}(?:[./月・-]\d{1,2}日?)?$")
    DETERMINISTIC_REVIEW_CODES: ClassVar[set[str]] = {
        "INDIVIDUAL_ID_REQUIRED",
        "DATE_SYNTAX",
        "NUMERIC_SYNTAX",
        "TEXT_VALUE_INVALID",
        "DUPLICATE_PARITY",
        "PARITY_SEQUENCE",
        "VALUE_RANGE",
        "LOW_REGISTRATION_CONFIDENCE",
        "MASK_COVERAGE_HIGH",
    }

    def validate(
        self,
        document: OcrDocument,
        *,
        image_quality: dict[str, Any] | None = None,
        configured_paths: set[str] | None = None,
    ) -> ValidationReport:
        issues: list[ValidationIssue] = []
        if self._configured("document.unifiedEarTag", configured_paths):
            self._validate_ear_tag(document.document.unified_ear_tag, issues)
        if self._configured("document.birthDate", configured_paths):
            self._validate_date(document.document.birth_date, "document.birthDate", issues)
        seen_parities: set[int] = set()
        last_parity = 0
        for index, calving in enumerate(document.calvings):
            path = f"calvings.{index}"
            if self._configured(f"{path}.calvingDate", configured_paths):
                self._validate_date(calving.calving_date, f"{path}.calvingDate", issues)
            parity = None
            if self._configured(f"{path}.parity", configured_paths):
                self._number(
                    calving.parity,
                    f"{path}.parity",
                    Decimal(1),
                    Decimal(99),
                    normalize_integer,
                    issues,
                )
                parity = self._integer(calving.parity)
            if parity is not None:
                if parity in seen_parities:
                    issues.append(
                        ValidationIssue(
                            code="DUPLICATE_PARITY",
                            path=f"{path}.parity",
                            message="産次が重複しています。",
                            severity="error",
                            source_region_id=calving.parity.source_region_id,
                        )
                    )
                if parity < last_parity:
                    issues.append(
                        ValidationIssue(
                            code="PARITY_SEQUENCE",
                            path=f"{path}.parity",
                            message="産次の順序を確認してください。",
                            severity="warning",
                            source_region_id=calving.parity.source_region_id,
                        )
                    )
                seen_parities.add(parity)
                last_parity = parity
        seen_sale_parities: set[int] = set()
        last_sale_parity = 0
        for index, calf in enumerate(document.sale_calves):
            path = f"saleCalves.{index}"
            parity = None
            if self._configured(f"{path}.parity", configured_paths):
                self._number(
                    calf.parity,
                    f"{path}.parity",
                    Decimal(1),
                    Decimal(99),
                    normalize_integer,
                    issues,
                )
                parity = self._integer(calf.parity)
            if parity is not None:
                if parity in seen_sale_parities:
                    issues.append(
                        ValidationIssue(
                            code="DUPLICATE_PARITY",
                            path=f"{path}.parity",
                            message="産次が重複しています。",
                            severity="error",
                            source_region_id=calf.parity.source_region_id,
                        )
                    )
                if parity < last_sale_parity:
                    issues.append(
                        ValidationIssue(
                            code="PARITY_SEQUENCE",
                            path=f"{path}.parity",
                            message="産次の順序を確認してください。",
                            severity="warning",
                            source_region_id=calf.parity.source_region_id,
                        )
                    )
                seen_sale_parities.add(parity)
                last_sale_parity = parity
            if self._configured(f"{path}.ageDays", configured_paths):
                self._number(
                    calf.age_days,
                    f"{path}.ageDays",
                    Decimal(0),
                    Decimal(5000),
                    normalize_integer,
                    issues,
                )
            if self._configured(f"{path}.weightKg", configured_paths):
                self._number(
                    calf.weight_kg,
                    f"{path}.weightKg",
                    Decimal(0),
                    Decimal(2000),
                    normalize_decimal,
                    issues,
                )
            if self._configured(f"{path}.priceYen", configured_paths):
                self._number(
                    calf.price_yen,
                    f"{path}.priceYen",
                    Decimal(0),
                    Decimal(100_000_000),
                    normalize_yen,
                    issues,
                )
            if self._configured(f"{path}.saleDate", configured_paths):
                self._validate_date(calf.sale_date, f"{path}.saleDate", issues)
            if self._configured(f"{path}.sireName", configured_paths):
                self._validate_name(
                    calf.sire_name,
                    f"{path}.sireName",
                    "種雄牛名を文字で入力してください。",
                    issues,
                )
        if image_quality:
            if float(image_quality.get("edgeOverlap", 1.0)) < 0.7:
                issues.append(
                    ValidationIssue(
                        code="LOW_REGISTRATION_CONFIDENCE",
                        path="processingMetadata",
                        message="位置合わせ品質が低いため確認が必要です。",
                        severity="error",
                    )
                )
            coverage = float(image_quality.get("handwritingCoverageRatio", 0.0))
            if coverage > 0.25:
                issues.append(
                    ValidationIssue(
                        code="MASK_COVERAGE_HIGH",
                        path="processingMetadata",
                        message="手書きマスク面積が大きすぎます。",
                        severity="warning",
                    )
                )
        return ValidationReport(
            valid=not any(item.severity == "error" for item in issues),
            issues=issues,
        )

    @staticmethod
    def _configured(path: str, configured_paths: set[str] | None) -> bool:
        return configured_paths is None or path in configured_paths

    def merge_review_items(self, document: OcrDocument, report: ValidationReport) -> OcrDocument:
        manual_items = [
            item
            for item in document.review_items
            if item.code not in self.DETERMINISTIC_REVIEW_CODES
        ]
        validation_items = [
            ReviewItem(
                code=item.code,
                path=item.path,
                message=item.message,
                severity=item.severity,
                source_region_id=item.source_region_id,
            )
            for item in report.issues
        ]
        return document.model_copy(
            update={"review_items": [*manual_items, *validation_items]}, deep=True
        )

    def _validate_ear_tag(self, reading: FieldReading, issues: list[ValidationIssue]) -> None:
        try:
            if reading.raw:
                normalize_individual_id(reading.raw)
            individual_id_from_reading(reading)
        except ValueError:
            issues.append(
                ValidationIssue(
                    code="INDIVIDUAL_ID_REQUIRED",
                    path="document.unifiedEarTag",
                    message="個体識別番号を10桁の数字で確定してください。",
                    severity="error",
                    source_region_id=reading.source_region_id,
                )
            )

    def _validate_date(
        self, reading: FieldReading, path: str, issues: list[ValidationIssue]
    ) -> None:
        candidate = reading.normalized or reading.raw
        if (
            reading.status is ReadingStatus.READABLE
            and candidate
            and not self.DATE_PATTERN.fullmatch(candidate.strip())
        ):
            issues.append(
                ValidationIssue(
                    code="DATE_SYNTAX",
                    path=path,
                    message="日付の原文形式を確認してください。",
                    severity="warning",
                    source_region_id=reading.source_region_id,
                )
            )

    def _number(
        self,
        reading: FieldReading,
        path: str,
        minimum: Decimal,
        maximum: Decimal,
        normalizer: Callable[[str | None], str | None],
        issues: list[ValidationIssue],
    ) -> None:
        if reading.status is not ReadingStatus.READABLE or not reading.raw:
            return
        normalized = normalizer(reading.normalized or reading.raw)
        value = decimal_value(normalized)
        if value is None:
            issues.append(
                ValidationIssue(
                    code="NUMERIC_SYNTAX",
                    path=path,
                    message="数値の区切りや小数点を確認してください。",
                    severity="error",
                    source_region_id=reading.source_region_id,
                )
            )
        elif not minimum <= value <= maximum:
            issues.append(
                ValidationIssue(
                    code="VALUE_RANGE",
                    path=path,
                    message="数値が業務上の想定範囲外です。",
                    severity="warning",
                    source_region_id=reading.source_region_id,
                )
            )

    @staticmethod
    def _validate_name(
        reading: FieldReading,
        path: str,
        message: str,
        issues: list[ValidationIssue],
    ) -> None:
        if reading.status is not ReadingStatus.READABLE or not reading.raw:
            return
        value = (reading.normalized or reading.raw).strip()
        if re.search(r"[0-9A-Za-zぁ-んァ-ヶ一-龯々〆ヵヶ]", value):
            return
        issues.append(
            ValidationIssue(
                code="TEXT_VALUE_INVALID",
                path=path,
                message=message,
                severity="error",
                source_region_id=reading.source_region_id,
            )
        )

    @staticmethod
    def _integer(reading: FieldReading) -> int | None:
        if reading.status is not ReadingStatus.READABLE or not reading.raw:
            return None
        compact = normalize_integer(reading.normalized or reading.raw)
        return int(compact) if compact is not None else None
