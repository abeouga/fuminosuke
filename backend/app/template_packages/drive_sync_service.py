from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Literal, Protocol, TypeVar
from uuid import uuid4

from sqlalchemy import select

from app.core.errors import AppError
from app.db.models import (
    TemplatePackageDriveDraftSync,
    TemplatePackageDriveReleaseSync,
)
from app.db.session import Database
from app.template_packages.import_models import (
    TemplatePackageImportError,
    ValidatedTemplatePackage,
)
from app.template_packages.package_reader import TemplatePackageReader

TemplateDriveKind = Literal["release", "draft"]
T = TypeVar("T")


class TemplatePackageDriveTransport(Protocol):
    def template_package_files(
        self,
        expected_subject: str | None = None,
    ) -> list[dict[str, Any]]: ...

    def upload_template_package_release(
        self,
        *,
        content: bytes,
        publisher_id: str,
        package_id: str,
        release_version: str,
        content_digest: str,
        package_sha256: str,
        file_name: str,
        expected_subject: str | None = None,
    ) -> dict[str, Any]: ...

    def upload_template_package_draft(
        self,
        *,
        content: bytes,
        publisher_id: str,
        package_id: str,
        release_version: str,
        content_digest: str,
        package_sha256: str,
        file_name: str,
        draft_id: str,
        base_release_version: str | None,
        expected_revision: int | None,
        expected_drive_file_id: str | None,
        expected_drive_version: str | None,
        expected_head_revision_id: str | None,
        expected_etag: str | None,
        expected_subject: str | None = None,
    ) -> dict[str, Any]: ...

    def download_template_package_file(
        self,
        file_id: str,
        *,
        expected_kind: str,
        expected_subject: str | None = None,
    ) -> tuple[bytes, dict[str, Any]]: ...


