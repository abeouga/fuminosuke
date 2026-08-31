from __future__ import annotations

from dataclasses import dataclass

from app.template_packages.semantic_version import compare_semantic_versions

APPLICATION_VERSION = "0.1.0"


class TemplatePackageFeature:
    CORE = "template-package-core-v1"
    FIELD_MAPPINGS = "field-mappings-v1"
    LAYOUT = "layout-v3"
    COMPOSITE_DOCUMENT_DETECTION = "composite-document-detection-v2"
    DOCUMENT_NAME = "document-name-v1"
    TEMPLATE_DEFINITION = "template-definition-v1"
    CANDIDATE_SETS = "candidate-sets-v1"
    MARK_RECOGNITION = "mark-recognition-v1"
    SPREADSHEET_TEMPLATE = "spreadsheet-template-v1"
    XLSX_LAYOUT = "xlsx-layout-v1"
    GOOGLE_OUTPUT_LAYOUT = "google-output-layout-v1"


SUPPORTED_TEMPLATE_PACKAGE_FEATURES = frozenset(
    {
        TemplatePackageFeature.CORE,
        TemplatePackageFeature.FIELD_MAPPINGS,
        TemplatePackageFeature.LAYOUT,
        TemplatePackageFeature.COMPOSITE_DOCUMENT_DETECTION,
        TemplatePackageFeature.DOCUMENT_NAME,
        TemplatePackageFeature.TEMPLATE_DEFINITION,
        TemplatePackageFeature.CANDIDATE_SETS,
        TemplatePackageFeature.MARK_RECOGNITION,
        TemplatePackageFeature.SPREADSHEET_TEMPLATE,
        TemplatePackageFeature.XLSX_LAYOUT,
        TemplatePackageFeature.GOOGLE_OUTPUT_LAYOUT,
    }
)


@dataclass(frozen=True, slots=True)
class CompatibilityReport:
    compatible: bool
    application_version: str
    minimum_application_version: str
    unsupported_required_features: tuple[str, ...]


def evaluate_compatibility(
    *,
    minimum_application_version: str,
    required_features: list[str] | tuple[str, ...],
    application_version: str = APPLICATION_VERSION,
    supported_features: frozenset[str] = SUPPORTED_TEMPLATE_PACKAGE_FEATURES,
) -> CompatibilityReport:
    unsupported = tuple(
        sorted(feature for feature in required_features if feature not in supported_features)
    )
    version_supported = (
        compare_semantic_versions(application_version, minimum_application_version) >= 0
    )
    return CompatibilityReport(
        compatible=version_supported and not unsupported,
        application_version=application_version,
        minimum_application_version=minimum_application_version,
        unsupported_required_features=unsupported,
    )
