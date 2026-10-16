"""Project saved review rows/columns without changing physical field bindings."""

import unicodedata
from collections import Counter
from typing import Any

from app.editing.contracts import RecordCell, TargetSearch
from app.editing.errors import reject


def normalized(value: str) -> str:
    return unicodedata.normalize("NFKC", value).casefold().strip()


def record_cells(structure: dict[str, Any]) -> dict[str, dict[str, Any]]:
    sections = structure.get("recordSections", [])
    counts = Counter(section["title"] for section in sections)
    indexes: Counter[str] = Counter()
    result: dict[str, dict[str, Any]] = {}
    table_ids: set[str] = set()
    # The UI consumes each field only once, including standalone parents.
    parent_fields = {
        field_id
        for section in structure.get("parentSections", [])
        for field_id in section.get("fieldIds", [])
    }
    for section in sections:
        table_id = section["id"]
        if table_id in table_ids:
            raise reject("FIELD_BINDING_CHANGED", "表の識別子が重複しています。")
        table_ids.add(table_id)
        indexes[section["title"]] += 1
        title = section["title"]
        if counts[title] > 1:
            title = f"{title} {indexes[title]}"
        columns = section.get("columns", [])
        labels = Counter(normalized(column["label"]) for column in columns)
        if len({column["id"] for column in columns}) != len(columns):
            raise reject("FIELD_BINDING_CHANGED", "表の列の識別子が重複しています。")
        rows = section.get("rows", [])
        if len({row["id"] for row in rows}) != len(rows):
            raise reject("FIELD_BINDING_CHANGED", "表の行の識別子が重複しています。")
        for row_number, row in enumerate(rows, 1):
            entries = row.get("cells", [])
            if len({cell["columnId"] for cell in entries}) != len(entries):
                raise reject("FIELD_BINDING_CHANGED", "同じ行と列に複数の欄があります。")
            for column_number, column in enumerate(columns, 1):
                cell = next((c for c in entries if c["columnId"] == column["id"]), None)
                field_id = cell.get("fieldId") if cell else None
                if not field_id:
                    continue
                if field_id in result or field_id in parent_fields:
                    raise reject(
                        "FIELD_BINDING_CHANGED", "欄の表・行・列への所属が重複しています。"
                    )
                result[field_id] = RecordCell(
                    table_id=table_id,
                    table_name=title,
                    table_title=section["title"],
                    row_id=row["id"],
                    row_number=row_number,
                    column_id=column["id"],
                    column_number=column_number,
                    column_name=column["label"],
                    column_label_ambiguous=labels[normalized(column["label"])] > 1,
                ).model_dump(by_alias=True)
    return result


def matches_location(detail: dict[str, Any], query: TargetSearch) -> bool:
    cell = detail.get("recordCell")
    if query.table_id and (not cell or cell["tableId"] != query.table_id):
        return False
    if query.table_name and (
        not cell
        or normalized(query.table_name)
        not in {
            normalized(cell["tableName"]),
            normalized(cell["tableTitle"]),
        }
    ):
        return False
    if query.column is not None and (not cell or cell["columnNumber"] != query.column):
        return False
    if query.row is not None:
        return query.row == (cell["rowNumber"] if cell else detail.get("order"))
    return True


def cell_label(target: dict[str, Any]) -> str:
    cell = target.get("recordCell")
    if cell and cell.get("columnLabelAmbiguous"):
        return (
            f"{cell['tableName']}の{cell['rowNumber']}行目の"
            f"{cell['columnName']} ({cell['columnNumber']}列目)"
        )
    return (
        f"{cell['tableName']}の{cell['rowNumber']}行目の{cell['columnName']}"
        if cell
        else target["name"]
    )
