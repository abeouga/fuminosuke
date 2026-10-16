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


class CommandRequest(Base):
    __tablename__ = "editing_command_requests"
    __table_args__ = (
        UniqueConstraint("session_id", "request_id", name="uq_editing_command_request"),
        CheckConstraint("fence >= 1"),
        CheckConstraint(
            "state IN ('ACCEPTED','PLANNING','VALIDATING','COMMITTING','APPLIED',"
            "'WAITING_INFORMATION','NO_ACTION','CONFLICT','FAILED','CANCELLED',"
            "'EXPIRED','INTERRUPTED')"
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_string)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("editing_sessions.id"), nullable=False, index=True
    )
    request_id: Mapped[str] = mapped_column(String(128), nullable=False)
    instruction_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    input_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    command_kind: Mapped[str] = mapped_column(String(30), nullable=False)
    resource_kind: Mapped[str] = mapped_column(String(40), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    binding_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    after_command_id: Mapped[str | None] = mapped_column(ForeignKey("editing_command_requests.id"))
    state: Mapped[str] = mapped_column(String(30), nullable=False)
    fence: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    held: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    worker_id: Mapped[str] = mapped_column(String(36), nullable=False)
    change_digest: Mapped[str | None] = mapped_column(String(64))
    receipt_key: Mapped[str | None] = mapped_column(ForeignKey("mutation_requests.key"))
    accepted_event_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    final_event_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
