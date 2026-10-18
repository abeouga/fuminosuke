"""Run fixed visual components through existing ownership, budget and merge paths."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import logging
import time
from typing import Any

from sqlalchemy import select

from app.core.errors import AppError
from app.editing.errors import reject
from app.editing.infrastructure.mutation_uow import BusinessMutationUnitOfWork
from app.template_analysis.compose import compose
from app.template_analysis.draft_answer import (
    FOLLOW_UP_MARGIN_SECONDS,
    FOLLOW_UP_MIN_SECONDS,
    follow_up_instruction,
    merge_follow_up,
)
from app.template_analysis.draft_answer import TIMEOUT_SECONDS as DRAFT_TIMEOUT_SECONDS
from app.template_analysis.models import AnalysisPage
from app.template_analysis.repository import ACTIVE
from app.template_analysis.visual_compile import compile_visual_proposal
from app.template_analysis.visual_input import (
    VisualPage,
    VisualPlan,
    VisualUnit,
    build_visual_evidence,
    capture_visual_page,
    plan_visual_units,
)
from app.template_analysis.visual_provider import contract

logger = logging.getLogger(__name__)


def plan_snapshot(snapshot: dict[str, Any], storage: Any) -> None:
    """Fix total calls before admission; source/plan are rebuilt from final snapshot."""
    snapshot["visualProvider"] = contract()
    snapshot["policy"] = "template-analysis.v3.1"
    snapshot["provider"] = {**snapshot["provider"], **contract()}
    for page in snapshot["pages"]:
        source = capture_visual_page(
            snapshot, page["pageKey"], storage.read_bytes(page["imagePath"])
        )
        plan = plan_visual_units(source, source.pin)
        page["plannedCalls"] = sum(u.state == "ready" for u in plan.units)


class VisualRunner:
    def __init__(self, service: Any) -> None:
        self.service = service

    def plan_source(
        self, snapshot: dict[str, Any], page: dict[str, Any]
    ) -> tuple[VisualPage, VisualPlan]:
        source = capture_visual_page(
            snapshot, page["pageKey"], self.service.storage.read_bytes(page["imagePath"])
        )
        return source, plan_visual_units(source, source.pin)

    def source(
        self, sid: str, token: str, aid: str, page: dict[str, Any]
    ) -> tuple[VisualPage, VisualPlan]:
        _, _, snapshot, _ = self.service.work(sid, token, aid, page)
        if snapshot.get("visualProvider") != contract():
            raise reject("SNAPSHOT_STALE", "解析の定義が変わりました。", 409)
        source = capture_visual_page(
            snapshot, page["pageKey"], self.service.storage.read_bytes(page["imagePath"])
        )
        plan = plan_visual_units(source, source.pin)
        if sum(u.state == "ready" for u in plan.units) != page["plannedCalls"]:
            raise reject("SNAPSHOT_STALE", "解析の処理範囲が変わりました。", 409)
        return source, plan

    def publish(
        self,
        sid: str,
        token: str,
        aid: str,
        page: dict[str, Any],
        unit: VisualUnit,
        proposal: dict[str, Any] | None,
        error: dict[str, str] | None,
        total: int,
    ) -> None:
        service = self.service
        with service.database.transaction() as session:
            owner, run = service.repository.owned(session, sid, token, aid)
            if run.state not in ACTIVE:
                return
            service.repository.check_current(session, owner, run)
            if run.snapshot_json.get("visualProvider") != contract() or (
                hashlib.sha256(service.storage.read_bytes(page["imagePath"])).hexdigest()
                != page["imageSha256"]
            ):
                raise reject("SNAPSHOT_STALE", "案の公開前に解析対象が変わりました。", 409)
            row = session.scalar(
                select(AnalysisPage).where(
                    AnalysisPage.run_id == aid, AnalysisPage.page_key == page["pageKey"]
                )
            )
            results = dict(row.metrics_json.get("visualUnits", {}))
            if unit.unit_id in results:
                raise reject("ANALYSIS_DUPLICATE_UNIT", "同じ解析範囲を二度公開できません。", 409)
            results[unit.unit_id] = {"state": "failed" if error else "ready", "error": error}
            row.metrics_json = {**row.metrics_json, "visualUnits": results}
            existing = copy.deepcopy(row.proposal_json) or {
                "units": [],
                "unresolved": [],
                "coverage": {"cells": 0},
                "focus": None,
            }
            if proposal:
                existing["units"] += proposal["units"]
                existing["unresolved"] += proposal["unresolved"]
                existing["focus"] = service.combined_focus([existing, proposal])
            else:
                existing["unresolved"] += [
                    {
                        "pageId": page["pageId"],
                        "cellId": page["refs"][ref]["id"],
                        "reason": error["code"] if error else "ANALYSIS_FAILED",
                    }
                    for ref in unit.write_refs
                ]
                if not unit.write_refs and unit.bounds is not None:
                    x, y, width, height = unit.bounds
                    existing["unresolved"].append(
                        {
                            "pageId": page["pageId"],
                            "cellId": "",
                            "reason": error["code"] if error else "ANALYSIS_FAILED",
                            "bounds": {
                                "id": "",
                                "x": x,
                                "y": y,
                                "width": width,
                                "height": height,
                            },
                        }
                    )
            existing["coverage"]["cells"] += len(unit.write_refs)
            existing["coverage"]["completedUnits"] = len(results)
            existing["coverage"]["plannedUnits"] = total
            row.proposal_json = existing
            failures = sum(r["state"] == "failed" for r in results.values())
            if len(results) == total:
                row.state = "partial" if 0 < failures < total else "failed" if failures else "ready"
            if error:
                row.error_json = {
                    "code": "ANALYSIS_PARTIAL",
                    "message": "解析できない範囲があります。",
                }
            service.repository.emit(
                session,
                run,
                "region_validated",
                {
                    "pageId": page["pageId"],
                    "unitCount": len(existing["units"]),
                    "unresolvedCount": len(existing["unresolved"]),
                },
            )

    async def process_unit(
        self,
        sid: str,
        token: str,
        aid: str,
        page: dict[str, Any],
        source: VisualPage,
        plan: VisualPlan,
        unit: VisualUnit,
    ) -> None:
        service = self.service
        proposal = None
        error = None
        queued_at = time.perf_counter()
        try:
            if unit.state != "ready":
                raise reject(
                    unit.reason or "ANALYSIS_HELD", "依存する範囲が解析上限を超えています。", 422
                )
            async with service.slots:
                acquired_at = time.perf_counter()
                await asyncio.to_thread(service.work, sid, token, aid, page)
                checked_at = time.perf_counter()
                evidence = await asyncio.to_thread(
                    build_visual_evidence, source, source.pin, plan, unit.unit_id
                )
                used = False
                metrics: dict[str, Any] = {
                    "slotWaitMs": round((acquired_at - queued_at) * 1000, 3),
                    "currentSnapshotCheckMs": round((checked_at - acquired_at) * 1000, 3),
                    "evidencePreparationMs": round((time.perf_counter() - checked_at) * 1000, 3),
                }

                async def reserved(ledger_id: str) -> None:
                    nonlocal used
                    used = True
                    accepted = await asyncio.to_thread(
                        service.repository.reserve, aid, page["pageKey"], ledger_id
                    )
                    if not accepted:
                        raise reject("ANALYSIS_CANCELLED", "解析を終了しました。", 409)
                    # The paid reservation stays consumed if this last guard refuses.
                    await asyncio.to_thread(service.work, sid, token, aid, page)

                try:
                    raw = await service.provider.analyze_visual(evidence, reserved, metrics)
                finally:
                    if used:
                        await asyncio.to_thread(
                            service.repository.settled, aid, page["pageKey"], metrics
                        )
                if page.get("wholePage"):
                    # Whole-page drafts are tuned from their answers; keep the model JSON in
                    # the server log only, never in a published proposal.
                    logger.info(
                        "template draft answer %s unit=%s: %s",
                        aid,
                        unit.unit_id,
                        json.dumps(raw, ensure_ascii=False)[:200000],
                    )
                proposal = await asyncio.to_thread(
                    self.validate, sid, token, aid, page, source, plan, evidence, raw
                )
                omitted = self.omitted(page, unit, proposal) if page.get("wholePage") else []
                remaining = (
                    DRAFT_TIMEOUT_SECONDS
                    - FOLLOW_UP_MARGIN_SECONDS
                    - (time.perf_counter() - queued_at)
                )
                if omitted and remaining >= FOLLOW_UP_MIN_SECONDS:
                    # One follow-up in the time left asks only for what the band's answer
                    # left out; the merged answer is kept only if it resolves more cells.
                    used = False
                    follow_metrics: dict[str, Any] = {"omittedCells": len(omitted)}
                    second = None
                    try:
                        second = await service.provider.analyze_visual(
                            evidence,
                            reserved,
                            follow_metrics,
                            follow_up=follow_up_instruction(omitted),
                            seconds=remaining,
                        )
                    except (AppError, ValueError) as exc:
                        logger.warning("template draft follow-up failed %s: %s", aid, exc)
                    finally:
                        if used:
                            await asyncio.to_thread(
                                service.repository.settled, aid, page["pageKey"], follow_metrics
                            )
                    if second is not None:
                        logger.info(
                            "template draft follow-up answer %s unit=%s: %s",
                            aid,
                            unit.unit_id,
                            json.dumps(second, ensure_ascii=False)[:200000],
                        )
                        merged = merge_follow_up(raw, second, set(omitted))
                        try:
                            improved = await asyncio.to_thread(
                                self.validate, sid, token, aid, page, source, plan, evidence, merged
                            )
                        except (AppError, ValueError) as exc:
                            logger.warning("template draft follow-up rejected %s: %s", aid, exc)
                        else:
                            if len(improved["unresolved"]) < len(proposal["unresolved"]):
                                proposal = improved
        except AppError as exc:
            error = {"code": exc.code, "message": exc.detail}
        except ValueError as exc:
            logger.warning(
                "template analysis proposal rejected %s unit=%s: %s", aid, unit.unit_id, exc
            )
            error = {
                "code": "PROPOSAL_INVALID",
                "message": "AI案の対応関係を確認できません。",
                "detail": str(exc)[:200],
            }
        await asyncio.to_thread(
            self.publish, sid, token, aid, page, unit, proposal, error, len(plan.units)
        )

    @staticmethod
    def omitted(page: dict[str, Any], unit: Any, proposal: dict[str, Any]) -> list[str]:
        ref_by_id = {cell["id"]: ref for ref, cell in page["refs"].items()}
        writable = set(unit.write_refs)
        cells = {ref_by_id.get(item["cellId"]) for item in proposal["unresolved"]}
        return sorted(
            (r for r in cells if r is not None and r in writable), key=lambda r: int(r[1:])
        )

    def validate(
        self,
        sid: str,
        token: str,
        aid: str,
        page: dict[str, Any],
        source: VisualPage,
        plan: VisualPlan,
        evidence: Any,
        raw: dict[str, Any],
    ) -> dict[str, Any]:
        owner, catalog, snapshot, _ = self.service.work(sid, token, aid, page)
        current = capture_visual_page(snapshot, page["pageKey"], source.image)
        if current.pin != source.pin:
            raise reject("SNAPSHOT_STALE", "解析対象が変わりました。", 409)
        compiled = compile_visual_proposal(raw, source, current.pin, plan, evidence, catalog)
        if any(u.bounds for u in plan.units) and not any(u.get("regionId") for u in compiled.units):
            from app.template_analysis.region_compile import atomic_region

            compiled = atomic_region(compiled, source, evidence, [])
        mergeable = [u for u in compiled.units if u["state"] == "mergeable"]
        focus = None
        if mergeable:
            prepared, _, _ = compose(
                self.service.editing,
                owner,
                catalog,
                snapshot["draft"],
                mergeable,
                "validate_" + aid,
                set(page["protectedFacets"]),
            )
            focus = BusinessMutationUnitOfWork.focus(owner, catalog, prepared)
        return {
            "units": [
                {
                    **unit,
                    "displayOrder": next(
                        index
                        for index, planned in enumerate(plan.units)
                        if planned.unit_id == evidence.unit_id
                    ),
                }
                for unit in compiled.units
            ],
            "focus": focus,
            "unresolved": [
                {
                    "pageId": page["pageId"],
                    "cellId": i["cellId"] if "cellId" in i else page["refs"][i["cell"]]["id"],
                    "reason": i["reason"],
                    **({"bounds": {"id": "", **i["bounds"]}} if i.get("bounds") else {}),
                }
                for i in compiled.unresolved
            ],
        }

    async def process_page(self, sid: str, token: str, aid: str, page: dict[str, Any]) -> None:
        source, plan = await asyncio.to_thread(self.source, sid, token, aid, page)
        # TaskGroup joins/cancels siblings before the owning page exits.
        try:
            async with asyncio.TaskGroup() as group:
                for unit in plan.units:
                    group.create_task(self.process_unit(sid, token, aid, page, source, plan, unit))
        except* AppError as errors:
            # Preserve expected stale/cancel codes after every sibling has joined.
            raise errors.exceptions[0] from None
