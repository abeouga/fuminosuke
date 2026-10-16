"""Resolve metadata only against the final current-layout text mappings."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import uuid4

from pydantic import ValidationError

from app.domain.template_config import (
    DocumentIdentityConfig,
    DocumentNameConfig,
    FieldMappingConfig,
)
from app.editing.errors import reject
from app.editing.operation_contracts import (
    DocumentNameConfigure,
    FieldRef,
    IdentityConfigure,
    LocalRef,
)
from app.layout_analysis.models import LayoutDefinition
from app.services.layout_service import LayoutService

if TYPE_CHECKING:
    from app.editing.semantic_commands import SemanticCommands


def configure_metadata(
    ctx: SemanticCommands, operations: list[IdentityConfigure | DocumentNameConfigure]
) -> None:
    if not operations:
        return
    from app.editing.commands import name

    mappings = LayoutService._as_field_mappings(LayoutDefinition.model_validate(ctx.layout))

    def field(ref: FieldRef) -> FieldMappingConfig:
        if isinstance(ref, str):
            entity = ctx.ref(ref, "field")
            matches = [m for m in mappings if m.mapping_id == entity]
            # A handle is bound to both physical identity and its original meaning.
            original = next(
                (
                    t
                    for t in ctx.catalog.targets.values()
                    if t.kind == "field" and t.entity_id == entity
                ),
                None,
            )
            if matches and original and original.binding_path != matches[0].target_path:
                raise reject(
                    "FIELD_BINDING_CHANGED",
                    "欄の意味が変更されています。欄と意味を明示してください。",
                )
        else:
            cell, meaning = ctx.cell(ref.cell), ctx.meaning(ref.meaning)
            matches = [
                m
                for m in mappings
                if m.mapping_id == f"layout_{cell['id']}" and m.target_path == meaning["targetPath"]
            ]
        if (
            len(matches) != 1
            or not matches[0].enabled
            or matches[0].mapping_type != "text"
            or matches[0].composite_subfields
        ):
            raise reject("FIELD_BINDING_CHANGED", "現在の一意の文字列欄を指定してください。")
        return matches[0]

    for op in operations:
        if isinstance(op, DocumentNameConfigure):
            rule = op.rule
            ctx.plan.after["documentName"] = (
                DocumentNameConfig(
                    field_paths=[field(ref).target_path for ref in rule.fields],
                    separator=rule.separator,
                    prefix=rule.prefix,
                    suffix=rule.suffix,
                ).model_dump(mode="json", by_alias=True)
                if rule
                else None
            )
            continue
        groups: dict[str, str] = {}
        entries = []
        for item in op.entries:
            mapping = field(item.field)
            group = None
            if isinstance(item.group, LocalRef):
                # Group declarations have a separate local namespace inside this operation.
                key = item.group.local_ref
                group = groups.setdefault(key, f"identity_{uuid4().hex}")
            elif item.group:
                group = ctx.ref(item.group, "identity_group")
            try:
                entry = DocumentIdentityConfig(
                    mapping_id=mapping.mapping_id,
                    target_path=mapping.target_path,
                    label=mapping.label,
                    value_type=item.value_type,
                    exact_length=item.exact_length,
                    match_role=item.match_role,
                    display_role=item.display_role,
                    match_group_id=group,
                    match_group_label=name(item.group_label) if item.group_label else None,
                    component_order=item.component_order,
                ).model_dump(mode="json", by_alias=True)
            except ValidationError as error:
                raise reject("INVALID_ARGUMENT", "書類識別の形式が不正です。", 422) from error
            entries.append(entry)
        validate_identity_groups(entries)
        ctx.plan.after["documentIdentityFields"] = entries


def validate_identity_groups(entries: list[dict[str, Any]]) -> None:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for entry in entries:
        if entry["matchGroupId"]:
            grouped.setdefault(entry["matchGroupId"], []).append(entry)
        elif entry["matchGroupLabel"] is not None:
            raise reject("INVALID_ARGUMENT", "グループなしでグループ名は指定できません。", 422)
    for members in grouped.values():
        if (
            len({m["matchGroupLabel"] for m in members}) != 1
            or not members[0]["matchGroupLabel"]
            or len({m["componentOrder"] for m in members}) != len(members)
        ):
            raise reject("INVALID_ARGUMENT", "識別グループ名と構成要素の順序が不正です。", 422)
