from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.errors import to_camel
from app.domain.text_encoding import repair_utf8_mojibake


class LayoutModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")


class LayoutMarkOption(LayoutModel):
    id: str = Field(min_length=1, max_length=100)
    label: str = Field(min_length=1, max_length=20)
    value: str = Field(min_length=1, max_length=100)
    x: int = Field(ge=0)
    y: int = Field(ge=0)
    width: int = Field(gt=0)
    height: int = Field(gt=0)

    @field_validator("label", "value", mode="before")
    @classmethod
    def repair_text_encoding(cls, value: object) -> object:
        return repair_utf8_mojibake(value) if isinstance(value, str) else value


class LayoutMarkSet(LayoutModel):
    id: str = Field(min_length=1, max_length=100)
    target_path: str = Field(min_length=1, max_length=200)
    selection_mode: Literal["single", "multiple"] = "multiple"
    options: list[LayoutMarkOption] = Field(min_length=1, max_length=20)


class LayoutCell(LayoutModel):
    id: str = Field(min_length=1, max_length=100)
    role: Literal["label", "input", "mark", "ignored"]
    x: int = Field(ge=0)
    y: int = Field(ge=0)
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    confidence: float = Field(ge=0, le=1)
    text: str = Field(default="", max_length=300)
    printed_label_text: str = Field(default="", max_length=300)
    text_confidence: float | None = Field(default=None, ge=0, le=1)
    source: Literal["detected", "manual"] = "detected"
    mark_sets: list[LayoutMarkSet] = Field(default_factory=list, max_length=10)

    @field_validator("text", "printed_label_text", mode="before")
    @classmethod
    def repair_text_encoding(cls, value: object) -> object:
        return repair_utf8_mojibake(value) if isinstance(value, str) else value


class LayoutRelation(LayoutModel):
    id: str = Field(min_length=1, max_length=120)
    input_cell_id: str = Field(min_length=1, max_length=100)
    # A field can be assigned directly when its printed label shares the same
    # rectangle, or when the form conveys the meaning without a label cell.
    label_cell_ids: list[str] = Field(default_factory=list, max_length=30)
    axis: Literal["horizontal", "vertical"]
    order: int = Field(ge=1)
    target_path: str = Field(min_length=1, max_length=200)


class LayoutRecordSection(LayoutModel):
    id: str = Field(min_length=1, max_length=120)
    title: str = Field(min_length=1, max_length=200)
    collection_path: Literal["saleCalves", "calvings"]
    record_type: Literal["saleCalf", "calving"]
    parent_label_cell_id: str | None = Field(default=None, max_length=100)
    source: Literal["manual", "inferred"] = "manual"

    @field_validator("title", mode="before")
    @classmethod
    def repair_text_encoding(cls, value: object) -> object:
        return repair_utf8_mojibake(value) if isinstance(value, str) else value


class LayoutRecordGroup(LayoutModel):
    id: str = Field(min_length=1, max_length=120)
    section_id: str = Field(min_length=1, max_length=120)
    order: int = Field(ge=1)
    x: int = Field(ge=0)
    y: int = Field(ge=0)
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    input_cell_ids: list[str] = Field(min_length=1, max_length=100)
    record_key_field: str = Field(default="parity", min_length=1, max_length=100)
    record_key_value: str | None = Field(default=None, max_length=100)
    source: Literal["manual", "inferred"] = "manual"


