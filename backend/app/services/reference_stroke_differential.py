"""Recheck owned stroke deltas against exact reference PNGs, without PDF parsing."""
from __future__ import annotations

import hashlib
import io
import math
import re
from bisect import bisect_right
from typing import Any

import numpy as np
from PIL import Image

from app.services.reference_vector_measurement import raster_point
from app.services.reference_vector_raster import MAX_SAMPLES, MAX_STROKE_PX, dash_intervals
from app.services.renderer_stroke_probe_pixels import (
    DIFFERENTIAL_POLICY,
    candidate_operations,
    unpack_delta,
)


def _sha(value: Any) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def painted_interval(ends: list[float], painted: list[bool], begin: float, end: float) -> bool:
    """Positive paint area in a pixel footprint; no half-pixel transition discard."""
    if end <= begin:
        return False
    if not ends:
        return True
    period = ends[-1]
    if end - begin >= period:
        return any(painted)
    remaining, phase = end - begin, begin % period
    # At most one wrap because the interval is shorter than a whole period.
    for _ in range(len(ends) + 1):
        index = bisect_right(ends, phase)
        span = min(remaining, ends[index] - phase)
        if span > 0 and painted[index]:
            return True
        remaining -= span
        if remaining <= 0:
            return False
        phase = ends[index] if index < len(ends) - 1 else 0.0
    raise ValueError("Invalid differential dash interval")


def paint_components(ends: list[float], painted: list[bool], begin: float,
                     end: float) -> list[tuple[float, float]]:
    """Complete dash paint components, without treating antialias slivers as pixels."""
    if not ends or end <= begin:
        return []
    period = ends[-1]
    first, last = math.floor(begin / period), math.floor(end / period)
    if (last - first + 1) * len(ends) > MAX_SAMPLES:
        raise ValueError("Differential component budget exceeded")
    output = []
    for cycle in range(first, last + 1):
        origin = cycle * period
        for index, high in enumerate(ends):
            low = ends[index - 1] if index else 0.0
            a, b = origin + low, origin + high
            if painted[index] and begin <= a < b <= end:
                output.append((a, b))
    return output


def _projection(segment: dict[str, Any], vector: dict[str, Any]) -> dict[str, Any]:
    start, end = (raster_point(segment[key], vector, 150) for key in ("from", "to"))
    if abs(start[0] - end[0]) < 1e-6 and abs(start[1] - end[1]) > 1e-6:
        axis, center = 1, (start[0] + end[0]) / 2
    elif abs(start[1] - end[1]) < 1e-6 and abs(start[0] - end[0]) > 1e-6:
        axis, center = 0, (start[1] + end[1]) / 2
    else:
        raise ValueError("Differential stroke must be axial")
    a, b, c, d, _, _ = segment["strokeMatrix"]
    det = a * d - b * c
    dx, dy = (segment["to"][i] - segment["from"][i] for i in (0, 1))
    length = math.hypot((d * dx - c * dy) / det, (a * dy - b * dx) / det)
    width = (segment["width"] * abs(det) * length / math.hypot(dx, dy)
             * vector["userUnit"] * 150 / 72)
    if not 0 < width <= MAX_STROKE_PX:
        raise ValueError("Differential stroke width is unsupported")
    return {"axis": axis, "center": center, "start": start[axis], "end": end[axis],
            "sourcePerPixel": length / abs(end[axis] - start[axis]), "width": width}


def validate_manifest(report: dict[str, Any], vectors: dict[str, Any], pdf_sha: str) -> None:
    if (not isinstance(report, dict) or report.get("policy") != DIFFERENTIAL_POLICY
            or report.get("sourcePdfSha256") != pdf_sha or report.get("dpi") != 150
            or report.get("rasterizer") != "poppler-pdftoppm"
            or report.get("visibilityVerified") is not False
            or report.get("productionQualified") is not False
            or not isinstance(report.get("probes"), list)):
        raise ValueError("Invalid differential source evidence")
    actual = []
    for probe in report["probes"]:
        if not isinstance(probe, dict) or type(probe.get("renderedPage")) is not int \
                or type(probe.get("operation")) is not int:
            raise ValueError("Invalid differential operation identity")
        actual.append((probe["renderedPage"], probe["operation"]))
    if actual != candidate_operations(vectors):
        raise ValueError("Differential operation set changed")
    for probe in report["probes"]:
        if probe.get("status") == "UNSUPPORTED" and probe.get("reason") == "COMBINED_FILL":
            continue
        mutation = probe.get("mutation")
        page = vectors["pages"][probe["renderedPage"] - 1]
        segments = [s for s in page["strokes"]["segments"] if s["operation"] == probe["operation"]]
        if (probe.get("status") != "MEASURED" or not isinstance(mutation, dict)
                or mutation.get("policy") != "wsl-single-stroke-suppression-v1"
                or mutation.get("sourcePdfSha256") != pdf_sha
                or mutation.get("sourceContentSha256") != page["contentSha256"]
                or type(mutation.get("renderedPage")) is not int
                or type(mutation.get("operation")) is not int
                or mutation.get("renderedPage") != probe["renderedPage"]
                or mutation.get("operation") != probe["operation"]
                or mutation.get("originalOperator") not in {"S", "s"}
                or mutation.get("replacementOperator") != "n"
                or type(mutation.get("byteOffset")) is not int or mutation["byteOffset"] < 0
                or type(mutation.get("changedDecodedBytes")) is not int
                or mutation["changedDecodedBytes"] != 1
                or type(mutation.get("segmentCount")) is not int
                or mutation["segmentCount"] != len(segments)
                or not _sha(mutation.get("probePdfSha256"))
                or not _sha(mutation.get("probeContentSha256"))
                or mutation.get("visibilityVerified") is not False
                or mutation.get("productionQualified") is not False):
            raise ValueError("Invalid differential single-operation mutation")


