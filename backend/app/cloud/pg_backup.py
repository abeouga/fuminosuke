"""Offline, typed PostgreSQL snapshots and verified S3-object recovery.

Only a new empty database can be restored. The source runtime remains fenced out
throughout recovery; the external receipt chain is reconciled before reopening.
"""

from __future__ import annotations

import base64
import hashlib
import json
import tempfile
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import (
    Connection,
    Date,
    DateTime,
    LargeBinary,
    MetaData,
    Numeric,
    Table,
    create_engine,
    func,
    inspect,
    select,
    text,
)

from app.cloud.backup import S3Backups, file_hash
from app.cloud.config import CloudSettings
from app.cloud.journal import Journal, ReceiptStore
from app.cloud.operations import Operations
from app.cloud.postgres import initialize_tenant, missing_tables, tenant_settings
from app.cloud.registry import Registry
from app.cloud.runtime import cloud_runtime
from app.core.config import Settings
from app.db.metadata import Base
from app.db.session import Database

CONTROL_TABLES = (
    "registry_version",
    "users",
    "sessions",
    "logins",
    "deleted_tenants",
    "receipts",
    "objects",
)


def encode_value(value: Any) -> Any:
    if isinstance(value, bytes):
        return base64.b64encode(value).decode()
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    return value


def decode_value(value: Any, column: Any) -> Any:
    if value is None:
        return None
    if isinstance(column.type, DateTime):
        return datetime.fromisoformat(value)
    if isinstance(column.type, Date):
        return date.fromisoformat(value)
    if isinstance(column.type, LargeBinary):
        return base64.b64decode(value, validate=True)
    if isinstance(column.type, Numeric):
        return Decimal(value)
    return value


