from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from app.layout_analysis.models import LayoutCell, LayoutDefinition


def empty_review_display_structure() -> dict[str, object]:
    return {
        "schemaVersion": 1,
        "fields": [],
        "recordSections": [],
        "parentSections": [],
        "independentFieldIds": [],
    }


def build_review_display_structure(
    layout: LayoutDefinition | None,
    raw_mappings: object,
) -> dict[str, object]:
    if not isinstance(raw_mappings, Sequence) or isinstance(raw_mappings, str | bytes):
        return empty_review_display_structure()
    mappings = [
        dict(mapping)
        for mapping in raw_mappings
        if isinstance(mapping, Mapping)
        and isinstance(mapping.get("mappingId"), str)
        and isinstance(mapping.get("targetPath"), str)
    ]
    if not mappings:
        return empty_review_display_structure()

    mapping_order = {
        str(mapping["mappingId"]): index for index, mapping in enumerate(mappings)
    }
    cells = {cell.id: cell for cell in layout.cells} if layout else {}
    mapping_ids_by_input = _mapping_ids_by_input(layout, mapping_order)
    input_id_by_mapping = {
        mapping_id: input_cell_id
        for input_cell_id, mapping_ids in mapping_ids_by_input.items()
        for mapping_id in mapping_ids
    }

    relations_by_input: dict[str, list[Any]] = {}
    if layout is not None:
        for relation in layout.relations:
            relations_by_input.setdefault(relation.input_cell_id, []).append(relation)

    fields: list[dict[str, object | None]] = []
    selected_relation_by_field_id: dict[str, Any] = {}
    for mapping in mappings:
        mapping_id = str(mapping["mappingId"])
        input_cell_id = input_id_by_mapping.get(mapping_id)
        field_relation = _relation_for_mapping(
            relations_by_input.get(input_cell_id or "", []),
            str(mapping["targetPath"]),
        )
        if field_relation is not None:
            selected_relation_by_field_id[mapping_id] = field_relation
        fields.append(
            {
                "id": mapping_id,
                "mappingId": mapping_id,
                "targetPath": str(mapping["targetPath"]),
                "label": _non_empty_text(mapping.get("label")) or str(mapping["targetPath"]),
                "groupId": _logical_field_group_id(field_relation, mapping_id, cells),
                "inputCellId": input_cell_id,
                "relationId": field_relation.id if field_relation is not None else None,
            }
        )

    assigned_field_ids: set[str] = set()
    record_sections: list[dict[str, object]] = []
    if layout is not None:
        groups_by_section: dict[str, list[Any]] = {}
        for group in layout.record_groups:
            groups_by_section.setdefault(group.section_id, []).append(group)

        for section in layout.record_sections:
            groups = sorted(
                groups_by_section.get(section.id, []),
                key=lambda group: (group.order, group.y, group.x, group.id),
            )
            column_count = max(
                [
                    len(section.record_label_cell_ids),
                    *(len(group.input_cell_ids) for group in groups),
                ],
                default=0,
            )
            if not groups or column_count == 0:
                continue
            column_specs: list[tuple[int, int, dict[str, object]]] = []
            for index in range(column_count):
                label_cell_id = (
                    section.record_label_cell_ids[index]
                    if index < len(section.record_label_cell_ids)
                    else None
                )
                mapping_count = max(
                    (
                        len(mapping_ids_by_input.get(group.input_cell_ids[index], []))
                        for group in groups
                        if index < len(group.input_cell_ids)
                    ),
                    default=0,
                )
                base_column_id = label_cell_id or f"{section.id}:column:{index + 1}"
                for mapping_index in range(max(1, mapping_count)):
                    first_field_id = next(
                        (
                            mapping_ids[mapping_index]
                            for group in groups
                            if index < len(group.input_cell_ids)
                            and len(
                                mapping_ids := mapping_ids_by_input.get(
                                    group.input_cell_ids[index],
                                    [],
                                )
                            )
                            > mapping_index
                        ),
                        None,
                    )
                    column: dict[str, object] = {
                        "id": (
                            base_column_id
                            if mapping_index == 0
                            else f"{base_column_id}:field:{mapping_index + 1}"
                        ),
                        "label": (
                            _cell_label(cells.get(label_cell_id or ""))
                            if mapping_index == 0
                            else None
                        )
                        or _mapping_label(mappings, first_field_id)
                        or f"列 {index + 1}",
                    }
                    column_specs.append((index, mapping_index, column))

            columns = [column for _, _, column in column_specs]

            rows: list[dict[str, object]] = []
            for group in groups:
                row_cells: list[dict[str, object | None]] = []
                for index, mapping_index, column in column_specs:
                    mapping_ids = (
                        mapping_ids_by_input.get(group.input_cell_ids[index], [])
                        if index < len(group.input_cell_ids)
                        else []
                    )
                    field_id = (
                        mapping_ids[mapping_index]
                        if mapping_index < len(mapping_ids)
                        else None
                    )
                    if field_id is not None:
                        assigned_field_ids.add(field_id)
                    row_cells.append({"columnId": column["id"], "fieldId": field_id})
                rows.append(
                    {
                        "id": group.id,
                        "order": group.order,
                        "cells": row_cells,
                    }
                )
            record_sections.append(
                {
                    "id": section.id,
                    "title": section.resolved_display_name,
                    "collectionPath": section.collection_path,
                    "columns": columns,
                    "rows": rows,
                }
            )

    parent_fields: dict[str, list[str]] = {}
    if layout is not None:
        for field_id, relation in selected_relation_by_field_id.items():
            parent_id = _effective_parent(relation, cells)
            if (
                not parent_id
                or parent_id not in cells
                or field_id in assigned_field_ids
            ):
                continue
            members = parent_fields.setdefault(parent_id, [])
            if field_id not in members:
                members.append(field_id)

    parent_sections = [
        {
            "id": parent_id,
            "title": _cell_label(cells[parent_id])
            or _non_empty_text(cells[parent_id].field_label)
            or "名称未設定のグループ",
            "fieldIds": sorted(
                field_ids,
                key=lambda field_id: _field_sort_key(
                    field_id,
                    input_id_by_mapping,
                    cells,
                    mappings,
                    mapping_order,
                ),
            ),
        }
        for parent_id, field_ids in sorted(
            parent_fields.items(),
            key=lambda item: _cell_sort_key(cells[item[0]]),
        )
    ]
    for parent_section in parent_sections:
        assigned_field_ids.update(
            str(field_id) for field_id in parent_section["fieldIds"]
        )

    independent_field_ids = sorted(
        (mapping_id for mapping_id in mapping_order if mapping_id not in assigned_field_ids),
        key=lambda field_id: _field_sort_key(
            field_id,
            input_id_by_mapping,
            cells,
            mappings,
            mapping_order,
        ),
    )
    return {
        "schemaVersion": 1,
        "fields": fields,
        "recordSections": record_sections,
        "parentSections": parent_sections,
        "independentFieldIds": independent_field_ids,
    }


