from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.models import utc_now, uuid_string


class ApiCallLedger(Base):
    __tablename__ = "api_call_ledger"
    __table_args__ = (CheckConstraint("ordinal >= 1"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_string)
    ordinal: Mapped[int] = mapped_column(Integer, unique=True, nullable=False)
    purpose: Mapped[str] = mapped_column(String(20), nullable=False)
    state: Mapped[str] = mapped_column(String(20), default="RESERVED", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
