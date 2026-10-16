from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.models import utc_now, uuid_string


class EditingSession(Base):
    __tablename__ = "editing_sessions"
    __table_args__ = (CheckConstraint("context_revision >= 1"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_string)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    resource_kind: Mapped[str] = mapped_column(String(40), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    view_instance_id: Mapped[str] = mapped_column(String(100), nullable=False)
    mode: Mapped[str] = mapped_column(String(20), nullable=False)
    context_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    context_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class EditingTurn(Base):
    __tablename__ = "editing_turns"
    __table_args__ = (
        UniqueConstraint("session_id", "sequence", name="uq_editing_turn_sequence"),
        CheckConstraint("state IN ('RESOLVING','READY','CANCELLED','CONSUMED')"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_string)
    session_id: Mapped[str] = mapped_column(ForeignKey("editing_sessions.id"), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    context_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    binding_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    state: Mapped[str] = mapped_column(String(20), nullable=False, default="RESOLVING")
    consumed_request_id: Mapped[str | None] = mapped_column(String(128), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class EditingOperation(Base):
    """New history metadata without changing legacy ResourceOperation reads."""

    __tablename__ = "editing_operations"

    operation_id: Mapped[str] = mapped_column(
        ForeignKey("resource_operations.id"),
        primary_key=True,
    )
    contract_version: Mapped[str] = mapped_column(String(40), nullable=False)
    origin: Mapped[str] = mapped_column(String(30), nullable=False)
    turn_id: Mapped[str | None] = mapped_column(ForeignKey("editing_turns.id"))
