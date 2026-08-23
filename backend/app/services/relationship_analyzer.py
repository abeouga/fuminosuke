from __future__ import annotations

import json
import hashlib
import logging
import re
from collections import defaultdict
from math import ceil
from statistics import mean

from openai import AsyncOpenAI
from pydantic import BaseModel, ConfigDict, Field

from app.core.config import OcrSettings
from app.domain.template_definition import (
    CellGeometry,
    InferencePhase,
    TemplateBox,
    TemplateCell,
    TemplateField,
    TemplateRecord,
    TemplateSection,
)
from app.layout_analysis.models import LayoutDefinition, LayoutRelation

logger = logging.getLogger(__name__)


class _ProposalModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProposedField(_ProposalModel):
    id: str
    key: str
    name: str
    label_cell_id: str | None = None
    value_cell_id: str
    value_type: str = "text"
    confidence: float = Field(ge=0, le=1)
    reason_codes: list[str] = Field(default_factory=list)


class ProposedRecord(_ProposalModel):
    id: str
    name: str
    field_ids: list[str]
    confidence: float = Field(ge=0, le=1)
    reason_codes: list[str] = Field(default_factory=list)


class ProposedSection(_ProposalModel):
    id: str
    key: str
    name: str
    parent_label_cell_id: str | None = None
    record_ids: list[str]
    confidence: float = Field(ge=0, le=1)
    reason_codes: list[str] = Field(default_factory=list)


class RelationshipProposal(_ProposalModel):
    fields: list[ProposedField]
    records: list[ProposedRecord]
    sections: list[ProposedSection]
    unassigned_cell_ids: list[str] = Field(default_factory=list)


class RelationshipDraft:
    def __init__(
        self,
        *,
        standalone_fields: list[TemplateField],
        sections: list[TemplateSection],
        unassigned_cell_ids: list[str],
        phases: list[InferencePhase],
        warnings: list[str],
    ) -> None:
        self.standalone_fields = standalone_fields
        self.sections = sections
        self.unassigned_cell_ids = unassigned_cell_ids
        self.phases = phases
        self.warnings = warnings


def infer_relationships(
    cells: list[TemplateCell],
    geometry: list[CellGeometry],
    legacy_layout: LayoutDefinition | None = None,
) -> RelationshipDraft:
    if legacy_layout is not None and legacy_layout.relations:
        return _from_legacy_layout(cells, legacy_layout)
    return _from_geometry(cells, geometry)


async def infer_relationships_with_openai(
    cells: list[TemplateCell],
    geometry: list[CellGeometry],
    settings: OcrSettings,
    legacy_layout: LayoutDefinition | None = None,
) -> RelationshipDraft:
    fallback = infer_relationships(cells, geometry, legacy_layout)
    if settings.provider != "openai" or settings.openai_api_key is None:
        return fallback
    model = (settings.primary_model or "").strip()
    key = settings.openai_api_key.get_secret_value().strip()
    if not model or not key:
        return fallback
    client = AsyncOpenAI(api_key=key, timeout=float(settings.timeout_seconds), max_retries=0)
    payload = {
        "cells": [cell.model_dump(mode="json", by_alias=True) for cell in cells],
        "geometry": [item.model_dump(mode="json", by_alias=True) for item in geometry],
    }
    try:
        response = await client.responses.parse(
            model=model,
            instructions=(
                "帳票セルの関係を3段階で推定してください。"
                "第1段階はLABELとVALUEをFieldとして結合し、第2段階は反復するFieldをRecordへまとめ、"
                "第3段階はRecordをPARENT_LABEL配下のSectionへまとめます。"
                "入力に存在するIDだけを参照し、VALUEを複数Fieldへ割り当てないでください。"
                "keyは英字で始まる英数字とアンダースコアのみとし、推定理由をreason_codesへ記録してください。"
                "確信がないセルはunassigned_cell_idsへ残してください。"
            ),
            input=json.dumps(payload, ensure_ascii=False),
            text_format=RelationshipProposal,
            max_output_tokens=settings.max_output_tokens,
            reasoning={"effort": settings.reasoning_effort},
            store=False,
        )
        proposal = response.output_parsed
        if not isinstance(proposal, RelationshipProposal):
            raise ValueError("structured relationship proposal was empty")
        candidate = _from_proposal(cells, proposal)
        quality_issue = _relationship_quality_issue(candidate, fallback)
        if quality_issue is not None:
            fallback.warnings.append(
                f"AI_RELATIONSHIP_INFERENCE_{quality_issue}_FALLBACK"
            )
            return fallback
        return candidate
    except Exception as exc:  # The deterministic path keeps template creation usable.
        logger.warning("Relationship inference fell back to geometry: %s", type(exc).__name__)
        fallback.warnings.append("AI_RELATIONSHIP_INFERENCE_FAILED_GEOMETRY_FALLBACK")
        return fallback


