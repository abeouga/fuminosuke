from __future__ import annotations

import io
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from PIL import Image
from sqlalchemy import select

from app.core.errors import AppError
from app.db.base import Base
from app.db.models import (
    Template,
    TemplatePackageDriveDraftSync,
    TemplatePackageInstallation,
)
from app.db.session import Database
from app.services.google_drive_service import GoogleDriveService
from app.storage.local_storage import LocalStorageService
from app.template_packages.canonical_models import CanonicalTemplate, CanonicalTemplateQuality
from app.template_packages.drive_sync_service import TemplatePackageDriveSyncService
from app.template_packages.import_service import TemplateImportService
from app.template_packages.package_serializer import TemplatePackageSerializer


class FakeTemplateDrive:
    def __init__(self) -> None:
        self.files: dict[str, dict[str, Any]] = {}
        self.contents: dict[str, bytes] = {}
        self.fail_once: set[str] = set()
        self.next_id = 1

    def template_package_files(
        self,
        expected_subject: str | None = None,
    ) -> list[dict[str, Any]]:
        self._maybe_fail("list")
        assert expected_subject == "subject-1"
        return [dict(item) for item in self.files.values()]

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
    ) -> dict[str, Any]:
        self._maybe_fail("release")
        assert expected_subject == "subject-1"
        matches = [
            item
            for item in self.files.values()
            if item["kind"] == "release"
            and item["publisherId"] == publisher_id
            and item["packageId"] == package_id
            and item["releaseVersion"] == release_version
        ]
        if matches:
            existing = matches[0]
            if (
                existing["contentDigest"] != content_digest
                or existing["packageSha256"] != package_sha256
            ):
                raise _conflict("DRIVE_TEMPLATE_RELEASE_CONFLICT")
            return dict(existing)
        return self._create(
            kind="release",
            content=content,
            publisher_id=publisher_id,
            package_id=package_id,
            release_version=release_version,
            content_digest=content_digest,
            package_sha256=package_sha256,
            file_name=file_name,
        )

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
    ) -> dict[str, Any]:
        self._maybe_fail("draft")
        assert expected_subject == "subject-1"
        if expected_drive_file_id is None:
            return self._create(
                kind="draft",
                content=content,
                publisher_id=publisher_id,
                package_id=package_id,
                release_version=release_version,
                content_digest=content_digest,
                package_sha256=package_sha256,
                file_name=file_name,
                draft_id=draft_id,
                draft_revision=1,
                base_release_version=base_release_version,
            )
        current = self.files[expected_drive_file_id]
        if (
            current["contentDigest"] == content_digest
            and current["packageSha256"] == package_sha256
        ):
            return dict(current)
        if (
            current["draftId"] != draft_id
            or current["draftRevision"] != expected_revision
            or current["driveVersion"] != expected_drive_version
            or current["headRevisionId"] != expected_head_revision_id
            or current["etag"] != expected_etag
        ):
            raise _conflict("DRIVE_TEMPLATE_DRAFT_CONFLICT")
        revision = int(current["draftRevision"]) + 1
        version = str(int(current["driveVersion"]) + 1)
        current.update(
            {
                "releaseVersion": release_version,
                "contentDigest": content_digest,
                "packageSha256": package_sha256,
                "draftRevision": revision,
                "baseReleaseVersion": base_release_version,
                "driveVersion": version,
                "headRevisionId": f"head-{version}",
                "etag": f'"etag-{version}"',
                "modifiedTime": f"2026-09-01T00:00:0{version}Z",
                "fileBytes": len(content),
            }
        )
        self.contents[expected_drive_file_id] = content
        return dict(current)

    def download_template_package_file(
        self,
        file_id: str,
        *,
        expected_kind: str,
        expected_subject: str | None = None,
    ) -> tuple[bytes, dict[str, Any]]:
        self._maybe_fail("download")
        assert expected_subject == "subject-1"
        item = self.files[file_id]
        if item["kind"] != expected_kind:
            raise _conflict("DRIVE_TEMPLATE_PACKAGE_NOT_FOUND")
        return self.contents[file_id], dict(item)

    def _create(
        self,
        *,
        kind: str,
        content: bytes,
        publisher_id: str,
        package_id: str,
        release_version: str,
        content_digest: str,
        package_sha256: str,
        file_name: str,
        draft_id: str | None = None,
        draft_revision: int | None = None,
        base_release_version: str | None = None,
    ) -> dict[str, Any]:
        file_id = f"template-file-{self.next_id}"
        self.next_id += 1
        item = {
            "kind": kind,
            "fileId": file_id,
            "fileName": file_name,
            "folderId": f"template-{kind}-folder",
            "publisherId": publisher_id,
            "packageId": package_id,
            "releaseVersion": release_version,
            "contentDigest": content_digest,
            "packageSha256": package_sha256,
            "draftId": draft_id,
            "draftRevision": draft_revision,
            "baseReleaseVersion": base_release_version,
            "driveVersion": "1",
            "headRevisionId": "head-1",
            "etag": '"etag-1"',
            "modifiedTime": "2026-09-01T00:00:01Z",
            "fileBytes": len(content),
        }
        self.files[file_id] = item
        self.contents[file_id] = content
        return dict(item)

    def _maybe_fail(self, operation: str) -> None:
        if operation in self.fail_once:
            self.fail_once.remove(operation)
            raise AppError(
                status=502,
                code="GOOGLE_DRIVE_ERROR",
                title="Temporary Drive failure",
                detail="一時的なDrive障害です。",
            )


