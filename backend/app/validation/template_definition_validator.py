from __future__ import annotations

from app.domain.template_definition import (
    TemplateDefinition,
    TemplateDefinitionValidation,
    TemplateField,
    ValidationIssue,
)


def validate_template_definition(
    definition: TemplateDefinition,
) -> TemplateDefinitionValidation:
    issues: list[ValidationIssue] = []
    cells = {cell.id: cell for cell in definition.cells}
    if len(cells) != len(definition.cells):
        issues.append(_error("DUPLICATE_CELL_ID", "cells", "セルIDが重複しています。"))
    geometry_ids = [item.cell_id for item in definition.geometry]
    if len(geometry_ids) != len(set(geometry_ids)):
        issues.append(_error("DUPLICATE_GEOMETRY", "geometry", "位置情報が重複しています。"))
    for geometry in definition.geometry:
        if geometry.cell_id not in cells:
            issues.append(
                _error(
                    "UNKNOWN_GEOMETRY_CELL",
                    "geometry",
                    "位置情報が存在しないセルを参照しています。",
                    [geometry.cell_id],
                )
            )

    fields: list[tuple[str, str, TemplateField]] = [
        ("standalone", f"standaloneFields.{index}", field)
        for index, field in enumerate(definition.standalone_fields)
    ]
    section_ids: set[str] = set()
    section_keys: set[str] = set()
    record_ids: set[str] = set()
    used_parent_cells: set[str] = set()
    for section_index, section in enumerate(definition.sections):
        if section.id in section_ids:
            issues.append(
                _error(
                    "DUPLICATE_SECTION_ID",
                    f"sections.{section_index}.id",
                    "Section IDが重複しています。",
                )
            )
        section_ids.add(section.id)
        if section.key in section_keys:
            issues.append(
                _error(
                    "DUPLICATE_SECTION_KEY",
                    f"sections.{section_index}.key",
                    "Section keyが重複しています。",
                )
            )
        section_keys.add(section.key)
        if section.parent_label_cell_id is not None:
            used_parent_cells.add(section.parent_label_cell_id)
            parent = cells.get(section.parent_label_cell_id)
            if parent is None or parent.type != "PARENT_LABEL":
                issues.append(
                    _error(
                        "INVALID_SECTION_PARENT",
                        f"sections.{section_index}.parentLabelCellId",
                        "セクション見出しはPARENT_LABELセルを参照する必要があります。",
                        [section.parent_label_cell_id],
                    )
                )
        for record_index, record in enumerate(section.records):
            if record.id in record_ids:
                issues.append(
                    _error(
                        "DUPLICATE_RECORD_ID",
                        f"sections.{section_index}.records.{record_index}.id",
                        "Record IDが重複しています。",
                    )
                )
            record_ids.add(record.id)
            for field_index, field in enumerate(record.fields):
                fields.append(
                    (
                        f"section:{section.id}:record:{record.id}",
                        f"sections.{section_index}.records.{record_index}.fields.{field_index}",
                        field,
                    )
                )

    field_ids: set[str] = set()
    field_keys_by_scope: dict[str, set[str]] = {}
    owned_values: dict[str, list[tuple[str, TemplateField]]] = {}
    used_labels: set[str] = set()
    for scope, path, field in fields:
        if field.id in field_ids:
            issues.append(_error("DUPLICATE_FIELD_ID", path, "Field IDが重複しています。"))
        field_ids.add(field.id)
        field_keys = field_keys_by_scope.setdefault(scope, set())
        if field.key in field_keys:
            issues.append(_error("DUPLICATE_FIELD_KEY", path, "Field keyが重複しています。"))
        field_keys.add(field.key)
        value = cells.get(field.value_cell_id)
        if value is None or value.type != "VALUE":
            issues.append(
                _error(
                    "INVALID_VALUE_REFERENCE",
                    f"{path}.valueCellId",
                    "FieldはVALUEセルを参照する必要があります。",
                    [field.value_cell_id],
                )
            )
        previous_owners = owned_values.get(field.value_cell_id, [])
        incompatible_owner = next(
            (
                owner_path
                for owner_path, owner_field in previous_owners
                if not _mark_fields_can_share_value(value, owner_field, field)
            ),
            None,
        )
        if incompatible_owner is not None:
            issues.append(
                _error(
                    "DUPLICATE_VALUE_OWNERSHIP",
                    f"{path}.valueCellId",
                    f"VALUEセルは既に{incompatible_owner}で使用されています。",
                    [field.value_cell_id],
                )
            )
        owned_values.setdefault(field.value_cell_id, []).append((path, field))
        if field.label_cell_id is not None:
            label = cells.get(field.label_cell_id)
            if label is None or label.type != "LABEL":
                issues.append(
                    _error(
                        "INVALID_LABEL_REFERENCE",
                        f"{path}.labelCellId",
                        "FieldラベルはLABELセルを参照する必要があります。",
                        [field.label_cell_id],
                    )
                )
            used_labels.add(field.label_cell_id)

    if len(fields) > 300:
        issues.append(
            _error(
                "FIELD_LIMIT_EXCEEDED",
                "standaloneFields",
                "1テンプレートで読み取れるFieldは300件までです。",
            )
        )

    explicitly_unassigned = set(definition.unassigned_cell_ids)
    if len(explicitly_unassigned) != len(definition.unassigned_cell_ids):
        issues.append(
            _error(
                "DUPLICATE_UNASSIGNED_CELL",
                "unassignedCellIds",
                "未割当セルIDが重複しています。",
            )
        )
    for cell_id in explicitly_unassigned:
        if cell_id not in cells:
            issues.append(
                _error(
                    "UNKNOWN_UNASSIGNED_CELL",
                    "unassignedCellIds",
                    "存在しないセルが未割当として指定されています。",
                    [cell_id],
                )
            )
        elif (
            cell_id in owned_values
            or cell_id in used_labels
            or cell_id in used_parent_cells
        ):
            issues.append(
                _error(
                    "ASSIGNED_CELL_MARKED_UNASSIGNED",
                    "unassignedCellIds",
                    "使用中のセルを未割当にはできません。",
                    [cell_id],
                )
            )
    for cell in definition.cells:
        if cell.type == "VALUE" and cell.id not in owned_values:
            issues.append(
                _warning(
                    "ORPHAN_VALUE",
                    "unassignedCellIds",
                    "Fieldに割り当てられていないVALUEセルです。",
                    [cell.id],
                )
            )
        if cell.type == "LABEL" and cell.id not in used_labels:
            issues.append(
                _warning(
                    "ORPHAN_LABEL",
                    "unassignedCellIds",
                    "Fieldに使用されていないLABELセルです。",
                    [cell.id],
                )
            )
        if cell.type == "PARENT_LABEL" and cell.id not in used_parent_cells:
            issues.append(
                _warning(
                    "ORPHAN_PARENT_LABEL",
                    "unassignedCellIds",
                    "Sectionに使用されていないPARENT_LABELセルです。",
                    [cell.id],
                )
            )
        if (
            cell.type in {"LABEL", "VALUE", "PARENT_LABEL"}
            and cell.id not in explicitly_unassigned
            and (
                (cell.type == "VALUE" and cell.id not in owned_values)
                or (cell.type == "LABEL" and cell.id not in used_labels)
                or (
                    cell.type == "PARENT_LABEL"
                    and cell.id not in used_parent_cells
                )
            )
        ):
            issues.append(
                _error(
                    "UNTRACKED_ORPHAN",
                    "unassignedCellIds",
                    "未割当セルをunassignedCellIdsへ明示する必要があります。",
                    [cell.id],
                )
            )

    errors = [issue for issue in issues if issue.severity == "ERROR"]
    return TemplateDefinitionValidation(valid=not errors, issues=issues)


def _mark_fields_can_share_value(
    value_cell: object,
    first: TemplateField,
    second: TemplateField,
) -> bool:
    return bool(
        getattr(value_cell, "source_role", None) == "mark"
        and first.mark_set_id
        and second.mark_set_id
        and first.mark_set_id != second.mark_set_id
    )


def _error(
    code: str,
    path: str,
    message: str,
    cell_ids: list[str] | None = None,
) -> ValidationIssue:
    return ValidationIssue(
        severity="ERROR", code=code, path=path, message=message, cell_ids=cell_ids or []
    )


def _warning(
    code: str,
    path: str,
    message: str,
    cell_ids: list[str] | None = None,
) -> ValidationIssue:
    return ValidationIssue(
        severity="WARNING", code=code, path=path, message=message, cell_ids=cell_ids or []
    )
