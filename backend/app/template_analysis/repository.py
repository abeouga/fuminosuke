"""Durable run ownership, state transitions and sequence-based event replay."""

from __future__ import annotations

import copy
from typing import Any

from sqlalchemy import select

from app.core.errors import AppError
from app.db.models import utc_now
from app.editing.errors import reject
from app.editing.infrastructure.sessions import aware
from app.template_analysis.graph import public_units
from app.template_analysis.merge_chain import proposal as live_proposal
from app.template_analysis.models import AnalysisEvent, AnalysisPage, AnalysisRun
from app.template_analysis.runtime_contract import check_visual_contract

ACTIVE = {"queued", "running", "validating"}
READABLE = {"ready", "partial"}


class AnalysisRepository:
    def __init__(self, editing: Any) -> None:
        self.editing = editing
        self.database = editing.sessions.database
        with self.database.transaction() as session:
            for run in session.scalars(select(AnalysisRun).where(AnalysisRun.state.in_(ACTIVE))):
                run.state = "failed"
                run.error_json = {
                    "code": "INTERRUPTED",
                    "message": "再起動で未完了の解析を終了しました。",
                }
                for page in self.pages(session, run):
                    if page.state in {"queued", "running"}:
                        page.state = "failed"
                        page.error_json = copy.deepcopy(run.error_json)
                self.emit(session, run, "failed", {"error": run.error_json})

    @staticmethod
    def emit(session: Any, run: AnalysisRun, kind: str, data: dict[str, Any]) -> None:
        run.sequence += 1
        session.add(
            AnalysisEvent(
                run_id=run.id,
                sequence=run.sequence,
                payload_json={
                    "contractVersion": "template-analysis.v3",
                    "analysisId": run.id,
                    "snapshotDigest": run.snapshot_digest,
                    "sequence": run.sequence,
                    "type": kind,
                    "emittedAt": utc_now().isoformat(),
                    **data,
                },
            )
        )

    def owned(self, session: Any, sid: str, token: str, aid: str) -> tuple[Any, AnalysisRun]:
        owner = self.editing.sessions.owner(session, sid, token)
        run = session.get(AnalysisRun, aid)
        if not run or run.session_id != owner.id:
            raise reject("ANALYSIS_NOT_FOUND", "この編集sessionの解析ではありません。", 404)
        return owner, run

    def expire(self, session: Any, run: AnalysisRun) -> None:
        if run.state in ACTIVE | READABLE and aware(run.expires_at) <= utc_now():
            run.state = "expired"
            self.emit(session, run, "expired", {})

    def check_current(self, session: Any, owner: Any, run: AnalysisRun) -> Any:
        self.expire(session, run)
        if run.state not in ACTIVE | READABLE:
            raise reject("ANALYSIS_UNAVAILABLE", "解析が終了又は失効しています。", 409)
        check_visual_contract(run.snapshot_json)
        catalog = self.editing.sessions.catalogs.build(session, owner)
        self.editing.repository.binding(
            session, owner, catalog, {"binding": run.snapshot_json["binding"]}
        )
        return catalog

    @staticmethod
    def pages(session: Any, run: AnalysisRun) -> list[AnalysisPage]:
        return list(
            session.scalars(
                select(AnalysisPage)
                .where(AnalysisPage.run_id == run.id)
                .order_by(AnalysisPage.page_key)
            )
        )

    def result(self, session: Any, run: AnalysisRun) -> dict[str, Any]:
        # A finished run from a retired contract stays readable so a client can
        # settle its journal, but carries no proposal that could be applied.
        retired = False
        try:
            check_visual_contract(run.snapshot_json)
        except AppError:
            if run.state in ACTIVE | READABLE:
                raise
            retired = True
        pages = self.pages(session, run)
        snapshot = run.snapshot_json
        proposal = None if retired else copy.deepcopy(live_proposal(pages, run.snapshot_digest))
        if proposal:
            proposal["units"] = public_units(proposal["units"])
        return {
            "contractVersion": "template-analysis.v3",
            "regionScope": snapshot.get("regionScope"),
            "analysisId": run.id,
            "requestId": run.request_id,
            "resource": {"kind": "template", "id": run.resource_id},
            "baseRevision": run.base_revision,
            "snapshotDigest": run.snapshot_digest,
            "draftDigest": snapshot["draftDigest"],
            "scopeDigest": snapshot["binding"]["scopeDigest"],
            "state": run.state,
            "pages": [
                {
                    "pageId": None if p.page_key == "legacy" else p.page_key,
                    "state": p.state,
                    "plannedCalls": p.planned_calls,
                    "reservedCalls": p.reserved_calls,
                    "completedCalls": p.completed_calls,
                    "metrics": p.metrics_json,
                    "error": p.error_json,
                    "proposal": {
                        **copy.deepcopy(p.proposal_json),
                        "units": public_units(p.proposal_json["units"]),
                    }
                    if p.proposal_json and not retired
                    else None,
                }
                for p in pages
            ],
            "plannedCalls": sum(p.planned_calls for p in pages),
            "reservedCalls": sum(p.reserved_calls for p in pages),
            "completedCalls": sum(p.completed_calls for p in pages),
            "expiresAt": aware(run.expires_at).isoformat(),
            "lastSequence": run.sequence,
            "proposal": proposal,
            "error": run.error_json,
        }

    def get(self, sid: str, token: str, aid: str) -> dict[str, Any]:
        with self.database.transaction() as session:
            owner, run = self.owned(session, sid, token, aid)
            self.expire(session, run)
            if run.state in READABLE | ACTIVE:
                try:
                    self.check_current(session, owner, run)
                except Exception as error:
                    from app.core.errors import AppError

                    if not isinstance(error, AppError):
                        raise
                    run.state = "stale"
                    self.emit(
                        session,
                        run,
                        "stale",
                        {"error": {"code": error.code, "message": error.detail}},
                    )
            return self.result(session, run)

    def events(
        self, sid: str, token: str, aid: str, after: int
    ) -> tuple[list[dict[str, Any]], str]:
        with self.database.transaction() as session:
            _, run = self.owned(session, sid, token, aid)
            self.expire(session, run)
            return [
                copy.deepcopy(e.payload_json)
                for e in session.scalars(
                    select(AnalysisEvent)
                    .where(AnalysisEvent.run_id == aid, AnalysisEvent.sequence > after)
                    .order_by(AnalysisEvent.sequence)
                    .limit(200)
                )
            ], run.state

    def cancel(self, sid: str, token: str, aid: str) -> dict[str, Any]:
        with self.database.transaction() as session:
            _, run = self.owned(session, sid, token, aid)
            if run.state in ACTIVE:
                run.state = "cancelled"
                self.emit(session, run, "cancelled", {})
            return self.result(session, run)

    def reserve(self, aid: str, page_key: str, ledger_id: str) -> bool:
        with self.database.transaction() as session:
            run = session.get(AnalysisRun, aid)
            page = session.scalar(
                select(AnalysisPage).where(
                    AnalysisPage.run_id == aid, AnalysisPage.page_key == page_key
                )
            )
            page.reserved_calls += 1
            page.metrics_json = {
                **page.metrics_json,
                "ledgerIds": [*page.metrics_json.get("ledgerIds", []), ledger_id],
            }
            self.expire(session, run)
            return run.state in ACTIVE

    def settled(self, aid: str, page_key: str, metrics: dict[str, Any]) -> None:
        with self.database.transaction() as session:
            page = session.scalar(
                select(AnalysisPage).where(
                    AnalysisPage.run_id == aid, AnalysisPage.page_key == page_key
                )
            )
            page.completed_calls += 1
            page.metrics_json = {
                **page.metrics_json,
                "requests": [*page.metrics_json.get("requests", []), metrics],
            }

    def fail(self, aid: str, page_key: str, code: str, message: str) -> None:
        with self.database.transaction() as session:
            run = session.get(AnalysisRun, aid)
            if not run or run.state not in ACTIVE:
                return
            page = session.scalar(
                select(AnalysisPage).where(
                    AnalysisPage.run_id == aid, AnalysisPage.page_key == page_key
                )
            )
            page.state = "failed"
            page.error_json = {"code": code, "message": message}
            self.emit(
                session,
                run,
                "progress",
                {
                    "pageId": None if page_key == "legacy" else page_key,
                    "state": "failed",
                    "error": page.error_json,
                },
            )