def test_two_independent_data_roots_share_immutable_release_without_activation(
    tmp_path: Path,
) -> None:
    drive = FakeTemplateDrive()
    service_a, database_a, _storage_a = _service(tmp_path / "pc-a", drive)
    service_b, database_b, storage_b = _service(tmp_path / "pc-b", drive)
    package = _package(version="1.0.0", candidate="山田太郎")
    try:
        first = service_a.publish_release(
            package,
            file_name="interview-card-1.0.0.llmocr-template",
            google_subject="subject-1",
        )
        duplicate = service_a.publish_release(
            package,
            file_name="interview-card-1.0.0.llmocr-template",
            google_subject="subject-1",
        )
        assert duplicate["fileId"] == first["fileId"]
        assert len(drive.files) == 1

        remote = service_b.list_items(google_subject="subject-1")
        assert remote[0]["syncStatus"] == "REMOTE_ONLY"
        downloaded, _name = service_b.package_for_inspection(
            first["fileId"],
            kind="release",
            google_subject="subject-1",
        )
        result = TemplateImportService(database_b, storage_b).import_package(
            downloaded,
            expected_package_sha256=first["packageSha256"],
            source_type="DRIVE",
        )
        with database_b.session() as session:
            installation = session.get(TemplatePackageInstallation, result.installation_id)
            template = session.get(Template, result.local_template_id)
            assert installation is not None
            assert installation.source_type == "DRIVE"
            assert installation.trust_status == "UNVERIFIED"
            assert template is not None
            assert template.active_version_id is None
        with zipfile.ZipFile(io.BytesIO(downloaded)) as archive:
            package_payload = b"\n".join(
                archive.read(name) for name in archive.namelist()
            )
        assert first["fileId"].encode() not in package_payload
        drive_metadata = drive.files[first["fileId"]]
        for drive_only_value in (
            drive_metadata["folderId"],
            drive_metadata["headRevisionId"],
            drive_metadata["etag"],
        ):
            assert str(drive_only_value).encode() not in package_payload
        for drive_only_key in (
            b"driveFileId",
            b"driveFolderId",
            b"driveVersion",
            b"headRevisionId",
            b"etag",
            b"draftRevision",
        ):
            assert drive_only_key not in package_payload
    finally:
        database_a.dispose()
        database_b.dispose()


