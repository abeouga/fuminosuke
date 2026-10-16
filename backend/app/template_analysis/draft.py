"""Validate unsaved hand intent while retaining its identity in AI previews."""

from __future__ import annotations

from typing import Any

from app.editing.catalog import Catalog
from app.editing.commands import Plan
from app.editing.draft_identity import DraftIdentityMap
from app.editing.errors import reject
from app.editing.manual_compiler import ManualCompiler, ManualInput
from app.editing.manual_draft import ManualDraft


def compile_hand(catalog: Catalog, body: ManualInput) -> tuple[Plan, DraftIdentityMap]:
    plan, substitutions, _ = ManualCompiler().compile(catalog, body)
    try:
        identities = DraftIdentityMap(substitutions)
        projected = identities.apply(plan.after, reverse=True)
        # A reversible ID translation must account for the whole hand draft.
        # Never use projection to hide another compiler normalization or loss.
        actual: dict[str, Any] = {
            k: projected[k] for k in ("layout", "documentIdentityFields", "documentName")
        }
        if ManualDraft.model_validate(actual) != body.draft:
            raise ValueError("DRAFT_SEMANTICS_CHANGED")
    except ValueError as error:
        raise reject(
            "MANUAL_DRAFT_NOT_REPRESENTABLE",
            "手動設定と解析用下書きの対応を確認できません。下書きは保持しています。",
            409,
        ) from error
    return plan, identities