class LayoutDefinition(LayoutModel):
    layout_schema_version: int = Field(default=3, ge=3)
    canonical_width: int = Field(gt=0)
    canonical_height: int = Field(gt=0)
    detector: str = Field(default="opencv+paddleocr", max_length=100)
    ocr_available: bool = False
    cells: list[LayoutCell] = Field(default_factory=list, max_length=1000)
    relations: list[LayoutRelation] = Field(default_factory=list, max_length=1000)
    record_sections: list[LayoutRecordSection] = Field(default_factory=list, max_length=100)
    record_groups: list[LayoutRecordGroup] = Field(default_factory=list, max_length=300)
    record_structure_verified: bool = False
    allow_cross_axis_labels: bool = False
    warnings: list[str] = Field(default_factory=list, max_length=30)

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_array_relations(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        raw: dict[str, Any] = dict(value)
        raw.setdefault("layoutSchemaVersion", raw.get("layout_schema_version", 3))
        sections = raw.get("recordSections", raw.get("record_sections"))
        groups = raw.get("recordGroups", raw.get("record_groups"))
        if sections or groups:
            return raw

        relations = raw.get("relations")
        cells = raw.get("cells")
        if not isinstance(relations, list) or not isinstance(cells, list):
            return raw
        normalized_cells = [
            item.model_dump(mode="json", by_alias=True) if isinstance(item, LayoutCell) else item
            for item in cells
        ]
        cell_by_id = {
            item.get("id"): item
            for item in normalized_cells
            if isinstance(item, dict) and isinstance(item.get("id"), str)
        }
        grouped: dict[tuple[str, int], list[str]] = {}
        label_ids_by_collection: dict[str, list[str]] = {}
        pattern = re.compile(r"^(saleCalves|calvings)\.(\d+)\.[^.]+$")
        for untyped_relation in relations:
            relation = (
                untyped_relation.model_dump(mode="json", by_alias=True)
                if isinstance(untyped_relation, LayoutRelation)
                else untyped_relation
            )
            if not isinstance(relation, dict):
                continue
            target_path = relation.get("targetPath", relation.get("target_path"))
            input_id = relation.get("inputCellId", relation.get("input_cell_id"))
            if not isinstance(target_path, str) or not isinstance(input_id, str):
                continue
            match = pattern.fullmatch(target_path)
            if match is None:
                continue
            collection, index_text = match.groups()
            key = (collection, int(index_text))
            if input_id not in grouped.setdefault(key, []):
                grouped[key].append(input_id)
            raw_label_ids = relation.get("labelCellIds", relation.get("label_cell_ids", []))
            if isinstance(raw_label_ids, list):
                for label_id in raw_label_ids:
                    if isinstance(
                        label_id, str
                    ) and label_id not in label_ids_by_collection.setdefault(collection, []):
                        label_ids_by_collection[collection].append(label_id)
        if not grouped:
            return raw

        section_rows: list[dict[str, object]] = []
        group_rows: list[dict[str, object]] = []
        collection_metadata = {
            "saleCalves": ("section_sale_calves", "売却子牛一覧", "saleCalf"),
            "calvings": ("section_calvings", "繁殖履歴", "calving"),
        }
        for collection in dict.fromkeys(key[0] for key in grouped):
            section_id, title, record_type = collection_metadata[collection]
            parent_label_id = next(
                (
                    label_id
                    for label_id in label_ids_by_collection.get(collection, [])
                    if isinstance((cell := cell_by_id.get(label_id)), dict)
                    and title.removesuffix("一覧") in str(cell.get("text", ""))
                ),
                None,
            )
            section_rows.append(
                {
                    "id": section_id,
                    "title": title,
                    "collectionPath": collection,
                    "recordType": record_type,
                    "parentLabelCellId": parent_label_id,
                    "source": "inferred",
                }
            )
            for (item_collection, index), input_ids in grouped.items():
                if item_collection != collection:
                    continue
                member_cells = [
                    cell_by_id[input_id]
                    for input_id in input_ids
                    if isinstance(cell_by_id.get(input_id), dict)
                ]
                if not member_cells:
                    continue
                x = min(int(cell.get("x", 0)) for cell in member_cells)
                y = min(int(cell.get("y", 0)) for cell in member_cells)
                right = max(
                    int(cell.get("x", 0)) + int(cell.get("width", 1)) for cell in member_cells
                )
                bottom = max(
                    int(cell.get("y", 0)) + int(cell.get("height", 1)) for cell in member_cells
                )
                group_rows.append(
                    {
                        "id": f"record_{collection}_{index + 1}",
                        "sectionId": section_id,
                        "order": index + 1,
                        "x": x,
                        "y": y,
                        "width": max(1, right - x),
                        "height": max(1, bottom - y),
                        "inputCellIds": input_ids,
                        "recordKeyField": "parity",
                        "recordKeyValue": str(index + 1),
                        "source": "inferred",
                    }
                )
        raw["recordSections"] = section_rows
        raw["recordGroups"] = group_rows
        raw.setdefault("recordStructureVerified", False)
        return raw

    @model_validator(mode="after")
    def validate_references_and_geometry(self) -> LayoutDefinition:
        cell_ids = [cell.id for cell in self.cells]
        if len(cell_ids) != len(set(cell_ids)):
            raise ValueError("Layout cell IDs must be unique")
        known = set(cell_ids)
        relation_ids = [relation.id for relation in self.relations]
        if len(relation_ids) != len(set(relation_ids)):
            raise ValueError("Layout relation IDs must be unique")
        for cell in self.cells:
            if cell.x + cell.width > self.canonical_width:
                raise ValueError(f"Cell {cell.id} exceeds canonical width")
            if cell.y + cell.height > self.canonical_height:
                raise ValueError(f"Cell {cell.id} exceeds canonical height")
        for relation in self.relations:
            if relation.input_cell_id not in known:
                raise ValueError(f"Relation {relation.id} references an unknown input cell")
            if any(label_id not in known for label_id in relation.label_cell_ids):
                raise ValueError(f"Relation {relation.id} references an unknown label cell")
        paths_by_input: dict[str, set[str]] = {}
        for relation in self.relations:
            paths_by_input.setdefault(relation.input_cell_id, set()).add(relation.target_path)
        if any(len(paths) > 1 for paths in paths_by_input.values()):
            raise ValueError("All relations for one input cell must use the same target path")
        section_ids = [section.id for section in self.record_sections]
        if len(section_ids) != len(set(section_ids)):
            raise ValueError("Record section IDs must be unique")
        sections = {section.id: section for section in self.record_sections}
        for section in self.record_sections:
            if (
                section.parent_label_cell_id is not None
                and section.parent_label_cell_id not in known
            ):
                raise ValueError(
                    f"Record section {section.id} references an unknown parent label cell"
                )
            if (
                section.parent_label_cell_id is not None
                and next(
                    cell for cell in self.cells if cell.id == section.parent_label_cell_id
                ).role
                != "label"
            ):
                raise ValueError(f"Record section {section.id} parent must be a label cell")
            expected_type = "saleCalf" if section.collection_path == "saleCalves" else "calving"
            if section.record_type != expected_type:
                raise ValueError(f"Record section {section.id} has an incompatible record type")
        group_ids = [group.id for group in self.record_groups]
        if len(group_ids) != len(set(group_ids)):
            raise ValueError("Record group IDs must be unique")
        claimed_inputs: set[str] = set()
        orders_by_section: dict[str, set[int]] = {}
        relations_by_input: dict[str, list[LayoutRelation]] = {}
        for relation in self.relations:
            relations_by_input.setdefault(relation.input_cell_id, []).append(relation)
        for group in self.record_groups:
            record_section = sections.get(group.section_id)
            if record_section is None:
                raise ValueError(f"Record group {group.id} references an unknown section")
            if group.x + group.width > self.canonical_width:
                raise ValueError(f"Record group {group.id} exceeds canonical width")
            if group.y + group.height > self.canonical_height:
                raise ValueError(f"Record group {group.id} exceeds canonical height")
            if len(group.input_cell_ids) != len(set(group.input_cell_ids)):
                raise ValueError(f"Record group {group.id} contains duplicate input cells")
            for input_id in group.input_cell_ids:
                if input_id not in known:
                    raise ValueError(f"Record group {group.id} references an unknown input cell")
                input_cell = next(cell for cell in self.cells if cell.id == input_id)
                if input_cell.role not in {"input", "mark"}:
                    raise ValueError(
                        f"Record group {group.id} may contain only input or mark cells"
                    )
                if input_id in claimed_inputs:
                    raise ValueError(f"Input cell {input_id} belongs to more than one record group")
                claimed_inputs.add(input_id)
                for relation in relations_by_input.get(input_id, []):
                    match = re.fullmatch(
                        r"^(saleCalves|calvings)\.(\d+)\.([^.]+)$",
                        relation.target_path,
                    )
                    if match is None:
                        raise ValueError(
                            f"Record input {input_id} must use a collection target path"
                        )
                    collection, index_text, _field = match.groups()
                    if (
                        collection != record_section.collection_path
                        or int(index_text) != group.order - 1
                    ):
                        raise ValueError(
                            f"Record group {group.id} does not match {relation.target_path}"
                        )
            orders = orders_by_section.setdefault(group.section_id, set())
            if group.order in orders:
                raise ValueError(f"Record order {group.order} is duplicated in {group.section_id}")
            orders.add(group.order)
        return self
