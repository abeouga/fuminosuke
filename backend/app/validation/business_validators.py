from __future__ import annotations
from dataclasses import asdict, dataclass
from typing import Any, ClassVar
from app.domain.ocr_models import OcrDocument, ReviewItem


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
    DETERMINISTIC_REVIEW_CODES: ClassVar[set[str]] = {
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
        if image_quality:
            if float(image_quality.get("edgeOverlap", 1.0)) < 0.7:
                issues.append(
                    ValidationIssue(
                        "LOW_REGISTRATION_CONFIDENCE",
                        "processingMetadata",
                        "位置合わせ品質が低いため確認が必要です。",
                        "error",
                    )
                )
            if float(image_quality.get("handwritingCoverageRatio", 0.0)) > 0.25:
                issues.append(
                    ValidationIssue(
                        "MASK_COVERAGE_HIGH",
                        "processingMetadata",
                        "手書きマスク面積が大きすぎます。",
                        "warning",
                    )
                )
        return ValidationReport(not any(i.severity == "error" for i in issues), issues)

    def merge_review_items(self, document: OcrDocument, report: ValidationReport) -> OcrDocument:
        manual = [i for i in document.review_items if i.code not in self.DETERMINISTIC_REVIEW_CODES]
        generated = [
            ReviewItem(
                code=i.code,
                path=i.path,
                message=i.message,
                severity=i.severity,
                source_region_id=i.source_region_id,
            )
            for i in report.issues
        ]
        return document.model_copy(update={"review_items": [*manual, *generated]}, deep=True)