def _relation_for_mapping(relations: list[Any], target_path: str) -> Any | None:
    matching = [relation for relation in relations if relation.target_path == target_path]
    return next(
        (relation for relation in matching if relation.parent_label_cell_id),
        matching[0] if matching else None,
    )


def _effective_parent(relation: Any, cells: dict[str, LayoutCell]) -> str | None:
    """The heading of a link; a link made after the item name got its heading takes the
    item name's heading (「その他」's write-in under 「利用目的」)."""
    if relation.parent_label_cell_id:
        return str(relation.parent_label_cell_id)
    parents = {_cell_heading(label_id, cells) for label_id in relation.label_cell_ids}
    if len(parents) == 1:
        parent = next(iter(parents))
        return parent if parent and parent in cells else None
    return None


def _cell_heading(cell_id: str, cells: dict[str, LayoutCell]) -> str | None:
    cell = cells.get(cell_id)
    if cell is None:
        return None
    if cell.parent_label_cell_id:
        return cell.parent_label_cell_id
    # A heading line drawn from the item name to the heading stores them the other way
    # round (the heading under a plain item name); it still puts the item under it.
    if cell.role != "label":
        return None
    reversed_headings = [
        other.id
        for other in cells.values()
        if other.role == "parent_label" and other.parent_label_cell_id == cell_id
    ]
    return reversed_headings[0] if len(reversed_headings) == 1 else None


def _logical_field_group_id(
    relation: Any | None, mapping_id: str, cells: dict[str, LayoutCell]
) -> str:
    """Return a stable display group without relying on the rendered label text."""
    if relation is None:
        return f"field:{mapping_id}"
    label_cell_ids = sorted(set(relation.label_cell_ids))
    if not label_cell_ids:
        return f"field:{mapping_id}"
    parent_id = _effective_parent(relation, cells) or "none"
    return f"labels:{parent_id}:{'|'.join(label_cell_ids)}"


def _mapping_ids_by_input(
    layout: LayoutDefinition | None,
    mapping_order: dict[str, int],
) -> dict[str, list[str]]:
    if layout is None:
        return {}
    result: dict[str, list[str]] = {}
    for cell in layout.cells:
        candidates = [
            f"layout_{cell.id}",
            *(f"layout_{cell.id}_{mark_set.id}"[:100] for mark_set in cell.mark_sets),
        ]
        mapping_ids = sorted(
            {
                mapping_id
                for mapping_id in candidates
                if mapping_id in mapping_order
            },
            key=mapping_order.__getitem__,
        )
        if mapping_ids:
            result[cell.id] = mapping_ids
    return result


def _mapping_label(mappings: list[dict[str, Any]], mapping_id: str | None) -> str | None:
    if mapping_id is None:
        return None
    mapping = next((item for item in mappings if item.get("mappingId") == mapping_id), None)
    return _non_empty_text(mapping.get("label")) if mapping else None


def _cell_label(cell: LayoutCell | None) -> str | None:
    if cell is None:
        return None
    return _non_empty_text(cell.printed_label_text) or _non_empty_text(cell.text)


def _non_empty_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _cell_sort_key(cell: LayoutCell) -> tuple[int, int, str]:
    return (cell.y, cell.x, cell.id)


def _field_sort_key(
    field_id: str,
    input_id_by_mapping: dict[str, str],
    cells: dict[str, LayoutCell],
    mappings: list[dict[str, Any]],
    mapping_order: dict[str, int],
) -> tuple[int, int, int, str]:
    cell = cells.get(input_id_by_mapping.get(field_id, ""))
    if cell is not None:
        return (cell.y, cell.x, mapping_order[field_id], field_id)
    mapping = next((item for item in mappings if item.get("mappingId") == field_id), {})
    y = mapping.get("y")
    x = mapping.get("x")
    return (
        int(y) if isinstance(y, int | float) else 2**31 - 1,
        int(x) if isinstance(x, int | float) else 2**31 - 1,
        mapping_order[field_id],
        field_id,
    )
