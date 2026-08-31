from __future__ import annotations

import re
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.errors import to_camel
from app.domain.text_encoding import repair_utf8_mojibake


class LayoutModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")


class LayoutMarkOption(LayoutModel):
    id: str = Field(min_length=1, max_length=100)
    label: str = Field(min_length=1, max_length=100)
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
    target_path: str = Field(default="", max_length=200)
    recognition_mode: Literal["automatic", "manual"] = "automatic"
    selection_mode: Literal["single", "multiple"] = "multiple"
    options: list[LayoutMarkOption] = Field(default_factory=list, max_length=50)

    @model_validator(mode="after")
    def validate_unique_options(self) -> Self:
        for attribute in ("id", "label", "value"):
            values = [getattr(option, attribute) for option in self.options]
            if len(values) != len(set(values)):
                raise ValueError(f"Mark options have duplicate {attribute}s")
        return self


class LayoutCompositeSubfield(LayoutModel):
    id: str = Field(min_length=1, max_length=100)
    semantic_type: Literal["address_prefecture", "address_municipality"]
    label: str = Field(min_length=1, max_length=100)
    x_ratio: float = Field(ge=0, le=1)
    y_ratio: float = Field(ge=0, le=1)
    width_ratio: float = Field(gt=0, le=1)
    height_ratio: float = Field(gt=0, le=1)


class LayoutCell(LayoutModel):
    id: str = Field(min_length=1, max_length=100)
    role: Literal[
        "label",
        "parent_label",
        "input",
        "mark",
        "record_key_cell",
        "column_key_cell",
        "ignored",
    ]
    x: int = Field(ge=0)
    y: int = Field(ge=0)
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    confidence: float = Field(ge=0, le=1)
    text: str = Field(default="", max_length=300)
    printed_label_text: str = Field(default="", max_length=300)
    text_confidence: float | None = Field(default=None, ge=0, le=1)
    text_direction: Literal["horizontal", "vertical"] = "horizontal"
    text_alignment: Literal["left", "center", "right"] = "left"
    source: Literal["detected", "manual"] = "detected"
    mark_sets: list[LayoutMarkSet] = Field(default_factory=list, max_length=10)
    composite_subfields: list[LayoutCompositeSubfield] = Field(
        default_factory=list,
        max_length=10,
    )

    @field_validator("text", "printed_label_text", mode="before")
    @classmethod
    def repair_text_encoding(cls, value: object) -> object:
        return repair_utf8_mojibake(value) if isinstance(value, str) else value


class LayoutCellMergePart(LayoutModel):
    cell: LayoutCell
    part_type: Literal["input", "literal", "ignored"]
    literal: str = Field(default="", max_length=300)

    @field_validator("literal", mode="before")
    @classmethod
    def repair_text_encoding(cls, value: object) -> object:
        return repair_utf8_mojibake(value) if isinstance(value, str) else value


class LayoutCellMerge(LayoutModel):
    id: str = Field(min_length=1, max_length=100)
    parent_cell_id: str = Field(min_length=1, max_length=100)
    direction: Literal["horizontal", "vertical"]
    parts: list[LayoutCellMergePart] = Field(min_length=2, max_length=100)


class LayoutRelation(LayoutModel):
    id: str = Field(min_length=1, max_length=120)
    input_cell_id: str = Field(min_length=1, max_length=100)
    # A field can be assigned directly when its printed label shares the same
    # rectangle, or when the form conveys the meaning without a label cell.
    label_cell_ids: list[str] = Field(default_factory=list, max_length=30)
    parent_label_cell_id: str | None = Field(default=None, max_length=100)
    axis: Literal["horizontal", "vertical"]
    order: int = Field(ge=1)
    target_path: str = Field(min_length=1, max_length=200)


class LayoutLabelPoolEntry(LayoutModel):
    id: str = Field(min_length=1, max_length=120)
    label: str = Field(min_length=1, max_length=100)
    target_path: str = Field(min_length=1, max_length=200)
    section: str = Field(min_length=1, max_length=100)
    aliases: list[str] = Field(default_factory=list, max_length=30)
    source: Literal["manual", "ocr"] = "manual"

    @field_validator("label", "section", mode="before")
    @classmethod
    def repair_text_encoding(cls, value: object) -> object:
        return repair_utf8_mojibake(value) if isinstance(value, str) else value

    @field_validator("aliases")
    @classmethod
    def validate_aliases(cls, value: list[str]) -> list[str]:
        normalized = [repair_utf8_mojibake(alias).strip() for alias in value if alias.strip()]
        if any(len(alias) > 100 for alias in normalized):
            raise ValueError("Label pool aliases must not exceed 100 characters")
        return list(dict.fromkeys(normalized))


