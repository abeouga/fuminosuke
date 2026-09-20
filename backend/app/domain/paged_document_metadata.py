"""Document-wide settings and page-owned OCR hints, without persistence or transport."""

from collections.abc import Mapping
from typing import Any

from app.domain.template_config import DocumentIdentityConfig, TemplateConfig

ADAPTER = "paged-document-fields-v1"


def has_document_metadata(config: TemplateConfig) -> bool:
    return bool(config.effective_document_identity_fields() or config.document_name)


def page_context(config: TemplateConfig, mappings: list[dict[str, Any]]) -> dict[str, Any]:
    owned = {m["mappingId"]: m for m in mappings}
    fields = []
    for field in config.effective_document_identity_fields():
        mapping = owned.get(field.mapping_id)
        if mapping is None:
            continue
        if (
            mapping["targetPath"] != field.target_path
            or mapping.get("mappingType", "text") != "text"
        ):
            raise ValueError("Identity field ownership does not match its page")
        fields.append(field.model_dump(mode="json", by_alias=True))
    return {"adapter": ADAPTER, "identityFields": fields}


def request_identity_fields(
    config: TemplateConfig, snapshot: Mapping[str, Any]
) -> list[DocumentIdentityConfig]:
    if "documentContext" not in snapshot:
        return config.effective_document_identity_fields()
    context = snapshot["documentContext"]
    if (
        not snapshot.get("pageId")
        or not isinstance(context, dict)
        or set(context) != {"adapter", "identityFields"}
        or context["adapter"] != ADAPTER
        or not isinstance(context["identityFields"], list)
        or has_document_metadata(config)
    ):
        raise ValueError("Invalid page document context")
    fields = [DocumentIdentityConfig.model_validate(f) for f in context["identityFields"]]
    mappings = {m.mapping_id: m for m in config.field_mappings if m.enabled}
    if len({f.mapping_id for f in fields}) != len(fields):
        raise ValueError("Duplicate page identity")
    for field in fields:
        mapping = mappings.get(field.mapping_id)
        if (
            mapping is None
            or mapping.target_path != field.target_path
            or mapping.mapping_type != "text"
        ):
            raise ValueError("Identity field is not owned by this page")
    return fields


def proposed_name(config: TemplateConfig, readings: dict[str, dict[str, Any]]) -> str | None:
    """Only a proposal. Never guess missing components or confirm/merge a document."""
    configured = config.document_name
    paths = (
        configured.field_paths
        if configured
        else [
            f.target_path
            for f in config.effective_document_identity_fields()
            if f.display_role == "title"
        ]
    )
    if not paths:
        return None
    values = []
    for path in paths:
        reading = readings.get(path)
        if not reading or reading.get("status") != "readable":
            return None
        value = (
            reading.get("normalized")
            if reading.get("normalized") is not None
            else reading.get("raw")
        )
        if not isinstance(value, str) or not value.strip():
            return None
        values.append(value.strip())
    if configured:
        candidate = configured.prefix + configured.separator.join(values) + configured.suffix
    else:
        candidate = " ".join(values)
    return candidate.strip()[:180] or None