def _relationship_quality_issue(
    candidate: RelationshipDraft,
    fallback: RelationshipDraft,
) -> str | None:
    """Reject syntactically valid AI drafts that discard most known structure."""

    candidate_field_count = _field_count(candidate)
    fallback_field_count = _field_count(fallback)
    if fallback_field_count >= 4 and candidate_field_count < ceil(
        fallback_field_count * 0.75
    ):
        return "LOW_FIELD_COVERAGE"

    candidate_record_count = _record_count(candidate)
    fallback_record_count = _record_count(fallback)
    if fallback_record_count >= 4 and candidate_record_count < ceil(
        fallback_record_count * 0.5
    ):
        return "LOW_RECORD_COVERAGE"

    if fallback.sections and not candidate.sections and fallback_record_count:
        return "MISSING_SECTIONS"
    return None


def _field_count(draft: RelationshipDraft) -> int:
    return len(draft.standalone_fields) + sum(
        len(record.fields)
        for section in draft.sections
        for record in section.records
    )


def _record_count(draft: RelationshipDraft) -> int:
    return sum(len(section.records) for section in draft.sections)


def _from_proposal(cells: list[TemplateCell], proposal: RelationshipProposal) -> RelationshipDraft:
    cell_by_id = {cell.id: cell for cell in cells}
    fields: list[TemplateField] = []
    used_values: set[str] = set()
    for index, field_item in enumerate(proposal.fields, start=1):
        value = cell_by_id.get(field_item.value_cell_id)
        label = cell_by_id.get(field_item.label_cell_id or "")
        if value is None or value.type != "VALUE" or value.id in used_values:
            continue
        label_id = label.id if label is not None and label.type == "LABEL" else None
        value_type = (
            field_item.value_type
            if field_item.value_type
            in {"text", "integer", "decimal", "date", "boolean", "enum"}
            else "text"
        )
        key = (
            field_item.key
            if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,99}", field_item.key)
            else f"field_{index:03d}"
        )
        fields.append(
            TemplateField(
                id=field_item.id or f"field_{index:03d}",
                key=key,
                name=field_item.name.strip() or (label.text if label else f"項目 {index}"),
                label_cell_id=label_id,
                value_cell_id=value.id,
                value_type=value_type,
                confidence=field_item.confidence,
                reason_codes=field_item.reason_codes or ["AI_FIELD_BINDING"],
            )
        )
        used_values.add(value.id)
    field_by_id = {field.id: field for field in fields}
    records: list[TemplateRecord] = []
    used_fields: set[str] = set()
    for order, record_item in enumerate(proposal.records, start=1):
        record_fields = [
            field_by_id[field_id]
            for field_id in record_item.field_ids
            if field_id in field_by_id and field_id not in used_fields
        ]
        if not record_fields:
            continue
        used_fields.update(field.id for field in record_fields)
        records.append(
            TemplateRecord(
                id=record_item.id or f"record_{order:03d}",
                name=record_item.name.strip() or f"明細 {order}",
                order=order,
                bbox=_union_box([cell_by_id[field.value_cell_id].bbox for field in record_fields]),
                fields=record_fields,
                record_key_value=str(order),
                confidence=record_item.confidence,
                reason_codes=record_item.reason_codes or ["AI_RECORD_GROUPING"],
            )
        )
    record_by_id = {record.id: record for record in records}
    sections: list[TemplateSection] = []
    used_records: set[str] = set()
    for index, section_item in enumerate(proposal.sections, start=1):
        section_records = [
            record_by_id[record_id]
            for record_id in section_item.record_ids
            if record_id in record_by_id and record_id not in used_records
        ]
        if not section_records:
            continue
        used_records.update(record.id for record in section_records)
        parent = cell_by_id.get(section_item.parent_label_cell_id or "")
        parent_id = parent.id if parent is not None and parent.type == "PARENT_LABEL" else None
        boxes = [record.bbox for record in section_records]
        if parent is not None and parent_id is not None:
            boxes.append(parent.bbox)
        key = (
            section_item.key
            if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,99}", section_item.key)
            else f"section_{index:03d}"
        )
        sections.append(
            TemplateSection(
                id=section_item.id or f"section_{index:03d}",
                key=key,
                name=section_item.name.strip()
                or (parent.text if parent else f"明細 {index}"),
                parent_label_cell_id=parent_id,
                bbox=_union_box(boxes),
                records=section_records,
                confidence=section_item.confidence,
                reason_codes=section_item.reason_codes or ["AI_SECTION_ASSIGNMENT"],
            )
        )
    # Records omitted from sections remain standalone fields instead of becoming
    # an invalid hidden hierarchy.
    section_field_ids = {
        field.id for section in sections for record in section.records for field in record.fields
    }
    standalone = [field for field in fields if field.id not in section_field_ids]
    used_labels = {field.label_cell_id for field in fields if field.label_cell_id}
    used_parents = {
        section.parent_label_cell_id for section in sections if section.parent_label_cell_id
    }
    inferred_unassigned = {
        cell.id
        for cell in cells
        if (cell.type == "VALUE" and cell.id not in used_values)
        or (cell.type == "LABEL" and cell.id not in used_labels)
        or (cell.type == "PARENT_LABEL" and cell.id not in used_parents)
    }
    inferred_unassigned.update(
        cell_id for cell_id in proposal.unassigned_cell_ids if cell_id in cell_by_id
    )
    field_confidence = mean(field.confidence for field in fields) if fields else 0
    record_confidence = mean(record.confidence for record in records) if records else 0.5
    section_confidence = mean(section.confidence for section in sections) if sections else 0.5
    return RelationshipDraft(
        standalone_fields=standalone,
        sections=sections,
        unassigned_cell_ids=sorted(inferred_unassigned),
        phases=[
            InferencePhase(
                phase="FIELD_BINDING",
                confidence=field_confidence,
                reason_codes=["OPENAI_STRUCTURED_OUTPUT"],
            ),
            InferencePhase(
                phase="RECORD_GROUPING",
                confidence=record_confidence,
                reason_codes=["OPENAI_STRUCTURED_OUTPUT"],
            ),
            InferencePhase(
                phase="SECTION_ASSIGNMENT",
                confidence=section_confidence,
                reason_codes=["OPENAI_STRUCTURED_OUTPUT"],
            ),
        ],
        warnings=[],
    )


