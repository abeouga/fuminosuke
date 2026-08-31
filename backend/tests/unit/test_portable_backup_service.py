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

import pytest
from fastapi.testclient import TestClient

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
    service._lock.acquire()
    try:
        response = client.post("/api/v1/auth/logout")
    finally:
        service._lock.release()

    assert response.status_code == 423
    assert response.json()["code"] == "BACKUP_IN_PROGRESS"
