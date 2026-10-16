"""Private handles for a draft already validated by the shared hand compiler."""

from __future__ import annotations

import hashlib
from dataclasses import replace
from typing import Any

from app.editing.catalog import Catalog, CatalogService, Target
from app.layout_analysis.models import LayoutDefinition


def with_draft_cells(
    catalog: Catalog,
    state: dict[str, Any],
    aliases: dict[str, str] | None = None,
) -> Catalog:
    layout = LayoutDefinition.model_validate(state["layout"])
    ids = {c.id for c in layout.cells}
    targets = {
        h: t
        for h, t in catalog.targets.items()
        if t.kind not in {"cell", "heading"} or t.entity_id in ids
    }
    known = {(t.kind, t.entity_id) for t in targets.values()}
    authorized_pages = {t.page_id for t in catalog.targets.values() if t.kind == "cell"}
    if not catalog.pages:
        authorized_pages.add(None)
    for cell in layout.cells:
        page = CatalogService.cell_page(layout, cell, catalog.pages)
        if page not in authorized_pages:
            continue
        for kind in ["cell"] + (["heading"] if cell.role in {"label", "parent_label"} else []):
            if (kind, cell.id) in known:
                continue
            alias = (aliases or {}).get(cell.id, cell.id)
            handle = (
                "t_"
                + hashlib.sha256(f"draft:{catalog.generation}:{kind}:{alias}".encode()).hexdigest()
            )
            targets[handle] = Target(
                handle, kind, cell.id, page, cell.field_label or cell.text or "未保存の枠"
            )
    return replace(catalog, state=state, targets=targets)
