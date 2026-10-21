"""Explicit row membership; order never establishes physical identity."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from app.editing.errors import reject
from app.editing.operation_contracts import (
    RecordRow,
    RecordsConfigure,
    RecordsDefine,
    RecordsDissolve,
    RecordsReplaceRows,
)
from app.layout_analysis.models import LayoutRecordGroup, LayoutRecordSection
from app.layout_analysis.record_geometry import validate_record_row_keys
from app.services.page_authoring_validation import bounds

if TYPE_CHECKING:
    from app.editing.semantic_commands import SemanticCommands


def edit_records(
    ctx: SemanticCommands,
    op: RecordsDefine | RecordsReplaceRows | RecordsConfigure | RecordsDissolve,
) -> None:
    from app.editing.commands import name

    layout = ctx.layout
    if isinstance(op, RecordsDefine):
        columns = [
            (ctx.meaning(c.meaning), [ctx.ref(h, "cell") for h in c.label_cells])
            for c in op.columns
        ]
        labels = [c for _, ids in columns for c in ids]
        if len(labels) != len(set(labels)) or any(ctx.cells[c]["role"] != "label" for c in labels):
            raise reject("INVALID_GRAPH", "列の項目名セルが重複しているか分類が異なります。", 422)
        section = LayoutRecordSection(
            id=ctx.create(op.local_ref, "record_section"),
            title=name(op.name),
            display_name=name(op.name),
            record_type="generic",
            collection_path=f"genericRecords.table_{uuid4().hex}",
            # The legacy display structure treats this as one label per column.
            # Keep every label in relations; an incomplete band uses field names.
            record_label_cell_ids=[ids[0] for _, ids in columns]
            if all(ids for _, ids in columns) and len(columns) <= 30
            else [],
            **op.options.model_dump(),
        ).model_dump(mode="json", by_alias=True)
        layout["recordSections"].append(section)
        if op.column_labels is not None:
            labels = [ctx.ref(h, "cell") for h in op.column_labels]
            if len(labels) != len(set(labels)) or any(
                ctx.cells[c]["role"] != "label" for c in labels
            ):
                raise reject("INVALID_GRAPH", "表の列ラベルを確認してください。", 422)
            section["recordLabelCellIds"] = labels
        replace_rows(ctx, section, op.rows, columns)
    else:
        section = ctx.item(op.section, "record_section", "recordSections")
        if isinstance(op, RecordsDissolve):
            layout["recordGroups"] = [
                g for g in layout["recordGroups"] if g["sectionId"] != section["id"]
            ]
            layout["recordSections"] = [
                s for s in layout["recordSections"] if s["id"] != section["id"]
            ]
        elif isinstance(op, RecordsConfigure):
            section.update(
                displayName=name(op.name) if op.name is not None else None,
                **op.options.model_dump(by_alias=True),
            )
            if op.column_labels is not None:
                labels = [ctx.ref(h, "cell") for h in op.column_labels]
                if len(labels) != len(set(labels)) or any(
                    ctx.cells[c]["role"] != "label" for c in labels
                ):
                    raise reject("INVALID_GRAPH", "表の列ラベルを確認してください。", 422)
                section["recordLabelCellIds"] = labels
        else:
            groups = sorted(
                (g for g in layout["recordGroups"] if g["sectionId"] == section["id"]),
                key=lambda g: (g["order"], g["id"]),
            )
            # An explicitly saved empty draft table has no prior rows. Resolve
            # its first completed row from existing physical-cell bindings.
            first_cells = (
                groups[0]["inputCellIds"]
                if groups
                else [ctx.ref(h, "cell") for h in op.rows[0].cells]
                if op.rows
                else []
            )
            columns = []
            for cell_id in first_cells:
                relations = [r for r in layout["relations"] if r["inputCellId"] == cell_id]
                mark_paths = [m["targetPath"] for m in ctx.cells[cell_id].get("markSets", [])]
                path = (
                    relations[0]["targetPath"]
                    if relations
                    else mark_paths[0]
                    if len(mark_paths) == 1
                    else None
                )
                pool = next(
                    (p for p in layout["labelPool"] if p["targetPath"] == path),
                    None,
                )
                if pool is None:
                    raise reject("DEPENDENCY_CONFLICT", "各列の意味付けを先に完成させてください。")
                columns.append(
                    (pool, list(dict.fromkeys(c for r in relations for c in r["labelCellIds"])))
                )
            replace_rows(ctx, section, op.rows, columns)
    # This is the existing explicit row-link completeness flag, not OCR qualification.
    bound = {r["inputCellId"] for r in layout["relations"]}
    bound.update(c["id"] for c in layout["cells"] if c["role"] == "mark" and c["markSets"])
    layout["recordStructureVerified"] = bool(layout["recordGroups"]) and all(
        set(g["inputCellIds"]) <= bound for g in layout["recordGroups"]
    )


def replace_rows(
    ctx: SemanticCommands,
    section: dict[str, Any],
    rows: list[RecordRow],
    columns: list[tuple[dict[str, Any], list[str]]],
) -> None:
    from app.editing.commands import CommandCompiler, name

    layout, cells = ctx.layout, ctx.cells
    previous = {g["id"]: g for g in layout["recordGroups"] if g["sectionId"] == section["id"]}
    occupied = {
        c
        for g in layout["recordGroups"]
        if g["sectionId"] != section["id"]
        for c in g["inputCellIds"]
    }
    groups = []
    ids_seen: set[str] = set()
    for order, row in enumerate(rows, 1):
        ids = [ctx.ref(h, "cell") for h in row.cells]
        if (
            len(ids) != len(columns)
            or len(ids) != len(set(ids))
            or set(ids) & (ids_seen | occupied)
        ):
            raise reject("INVALID_GRAPH", "表の列数・行の所属・セルの重複を確認してください。", 422)
        ids_seen.update(ids)
        if any(
            cells[c]["role"] not in {"input", "mark"}
            or cells[c].get("compositeSubfields")
            or (cells[c]["role"] == "mark" and len(cells[c]["markSets"]) != 1)
            for c in ids
        ):
            raise reject("CAPABILITY_UNAVAILABLE", "表の各欄には一意の意味を指定してください。")
        existing = previous.get(ctx.ref(row.group, "record_group")) if row.group else None
        if row.group and (existing is None or existing["inputCellIds"] != ids):
            raise reject("ROW_IDENTITY_MISMATCH", "既存の行IDに別の物理セルを割り当てられません。")
        if not row.group and any(set(ids) & set(g["inputCellIds"]) for g in previous.values()):
            raise reject("ROW_IDENTITY_MISMATCH", "既存の物理行は現在の行IDで指定してください。")
        group_id = existing["id"] if existing else ctx.create(str(row.local_ref), "record_group")
        key_id = ctx.ref(row.row_key_cell, "cell") if row.row_key_cell else None
        if key_id and cells[key_id]["role"] != "record_key_cell":
            raise reject("DEPENDENCY_CONFLICT", "行番号セルの分類を指定してください。")
        for cell_id, (meaning, labels) in zip(ids, columns, strict=True):
            if existing:
                continue
            relations = [r for r in layout["relations"] if r["inputCellId"] == cell_id]
            if not relations:
                used = {r["targetPath"] for r in layout["relations"]}
                used.update(m["targetPath"] for c in cells.values() for m in c.get("markSets", []))
                entry = meaning
                if cells[cell_id]["role"] == "mark":
                    entry = {**meaning, "targetPath": cells[cell_id]["markSets"][0]["targetPath"]}
                elif entry["targetPath"] in used:
                    entry = CommandCompiler._new_meaning(meaning["label"], meaning["aliases"])
                    layout["labelPool"].append(entry)
                layout["relations"].append(
                    CommandCompiler._relation(cell_id, entry["targetPath"], labels, "vertical")
                )
            else:
                # Preserve the physical field path and relation IDs while defining its column.
                for relation in relations:
                    relation["labelCellIds"] = labels[:]
            # Every row shows the column's name, not the first row's 「備考 1行目」. A name
            # typed for one box stays unless it was only the old made-up row name.
            column_name = re.sub(r"\s*\d+行目$", "", meaning["label"]).strip() or meaning["label"]
            current = cells[cell_id].get("fieldLabel")
            if not current or re.search(r"\d+行目$", current) or current == meaning["label"]:
                cells[cell_id]["fieldLabel"] = column_name
        group = LayoutRecordGroup(
            id=group_id,
            section_id=section["id"],
            order=order,
            **bounds([cells[c] for c in ids]),
            input_cell_ids=ids,
            record_key_field="rowNumber",
            record_key_cell_id=key_id,
            record_key_value=name(row.row_key_value) if row.row_key_value is not None else None,
            source="manual",
        ).model_dump(mode="json", by_alias=True)
        groups.append(group)
    layout["recordGroups"] = [
        g for g in layout["recordGroups"] if g["sectionId"] != section["id"]
    ] + groups
    section["rowKeyCellIds"] = [g["recordKeyCellId"] for g in groups if g["recordKeyCellId"]]


def validate_records(ctx: SemanticCommands) -> None:
    from app.editing.semantic_commands import section_cells

    try:
        validate_record_row_keys(ctx.layout)
    except ValueError as error:
        raise reject(
            str(error), "行番号セルは対応する同じ行に一意に指定してください。", 422,
        ) from error
    for section in ctx.layout["recordSections"]:
        groups = [g for g in ctx.layout["recordGroups"] if g["sectionId"] == section["id"]]
        if len({len(g["inputCellIds"]) for g in groups}) > 1:
            raise reject("INVALID_GRAPH", "同じ表の行ごとに列数が異なります。", 422)
        members = section_cells(ctx.layout, section["id"])
        if section.get("parentLabelCellId"):
            members.add(section["parentLabelCellId"])
        ctx.same_page([ctx.cells[c] for c in members if c in ctx.cells])
        if section["rowKeyStrategy"] == "printed":
            keys = [g.get("recordKeyValue") for g in groups]
            if any(
                not g.get("recordKeyCellId") or not g.get("recordKeyValue") for g in groups
            ) or len(keys) != len(set(keys)):
                raise reject(
                    "RECORD_KEY_REQUIRED",
                    "各行に一意の印字行番号と行番号セルを指定してください。",
                    422,
                )
