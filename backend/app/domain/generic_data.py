from __future__ import annotations

from decimal import Decimal, InvalidOperation

from app.domain.generic_template_mapping import generic_field_locations
from app.domain.ocr_factory import blank_reading
from app.domain.ocr_models import (
    GenericDataDocument,
    GenericFieldValue,
    GenericRecordValue,
    GenericSectionValue,
    OcrDocument,
)
from app.domain.template_definition import TemplateDefinition, TemplateField, TemplateSection


def build_generic_data(document: OcrDocument, raw_definition: object) -> GenericDataDocument | None:
    if not isinstance(raw_definition, dict):
        return None
    definition = TemplateDefinition.model_validate(raw_definition)
    readings = document.fields
    standalone_mapping_key = {
        location.field.id: location.mapping_key
        for location in generic_field_locations(definition)
        if location.section is None and location.record is None
    }
    record_mapping_key = {
        (location.section.id, location.record.id, location.field.id): location.mapping_key
        for location in generic_field_locations(definition)
        if location.section is not None and location.record is not None
    }

    def field_value(field: TemplateField, mapping_key: str) -> GenericFieldValue:
        return GenericFieldValue(
            field_id=field.id,
            key=field.key,
            name=field.name,
            value_type=field.value_type,
            reading=readings.get(mapping_key) or blank_reading(mapping_key),
        )

    def section_records(section: TemplateSection) -> list[GenericRecordValue]:
        values = [
            GenericRecordValue(
                record_id=record.id,
                name=record.name,
                order=record.order,
                record_key_value=record.record_key_value,
                fields=[
                    field_value(
                        field,
                        record_mapping_key[(section.id, record.id, field.id)],
                    )
                    for field in record.fields
                ],
            )
            for record in section.records
        ]
        if section.include_empty_records:
            return values
        return [
            record
            for record in values
            if any(
                field.reading.raw is not None or field.reading.normalized is not None
                for field in record.fields
            )
        ]

    return GenericDataDocument(
        template_name=definition.template_name,
        standalone_fields=[
            field_value(field, standalone_mapping_key[field.id])
            for field in definition.standalone_fields
        ],
        sections=[
            GenericSectionValue(
                section_id=section.id,
                key=section.key,
                name=section.name,
                records=section_records(section),
            )
            for section in definition.sections
        ],
    )


def build_generic_values(data: GenericDataDocument | None) -> dict[str, object] | None:
    if data is None:
        return None

    def value(field: GenericFieldValue) -> object:
        raw = field.reading.normalized or field.reading.raw
        if raw is None:
            return None
        compact = raw.replace(",", "").strip()
        if field.value_type == "integer":
            try:
                return int(compact)
            except ValueError:
                return raw
        if field.value_type == "decimal":
            try:
                return float(Decimal(compact))
            except InvalidOperation:
                return raw
        if field.value_type == "boolean":
            normalized = compact.casefold()
            if normalized in {"true", "yes", "1", "有", "はい", "○", "✓"}:
                return True
            if normalized in {"false", "no", "0", "無", "いいえ", "×"}:  # noqa: RUF001
                return False
        return raw

    result: dict[str, object] = {field.key: value(field) for field in data.standalone_fields}
    for section in data.sections:
        result[section.key] = [
            {field.key: value(field) for field in record.fields} for record in section.records
        ]
    return result
