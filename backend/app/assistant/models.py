from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, CheckConstraint, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.models import utc_now, uuid_string


class AssistantSession(Base):
    __tablename__ = "assistant_sessions"
    __table_args__ = (CheckConstraint("context_revision >= 1"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_string)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    context_revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    context_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class AssistantProposal(Base):
    __tablename__ = "assistant_proposals"
    __table_args__ = (
        CheckConstraint(
            "state IN ('PROPOSED','PRESENTED','CONSUMED','CANCELLED','SUPERSEDED','EXPIRED')"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_string)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("assistant_sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    context_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    context_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    plan_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    diff_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(String(20), default="PROPOSED", nullable=False)
    challenge_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    presented_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    consumed_request_key: Mapped[str | None] = mapped_column(
        ForeignKey("mutation_requests.key"), nullable=True
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
