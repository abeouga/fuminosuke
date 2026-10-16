"""Audio metadata only. Recordings, captions and transcripts never enter these tables."""

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


class VoiceStream(Base):
    __tablename__ = "editing_voice_streams"
    __table_args__ = (
        CheckConstraint("reserved_seconds BETWEEN 1 AND 300"),
        CheckConstraint("charged_seconds BETWEEN 1 AND reserved_seconds"),
        CheckConstraint("state IN ('CREATED','CONNECTED','CLOSED')"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_string)
    session_id: Mapped[str] = mapped_column(ForeignKey("editing_sessions.id"), index=True)
    state: Mapped[str] = mapped_column(String(20), default="CREATED")
    reserved_seconds: Mapped[int] = mapped_column(Integer)
    charged_seconds: Mapped[int] = mapped_column(Integer)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    connected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    connect_call_id: Mapped[str | None] = mapped_column(ForeignKey("api_call_ledger.id"))


class VoiceAudioItem(Base):
    __tablename__ = "editing_voice_audio_items"
    __table_args__ = (
        UniqueConstraint("stream_id", "audio_id"),
        UniqueConstraint("stream_id", "sequence"),
        CheckConstraint("sample_count BETWEEN 0 AND 720000"),
        CheckConstraint("state IN ('RECORDING','SUBMITTED','COMPLETED','CANCELLED','FAILED')"),
        CheckConstraint("held IN (0,1)"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_string)
    stream_id: Mapped[str] = mapped_column(ForeignKey("editing_voice_streams.id"), index=True)
    audio_id: Mapped[str] = mapped_column(String(100))
    sequence: Mapped[int] = mapped_column(Integer)
    state: Mapped[str] = mapped_column(String(20), default="RECORDING")
    binding_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    sample_count: Mapped[int] = mapped_column(Integer, default=0)
    held: Mapped[int] = mapped_column(Integer, default=1)
    call_id: Mapped[str | None] = mapped_column(ForeignKey("api_call_ledger.id"))
    upstream_item_id: Mapped[str | None] = mapped_column(String(100), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
