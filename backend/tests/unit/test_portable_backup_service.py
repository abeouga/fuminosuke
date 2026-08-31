from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import subprocess
import zipfile
from collections.abc import Callable
from io import BytesIO
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

import app.services.portable_restore_service as portable_restore_module
from app.core.config import Settings
from app.core.errors import AppError
from app.db.base import Base
from app.db.models import GoogleOAuthAccount, ProcessingJob, SireName
from app.db.session import Database
from app.main import create_app
from app.services.portable_backup_service import PortableBackupService
from app.storage.local_storage import LocalStorageService


def _service(settings: Settings) -> tuple[PortableBackupService, Database, LocalStorageService]:
    database = Database(settings.database)
    Base.metadata.create_all(database.engine)
    storage = LocalStorageService(settings.storage.data_dir)
    storage.initialize()
    return PortableBackupService(database, storage), database, storage


def test_portable_backup_contains_saved_data_and_verified_manifest(
    settings_factory: Callable[..., Settings],
    tmp_path: Path,
) -> None:
    settings = settings_factory()
    service, database, storage = _service(settings)
    expected_files = {
        "uploads/job-1/source.png": b"source-image",
        "artifacts/job-1/result.json": b'{"value": 1}',
        "templates/template-1/config.yaml": "帳票: 繁殖台帳\n".encode(),
        "exports/job-1/result.xlsx": b"spreadsheet",
    }
    for relative_path, content in expected_files.items():
        storage.write_bytes(relative_path, content)
    storage.write_text("logs/app.log", "excluded log")
    storage.write_bytes("tmp/work.bin", b"excluded temporary data")
    with database.session() as session:
        session.add(
            SireName(
                id="sire-1",
                name="勝忠平",
                normalized_name="勝忠平",
            )
        )
        session.add(
            GoogleOAuthAccount(
                google_subject="google-user-1",
                email="owner@example.com",
                encrypted_refresh_token="encrypted-secret-token",
                scopes="openid email",
                is_active=True,
            )
        )
        session.commit()

    backup = service.create()
    try:
        with zipfile.ZipFile(backup.path) as archive:
            names = set(archive.namelist())
            root = "llm-ocr-portable-backup"
            assert f"{root}/data/app.db" in names
            for relative_path in expected_files:
                assert f"{root}/data/{relative_path}" in names
            assert not any("/logs/" in name or "/tmp/" in name for name in names)
            assert f"{root}/manifest.json" in names
            assert f"{root}/復元手順.txt" in names
            assert f"{root}/バックアップ検証.ps1" in names

            manifest = json.loads(archive.read(f"{root}/manifest.json"))
            assert manifest["formatVersion"] == 1
            assert manifest["requiresLocalCopyBeforeUse"] is True
            assert manifest["restoreTarget"] == "backend/data"
            assert manifest["fileCount"] == len(expected_files) + 1
            indexed = {item["path"]: item for item in manifest["files"]}
            for relative_path, content in expected_files.items():
                item = indexed[f"data/{relative_path}"]
                assert item["size"] == len(content)
                assert item["sha256"] == hashlib.sha256(content).hexdigest()

            snapshot = tmp_path / "restored-app.db"
            snapshot.write_bytes(archive.read(f"{root}/data/app.db"))
        with sqlite3.connect(snapshot) as connection:
            assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
            assert connection.execute(
                "SELECT name FROM sire_names WHERE id = 'sire-1'"
            ).fetchone() == ("勝忠平",)
            assert connection.execute(
                "SELECT encrypted_refresh_token, is_active "
                "FROM google_oauth_accounts WHERE google_subject = 'google-user-1'"
            ).fetchone() == ("", 0)
    finally:
        service.delete(backup.backup_id)
        database.dispose()


