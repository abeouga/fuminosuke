"""Explicit font substitutions for new Linux preparations; historical plans stay unchanged."""

from __future__ import annotations

from typing import Any

POLICY = "linux-declared-font-substitution-v1"
# Metric-compatible replacements only. Unknown and Japanese system fonts fail closed.
SUBSTITUTIONS = {
    "calibri": "carlito",
    "cambria": "caladea",
    "arial": "liberation sans",
    "times new roman": "liberation serif",
    "courier new": "liberation mono",
}


def validate_fonts(fonts: list[dict[str, Any]]) -> list[dict[str, str]]:
    decisions = []
    if not fonts:
        raise ValueError("Missing declared fonts")
    for item in fonts:
        requested = item["requestedFamily"].casefold()
        resolved = {part.strip().casefold() for part in item["resolvedFamily"].split(",")}
        if requested in resolved:
            mode = "exact"
        elif SUBSTITUTIONS.get(requested) in resolved:
            mode = "declaredSubstitution"
        else:
            raise ValueError("Unapproved font substitution")
        decisions.append(
            {
                "policy": POLICY,
                "requestedFamily": item["requestedFamily"],
                "resolvedFamily": item["resolvedFamily"],
                "mode": mode,
            }
        )
    return decisions
