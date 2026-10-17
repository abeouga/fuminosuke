from __future__ import annotations

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager

import httpx
from openai import APIConnectionError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ai.models import ApiCallLedger
from app.core.errors import AppError
from app.db.session import Database


def refused(detail: str, code: str, status: int) -> AppError:
    return AppError(detail=detail, code=code, status=status, title=code)


@contextmanager
def preserve_reservation_error() -> Iterator[None]:
    """The SDK wraps request-hook failures as connection errors; do not retry our guard."""
    try:
        yield
    except APIConnectionError as error:
        cause = error.__cause__
        if isinstance(cause, AppError) and cause.code in {
            "AI_API_BUDGET_EXHAUSTED",
            "AI_DESTINATION_REJECTED",
            "BETA_JOURNAL_UNAVAILABLE",
            "VOICE_TIME_EXHAUSTED",
        }:
            raise cause from error
        raise


class ApiBudget:
    """Commit a reservation before each actual HTTP send, across all AI providers."""

    # 2026-10-13: 500 calls authorized from 424 already used; keep the ledger intact.
    # 2026-10-17: 300 calls authorized from 445 already used (AI template drafts).
    limit = 1224

    def __init__(self, database: Database) -> None:
        self.database = database
        self.shared = database.api_budget

    def status(self) -> dict[str, int]:
        if self.shared is not None:
            return self.shared.status()
        with self.database.session() as session:
            used = session.scalar(select(func.count()).select_from(ApiCallLedger)) or 0
        return {"used": used, "limit": self.limit, "remaining": self.limit - used}

    def reserve(self, purpose: str) -> str:
        if self.shared is not None:
            return self.shared.reserve(purpose)
        with self.database.transaction() as session:
            return self.reserve_in(session, purpose)

    def reserve_in(self, session: Session, purpose: str) -> str:
        """Share the caller's write transaction with a WS/audio reservation."""
        if self.shared is not None:
            # The durable global receipt survives a later rollback of the business transaction.
            identity = self.shared.reserve(purpose)
            # Existing voice metadata has local ledger foreign keys. This is a reference mirror,
            # not another allowance; shared.status()/reserve() alone authorize paid sending.
            used = session.scalar(select(func.max(ApiCallLedger.ordinal))) or 0
            session.add(ApiCallLedger(id=identity, ordinal=used + 1, purpose=purpose))
            session.flush()
            return identity
        used = session.scalar(select(func.max(ApiCallLedger.ordinal))) or 0
        if used >= self.limit:
            raise refused(
                f"外部AI APIの許可枠{self.limit}回に達しました。追加利用の承認が必要です。",
                "AI_API_BUDGET_EXHAUSTED",
                429,
            )
        row = ApiCallLedger(ordinal=used + 1, purpose=purpose)
        session.add(row)
        session.flush()
        return row.id

    def client(self, purpose: str) -> httpx.AsyncClient:
        async def sending(request: httpx.Request) -> None:
            if request.method != "POST" or request.url.host != "api.openai.com":
                raise refused("許可されていないAI送信先です。", "AI_DESTINATION_REJECTED", 503)
            if self.shared is not None and (request.url.scheme != "https"
                                           or request.url.port not in {None, 443}):
                raise refused("許可されていないAI送信先です。", "AI_DESTINATION_REJECTED", 503)
            request.extensions["api_call_id"] = (
                await asyncio.to_thread(self.reserve, purpose)
                if self.shared is not None else self.reserve(purpose)
            )

        async def received(response: httpx.Response) -> None:
            if self.shared is not None:
                return  # Remote reservation receipts are immutable, including failures.
            with self.database.transaction() as session:
                row = session.get(ApiCallLedger, response.request.extensions["api_call_id"])
                if row is not None:
                    row.state = "RESPONSE" if response.is_success else "ERROR"

        # A lost connection/crash remains RESERVED and still consumes a call.
        return httpx.AsyncClient(
            event_hooks={"request": [sending], "response": [received]}, follow_redirects=False
        )