def _from_geometry(cells: list[TemplateCell], geometry: list[CellGeometry]) -> RelationshipDraft:
    cell_by_id = {cell.id: cell for cell in cells}
    geo_by_id = {item.cell_id: item for item in geometry}
    label_ids = {cell.id for cell in cells if cell.type == "LABEL"}
    fields: list[TemplateField] = []
    used_labels: set[str] = set()
    used_values: set[str] = set()
    for index, value in enumerate(cell for cell in cells if cell.type == "VALUE"):
        geo = geo_by_id[value.id]
        same_row_left = [cell_id for cell_id in geo.left if cell_id in label_ids]
        same_column_above = [cell_id for cell_id in geo.above if cell_id in label_ids]
        label_id = (
            same_row_left[0]
            if same_row_left
            else (same_column_above[0] if same_column_above else None)
        )
        if label_id is None:
            continue
        label = cell_by_id[label_id]
        horizontal = bool(same_row_left)
        distance = geo.horizontal_distance if horizontal else geo.vertical_distance
        size = value.bbox.width if horizontal else value.bbox.height
        distance_score = max(0.35, 1 - float(distance or 0) / max(size * 5, 1))
        confidence = round(
            min(0.98, 0.55 + distance_score * 0.25 + (label.text_confidence or 0.5) * 0.2),
            3,
        )
        fields.append(
            TemplateField(
                id=f"field_{index + 1:03d}",
                key=f"field_{index + 1:03d}",
                name=label.text.strip() or f"項目 {index + 1}",
                label_cell_id=label_id,
                value_cell_id=value.id,
                confidence=confidence,
                reason_codes=[
                    "NEAREST_LABEL_LEFT" if horizontal else "NEAREST_LABEL_ABOVE",
                    "GEOMETRY_INFERENCE",
                ],
            )
        )
        used_labels.add(label_id)
        used_values.add(value.id)

    # Repeated VALUE columns across multiple rows are grouped as records only
    # when at least two rows contain two or more inferred fields.
    fields_by_row: dict[int, list[TemplateField]] = defaultdict(list)
    for field in fields:
        fields_by_row[geo_by_id[field.value_cell_id].row_cluster].append(field)
    record_rows = [
        (row, row_fields)
        for row, row_fields in sorted(fields_by_row.items())
        if len(row_fields) >= 2
        and sum(geo_by_id[field.value_cell_id].record_pattern_candidate for field in row_fields)
        >= 2
    ]
    records: list[TemplateRecord] = []
    record_field_ids: set[str] = set()
    if len(record_rows) >= 2:
        for order, (_row, row_fields) in enumerate(record_rows, start=1):
            record_field_ids.update(field.id for field in row_fields)
            records.append(
                TemplateRecord(
                    id=f"record_{order:03d}",
                    name=f"明細 {order}",
                    order=order,
                    bbox=_union_box([cell_by_id[field.value_cell_id].bbox for field in row_fields]),
                    fields=row_fields,
                    record_key_value=str(order),
                    confidence=round(mean(field.confidence for field in row_fields) * 0.9, 3),
                    reason_codes=["REPEATED_ROW_PATTERN"],
                )
            )

    sections: list[TemplateSection] = []
    if records:
        parent_candidates = [cell for cell in cells if cell.type == "PARENT_LABEL"]
        parent = _nearest_parent(parent_candidates, records[0].bbox)
        section_boxes = [record.bbox for record in records]
        if parent is not None:
            section_boxes.append(parent.bbox)
        sections.append(
            TemplateSection(
                id="section_001",
                key="section_001",
                name=(parent.text.strip() if parent else "明細"),
                parent_label_cell_id=parent.id if parent else None,
                bbox=_union_box(section_boxes),
                records=records,
                confidence=round(
                    mean(record.confidence for record in records) * (1 if parent else 0.85),
                    3,
                ),
                reason_codes=[
                    "PARENT_LABEL_ABOVE" if parent else "SECTION_WITHOUT_PARENT_LABEL",
                    "RECORD_PATTERN_GROUPING",
                ],
            )
        )

    standalone = [field for field in fields if field.id not in record_field_ids]
    unassigned = [
        cell.id
        for cell in cells
        if (cell.type == "VALUE" and cell.id not in used_values)
        or (cell.type == "LABEL" and cell.id not in used_labels)
        or (
            cell.type == "PARENT_LABEL"
            and not any(section.parent_label_cell_id == cell.id for section in sections)
        )
    ]
    field_confidence = mean(field.confidence for field in fields) if fields else 0
    record_confidence = mean(record.confidence for record in records) if records else 0.5
    section_confidence = mean(section.confidence for section in sections) if sections else 0.5
    return RelationshipDraft(
        standalone_fields=standalone,
        sections=sections,
        unassigned_cell_ids=unassigned,
        phases=[
            InferencePhase(
                phase="FIELD_BINDING",
                confidence=round(field_confidence, 3),
                reason_codes=["DETERMINISTIC_GEOMETRY"],
            ),
            InferencePhase(
                phase="RECORD_GROUPING",
                confidence=round(record_confidence, 3),
                reason_codes=["REPEATED_ROW_ANALYSIS"],
            ),
            InferencePhase(
                phase="SECTION_ASSIGNMENT",
                confidence=round(section_confidence, 3),
                reason_codes=["PARENT_LABEL_PROXIMITY"],
            ),
        ],
        warnings=["AI_PROVIDER_NOT_USED_DETERMINISTIC_GEOMETRY_DRAFT"],
    )


