"""Offline snapshots and verified restore into a new, unpublished cloud root."""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import sqlite3
import tempfile
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Any, cast
from uuid import UUID, uuid4

from app.cloud.config import CloudSettings
from app.cloud.journal import Journal, ReceiptStore, encoded
from app.cloud.lease import runtime_lease
from app.cloud.operations import Operations
from app.cloud.registry import Registry
from app.core.config import Settings


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024**2), b""):
            digest.update(chunk)
    return digest.hexdigest()


def relative_file(name: str) -> Path:
    path = PurePosixPath(name)
    if (path.is_absolute() or not path.parts or any(p in {".", ".."} for p in path.parts)
            or path.as_posix() != name or "\\" in name or ":" in name
            or any(p in {"logs", "tmp", ".runtime.lock", ".restore-incomplete"}
                   for p in path.parts)):
        raise ValueError("Unsafe backup member")
    if name not in {"registry.db", "operations.db", "cloud.json"} and not re.fullmatch(
        r"control/[a-z_]+\.jsonl", name,
    ) and not re.fullmatch(
        r"tenants/[0-9a-f]{32}/.+", name,
    ):
        raise ValueError("Unexpected backup namespace")
    return Path(*path.parts)


def snapshot(root: Path, destination: Path, installation: str) -> dict[str, Any]:
    files = {}
    for source in sorted(root.rglob("*")):
        if source.is_symlink():
            raise ValueError("Symlinks cannot be backed up")
        if not source.is_file():
            continue
        name = source.relative_to(root).as_posix()
        if (any(p in {"tmp", "logs", ".runtime.lock"} for p in source.relative_to(root).parts)
                or name.endswith(("-wal", "-shm"))):
            continue
        target = destination / relative_file(name)
        target.parent.mkdir(parents=True, exist_ok=True)
        if name in {"registry.db", "operations.db"} or re.fullmatch(
            r"tenants/[0-9a-f]{32}/app.db", name,
        ):
            with (closing(sqlite3.connect(source)) as origin,
                  closing(sqlite3.connect(target)) as copied):
                if origin.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise ValueError("Source database integrity failure")
                origin.backup(copied)
                copied.execute("PRAGMA journal_mode=DELETE")
                if name == "registry.db":
                    copied.execute("DELETE FROM sessions")
                    copied.execute("DELETE FROM logins")
                copied.commit()
        else:
            shutil.copyfile(source, target)
        files[name] = {"sha256": file_hash(target), "size": target.stat().st_size}
    if not {"registry.db", "operations.db"}.issubset(files):
        raise ValueError("Incomplete cloud snapshot")
    return {"version": 1, "installation": installation,
            "createdAt": datetime.now(UTC).isoformat(), "files": files}


class S3Backups:
    def __init__(self, client: Any, bucket: str, installation: str) -> None:
        self.client, self.bucket, self.installation = client, bucket, installation

    def prefix(self, backup_id: str) -> str:
        if str(UUID(backup_id)) != backup_id:
            raise ValueError("Invalid backup identity")
        return f"{self.installation}/backups/{backup_id}/"

    def upload(self, source: Path, manifest: dict[str, Any]) -> str:
        identity = str(uuid4())
        prefix = self.prefix(identity)
        for name in manifest["files"]:
            with (source / relative_file(name)).open("rb") as stream:
                self.client.put_object(Bucket=self.bucket, Key=prefix + name, Body=stream,
                                       IfNoneMatch="*", ServerSideEncryption="AES256")
        # A backup is eligible for restore only after the complete manifest is durable.
        self.client.put_object(Bucket=self.bucket, Key=prefix + "manifest.json",
                               Body=encoded(manifest), ContentType="application/json",
                               IfNoneMatch="*", ServerSideEncryption="AES256")
        return identity

    def download(self, identity: str, target: Path) -> dict[str, Any]:
        prefix = self.prefix(identity)
        body = self.client.get_object(Bucket=self.bucket, Key=prefix + "manifest.json")["Body"]
        try:
            content = body.read(8 * 1024**2 + 1)
        finally:
            body.close()
        if len(content) > 8 * 1024**2:
            raise ValueError("Backup manifest too large")
        manifest = json.loads(content)
        created = datetime.fromisoformat(manifest["createdAt"])
        age = datetime.now(UTC) - created
        if (created.utcoffset() != timedelta(0)
                or not -timedelta(minutes=5) <= age <= timedelta(days=7)):
            raise ValueError("Backup is outside the seven-day recovery window")
        required = (
            {"registry.db", "operations.db"} if manifest["version"] == 1 else
            {"cloud.json", "control/users.jsonl", "control/receipts.jsonl", "control/objects.jsonl"}
        )
        if (manifest["version"] not in {1, 2} or manifest["installation"] != self.installation
                or not required.issubset(manifest["files"])
                or len(manifest["files"]) > 100_000
                or sum(f["size"] for f in manifest["files"].values()) > 6 * 1024**3):
            raise ValueError("Invalid backup manifest")
        for name, metadata in manifest["files"].items():
            if manifest["version"] == 1 and (name == "cloud.json" or name.startswith("control/")):
                raise ValueError("Unexpected SQLite backup member")
            if manifest["version"] == 2:
                from app.cloud.pg_backup import CONTROL_TABLES
                allowed = {"cloud.json", *(f"control/{t}.jsonl" for t in CONTROL_TABLES)}
                if name not in allowed and not re.fullmatch(
                    r"tenants/[0-9a-f]{32}/(objects/[0-9a-f]{64}|database/[a-z_]+\.jsonl)", name,
                ):
                    raise ValueError("Unexpected PostgreSQL backup member")
                if name == "cloud.json" and metadata["size"] > 64 * 1024:
                    raise ValueError("Backup tenant metadata is too large")
            path = target / relative_file(name)
            if not isinstance(metadata["size"], int) or not 0 <= metadata["size"] <= 1024**3:
                raise ValueError("Invalid backup file size")
            path.parent.mkdir(parents=True, exist_ok=True)
            body = self.client.get_object(Bucket=self.bucket, Key=prefix + name)["Body"]
            size = 0
            try:
                with path.open("xb") as stream:
                    while chunk := body.read(1024**2):
                        size += len(chunk)
                        if size > metadata["size"]:
                            raise ValueError("Oversized backup member")
                        stream.write(chunk)
            finally:
                body.close()
            if size != metadata["size"] or file_hash(path) != metadata["sha256"]:
                raise ValueError("Backup content mismatch")
        return cast(dict[str, Any], manifest)