class LayoutRecordSection(LayoutModel):
    id: str = Field(min_length=1, max_length=120)
    title: str = Field(min_length=1, max_length=200)
    collection_path: str = Field(min_length=1, max_length=120)
    record_type: Literal["saleCalf", "calving", "generic"]
    record_label_cell_ids: list[str] = Field(default_factory=list, max_length=30)
    parent_label_cell_id: str | None = Field(default=None, max_length=100)
    row_key_strategy: Literal["printed", "position"] = "position"
    row_key_cell_ids: list[str] = Field(default_factory=list, max_length=300)
    include_empty_rows: bool = False
    source: Literal["manual", "inferred"] = "manual"

    @field_validator("collection_path")
    @classmethod
    def validate_collection_path(cls, value: str) -> str:
        if value in {"saleCalves", "calvings"}:
            return value
        if not re.fullmatch(r"genericRecords\.[A-Za-z][A-Za-z0-9_]{0,99}", value):
            raise ValueError("Generic record collection paths must use genericRecords.<key>")
        return value

    @model_validator(mode="before")
    @classmethod
    def migrate_single_record_label(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        raw: dict[str, Any] = dict(value)
        plural = raw.get("recordLabelCellIds", raw.get("record_label_cell_ids"))
        singular = raw.pop("recordLabelCellId", raw.pop("record_label_cell_id", None))
        if plural is None:
            raw["recordLabelCellIds"] = [singular] if isinstance(singular, str) else []
        return raw

    @field_validator("record_label_cell_ids")
    @classmethod
    def validate_unique_record_labels(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("Record label cell IDs must be unique")
        return value

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
    record_key_cell_id: str | None = Field(default=None, max_length=100)
    source: Literal["manual", "inferred"] = "manual"


class LayoutDefinition(LayoutModel):
    layout_schema_version: int = Field(default=3, ge=3)
    canonical_width: int = Field(gt=0)
    canonical_height: int = Field(gt=0)
    detector: str = Field(default="opencv+paddleocr", max_length=100)
    ocr_available: bool = False
    cells: list[LayoutCell] = Field(default_factory=list, max_length=1000)
    cell_merges: list[LayoutCellMerge] = Field(default_factory=list, max_length=300)
    relations: list[LayoutRelation] = Field(default_factory=list, max_length=1000)
    label_pool: list[LayoutLabelPoolEntry] = Field(default_factory=list, max_length=500)
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
                    "recordLabelCellIds": [
                        label_id
                        for label_id in label_ids_by_collection.get(collection, [])
                        if label_id != parent_label_id
                    ],
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
        pool_ids = [entry.id for entry in self.label_pool]
        if len(pool_ids) != len(set(pool_ids)):
            raise ValueError("Label pool IDs must be unique")
        pool_paths = [entry.target_path for entry in self.label_pool]
        if len(pool_paths) != len(set(pool_paths)):
            raise ValueError("Label pool target paths must be unique")
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
            if any(label_id not in known for label_id in section.record_label_cell_ids):
                raise ValueError(
                    f"Record section {section.id} references an unknown record label cell"
                )
            if any(
                next(cell for cell in self.cells if cell.id == label_id).role != "label"
                for label_id in section.record_label_cell_ids
            ):
                raise ValueError(f"Record section {section.id} record anchors must be label cells")
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
                not in {"label", "parent_label"}
            ):
                raise ValueError(f"Record section {section.id} parent must be a label cell")
            if len(section.row_key_cell_ids) != len(set(section.row_key_cell_ids)):
                raise ValueError(f"Record section {section.id} row key cells must be unique")
            for row_key_cell_id in section.row_key_cell_ids:
                if row_key_cell_id not in known:
                    raise ValueError(
                        f"Record section {section.id} references an unknown row key cell"
                    )
                row_key_cell = next(
                    cell for cell in self.cells if cell.id == row_key_cell_id
                )
                if row_key_cell.role != "record_key_cell":
                    raise ValueError(
                        f"Record section {section.id} row keys must be row number cells"
                    )
            expected_type = (
                "saleCalf"
                if section.collection_path == "saleCalves"
                else "calving"
                if section.collection_path == "calvings"
                else "generic"
            )
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
            if group.record_key_cell_id is not None:
                if group.record_key_cell_id not in known:
                    raise ValueError(
                        f"Record group {group.id} references an unknown row key cell"
                    )
                key_cell = next(
                    cell for cell in self.cells if cell.id == group.record_key_cell_id
                )
                if key_cell.role != "record_key_cell":
                    raise ValueError(
                        f"Record group {group.id} row key must be a row number cell"
                    )
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
                    if record_section.record_type == "generic":
                        if not relation.target_path.startswith("customFields."):
                            raise ValueError(
                                f"Generic record input {input_id} must use a custom field target"
                            )
                        continue
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
