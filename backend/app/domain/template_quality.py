from __future__ import annotations

from collections.abc import Mapping


def has_full_image_boundary_fallback(quality: Mapping[str, object]) -> bool:
    warnings = quality.get("warnings", [])
    return quality.get("fallbackUsed") is True or (
        isinstance(warnings, list) and "FULL_IMAGE_BOUNDARY_FALLBACK" in warnings
    )
