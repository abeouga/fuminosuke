"""Versioned contracts for self-contained ``.llmocr-template`` packages."""

from app.template_packages.canonical_models import (
    CanonicalOutputLayout,
    CanonicalTemplate,
    CanonicalTemplateQuality,
)
from app.template_packages.export_models import (
    TemplateExportInspection,
    TemplateExportRequest,
    TemplatePackageExport,
    TemplatePackageExportError,
)
from app.template_packages.export_service import TemplateExportService
from app.template_packages.feature_registry import (
    APPLICATION_VERSION,
    SUPPORTED_TEMPLATE_PACKAGE_FEATURES,
    CompatibilityReport,
    evaluate_compatibility,
)
from app.template_packages.import_models import (
    TemplateImportInspection,
    TemplatePackageImportError,
    TemplatePackageImportResult,
)
from app.template_packages.import_service import (
    TemplateImportInspectionService,
    TemplateImportService,
)
from app.template_packages.manifest_models import TemplatePackageManifest
from app.template_packages.package_reader import TemplatePackageReader
from app.template_packages.package_serializer import GeneratedTemplatePackageValidator
from app.template_packages.security_limits import (
    DEFAULT_TEMPLATE_PACKAGE_SECURITY_LIMITS,
    TemplatePackageSecurityLimits,
)

__all__ = [
    "APPLICATION_VERSION",
    "DEFAULT_TEMPLATE_PACKAGE_SECURITY_LIMITS",
    "SUPPORTED_TEMPLATE_PACKAGE_FEATURES",
    "CanonicalOutputLayout",
    "CanonicalTemplate",
    "CanonicalTemplateQuality",
    "CompatibilityReport",
    "GeneratedTemplatePackageValidator",
    "TemplateExportInspection",
    "TemplateExportRequest",
    "TemplateExportService",
    "TemplateImportInspection",
    "TemplateImportInspectionService",
    "TemplateImportService",
    "TemplatePackageExport",
    "TemplatePackageExportError",
    "TemplatePackageImportError",
    "TemplatePackageImportResult",
    "TemplatePackageManifest",
    "TemplatePackageReader",
    "TemplatePackageSecurityLimits",
    "evaluate_compatibility",
]
