from __future__ import annotations

import hashlib
from collections.abc import Iterator
from dataclasses import dataclass

from app.domain.template_definition import (
    TemplateDefinition,
    TemplateField,
    TemplateRecord,
    TemplateSection,
)


@dataclass(frozen=True, slots=True)
class GenericFieldLocation:
    mapping_key: str
    field: TemplateField
    section: TemplateSection | None
    record: TemplateRecord | None


def generic_field_locations(
    definition: TemplateDefinition,
) -> Iterator[GenericFieldLocation]:
    for field in definition.standalone_fields:
        yield GenericFieldLocation(_mapping_key("standalone", field.id), field, None, None)
    for section in definition.sections:
        for record in section.records:
            for field in record.fields:
                context = f"{section.id}:{record.id}"
                yield GenericFieldLocation(_mapping_key(context, field.id), field, section, record)


def _mapping_key(context: str, field_id: str) -> str:
    digest = hashlib.sha256(f"{context}:{field_id}".encode()).hexdigest()[:24]
    return f"generic_{digest}"
