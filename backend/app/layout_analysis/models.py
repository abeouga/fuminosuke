from __future__ import annotations

import re
from typing import Any, Literal, Self, cast

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    GetJsonSchemaHandler,
    SerializerFunctionWrapHandler,
    field_validator,
    model_serializer,
    model_validator,
)
from pydantic.json_schema import JsonSchemaValue
from pydantic_core import core_schema

from app.core.errors import to_camel
from app.domain.polygon import PolygonPoint, validate_polygon
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
    recognition_mode: Literal["automatic", "manual", "symbol", "checklist"] = "automatic"
    selection_mode: Literal["single", "multiple"] = "multiple"
    options: list[LayoutMarkOption] = Field(default_factory=list, max_length=50)

    @model_validator(mode="after")
    def validate_unique_options(self) -> Self:
        if self.recognition_mode == "checklist":
            if self.selection_mode != "multiple":
                raise ValueError("Checklist recognition requires multiple selection")
            labels = [option.label for option in self.options]
            if any(not label.strip() or label != label.strip() or any(
                separator in label for separator in "・、,;\n\r"
            ) for label in labels):
                raise ValueError("Checklist labels must not contain selection separators")
        if self.recognition_mode == "symbol" and self.selection_mode != "single":
            raise ValueError("Symbol recognition requires single selection")
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
    field_label: str | None = Field(default=None, min_length=1, max_length=100)
    printed_label_text: str = Field(default="", max_length=300)
    # Authoring preset; saving freezes a local sequence number in each mapping.
    reading_mode: Literal["number"] | None = None
    text_confidence: float | None = Field(default=None, ge=0, le=1)
    text_direction: Literal["horizontal", "vertical"] = "horizontal"
    text_alignment: Literal["left", "center", "right"] = "left"
    text_vertical_alignment: Literal["top", "middle", "bottom"] | None = None
    source: Literal["detected", "manual"] = "detected"
    shape_type: Literal["rectangle", "polygon"] = "rectangle"
    vertices: list[PolygonPoint] = Field(default_factory=list, max_length=100)
    virtual: bool = False
    # Only virtual headings use page-local coordinates; physical cells stay canonical.
    page_range: str | None = Field(default=None, min_length=1, max_length=40)
    parent_label_cell_id: str | None = Field(default=None, max_length=100)
    mark_sets: list[LayoutMarkSet] = Field(default_factory=list, max_length=10)
    composite_subfields: list[LayoutCompositeSubfield] = Field(
        default_factory=list,
        max_length=10,
    )

    @classmethod
    def __get_pydantic_json_schema__(
        cls, schema: core_schema.CoreSchema, handler: GetJsonSchemaHandler,
    ) -> JsonSchemaValue:
        # Preserve the declared schema despite the optional-field wrap serializer.
        def declared_model(node: core_schema.CoreSchema) -> core_schema.CoreSchema:
            declared = dict(node)
            declared.pop("serialization", None)
            if node["type"] != "model" and isinstance(declared.get("schema"), dict):
                declared["schema"] = declared_model(declared["schema"])
            return cast(core_schema.CoreSchema, declared)

        return handler(declared_model(schema))

    @model_serializer(mode="wrap")
    def serialize_page_range(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        data: dict[str, Any] = handler(self)
        if self.page_range is None:
            data.pop("pageRange", None)
            data.pop("page_range", None)
        if self.reading_mode is None:
            data.pop("readingMode", None)
            data.pop("reading_mode", None)
        if self.field_label is None:
            data.pop("fieldLabel", None)
            data.pop("field_label", None)
        return data

    @model_validator(mode="after")
    def validate_shape(self) -> Self:
        if self.reading_mode is not None and self.role != "input":
            raise ValueError("Number reading requires an input cell")
        if (self.shape_type == "polygon") != bool(self.vertices):
            raise ValueError("Only polygon cells require vertices")
        validate_polygon(self.vertices, self.x, self.y, self.width, self.height)
        if self.virtual and (
            self.role != "parent_label" or self.source != "manual"
            or self.shape_type != "rectangle" or not self.text.strip()
        ):
            raise ValueError("Virtual cells must be named manual headings")
        if self.page_range is not None and (
            not self.virtual or self.mark_sets or self.composite_subfields
        ):
            raise ValueError("Page-local coordinates are only allowed for virtual headings")
        return self

    @field_validator("text", "printed_label_text", "field_label", mode="before")
    @classmethod
    def repair_text_encoding(cls, value: object) -> object:
        return repair_utf8_mojibake(value) if isinstance(value, str) else value

    @field_validator("field_label")
    @classmethod
    def validate_field_label(cls, value: str | None) -> str | None:
        if value is not None and (not value.strip() or value != value.strip()):
            raise ValueError("Field labels require a name without surrounding whitespace")
        return value


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
    display_name: str | None = Field(default=None, min_length=1, max_length=200)
    collection_path: str = Field(min_length=1, max_length=120)
    record_type: Literal["generic"]
    record_label_cell_ids: list[str] = Field(default_factory=list, max_length=30)
    parent_label_cell_id: str | None = Field(default=None, max_length=100)
    row_key_strategy: Literal["printed", "position"] = "position"
    row_key_cell_ids: list[str] = Field(default_factory=list, max_length=300)
    include_empty_rows: bool = False
    source: Literal["manual", "inferred"] = "manual"

    @field_validator("collection_path")
    @classmethod
    def validate_collection_path(cls, value: str) -> str:
        if not re.fullmatch(r"genericRecords\.[A-Za-z][A-Za-z0-9_]{0,99}", value):
            raise ValueError("Generic record collection paths must use genericRecords.<key>")
        return value


    @field_validator("record_label_cell_ids")
    @classmethod
    def validate_unique_record_labels(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("Record label cell IDs must be unique")
        return value

    @field_validator("title", "display_name", mode="before")
    @classmethod
    def repair_text_encoding(cls, value: object) -> object:
        return repair_utf8_mojibake(value) if isinstance(value, str) else value

    @property
    def resolved_display_name(self) -> str:
        return (self.display_name or "").strip() or self.title.strip() or "名称未設定の繰り返し表"


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


    @model_validator(mode="after")
    def validate_references_and_geometry(self) -> LayoutDefinition:
        cell_ids = [cell.id for cell in self.cells]
        if len(cell_ids) != len(set(cell_ids)):
            raise ValueError("Layout cell IDs must be unique")
        known = set(cell_ids)
        cells_by_id = {cell.id: cell for cell in self.cells}
        parents: dict[str, set[str]] = {}
        for cell in self.cells:
            if cell.parent_label_cell_id is None:
                continue
            parent = cells_by_id.get(cell.parent_label_cell_id)
            if parent is None or parent.role not in {"label", "parent_label"}:
                raise ValueError("Cell parent must reference a heading")
            if cell.role not in {"label", "parent_label", "input", "mark"}:
                raise ValueError("Only headings and fields may have a parent")
            parents.setdefault(cell.id, set()).add(parent.id)
        # Include legacy meaning edges when checking new hierarchy cycles.
        for relation in self.relations:
            if relation.parent_label_cell_id:
                for child_id in relation.label_cell_ids or [relation.input_cell_id]:
                    parents.setdefault(child_id, set()).add(relation.parent_label_cell_id)
        for section in self.record_sections:
            if section.parent_label_cell_id:
                for child_id in section.record_label_cell_ids:
                    parents.setdefault(child_id, set()).add(section.parent_label_cell_id)
        for cell in self.cells:
            if cell.parent_label_cell_id and len(parents[cell.id]) > 1:
                raise ValueError("A hierarchy node may have only one parent")
            visited = {cell.id}
            pending = list(parents.get(cell.id, set()))
            while pending:
                parent_id = pending.pop()
                if parent_id == cell.id:
                    raise ValueError("Heading hierarchy must not contain cycles")
                if parent_id not in visited:
                    visited.add(parent_id)
                    pending.extend(parents.get(parent_id, set()))
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
            if cell.page_range is not None:
                # The save service checks these against the version's saved page.
                continue
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
                    if not relation.target_path.startswith("fields."):
                        raise ValueError(f"Record input {input_id} must use a generic field target")
            orders = orders_by_section.setdefault(group.section_id, set())
            if group.order in orders:
                raise ValueError(f"Record order {group.order} is duplicated in {group.section_id}")
            orders.add(group.order)
        return self
