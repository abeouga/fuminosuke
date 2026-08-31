from __future__ import annotations

import hashlib
import json
import unicodedata
from datetime import UTC, datetime
from typing import Any, Literal, cast

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.db.models import (
    ConfirmedResult,
    LedgerDocument,
    LedgerDocumentPage,
    OcrResult,
    ProcessingJob,
    Template,
    TemplateVersion,
)
from app.domain.enums import RevisionSource
from app.domain.individual_id import individual_id_from_document
from app.domain.ocr_models import OcrDocument
from app.repositories.ocr_result_repository import OcrResultRepository
from app.validation.business_validators import DeterministicValidator

ResolutionAction = Literal["ADD_PAGE", "RESCAN_PAGE", "NEW_DOCUMENT"]


class DocumentReconciliationService:
    def __init__(self, validator: DeterministicValidator) -> None:
        self._validator = validator

    def payload(
        self,
        session: Session,
        *,
        job: ProcessingJob,
        document_json: dict[str, Any],
    ) -> dict[str, Any]:
        page = session.scalar(select(LedgerDocumentPage).where(LedgerDocumentPage.job_id == job.id))
        if page is not None:
            ledger = session.get(LedgerDocument, page.ledger_document_id)
            previous = ledger.current_document_json if ledger is not None else {}
            return {
                "required": False,
                "resolved": True,
                "action": page.relation_type,
                "documentId": page.ledger_document_id,
                "pageId": page.id,
                "candidates": [],
                "changes": self._changes(previous, document_json)
                if page.relation_type in {"ADD_PAGE", "RESCAN_PAGE"}
                else [],
            }

        template_id = self._template_id(session, job)
        identity = self._individual_id(document_json)
        match_groups = self._match_groups(job)
        combined_paths = self._combined_confirmation_paths(job)
        candidates = (
            self._candidates(
                session,
                template_id=template_id,
                individual_id=identity,
                exclude_job_id=job.id,
                incoming=document_json,
                combined_paths=combined_paths,
                match_groups=match_groups,
            )
            if template_id and (identity or combined_paths or match_groups)
            else []
        )
        return {
            "required": bool(candidates),
            "resolved": False,
            "action": None,
            "documentId": None,
            "pageId": None,
            "candidates": candidates,
            "changes": [],
        }

    def resolve(
        self,
        session: Session,
        *,
        job: ProcessingJob,
        stored: OcrResult,
        action: ResolutionAction,
        target_document_id: str | None,
        target_job_id: str | None,
        target_page_id: str | None,
        configured_paths: set[str] | None,
    ) -> OcrResult:
        if session.scalar(select(LedgerDocumentPage).where(LedgerDocumentPage.job_id == job.id)):
            return stored
        identity = self._individual_id(stored.current_corrected_json)
        template_id = self._template_id(session, job)
        match_groups = self._match_groups(job)
        requires_individual_id = self._requires_individual_identity(
            session,
            job,
            configured_paths,
        )
        if not template_id or (requires_individual_id and not identity):
            raise AppError(
                status=409,
                code="INDIVIDUAL_ID_REQUIRED",
                title="Individual identification number is required",
                detail="個体識別番号を確定してから保存方法を選択してください。",
            )
        document_key = identity or self.document_key(job.id)
        incoming = dict(stored.current_corrected_json)
        if action == "NEW_DOCUMENT":
            identity_snapshot, identity_fingerprint = self.identity_snapshot(
                job,
                incoming,
            )
            ledger = LedgerDocument(
                id=job.id,
                template_id=template_id,
                individual_id=document_key,
                current_job_id=job.id,
                current_document_json=incoming,
                identity_snapshot_json=identity_snapshot or None,
                identity_fingerprint=identity_fingerprint,
            )
            session.add(ledger)
            session.add(
                LedgerDocumentPage(
                    ledger_document_id=ledger.id,
                    job_id=job.id,
                    page_number=1,
                    relation_type=action,
                    page_content_json=incoming,
                )
            )
            session.flush()
            return stored

        ledger = self._target_document(
            session,
            template_id=template_id,
            individual_id=document_key,
            target_document_id=target_document_id,
            target_job_id=target_job_id,
            incoming=incoming,
            combined_paths=self._combined_confirmation_paths(job),
            match_groups=match_groups,
            enforce_individual_id=requires_individual_id,
        )
        pages = list(
            session.scalars(
                select(LedgerDocumentPage)
                .where(LedgerDocumentPage.ledger_document_id == ledger.id)
                .order_by(LedgerDocumentPage.page_number, LedgerDocumentPage.created_at)
            )
        )
        replaced: LedgerDocumentPage | None = None
        if action == "RESCAN_PAGE":
            if target_page_id:
                replaced = session.get(LedgerDocumentPage, target_page_id)
                if replaced is None or replaced.ledger_document_id != ledger.id:
                    replaced = None
            if replaced is None and target_job_id:
                replaced = session.scalar(
                    select(LedgerDocumentPage).where(
                        LedgerDocumentPage.ledger_document_id == ledger.id,
                        LedgerDocumentPage.job_id == target_job_id,
                    )
                )
            if replaced is None:
                raise AppError(
                    status=409,
                    code="TARGET_PAGE_REQUIRED",
                    title="A page to compare is required",
                    detail="比較する以前のページを選択してください。",
                )
            page_number = replaced.page_number
        else:
            page_number = max((page.page_number for page in pages), default=0) + 1

        merged = self._merge_documents(ledger.current_document_json, incoming)
        candidate = OcrDocument.model_validate(merged)
        report = self._validator.validate(candidate, configured_paths=configured_paths)
        candidate = self._validator.merge_review_items(candidate, report)
        serialized = candidate.model_dump(mode="json", by_alias=True)
        updated = OcrResultRepository(session).save_correction(
            result=stored,
            base_revision=stored.revision,
            candidate=serialized,
            report=report,
            source=RevisionSource.RECONCILIATION,
            commit=False,
        )
        if updated is None:
            raise AppError(
                status=409,
                code="REVISION_CONFLICT",
                title="OCR result revision conflict",
                detail="別の修正が保存されています。最新結果を再取得してください。",
            )
        session.add(
            LedgerDocumentPage(
                ledger_document_id=ledger.id,
                job_id=job.id,
                page_number=page_number,
                relation_type=action,
                replaces_page_id=replaced.id if replaced else None,
                active=False,
                page_content_json=incoming,
            )
        )
        session.flush()
        return updated

    def ensure_for_confirmation(
        self,
        session: Session,
        *,
        job: ProcessingJob,
        document_json: dict[str, Any],
        configured_paths: set[str] | None,
    ) -> tuple[LedgerDocument | None, LedgerDocumentPage | None]:
        page = session.scalar(select(LedgerDocumentPage).where(LedgerDocumentPage.job_id == job.id))
        if page is None:
            payload = self.payload(session, job=job, document_json=document_json)
            if payload["required"]:
                raise AppError(
                    status=409,
                    code="DOCUMENT_RESOLUTION_REQUIRED",
                    title="Document destination must be selected",
                    detail="同じ個体の台帳があります。追加・比較・別台帳のいずれかを選択してください。",
                )
            identity = self._individual_id(document_json)
            template_id = self._template_id(session, job)
            requires_individual_id = self._requires_individual_identity(
                session,
                job,
                configured_paths,
            )
            if requires_individual_id and not identity:
                raise AppError(
                    status=409,
                    code="INDIVIDUAL_ID_REQUIRED",
                    title="Individual identification number is required",
                    detail="個体識別番号を確定してください。",
                )
            # Imported legacy/test records can predate template-version linkage.
            # They remain confirmable and continue using the job ID as the row key.
            if not template_id:
                return None, None
            document_key = identity or self.document_key(job.id)
            identity_snapshot, identity_fingerprint = self.identity_snapshot(job, document_json)
            ledger = LedgerDocument(
                id=job.id,
                template_id=template_id,
                individual_id=document_key,
                current_job_id=job.id,
                current_document_json=document_json,
                identity_snapshot_json=identity_snapshot or None,
                identity_fingerprint=identity_fingerprint,
            )
            page = LedgerDocumentPage(
                ledger_document_id=job.id,
                job_id=job.id,
                page_number=1,
                relation_type="NEW_DOCUMENT",
                page_content_json=document_json,
            )
            session.add_all([ledger, page])
            session.flush()
            return ledger, page
        existing_ledger = session.get(LedgerDocument, page.ledger_document_id)
        if existing_ledger is None:
            raise AppError(
                status=409,
                code="LEDGER_DOCUMENT_MISSING",
                title="Ledger document is missing",
                detail="保存先の台帳を取得できません。",
            )
        return existing_ledger, page

    @classmethod
    def mark_confirmed(
        cls,
        session: Session,
        ledger: LedgerDocument,
        page: LedgerDocumentPage,
        *,
        job_id: str,
        document_json: dict[str, Any],
        individual_id: str,
        job: ProcessingJob | None = None,
    ) -> None:
        now = datetime.now(UTC)
        ledger.current_job_id = job_id
        ledger.current_document_json = document_json
        ledger.individual_id = individual_id
        if job is not None:
            identity_snapshot, identity_fingerprint = cls.identity_snapshot(job, document_json)
            ledger.identity_snapshot_json = identity_snapshot or None
            ledger.identity_fingerprint = identity_fingerprint
        ledger.updated_at = now
        if page.replaces_page_id:
            replaced = session.get(LedgerDocumentPage, page.replaces_page_id)
            if replaced is not None:
                replaced.active = False
        page.active = True
        page.confirmed_at = now

    @staticmethod
    def document_id_for_job(session: Session, job_id: str) -> str | None:
        page = session.scalar(select(LedgerDocumentPage).where(LedgerDocumentPage.job_id == job_id))
        return page.ledger_document_id if page else None

    @staticmethod
    def document_key(job_id: str) -> str:
        digest = hashlib.sha256(job_id.encode("utf-8")).digest()
        return f"{int.from_bytes(digest[:8], 'big') % 10_000_000_000:010d}"

    def _target_document(
        self,
        session: Session,
        *,
        template_id: str,
        individual_id: str,
        target_document_id: str | None,
        target_job_id: str | None,
        incoming: dict[str, Any],
        combined_paths: tuple[str, ...],
        match_groups: tuple[tuple[str, ...], ...],
        enforce_individual_id: bool,
    ) -> LedgerDocument:
        ledger = session.get(LedgerDocument, target_document_id) if target_document_id else None
        if ledger is None and target_job_id:
            page = session.scalar(
                select(LedgerDocumentPage).where(LedgerDocumentPage.job_id == target_job_id)
            )
            ledger = session.get(LedgerDocument, page.ledger_document_id) if page else None
        if ledger is None and target_job_id:
            confirmed = session.scalar(
                select(ConfirmedResult).where(ConfirmedResult.job_id == target_job_id)
            )
            source_job = session.get(ProcessingJob, target_job_id)
            if (
                confirmed
                and source_job
                and self._template_id(session, source_job) == template_id
                and (
                    self._all_groups_match(
                        incoming,
                        confirmed.confirmed_json,
                        match_groups,
                    )
                    if match_groups
                    else self._all_values_differ(
                        incoming,
                        confirmed.confirmed_json,
                        combined_paths,
                    )
                    if combined_paths
                    else (
                        confirmed.individual_id == individual_id
                        if enforce_individual_id
                        else True
                    )
                )
            ):
                ledger = LedgerDocument(
                    id=target_job_id,
                    template_id=template_id,
                    individual_id=confirmed.individual_id,
                    current_job_id=target_job_id,
                    current_document_json=confirmed.confirmed_json,
                    document_name=confirmed.spreadsheet_name,
                    library_name=confirmed.spreadsheet_name,
                )
                session.add(ledger)
                session.add(
                    LedgerDocumentPage(
                        ledger_document_id=ledger.id,
                        job_id=target_job_id,
                        page_number=1,
                        relation_type="NEW_DOCUMENT",
                        page_content_json=confirmed.confirmed_json,
                        confirmed_at=confirmed.confirmed_at,
                    )
                )
                session.flush()
        if (
            ledger is None
            or ledger.template_id != template_id
            or (
                bool(match_groups)
                and not self._all_groups_match(
                    incoming,
                    ledger.current_document_json,
                    match_groups,
                )
            )
            or (
                bool(combined_paths)
                and not match_groups
                and not self._all_values_differ(
                    incoming,
                    ledger.current_document_json,
                    combined_paths,
                )
            )
            or (
                enforce_individual_id
                and not combined_paths
                and not match_groups
                and ledger.individual_id != individual_id
            )
        ):
            raise AppError(
                status=409,
                code="DOCUMENT_TARGET_INVALID",
                title="Selected document is invalid",
                detail="選択した台帳は現在の個体と一致しません。",
            )
        return ledger

    def _candidates(
        self,
        session: Session,
        *,
        template_id: str,
        individual_id: str | None,
        exclude_job_id: str,
        incoming: dict[str, Any],
        combined_paths: tuple[str, ...],
        match_groups: tuple[tuple[str, ...], ...],
    ) -> list[dict[str, Any]]:
        candidates: list[dict[str, Any]] = []
        document_query = select(LedgerDocument).where(LedgerDocument.template_id == template_id)
        if not combined_paths and not match_groups:
            if not individual_id:
                return []
            document_query = document_query.where(
                LedgerDocument.individual_id == individual_id,
            )
        documents = list(session.scalars(document_query.order_by(LedgerDocument.updated_at.desc())))
        linked_jobs: set[str] = set()
        for ledger in documents:
            pages = list(
                session.scalars(
                    select(LedgerDocumentPage)
                    .where(LedgerDocumentPage.ledger_document_id == ledger.id)
                    .order_by(LedgerDocumentPage.page_number, LedgerDocumentPage.created_at)
                )
            )
            linked_jobs.update(page.job_id for page in pages)
            if match_groups and not self._all_groups_match(
                incoming,
                ledger.current_document_json,
                match_groups,
            ):
                continue
            if combined_paths and not match_groups and not self._all_values_differ(
                incoming,
                ledger.current_document_json,
                combined_paths,
            ):
                continue
            candidates.append(
                self._candidate_payload(
                    ledger.id,
                    ledger.current_job_id,
                    ledger.current_document_json,
                    pages,
                    ledger.updated_at,
                    ledger.document_name or ledger.library_name,
                )
            )
        legacy_query = (
            select(ConfirmedResult, ProcessingJob)
            .join(ProcessingJob, ProcessingJob.id == ConfirmedResult.job_id)
            .join(TemplateVersion, TemplateVersion.id == ProcessingJob.template_version_id)
            .where(
                TemplateVersion.template_id == template_id,
                ConfirmedResult.job_id != exclude_job_id,
            )
            .order_by(ConfirmedResult.confirmed_at.desc())
        )
        if not combined_paths and not match_groups:
            legacy_query = legacy_query.where(ConfirmedResult.individual_id == individual_id)
        legacy = session.execute(legacy_query).all()
        for confirmed, source_job in legacy:
            if source_job.id in linked_jobs:
                continue
            if match_groups and not self._all_groups_match(
                incoming,
                confirmed.confirmed_json,
                match_groups,
            ):
                continue
            if combined_paths and not match_groups and not self._all_values_differ(
                incoming,
                confirmed.confirmed_json,
                combined_paths,
            ):
                continue
            candidates.append(
                self._candidate_payload(
                    None,
                    source_job.id,
                    confirmed.confirmed_json,
                    [],
                    confirmed.confirmed_at,
                    confirmed.spreadsheet_name,
                )
            )
        return candidates

    @staticmethod
    def _match_groups(job: ProcessingJob) -> tuple[tuple[str, ...], ...]:
        snapshot = job.config_snapshot_json
        template_snapshot = snapshot.get("templateSnapshot")
        config = template_snapshot.get("config") if isinstance(template_snapshot, dict) else None
        raw_fields = config.get("documentIdentityFields") if isinstance(config, dict) else None
        if not isinstance(raw_fields, list):
            return ()
        grouped: dict[str, list[tuple[int, str]]] = {}
        for field in raw_fields:
            if not isinstance(field, dict):
                return ()
            group_id = field.get("matchGroupId")
            target_path = field.get("targetPath")
            if not isinstance(group_id, str) or not group_id:
                return ()
            if not isinstance(target_path, str) or not target_path:
                return ()
            raw_order = field.get("componentOrder", 0)
            order = raw_order if isinstance(raw_order, int) else 0
            grouped.setdefault(group_id, []).append((order, target_path))
        return tuple(
            tuple(path for _, path in sorted(components))
            for _, components in grouped.items()
            if components
        )

    @classmethod
    def _all_groups_match(
        cls,
        incoming: dict[str, Any],
        existing: dict[str, Any],
        groups: tuple[tuple[str, ...], ...],
    ) -> bool:
        if not groups:
            return False
        for paths in groups:
            for path in paths:
                incoming_value = cls._path_value(incoming, path, require_readable=True)
                existing_value = cls._path_value(existing, path, require_readable=True)
                if incoming_value is None or existing_value is None:
                    return False
                if cls._comparable_value(incoming_value) != cls._comparable_value(
                    existing_value
                ):
                    return False
        return True

    @classmethod
    def identity_snapshot(
        cls,
        job: ProcessingJob,
        document: dict[str, Any],
    ) -> tuple[dict[str, list[str]], str | None]:
        groups = cls._match_groups(job)
        if not groups:
            return {}, None
        snapshot: dict[str, list[str]] = {}
        for index, paths in enumerate(groups):
            values: list[str] = []
            for path in paths:
                value = cls._path_value(document, path, require_readable=True)
                if value is None:
                    return {}, None
                values.append(cls._comparable_value(value))
            snapshot[f"group-{index + 1}"] = values
        serialized = json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"))
        return snapshot, hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    @classmethod
    def proposed_document_name(
        cls,
        job: ProcessingJob,
        document: dict[str, Any],
    ) -> str:
        snapshot = job.config_snapshot_json
        template_snapshot = snapshot.get("templateSnapshot")
        config = template_snapshot.get("config") if isinstance(template_snapshot, dict) else None
        raw_name = config.get("documentName") if isinstance(config, dict) else None
        if isinstance(raw_name, dict):
            paths = raw_name.get("fieldPaths")
            if isinstance(paths, list):
                values = [
                    value
                    for path in paths
                    if isinstance(path, str)
                    if (value := cls._path_value(document, path)) is not None
                ]
                if values:
                    separator = raw_name.get("separator", "")
                    prefix = raw_name.get("prefix", "")
                    suffix = raw_name.get("suffix", "")
                    joined = (separator if isinstance(separator, str) else "").join(values)
                    candidate = (
                        f"{prefix if isinstance(prefix, str) else ''}"
                        f"{joined}"
                        f"{suffix if isinstance(suffix, str) else ''}"
                    ).strip()
                    if candidate:
                        return candidate[:180]
        raw_identity = config.get("documentIdentityFields") if isinstance(config, dict) else None
        if isinstance(raw_identity, list):
            title_path = next(
                (
                    field.get("targetPath")
                    for field in raw_identity
                    if isinstance(field, dict) and field.get("displayRole") == "title"
                ),
                None,
            )
            if isinstance(title_path, str):
                title = cls._path_value(document, title_path)
                if title:
                    return title[:180]
        identity = document.get("document")
        if isinstance(identity, dict):
            cattle_name = cls._reading_value(identity.get("cattleName"))
            if cattle_name:
                return cattle_name[:180]
        return f"書類_{job.id[:8]}"

    @staticmethod
    def _combined_confirmation_paths(job: ProcessingJob) -> tuple[str, ...]:
        snapshot = job.config_snapshot_json
        template_snapshot = snapshot.get("templateSnapshot")
        config = template_snapshot.get("config") if isinstance(template_snapshot, dict) else None
        raw_fields = config.get("documentIdentityFields") if isinstance(config, dict) else None
        if not isinstance(raw_fields, list) or len(raw_fields) < 2:
            return ()
        paths: list[str] = []
        for field in raw_fields:
            if not isinstance(field, dict) or field.get("matchRole") != "confirmation":
                return ()
            target_path = field.get("targetPath")
            if not isinstance(target_path, str) or not target_path:
                return ()
            paths.append(target_path)
        return tuple(paths)

    @classmethod
    def _all_values_differ(
        cls,
        incoming: dict[str, Any],
        existing: dict[str, Any],
        paths: tuple[str, ...],
    ) -> bool:
        for path in paths:
            incoming_value = cls._path_value(incoming, path)
            existing_value = cls._path_value(existing, path)
            if incoming_value is None or existing_value is None:
                return False
            if cls._comparable_value(incoming_value) == cls._comparable_value(existing_value):
                return False
        return True

    @staticmethod
    def _comparable_value(value: str) -> str:
        normalized = unicodedata.normalize("NFKC", value).casefold()
        normalized = "".join(character for character in normalized if not character.isspace())
        dash_characters = "\u2010\u2011\u2012\u2013\u2014\u2015\u2212"
        return normalized.translate(str.maketrans(dash_characters, "-------"))

    @classmethod
    def _path_value(
        cls,
        document: dict[str, Any],
        target_path: str,
        *,
        require_readable: bool = False,
    ) -> str | None:
        current: Any = document
        for segment in target_path.split("."):
            if isinstance(current, dict):
                current = current.get(segment)
            elif isinstance(current, list) and segment.isdigit():
                index = int(segment)
                current = current[index] if index < len(current) else None
            else:
                return None
        if isinstance(current, dict):
            if require_readable and current.get("status") != "readable":
                return None
            return cls._reading_value(current)
        if current in {None, ""}:
            return None
        return str(current).strip() or None

    def _candidate_payload(
        self,
        document_id: str | None,
        source_job_id: str | None,
        document: dict[str, Any],
        pages: list[LedgerDocumentPage],
        updated_at: datetime,
        document_name: str | None = None,
    ) -> dict[str, Any]:
        identity = document.get("document", {}) if isinstance(document, dict) else {}
        name = (
            self._reading_value(identity.get("cattleName")) if isinstance(identity, dict) else None
        )
        return {
            "documentId": document_id,
            "sourceJobId": source_job_id,
            "individualId": self._individual_id(document),
            "cattleName": name,
            "documentName": document_name,
            "pageCount": max((page.page_number for page in pages), default=1),
            "lastConfirmedAt": updated_at.isoformat(),
            "pages": [
                {
                    "pageId": page.id,
                    "sourceJobId": page.job_id,
                    "pageNumber": page.page_number,
                    "active": page.active,
                    "confirmedAt": page.confirmed_at.isoformat() if page.confirmed_at else None,
                }
                for page in pages
            ]
            or [
                {
                    "pageId": None,
                    "sourceJobId": source_job_id,
                    "pageNumber": 1,
                    "active": True,
                    "confirmedAt": updated_at.isoformat(),
                }
            ],
        }

    @staticmethod
    def _template_id(session: Session, job: ProcessingJob) -> str | None:
        version = (
            session.get(TemplateVersion, job.template_version_id)
            if job.template_version_id
            else None
        )
        return version.template_id if version else None

    @staticmethod
    def _requires_individual_identity(
        session: Session,
        job: ProcessingJob,
        configured_paths: set[str] | None,
    ) -> bool:
        version = (
            session.get(TemplateVersion, job.template_version_id)
            if job.template_version_id
            else None
        )
        template = session.get(Template, version.template_id) if version else None
        if template is not None:
            document_type = template.document_type.casefold()
            if "breeding" in document_type or document_type.startswith("cow-"):
                return True
            if document_type == "spreadsheet":
                return False
        if configured_paths is None:
            return True
        return any(
            path == "document.unifiedEarTag"
            or path.startswith(("pedigree.", "saleCalves.", "calvings."))
            for path in configured_paths
        )

    @staticmethod
    def _individual_id(document: dict[str, Any]) -> str | None:
        try:
            return individual_id_from_document(OcrDocument.model_validate(document))
        except ValueError:
            return None

    @staticmethod
    def _reading_value(value: Any) -> str | None:
        if not isinstance(value, dict):
            return None
        resolved = value.get("normalized") or value.get("raw")
        if resolved in {None, ""}:
            return None
        return str(resolved).strip() or None

    @classmethod
    def _merge_documents(cls, previous: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
        def merge(old: Any, new: Any, key: str = "") -> Any:
            if isinstance(old, dict) and isinstance(new, dict):
                if "status" in new and ("raw" in new or "normalized" in new):
                    return new if cls._reading_value(new) is not None else old
                return {
                    name: merge(old.get(name), new.get(name), name)
                    if name in old and name in new
                    else (new[name] if name in new else old[name])
                    for name in old.keys() | new.keys()
                }
            if isinstance(old, list) and isinstance(new, list):
                if key in {"saleCalves", "calvings"}:
                    result = [dict(item) if isinstance(item, dict) else item for item in old]
                    positions = {
                        cls._reading_value(item.get("parity")): index
                        for index, item in enumerate(result)
                        if isinstance(item, dict) and cls._reading_value(item.get("parity"))
                    }
                    for index, item in enumerate(new):
                        parity = (
                            cls._reading_value(item.get("parity"))
                            if isinstance(item, dict)
                            else None
                        )
                        target = (
                            positions.get(parity)
                            if parity
                            else (index if index < len(result) else None)
                        )
                        if target is None:
                            result.append(item)
                        else:
                            result[target] = merge(result[target], item)
                    return result
                return new or old
            return new if new is not None and new != "" else old

        return cast(dict[str, Any], merge(previous, incoming))

    @classmethod
    def _changes(cls, previous: dict[str, Any], current: dict[str, Any]) -> list[dict[str, Any]]:
        def readings(value: Any, path: str = "") -> dict[str, str | None]:
            found: dict[str, str | None] = {}
            if isinstance(value, dict):
                if "status" in value and ("raw" in value or "normalized" in value):
                    found[path] = cls._reading_value(value)
                else:
                    for key, child in value.items():
                        found.update(readings(child, f"{path}.{key}" if path else key))
            elif isinstance(value, list):
                for index, child in enumerate(value):
                    found.update(readings(child, f"{path}.{index}"))
            return found

        before, after = readings(previous), readings(current)
        return [
            {
                "path": path,
                "kind": "ADDED"
                if before.get(path) is None
                else "REMOVED"
                if after.get(path) is None
                else "CHANGED",
                "previousValue": before.get(path),
                "currentValue": after.get(path),
            }
            for path in sorted(before.keys() | after.keys())
            if before.get(path) != after.get(path)
        ]
