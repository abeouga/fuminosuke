from __future__ import annotations

import re
from collections import Counter, defaultdict
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from app.core.errors import AppError
from app.db.models import (
    ConfirmedResult,
    DocumentFolder,
    JobArtifact,
    LedgerDocument,
    LedgerDocumentPage,
    ProcessingJob,
    Template,
    TemplateVersion,
)
from app.db.session import Database
from app.domain.template_config import parse_template_config
from app.services.google_drive_service import GoogleDriveService
from app.storage.local_storage import LocalStorageService


class DocumentLibraryService:
    def __init__(
        self,
        database: Database,
        storage: LocalStorageService | None = None,
        google_drive: GoogleDriveService | None = None,
    ) -> None:
        self._database = database
        self._storage = storage
        self._google_drive = google_drive

    def list_library(self) -> dict[str, Any]:
        with self._database.session() as session:
            self._backfill_legacy(session)
            folders = list(
                session.scalars(
                    select(DocumentFolder).order_by(DocumentFolder.name, DocumentFolder.id)
                )
            )
            documents = list(
                session.scalars(
                    select(LedgerDocument)
                    .where(LedgerDocument.library_deleted_at.is_(None))
                    .order_by(LedgerDocument.updated_at.desc())
                )
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
                canonical_name = ledger.document_name or ledger.library_name
                if canonical_name:
                    display = {
                        **display,
                        "displayTitle": canonical_name,
                        "displayTitleLabel": "書類名",
                    }
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
                current_confirmation = (
                    session.scalar(
                        select(ConfirmedResult).where(
                            ConfirmedResult.job_id == ledger.current_job_id
                        )
                    )
                    if ledger.current_job_id
                    else None
                )
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
                    "spreadsheetId": ledger.spreadsheet_id
                    or (current_confirmation.spreadsheet_id if current_confirmation else None),
                    "spreadsheetUrl": ledger.spreadsheet_url
                    or (current_confirmation.spreadsheet_url if current_confirmation else None),
                    "driveFolderPath": ledger.drive_folder_path,
                    "spreadsheetSyncStatus": ledger.spreadsheet_sync_status
                    or (
                        current_confirmation.spreadsheet_sync_status
                        if current_confirmation
                        else None
                    ),
                    "spreadsheetSyncError": ledger.spreadsheet_sync_error
                    or (
                        current_confirmation.spreadsheet_sync_error
                        if current_confirmation
                        else None
                    ),
                    "libraryFolderId": ledger.library_folder_id,
                    "locationVersion": ledger.library_location_version,
                    "libraryName": canonical_name,
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

    def create_folder(
        self,
        *,
        name: str,
        parent_id: str | None,
        expected_subject: str | None = None,
    ) -> dict[str, Any]:
        cleaned, normalized = self._folder_name(name)
        with self._database.session() as session:
            if parent_id is not None and session.get(DocumentFolder, parent_id) is None:
                raise self._not_found("移動先のフォルダが見つかりません。")
            self._assert_unique_name(session, parent_id, normalized)
            folder = DocumentFolder(parent_id=parent_id, name=cleaned, normalized_name=normalized)
            session.add(folder)
            session.commit()
            session.refresh(folder)
            folder_id = folder.id
        if self._google_drive is not None:
            # Keep the stable local id if one of several Drive roots fails.
            # A later sync can adopt any already-created tagged mirror and
            # finish the remaining roots without leaving an orphaned tag.
            self._google_drive.mirror_library_folder(folder_id, expected_subject)
        with self._database.session() as session:
            persisted = session.get(DocumentFolder, folder_id)
            if persisted is None:
                raise self._not_found("作成したフォルダが見つかりません。")
            return self._folder_payload(persisted, 0)

    def rename_folder(
        self,
        folder_id: str,
        *,
        name: str,
        expected_subject: str | None = None,
    ) -> dict[str, Any]:
        cleaned, normalized = self._folder_name(name)
        with self._database.session() as session:
            folder = session.get(DocumentFolder, folder_id)
            if folder is None:
                raise self._not_found("フォルダが見つかりません。")
            self._assert_unique_name(session, folder.parent_id, normalized, exclude_id=folder.id)
        if self._google_drive is not None:
            self._google_drive.rename_mirrored_library_folder(
                folder_id,
                cleaned,
                expected_subject,
            )
        with self._database.session() as session:
            folder = session.get(DocumentFolder, folder_id)
            if folder is None:
                raise self._not_found("フォルダが見つかりません。")
            self._assert_unique_name(session, folder.parent_id, normalized, exclude_id=folder.id)
            folder.name = cleaned
            folder.normalized_name = normalized
            session.commit()
            session.refresh(folder)
            count = len(
                list(
                    session.scalars(
                        select(LedgerDocument.id).where(
                            LedgerDocument.library_folder_id == folder.id
                        )
                    )
                )
            )
            return self._folder_payload(folder, count)

    def delete_folder(
        self,
        folder_id: str,
        expected_subject: str | None = None,
    ) -> None:
        with self._database.session() as session:
            folder = session.get(DocumentFolder, folder_id)
            if folder is None:
                raise self._not_found("フォルダが見つかりません。")
            self._assert_folder_empty(session, folder.id)
        if self._google_drive is not None:
            self._google_drive.delete_mirrored_library_folder(
                folder_id,
                expected_subject,
            )
        with self._database.session() as session:
            folder = session.get(DocumentFolder, folder_id)
            if folder is None:
                # The production Drive hook deletes the local row in the same
                # locked transaction as its mirrored folders. Test doubles and
                # older hook implementations leave the local deletion here.
                return
            self._assert_folder_empty(session, folder.id)
            session.delete(folder)
            session.commit()

    def move_document(
        self,
        document_id: str,
        *,
        folder_id: str | None,
        expected_version: int,
        expected_subject: str | None = None,
    ) -> dict[str, Any]:
        with self._database.session() as session:
            document = session.get(LedgerDocument, document_id)
            if document is None or document.library_deleted_at is not None:
                raise self._not_found("書類が見つかりません。")
            if document.library_location_version != expected_version:
                raise self._location_conflict()
            if folder_id is not None and session.get(DocumentFolder, folder_id) is None:
                raise self._not_found("移動先のフォルダが見つかりません。")
        if self._google_drive is not None:
            moved = self._google_drive.move_library_document(
                document_id,
                folder_id,
                expected_version=expected_version,
                expected_subject=expected_subject,
            )
            if isinstance(moved, dict):
                return moved
        with self._database.session() as session:
            document = session.get(LedgerDocument, document_id)
            if document is None or document.library_deleted_at is not None:
                raise self._not_found("書類が見つかりません。")
            if document.library_location_version != expected_version:
                raise self._location_conflict()
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

    def sync_document_library(
        self,
        expected_subject: str | None = None,
    ) -> dict[str, Any]:
        if self._google_drive is None:
            raise AppError(
                status=409,
                code="GOOGLE_DRIVE_SYNC_UNAVAILABLE",
                title="Google Drive sync is unavailable",
                detail="Googleアカウントへ接続してから同期してください。",
            )
        with self._database.session() as session:
            self._backfill_legacy(session)
        return self._google_drive.sync_document_library(expected_subject)

    def push_document_library_to_drive(
        self,
        expected_subject: str | None = None,
    ) -> dict[str, Any]:
        if self._google_drive is None:
            raise AppError(
                status=409,
                code="GOOGLE_DRIVE_SYNC_UNAVAILABLE",
                title="Google Drive sync is unavailable",
                detail="Googleアカウントへ接続してから同期してください。",
            )
        with self._database.session() as session:
            self._backfill_legacy(session)
        return self._google_drive.push_document_library_to_drive(expected_subject)

    def confirm_drive_ownership(
        self,
        document_id: str,
        expected_subject: str | None = None,
    ) -> dict[str, Any]:
        if self._google_drive is None:
            raise AppError(
                status=409,
                code="GOOGLE_DRIVE_SYNC_UNAVAILABLE",
                title="Google Drive sync is unavailable",
                detail="Googleアカウントへ接続してから所有者を確認してください。",
            )
        return self._google_drive.confirm_legacy_document_ownership(
            document_id,
            expected_subject,
        )

    def rename_document(
        self,
        document_id: str,
        *,
        name: str,
        expected_subject: str | None = None,
    ) -> dict[str, Any]:
        cleaned = self._document_name(name)
        with self._database.session() as session:
            document = session.get(LedgerDocument, document_id)
            if document is None or document.library_deleted_at is not None:
                raise self._not_found("書類が見つかりません。")
            confirmed = (
                session.scalar(
                    select(ConfirmedResult).where(
                        ConfirmedResult.job_id == document.current_job_id
                    )
                )
                if document.current_job_id
                else None
            )
            spreadsheet_id = document.spreadsheet_id or (
                confirmed.spreadsheet_id if confirmed is not None else None
            )
            baseline_name = (
                document.library_name
                or document.document_name
                or (confirmed.spreadsheet_name if confirmed is not None else None)
            )
        renamed = cleaned
        if spreadsheet_id:
            if self._google_drive is None:
                raise AppError(
                    status=409,
                    code="GOOGLE_DRIVE_RENAME_UNAVAILABLE",
                    title="Google Drive rename is unavailable",
                    detail="Googleアカウントへ再接続してから名前を変更してください。",
                )
            if baseline_name is None:
                raise AppError(
                    status=409,
                    code="DRIVE_DOCUMENT_NAME_BASELINE_REQUIRED",
                    title="Drive document name baseline is required",
                    detail=(
                        "Google Drive側の現在名を確認できません。"
                        "書類ライブラリを再同期してから名前を変更してください。"
                    ),
                )
            renamed = self._google_drive.rename_spreadsheet(
                spreadsheet_id,
                cleaned,
                expected_subject,
                baseline_name=baseline_name,
            )
        try:
            with self._database.session() as session:
                document = session.get(LedgerDocument, document_id)
                if document is None or document.library_deleted_at is not None:
                    raise self._not_found("書類が見つかりません。")
                document.library_name = renamed
                document.document_name = renamed
                if document.current_job_id:
                    confirmed = session.scalar(
                        select(ConfirmedResult).where(
                            ConfirmedResult.job_id == document.current_job_id
                        )
                    )
                    if confirmed is not None and confirmed.spreadsheet_id == spreadsheet_id:
                        confirmed.spreadsheet_name = renamed
                session.commit()
        except Exception as exc:
            if (
                spreadsheet_id
                and self._google_drive is not None
                and baseline_name is not None
                and baseline_name != renamed
            ):
                try:
                    self._google_drive.rename_spreadsheet(
                        spreadsheet_id,
                        baseline_name,
                        expected_subject,
                        baseline_name=renamed,
                    )
                except Exception as compensation_error:
                    raise AppError(
                        status=409,
                        code="DRIVE_DOCUMENT_RENAME_PARTIAL",
                        title="Drive document rename was partially applied",
                        detail=(
                            "Google Drive側の名前変更を自動で元に戻せませんでした。"
                            "再同期して現在名を確認してください。"
                        ),
                    ) from compensation_error
            if isinstance(exc, AppError):
                raise
            raise AppError(
                status=409,
                code="DRIVE_DOCUMENT_RENAME_ROLLED_BACK",
                title="Drive document rename was rolled back",
                detail=(
                    "ローカルへの保存に失敗したため、Google Drive側の名前を"
                    "元に戻しました。再試行してください。"
                ),
            ) from exc
        return {
            "documentId": document_id,
            "name": renamed,
            "spreadsheetRenamed": spreadsheet_id is not None,
        }

    def export_spreadsheet_pdf(
        self,
        document_id: str,
        *,
        paper_size: str = "A4",
        orientation: str = "portrait",
        expected_subject: str | None = None,
    ) -> bytes:
        with self._database.session() as session:
            document = session.get(LedgerDocument, document_id)
            if document is None or document.library_deleted_at is not None:
                raise self._not_found("書類が見つかりません。")
            confirmed = (
                session.scalar(
                    select(ConfirmedResult).where(
                        ConfirmedResult.job_id == document.current_job_id
                    )
                )
                if document.current_job_id
                else None
            )
            spreadsheet_id = document.spreadsheet_id or (
                confirmed.spreadsheet_id if confirmed is not None else None
            )
        if not spreadsheet_id:
            raise AppError(
                status=409,
                code="DOCUMENT_SPREADSHEET_NOT_READY",
                title="Document spreadsheet is not ready",
                detail="この書類には印刷できる構造化済みスプレッドシートがありません。",
            )
        if self._google_drive is None:
            raise AppError(
                status=409,
                code="GOOGLE_DRIVE_EXPORT_UNAVAILABLE",
                title="Google Drive export is unavailable",
                detail="Googleアカウントへ再接続してから印刷してください。",
            )
        return self._google_drive.export_spreadsheet_pdf(
            spreadsheet_id,
            paper_size=paper_size,
            orientation=orientation,
            expected_subject=expected_subject,
        )

    def first_page_source(self, document_id: str) -> tuple[bytes, str]:
        with self._database.session() as session:
            document = session.get(LedgerDocument, document_id)
            if document is None or document.library_deleted_at is not None:
                raise self._not_found("書類が見つかりません。")
            page = session.scalar(
                select(LedgerDocumentPage)
                .where(
                    LedgerDocumentPage.ledger_document_id == document.id,
                    LedgerDocumentPage.active.is_(True),
                    LedgerDocumentPage.confirmed_at.is_not(None),
                )
                .order_by(LedgerDocumentPage.page_number)
                .limit(1)
            )
            if page is None:
                raise AppError(
                    status=409,
                    code="DOCUMENT_IMAGE_NOT_READY",
                    title="Document image is not ready",
                    detail="印刷できる書類画像がありません。",
                )
            artifact = session.scalar(
                select(JobArtifact).where(
                    JobArtifact.job_id == page.job_id,
                    JobArtifact.artifact_name == "alignedSource",
                )
            )
            if artifact is None or self._storage is None:
                raise AppError(
                    status=409,
                    code="DOCUMENT_IMAGE_NOT_READY",
                    title="Document image is not ready",
                    detail="印刷できる書類画像がありません。",
                )
            relative_path = artifact.relative_path
            media_type = artifact.media_type
        if not self._storage.exists(relative_path):
            raise AppError(
                status=409,
                code="DOCUMENT_IMAGE_NOT_READY",
                title="Document image is not ready",
                detail="印刷する書類画像が保存されていません。",
            )
        return self._storage.read_bytes(relative_path), media_type

    def delete_documents(self, document_ids: list[str]) -> dict[str, int | bool]:
        unique_ids = list(dict.fromkeys(document_ids))
        if not unique_ids or len(unique_ids) > 100:
            raise AppError(
                status=422,
                code="DOCUMENT_DELETE_SELECTION_INVALID",
                title="Document delete selection is invalid",
                detail="削除する書類を1件以上100件以内で選択してください。",
            )
        with self._database.session() as session:
            documents = list(
                session.scalars(
                    select(LedgerDocument).where(
                        LedgerDocument.id.in_(unique_ids),
                        LedgerDocument.library_deleted_at.is_(None),
                    )
                )
            )
            if len(documents) != len(unique_ids):
                raise self._not_found("選択した書類の一部が見つかりません。")
            deleted_at = datetime.now(UTC)
            for document in documents:
                document.library_deleted_at = deleted_at
            session.commit()
        return {"deletedCount": len(documents), "driveFilesPreserved": True}

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
    def _assert_folder_empty(session: Any, folder_id: str) -> None:
        has_children = session.scalar(
            select(DocumentFolder.id)
            .where(DocumentFolder.parent_id == folder_id)
            .limit(1)
        )
        has_documents = session.scalar(
            select(LedgerDocument.id)
            .where(LedgerDocument.library_folder_id == folder_id)
            .limit(1)
        )
        if has_children is not None or has_documents is not None:
            raise AppError(
                status=409,
                code="DOCUMENT_FOLDER_NOT_EMPTY",
                title="Document folder is not empty",
                detail="書類または子フォルダを移動してから削除してください。",
            )

    @staticmethod
    def _location_conflict() -> AppError:
        return AppError(
            status=409,
            code="DOCUMENT_LOCATION_CONFLICT",
            title="Document location changed",
            detail="別の操作で保存場所が変更されています。最新状態を読み込んでください。",
        )

    @staticmethod
    def _folder_name(name: str) -> tuple[str, str]:
        cleaned = " ".join(name.split())
        if (
            not cleaned
            or len(cleaned) > 100
            or cleaned in {".", ".."}
            or "/" in cleaned
            or "\\" in cleaned
        ):
            raise AppError(
                status=422,
                code="DOCUMENT_FOLDER_NAME_INVALID",
                title="Invalid document folder name",
                detail="フォルダ名は1〜100文字で入力し、/ と \\ は使用しないでください。",
            )
        return cleaned, cleaned.casefold()

    @staticmethod
    def _document_name(name: str) -> str:
        cleaned = " ".join(name.split())
        if (
            not cleaned
            or len(cleaned) > 180
            or re.search(r'[\\/:*?"<>|\x00-\x1f]', cleaned)
        ):
            raise AppError(
                status=422,
                code="DOCUMENT_LIBRARY_NAME_INVALID",
                title="Invalid document name",
                detail='書類名は1〜180文字で入力し、\\ / : * ? " < > | は使用しないでください。',
            )
        return cleaned

    @staticmethod
    def _assert_unique_name(
        session: Any,
        parent_id: str | None,
        normalized_name: str,
        *,
        exclude_id: str | None = None,
    ) -> None:
        parent_condition = (
            DocumentFolder.parent_id.is_(None)
            if parent_id is None
            else DocumentFolder.parent_id == parent_id
        )
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
            current_ledger = session.scalar(
                select(LedgerDocument).where(
                    LedgerDocument.current_job_id == job.id,
                )
            )
            if current_ledger is not None:
                if confirmed.spreadsheet_id and not current_ledger.spreadsheet_id:
                    existing_document_id = session.scalar(
                        select(LedgerDocument.id).where(
                            LedgerDocument.spreadsheet_id == confirmed.spreadsheet_id,
                            LedgerDocument.id != current_ledger.id,
                        )
                    )
                    if existing_document_id is None:
                        current_ledger.spreadsheet_id = confirmed.spreadsheet_id
                        current_ledger.spreadsheet_url = confirmed.spreadsheet_url
                        current_ledger.spreadsheet_sync_status = (
                            confirmed.spreadsheet_sync_status
                        )
                        current_ledger.spreadsheet_sync_error = (
                            confirmed.spreadsheet_sync_error
                        )
                        changed = True
                if confirmed.spreadsheet_name and not current_ledger.library_name:
                    current_ledger.library_name = confirmed.spreadsheet_name
                    changed = True
                if confirmed.spreadsheet_name and not current_ledger.document_name:
                    current_ledger.document_name = confirmed.spreadsheet_name
                    changed = True
            if job.id in linked_jobs:
                continue
            ledger = current_ledger or session.get(LedgerDocument, job.id)
            if ledger is None:
                ledger = LedgerDocument(
                    id=job.id,
                    template_id=version.template_id,
                    individual_id=confirmed.individual_id or "0000000000",
                    current_job_id=job.id,
                    current_document_json=confirmed.confirmed_json,
                    created_at=confirmed.confirmed_at,
                    updated_at=confirmed.confirmed_at,
                    spreadsheet_id=confirmed.spreadsheet_id,
                    spreadsheet_url=confirmed.spreadsheet_url,
                    spreadsheet_sync_status=confirmed.spreadsheet_sync_status,
                    spreadsheet_sync_error=confirmed.spreadsheet_sync_error,
                    library_name=confirmed.spreadsheet_name,
                    document_name=confirmed.spreadsheet_name,
                )
                session.add(ledger)
            else:
                if confirmed.spreadsheet_id and not ledger.spreadsheet_id:
                    ledger.spreadsheet_id = confirmed.spreadsheet_id
                    ledger.spreadsheet_url = confirmed.spreadsheet_url
                    ledger.spreadsheet_sync_status = confirmed.spreadsheet_sync_status
                    ledger.spreadsheet_sync_error = confirmed.spreadsheet_sync_error
                    changed = True
                if confirmed.spreadsheet_name and not ledger.library_name:
                    ledger.library_name = confirmed.spreadsheet_name
                    changed = True
                if confirmed.spreadsheet_name and not ledger.document_name:
                    ledger.document_name = confirmed.spreadsheet_name
                    changed = True
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
