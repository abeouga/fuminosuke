from __future__ import annotations

from collections.abc import Mapping


def has_full_image_boundary_fallback(quality: Mapping[str, object]) -> bool:
    warnings = quality.get("warnings", [])
    return quality.get("fallbackUsed") is True or (
        isinstance(warnings, list) and "FULL_IMAGE_BOUNDARY_FALLBACK" in warnings
    )


def has_approved_generic_definition(quality: Mapping[str, object]) -> bool:
    revision = quality.get("templateDefinitionRevision")
    return (
        quality.get("templateDefinitionState") == "APPROVED"
        and quality.get("templateDefinitionValid") is True
        and isinstance(revision, int)
        and not isinstance(revision, bool)
        and revision > 0
        and quality.get("templateDefinitionLayoutInputRevision")
        == quality.get("layoutInputRevision")
    )
