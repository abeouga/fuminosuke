"""New Linux evidence identity using the existing physical-length projection algorithm."""

from __future__ import annotations

import copy
from typing import Any

from app.services import wsl_workbook_geometry as physical_lengths

MODEL = "linux-fods-physical-length-v1"


def dimensions(fods: bytes, sheet_name: str, rows: int, columns: int) -> dict[str, Any]:
    measured = physical_lengths.dimensions(fods, sheet_name, rows, columns)
    return {**measured, "model": MODEL}


def apply_dimensions(source: dict[str, Any], evidence: dict[str, Any]) -> dict[str, Any]:
    if evidence.get("model") != MODEL:
        raise ValueError("Linux geometry evidence required")
    # The numerical projection is shared. Persist only the new measured identity.
    result = physical_lengths.apply_dimensions(
        source, {**evidence, "model": physical_lengths.MODEL}
    )
    result.update(geometryModelVersion=MODEL, geometryEvidence=copy.deepcopy(evidence))
    return result