def backup(settings: Settings, config: CloudSettings, receipts: ReceiptStore,
           destination: S3Backups, workspace: Path) -> str:
    if config.database_url is not None:
        from app.cloud.pg_backup import backup_postgres
        return backup_postgres(settings, config, receipts, destination, workspace)
    root = config.root
    if not workspace.is_absolute() or workspace.resolve().is_relative_to(root):
        raise ValueError("Backup workspace must be outside the cloud root")
    workspace.mkdir(parents=True, exist_ok=True)
    with runtime_lease(root):
        registry = Registry(root, config.max_users)
        journal = Journal(root, str(config.installation_id), receipts)
        journal.reconcile()
        operations = Operations(settings, config, registry, journal)
        operations.replay_deletions()
        with tempfile.TemporaryDirectory(dir=workspace) as temporary:
            staging = Path(temporary)
            manifest = snapshot(root, staging, journal.installation)
            return destination.upload(staging, manifest)


def restore(settings: Settings, config: CloudSettings, source: S3Backups, identity: str,
            target: Path, receipts: ReceiptStore, *,
            target_url: str | None = None) -> dict[str, Any]:
    if config.database_url is not None:
        if target_url is None:
            raise ValueError("PostgreSQL restore requires a separate empty target database")
        from app.cloud.pg_backup import restore_postgres
        return restore_postgres(settings, config, source, identity, target, receipts, target_url)
    if (not target.is_absolute() or target.exists() or target.is_symlink()
            or target.resolve().is_relative_to(config.root)
            or config.root.is_relative_to(target.resolve())):
        raise ValueError("Restore requires a new, separate, empty destination")
    target.mkdir(parents=True, exist_ok=False, mode=0o700)
    marker = target / ".restore-incomplete"
    marker.touch()
    with runtime_lease(target):
        manifest = source.download(identity, target)
        for db_path in [target / "registry.db", target / "operations.db",
                        *(target / "tenants").glob("*/app.db")]:
            with closing(sqlite3.connect(db_path)) as db:
                if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise ValueError("Restored database integrity failure")
                if db_path.name == "registry.db":
                    db.execute("DELETE FROM sessions")
                    db.execute("DELETE FROM logins")
                    # A backup cannot prove that an owner was not disabled afterwards.
                    # Only explicit operator re-admission may restore account access.
                    db.execute("UPDATE users SET active=0")
                    db.commit()
        restored = config.model_copy(update={"root": target})
        registry = Registry(target, config.max_users)
        journal = Journal(target, str(config.installation_id), receipts)
        journal.reconcile()  # Imports orphan reservations and later deletions before any runtime.
        Operations(settings, restored, registry, journal).replay_deletions()
        marker.unlink()
        return {"backupId": identity, "filesVerified": len(manifest["files"]),
                "sessionsRevoked": True, "usersRequireReadmission": True,
                "remoteJournalReconciled": True}