def test_release_digest_conflict_is_never_overwritten(tmp_path: Path) -> None:
    drive = FakeTemplateDrive()
    service, database, _storage = _service(tmp_path, drive)
    try:
        service.publish_release(
            _package(version="1.0.0", candidate="山田太郎"),
            file_name="release.llmocr-template",
            google_subject="subject-1",
        )
        with pytest.raises(AppError) as error:
            service.publish_release(
                _package(version="1.0.0", candidate="佐藤花子"),
                file_name="release.llmocr-template",
                google_subject="subject-1",
            )
        assert error.value.code == "DRIVE_TEMPLATE_RELEASE_CONFLICT"
        assert len(drive.files) == 1
    finally:
        database.dispose()


def test_draft_revision_etag_conflict_between_independent_roots(tmp_path: Path) -> None:
    drive = FakeTemplateDrive()
    service_a, database_a, _storage_a = _service(tmp_path / "pc-a", drive)
    service_b, database_b, _storage_b = _service(tmp_path / "pc-b", drive)
    try:
        created = service_a.publish_draft(
            _package(version="1.0.0-draft.1", candidate="山田太郎"),
            google_subject="subject-1",
            base_release_version="1.0.0",
            draft_id=None,
            expected_revision=None,
        )
        service_b.package_for_inspection(
            created["fileId"],
            kind="draft",
            google_subject="subject-1",
        )
        updated = service_a.publish_draft(
            _package(version="1.0.0-draft.2", candidate="佐藤花子"),
            google_subject="subject-1",
            base_release_version="1.0.0",
            draft_id=created["draftId"],
            expected_revision=1,
        )
        assert updated["draftRevision"] == 2
        with pytest.raises(AppError) as error:
            service_b.publish_draft(
                _package(version="1.0.0-draft.2", candidate="鈴木一郎"),
                google_subject="subject-1",
                base_release_version="1.0.0",
                draft_id=created["draftId"],
                expected_revision=1,
            )
        assert error.value.code == "DRIVE_TEMPLATE_DRAFT_CONFLICT"
        with database_b.session() as session:
            local = session.scalar(select(TemplatePackageDriveDraftSync))
            assert local is not None
            assert local.revision == 1
    finally:
        database_a.dispose()
        database_b.dispose()


def test_temporary_drive_failure_is_retried(tmp_path: Path) -> None:
    drive = FakeTemplateDrive()
    drive.fail_once.add("release")
    service, database, _storage = _service(tmp_path, drive)
    try:
        item = service.publish_release(
            _package(),
            file_name="release.llmocr-template",
            google_subject="subject-1",
        )
        assert item["syncStatus"] == "SYNCED"
    finally:
        database.dispose()


def test_drive_draft_patch_uses_etag_precondition(tmp_path: Path) -> None:
    _sync, database, _storage = _service(tmp_path, FakeTemplateDrive())
    captured: dict[str, Any] = {}

    class Response:
        @staticmethod
        def json() -> dict[str, str]:
            return {"id": "draft-file-1"}

    drive = GoogleDriveService(
        database,
        credentials_provider=lambda _subject: None,  # type: ignore[arg-type,return-value]
        subject_provider=lambda: "subject-1",
    )

    def request(method: str, url: str, **kwargs: object) -> Response:
        captured.update({"method": method, "url": url, **kwargs})
        return Response()

    drive._raw_drive_request = request  # type: ignore[method-assign]
    try:
        result = drive._upload_template_package(
            method="PATCH",
            file_id="draft-file-1",
            content=b"package",
            metadata={"name": "draft.llmocr-template"},
            etag='"etag-7"',
        )
        assert result == {"id": "draft-file-1"}
        assert captured["method"] == "PATCH"
        assert captured["headers"] == {
            "Content-Type": captured["headers"]["Content-Type"],
            "If-Match": '"etag-7"',
        }
    finally:
        database.dispose()


