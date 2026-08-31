from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TemplatePackageSecurityLimits:
    max_package_bytes: int = 128 * 1024 * 1024
    max_entry_count: int = 32
    max_total_uncompressed_bytes: int = 256 * 1024 * 1024
    max_compression_ratio: float = 100.0
    max_manifest_bytes: int = 1024 * 1024
    max_json_bytes: int = 16 * 1024 * 1024
    max_image_bytes: int = 50 * 1024 * 1024
    max_image_width: int = 20_000
    max_image_height: int = 20_000
    max_image_pixels: int = 100_000_000
    max_xlsx_bytes: int = 64 * 1024 * 1024
    max_xlsx_entry_count: int = 5_000
    max_xlsx_entry_bytes: int = 32 * 1024 * 1024
    max_xlsx_total_uncompressed_bytes: int = 256 * 1024 * 1024
    max_xlsx_compression_ratio: float = 100.0


DEFAULT_TEMPLATE_PACKAGE_SECURITY_LIMITS = TemplatePackageSecurityLimits()
