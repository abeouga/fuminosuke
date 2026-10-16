"""Reject visual proposals and merge provenance from a retired analysis contract."""

from __future__ import annotations

from typing import Any

from app.editing.errors import reject


def check_visual_contract(snapshot: dict[str, Any]) -> None:
    from app.template_analysis.snapshot import PHYSICAL_POLICY

    if snapshot.get("physicalPolicy") != PHYSICAL_POLICY:
        raise reject("SNAPSHOT_STALE", "解析の数値表現の定義が変わりました。", 409)
    if not snapshot.get("policy", "").startswith("template-analysis.v3."):
        raise reject("SNAPSHOT_STALE", "解析APIの定義が変わりました。", 409)
    if "visualProvider" not in snapshot:
        # Classification-only graph runs have their own snapshot contract.
        return
    # Imported at use, keeping editing provenance free of provider import cycles.
    from app.template_analysis.visual_provider import contract

    if snapshot["visualProvider"] != contract():
        raise reject("SNAPSHOT_STALE", "解析の定義が変わりました。", 409)
