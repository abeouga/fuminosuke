"""Analysis orchestration; no business persistence and no model-driven tool loop."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import logging
from datetime import timedelta
from typing import Any, cast

from sqlalchemy import select

from app.core.errors import AppError
from app.db.models import utc_now
from app.editing.authoring_provenance import facets, touches
from app.editing.domain.command import digest
from app.editing.errors import reject
from app.template_analysis.compose import compose, draft_from
from app.template_analysis.draft_answer import TIMEOUT_SECONDS as DRAFT_TIMEOUT_SECONDS
from app.template_analysis.graph import ROLE_NAMES, build_units, validate_graph
from app.template_analysis.merge_chain import chain
from app.template_analysis.merge_chain import proposal as live_proposal
from app.template_analysis.models import AnalysisMerge, AnalysisPage, AnalysisRun
from app.template_analysis.repository import ACTIVE, READABLE, AnalysisRepository
from app.template_analysis.snapshot import capture, physical
from app.template_analysis.visual_provider import VisualAnalysisProvider
from app.template_analysis.visual_runner import VisualRunner, plan_snapshot

logger = logging.getLogger(__name__)


class AnalysisService:
    def __init__(
        self, editing: Any, storage: Any, settings: Any, budget: Any, provider: Any = None
    ) -> None:
        self.editing, self.storage, self.budget = editing, storage, budget
        self.repository = AnalysisRepository(editing)
        self.database = self.repository.database
        self.provider = provider or VisualAnalysisProvider(settings, budget)
        self.visual = VisualRunner(self)
        # Four lets a whole-page draft send its bands at once (2026-10-17).
        self.slots = asyncio.Semaphore(4)
        self.tasks: dict[str, asyncio.Task[None]] = {}

    def admit(self, sid: str, token: str, body: Any) -> tuple[dict[str, Any], bool]:
        fingerprint = digest(body.model_dump(mode="json", by_alias=True))
        with self.database.transaction() as session:
            owner = self.editing.sessions.owner(session, sid, token)
            runs = list(session.scalars(select(AnalysisRun).where(AnalysisRun.session_id == sid)))
            for run in runs:
                known = (
                    run.fingerprint
                    if run.request_id == body.request_id
                    else run.request_aliases.get(body.request_id)
                )
                if known:
                    if known != fingerprint:
                        raise reject("IDEMPOTENCY_CONFLICT", "同じ要求IDの内容が異なります。", 409)
                    return self.repository.result(session, run), False
            catalog = self.editing.sessions.catalogs.build(session, owner)
            self.editing.repository.binding(
                session, owner, catalog, {"binding": body.binding.model_dump(by_alias=True)}
            )
            snapshot = capture(session, owner, catalog, body, self.storage)
            if body.region_scope and (
                len(snapshot["pages"]) != 1 or not snapshot["pages"][0]["structure"]
            ):
                raise reject(
                    "REGION_SCOPE_UNAVAILABLE", "この対象では領域限定解析を利用できません。", 422
                )
            if body.task in {"full", "structure", "draft"}:
                plan_snapshot(snapshot, self.storage)
            snapshot_digest = digest(snapshot)
            for run in runs:
                self.repository.expire(session, run)
                if run.state in ACTIVE:
                    if run.snapshot_digest == snapshot_digest and run.task == body.task:
                        if len(run.request_aliases) >= 200:
                            raise reject("ANALYSIS_BUSY", "同じ解析への再要求が多すぎます。", 409)
                        run.request_aliases = {**run.request_aliases, body.request_id: fingerprint}
                        return self.repository.result(session, run), False
                    raise reject(
                        "ANALYSIS_BUSY", "この画面で別の解析を実行中です。", 409, analysisId=run.id
                    )
            calls = sum(p["plannedCalls"] for p in snapshot["pages"])
            if calls:
                self.provider.check_available()
                if self.budget.status()["remaining"] < calls:
                    raise reject(
                        "API_BUDGET_EXCEEDED", "解析に必要なAPI利用枠が不足しています。", 429
                    )
            run = AnalysisRun(
                session_id=sid,
                resource_id=owner.resource_id,
                request_id=body.request_id,
                fingerprint=fingerprint,
                snapshot_digest=snapshot_digest,
                snapshot_json=snapshot,
                base_revision=catalog.revision,
                state="queued",
                task=body.task,
                expires_at=utc_now() + timedelta(minutes=30),
                sequence=0,
            )
            session.add(run)
            session.flush()
            for page in snapshot["pages"]:
                session.add(
                    AnalysisPage(
                        run_id=run.id,
                        page_key=page["pageKey"],
                        state="queued",
                        planned_calls=page["plannedCalls"],
                    )
                )
            self.repository.emit(session, run, "accepted", {"plannedCalls": calls})
            session.flush()
            return self.repository.result(session, run), True

    def plan(self, sid: str, token: str, body: Any) -> dict[str, Any]:
        with self.database.transaction() as session:
            owner = self.editing.sessions.owner(session, sid, token)
            catalog = self.editing.sessions.catalogs.build(session, owner)
            self.editing.repository.binding(
                session, owner, catalog, {"binding": body.binding.model_dump(by_alias=True)}
            )
            snapshot = capture(
                session,
                owner,
                catalog,
                body.model_copy(update={"region_scope": None}),
                self.storage,
            )
            if len(snapshot["pages"]) != 1 or not snapshot["pages"][0]["structure"]:
                raise reject(
                    "REGION_SCOPE_UNAVAILABLE", "この対象では領域計画を利用できません。", 422
                )
            plan_snapshot(snapshot, self.storage)
            page = snapshot["pages"][0]
            _, plan = self.visual.plan_source(snapshot, page)
            regions = []
            for unit in plan.units:
                if unit.bounds is None:
                    raise reject(
                        "REGION_SCOPE_UNAVAILABLE", "領域の所有範囲を確認できません。", 422
                    )
                regions.append(
                    dict(
                        bounds=dict(zip(["x", "y", "width", "height"], unit.bounds, strict=True)),
                        cellIds=[page["refs"][r]["id"] for r in unit.write_refs],
                        state=unit.state,
                        reason=unit.reason,
                    )
                )
            return dict(
                contractVersion="template-analysis.v3",
                resource={"kind": "template", "id": owner.resource_id},
                baseRevision=catalog.revision,
                draftDigest=snapshot["draftDigest"],
                regions=regions,
            )

    async def create(self, sid: str, token: str, body: Any) -> dict[str, Any]:
        result, new = await asyncio.to_thread(self.admit, sid, token, body)
        if new:
            aid = result["analysisId"]
            task = asyncio.create_task(self.execute(sid, token, aid))
            self.tasks[aid] = task
            task.add_done_callback(lambda _: self.tasks.pop(aid, None))
        return result

    def work(
        self, sid: str, token: str, aid: str, page: dict[str, Any] | None = None
    ) -> tuple[Any, Any, dict[str, Any], str]:
        with self.database.transaction() as session:
            owner, run = self.repository.owned(session, sid, token, aid)
            catalog = self.repository.check_current(session, owner, run)
            if page is not None:
                image = self.storage.read_bytes(page["imagePath"])
                if hashlib.sha256(image).hexdigest() != page["imageSha256"]:
                    raise reject("SNAPSHOT_STALE", "解析開始後に基準画像が変わりました。", 409)
            return copy.deepcopy(owner), catalog, copy.deepcopy(run.snapshot_json), run.task

    def start_page(self, aid: str, page_key: str) -> bool:
        with self.database.transaction() as session:
            run = session.get(AnalysisRun, aid)
            if run.state not in ACTIVE:
                return False
            run.state = "running"
            page = session.scalar(
                select(AnalysisPage).where(
                    AnalysisPage.run_id == aid, AnalysisPage.page_key == page_key
                )
            )
            if page.state != "queued":
                return False
            page.state = "running"
            self.repository.emit(
                session, run, "page_started", {"pageId": None if page_key == "legacy" else page_key}
            )
            return True

    async def request(
        self, sid: str, token: str, aid: str, page: dict[str, Any], task: str
    ) -> dict[str, Any]:
        async with self.slots:
            await asyncio.to_thread(self.work, sid, token, aid, page)
            image = await asyncio.to_thread(self.storage.read_bytes, page["imagePath"])
            if hashlib.sha256(image).hexdigest() != page["imageSha256"]:
                raise reject("SNAPSHOT_STALE", "送信前に基準画像が変わりました。", 409)
            used = False

            async def reserved(ledger_id: str) -> None:
                nonlocal used
                used = True
                if not await asyncio.to_thread(
                    self.repository.reserve, aid, page["pageKey"], ledger_id
                ):
                    raise reject("ANALYSIS_CANCELLED", "解析を終了しました。", 409)
                await asyncio.to_thread(self.work, sid, token, aid, page)

            metrics: dict[str, Any] = {}
            try:
                result, metrics = await self.provider.analyze(task, page, image, reserved)
                return result
            finally:
                if used:
                    await asyncio.to_thread(self.repository.settled, aid, page["pageKey"], metrics)

    def validated_page(
        self, sid: str, token: str, aid: str, page: dict[str, Any], raw: dict[str, Any]
    ) -> None:
        owner, catalog, snapshot, task = self.work(sid, token, aid, page)
        graph = validate_graph(raw, page, task)
        units = build_units(graph, page, snapshot["draft"]["layout"], task)
        mergeable = [u for u in units if u["state"] == "mergeable"]
        focus = None
        if mergeable:
            plan, _, _ = compose(
                self.editing,
                owner,
                catalog,
                snapshot["draft"],
                mergeable,
                "validate_" + aid,
                set(page["protectedFacets"]),
            )
            from app.editing.infrastructure.mutation_uow import BusinessMutationUnitOfWork

            focus = BusinessMutationUnitOfWork.focus(owner, catalog, plan)
        proposal: dict[str, Any] = {
            "focus": focus,
            "units": units,
            "unresolved": [
                {
                    "pageId": page["pageId"],
                    "cellId": page["refs"][v["cell"]]["id"],
                    "reason": v["reason"],
                }
                for v in graph["unresolved"]
            ],
            "coverage": {
                "cells": len(page["targets"]),
                "unknownRoles": len(graph["roles"]["unknown"]),
                "fields": len(graph["fields"]),
                "tableCells": sum(len(row["cells"]) for t in graph["tables"] for row in t["rows"]),
            },
        }
        with self.database.transaction() as session:
            owner, run = self.repository.owned(session, sid, token, aid)
            self.repository.check_current(session, owner, run)
            if run.state not in ACTIVE:
                return
            row = session.scalar(
                select(AnalysisPage).where(
                    AnalysisPage.run_id == aid, AnalysisPage.page_key == page["pageKey"]
                )
            )
            row.state, row.proposal_json = "ready", proposal
            self.repository.emit(
                session,
                run,
                "page_validated",
                {
                    "pageId": page["pageId"],
                    "unitCount": len(units),
                    "unresolvedCount": len(proposal["unresolved"]),
                },
            )

    async def process_page(
        self, sid: str, token: str, aid: str, page: dict[str, Any], task: str
    ) -> None:
        if not await asyncio.to_thread(self.start_page, aid, page["pageKey"]):
            return
        try:
            if task in {"full", "structure", "draft"}:
                await self.visual.process_page(sid, token, aid, page)
                return
            else:
                editable = [r for r in page["targets"] if r not in page["lockedRoles"]]
                chunks = [
                    {**page, "questionRefs": editable[i : i + 200]}
                    for i in range(0, len(editable), 200)
                ]
                results = await asyncio.gather(
                    *(self.request(sid, token, aid, c, task) for c in chunks),
                    return_exceptions=True,
                )
                for result in results:
                    if isinstance(result, BaseException):
                        raise result
                raw: dict[str, Any] = {
                    "version": "template-analysis.graph.v1",
                    "roles": {r: [] for r in ROLE_NAMES},
                    "headings": [],
                    "fields": [],
                    "tables": [],
                    "unresolved": [],
                }
                for result in results:
                    if isinstance(result, BaseException):
                        raise result
                    for role, refs in result["roles"].items():
                        raw["roles"][role] += refs
                    raw["unresolved"] += result["unresolved"]
                for ref in page["lockedRoles"]:
                    raw["roles"][page["refs"][ref]["role"]].append(ref)
            await asyncio.to_thread(self.validated_page, sid, token, aid, page, raw)
        except AppError as error:
            await asyncio.to_thread(
                self.repository.fail, aid, page["pageKey"], error.code, error.detail
            )
        except Exception:
            logger.exception("Template analysis page failed: %s", aid)
            await asyncio.to_thread(
                self.repository.fail,
                aid,
                page["pageKey"],
                "ANALYSIS_FAILED",
                "解析を完了できませんでした。",
            )

    def finish(self, aid: str) -> None:
        with self.database.transaction() as session:
            run = session.get(AnalysisRun, aid)
            if not run or run.state not in ACTIVE:
                return
            pages = self.repository.pages(session, run)
            good = [p.proposal_json for p in pages if p.proposal_json]
            complete = bool(pages) and all(p.state == "ready" for p in pages)
            some = any(p.state in {"ready", "partial"} for p in pages)
            run.state = "ready" if complete else "partial" if some else "failed"
            units = [u for p in good for u in p["units"]]
            unresolved = [v for p in good for v in p["unresolved"]]
            run.proposal_json = {
                "proposalDigest": digest([run.snapshot_digest, units, unresolved]),
                "units": units,
                "unresolved": unresolved,
                "coverage": {
                    "pages": len(pages),
                    "validatedPages": sum(p.state in {"ready", "partial"} for p in pages),
                    "cells": sum(p["coverage"]["cells"] for p in good),
                },
                "focus": self.combined_focus(good),
            }
            if run.state == "failed":
                run.error_json = {
                    "code": "ANALYSIS_FAILED",
                    "message": "解析できたページがありません。",
                }
            self.repository.emit(
                session, run, run.state, {"proposalDigest": run.proposal_json["proposalDigest"]}
            )

    @staticmethod
    def combined_focus(pages: list[dict[str, Any]]) -> dict[str, Any] | None:
        focuses = [p["focus"] for p in pages if p.get("focus")]
        if not focuses:
            return None
        first = cast(dict[str, Any], copy.deepcopy(focuses[0]))
        anchors = {a["anchorId"]: a for f in focuses for a in f["anchors"]}
        first["anchors"] = list(anchors.values())
        return first

    def claim_execution(self, aid: str) -> bool:
        with self.database.transaction() as session:
            run = session.get(AnalysisRun, aid)
            if not run or run.state != "queued":
                return False
            run.state = "running"
            return True

    async def execute(self, sid: str, token: str, aid: str) -> None:
        if not await asyncio.to_thread(self.claim_execution, aid):
            return
        try:
            _, _, snapshot, task = await asyncio.to_thread(self.work, sid, token, aid)
            calls = sum(p["plannedCalls"] for p in snapshot["pages"])
            limit = DRAFT_TIMEOUT_SECONDS if task == "draft" else 60 * ((calls + 1) // 2) + 30
            async with asyncio.timeout(min(1800, limit)):
                await asyncio.gather(
                    *(self.process_page(sid, token, aid, page, task) for page in snapshot["pages"])
                )
            await asyncio.to_thread(self.finish, aid)
        except Exception as error:
            if not isinstance(error, TimeoutError | AppError):
                logger.exception("Template analysis run failed: %s", aid)
            with self.database.transaction() as session:
                run = session.get(AnalysisRun, aid)
                if run and run.state in ACTIVE:
                    run.state = "failed"
                    run.error_json = {
                        "code": error.code
                        if isinstance(error, AppError)
                        else (
                            "PROVIDER_TIMEOUT"
                            if isinstance(error, TimeoutError)
                            else "ANALYSIS_FAILED"
                        ),
                        "message": "解析を継続できませんでした。",
                    }
                    self.repository.emit(session, run, "failed", {"error": run.error_json})

    def merge(self, sid: str, token: str, aid: str, body: Any) -> dict[str, Any]:
        value = body.model_dump(mode="json", by_alias=True)
        fingerprint = digest(value)
        with self.database.transaction() as session:
            owner, run = self.repository.owned(session, sid, token, aid)
            prior = session.scalar(
                select(AnalysisMerge).where(
                    AnalysisMerge.run_id == aid, AnalysisMerge.request_id == body.request_id
                )
            )
            if prior:
                if prior.fingerprint != fingerprint:
                    raise reject("IDEMPOTENCY_CONFLICT", "同じ反映要求IDの内容が異なります。", 409)
                if prior.result_json.get("reverted"):
                    raise reject("MERGE_CHAIN_STALE", "この反映は取り消されています。", 409)
                return copy.deepcopy(prior.result_json)
            catalog = self.repository.check_current(session, owner, run)
            self.editing.repository.binding(session, owner, catalog, {"binding": value["binding"]})
            current_proposal = live_proposal(
                self.repository.pages(session, run), run.snapshot_digest
            )
            if run.state not in ACTIVE | READABLE or current_proposal is None:
                raise reject("PROPOSAL_STALE", "反映するAI案が一致しません。", 409)
            history = chain(session, aid, body.parent_merge_id)
            applied = {u for receipt in history for u in receipt.result_json["appliedUnitIds"]}
            snapshot = copy.deepcopy(run.snapshot_json)
            units = copy.deepcopy(current_proposal["units"])
            unresolved = copy.deepcopy(current_proposal["unresolved"])
            previous_draft = copy.deepcopy(
                history[-1].result_json["draft"] if history else snapshot["draft"]
            )
        draft = value["draft"]
        if physical(draft["layout"]) != physical(previous_draft["layout"]):
            raise reject("SNAPSHOT_STALE", "枠の変更後は解析をやり直してください。", 409)
        if len(set(body.selected_unit_ids)) != len(body.selected_unit_ids) or not set(
            body.selected_unit_ids
        ) <= {u["unitId"] for u in units}:
            raise reject("INVALID_ARGUMENT", "反映単位が不明又は重複しています。", 422)
        if set(body.selected_unit_ids) & applied or set(body.unit_digests) != set(
            body.selected_unit_ids
        ):
            raise reject("PROPOSAL_STALE", "反映済み又は対象の異なる案です。", 409)
        if any(
            body.unit_digests[u["unitId"]] != u["proposalDigest"]
            for u in units
            if u["unitId"] in body.selected_unit_ids
        ):
            raise reject("PROPOSAL_STALE", "領域案の内容が一致しません。", 409)
        current = facets(draft["layout"])
        manual = touches(value["manualTouchedFacets"], catalog, draft["layout"])
        selected = []
        conflicts = []
        for u in units:
            if u["unitId"] not in body.selected_unit_ids:
                continue
            conflict = (
                u["state"] != "mergeable"
                or bool(set(u["readSet"]) & manual)
                or any(digest(current.get(k)) != v for k, v in u["readSet"].items())
            )
            if conflict:
                conflicts.append({"unitId": u["unitId"], "code": "MANUAL_CONFLICT"})
            else:
                selected.append(u)
        if not selected:
            raise reject("MANUAL_CONFLICT", "手動設定と競合するため反映していません。", 409)
        for page in snapshot["pages"]:
            if (
                hashlib.sha256(self.storage.read_bytes(page["imagePath"])).hexdigest()
                != page["imageSha256"]
            ):
                raise reject("SNAPSHOT_STALE", "基準画像が変わりました。", 409)
        protected = {k for p in snapshot["pages"] for k in p["protectedFacets"]}
        plan, substitutions, ai_before = compose(
            self.editing, owner, catalog, draft, selected, body.request_id, protected
        )
        output = draft_from(plan)
        from app.editing.infrastructure.mutation_uow import BusinessMutationUnitOfWork

        focus = BusinessMutationUnitOfWork.focus(owner, catalog, plan)
        prior_facets = facets(ai_before)
        after_facets = facets(output["layout"])
        changed = {
            k: digest(after_facets.get(k))
            for k in set(prior_facets) | set(after_facets)
            if prior_facets.get(k) != after_facets.get(k)
        }
        with self.database.transaction() as session:
            owner, run = self.repository.owned(session, sid, token, aid)
            self.repository.check_current(session, owner, run)
            prior = session.scalar(
                select(AnalysisMerge).where(
                    AnalysisMerge.run_id == aid, AnalysisMerge.request_id == body.request_id
                )
            )
            if prior:
                if prior.fingerprint != fingerprint:
                    raise reject("IDEMPOTENCY_CONFLICT", "同じ反映要求IDの内容が異なります。", 409)
                if prior.result_json.get("reverted"):
                    raise reject("MERGE_CHAIN_STALE", "この反映は取り消されています。", 409)
                return copy.deepcopy(prior.result_json)
            # BEGIN IMMEDIATE serializes the final receipt check and insertion.
            chain(session, aid, body.parent_merge_id)
            merge = AnalysisMerge(
                run_id=aid,
                request_id=body.request_id,
                fingerprint=fingerprint,
                result_json={},
                facet_json=changed,
            )
            session.add(merge)
            session.flush()
            result = {
                "contractVersion": "template-analysis.v3",
                "parentMergeId": body.parent_merge_id,
                "analysisId": aid,
                "mergeId": merge.id,
                "baseRevision": run.base_revision,
                "inputDraftDigest": digest(draft),
                "outputDraftDigest": digest(output),
                "draft": output,
                "appliedUnitIds": [u["unitId"] for u in selected],
                "conflicts": conflicts,
                "unresolved": unresolved,
                "focus": focus,
                "substitutions": substitutions,
            }
            merge.result_json = result
            return copy.deepcopy(result)

    def revert_merge(
        self, sid: str, token: str, aid: str, merge_id: str, body: Any
    ) -> dict[str, Any]:
        with self.database.transaction() as session:
            owner, run = self.repository.owned(session, sid, token, aid)
            from app.template_analysis.runtime_contract import check_visual_contract

            check_visual_contract(run.snapshot_json)
            catalog = self.editing.sessions.catalogs.build(session, owner)
            self.editing.repository.binding(
                session, owner, catalog, {"binding": body.binding.model_dump(by_alias=True)}
            )
            row = session.get(AnalysisMerge, merge_id)
            if not row or row.run_id != aid:
                raise reject("ANALYSIS_MERGE_INVALID", "取り消す反映記録が一致しません。", 409)
            if row.result_json.get("reverted"):
                if row.result_json.get("revertRequestId") != body.request_id:
                    raise reject("IDEMPOTENCY_CONFLICT", "取り消し要求が一致しません。", 409)
            else:
                chain(session, aid, merge_id)
                row.result_json = {
                    **row.result_json,
                    "reverted": True,
                    "revertRequestId": body.request_id,
                }
            return self.repository.result(session, run)

    async def close(self) -> None:
        pending = list(self.tasks.values())
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
