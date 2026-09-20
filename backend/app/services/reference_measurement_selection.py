"""Select only geometrically missing or subpixel-unsampleable measurement edges.

This selects changes in a disposable instrumentation workbook, not changes in a
template or its print artifact. An actually missing raster stroke is not rescued.
"""
from __future__ import annotations

from typing import Any

from app.services.reference_border_probe import SIDES
from app.services.reference_stroke_differential import raster_ink_observed


def _unsampled_crossings(sample: dict[str, Any], differences: list[dict[str, Any]]) -> bool:
    matches = [d for d in differences if d["segmentIndex"] == sample["segmentIndex"]]
    if len(matches) != 1:
        return False
    value = matches[0]
    components = value.get("subpixelComponents")
    if value["status"] != "UNVERIFIED" or not components:
        return False
    changed, count = components["changedComponents"], components["paintComponents"]
    crossings = components["intersectionCandidates"]
    return bool(components["status"] == "UNVERIFIED"
                and 0 < components["maxPaintComponentPx"] < 1
                and 0 < changed < count and len(crossings) == count - changed
                and all(c["candidateSegmentIndices"] for c in crossings))


def _requires_measurement(edge: dict[str, Any], samples: dict[int, dict[str, Any]],
                          differences: list[dict[str, Any]]) -> bool:
    if edge["status"] == "UNOBSERVED":
        return True
    indices = edge.get("segmentIndices", [])
    if edge["status"] != "OBSERVED" or not indices:
        return False
    unsampleable = False
    for index in indices:
        item = samples.get(index)
        if item is None:
            return False
        if item["status"] == "UNVERIFIABLE" \
                and item.get("reason") == "NO_INTERIOR_PAINT_SAMPLES":
            if raster_ink_observed(item, differences, list(samples.values())):
                continue
            if not _unsampled_crossings(item, differences):
                return False
            unsampleable = True
        elif item["status"] != "SAMPLED" or item.get("inkCoverage") != 1:
            # No measurement copy may hide ink that was actually tested
            # and absent, clipping, unsupported caps, or missing evidence.
            return False
    return unsampleable


def select_measurement_edges(observations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    selected = []
    for page in observations:
        samples = {item["segmentIndex"]: item
                   for item in page.get("rasterObservation", {}).get("segments", [])}
        differences = page.get("strokeDifferential", [])

        for field in page["fields"]:
            sides = [side for side, edge in zip(SIDES, field["edges"], strict=True)
                     if _requires_measurement(edge, samples, differences)]
            if sides:
                selected.append({"cellId": field["cellId"], "sourceRange": field["sourceRange"],
                                 "sides": sides})
    return selected
