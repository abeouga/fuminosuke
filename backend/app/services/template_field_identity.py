"""Logical provenance is a sidecar, never an OCR/provider mapping replacement."""

from uuid import NAMESPACE_URL, uuid4, uuid5

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import TemplateFieldIdentity, TemplateLogicalField, TemplateVersion
from app.domain.template_config import FieldMappingConfig


def mapping_keys(mappings: list[FieldMappingConfig]) -> list[str]:
    return [
        key
        for m in mappings
        for key in (m.mapping_id, *(f"{m.mapping_id}:{s.id}" for s in m.composite_subfields))
    ]


def identities(
    session: Session, version: TemplateVersion, mappings: list[FieldMappingConfig]
) -> dict[str, str]:
    stored = {
        row.mapping_id: row.logical_field_id
        for row in session.scalars(
            select(TemplateFieldIdentity).where(
                TemplateFieldIdentity.template_version_id == version.id,
                TemplateFieldIdentity.present.is_(True),
            )
        )
    }
    return {
        key: stored.get(key)
        or str(uuid5(NAMESPACE_URL, f"llm-ocr:logical-field:{version.id}:{key}"))
        for key in mapping_keys(mappings)
    }


def bind(session: Session, version: TemplateVersion, key: str, logical_id: str) -> None:
    field = session.get(TemplateLogicalField, logical_id)
    if field is None:
        session.add(TemplateLogicalField(id=logical_id, template_id=version.template_id))
        session.flush()
    elif field.template_id != version.template_id:
        raise ValueError("Logical field belongs to a different template")
    row = session.get(TemplateFieldIdentity, (version.id, key))
    if row is None:
        session.add(
            TemplateFieldIdentity(
                template_version_id=version.id,
                mapping_id=key,
                logical_field_id=logical_id,
                present=True,
            )
        )
        session.flush()


def reconcile(
    session: Session,
    version: TemplateVersion,
    previous: list[FieldMappingConfig],
    current: list[FieldMappingConfig],
) -> None:
    for key, logical_id in identities(session, version, previous).items():
        bind(session, version, key, logical_id)
    current_keys = set(mapping_keys(current))
    rows = {
        r.mapping_id: r
        for r in session.scalars(
            select(TemplateFieldIdentity).where(
                TemplateFieldIdentity.template_version_id == version.id,
            )
        )
    }
    for key in current_keys:
        row = rows.get(key)
        if row is not None:
            # Reattaching an existing draft cell is explicit physical provenance;
            # labels, positions and output paths are never used for matching.
            row.present = True
        if row is None:
            bind(session, version, key, str(uuid4()))
    for key, row in rows.items():
        if key not in current_keys:
            row.present = False
