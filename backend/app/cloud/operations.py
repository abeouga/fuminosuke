"""Cloud-only deletion receipts, recovery and admission limits."""
from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import cast

from app.ai.api_budget import refused
from app.cloud.config import CloudSettings
from app.cloud.journal import Journal, TenantBudget, s3_client
from app.cloud.registry import Registry
from app.core.config import DatabaseSettings, Settings
from app.db.models import ProcessingJob, Template
from app.db.session import Database
from app.jobs.runner import JobRunner
from app.services.job_service import JobService
from app.services.template_management_service import TemplateManagementService
from app.storage.local_storage import LocalStorageService


class ClosedBudget:
    def status(self) -> dict[str, int]:
        return {"used": 0, "limit": 0, "remaining": 0}

    def reserve(self, purpose: str) -> str:
        raise refused("共通利用記録が未設定です。AIは利用できません。",
                      "BETA_JOURNAL_UNAVAILABLE", 503)


def tenant_root(root: Path, tenant: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{32}", tenant):
        raise ValueError("Invalid tenant")
    path = root / "tenants" / tenant
    if path.is_symlink() or path.parent.is_symlink() or root.is_symlink():
        raise ValueError("Symlink tenant root")
    if path.resolve().parent != (root / "tenants").resolve():
        raise ValueError("Tenant path escapes its root")
    return path


class Operations:
    def __init__(self, settings: Settings, config: CloudSettings,
                 registry: Registry, journal: Journal) -> None:
        self.settings, self.config = settings, config
        self.registry, self.journal = registry, journal
        self.storages: dict[str, LocalStorageService] = {}

    def budget(self, tenant: str) -> TenantBudget:
        return TenantBudget(self.journal, tenant, lambda: self.registry.require_access(tenant))

    def deletion(self, tenant: str, kind: str, identity: str) -> None:
        self.registry.require_access(tenant)
        database = self.database(tenant)
        if database is None:
            raise ValueError("Tenant database is missing")
        try:
            with database.session() as db:
                from sqlalchemy import select
                busy = db.scalar(select(ProcessingJob.id).where(
                    ProcessingJob.status.in_(("CREATED", "QUEUED", "PROCESSING"))
                ).limit(1))
        finally:
            database.dispose()
        if busy:
            raise refused("処理中・待機中の読み取りを停止してから削除してください。",
                          "BETA_DELETE_BUSY", 409)
        self.journal.delete(tenant, kind, identity)

    def database(self, tenant: str) -> Database | None:
        root = tenant_root(self.config.root, tenant)
        if self.registry.control is not None:
            from app.cloud.postgres import tenant_settings
            with self.registry.control.connect("operations") as db:
                exists = db.execute("SELECT 1 FROM pg_namespace WHERE nspname=?",
                                    ("tenant_" + tenant,)).fetchone()
            if not exists:
                return None
            return Database(tenant_settings(self.registry.control.url, tenant),
                            transaction_fence=self.registry.control.fence)
        if not (root / "app.db").exists():
            return None
        return Database(DatabaseSettings(url="sqlite+pysqlite:///" + (root / "app.db").as_posix()))

    def storage(self, tenant: str) -> LocalStorageService:
        root = tenant_root(self.config.root, tenant)
        if tenant in self.storages:
            return self.storages[tenant]
        storage: LocalStorageService
        if self.registry.control is not None:
            from app.storage.s3_storage import S3StorageService
            storage = S3StorageService(root, control=self.registry.control, client=s3_client(),
                                       bucket=str(self.config.object_bucket), tenant=tenant)
        else:
            storage = LocalStorageService(root)
        self.storages[tenant] = storage
        return storage

    def delete_tenant(self, tenant: str) -> None:
        # Host-only, under the offline runtime lease; all workers have already stopped.
        with self.registry.connect() as db:
            if not db.execute("SELECT 1 FROM users WHERE tenant_id=?", (tenant,)).fetchone():
                raise ValueError("Unknown admitted tenant")
        self.journal.delete(tenant, "tenant", tenant)
        self.replay_deletions()

    def replay_deletions(self) -> None:
        with self.journal.connect() as db:
            records = self.journal.read(db)
        deleted = {r["tenant"] for r in records
                   if r["kind"] == "delete" and r["resourceKind"] == "tenant"}
        for tenant in deleted:
            self.registry.mark_deleted(tenant)
            path = tenant_root(self.config.root, tenant)
            if self.registry.control is not None:
                storage = self.storage(tenant)
                for directory in ("uploads", "artifacts", "exports", "templates"):
                    storage.delete_tree(directory)
                with self.registry.control.connect("operations") as db:
                    # Identity was validated by tenant_root; never interpolate a browser schema.
                    db.execute(f'DROP SCHEMA IF EXISTS "tenant_{tenant}" CASCADE')
            if path.exists():
                # Reject every nested symlink before recursive removal.
                if any(p.is_symlink() for p in path.rglob("*")):
                    raise ValueError("Symlink in deleted tenant data")
                shutil.rmtree(path)
        databases: dict[str, Database] = {}
        try:
            for record in records:
                tenant = record["tenant"]
                if (record["kind"] != "delete" or tenant in deleted
                        or record["resourceKind"] == "tenant"):
                    continue
                if tenant not in databases:
                    database = self.database(tenant)
                    if database is None:
                        self._remove_deleted_files(self.storage(tenant), record["resourceKind"],
                                                   record["resourceId"])
                        continue
                    databases[tenant] = database
                database = databases[tenant]
                storage = self.storage(tenant)
                kind, identity = record["resourceKind"], record["resourceId"]
                with database.session() as db:
                    present = db.get(Template if kind == "template" else ProcessingJob, identity)
                if present is None:
                    # The business commit can precede a crash during filesystem cleanup.
                    # A durable deletion receipt still owns this exact resource's residue.
                    self._remove_deleted_files(storage, kind, identity)
                    continue
                if kind == "template":
                    TemplateManagementService(database, storage).delete_template(identity)
                else:
                    # Deletion uses no worker. Preserve the existing reference/status guards.
                    with database.transaction() as db:
                        job = db.get(ProcessingJob, identity)
                        if job is not None and job.status == "PROCESSING":
                            # The offline lease proves no worker owns this restored old state.
                            job.status, job.cancel_requested = "CANCELLED", True
                    JobService(settings=self.settings, database=database, storage=storage,
                               runner=cast(JobRunner, None)).delete(identity, confirm=True)
        finally:
            for database in databases.values():
                database.dispose()

    @staticmethod
    def _remove_deleted_files(storage: LocalStorageService, kind: str, identity: str) -> None:
        directories = ("templates",) if kind == "template" else ("uploads", "artifacts", "exports")
        for directory in directories:
            relative = f"{directory}/{identity}"
            if storage.requires_immutable_paths:
                storage.delete_tree(relative)
                continue
            if (storage.root / relative).is_symlink():
                raise ValueError("Symlink in deleted resource path")
            path = storage.resolve(relative)
            if path.exists():
                if any(p.is_symlink() for p in path.rglob("*")):
                    raise ValueError("Symlink in deleted resource data")
                storage.delete_tree(relative)

    def admission(self, tenant: str, upload_bytes: int = 0) -> None:
        root = tenant_root(self.config.root, tenant)
        if self.registry.control is not None:
            with self.registry.control.connect("objects") as db:
                personal = db.execute(
                    "SELECT COALESCE(sum(bytes),0) FROM objects "
                    "WHERE tenant=? AND state!='DELETED'", (tenant,),
                ).scalar_one()
                total = db.execute("SELECT COALESCE(sum(bytes),0) FROM objects "
                                   "WHERE state!='DELETED'").scalar_one()
            if personal + upload_bytes > 500 * 1024**2 or total + upload_bytes > 5 * 1024**3:
                raise refused("保存容量の上限に達しました。不要な帳票を削除してください。",
                              "BETA_STORAGE_LIMIT", 413)
            return
        total = 0
        personal = 0
        for path in (self.config.root / "tenants").rglob("*"):
            if path.is_symlink():
                raise ValueError("Symlink in cloud storage")
            if path.is_file():
                size = path.stat().st_size
                total += size
                if path.is_relative_to(root):
                    personal += size
        if personal + upload_bytes > 500 * 1024**2 or total + upload_bytes > 5 * 1024**3:
            raise refused("保存容量の上限に達しました。不要な帳票を削除してください。",
                          "BETA_STORAGE_LIMIT", 413)

