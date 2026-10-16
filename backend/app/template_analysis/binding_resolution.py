"""Resolve issued bindings for ordinary fields and validated table columns.

Geometry permits inheritance of a column binding; it never selects a heading
or supplies missing semantic evidence. No persistence or provider dependency.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import pairwise
from typing import Annotated, Any, Literal

from pydantic import Field

from app.editing.operation_contracts import Contract
from app.layout_analysis.record_geometry import validate_row_keys
from app.template_analysis.contracts import CellRef
from app.template_analysis.visual_bindings import (
    CONTACT_PIXELS,
    MIN_OVERLAP_RATIO,
    BindingCatalog,
)

POLICY = "template-analysis.binding-resolution.v2"
LinkRef = Annotated[str, Field(pattern=r"^b[1-9][0-9]{0,3}$", strict=True)]


class LinkedBinding(Contract):
    kind: Literal["linked"]
    links: Annotated[list[LinkRef], Field(min_length=1, max_length=30)]


class UnlabelledEvidence(Contract):
    basis: Literal["free_entry", "in_cell_caption"]
    text: Annotated[str, Field(min_length=1, max_length=300, strict=True)]
    refs: Annotated[list[CellRef], Field(min_length=1, max_length=30)]


class UnlabelledBinding(Contract):
    kind: Literal["unlabelled"]
    cell: CellRef
    evidence: UnlabelledEvidence


Binding = LinkedBinding | UnlabelledBinding


@dataclass(frozen=True)
class ResolvedBinding:
    cell: str
    labels: tuple[str, ...]
    axis: Literal["horizontal", "vertical", "direct"]
    evidence: UnlabelledEvidence | None


class BindingResolver:
    def __init__(self, catalog: BindingCatalog, metadata: dict[str, Any], digest: str):
        if digest != catalog.digest:
            raise ValueError("VISUAL_BINDING_CATALOG_STALE")
        self.links = {b.ref: b for b in catalog.links}
        self.cells = {c["ref"]: c for c in metadata["cells"]}
        self.targets = set(metadata["writeRefs"])

    def resolve(self, binding: Binding) -> ResolvedBinding:
        if isinstance(binding, LinkedBinding):
            if (
                len(binding.links) != len(set(binding.links))
                or not set(binding.links) <= self.links.keys()
            ):
                raise ValueError("VISUAL_BINDING_REFERENCE")
            selected = [self.links[b] for b in binding.links]
            if len({(b.input, b.axis) for b in selected}) != 1:
                raise ValueError("VISUAL_BINDING_MIXED_TARGET")
            result = ResolvedBinding(
                selected[0].input, tuple(b.label for b in selected), selected[0].axis, None,
            )
        else:
            cell, proof = binding.cell, binding.evidence
            if (
                cell not in self.cells or not set(proof.refs) <= self.cells.keys()
                or len(proof.refs) != len(set(proof.refs)) or not proof.text.strip()
                or (proof.basis == "in_cell_caption" and proof.refs != [cell])
                or (proof.basis == "free_entry" and not set(proof.refs) - {cell})
            ):
                raise ValueError("VISUAL_UNLABELLED_EVIDENCE")
            result = ResolvedBinding(cell, (), "direct", proof)
        if result.cell not in self.targets or not set(result.labels) <= self.cells.keys():
            raise ValueError("VISUAL_BINDING_SCOPE")
        return result

    def table(
        self, rows: list[list[str]], columns: list[ResolvedBinding], row_keys: list[str | None],
    ) -> None:
        """Validate the whole physical body before propagating any column."""
        if not rows or any(len(row) != len(columns) for row in rows):
            raise ValueError("VISUAL_TABLE_WIDTH")
        body = [r for row in rows for r in row]
        if len(body) != len(set(body)):
            raise ValueError("VISUAL_DOUBLE_OWNER")
        keys = {key for key in row_keys if key is not None}
        if not (set(body) | keys) <= self.targets:
            raise ValueError("VISUAL_BINDING_SCOPE")
        validate_row_keys(self.cells, rows, row_keys)
        bands = []
        for row in rows:
            cells = [self.cells[r] for r in row]
            if min(c["y"] + c["height"] for c in cells) <= max(c["y"] for c in cells):
                raise ValueError("VISUAL_TABLE_ROW_ALIGNMENT")
            if any(
                a["x"] + a["width"] > b["x"] + CONTACT_PIXELS for a, b in pairwise(cells)
            ):
                raise ValueError("VISUAL_TABLE_COLUMN_ORDER")
            bands.append((min(c["y"] for c in cells), max(c["y"] + c["height"] for c in cells)))
        if any(a[1] > b[0] + CONTACT_PIXELS for a, b in pairwise(bands)):
            raise ValueError("VISUAL_TABLE_ROW_ORDER")
        exemptions = set(body) | keys | {r for column in columns for r in column.labels}
        for i, column in enumerate(columns):
            if column.cell != rows[0][i]:
                raise ValueError("VISUAL_TABLE_BINDING_TARGET")
            if column.axis not in {"vertical", "direct"}:
                raise ValueError("VISUAL_TABLE_COLUMN_AXIS")
            anchor = self.cells[column.cell]
            column_cells = [self.cells[row[i]] for row in rows]
            for cell in column_cells:
                if _overlap(anchor, cell, "x", "width") < MIN_OVERLAP_RATIO * min(
                    anchor["width"], cell["width"],
                ):
                    raise ValueError("VISUAL_TABLE_COLUMN_ALIGNMENT")
            # Check intersections, including a cell whose centre lies outside
            # the band but whose interior intrudes. ignored is no exemption.
            left = min(c["x"] for c in column_cells)
            right = max(c["x"] + c["width"] for c in column_cells)
            corridor = dict(
                x=left, width=right-left, y=bands[0][0], height=bands[-1][1]-bands[0][0],
            )
            if any(
                ref not in exemptions
                and _overlap(corridor, cell, "x", "width") > CONTACT_PIXELS
                and _overlap(corridor, cell, "y", "height") > CONTACT_PIXELS
                for ref, cell in self.cells.items()
            ):
                raise ValueError("VISUAL_TABLE_INTERIOR_OMISSION")


def _overlap(a: dict[str, Any], b: dict[str, Any], axis: str, size: str) -> float:
    return float(min(a[axis] + a[size], b[axis] + b[size]) - max(a[axis], b[axis]))