def test_embedded_verifier_rejects_unlisted_database_wal(
    settings_factory: Callable[..., Settings],
    tmp_path: Path,
) -> None:
    powershell = shutil.which("pwsh")
    if powershell is None:
        pytest.skip("PowerShell is required to execute the embedded verifier")
    settings = settings_factory()
    service, database, _storage = _service(settings)
    backup = service.create()
    extracted = tmp_path / "portable-restore"
    try:
        with zipfile.ZipFile(backup.path) as archive:
            archive.extractall(extracted)
        backup_root = extracted / "llm-ocr-portable-backup"
        script = backup_root / "バックアップ検証.ps1"
        verified = subprocess.run(
            [powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", script],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        assert verified.returncode == 0, verified.stdout + verified.stderr

        (backup_root / "data" / "app.db-wal").write_bytes(b"stale-wal")
        rejected = subprocess.run(
            [powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", script],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        assert rejected.returncode != 0
        assert "未記載ファイル" in rejected.stdout + rejected.stderr
    finally:
        service.delete(backup.backup_id)
        database.dispose()


def test_portable_backup_rejects_active_jobs(
    settings_factory: Callable[..., Settings],
) -> None:
    settings = settings_factory()
    service, database, _storage = _service(settings)
    with database.session() as session:
        session.add(
            ProcessingJob(
                id="active-job",
                job_type="EXTRACT_DOCUMENT",
                status="PROCESSING",
                request_hash="0" * 64,
            )
        )
        session.commit()

    with pytest.raises(AppError) as raised:
        service.create()

    assert raised.value.status == 409
    assert raised.value.code == "BACKUP_ACTIVE_JOBS"
    database.dispose()


def test_portable_backup_rejects_a_request_that_is_already_mutating_data(
    settings_factory: Callable[..., Settings],
) -> None:
    settings = settings_factory()
    service, database, _storage = _service(settings)

    assert service.begin_mutation() is True
    try:
        with pytest.raises(AppError) as raised:
            service.create()
    finally:
        service.end_mutation()

    assert raised.value.status == 409
    assert raised.value.code == "BACKUP_MUTATIONS_ACTIVE"
    database.dispose()


def test_portable_backup_discards_archive_when_database_changes_during_creation(
    settings_factory: Callable[..., Settings],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = settings_factory()
    service, database, storage = _service(settings)
    storage.write_bytes("uploads/job-1/source.png", b"source")
    original_write_archive = service._write_archive

    def write_then_change_database(*args: object, **kwargs: object) -> object:
        result = original_write_archive(*args, **kwargs)  # type: ignore[arg-type]
        with database.session() as session:
            session.add(
                SireName(
                    id="late-sire",
                    name="後から追加",
                    normalized_name="後から追加",
                )
            )
            session.commit()
        return result

    monkeypatch.setattr(service, "_write_archive", write_then_change_database)

    with pytest.raises(AppError) as raised:
        service.create()

    assert raised.value.code == "BACKUP_DATA_CHANGED"
    backup_dir = storage.resolve("tmp/portable-backups")
    assert list(backup_dir.glob("*.zip")) == []
    assert list(backup_dir.glob("*.json")) == []
    database.dispose()


def test_portable_backup_download_api_returns_reusable_zip_until_deleted(
    client: TestClient,
) -> None:
    storage = client.app.state.storage
    storage.write_bytes("uploads/job-1/source.png", b"source")

    created = client.post("/api/v1/system-backups/portable")

    assert created.status_code == 200
    export = created.json()
    assert export["fileCount"] == 2
    assert export["downloadUrl"].endswith(export["id"])
    response = client.get(export["downloadUrl"])
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    assert response.headers["cache-control"] == "no-store"
    assert "attachment" in response.headers["content-disposition"]
    assert response.headers["x-backup-file-count"] == "2"
    with zipfile.ZipFile(BytesIO(response.content)) as archive:
        assert "llm-ocr-portable-backup/data/app.db" in archive.namelist()
        assert "llm-ocr-portable-backup/data/uploads/job-1/source.png" in archive.namelist()
    resumed = client.get(export["downloadUrl"], headers={"Range": "bytes=0-3"})
    assert resumed.status_code == 206
    assert resumed.headers["content-range"].startswith("bytes 0-3/")
    assert len(resumed.content) == 4
    backup_dir = storage.resolve("tmp/portable-backups")
    assert len(list(backup_dir.glob("*.zip"))) == 1

    deleted = client.delete(export["downloadUrl"])

    assert deleted.status_code == 204
    assert list(backup_dir.glob("*.zip")) == []
    assert list(backup_dir.glob("*.json")) == []


def test_portable_backup_route_requires_authentication_when_oauth_is_enabled(
    settings_factory: Callable[..., Settings],
) -> None:
    settings = settings_factory(
        google_oauth_enabled=True,
        google_oauth_client_id="123456789-example.apps.googleusercontent.com",
        google_oauth_client_secret="test-client-secret",
        auth_jwt_secret="x" * 32,
    )
    database = Database(settings.database)
    Base.metadata.create_all(database.engine)
    database.dispose()

    with TestClient(create_app(settings)) as authenticated_app:
        response = authenticated_app.post("/api/v1/system-backups/portable")

    assert response.status_code == 401


def test_mutating_requests_are_locked_while_a_backup_is_running(client: TestClient) -> None:
    service = client.app.state.portable_backup_service
    service._acquire_operation("backup")
    try:
        response = client.post("/api/v1/auth/logout")
    finally:
        service.end_exclusive_operation()

    assert response.status_code == 423
    assert response.json()["code"] == "BACKUP_IN_PROGRESS"


def test_portable_backup_operation_reports_ready_progress(client: TestClient) -> None:
    operation_id = str(uuid4())

    created = client.post(
        "/api/v1/system-backups/portable",
        json={"operationId": operation_id},
    )
    progress = client.get(
        f"/api/v1/system-backups/portable-operations/{operation_id}"
    )

    assert created.status_code == 200
    assert progress.status_code == 200
    assert progress.json() == {
        "operationId": operation_id,
        "status": "READY",
        "stage": "READY",
        "progressPercent": 100,
        "message": "バックアップの作成が完了しました。",
        "bytesProcessed": created.json()["sourceBytes"],
        "totalBytes": created.json()["sourceBytes"],
        "backupId": created.json()["id"],
        "errorCode": None,
        "errorMessage": None,
        "updatedAt": progress.json()["updatedAt"],
    }


def test_portable_import_restores_database_files_and_runtime(client: TestClient) -> None:
    operation_id = str(uuid4())
    restore_id = str(uuid4())
    storage = client.app.state.storage
    database = client.app.state.database
    _stamp_test_database(database)
    storage.write_bytes("uploads/original/source.png", b"before-restore")
    with database.session() as session:
        session.add(
            SireName(
                id="portable-original",
                name="復元対象",
                normalized_name="復元対象",
            )
        )
        session.add(
            GoogleOAuthAccount(
                google_subject="portable-google-user",
                email="portable@example.com",
                encrypted_refresh_token="must-not-travel",
                scopes="openid email",
                is_active=True,
            )
        )
        session.commit()

    exported = client.post(
        "/api/v1/system-backups/portable",
        json={"operationId": operation_id},
    )
    archive = client.get(exported.json()["downloadUrl"])
    assert exported.status_code == 200
    assert archive.status_code == 200

    storage.write_bytes("uploads/original/source.png", b"changed-after-export")
    storage.write_bytes("uploads/later/extra.png", b"must-be-removed")
    with database.session() as session:
        session.add(
            SireName(
                id="portable-later",
                name="後から追加",
                normalized_name="後から追加",
            )
        )
        session.commit()

    validated = client.post(
        f"/api/v1/system-backups/portable-imports/{restore_id}",
        files={"file": ("portable-backup.zip", archive.content, "application/zip")},
    )

    assert validated.status_code == 200, validated.text
    assert validated.json()["status"] == "READY"
    assert validated.json()["stage"] == "READY_TO_APPLY"
    assert validated.json()["progressPercent"] == 75
    assert validated.json()["fileCount"] == exported.json()["fileCount"]

    applied = client.post(
        f"/api/v1/system-backups/portable-imports/{restore_id}/apply"
    )

    assert applied.status_code == 200, applied.text
    assert applied.json()["status"] == "COMPLETED"
    assert applied.json()["progressPercent"] == 100
    assert applied.json()["rollbackExpiresAt"] is not None
    assert client.app.state.job_runner.is_running is True
    assert storage.read_bytes("uploads/original/source.png") == b"before-restore"
    assert storage.exists("uploads/later/extra.png") is False
    with database.session() as session:
        assert session.get(SireName, "portable-original") is not None
        assert session.get(SireName, "portable-later") is None
        oauth = session.get(GoogleOAuthAccount, "portable-google-user")
        assert oauth is not None
        assert oauth.encrypted_refresh_token == ""
        assert oauth.is_active is False

    status_response = client.get(
        f"/api/v1/system-backups/portable-imports/{restore_id}"
    )
    assert status_response.status_code == 200
    assert status_response.json()["status"] == "COMPLETED"


def test_invalid_portable_import_does_not_change_live_data(client: TestClient) -> None:
    restore_id = str(uuid4())
    storage = client.app.state.storage
    storage.write_bytes("uploads/live/source.png", b"live-data")

    response = client.post(
        f"/api/v1/system-backups/portable-imports/{restore_id}",
        files={"file": ("broken.zip", b"this-is-not-a-zip", "application/zip")},
    )

    assert response.status_code == 422
    assert response.json()["code"] == "RESTORE_ARCHIVE_INVALID"
    assert storage.read_bytes("uploads/live/source.png") == b"live-data"
    status_response = client.get(
        f"/api/v1/system-backups/portable-imports/{restore_id}"
    )
    assert status_response.status_code == 200
    assert status_response.json()["status"] == "FAILED"
    assert status_response.json()["progressPercent"] < 100


def test_portable_import_rolls_back_a_partial_filesystem_swap(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = client.app.state.database
    storage = client.app.state.storage
    _stamp_test_database(database)
    storage.write_bytes("uploads/source.png", b"backup-version")
    with database.session() as session:
        session.add(SireName(id="backup-row", name="退避", normalized_name="退避"))
        session.commit()
    exported = client.post(
        "/api/v1/system-backups/portable",
        json={"operationId": str(uuid4())},
    )
    archive = client.get(exported.json()["downloadUrl"])

    storage.write_bytes("uploads/source.png", b"current-version")
    with database.session() as session:
        session.add(SireName(id="current-row", name="現行", normalized_name="現行"))
        session.commit()
    restore_id = str(uuid4())
    validated = client.post(
        f"/api/v1/system-backups/portable-imports/{restore_id}",
        files={"file": ("portable-backup.zip", archive.content, "application/zip")},
    )
    assert validated.status_code == 200

    original_replace = portable_restore_module.os.replace

    def fail_before_installing_uploads(source: object, destination: object) -> None:
        source_path = Path(source)  # type: ignore[arg-type]
        destination_path = Path(destination)  # type: ignore[arg-type]
        if (
            source_path.name == "uploads"
            and "portable-imports" in source_path.parts
            and destination_path == storage.resolve("uploads")
        ):
            raise OSError("simulated interrupted swap")
        original_replace(source, destination)  # type: ignore[arg-type]

    monkeypatch.setattr(portable_restore_module.os, "replace", fail_before_installing_uploads)

    applied = client.post(
        f"/api/v1/system-backups/portable-imports/{restore_id}/apply"
    )

    assert applied.status_code == 500
    assert applied.json()["code"] == "RESTORE_APPLY_FAILED"
    assert storage.read_bytes("uploads/source.png") == b"current-version"
    with database.session() as session:
        assert session.get(SireName, "current-row") is not None
        assert session.get(SireName, "backup-row") is not None
    assert client.app.state.job_runner.is_running is True


def test_requests_are_locked_while_a_restore_is_applying(client: TestClient) -> None:
    service = client.app.state.portable_backup_service
    service._acquire_operation("restore")
    try:
        normal_request = client.get("/api/v1/templates")
        health = client.get("/api/v1/health")
    finally:
        service.end_exclusive_operation()

    assert normal_request.status_code == 423
    assert normal_request.json()["code"] == "BACKUP_IN_PROGRESS"
    assert health.status_code == 200


def _stamp_test_database(database: Database) -> None:
    with database.engine.begin() as connection:
        connection.execute(
            text("CREATE TABLE IF NOT EXISTS alembic_version (version_num VARCHAR(32) NOT NULL)")
        )
        connection.execute(text("DELETE FROM alembic_version"))
        connection.execute(
            text(
                "INSERT INTO alembic_version (version_num) "
                "VALUES ('0022_archive_in_use_templates')"
            )
        )
