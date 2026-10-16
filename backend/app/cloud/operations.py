"""Cloud-only deletion receipts, recovery and admission limits."""
from __future__ import annotations

import re
import shutil
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import cast

from app.ai.api_budget import refused
from app.cloud.config import CloudSettings
from app.cloud.journal import Journal, TenantBudget
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

    def budget(self, tenant: str) -> TenantBudget:
        return TenantBudget(self.journal, tenant, lambda: self.registry.require_access(tenant))

    def deletion(self, tenant: str, kind: str, identity: str) -> None:
        self.registry.require_access(tenant)
        path = tenant_root(self.config.root, tenant) / "app.db"
        with closing(sqlite3.connect(path)) as db:
            busy = db.execute("SELECT 1 FROM processing_jobs WHERE status IN "
                              "('CREATED','QUEUED','PROCESSING') LIMIT 1").fetchone()
        if busy:
            raise refused("処理中・待機中の読み取りを停止してから削除してください。",
                          "BETA_DELETE_BUSY", 409)
        self.journal.delete(tenant, kind, identity)

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
                root = tenant_root(self.config.root, tenant)
                if not (root / "app.db").exists():
                    continue
                if tenant not in databases:
                    databases[tenant] = Database(DatabaseSettings(
                        url="sqlite+pysqlite:///" + (root / "app.db").as_posix(),
                    ))
                database = databases[tenant]
                storage = LocalStorageService(root)
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
            if (storage.root / relative).is_symlink():
                raise ValueError("Symlink in deleted resource path")
            path = storage.resolve(relative)
            if path.exists():
                if any(p.is_symlink() for p in path.rglob("*")):
                    raise ValueError("Symlink in deleted resource data")
                storage.delete_tree(relative)

    def admission(self, tenant: str, upload_bytes: int = 0) -> None:
        root = tenant_root(self.config.root, tenant)
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