class TemplatePackageDriveSyncService:
    """Synchronizes package bytes while keeping every Drive identifier local-only."""

    def __init__(
        self,
        database: Database,
        drive: TemplatePackageDriveTransport,
    ) -> None:
        self._database = database
        self._drive = drive
        self._reader = TemplatePackageReader()

    def list_items(self, *, google_subject: str) -> list[dict[str, Any]]:
        remote_items = self._retry(
            lambda: self._drive.template_package_files(google_subject)
        )
        items = [
            self._validated_item(
                file_id=str(remote["fileId"]),
                kind=self._kind(remote.get("kind")),
                google_subject=google_subject,
            )[3]
            for remote in remote_items
        ]
        items.sort(
            key=lambda item: (
                str(item["publisherId"]),
                str(item["packageId"]),
                str(item["kind"]),
                str(item["releaseVersion"]),
                str(item.get("draftId") or ""),
            )
        )
        return items

    def publish_release(
        self,
        content: bytes,
        *,
        file_name: str,
        google_subject: str,
    ) -> dict[str, Any]:
        package = self._read(content)
        manifest = package.manifest
        remote = self._retry(
            lambda: self._drive.upload_template_package_release(
                content=content,
                publisher_id=manifest.publisher_id,
                package_id=manifest.package_id,
                release_version=manifest.version,
                content_digest=manifest.content_digest,
                package_sha256=package.package_sha256,
                file_name=file_name,
                expected_subject=google_subject,
            )
        )
        verified_package, verified_remote, _content, item = self._validated_item(
            file_id=str(remote["fileId"]),
            kind="release",
            google_subject=google_subject,
        )
        self._require_same_package(package, verified_package)
        self._save_release_state(google_subject, verified_package, verified_remote)
        return {**item, "syncStatus": "SYNCED"}

    def publish_draft(
        self,
        content: bytes,
        *,
        google_subject: str,
        base_release_version: str | None,
        draft_id: str | None,
        expected_revision: int | None,
    ) -> dict[str, Any]:
        package = self._read(content)
        resolved_draft_id = draft_id or str(uuid4())
        current = self._draft_state(google_subject, resolved_draft_id)
        if current is None and draft_id is not None:
            raise self._draft_conflict("このPCに作業版の同期基準がありません。再取得してください。")
        if current is not None and current.revision != expected_revision:
            raise self._draft_conflict("確認後にlocalの作業版revisionが変更されています。")
        remote = self._retry(
            lambda: self._drive.upload_template_package_draft(
                content=content,
                publisher_id=package.manifest.publisher_id,
                package_id=package.manifest.package_id,
                release_version=package.manifest.version,
                content_digest=package.manifest.content_digest,
                package_sha256=package.package_sha256,
                file_name=f"{resolved_draft_id}.llmocr-template",
                draft_id=resolved_draft_id,
                base_release_version=base_release_version,
                expected_revision=(current.revision if current is not None else None),
                expected_drive_file_id=(current.drive_file_id if current is not None else None),
                expected_drive_version=(current.drive_version if current is not None else None),
                expected_head_revision_id=(
                    current.drive_head_revision_id if current is not None else None
                ),
                expected_etag=(current.drive_etag if current is not None else None),
                expected_subject=google_subject,
            )
        )
        verified_package, verified_remote, _content, item = self._validated_item(
            file_id=str(remote["fileId"]),
            kind="draft",
            google_subject=google_subject,
        )
        self._require_same_package(package, verified_package)
        if item.get("draftId") != resolved_draft_id:
            raise self._draft_conflict("Drive作業版のdraft IDが一致しません。")
        self._save_draft_state(google_subject, verified_package, verified_remote)
        return {**item, "syncStatus": "SYNCED"}

    def package_for_inspection(
        self,
        file_id: str,
        *,
        kind: TemplateDriveKind,
        google_subject: str,
    ) -> tuple[bytes, str]:
        package, remote, content, _item = self._validated_item(
            file_id=file_id,
            kind=kind,
            google_subject=google_subject,
        )
        if kind == "release":
            self._save_release_state(google_subject, package, remote)
        else:
            self._save_draft_state(google_subject, package, remote)
        return content, str(remote["fileName"])

    def _validated_item(
        self,
        *,
        file_id: str,
        kind: TemplateDriveKind,
        google_subject: str,
    ) -> tuple[
        ValidatedTemplatePackage,
        dict[str, Any],
        bytes,
        dict[str, Any],
    ]:
        content, remote = self._retry(
            lambda: self._drive.download_template_package_file(
                file_id,
                expected_kind=kind,
                expected_subject=google_subject,
            )
        )
        package = self._read(content)
        self._require_remote_metadata(package, remote, kind)
        sync_status = self._sync_status(google_subject, package, remote, kind)
        return package, remote, content, {
            "kind": kind.upper(),
            "fileId": file_id,
            "fileName": str(remote["fileName"]),
            "publisherId": package.manifest.publisher_id,
            "packageId": package.manifest.package_id,
            "releaseVersion": package.manifest.version,
            "contentDigest": package.manifest.content_digest,
            "packageSha256": package.package_sha256,
            "draftId": remote.get("draftId"),
            "draftRevision": remote.get("draftRevision"),
            "baseReleaseVersion": remote.get("baseReleaseVersion"),
            "driveVersion": str(remote.get("driveVersion") or ""),
            "headRevisionId": remote.get("headRevisionId"),
            "etag": remote.get("etag"),
            "modifiedTime": remote.get("modifiedTime"),
            "fileBytes": int(remote.get("fileBytes") or len(content)),
            "trustStatus": "UNVERIFIED",
            "syncStatus": sync_status,
        }

    def _read(self, content: bytes) -> ValidatedTemplatePackage:
        try:
            package = self._reader.read(content)
        except TemplatePackageImportError as exc:
            raise AppError(
                status=422,
                code=exc.code,
                title="Drive template package rejected",
                detail=exc.detail,
                metadata={"errorCategory": "SECURITY"},
            ) from exc
        return package

    @staticmethod
    def _require_remote_metadata(
        package: ValidatedTemplatePackage,
        remote: dict[str, Any],
        kind: TemplateDriveKind,
    ) -> None:
        manifest = package.manifest
        if (
            remote.get("kind") != kind
            or remote.get("releaseVersion") != manifest.version
            or remote.get("contentDigest") != manifest.content_digest
            or remote.get("packageSha256") != package.package_sha256
        ):
            raise AppError(
                status=409,
                code="DRIVE_TEMPLATE_METADATA_CONFLICT",
                title="Drive template metadata conflict",
                detail="Drive metadataと検証済みpackage内容が一致しません。",
                metadata={"errorCategory": "CONFLICT"},
            )

    @staticmethod
    def _require_same_package(
        expected: ValidatedTemplatePackage,
        actual: ValidatedTemplatePackage,
    ) -> None:
        if (
            expected.manifest.publisher_id != actual.manifest.publisher_id
            or expected.manifest.package_id != actual.manifest.package_id
            or expected.manifest.version != actual.manifest.version
            or expected.manifest.content_digest != actual.manifest.content_digest
            or expected.package_sha256 != actual.package_sha256
        ):
            raise AppError(
                status=409,
                code="DRIVE_TEMPLATE_UPLOAD_CHANGED",
                title="Drive template upload changed",
                detail="Google Driveへ保存されたpackageが送信内容と一致しません。",
            )

    def _sync_status(
        self,
        google_subject: str,
        package: ValidatedTemplatePackage,
        remote: dict[str, Any],
        kind: TemplateDriveKind,
    ) -> str:
        state: Any | None
        if kind == "release":
            with self._database.session() as session:
                state = session.scalar(
                    select(TemplatePackageDriveReleaseSync).where(
                        TemplatePackageDriveReleaseSync.google_subject == google_subject,
                        TemplatePackageDriveReleaseSync.publisher_id
                        == package.manifest.publisher_id,
                        TemplatePackageDriveReleaseSync.package_id
                        == package.manifest.package_id,
                        TemplatePackageDriveReleaseSync.release_version
                        == package.manifest.version,
                    )
                )
        else:
            draft_id = remote.get("draftId")
            state = self._draft_state(google_subject, str(draft_id or ""))
        if state is None:
            return "REMOTE_ONLY"
        return "SYNCED" if self._state_matches(state, package, remote) else "CONFLICT"

    def _save_release_state(
        self,
        google_subject: str,
        package: ValidatedTemplatePackage,
        remote: dict[str, Any],
    ) -> None:
        with self._database.session() as session:
            state = session.scalar(
                select(TemplatePackageDriveReleaseSync).where(
                    TemplatePackageDriveReleaseSync.google_subject == google_subject,
                    TemplatePackageDriveReleaseSync.publisher_id
                    == package.manifest.publisher_id,
                    TemplatePackageDriveReleaseSync.package_id
                    == package.manifest.package_id,
                    TemplatePackageDriveReleaseSync.release_version
                    == package.manifest.version,
                )
            )
            if state is not None and (
                state.content_digest != package.manifest.content_digest
                or state.package_sha256 != package.package_sha256
            ):
                raise AppError(
                    status=409,
                    code="DRIVE_TEMPLATE_RELEASE_CONFLICT",
                    title="Drive template release conflict",
                    detail="同一identity/versionのlocal同期状態とdigestが異なります。",
                )
            if state is None:
                state = TemplatePackageDriveReleaseSync(
                    google_subject=google_subject,
                    publisher_id=package.manifest.publisher_id,
                    package_id=package.manifest.package_id,
                    release_version=package.manifest.version,
                    content_digest=package.manifest.content_digest,
                    package_sha256=package.package_sha256,
                    drive_file_id=str(remote["fileId"]),
                    drive_folder_id=str(remote["folderId"]),
                    drive_version=str(remote["driveVersion"]),
                    file_name=str(remote["fileName"]),
                )
                session.add(state)
            self._update_common_state(state, remote)
            session.commit()

    def _save_draft_state(
        self,
        google_subject: str,
        package: ValidatedTemplatePackage,
        remote: dict[str, Any],
    ) -> None:
        draft_id = str(remote.get("draftId") or "")
        revision = int(remote.get("draftRevision") or 0)
        if not draft_id or revision < 1:
            raise self._draft_conflict("Drive作業版のrevision metadataが不正です。")
        with self._database.session() as session:
            state = session.scalar(
                select(TemplatePackageDriveDraftSync).where(
                    TemplatePackageDriveDraftSync.google_subject == google_subject,
                    TemplatePackageDriveDraftSync.draft_id == draft_id,
                )
            )
            if state is None:
                state = TemplatePackageDriveDraftSync(
                    google_subject=google_subject,
                    draft_id=draft_id,
                    publisher_id=package.manifest.publisher_id,
                    package_id=package.manifest.package_id,
                    revision=revision,
                    base_release_version=remote.get("baseReleaseVersion"),
                    content_digest=package.manifest.content_digest,
                    package_sha256=package.package_sha256,
                    drive_file_id=str(remote["fileId"]),
                    drive_folder_id=str(remote["folderId"]),
                    drive_version=str(remote["driveVersion"]),
                    file_name=str(remote["fileName"]),
                )
                session.add(state)
            else:
                state.publisher_id = package.manifest.publisher_id
                state.package_id = package.manifest.package_id
                state.revision = revision
                state.base_release_version = remote.get("baseReleaseVersion")
                state.content_digest = package.manifest.content_digest
                state.package_sha256 = package.package_sha256
            self._update_common_state(state, remote)
            session.commit()

    @staticmethod
    def _update_common_state(state: Any, remote: dict[str, Any]) -> None:
        state.drive_file_id = str(remote["fileId"])
        state.drive_folder_id = str(remote["folderId"])
        state.drive_version = str(remote["driveVersion"])
        state.drive_head_revision_id = remote.get("headRevisionId")
        state.drive_etag = remote.get("etag")
        state.drive_modified_at = TemplatePackageDriveSyncService._datetime(
            remote.get("modifiedTime")
        )
        state.file_name = str(remote["fileName"])
        state.last_synced_at = datetime.now(UTC)

    def _draft_state(
        self,
        google_subject: str,
        draft_id: str,
    ) -> TemplatePackageDriveDraftSync | None:
        with self._database.session() as session:
            return session.scalar(
                select(TemplatePackageDriveDraftSync).where(
                    TemplatePackageDriveDraftSync.google_subject == google_subject,
                    TemplatePackageDriveDraftSync.draft_id == draft_id,
                )
            )

    @staticmethod
    def _state_matches(
        state: Any,
        package: ValidatedTemplatePackage,
        remote: dict[str, Any],
    ) -> bool:
        return (
            state.content_digest == package.manifest.content_digest
            and state.package_sha256 == package.package_sha256
            and state.drive_file_id == remote.get("fileId")
            and state.drive_version == str(remote.get("driveVersion") or "")
            and (state.drive_head_revision_id or None) == remote.get("headRevisionId")
            and (state.drive_etag or None) == remote.get("etag")
            and (
                not isinstance(state, TemplatePackageDriveDraftSync)
                or state.revision == int(remote.get("draftRevision") or 0)
            )
        )

    @staticmethod
    def _datetime(value: object) -> datetime | None:
        if not isinstance(value, str) or not value:
            return None
        return datetime.fromisoformat(value.replace("Z", "+00:00"))

    @staticmethod
    def _kind(value: object) -> TemplateDriveKind:
        if value == "release":
            return "release"
        if value == "draft":
            return "draft"
        raise AppError(
            status=422,
            code="DRIVE_TEMPLATE_KIND_INVALID",
            title="Drive template kind is invalid",
            detail="Drive template packageの種類が不正です。",
        )

    @staticmethod
    def _draft_conflict(detail: str) -> AppError:
        return AppError(
            status=409,
            code="DRIVE_TEMPLATE_DRAFT_CONFLICT",
            title="Drive template draft conflict",
            detail=detail,
        )

    @staticmethod
    def _retry(action: Callable[[], T]) -> T:
        try:
            return action()
        except AppError as exc:
            if exc.code != "GOOGLE_DRIVE_ERROR":
                raise
        return action()
