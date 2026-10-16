from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.models import utc_now, uuid_string


class AssistantTurn(Base):
    """Request ownership and minimal outcome; never raw audio or conversation text."""

    __tablename__ = "assistant_turns"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_string)
    request_key: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    session_id: Mapped[str] = mapped_column(ForeignKey("assistant_sessions.id"), nullable=False)
    context_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    purpose: Mapped[str] = mapped_column(String(20), nullable=False)
    binding_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    input_hash: Mapped[str | None] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(20), default="RECORDING", nullable=False)
    result_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