def test_drive_precondition_failure_is_reported_as_draft_conflict(tmp_path: Path) -> None:
    _sync, database, _storage = _service(tmp_path, FakeTemplateDrive())

    class Response:
        status_code = 412

    class Session:
        @staticmethod
        def request(*_args: object, **_kwargs: object) -> Response:
            return Response()

    drive = GoogleDriveService(
        database,
        credentials_provider=lambda _subject: None,  # type: ignore[arg-type,return-value]
        subject_provider=lambda: "subject-1",
    )
    drive._authorized_session = lambda _subject=None: Session()  # type: ignore[method-assign,return-value]
    try:
        with pytest.raises(AppError) as error:
            drive._raw_drive_request("PATCH", "https://example.invalid")
        assert error.value.code == "DRIVE_TEMPLATE_DRAFT_CONFLICT"
    finally:
        database.dispose()


def _service(
    root: Path,
    drive: FakeTemplateDrive,
) -> tuple[TemplatePackageDriveSyncService, Database, LocalStorageService]:
    storage = LocalStorageService(root / "data")
    storage.initialize()
    settings = type(
        "DatabaseConfig",
        (),
        {"url": f"sqlite:///{(storage.root / 'app.db').as_posix()}"},
    )()
    database = Database(settings)  # type: ignore[arg-type]
    Base.metadata.create_all(database.engine)
    return TemplatePackageDriveSyncService(database, drive), database, storage


def _package(*, version: str = "1.0.0", candidate: str = "山田太郎") -> bytes:
    image = io.BytesIO()
    Image.new("RGB", (300, 100), color=(250, 250, 250)).save(image, format="PNG")
    template = CanonicalTemplate.model_validate(
        {
            "name": "面接カード",
            "documentType": "interview-card",
            "mode": "REFERENCE_TEMPLATE",
            "canonicalWidth": 300,
            "canonicalHeight": 100,
            "runtime": {
                "registration": {
                    "preferredMethod": "ORB_HOMOGRAPHY",
                    "minFeatureMatches": 4,
                    "minInliers": 4,
                    "minInlierRatio": 0.3,
                    "maxReprojectionErrorPx": 8,
                    "minEccScore": 0.4,
                },
                "regions": [],
                "fieldMappings": [{
                    "mappingId": "person-name",
                    "targetPath": "document.personName",
                    "label": "氏名",
                    "x": 100,
                    "y": 10,
                    "width": 150,
                    "height": 30,
                }],
                "documentIdentityFields": [],
                "documentName": {"fieldPaths": ["document.personName"]},
                "marks": [],
                "export": {"csvColumns": ["document.personName"]},
            },
            "layout": {
                "layoutSchemaVersion": 3,
                "canonicalWidth": 300,
                "canonicalHeight": 100,
                "cells": [{
                    "id": "value-name",
                    "role": "input",
                    "x": 100,
                    "y": 10,
                    "width": 150,
                    "height": 30,
                    "confidence": 1,
                }],
                "relations": [{
                    "id": "relation-name",
                    "inputCellId": "value-name",
                    "labelCellIds": [],
                    "axis": "horizontal",
                    "order": 1,
                    "targetPath": "document.personName",
                }],
                "recordStructureVerified": False,
            },
            "candidateSets": [{
                "id": "candidate-set-001",
                "name": "氏名候補",
                "values": [candidate],
                "targetPatterns": ["document.personName"],
            }],
            "assets": {"cleanTemplate": "assets/clean-template.png"},
        }
    )
    quality = CanonicalTemplateQuality(
        document_boundary_verified=True,
        layout_verified=True,
        layout_finalized=True,
        record_structure_verified=False,
        definition_approved=False,
        definition_valid=False,
        field_mapping_count=1,
    )
    return TemplatePackageSerializer().build(
        canonical_template=template,
        quality=quality,
        output_layout=None,
        asset_payloads={"assets/clean-template.png": image.getvalue()},
        publisher_id="jp.example",
        package_id="interview-card",
        release_version=version,
        exported_at=datetime(2026, 9, 1, tzinfo=UTC),
    ).content


def _conflict(code: str) -> AppError:
    return AppError(
        status=409,
        code=code,
        title="Drive template conflict",
        detail="Drive template conflict",
    )
