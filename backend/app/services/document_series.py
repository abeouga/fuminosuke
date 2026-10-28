"""Document series: reading a paper document again as the next version of a confirmed job.

The user explicitly picks the earlier confirmed job (never matched automatically). The new job
is linked to the predecessor's confirmed snapshot it was based on; the predecessor itself is
never modified and keeps its confirmed history. Only the newest confirmed version of a series
is used for questions and lists.

Stage 2 (2026-10-17): the link, candidate listing, confirm/reopen/delete checks and the
"latest only" view. Stage 3 (2026-10-18): mode "blank_only" fixes at acceptance which mappings
keep the predecessor's confirmed reading ("carried") and which are read again (previously blank
fields and every document identity field). Carrying is bound to the same mappingId/targetPath of
the same template version and fieldMappingHash; values outside the mappings are never carried.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from typing import Any

from sqlalchemy import inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.db.models import (
    ConfirmedResult,
    DocumentSeriesLink,
    OcrResult,
    ProcessingJob,
    TemplateVersion,
)
from app.db.session import Database
from app.domain.document_identity import identity_text
from app.domain.enums import JobStatus, JobType
from app.domain.field_targets import field_key
from app.template_packages.canonical_json import canonical_json_bytes

SNAPSHOT_KEY = "documentSeries"
MODE_FULL = "full"
MODE_BLANK_ONLY = "blank_only"
CARRIED_FIELDS = ("raw", "normalized", "status", "alternatives", "reviewReason")
# A cancelled successor no longer holds its predecessor; every other state does (a failed
# job can still be retried), so the user deletes or cancels it before updating again.
RELEASED_STATES = frozenset({JobStatus.CANCELLED.value})


def confirmed_sha256(confirmed_json: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json_bytes(confirmed_json)).hexdigest()


def _error(status: int, code: str, detail: str) -> AppError:
    return AppError(status=status, code=code, title="Document series", detail=detail)


_MIGRATED: set[str] = set()


def _migrated(session: Session) -> bool:
    """Whether this database has the series table (alembic document_series_v1).

    Without it nothing can be linked, so every check passes and lists are unchanged: an
    unmigrated database keeps the existing confirm/reopen/delete behaviour. Only accepting
    an update is refused. A positive answer is cached per database URL.
    """
    bind = session.get_bind()
    key = str(bind.engine.url)
    if key in _MIGRATED:
        return True
    if inspect(bind).has_table(DocumentSeriesLink.__tablename__):
        _MIGRATED.add(key)
        return True
    return False


def _reading_value(reading: object) -> str | None:
    if not isinstance(reading, dict):
        return None
    for key in ("normalized", "raw"):
        value = reading.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _raw_value(reading: object) -> str | None:
    raw = reading.get("raw") if isinstance(reading, dict) else None
    return raw if isinstance(raw, str) and raw else None


def carried_readings(
    fields: dict[str, Any], field_mappings: object, identity_mapping_ids: set[str]
) -> dict[str, dict[str, Any]]:
    """Confirmed readings to keep, keyed by mappingId (blank_only).

    A mapping is carried only when the predecessor's confirmed reading for the same field key
    is bound to the same mappingId and is not blank. Identity fields are always read again so
    the user can compare them with the predecessor.
    """
    carried: dict[str, dict[str, Any]] = {}
    for mapping in field_mappings if isinstance(field_mappings, list) else []:
        if not isinstance(mapping, dict) or mapping.get("enabled") is False:
            continue
        mapping_id, target_path = mapping.get("mappingId"), mapping.get("targetPath")
        if not isinstance(mapping_id, str) or not isinstance(target_path, str):
            continue
        key = field_key(target_path)
        previous = fields.get(key) if key is not None else None
        if (
            mapping_id in identity_mapping_ids
            or not isinstance(previous, dict)
            or previous.get("status") not in ("readable", "uncertain")
            or previous.get("sourceRegionId") != mapping_id
        ):
            continue
        carried[mapping_id] = {
            "targetPath": target_path,
            **{name: previous.get(name) for name in CARRIED_FIELDS},
        }
        carried[mapping_id]["alternatives"] = list(previous.get("alternatives") or [])
    return carried


def _is_paged(job: ProcessingJob) -> bool:
    return "jobPages" in job.config_snapshot_json


class DocumentSeries:
    """Series queries and checks that run inside the caller's session/transaction."""

    def __init__(self, session: Session) -> None:
        self._session = session
        self._enabled = _migrated(session)

    # ---- reading ---------------------------------------------------------------------

    def link_of(self, job_id: str) -> DocumentSeriesLink | None:
        if not self._enabled:
            return None
        return self._session.get(DocumentSeriesLink, job_id)

    def successor_of(self, job_id: str) -> tuple[DocumentSeriesLink, ProcessingJob] | None:
        if not self._enabled:
            return None
        link = self._session.scalar(
            select(DocumentSeriesLink).where(DocumentSeriesLink.predecessor_job_id == job_id)
        )
        if link is None:
            return None
        job = self._session.get(ProcessingJob, link.job_id)
        assert job is not None  # job_id is a cascading FK
        return link, job

    def view(self) -> tuple[set[str], dict[str, tuple[str, int]]]:
        """(jobs superseded by a confirmed successor, job -> (seriesId, version number))."""
        if not self._enabled:
            return set(), {}
        rows = self._session.execute(
            select(
                DocumentSeriesLink.job_id,
                DocumentSeriesLink.predecessor_job_id,
                DocumentSeriesLink.series_id,
                ProcessingJob.status,
            ).join(ProcessingJob, ProcessingJob.id == DocumentSeriesLink.job_id)
        ).all()
        superseded = {
            predecessor for _, predecessor, _, status in rows if status == JobStatus.CONFIRMED.value
        }
        parent = {job: predecessor for job, predecessor, _, _ in rows}
        versions: dict[str, tuple[str, int]] = {}
        for job, _, series, _ in rows:
            depth, current = 1, job
            while current in parent:
                current, depth = parent[current], depth + 1
            versions[job] = (series, depth)
            versions.setdefault(current, (series, 1))
        return superseded, versions

    # ---- accepting an update ---------------------------------------------------------

    def snapshot_for(
        self,
        predecessor_job_id: str,
        *,
        template_version_id: str,
        field_mapping_hash: object,
        field_mappings: object = (),
        identity_fields: Sequence[tuple[str, str, str]] = (),
    ) -> dict[str, Any]:
        """Validate the chosen predecessor and describe it for the new job's snapshot."""
        if not self._enabled:
            raise _error(
                503,
                "SERIES_NOT_MIGRATED",
                "書類の更新を使うにはデータベースの移行が必要です。",
            )
        job = self._session.get(ProcessingJob, predecessor_job_id)
        if job is None or job.job_type != JobType.EXTRACT_DOCUMENT.value:
            raise _error(404, "SERIES_PREDECESSOR_NOT_FOUND", "更新元の書類が見つかりません。")
        if _is_paged(job):
            raise _error(
                409, "SERIES_PAGED_UNSUPPORTED", "複数ページの書類はまだ更新として読み込めません。"
            )
        confirmed = self._confirmed(job)
        if confirmed is None:
            raise _error(
                409, "SERIES_PREDECESSOR_NOT_CONFIRMED", "更新元は確定済みの書類だけです。"
            )
        snapshot = job.config_snapshot_json
        if (
            job.template_version_id != template_version_id
            or snapshot.get("fieldMappingHash") != field_mapping_hash
        ):
            raise _error(
                409,
                "SERIES_TEMPLATE_MISMATCH",
                "更新元とテンプレートの版が違うため、更新として読み込めません。",
            )
        self._require_free(job.id)
        link = self.link_of(job.id)
        fields = confirmed.confirmed_json.get("fields")
        fields = fields if isinstance(fields, dict) else {}
        identity_ids = {mapping_id for mapping_id, _, _ in identity_fields}
        return {
            "predecessorJobId": job.id,
            "seriesId": link.series_id if link is not None else job.id,
            "predecessorRevision": confirmed.ocr_result_revision,
            "predecessorConfirmedSha256": confirmed_sha256(confirmed.confirmed_json),
            "predecessorDocumentName": confirmed.document_name,
            "mode": MODE_BLANK_ONLY,
            "carried": carried_readings(fields, field_mappings, identity_ids),
            "predecessorIdentity": [
                {
                    "mappingId": mapping_id,
                    "targetPath": target_path,
                    "label": label,
                    "value": _reading_value(fields.get(field_key(target_path) or "")),
                    "raw": _raw_value(fields.get(field_key(target_path) or "")),
                }
                for mapping_id, target_path, label in identity_fields
            ],
        }

    def attach(self, job: ProcessingJob, series: dict[str, Any]) -> None:
        """Link a just-created job in the same transaction; re-checks the predecessor."""
        predecessor = self._session.get(ProcessingJob, series["predecessorJobId"])
        confirmed = self._confirmed(predecessor) if predecessor is not None else None
        if (
            confirmed is None
            or confirmed.ocr_result_revision != series["predecessorRevision"]
            or confirmed_sha256(confirmed.confirmed_json) != series["predecessorConfirmedSha256"]
        ):
            raise _error(
                409,
                "SERIES_PREDECESSOR_CHANGED",
                "更新元の書類が変更されました。選び直してください。",
            )
        self._require_free(series["predecessorJobId"], release=True)
        self._session.add(
            DocumentSeriesLink(
                job_id=job.id,
                predecessor_job_id=series["predecessorJobId"],
                series_id=series["seriesId"],
                predecessor_revision=series["predecessorRevision"],
                predecessor_confirmed_sha256=series["predecessorConfirmedSha256"],
                mode=series["mode"],
            )
        )
        try:
            self._session.flush()
        except IntegrityError:
            self._session.rollback()
            raise _error(
                409, "SERIES_SUCCESSOR_EXISTS", "この書類は別の読み取りで更新中です。"
            ) from None

    # ---- review ----------------------------------------------------------------------

    def review_info(self, job: ProcessingJob) -> dict[str, Any] | None:
        """What the review screen shows for an update: predecessor, carried fields, identity."""
        series = job.config_snapshot_json.get(SNAPSHOT_KEY)
        if not isinstance(series, dict):
            return None
        carried = series.get("carried")
        _, versions = self.view()
        return {
            "predecessorJobId": series.get("predecessorJobId"),
            "predecessorDocumentName": series.get("predecessorDocumentName"),
            "seriesVersion": versions.get(job.id, (job.id, 1))[1],
            "mode": series.get("mode"),
            "carriedPaths": sorted(
                spec["targetPath"]
                for spec in (carried.values() if isinstance(carried, dict) else [])
                if isinstance(spec, dict) and isinstance(spec.get("targetPath"), str)
            ),
            "predecessorIdentity": [
                {key: item.get(key) for key in ("targetPath", "label", "value", "raw")}
                for item in series.get("predecessorIdentity") or []
                if isinstance(item, dict)
            ],
        }

    @staticmethod
    def identity_mismatches(job: ProcessingJob, document: dict[str, Any]) -> list[str]:
        """Labels whose identity differs from the predecessor's (spacing/width ignored).

        The same paper read again yields the predecessor's raw text, while its confirmed value
        may have been corrected by the user; matching either one counts as the same document.
        Only the fields with a value in both versions are compared (one blank on either side,
        e.g. a birth date written in later, is no difference); when no field can be compared,
        nothing shows it is the same document and the predecessor's fields with a value are
        reported.
        """
        series = job.config_snapshot_json.get(SNAPSHOT_KEY)
        if not isinstance(series, dict):
            return []
        fields = document.get("fields")
        fields = fields if isinstance(fields, dict) else {}
        items = [item for item in series.get("predecessorIdentity") or [] if isinstance(item, dict)]
        differing: list[str] = []
        compared = False
        for item in items:
            reading = fields.get(field_key(str(item.get("targetPath"))) or "")
            now = identity_text(_reading_value(reading))
            before = {identity_text(item.get("value")), identity_text(item.get("raw"))} - {""}
            if not now or not before:
                continue
            compared = True
            if now not in before:
                differing.append(str(item.get("label")))
        if compared:
            return differing
        known = [
            item for item in items
            if identity_text(item.get("value")) or identity_text(item.get("raw"))
        ]
        return [str(item.get("label")) for item in known or items]

    # ---- lifecycle checks ------------------------------------------------------------

    def require_predecessor_unchanged(self, job: ProcessingJob) -> None:
        """Confirming an update requires the predecessor still as it was when accepted."""
        link = self.link_of(job.id)
        if link is None:
            return
        predecessor = self._session.get(ProcessingJob, link.predecessor_job_id)
        confirmed = self._confirmed(predecessor) if predecessor is not None else None
        if (
            confirmed is None
            or confirmed.ocr_result_revision != link.predecessor_revision
            or confirmed_sha256(confirmed.confirmed_json) != link.predecessor_confirmed_sha256
        ):
            raise _error(
                409,
                "SERIES_PREDECESSOR_CHANGED",
                "更新元の書類が読み取り後に変更されたため確定できません。",
            )

    def require_no_successor(self, job: ProcessingJob, action: str) -> None:
        """Reopening or deleting an older version would fork or cut the series."""
        found = self.successor_of(job.id)
        if found is None or found[1].status in RELEASED_STATES:
            return
        raise _error(
            409,
            "SERIES_HAS_SUCCESSOR",
            f"この書類には新しい版があるため{action}できません。新しい版を操作してください。",
        )

    def release_for_delete(self, job: ProcessingJob) -> None:
        """Before deleting a predecessor, drop a link held only by a cancelled successor."""
        self.require_no_successor(job, "削除")
        found = self.successor_of(job.id)
        if found is not None:
            self._session.delete(found[0])
            self._session.flush()

    # ---- helpers ---------------------------------------------------------------------

    def _confirmed(self, job: ProcessingJob) -> ConfirmedResult | None:
        """Authoritative confirmed snapshot: CONFIRMED status and matching revision."""
        if job.status != JobStatus.CONFIRMED.value:
            return None
        stored = self._session.scalar(select(OcrResult).where(OcrResult.job_id == job.id))
        confirmed = self._session.scalar(
            select(ConfirmedResult).where(ConfirmedResult.job_id == job.id)
        )
        if stored is None or confirmed is None or confirmed.ocr_result_revision != stored.revision:
            return None
        return confirmed

    def _require_free(self, predecessor_job_id: str, *, release: bool = False) -> None:
        found = self.successor_of(predecessor_job_id)
        if found is None:
            return
        link, successor = found
        if successor.status not in RELEASED_STATES:
            raise _error(
                409,
                "SERIES_SUCCESSOR_EXISTS",
                "この書類には新しい版か更新中の読み取りがあります。"
                "不要な読み取りは削除してから選び直してください。",
            )
        if release:
            self._session.delete(link)
            self._session.flush()


def series_candidates(
    database: Database, *, template_version_id: str, query: str | None, limit: int = 200
) -> list[dict[str, Any]]:
    """Latest confirmed versions of the template's documents the user can pick to update.

    Documents of another template version are listed but not selectable, so the user sees
    why a document is missing instead of silently getting a different one.
    """
    from app.structured.service import StructuredDocumentService

    with database.session() as session:
        version = session.get(TemplateVersion, template_version_id)
        if version is None:
            raise _error(404, "TEMPLATE_NOT_FOUND", "テンプレートが見つかりません。")
        template_id = version.template_id
    summaries = StructuredDocumentService(database).list(
        limit=limit, template_id=template_id, query=query
    )
    items: list[dict[str, Any]] = []
    with database.session() as session:
        series = DocumentSeries(session)
        for summary in summaries:
            if summary["kind"] != "single":
                continue
            reason: str | None = None
            if summary["templateVersionId"] != template_version_id:
                reason = "テンプレートの版が違うため更新にできません。"
            else:
                found = series.successor_of(summary["jobId"])
                if found is not None and found[1].status not in RELEASED_STATES:
                    reason = "更新中の読み取りがあります。"
            items.append({**summary, "selectable": reason is None, "reason": reason})
    return items
