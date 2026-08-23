from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

from sqlalchemy import select

from app.core.errors import AppError
from app.db.models import (
    ConfirmedResult,
    DocumentFolder,
    LedgerDocument,
    LedgerDocumentPage,
    ProcessingJob,
    Template,
    TemplateVersion,
)
from app.db.session import Database
from app.domain.template_config import parse_template_config
from app.storage.local_storage import LocalStorageService


class DocumentLibraryService:
    def __init__(
        self,
        database: Database,
        storage: LocalStorageService | None = None,
    ) -> None:
        self._database = database
        self._storage = storage

    def list_library(self) -> dict[str, Any]:
        with self._database.session() as session:
            self._backfill_legacy(session)
            folders = list(
                session.scalars(
                    select(DocumentFolder).order_by(DocumentFolder.name, DocumentFolder.id)
                )
            )
            documents = list(
                session.scalars(select(LedgerDocument).order_by(LedgerDocument.updated_at.desc()))
            )
            groups: dict[str, dict[str, Any]] = {}
            document_items: list[dict[str, Any]] = []
            for ledger in documents:
                template = session.get(Template, ledger.template_id)
                pages = list(
                    session.scalars(
                        select(LedgerDocumentPage)
                        .where(LedgerDocumentPage.ledger_document_id == ledger.id)
                        .order_by(
                            LedgerDocumentPage.page_number,
                            LedgerDocumentPage.created_at.desc(),
                        )
                    )
                )
                versions: dict[int, list[LedgerDocumentPage]] = defaultdict(list)
                for page in pages:
                    versions[page.page_number].append(page)
                active_pages = []
                for page_number in sorted(versions):
                    history = versions[page_number]
                    active = next(
                        (page for page in history if page.active and page.confirmed_at is not None),
                        None,
                    )
                    if active is None:
                        continue
                    job = session.get(ProcessingJob, active.job_id)
                    active_pages.append(
                        {
                            "pageId": active.id,
                            "pageNumber": page_number,
                            "sourceJobId": active.job_id,
                            "templateVersionId": job.template_version_id if job else None,
                            "thumbnailUrl": (
                                f"/api/v1/jobs/{active.job_id}/artifacts/alignedSource"
                            ),
                            "confirmedAt": active.confirmed_at,
                            "versionCount": len(history),
                            "history": [
                                {
                                    "pageId": item.id,
                                    "sourceJobId": item.job_id,
                                    "createdAt": item.created_at,
                                    "confirmedAt": item.confirmed_at,
                                    "active": item.active,
                                }
                                for item in history
                            ],
                        }
                    )
                if not active_pages:
                    continue
                individual_id = ledger.individual_id
                display = self._display_information(session, ledger)
                group = groups.setdefault(
                    individual_id,
                    {
                        "individualId": individual_id,
                        "cattleName": self._cattle_name(ledger.current_document_json),
                        **display,
                        "ledgerCount": 0,
                        "pageCount": 0,
                        "lastUpdatedAt": ledger.updated_at,
                        "ledgers": [],
                    },
                )
                group["ledgerCount"] += 1
                group["pageCount"] += len(active_pages)
                if ledger.updated_at > group["lastUpdatedAt"]:
                    group["lastUpdatedAt"] = ledger.updated_at
                    group["cattleName"] = self._cattle_name(ledger.current_document_json)
                    group.update(display)
                ledger_item = {
                    "documentId": ledger.id,
                    "templateId": ledger.template_id,
                    "templateName": template.name if template else "台帳",
                    "individualId": individual_id,
                    "cattleName": self._cattle_name(ledger.current_document_json),
                    **display,
                    "currentJobId": ledger.current_job_id,
                    "updatedAt": ledger.updated_at,
                    "pageCount": len(active_pages),
                    "pages": active_pages,
                    "spreadsheetId": ledger.spreadsheet_id,
                    "spreadsheetUrl": ledger.spreadsheet_url,
                    "driveFolderPath": ledger.drive_folder_path,
                    "spreadsheetSyncStatus": ledger.spreadsheet_sync_status,
                    "spreadsheetSyncError": ledger.spreadsheet_sync_error,
                    "libraryFolderId": ledger.library_folder_id,
                    "locationVersion": ledger.library_location_version,
                }
                group["ledgers"].append(ledger_item)
                document_items.append(ledger_item)
            folder_counts = Counter(item["libraryFolderId"] for item in document_items)
            return {
                "count": len(document_items),
                "folders": [
                    {
                        "id": folder.id,
                        "parentId": folder.parent_id,
                        "name": folder.name,
                        "documentCount": folder_counts[folder.id],
                        "createdAt": folder.created_at,
                        "updatedAt": folder.updated_at,
                    }
                    for folder in folders
                ],
                "documents": document_items,
                "items": sorted(
                    groups.values(),
                    key=lambda item: item["lastUpdatedAt"],
                    reverse=True,
                ),
            }

    def create_folder(self, *, name: str, parent_id: str | None) -> dict[str, Any]:
        cleaned, normalized = self._folder_name(name)
        with self._database.session() as session:
            if parent_id is not None and session.get(DocumentFolder, parent_id) is None:
                raise self._not_found("移動先のフォルダが見つかりません。")
            self._assert_unique_name(session, parent_id, normalized)
            folder = DocumentFolder(parent_id=parent_id, name=cleaned, normalized_name=normalized)
            session.add(folder)
            session.commit()
            session.refresh(folder)
            return self._folder_payload(folder, 0)

    def rename_folder(self, folder_id: str, *, name: str) -> dict[str, Any]:
        cleaned, normalized = self._folder_name(name)
        with self._database.session() as session:
            folder = session.get(DocumentFolder, folder_id)
            if folder is None:
                raise self._not_found("フォルダが見つかりません。")
            self._assert_unique_name(session, folder.parent_id, normalized, exclude_id=folder.id)
            folder.name = cleaned
            folder.normalized_name = normalized
            session.commit()
            session.refresh(folder)
            count = len(list(session.scalars(select(LedgerDocument.id).where(LedgerDocument.library_folder_id == folder.id))))
            return self._folder_payload(folder, count)

    def delete_folder(self, folder_id: str) -> None:
        with self._database.session() as session:
            folder = session.get(DocumentFolder, folder_id)
            if folder is None:
                raise self._not_found("フォルダが見つかりません。")
            has_children = session.scalar(select(DocumentFolder.id).where(DocumentFolder.parent_id == folder.id).limit(1))
            has_documents = session.scalar(select(LedgerDocument.id).where(LedgerDocument.library_folder_id == folder.id).limit(1))
            if has_children is not None or has_documents is not None:
                raise AppError(
                    status=409,
                    code="DOCUMENT_FOLDER_NOT_EMPTY",
                    title="Document folder is not empty",
                    detail="書類または子フォルダを移動してから削除してください。",
                )
            session.delete(folder)
            session.commit()

    def move_document(
        self,
        document_id: str,
        *,
        folder_id: str | None,
        expected_version: int,
    ) -> dict[str, Any]:
        with self._database.session() as session:
            document = session.get(LedgerDocument, document_id)
            if document is None:
                raise self._not_found("書類が見つかりません。")
            if document.library_location_version != expected_version:
                raise AppError(
                    status=409,
                    code="DOCUMENT_LOCATION_CONFLICT",
                    title="Document location changed",
                    detail="別の操作で保存場所が変更されています。最新状態を読み込んでください。",
                )
            if folder_id is not None and session.get(DocumentFolder, folder_id) is None:
                raise self._not_found("移動先のフォルダが見つかりません。")
            document.library_folder_id = folder_id
            document.library_location_version += 1
            session.commit()
            session.refresh(document)
            return {
                "documentId": document.id,
                "folderId": document.library_folder_id,
                "locationVersion": document.library_location_version,
            }

    @staticmethod
    def _folder_payload(folder: DocumentFolder, document_count: int) -> dict[str, Any]:
        return {
            "id": folder.id,
            "parentId": folder.parent_id,
            "name": folder.name,
            "documentCount": document_count,
            "createdAt": folder.created_at,
            "updatedAt": folder.updated_at,
        }

    @staticmethod
    def _folder_name(name: str) -> tuple[str, str]:
        cleaned = " ".join(name.split())
        if not cleaned or len(cleaned) > 100 or cleaned in {".", ".."} or "/" in cleaned or "\\" in cleaned:
            raise AppError(
                status=422,
                code="DOCUMENT_FOLDER_NAME_INVALID",
                title="Invalid document folder name",
                detail="フォルダ名は1〜100文字で入力し、/ と \\ は使用しないでください。",
            )
        return cleaned, cleaned.casefold()

    @staticmethod
    def _assert_unique_name(
        session: Any,
        parent_id: str | None,
        normalized_name: str,
        *,
        exclude_id: str | None = None,
    ) -> None:
        parent_condition = DocumentFolder.parent_id.is_(None) if parent_id is None else DocumentFolder.parent_id == parent_id
        statement = select(DocumentFolder.id).where(
            parent_condition,
            DocumentFolder.normalized_name == normalized_name,
        )
        if exclude_id is not None:
            statement = statement.where(DocumentFolder.id != exclude_id)
        if session.scalar(statement.limit(1)) is not None:
            raise AppError(
                status=409,
                code="DOCUMENT_FOLDER_NAME_CONFLICT",
                title="Document folder name already exists",
                detail="同じ場所に同名のフォルダがあります。",
            )

    @staticmethod
    def _not_found(detail: str) -> AppError:
        return AppError(
            status=404,
            code="DOCUMENT_LIBRARY_ITEM_NOT_FOUND",
            title="Document library item not found",
            detail=detail,
        )

    @staticmethod
    def _backfill_legacy(session: Any) -> None:
        linked_jobs = set(session.scalars(select(LedgerDocumentPage.job_id)))
        rows = session.execute(
            select(ConfirmedResult, ProcessingJob, TemplateVersion)
            .join(ProcessingJob, ProcessingJob.id == ConfirmedResult.job_id)
            .join(TemplateVersion, TemplateVersion.id == ProcessingJob.template_version_id)
            .order_by(ConfirmedResult.confirmed_at)
        ).all()
        changed = False
        for confirmed, job, version in rows:
            if job.id in linked_jobs:
                continue
            ledger = session.get(LedgerDocument, job.id)
            if ledger is None:
                ledger = LedgerDocument(
                    id=job.id,
                    template_id=version.template_id,
                    individual_id=confirmed.individual_id or "0000000000",
                    current_job_id=job.id,
                    current_document_json=confirmed.confirmed_json,
                    created_at=confirmed.confirmed_at,
                    updated_at=confirmed.confirmed_at,
                )
                session.add(ledger)
            session.add(
                LedgerDocumentPage(
                    ledger_document_id=ledger.id,
                    job_id=job.id,
                    page_number=1,
                    relation_type="NEW_DOCUMENT",
                    page_content_json=confirmed.confirmed_json,
                    active=True,
                    created_at=confirmed.confirmed_at,
                    confirmed_at=confirmed.confirmed_at,
                )
            )
            linked_jobs.add(job.id)
            changed = True
        if changed:
            session.commit()

    @staticmethod
    def _cattle_name(document: dict[str, Any]) -> str | None:
        identity = document.get("document")
        if not isinstance(identity, dict):
            return None
        value = identity.get("cattleName")
        if not isinstance(value, dict):
            return None
        candidate = value.get("normalized") or value.get("raw")
        return str(candidate).strip() if candidate else None

    def _display_information(
        self,
        session: Any,
        ledger: LedgerDocument,
    ) -> dict[str, str | None]:
        cattle_name = self._cattle_name(ledger.current_document_json)
        fallback = {
            "displayTitle": cattle_name or ledger.individual_id,
            "displayTitleLabel": "牛名" if cattle_name else "個体識別番号",
            "displaySubtitle": ledger.individual_id if cattle_name else None,
            "displaySubtitleLabel": "個体識別番号" if cattle_name else None,
        }
        if self._storage is None or not ledger.current_job_id:
            return fallback
        job = session.get(ProcessingJob, ledger.current_job_id)
        version = (
            session.get(TemplateVersion, job.template_version_id)
            if job is not None and job.template_version_id
            else None
        )
        if version is None:
            return fallback
        try:
            config = parse_template_config(
                self._storage.read_bytes(version.config_relative_path).decode("utf-8")
            )
        except (OSError, UnicodeError, ValueError):
            return fallback
        # An empty list identifies a legacy template whose card presentation was not configured.
        if not config.document_identity_fields:
            return fallback
        title_field = next(
            (
                field
                for field in config.document_identity_fields
                if field.display_role == "title"
            ),
            None,
        )
        primary_field = next(
            (
                field
                for field in config.document_identity_fields
                if field.match_role == "primary"
            ),
            None,
        )
        subtitle_field = next(
            (
                field
                for field in config.document_identity_fields
                if field.display_role == "subtitle"
            ),
            None,
        )
        resolved_title_field = title_field or primary_field
        title_value = self._path_value(
            ledger.current_document_json,
            resolved_title_field.target_path if resolved_title_field else "",
        )
        if not title_value and primary_field is not None:
            title_value = self._path_value(
                ledger.current_document_json,
                primary_field.target_path,
            )
            resolved_title_field = primary_field
        subtitle_value = self._path_value(
            ledger.current_document_json,
            subtitle_field.target_path if subtitle_field else "",
        )
        resolved_subtitle_field = subtitle_field
        if (
            not subtitle_value
            and primary_field is not None
            and (
                resolved_title_field is None
                or primary_field.target_path != resolved_title_field.target_path
            )
        ):
            subtitle_value = self._path_value(
                ledger.current_document_json,
                primary_field.target_path,
            )
            resolved_subtitle_field = primary_field
        return {
            "displayTitle": title_value or ledger.individual_id,
            "displayTitleLabel": (
                resolved_title_field.label if resolved_title_field else "個体識別番号"
            ),
            "displaySubtitle": subtitle_value,
            "displaySubtitleLabel": (
                resolved_subtitle_field.label
                if resolved_subtitle_field and subtitle_value
                else None
            ),
        }

    @classmethod
    def _path_value(cls, document: dict[str, Any], target_path: str) -> str | None:
        current: Any = document
        for segment in target_path.split(".") if target_path else []:
            if isinstance(current, dict):
                current = current.get(segment)
            elif isinstance(current, list) and segment.isdigit():
                index = int(segment)
                current = current[index] if index < len(current) else None
            else:
                return None
        if isinstance(current, dict):
            candidate = current.get("normalized") or current.get("raw")
        else:
            candidate = current
        return str(candidate).strip() if candidate is not None and candidate != "" else None