def dump_table(connection: Connection, table: Table, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as output:
        output.write(json.dumps({"version": 1, "columns": list(table.columns.keys())}) + "\n")
        # Authentication challenges/sessions never travel in a backup.
        if table.name in {"sessions", "logins"}:
            return
        for row in connection.execute(select(table).execution_options(yield_per=100)):
            output.write(
                json.dumps(
                    [encode_value(v) for v in row], ensure_ascii=False, separators=(",", ":")
                )
                + "\n"
            )


def load_table(connection: Connection, table: Table, path: Path) -> None:
    columns = list(table.columns)
    with path.open(encoding="utf-8") as source:
        header = json.loads(source.readline(1024**2))
        if header != {"version": 1, "columns": [c.name for c in columns]}:
            raise ValueError("Backup table schema differs from the installed product")
        batch = []
        batch_bytes = 0
        while line := source.readline(16 * 1024**2 + 1):
            if len(line) > 16 * 1024**2:
                raise ValueError("Backup row too large")
            values = json.loads(line)
            if not isinstance(values, list) or len(values) != len(columns):
                raise ValueError("Invalid backup row")
            batch.append({c.name: decode_value(v, c) for c, v in zip(columns, values, strict=True)})
            batch_bytes += len(line.encode("utf-8"))
            if len(batch) == 100 or batch_bytes >= 4 * 1024**2:
                connection.execute(table.insert(), batch)
                batch.clear()
                batch_bytes = 0
        if batch:
            connection.execute(table.insert(), batch)
    reset_sequences(connection, table)


def reset_sequences(connection: Connection, table: Table) -> None:
    """Explicit restored IDs must advance PostgreSQL's independent SERIAL counters."""
    quote = connection.dialect.identifier_preparer.quote
    schema = connection.execute(text("SELECT current_schema()")).scalar_one()
    qualified = quote(schema) + "." + quote(table.name)
    for column in table.primary_key.columns:
        sequence = connection.execute(
            text("SELECT pg_get_serial_sequence(:table,:column)"),
            {"table": qualified, "column": column.name},
        ).scalar_one()
        if sequence is None:
            continue
        maximum = connection.execute(select(func.max(column))).scalar_one()
        connection.execute(
            text("SELECT setval(:sequence, :value, :called)"),
            {
                "sequence": sequence,
                "value": max(int(maximum or 1), 1),
                "called": maximum is not None and maximum >= 1,
            },
        )


def backup_postgres(
    settings: Settings,
    config: CloudSettings,
    receipts: ReceiptStore,
    destination: S3Backups,
    workspace: Path,
) -> str:
    if not workspace.is_absolute() or workspace.resolve().is_relative_to(config.root):
        raise ValueError("Backup workspace must be outside the cloud root")
    workspace.mkdir(parents=True, exist_ok=True)
    with cloud_runtime(config) as control:
        if control is None:
            raise ValueError("PostgreSQL configuration is required")
        registry = Registry(config.root, config.max_users, control=control)
        journal = Journal(config.root, control.installation, receipts, control=control)
        journal.reconcile()
        operations = Operations(settings, config, registry, journal)
        operations.replay_deletions()
        with tempfile.TemporaryDirectory(dir=workspace) as temporary:
            root = Path(temporary)
            tenants = {}
            with control.connect("backup") as db:
                for name in CONTROL_TABLES:
                    table = Table(name, MetaData(), autoload_with=db.connection)
                    dump_table(db.connection, table, root / "control" / (name + ".jsonl"))
                owners = db.execute("SELECT tenant_id FROM users ORDER BY tenant_id").fetchall()
            for (tenant,) in owners:
                database = operations.database(tenant)
                if database is None:
                    continue
                try:
                    with database.engine.begin() as db:
                        tenants[tenant] = db.execute(
                            text("SELECT fingerprint FROM cloud_schema_version WHERE id=1")
                        ).scalar_one()
                        for table in sorted(Base.metadata.tables.values(), key=lambda t: t.name):
                            dump_table(
                                db,
                                table,
                                root / "tenants" / tenant / "database" / (table.name + ".jsonl"),
                            )
                finally:
                    database.dispose()
                storage = operations.storage(tenant)
                with control.connect("backup") as db:
                    objects = db.execute(
                        "SELECT path,sha,bytes FROM objects WHERE tenant=? "
                        "AND state='READY' ORDER BY path",
                        (tenant,),
                    ).fetchall()
                for name, sha, size in objects:
                    content = storage.read_bytes(name)
                    path = (
                        root
                        / "tenants"
                        / tenant
                        / "objects"
                        / hashlib.sha256(name.encode()).hexdigest()
                    )
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(content)
                    if file_hash(path) != sha or path.stat().st_size != size:
                        raise ValueError("Backup object differs from its durable catalog")
            (root / "cloud.json").write_text(json.dumps({"tenants": tenants}), encoding="utf-8")
            files = {
                p.relative_to(root).as_posix(): {"sha256": file_hash(p), "size": p.stat().st_size}
                for p in root.rglob("*")
                if p.is_file()
            }
            from datetime import UTC

            manifest = {
                "version": 2,
                "installation": control.installation,
                "createdAt": datetime.now(UTC).isoformat(),
                "files": files,
            }
            return destination.upload(root, manifest)


def restore_postgres(
    settings: Settings,
    config: CloudSettings,
    source: S3Backups,
    identity: str,
    target: Path,
    receipts: ReceiptStore,
    target_url: str,
) -> dict[str, Any]:
    if (
        config.database_url is None
        or target_url == config.database_url.get_secret_value()
        or not target.is_absolute()
        or target.exists()
        or target.is_symlink()
        or target.resolve().is_relative_to(config.root)
        or config.root.is_relative_to(target.resolve())
    ):
        raise ValueError("Restore requires a new database and a separate empty workspace")
    probe = create_engine(target_url)
    try:
        with probe.connect() as db:
            if db.execute(
                text(
                    "SELECT 1 FROM pg_namespace "
                    "WHERE nspname NOT IN ('public','information_schema') "
                    "AND nspname NOT LIKE 'pg_%'"
                )
            ).first():
                raise ValueError("Restore database is not empty")
            if inspect(db).get_table_names(schema="public"):
                raise ValueError("Restore database contains unrelated public tables")
    finally:
        probe.dispose()
    target.mkdir(parents=True, exist_ok=False, mode=0o700)
    marker = target / ".restore-incomplete"
    marker.touch()
    from pydantic import SecretStr

    restored = config.model_copy(update={"root": target, "database_url": SecretStr(target_url)})
    with cloud_runtime(config, allow_retired=True) as origin, cloud_runtime(restored) as control:
        if control is None:
            raise ValueError("Missing PostgreSQL runtime")
        with control.connect("restore") as db:
            db.execute("UPDATE recovery_state SET state='RESTORING' WHERE id=1")
        manifest = source.download(identity, target)
        if manifest["version"] != 2:
            raise ValueError("Use the matching PostgreSQL backup format")
        registry = Registry(target, config.max_users, control=control)
        journal = Journal(target, control.installation, receipts, control=control)
        with control.connect("restore") as db:
            for name in reversed(CONTROL_TABLES):
                db.execute(f'DELETE FROM "{name}"')
            for name in CONTROL_TABLES:
                table = Table(name, MetaData(), autoload_with=db.connection)
                load_table(db.connection, table, target / "control" / (name + ".jsonl"))
            db.execute("UPDATE users SET active=0")
            db.execute("DELETE FROM sessions")
            db.execute("DELETE FROM logins")
            ready_objects = db.execute(
                "SELECT tenant,path,sha,bytes FROM objects WHERE state='READY'"
            ).fetchall()
            db.execute("UPDATE objects SET state='STAGING' WHERE state='READY'")
        metadata = json.loads((target / "cloud.json").read_text(encoding="utf-8"))
        operations = Operations(settings, restored, registry, journal)
        with control.connect("restore") as db:
            admitted = {r[0] for r in db.execute("SELECT tenant_id FROM users")}
        for tenant, fingerprint in metadata["tenants"].items():
            if tenant not in admitted:
                raise ValueError("Backup tenant is not admitted")
            db_settings = tenant_settings(target_url, tenant)
            initialize_tenant(db_settings, fence=control.fence)
            database = Database(db_settings, transaction_fence=control.fence)
            try:
                with database.engine.begin() as db:
                    actual = db.execute(
                        text("SELECT fingerprint FROM cloud_schema_version WHERE id=1")
                    ).scalar_one()
                    # A backup made before tables were added restores without them (left empty).
                    absent = (
                        () if actual == fingerprint
                        else missing_tables(database.engine.dialect, fingerprint)
                    )
                    if absent is None:
                        raise ValueError("Backup requires an explicit schema migration")
                    db.execute(text("SET CONSTRAINTS ALL DEFERRED"))
                    for table in sorted(Base.metadata.tables.values(), key=lambda t: t.name):
                        if table.name in absent:
                            continue
                        load_table(
                            db,
                            table,
                            target / "tenants" / tenant / "database" / (table.name + ".jsonl"),
                        )
            finally:
                database.dispose()
        for tenant, name, sha, size in ready_objects:
            if tenant not in admitted:
                raise ValueError("Object owner is not admitted")
            member = f"tenants/{tenant}/objects/{hashlib.sha256(name.encode()).hexdigest()}"
            if manifest["files"].get(member) != {"sha256": sha, "size": size}:
                raise ValueError("Backup object/catalog binding differs")
            operations.storage(tenant).write_bytes(name, (target / member).read_bytes())
        journal.reconcile()
        operations.replay_deletions()
        if origin is None:
            raise ValueError("Missing source PostgreSQL runtime")
        with origin.connect("restore") as db:
            db.execute("UPDATE recovery_state SET state='RETIRED' WHERE id=1")
        with control.connect("restore") as db:
            db.execute("UPDATE recovery_state SET state='READY' WHERE id=1")
        marker.unlink()
        return {
            "backupId": identity,
            "filesVerified": len(manifest["files"]),
            "sessionsRevoked": True,
            "usersRequireReadmission": True,
            "remoteJournalReconciled": True,
            "database": "postgresql",
            "sourceRetired": True,
        }
