from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any, ClassVar

from app.domain.individual_id import individual_id_from_reading, normalize_individual_id
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
    ) -> ValidationReport:
        issues: list[ValidationIssue] = []
        self._validate_ear_tag(document.document.unified_ear_tag, issues)
        self._validate_date(document.document.birth_date, "document.birthDate", issues)
        seen_parities: set[int] = set()
        last_parity = 0
        for index, calving in enumerate(document.calvings):
            path = f"calvings.{index}"
            self._validate_date(calving.calving_date, f"{path}.calvingDate", issues)
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
        for index, calf in enumerate(document.sale_calves):
            self._range(calf.age_days, f"saleCalves.{index}.ageDays", 0, 5000, issues)
            self._range(calf.weight_kg, f"saleCalves.{index}.weightKg", 0, 2000, issues)
            self._range(calf.price_yen, f"saleCalves.{index}.priceYen", 0, 100_000_000, issues)
            self._validate_date(calf.sale_date, f"saleCalves.{index}.saleDate", issues)
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
        if (
            reading.status is ReadingStatus.READABLE
            and reading.raw
            and not self.DATE_PATTERN.fullmatch(reading.raw.strip())
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

    def _range(
        self,
        reading: FieldReading,
        path: str,
        minimum: int,
        maximum: int,
        issues: list[ValidationIssue],
    ) -> None:
        value = self._integer(reading)
        if value is not None and not minimum <= value <= maximum:
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
    def _integer(reading: FieldReading) -> int | None:
        if reading.status is not ReadingStatus.READABLE or not reading.raw:
            return None
        compact = reading.raw.replace(",", "").strip()
        return int(compact) if compact.isdigit() else None
