from __future__ import annotations

import asyncio
import re
from contextlib import AsyncExitStack
from dataclasses import replace
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from alembic import command
from alembic.config import Config
from app.cloud.config import CloudSettings
from app.cloud.operations import ClosedBudget, Operations
from app.cloud.registry import AccessDenied, Registry, shutdown_tenants
from app.core.config import DatabaseSettings, Settings
from app.db.session import create_sqlite_engine


def migrate(settings: DatabaseSettings) -> None:
    """The caller supplies the dedicated connection; never load the local .env DB."""
    backend = Path(__file__).resolve().parents[2]
    config = Config()
    config.set_main_option("script_location", str(backend / "alembic"))
    engine = create_sqlite_engine(settings)
    try:
        with engine.connect() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
    finally:
        engine.dispose()


class TenantApplications:
    """Select an entire business runtime, not an ambient request-dependent SQL filter."""

    def __init__(self, settings: Settings, config: CloudSettings, registry: Registry,
                 operations: Operations | None = None) -> None:
        self.settings, self.config, self.registry = settings, config, registry
        self.apps: dict[str, FastAPI] = {}
        self.stack = AsyncExitStack()
        self.lock = asyncio.Lock()
        self.job_gate = asyncio.Semaphore(1)
        self.operations = operations

    async def get(self, tenant: str) -> FastAPI:
        self.registry.require_active(tenant)
        if tenant in self.apps:
            return self.apps[tenant]
        async with self.lock:
            if tenant in self.apps:
                return self.apps[tenant]
            if not re.fullmatch(r"[0-9a-f]{32}", tenant):
                raise ValueError("Invalid internal tenant identity")
            root = self.config.root / "tenants" / tenant
            if root.is_symlink() or root.parent.is_symlink():
                raise ValueError("Tenant storage cannot be a symlink")
            root.mkdir(parents=True, exist_ok=True, mode=0o700)
            db = DatabaseSettings(url="sqlite+pysqlite:///" + (root / "app.db").as_posix())
            await asyncio.to_thread(migrate, db)
            settings = replace(
                self.settings, database=db,
                storage=self.settings.storage.model_copy(update={"data_dir": root}),
            )
            from app.main import create_business_app

            app = create_business_app(settings)
            app.state.cloud_authenticated = True
            app.state.access_check = lambda: self.registry.require_access(tenant)
            app.state.job_gate = self.job_gate
            app.state.shared_logging = True
            app.state.api_budget = (
                self.operations.budget(tenant) if self.operations else ClosedBudget()
            )

            def deletion(kind: str, identity: str) -> None:
                if self.operations is None:
                    ClosedBudget().reserve("delete")
                else:
                    self.operations.deletion(tenant, kind, identity)

            app.state.before_delete = deletion
            async def denied(_request: Request, _error: Exception) -> JSONResponse:
                return JSONResponse({"code": "AUTH_REQUIRED"}, status_code=401)

            app.add_exception_handler(AccessDenied, denied)
            await self.stack.enter_async_context(app.router.lifespan_context(app))
            self.apps[tenant] = app
            return app

    async def start(self) -> None:
        # Resume only admitted owners' queued work after a process restart.
        for tenant in self.registry.active_tenants():
            await self.get(tenant)

    async def close(self) -> None:
        token = shutdown_tenants.set(frozenset(self.apps))
        try:
            await self.stack.aclose()
        finally:
            shutdown_tenants.reset(token)
