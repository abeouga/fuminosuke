"""Receipt chain validation inside the existing SQLite mutation transaction."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from app.editing.errors import reject
from app.template_analysis.models import AnalysisMerge


def chain(session: Any, run_id: str, parent_id: str | None) -> list[AnalysisMerge]:
    rows = list(session.scalars(select(AnalysisMerge).where(AnalysisMerge.run_id == run_id)))
    active = {r.id: r for r in rows if not r.result_json.get("reverted")}
    parents = {r.result_json.get("parentMergeId") for r in active.values()}
    leaves = set(active) - parents
    valid_parent = (len(leaves) == 1 and parent_id in leaves) if active else parent_id is None
    if not valid_parent:
        raise reject("MERGE_CHAIN_STALE", "反映履歴が変わりました。結果を再取得してください。", 409)
    result = []
    seen = set()
    current = parent_id
    while current is not None:
        if current in seen or current not in active:
            raise reject("MERGE_CHAIN_STALE", "反映履歴を照合できません。", 409)
        seen.add(current)
        row = active[current]
        result.append(row)
        current = row.result_json.get("parentMergeId")
    if seen != set(active):
        raise reject("MERGE_CHAIN_STALE", "反映履歴が分岐しています。", 409)
    return list(reversed(result))


def proposal(pages: list[Any], snapshot_digest: str) -> dict[str, Any] | None:
    from app.editing.domain.command import digest

    parts = [p.proposal_json for p in pages if p.proposal_json]
    if not parts:
        return None
    import copy

    units = [copy.deepcopy(u) for p in parts for u in p["units"]]
    for u in units:
        u.setdefault("regionId", u["unitId"])
        u.setdefault("proposalDigest", digest(u))
    unresolved = [u for p in parts for u in p["unresolved"]]
    return dict(
        proposalDigest=digest([snapshot_digest, units, unresolved]),
        units=units,
        unresolved=unresolved,
        coverage={"cells": sum(p["coverage"]["cells"] for p in parts)},
        focus=next((p["focus"] for p in parts if p.get("focus")), None),
    )
