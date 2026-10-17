"""Image-first analysis inputs. Pure preparation; no provider, DB, or save access.

A work unit is a conservative spatial/dependency component, not an inferred semantic table.
Text hints never determine membership, eligibility, or whether a cell is blank.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
from dataclasses import dataclass
from io import BytesIO
from typing import Any

from PIL import Image

from app.api.v2.editing_contracts import Binding
from app.editing.domain.command import digest
from app.template_analysis.snapshot import physical

POLICY = "template-analysis.visual-input.v1"
MAX_UNIT_CELLS = 320
# Whole-page drafts run as parallel bands cut at blank horizontal gaps (2026-10-17).
DRAFT_MIN_GAP = 8
DRAFT_MIN_BAND_CELLS = 6
DRAFT_MAX_BANDS = 4
MAX_PAGE_CELLS = 1000


def _encode(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


@dataclass(frozen=True)
class VisualPage:
    """Immutable copy of an admitted server snapshot and its owned image."""

    snapshot_json: str
    page_key: str
    image: bytes
    pin: str

    def unpack(self, expected_pin: str) -> tuple[dict[str, Any], dict[str, Any]]:
        actual = digest([POLICY, self.snapshot_json, self.page_key, _sha(self.image)])
        if actual != expected_pin or self.pin != expected_pin:
            raise ValueError("VISUAL_SNAPSHOT_STALE")
        snapshot = json.loads(self.snapshot_json)
        matches = [p for p in snapshot["pages"] if p["pageKey"] == self.page_key]
        if len(matches) != 1 or matches[0]["imageSha256"] != _sha(self.image):
            raise ValueError("VISUAL_PAGE_OWNERSHIP")
        return snapshot, matches[0]


@dataclass(frozen=True)
class VisualUnit:
    unit_id: str
    write_refs: tuple[str, ...]
    read_refs: tuple[str, ...]
    state: str
    reason: str | None
    bounds: tuple[int, ...] | None = None
    kind: str = "form"


@dataclass(frozen=True)
class VisualPlan:
    snapshot_pin: str
    units: tuple[VisualUnit, ...]
    digest: str


@dataclass(frozen=True)
class VisualEvidence:
    snapshot_pin: str
    plan_digest: str
    unit_id: str
    metadata_json: str
    crop_png: bytes
    full_image: bytes
    digest: str


def capture_visual_page(snapshot: dict[str, Any], page_key: str, image: bytes) -> VisualPage:
    """Accept only the already-admitted snapshot contract, never model coordinates."""
    if not isinstance(image, bytes) or not image or len(image) > 20 * 1024 * 1024:
        raise ValueError("VISUAL_IMAGE_LIMIT")
    snapshot = copy.deepcopy(snapshot)
    pages = [p for p in snapshot["pages"] if p["pageKey"] == page_key]
    if len(pages) != 1 or pages[0]["imageSha256"] != _sha(image):
        raise ValueError("VISUAL_PAGE_OWNERSHIP")
    if not snapshot.get("resourceId") or type(snapshot.get("baseRevision")) is not int:
        raise ValueError("VISUAL_BINDING_REQUIRED")
    binding = Binding.model_validate(snapshot["binding"])
    if binding.expected_revision != snapshot["baseRevision"]:
        raise ValueError("VISUAL_BINDING_STALE")
    if snapshot["physicalDigest"] != physical(snapshot["draft"]["layout"]):
        raise ValueError("VISUAL_PHYSICAL_STALE")
    if snapshot["draftDigest"] != digest(snapshot["draft"]):
        raise ValueError("VISUAL_DRAFT_STALE")
    page = pages[0]
    refs = page["refs"]
    ids = [c["id"] for c in refs.values()]
    targets = page["targets"]
    cells = page["providerCells"]
    if (
        (not refs and not page.get("structure"))
        or len(refs) > MAX_PAGE_CELLS
        or len(set(ids)) != len(ids)
        or (not targets and not page.get("structure"))
        or len(targets) != len(set(targets))
        or not set(targets) <= set(refs)
        or {c["ref"] for c in cells} != set(refs)
        or len(cells) != len(refs)
        or set(page["handles"]) != set(refs)
        or not set(page["lockedRoles"]) <= set(targets)
    ):
        raise ValueError("VISUAL_REFERENCE_COVERAGE")
    layout_cells = {c["id"]: c for c in snapshot["draft"]["layout"]["cells"]}
    if any(layout_cells.get(c["id"]) != c for c in refs.values()):
        raise ValueError("VISUAL_CELL_OWNERSHIP")
    with Image.open(BytesIO(image)) as source:
        if source.format not in {"PNG", "JPEG", "WEBP"}:
            raise ValueError("VISUAL_IMAGE_TYPE")
        width, height = source.size
        if max(width, height) > 20000:
            raise ValueError("VISUAL_IMAGE_LIMIT")
        source.verify()
    for c in cells:
        numbers = [c[k] for k in ("x", "y", "width", "height")]
        if any(type(v) not in (int, float) or not math.isfinite(v) for v in numbers):
            raise ValueError("VISUAL_GEOMETRY_INVALID")
        x, y, w, h = numbers
        if (
            w <= 0
            or h <= 0
            or (not c.get("virtual") and (x < 0 or y < 0 or x + w > width or y + h > height))
        ):
            raise ValueError("VISUAL_GEOMETRY_INVALID")
        if bool(c["target"]) != (c["ref"] in targets):
            raise ValueError("VISUAL_TARGET_MISMATCH")
        if c.get("virtual") and c["ref"] in targets:
            raise ValueError("VISUAL_VIRTUAL_WRITE")
        # In legacy single-image authoring the issued image coordinates are
        # canonical. Paged transforms are checked by the admitting snapshot.
        if page["pageId"] is None and any(
            c[k] != refs[c["ref"]][k] for k in ("x", "y", "width", "height", "vertices")
        ):
            raise ValueError("VISUAL_GEOMETRY_BINDING")
    serialized = _encode(snapshot)
    pin = digest([POLICY, serialized, page_key, _sha(image)])
    return VisualPage(serialized, page_key, image, pin)


def _touching(a: dict[str, Any], b: dict[str, Any]) -> bool:
    # Two pixel adjacency is a grouping hint, never a coordinate correction.
    overlap_x = min(a["x"] + a["width"], b["x"] + b["width"]) - max(a["x"], b["x"])
    overlap_y = min(a["y"] + a["height"], b["y"] + b["height"]) - max(a["y"], b["y"])
    return bool((overlap_x > 0 and overlap_y >= -2) or (overlap_y > 0 and overlap_x >= -2))


def _gap_cut(cells: list[dict[str, Any]], axis: str) -> list[list[dict[str, Any]]]:
    """Split cells where no cell covers a blank run of at least DRAFT_MIN_GAP on axis."""
    start, size = ("y", "height") if axis == "y" else ("x", "width")
    parts: list[list[dict[str, Any]]] = []
    end = None
    for cell in sorted(cells, key=lambda c: (c[start], c["ref"])):
        if end is None or cell[start] - end >= DRAFT_MIN_GAP:
            parts.append([])
        parts[-1].append(cell)
        end = cell[start] + cell[size] if end is None else max(end, cell[start] + cell[size])
    return parts


def _xy_leaves(cells: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    # Recursive XY cut: blank horizontal runs first, then blank vertical runs, so
    # side-by-side sections of a page become separate leaves in reading order.
    for axis in ("y", "x"):
        parts = _gap_cut(cells, axis)
        if len(parts) > 1:
            return [leaf for part in parts for leaf in _xy_leaves(part)]
    return [cells]


def draft_bands(cells: list[dict[str, Any]]) -> list[list[str]]:
    """Cut writable cells into regions at blank gaps no cell covers.

    A gap is real whitespace between form sections, so a table, a field and its
    adjacent label never straddle a cut. Cuts run across the page and then down
    it (XY cut), and small regions join a neighbour in reading order.
    """
    bands = _xy_leaves(cells)
    while len(bands) > 1 and (
        len(bands) > DRAFT_MAX_BANDS or min(len(b) for b in bands) < DRAFT_MIN_BAND_CELLS
    ):
        i = min(range(len(bands)), key=lambda k: len(bands[k]))
        # Join the smallest band to its smaller neighbour.
        j = (
            i - 1
            if i == len(bands) - 1 or (i > 0 and len(bands[i - 1]) <= len(bands[i + 1]))
            else i + 1
        )
        low, high = sorted((i, j))
        bands[low : high + 1] = [bands[low] + bands[high]]
    return [sorted(c["ref"] for c in band) for band in bands]


def plan_visual_units(source: VisualPage, expected_pin: str) -> VisualPlan:
    snapshot, page = source.unpack(expected_pin)
    refs = page["refs"]
    targets = set(page["targets"])
    if page.get("wholePage"):
        # Whole-page draft: one form unit per band, sent in parallel, no bounds, so
        # the compiled components merge independently instead of as one region.
        draft_units = tuple(
            VisualUnit(
                "d_" + digest([POLICY, expected_pin, band])[:24],
                tuple(band),
                tuple(sorted(set(refs) - set(band))),
                "ready",
                None,
                None,
                "form",
            )
            for band in draft_bands([c for c in page["providerCells"] if c["ref"] in targets])
        )
        return VisualPlan(
            expected_pin,
            draft_units,
            digest([POLICY, expected_pin, [u.__dict__ for u in draft_units]]),
        )
    if page.get("structure"):
        if page.get("structureSemantics"):
            unit = VisualUnit(
                "s_" + digest([POLICY, expected_pin, sorted(targets)])[:24],
                tuple(sorted(targets)),
                tuple(sorted(set(refs) - targets)),
                "ready",
                None,
                tuple(page["geometryBounds"]),
                "form",
            )
            return VisualPlan(
                expected_pin, (unit,), digest([POLICY, expected_pin, [unit.__dict__]])
            )
        from app.template_analysis.region_plan import POLICY as REGION_POLICY
        from app.template_analysis.region_plan import plan_regions

        region_units = plan_regions(source)
        return VisualPlan(
            expected_pin,
            region_units,
            digest([REGION_POLICY, expected_pin, [u.__dict__ for u in region_units]]),
        )
    parent = {r: r for r in targets}

    def root(r: str) -> str:
        while parent[r] != r:
            parent[r] = parent[parent[r]]
            r = parent[r]
        return r

    def join(group: list[str]) -> None:
        selected = sorted(set(group) & targets)
        for r in selected[1:]:
            parent[root(r)] = root(selected[0])

    cells = sorted(
        (c for c in page["providerCells"] if c["ref"] in targets),
        key=lambda c: (c["y"], c["x"], c["ref"]),
    )
    for i, a in enumerate(cells):
        for b in cells[i + 1 :]:
            if b["y"] > a["y"] + a["height"] + 2:
                break
            if _touching(a, b):
                join([a["ref"], b["ref"]])
    # Existing explicit ownership outranks whitespace. Do not split a saved
    # relation, record section, or merged-cell identity between work units.
    reverse = {c["id"]: r for r, c in refs.items()}
    layout = snapshot["draft"]["layout"]
    for relation in layout.get("relations", []):
        ids = [relation["inputCellId"], *relation.get("labelCellIds", [])]
        join([reverse[i] for i in ids if i in reverse])
    from app.editing.semantic_commands import section_cells

    for section in layout.get("recordSections", []):
        join([reverse[i] for i in section_cells(layout, section["id"]) if i in reverse])
    groups: dict[str, list[str]] = {}
    for c in cells:
        groups.setdefault(root(c["ref"]), []).append(c["ref"])
    units = []
    for group in groups.values():
        writable = tuple(sorted(group))
        held = len(writable) > MAX_UNIT_CELLS
        units.append(
            VisualUnit(
                unit_id="v_" + digest([POLICY, expected_pin, writable])[:24],
                write_refs=writable,
                # All page context stays readable, including textless labels and
                # outside legends. It does not become writable via a model claim.
                read_refs=tuple(sorted(set(refs) - set(writable))),
                state="held" if held else "ready",
                reason="DEPENDENCY_COMPONENT_LIMIT" if held else None,
            )
        )
    plan_digest = digest([POLICY, expected_pin, [u.__dict__ for u in units]])
    return VisualPlan(expected_pin, tuple(units), plan_digest)


def check_plan(source: VisualPage, pin: str, plan: VisualPlan) -> None:
    if plan != plan_visual_units(source, pin):
        raise ValueError("VISUAL_PLAN_STALE")


def build_visual_evidence(
    source: VisualPage, expected_pin: str, plan: VisualPlan, unit_id: str
) -> VisualEvidence:
    check_plan(source, expected_pin, plan)
    snapshot, page = source.unpack(expected_pin)
    matches = [u for u in plan.units if u.unit_id == unit_id]
    if len(matches) != 1 or matches[0].state != "ready":
        raise ValueError("VISUAL_UNIT_UNAVAILABLE")
    unit = matches[0]
    writes = set(unit.write_refs)
    cells = page["providerCells"]
    reverse = {c["id"]: r for r, c in page["refs"].items()}
    existing_bindings = []
    for relation in snapshot["draft"]["layout"].get("relations", []):
        if relation["inputCellId"] not in reverse:
            continue
        if not set(relation["labelCellIds"]) <= reverse.keys():
            raise ValueError("VISUAL_EXISTING_BINDING_REFERENCE")
        existing_bindings.append(
            {
                "input": reverse[relation["inputCellId"]],
                "labels": [reverse[c] for c in relation["labelCellIds"]],
                "axis": relation["axis"],
            }
        )
    cell_details = [
        {
            **{k: c[k] for k in ("ref", "x", "y", "width", "height", "vertices")},
            "writable": c["ref"] in writes,
            "existingRole": c["role"],
            "roleLocked": c.get("roleLocked", False) or c["ref"] not in writes,
            "existingParentRef": page["parentRefs"].get(c["ref"]),
            "hasMeaning": c["ref"] in page["bound"],
            "protectedFacets": [
                json.loads(k)[1]
                for k in page["protectedFacets"]
                if json.loads(k)[0] == page["refs"][c["ref"]]["id"]
            ],
            "textHint": c.get("text") or None,
            "textHintStatus": "unverified" if c.get("text") else "unavailable",
        }
        for c in cells
    ]
    target = [c for c in cells if c["ref"] in writes]
    context_cells = []
    if unit.kind == "region":
        from app.template_analysis.visual_bindings import build_binding_catalog

        options = build_binding_catalog({"writeRefs": list(writes), "cells": cell_details})
        labels = {b.label for b in options.links}
        context_cells = [c for c in cells if c["ref"] in labels]
    with Image.open(BytesIO(source.image)) as image:
        if unit.bounds:
            x, y, w, h = unit.bounds
            left, top = max(0, x - 64), max(0, y - 64)
            right, bottom = min(image.width, x + w + 64), min(image.height, y + h + 64)
            if context_cells:
                left = min(left, max(0, math.floor(min(c["x"] for c in context_cells)) - 16))
                top = min(top, max(0, math.floor(min(c["y"] for c in context_cells)) - 16))
                right = max(
                    right,
                    min(
                        image.width, math.ceil(max(c["x"] + c["width"] for c in context_cells)) + 16
                    ),
                )
                bottom = max(
                    bottom,
                    min(
                        image.height,
                        math.ceil(max(c["y"] + c["height"] for c in context_cells)) + 16,
                    ),
                )
        else:
            left = max(0, math.floor(min(c["x"] for c in target)) - 64)
            top = max(0, math.floor(min(c["y"] for c in target)) - 64)
            right = min(image.width, math.ceil(max(c["x"] + c["width"] for c in target)) + 64)
            bottom = min(image.height, math.ceil(max(c["y"] + c["height"] for c in target)) + 64)
        with image.crop((left, top, right, bottom)) as cropped:
            buffer = BytesIO()
            cropped.save(buffer, format="PNG")
            crop = buffer.getvalue()
        size = [image.width, image.height]
    from app.template_analysis.region_plan import POLICY as REGION_POLICY

    metadata = _encode(
        {
            "policy": POLICY,
            "snapshotPin": expected_pin,
            "planDigest": plan.digest,
            "unitId": unit_id,
            "pageKey": source.page_key,
            "sourceImageSha256": _sha(source.image),
            "sourceImageSize": size,
            "cropRect": [left, top, right - left, bottom - top],
            "cropSha256": _sha(crop),
            "writeRefs": list(unit.write_refs),
            "readOnlyRefs": list(unit.read_refs),
            "existingBindings": existing_bindings,
            "structure": bool(page.get("structure")),
            "structureSemantics": bool(page.get("structureSemantics")),
            "draft": bool(page.get("wholePage")),
            "geometryEditable": page.get("geometryEditable", []),
            "geometryBounds": list(unit.bounds)
            if unit.bounds
            else page.get("geometryBounds", [0, 0, *size]),
            "regionKind": unit.kind,
            "regionPolicy": REGION_POLICY if unit.bounds else None,
            "cells": cell_details,
        }
    )
    evidence_digest = digest([POLICY, metadata, _sha(crop), _sha(source.image)])
    return VisualEvidence(
        expected_pin, plan.digest, unit_id, metadata, crop, source.image, evidence_digest
    )


def check_evidence(
    source: VisualPage, pin: str, plan: VisualPlan, evidence: VisualEvidence
) -> None:
    if evidence != build_visual_evidence(source, pin, plan, evidence.unit_id):
        raise ValueError("VISUAL_EVIDENCE_STALE")
