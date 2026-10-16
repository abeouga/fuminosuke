"""Issue bounded geometric options; proximity never assigns semantic ownership."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any, Literal

from app.editing.domain.command import digest

POLICY = "template-analysis.visual-bindings.v1"
MAX_LINKS = 4096
MAX_METADATA_BYTES = 65536
CONTACT_PIXELS = 2
MIN_OVERLAP_RATIO = .5
Axis = Literal["horizontal", "vertical"]


def policy_parameters() -> dict[str, int | float]:
    return {
        "maxLinks": MAX_LINKS, "maxMetadataBytes": MAX_METADATA_BYTES,
        "contactPixels": CONTACT_PIXELS, "minOverlapRatio": MIN_OVERLAP_RATIO,
    }


@dataclass(frozen=True)
class BindingCandidate:
    ref: str
    input: str
    label: str
    axis: Axis
    reasons: tuple[str, ...]
    gap: float
    intermediate: tuple[str, ...]


@dataclass(frozen=True)
class BindingCatalog:
    digest: str
    links: tuple[BindingCandidate, ...]

    def metadata(self) -> dict[str, Any]:
        return {
            "bindingPolicy": POLICY, "catalogDigest": self.digest,
            "linkColumns": ["ref", "input", "label", "axis"],
            "links": [[
                b.ref, b.input, b.label, "h" if b.axis == "horizontal" else "v",
            ] for b in self.links],
        }


def _directions(a: dict[str, Any], b: dict[str, Any]) -> list[tuple[str, Axis, float]]:
    xo = min(a["x"] + a["width"], b["x"] + b["width"]) - max(a["x"], b["x"])
    yo = min(a["y"] + a["height"], b["y"] + b["height"]) - max(a["y"], b["y"])
    result: list[tuple[str, Axis, float]] = []
    if yo >= MIN_OVERLAP_RATIO * min(a["height"], b["height"]):
        result.extend([
            ("left", "horizontal", a["x"] - b["x"] - b["width"]),
            ("right", "horizontal", b["x"] - a["x"] - a["width"]),
        ])
    if xo >= MIN_OVERLAP_RATIO * min(a["width"], b["width"]):
        result.extend([
            ("above", "vertical", a["y"] - b["y"] - b["height"]),
            ("below", "vertical", b["y"] - a["y"] - a["height"]),
        ])
    return result


def _intermediate(
    a: dict[str, Any], b: dict[str, Any], axis: Axis, cells: list[dict[str, Any]],
) -> tuple[str, ...]:
    # Record intervening physical cells without interpreting or excluding them.
    along, length, across, breadth = (
        ("x", "width", "y", "height") if axis == "horizontal"
        else ("y", "height", "x", "width")
    )
    first, last = sorted((a, b), key=lambda c: c[along])
    start, end = first[along] + first[length], last[along]
    low = max(a[across], b[across])
    high = min(a[across] + a[breadth], b[across] + b[breadth])
    if start >= end:
        return ()
    return tuple(
        c["ref"] for c in cells if c["ref"] not in {a["ref"], b["ref"]}
        and c[along] < end and c[along] + c[length] > start
        and c[across] < high and c[across] + c[breadth] > low
    )


def build_binding_catalog(metadata: dict[str, Any]) -> BindingCatalog:
    cells = sorted(metadata["cells"], key=lambda c: int(c["ref"][1:]))
    by_ref = {c["ref"]: c for c in cells}
    targets = set(metadata["writeRefs"])
    options: dict[tuple[str, str, Axis], tuple[set[str], float]] = {}

    def add(a: str, b: str, axis: Axis, reason: str, gap: float) -> None:
        key = (a, b, axis)
        reasons, old_gap = options.setdefault(key, (set(), gap))
        reasons.add(reason)
        options[key] = reasons, min(gap, old_gap)
        if len(options) > MAX_LINKS:
            raise ValueError("VISUAL_BINDING_LIMIT")

    for a in cells:
        if (
            a["ref"] not in targets or a["hasMeaning"] or a["protectedFacets"]
            or (a["roleLocked"] and a["existingRole"] not in {"input", "mark"})
        ):
            continue
        nearest: dict[str, list[tuple[dict[str, Any], Axis, float]]] = {}
        for b in cells:
            if a["ref"] == b["ref"]:
                continue
            if (
                b["ref"] not in targets or b["roleLocked"] or b["protectedFacets"]
            ) and b["existingRole"] != "label":
                continue
            for direction, axis, gap in _directions(a, b):
                if -CONTACT_PIXELS <= gap <= CONTACT_PIXELS:
                    add(a["ref"], b["ref"], axis, "contact:" + direction, gap)
                if gap >= 0:
                    found = nearest.get(direction, [])
                    if not found or gap < found[0][2]:
                        nearest[direction] = [(b, axis, gap)]
                    elif gap == found[0][2]:
                        found.append((b, axis, gap))
        for direction, choices in nearest.items():
            for b, axis, gap in choices:
                add(a["ref"], b["ref"], axis, "nearest:" + direction, gap)

    ordered = sorted(options, key=lambda k: (int(k[0][1:]), k[2], int(k[1][1:])))
    links = tuple(
        BindingCandidate(
            f"b{i + 1}", *key, tuple(sorted(options[key][0])), options[key][1],
            _intermediate(by_ref[key[0]], by_ref[key[1]], key[2], cells),
        )
        for i, key in enumerate(ordered)
    )
    catalog = BindingCatalog(
        digest([POLICY, policy_parameters(), metadata, [asdict(b) for b in links]]), links,
    )
    encoded = json.dumps(catalog.metadata(), ensure_ascii=False, separators=(",", ":"))
    if len(encoded.encode()) > MAX_METADATA_BYTES:
        raise ValueError("VISUAL_BINDING_LIMIT")
    return catalog
