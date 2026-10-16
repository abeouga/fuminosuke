from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.models import utc_now, uuid_string


class AnalysisRun(Base):
    __tablename__ = "template_analysis_runs"
    __table_args__ = (UniqueConstraint("session_id", "request_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_string)
    session_id: Mapped[str] = mapped_column(ForeignKey("editing_sessions.id"), index=True)
    request_id: Mapped[str] = mapped_column(String(128))
    fingerprint: Mapped[str] = mapped_column(String(64))
    request_aliases: Mapped[dict[str, str]] = mapped_column(JSON, default=dict)
    snapshot_digest: Mapped[str] = mapped_column(String(64))
    resource_id: Mapped[str] = mapped_column(ForeignKey("template_versions.id"), index=True)
    base_revision: Mapped[int] = mapped_column(Integer)
    state: Mapped[str] = mapped_column(String(24))
    task: Mapped[str] = mapped_column(String(10))
    snapshot_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    proposal_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    error_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    sequence: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AnalysisPage(Base):
    __tablename__ = "template_analysis_pages"
    __table_args__ = (UniqueConstraint("run_id", "page_key"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_string)
    run_id: Mapped[str] = mapped_column(ForeignKey("template_analysis_runs.id"), index=True)
    page_key: Mapped[str] = mapped_column(String(128))
    state: Mapped[str] = mapped_column(String(24))
    planned_calls: Mapped[int] = mapped_column(Integer)
    reserved_calls: Mapped[int] = mapped_column(Integer, default=0)
    completed_calls: Mapped[int] = mapped_column(Integer, default=0)
    proposal_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    error_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    metrics_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class AnalysisMerge(Base):
    __tablename__ = "template_analysis_merges"
    __table_args__ = (UniqueConstraint("run_id", "request_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_string)
    run_id: Mapped[str] = mapped_column(ForeignKey("template_analysis_runs.id"), index=True)
    request_id: Mapped[str] = mapped_column(String(128))
    fingerprint: Mapped[str] = mapped_column(String(64))
    result_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    facet_json: Mapped[dict[str, str]] = mapped_column(JSON)


class AuthoringFacet(Base):
    __tablename__ = "template_authoring_facets"
    __table_args__ = (UniqueConstraint("version_id", "entity_id", "facet"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_string)
    version_id: Mapped[str] = mapped_column(ForeignKey("template_versions.id"), index=True)
    entity_id: Mapped[str] = mapped_column(String(128))
    facet: Mapped[str] = mapped_column(String(20))
    origin: Mapped[str] = mapped_column(String(20))
    value_digest: Mapped[str] = mapped_column(String(64))
    revision: Mapped[int] = mapped_column(Integer)
    merge_id: Mapped[str | None] = mapped_column(String(36))


class AnalysisEvent(Base):
    __tablename__ = "template_analysis_events"
    __table_args__ = (UniqueConstraint("run_id", "sequence"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_string)
    run_id: Mapped[str] = mapped_column(ForeignKey("template_analysis_runs.id"), index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON)


TABLES = [
    AnalysisRun.__table__,
    AnalysisPage.__table__,
    AnalysisMerge.__table__,
    AuthoringFacet.__table__,
    AnalysisEvent.__table__,
]
