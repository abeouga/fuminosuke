"""Observe causal subpixel ink only over each explicitly bound field edge.

Keep the original whole-segment diagnostic. An unrelated portion of a long
stroke must not become this field's ink requirement. No crop or geometry moves.
"""
from __future__ import annotations

from typing import Any

from app.services.reference_stroke_differential import (
    _projection,
    paint_components,
    raster_ink_observed,
)
from app.services.reference_vector_raster import MAX_SAMPLES, dash_intervals

POLICY = "wsl-bound-field-edge-ink-v1"


def _scoped(sample: dict[str, Any], differences: list[dict[str, Any]], line: dict[str, Any],
            vector: dict[str, Any], rectangle: dict[str, Any], side: int) -> dict[str, Any]:
    result: dict[str, Any] = {"status": "UNVERIFIED", "requiredInterval": None}
    if sample["status"] != "UNVERIFIABLE" \
            or sample.get("reason") != "NO_INTERIOR_PAINT_SAMPLES":
        return result  # Never rescue actually sampled missing ink or an unsupported path.
    matches = [d for d in differences if d["segmentIndex"] == sample["segmentIndex"]]
    if len(matches) != 1:
        return result
    component = matches[0].get("subpixelComponents")
    if (not component or not 0 < component["maxPaintComponentPx"] < 1
            or not 0 < component["changedComponents"] < component["paintComponents"]
            or component["status"] != "UNVERIFIED"):
        return result
    missing = component["intersectionCandidates"]
    if len(missing) + component["changedComponents"] != component["paintComponents"]:
        return result
    projection = _projection(line, vector)
    if projection["axis"] != (1 if side < 2 else 0):
        return result
    start = rectangle["y" if side < 2 else "x"]
    end = start + rectangle["height" if side < 2 else "width"]
    low, high = sorted([projection["start"], projection["end"]])
    guard = max(2.0, projection["width"] / 2 + 1)  # Same existing endpoint exclusion.
    required_low, required_high = max(start, low) + guard, min(end, high) - guard
    result["requiredInterval"] = [required_low, required_high]
    if required_high <= required_low:
        return result
    ends, painted = dash_intervals(line)
    if not ends:
        return result
    phase = line["dashPhase"] + line["subpathDistance"]
    distances = sorted(abs(v - projection["start"]) for v in (required_low, required_high))
    components = paint_components(ends, painted,
        phase + distances[0] * projection["sourcePerPixel"],
        phase + distances[1] * projection["sourcePerPixel"])
    intersecting = [m["rasterInterval"] for m in missing
                    if m["rasterInterval"][1] > required_low
                    and m["rasterInterval"][0] < required_high]
    result.update(paintComponents=len(components), unobservedIntervals=intersecting,
                  endpointGuardPx=guard)
    if components and not intersecting:
        result["status"] = "OBSERVED"
    return result


def observe_field_ink(page: dict[str, Any], vector: dict[str, Any],
                      observation: dict[str, Any],
                      adjusted: dict[str, Any] | None = None) -> dict[str, Any]:
    """Accept only freshly computed vector/raster/delta observations, never saved flags."""
    samples = observation["rasterObservation"]["segments"]
    differences = observation["strokeDifferential"]
    by_index = {s["segmentIndex"]: s for s in samples}
    required = {s["segmentIndex"] for s in samples
                if not raster_ink_observed(s, differences, samples)}
    # These are fresh model observations over the same explicit segments, not
    # saved approval flags. Keep missing ink rejected when the predicted pixel
    # is also empty; never change the raw diagnostic or the dash fallback.
    adjusted_indices = {s["segmentIndex"] for s in (adjusted or {}).get("segments", [])
        if s["segmentIndex"] in required and s["status"] == "SAMPLED"
        and s["paintSamples"] > 0 and s["inkSamples"] == s["paintSamples"]
        and by_index[s["segmentIndex"]]["status"] == "SAMPLED"}
    required -= adjusted_indices
    fields = {f["cellId"]: f for f in page["fields"] if f.get("bindings")}
    observations = []
    checks = 0
    for field in observation["fields"]:
        original = fields[field["cellId"]]
        if original["bindings"] != field["bindings"]:
            raise ValueError("Field ink observation binding changed")
        for side, edge in enumerate(field["edges"]):
            for index in edge.get("segmentIndices", []):
                if index in required:
                    scoped = _scoped(by_index[index], differences,
                        vector["strokes"]["segments"][index], vector, original["rectangle"], side)
                    checks += 1 + scoped.get("paintComponents", 0)
                    if checks > MAX_SAMPLES:
                        raise ValueError("Field ink observation sample budget exceeded")
                    observations.append({"cellId": field["cellId"], "edgeIndex": side,
                                         "segmentIndex": index, **scoped})
    covered = {o["segmentIndex"] for o in observations}
    return {"policy": POLICY, "observations": observations,
            "adjustedSegmentIndices": sorted(adjusted_indices),
            "allRequiredInkObserved": bool(samples) and covered == required
                and all(o["status"] == "OBSERVED" for o in observations),
            "visibilityVerified": False, "productionQualified": False}