def observe_page(page: dict[str, Any], vector: dict[str, Any], png: bytes,
                 probes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Caller first validates vector/glyph and the complete differential manifest."""
    if hashlib.sha256(png).hexdigest() != page["referenceImage"]["sha256"]:
        raise ValueError("Differential reference PNG changed")
    with Image.open(io.BytesIO(png)) as image:
        if image.format != "PNG" or image.size != (page["width"], page["height"]) \
                or image.mode not in {"RGB", "L"}:
            raise ValueError("Invalid differential reference raster")
        rgb = image.convert("RGB").tobytes()
    width, height = page["width"], page["height"]
    output, samples, intersection_checks = [], 0, 0
    for probe in probes:
        if probe["renderedPage"] != vector["renderedPage"] or probe["status"] != "MEASURED":
            continue
        delta = probe["delta"]
        if (delta["width"], delta["height"]) != (width, height):
            raise ValueError("Differential raster dimensions changed")
        changed = unpack_delta(rgb, delta)
        original_pixels = np.frombuffer(rgb, dtype=np.uint8).reshape(height, width, 3)
        different = np.any(
            original_pixels
            != np.frombuffer(changed, dtype=np.uint8).reshape(height, width, 3), axis=2)
        selected = [(i, s) for i, s in enumerate(vector["strokes"]["segments"])
                    if s["operation"] == probe["operation"]]
        mask = np.zeros((height, width), dtype=bool)
        projections = []
        for index, segment in selected:
            projection = _projection(segment, vector)
            projections.append((index, segment, projection))
            axis, center = projection["axis"], projection["center"]
            lo, hi = sorted([projection["start"], projection["end"]])
            radius = projection["width"] * max(1, segment["miterLimit"]) / 2 + 1
            low, high = math.floor(lo - radius), math.ceil(hi + radius)
            nlo, nhi = math.floor(center - radius), math.ceil(center + radius)
            if axis == 0:
                mask[max(0, nlo):min(height, nhi + 1), max(0, low):min(width, high + 1)] = True
            else:
                mask[max(0, low):min(height, high + 1), max(0, nlo):min(width, nhi + 1)] = True
        if np.any(different & ~mask):
            raise ValueError("Raster changed outside the suppressed stroke operation")
        for index, segment, projection in projections:
            axis, center = projection["axis"], projection["center"]
            lo, hi = sorted([projection["start"], projection["end"]])
            guard = max(2.0, projection["width"] / 2 + 1)
            first, last = math.ceil(lo + guard), math.floor(hi - guard) - 1
            radius = projection["width"] / 2 + .75
            normal = (math.ceil(center - radius - .5), math.floor(center + radius - .5))
            expected, observed = 0, 0
            ends, painted = dash_intervals(segment)
            phase = segment["dashPhase"] + segment["subpathDistance"]
            if min(normal) < 0 or max(normal) >= (height if axis == 0 else width) \
                    or lo < 0 or hi > (width if axis == 0 else height):
                raise ValueError("Differential stroke intersects the raster boundary")
            samples += max(0, last - first + 1)
            if samples > MAX_SAMPLES:
                raise ValueError("Differential sample budget exceeded")
            for position in range(first, last + 1):
                span = sorted([abs(position - projection["start"]),
                               abs(position + 1 - projection["start"])])
                a, b = (phase + value * projection["sourcePerPixel"] for value in span)
                if not painted_interval(ends, painted, a, b):
                    continue
                expected += 1
                strip = (different[normal[0]:normal[1] + 1, position] if axis == 0 else
                         different[position, normal[0]:normal[1] + 1])
                observed += bool(np.any(strip))
            status = "OBSERVED" if expected and observed == expected else "UNVERIFIED"
            component_observation = None
            if ends:
                distances = sorted([abs(lo + guard - projection["start"]),
                                    abs(hi - guard - projection["start"])])
                components = paint_components(
                    ends, painted, phase + distances[0] * projection["sourcePerPixel"],
                    phase + distances[1] * projection["sourcePerPixel"])
                samples += len(components)
                if samples > MAX_SAMPLES:
                    raise ValueError("Differential total sample budget exceeded")
                maximum = max((b - a for a, b in components), default=0)
                maximum /= projection["sourcePerPixel"]
                # Supplemental evidence only for paint components smaller than
                # one pixel, where demanding every intersecting pixel mistakes
                # quantization of a fractional area for absent ink. Long dashes
                # keep the existing interior/whole-footprint observation.
                if 0 < maximum < 1:
                    direction = 1 if projection["end"] > projection["start"] else -1
                    contributed = 0
                    intersections = []
                    for a, b in components:
                        positions = sorted(projection["start"] + direction * (d - phase)
                                           / projection["sourcePerPixel"] for d in (a, b))
                        start, stop = math.floor(positions[0]), math.ceil(positions[1])
                        strip = (different[normal[0]:normal[1] + 1, start:stop] if axis == 0 else
                                 different[start:stop, normal[0]:normal[1] + 1])
                        if np.any(strip):
                            contributed += 1
                            continue
                        source_strip = (
                            original_pixels[normal[0]:normal[1] + 1, start:stop]
                            if axis == 0 else original_pixels[start:stop, normal[0]:normal[1] + 1])
                        candidates = []
                        # A solid crossing can already paint the same pixels.
                        # Record exact vector identities; the caller must also
                        # corroborate each with independent solid-line samples.
                        if source_strip.size and np.any(np.all(source_strip < 128, axis=2)):
                            intersection_checks += len(vector["strokes"]["segments"])
                            if intersection_checks > MAX_SAMPLES:
                                raise ValueError("Differential intersection budget exceeded")
                            for other_index, other in enumerate(vector["strokes"]["segments"]):
                                if (other["operation"] == probe["operation"] or other["dash"]
                                        or other["width"] <= 0 or other["clippingUnverified"]
                                        or other["sharesPathWithCurve"]):
                                    continue
                                try:
                                    crossing = _projection(other, vector)
                                except ValueError:
                                    continue
                                cross_radius = crossing["width"] / 2 + .75
                                if (crossing["axis"] != axis
                                        and min(crossing["start"], crossing["end"]) <= center
                                        <= max(crossing["start"], crossing["end"])
                                        and crossing["center"] - cross_radius <= positions[0]
                                        and positions[1] <= crossing["center"] + cross_radius):
                                    candidates.append(other_index)
                        intersections.append({"rasterInterval": positions,
                                              "candidateSegmentIndices": candidates})
                    component_observation = {
                        "policy": "subpixel-paint-component-contribution-v1",
                        "paintComponents": len(components), "changedComponents": contributed,
                        "maxPaintComponentPx": maximum,
                        "status": "OBSERVED" if contributed == len(components) else "UNVERIFIED",
                        "intersectionCandidates": intersections,
                        "visibilityVerified": False, "productionQualified": False,
                    }
            output.append({"segmentIndex": index, "operation": probe["operation"],
                           "status": status,
                           "paintFootprints": expected, "changedFootprints": observed,
                           "subpixelComponents": component_observation,
                           "visibilityVerified": False, "productionQualified": False})
    return output


def raster_ink_observed(segment: dict[str, Any], observations: list[dict[str, Any]],
                        solid_samples: list[dict[str, Any]] | None = None) -> bool:
    """Supplement only unsampleable paint interiors, never contradictory ink.

    These observations must be the fresh output of observe_page, not values from
    an API payload or a saved summary. This is still not a visibility certificate.
    """
    if segment["status"] == "SAMPLED":
        return segment.get("inkCoverage") == 1
    if (segment["status"] != "UNVERIFIABLE"
            or segment.get("reason") != "NO_INTERIOR_PAINT_SAMPLES"):
        return False
    matches = [value for value in observations if value["segmentIndex"] == segment["segmentIndex"]]
    if len(matches) != 1:
        return False
    value = matches[0]
    if (value["status"] == "OBSERVED" and value["paintFootprints"] > 0
            and value["changedFootprints"] == value["paintFootprints"]):
        return True
    components = value.get("subpixelComponents")
    if not components or not 0 < components["maxPaintComponentPx"] < 1 \
            or components["changedComponents"] <= 0:
        return False
    if components["changedComponents"] == components["paintComponents"]:
        return bool(components["status"] == "OBSERVED")
    intersections = components["intersectionCandidates"]
    if len(intersections) + components["changedComponents"] != components["paintComponents"]:
        return False
    corroborated = {s["segmentIndex"] for s in (solid_samples or [])
                    if s["status"] == "SAMPLED" and s.get("inkCoverage") == 1
                    and s.get("contrastCoverage") == 1}
    return all(corroborated.intersection(item["candidateSegmentIndices"]) for item in intersections)
