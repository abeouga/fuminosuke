"""Compile semantic commands against one owned, versioned graph."""

from __future__ import annotations

from collections.abc import Callable
from functools import cached_property
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from app.domain.print_layout import transform_rect
from app.editing.catalog import Catalog, CatalogService, Target
from app.editing.errors import reject
from app.editing.operation_contracts import (
    DocumentNameConfigure,
    HeadingAttach,
    HeadingCreate,
    HeadingDelete,
    HeadingDetach,
    HeadingUpdate,
    IdentityConfigure,
    LocalRef,
    MarkClear,
    MarkConfigure,
    Operation,
    RecordsConfigure,
    RecordsDefine,
    RecordsDissolve,
    RecordsReplaceRows,
    Ref,
)
from app.layout_analysis.models import LayoutCell, LayoutDefinition

if TYPE_CHECKING:
    from app.editing.commands import Plan


class SemanticCommands:
    def __init__(
        self,
        catalog: Catalog,
        plan: Plan,
        local: dict[str, tuple[str, str]],
        resolve: Callable[[str, str | tuple[str, ...]], Target],
        meaning: Callable[[Ref], dict[str, Any]],
    ) -> None:
        self.catalog, self.plan, self.local = catalog, plan, local
        self.resolve, self.meaning = resolve, meaning
        self.metadata: list[IdentityConfigure | DocumentNameConfigure] = []
        self.required_bindings: set[str] = set()

    @property
    def layout(self) -> dict[str, Any]:
        return self.plan.after["layout"]  # type: ignore[no-any-return]

    @property
    def cells(self) -> dict[str, dict[str, Any]]:
        return {c["id"]: c for c in self.layout["cells"]}

    def create(self, local: str, kind: str) -> str:
        if local in self.local:
            raise reject("LOCAL_REF_INVALID", "同じlocalRefを二度作成できません。", 422)
        entity = f"{kind}_{uuid4().hex}"
        self.local[local] = self.plan.created[local] = (kind, entity)
        return entity

    def ref(self, ref: Ref, kinds: str | tuple[str, ...]) -> str:
        accepted = (kinds,) if isinstance(kinds, str) else kinds
        if isinstance(ref, LocalRef):
            target = self.local.get(ref.local_ref)
            if target is None or target[0] not in accepted:
                raise reject(
                    "LOCAL_REF_INVALID", "作成済みの同じ種類の対象を指定してください。", 422
                )
            return target[1]
        return self.resolve(ref, kinds).entity_id

    def cell(self, ref: Ref, kinds: str | tuple[str, ...] = ("cell", "heading")) -> dict[str, Any]:
        cell = self.cells.get(self.ref(ref, kinds))
        if cell is None:
            raise reject("TARGET_STALE", "削除済みの欄を参照しています。")
        return cell

    def item(self, ref: Ref, kind: str, collection: str) -> dict[str, Any]:
        entity = self.ref(ref, kind)
        item: dict[str, Any] | None = next(
            (v for v in self.layout[collection] if v["id"] == entity), None
        )
        if item is None:
            raise reject("TARGET_STALE", "削除済みの対象を参照しています。")
        return item

    def page(self, cell: dict[str, Any]) -> str | None:
        if not self.catalog.pages:
            return None
        # Page resolution only needs merges/cells; avoid validating an intermediate graph.
        layout = LayoutDefinition.model_construct(
            canonical_width=self.layout["canonicalWidth"],
            canonical_height=self.layout["canonicalHeight"],
            cell_merges=self.catalog_layout.cell_merges,
        )
        return CatalogService.cell_page(layout, LayoutCell.model_validate(cell), self.catalog.pages)

    @cached_property
    def catalog_layout(self) -> LayoutDefinition:
        return LayoutDefinition.model_validate(self.catalog.state["layout"])

    def same_page(self, cells: list[dict[str, Any]]) -> None:
        if len({self.page(cell) for cell in cells}) > 1:
            raise reject("PAGE_SCOPE_INVALID", "見出しと表の所属は同じページ内に指定してください。")

    def apply(self, operation: Operation) -> bool:
        from app.editing.mark_commands import edit_marks
        from app.editing.record_commands import edit_records

        if isinstance(operation, IdentityConfigure | DocumentNameConfigure):
            refs = (
                [entry.field for entry in operation.entries]
                if isinstance(operation, IdentityConfigure)
                else operation.rule.fields
                if operation.rule
                else []
            )
            for ref in refs:
                if not isinstance(ref, str):
                    if isinstance(ref.cell, LocalRef):
                        self.ref(ref.cell, ("cell", "heading"))
                    if isinstance(ref.meaning, LocalRef):
                        self.ref(ref.meaning, "meaning")
            self.metadata.append(operation)
        elif isinstance(
            operation, HeadingCreate | HeadingUpdate | HeadingAttach | HeadingDetach | HeadingDelete
        ):
            self.heading(operation)
        elif isinstance(
            operation, RecordsDefine | RecordsReplaceRows | RecordsConfigure | RecordsDissolve
        ):
            edit_records(self, operation)
        elif isinstance(operation, MarkConfigure | MarkClear):
            edit_marks(self, operation)
        else:
            return False
        return True

    def heading(
        self,
        operation: HeadingCreate | HeadingUpdate | HeadingAttach | HeadingDetach | HeadingDelete,
    ) -> None:
        from app.editing.commands import name

        if isinstance(operation, HeadingCreate):
            anchor = self.cell(operation.anchor)
            page_id = self.ref(operation.page, "page") if operation.page else None
            if page_id != self.page(anchor):
                raise reject("PAGE_SCOPE_INVALID", "見出しのページと基準欄のページが異なります。")
            page = next(
                (p for p in self.catalog.pages if f"{p['page']}:{p['range']}" == page_id), None
            )
            position = (
                transform_rect(anchor, page["transform"])
                if page and not anchor.get("virtual")
                else anchor
            )
            width = page["width"] if page else self.layout["canonicalWidth"]
            height = page["height"] if page else self.layout["canonicalHeight"]
            box_w, box_h = min(180, width), min(24, height)
            created_cell = LayoutCell(
                id=self.create(operation.local_ref, "heading"),
                role="parent_label",
                text=name(operation.name),
                source="manual",
                virtual=True,
                x=max(0, min(int(position["x"]), width - box_w)),
                y=max(0, min(int(position["y"]) - box_h, height - box_h)),
                width=box_w,
                height=box_h,
                page_range=page["range"] if page else None,
                confidence=1,
            )
            self.layout["cells"].append(created_cell.model_dump(mode="json", by_alias=True))
            return
        if isinstance(operation, HeadingUpdate):
            cell = self.cell(operation.heading)
            if cell["role"] not in {"parent_label", "label"}:
                raise reject("TARGET_KIND_MISMATCH", "見出しを指定してください。", 422)
            cell["text" if cell.get("virtual") else "fieldLabel"] = name(operation.name)
            return
        if isinstance(operation, HeadingDelete):
            cell = self.cell(operation.heading)
            if not cell.get("virtual"):
                raise reject("CAPABILITY_UNAVAILABLE", "物理見出しはこの操作で削除できません。")
            self.layout["cells"] = [c for c in self.layout["cells"] if c["id"] != cell["id"]]
            return
        parent = self.cell(operation.heading) if isinstance(operation, HeadingAttach) else None
        if parent and parent["role"] not in {"parent_label", "label"}:
            raise reject("TARGET_KIND_MISMATCH", "親に見出しを指定してください。", 422)
        ids = [self.ref(r, ("cell", "heading", "record_section")) for r in operation.children]
        if len(ids) != len(set(ids)):
            raise reject("INVALID_ARGUMENT", "子の指定が重複しています。", 422)
        sections = {s["id"]: s for s in self.layout["recordSections"]}
        for entity in ids:
            child = self.cells.get(entity) or sections.get(entity)
            if child is None:
                raise reject("TARGET_STALE", "削除済みの見出し対象です。")
            if entity in sections:
                members = section_cells(self.layout, entity)
                if parent:
                    self.same_page([parent, *[self.cells[c] for c in members]])
                # Shared collection bands represent the same heading node.
                siblings = [
                    s for s in sections.values() if s["collectionPath"] == child["collectionPath"]
                ]
                for sibling in siblings:
                    if parent:
                        self.same_page(
                            [
                                parent,
                                *[self.cells[c] for c in section_cells(self.layout, sibling["id"])],
                            ]
                        )
                    sibling["parentLabelCellId"] = parent["id"] if parent else None
            else:
                # Detaching is always allowed: a box changed to "not read" leaves its heading.
                if parent and child["role"] not in {"parent_label", "label", "input", "mark"}:
                    raise reject("DEPENDENCY_CONFLICT", "この分類には見出しを付けられません。")
                if parent:
                    self.same_page([parent, child])
                child["parentLabelCellId"] = parent["id"] if parent else None
                # Keep the existing relation-based representation consistent.
                for relation in self.layout["relations"]:
                    if entity in (relation["labelCellIds"] or [relation["inputCellId"]]):
                        relation["parentLabelCellId"] = parent["id"] if parent else None

    def finish(self) -> None:
        from app.editing.metadata_commands import configure_metadata
        from app.editing.record_commands import validate_records

        cells = self.cells
        bound = {r["inputCellId"] for r in self.layout["relations"]}
        bound.update(
            c["id"]
            for c in cells.values()
            if c["role"] == "mark"
            and c["markSets"]
            and all(m["targetPath"] and m["options"] for m in c["markSets"])
        )
        self.layout["recordStructureVerified"] = bool(self.layout["recordGroups"]) and all(
            set(g["inputCellIds"]) <= bound for g in self.layout["recordGroups"]
        )
        for cell_id in self.required_bindings:
            paths = {
                r["targetPath"] for r in self.layout["relations"] if r["inputCellId"] == cell_id
            }
            if len(paths) != 1:
                raise reject(
                    "DEPENDENCY_CONFLICT", "複数のマークを解除する場合は意味を明示してください。"
                )
        for node in [*cells.values(), *self.layout["relations"], *self.layout["recordSections"]]:
            parent = node.get("parentLabelCellId")
            if parent and (
                parent not in cells or cells[parent]["role"] not in {"label", "parent_label"}
            ):
                raise reject(
                    "DEPENDENCY_CONFLICT", "削除・分類変更した見出しへの所属が残っています。"
                )
        parents: dict[str, set[str]] = {}
        for cell in cells.values():
            if cell.get("parentLabelCellId"):
                parents.setdefault(cell["id"], set()).add(cell["parentLabelCellId"])
        for relation in self.layout["relations"]:
            if relation.get("parentLabelCellId"):
                for child in relation["labelCellIds"] or [relation["inputCellId"]]:
                    parents.setdefault(child, set()).add(relation["parentLabelCellId"])
        for section in self.layout["recordSections"]:
            if section.get("parentLabelCellId"):
                for child in section["recordLabelCellIds"]:
                    parents.setdefault(child, set()).add(section["parentLabelCellId"])
        if any(len(values) > 1 for values in parents.values()):
            raise reject("DEPENDENCY_CONFLICT", "同じ見出しに複数の親が指定されています。")
        for cell in cells.values():
            if cell.get("parentLabelCellId"):
                self.same_page([cell, cells[cell["parentLabelCellId"]]])
        for relation in self.layout["relations"]:
            linked = [relation["inputCellId"], *relation["labelCellIds"]]
            if relation.get("parentLabelCellId"):
                linked.append(relation["parentLabelCellId"])
            self.same_page([cells[c] for c in linked if c in cells])
        validate_records(self)
        configure_metadata(self, self.metadata)


def section_cells(layout: dict[str, Any], section_id: str) -> set[str]:
    section = next(s for s in layout["recordSections"] if s["id"] == section_id)
    return (
        {
            c
            for g in layout["recordGroups"]
            if g["sectionId"] == section_id
            for c in [
                *g["inputCellIds"],
                *([g["recordKeyCellId"]] if g.get("recordKeyCellId") else []),
            ]
        }
        | set(section["recordLabelCellIds"])
        | set(section["rowKeyCellIds"])
    )