def _from_legacy_layout(cells: list[TemplateCell], layout: LayoutDefinition) -> RelationshipDraft:
    cell_by_id = {cell.id: cell for cell in cells}
    layout_cell_by_id = {cell.id: cell for cell in layout.cells}
    grouped_relations: dict[tuple[str, str], list[LayoutRelation]] = defaultdict(list)
    for relation in layout.relations:
        layout_cell = layout_cell_by_id.get(relation.input_cell_id)
        if layout_cell is not None and layout_cell.role == "mark":
            continue
        grouped_relations[(relation.input_cell_id, relation.target_path)].append(relation)
    fields: list[TemplateField] = []
    for index, ((input_id, target_path), relations) in enumerate(
        grouped_relations.items(), start=1
    ):
        label_id = next(
            (
                label_id
                for relation in relations
                for label_id in relation.label_cell_ids
                if label_id in cell_by_id and cell_by_id[label_id].type == "LABEL"
            ),
            None,
        )
        layout_cell = layout_cell_by_id.get(input_id)
        name = (
            cell_by_id[label_id].text.strip()
            if label_id is not None and cell_by_id[label_id].text.strip()
            else (layout_cell.printed_label_text.strip() if layout_cell else "")
            or target_path.rsplit(".", 1)[-1]
        )
        fields.append(
            TemplateField(
                id=f"field_{index:03d}",
                key=_safe_key(target_path, index),
                name=name,
                label_cell_id=label_id,
                value_cell_id=input_id,
                value_type="boolean" if layout_cell and layout_cell.role == "mark" else "text",
                confidence=0.99,
                reason_codes=["LEGACY_RELATION_ADAPTER"],
                legacy_target_path=target_path,
            )
        )
    relations_by_input: dict[str, list[LayoutRelation]] = defaultdict(list)
    for relation in layout.relations:
        relations_by_input[relation.input_cell_id].append(relation)
    for layout_cell in layout.cells:
        if layout_cell.role != "mark":
            continue
        label_id = next(
            (
                label_id
                for relation in relations_by_input.get(layout_cell.id, [])
                for label_id in relation.label_cell_ids
                if label_id in cell_by_id and cell_by_id[label_id].type == "LABEL"
            ),
            None,
        )
        for mark_set in layout_cell.mark_sets:
            index = len(fields) + 1
            target_path = mark_set.target_path
            fields.append(
                TemplateField(
                    id=f"field_{index:03d}",
                    key=_safe_key(target_path, index),
                    name=target_path.rsplit(".", 1)[-1],
                    label_cell_id=label_id,
                    value_cell_id=layout_cell.id,
                    value_type="enum",
                    confidence=0.99,
                    reason_codes=["LEGACY_MARK_SET_ADAPTER"],
                    legacy_target_path=target_path,
                    mark_set_id=mark_set.id,
                )
            )
    fields_by_value: dict[str, list[TemplateField]] = defaultdict(list)
    for field in fields:
        fields_by_value[field.value_cell_id].append(field)
    records_by_section: dict[str, list[TemplateRecord]] = defaultdict(list)
    record_field_ids: set[str] = set()
    for group in layout.record_groups:
        group_fields = [
            field
            for cell_id in group.input_cell_ids
            for field in fields_by_value.get(cell_id, [])
        ]
        if not group_fields:
            continue
        record_field_ids.update(field.id for field in group_fields)
        records_by_section[group.section_id].append(
            TemplateRecord(
                id=group.id,
                name=f"明細 {group.order}",
                order=group.order,
                bbox=TemplateBox(x=group.x, y=group.y, width=group.width, height=group.height),
                fields=group_fields,
                record_key_value=group.record_key_value,
                confidence=0.99,
                reason_codes=["LEGACY_RECORD_GROUP_ADAPTER"],
            )
        )
    sections: list[TemplateSection] = []
    for index, section in enumerate(layout.record_sections, start=1):
        records = sorted(records_by_section.get(section.id, []), key=lambda item: item.order)
        if not records:
            continue
        parent = cell_by_id.get(section.parent_label_cell_id or "")
        boxes = [record.bbox for record in records]
        if parent is not None:
            boxes.append(parent.bbox)
        sections.append(
            TemplateSection(
                id=section.id,
                key=(
                    section.collection_path.removeprefix("genericRecords.")
                    if section.record_type == "generic"
                    else f"section_{index:03d}"
                ),
                name=section.title,
                parent_label_cell_id=section.parent_label_cell_id,
                bbox=_union_box(boxes),
                records=records,
                include_empty_records=section.include_empty_rows,
                confidence=0.99,
                reason_codes=["LEGACY_SECTION_ADAPTER"],
            )
        )
    used_labels = {field.label_cell_id for field in fields if field.label_cell_id}
    used_values = {field.value_cell_id for field in fields}
    used_parents = {
        section.parent_label_cell_id for section in sections if section.parent_label_cell_id
    }
    return RelationshipDraft(
        standalone_fields=[field for field in fields if field.id not in record_field_ids],
        sections=sections,
        unassigned_cell_ids=[
            cell.id
            for cell in cells
            if (cell.type == "VALUE" and cell.id not in used_values)
            or (cell.type == "LABEL" and cell.id not in used_labels)
            or (cell.type == "PARENT_LABEL" and cell.id not in used_parents)
        ],
        phases=[
            InferencePhase(phase="FIELD_BINDING", confidence=0.99, reason_codes=["LEGACY_ADAPTER"]),
            InferencePhase(
                phase="RECORD_GROUPING",
                confidence=0.99,
                reason_codes=["LEGACY_ADAPTER"],
            ),
            InferencePhase(
                phase="SECTION_ASSIGNMENT",
                confidence=0.99,
                reason_codes=["LEGACY_ADAPTER"],
            ),
        ],
        warnings=["LEGACY_CATTLE_LAYOUT_ADAPTED_TO_GENERIC_DEFINITION"],
    )


def _safe_key(target_path: str, index: int) -> str:
    tail = target_path.rsplit(".", 1)[-1]
    logical_tail = re.sub(r"_\d+$", "", tail) if target_path.startswith("customFields.") else tail
    key = re.sub(r"[^A-Za-z0-9_]", "_", logical_tail)
    if not key or not key[0].isalpha():
        digest = hashlib.sha256(logical_tail.encode("utf-8")).hexdigest()[:12]
        return f"field_{digest}"
    return key[:100]


def _nearest_parent(candidates: list[TemplateCell], record_box: TemplateBox) -> TemplateCell | None:
    above = [cell for cell in candidates if cell.bbox.y + cell.bbox.height <= record_box.y]
    return max(above, key=lambda cell: cell.bbox.y + cell.bbox.height) if above else None


def _union_box(boxes: list[TemplateBox]) -> TemplateBox:
    left = min(box.x for box in boxes)
    top = min(box.y for box in boxes)
    right = max(box.x + box.width for box in boxes)
    bottom = max(box.y + box.height for box in boxes)
    return TemplateBox(x=left, y=top, width=right - left, height=bottom - top)
