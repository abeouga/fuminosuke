from __future__ import annotations

import hashlib
import secrets
import sqlite3
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.cloud.relational import ControlDatabase


class AccessDenied(Exception):
    pass


request_credential: ContextVar[tuple[Registry, str] | None] = ContextVar(
    "cloud_request_credential", default=None,
)
shutdown_tenants: ContextVar[frozenset[str]] = ContextVar(
    "cloud_shutdown_tenants", default=frozenset(),
)


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


@dataclass(frozen=True)
class Identity:
    tenant_id: str
    subject: str
    session_hash: str
    csrf: str


class Registry:
    """Only operator admission and verified Cognito subjects create tenant bindings."""

    def __init__(self, root: Path, max_users: int, *,
                 control: ControlDatabase | None = None) -> None:
        self.control = control
        if root.is_symlink() or (root / "app.db").exists():
            raise ValueError("Use a dedicated cloud directory, never a local business database")
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        root.chmod(0o700)
        self.path = root / "registry.db"
        if self.path.is_symlink():
            raise ValueError("Registry DB cannot be a symlink")
        self.max_users = max_users
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS registry_version (version INTEGER PRIMARY KEY);
                INSERT INTO registry_version SELECT 2
                    WHERE NOT EXISTS (SELECT 1 FROM registry_version);
                CREATE TABLE IF NOT EXISTS users (
                    subject TEXT PRIMARY KEY, tenant_id TEXT NOT NULL UNIQUE,
                    active INTEGER NOT NULL CHECK(active IN (0,1)), created REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    token_hash TEXT PRIMARY KEY, subject TEXT NOT NULL REFERENCES users(subject),
                    csrf TEXT NOT NULL, expires REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS logins (
                    state_hash TEXT PRIMARY KEY, browser_hash TEXT NOT NULL,
                    verifier TEXT NOT NULL, nonce TEXT NOT NULL, expires REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS deleted_tenants (tenant_id TEXT PRIMARY KEY);
            """)
            versions = db.execute("SELECT version FROM registry_version").fetchall()
            if versions == [(1,)]:
                db.execute("UPDATE registry_version SET version=2 WHERE version=1")
            elif versions != [(2,)]:
                raise RuntimeError("Unsupported cloud registry schema")
        if control is None:
            self.path.chmod(0o600)

    @contextmanager
    def connect(self) -> Iterator[Any]:
        if self.control is not None:
            with self.control.connect("registry") as db:
                yield db
            return
        with sqlite3.connect(self.path, timeout=10) as db:
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("PRAGMA journal_mode=WAL")
            yield db

    def admit(self, subject: str) -> str:
        # Admission is a host-only administrative command, never an HTTP signup route.
        if str(uuid.UUID(subject)) != subject:
            raise ValueError("Use the exact Cognito sub UUID")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT tenant_id FROM users WHERE subject=?", (subject,)).fetchone()
            if row:
                if db.execute("SELECT 1 FROM deleted_tenants WHERE tenant_id=?", row).fetchone():
                    raise ValueError("Deleted accounts cannot be re-enabled")
                return str(row[0])
            admitted = db.execute(
                "SELECT count(*) FROM users u LEFT JOIN deleted_tenants d "
                "ON u.tenant_id=d.tenant_id WHERE d.tenant_id IS NULL",
            ).fetchone()[0]
            if admitted >= self.max_users:
                raise ValueError("The beta user limit has been reached")
            tenant = uuid.uuid4().hex
            db.execute("INSERT INTO users VALUES (?,?,1,?)", (subject, tenant, time.time()))
            return tenant

    def set_active(self, subject: str, active: bool) -> None:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if active and db.execute(
                "SELECT 1 FROM users u JOIN deleted_tenants d ON u.tenant_id=d.tenant_id "
                "WHERE u.subject=?", (subject,),
            ).fetchone():
                raise ValueError("Deleted accounts cannot be re-enabled")
            if db.execute("UPDATE users SET active=? WHERE subject=?",
                          (int(active), subject)).rowcount != 1:
                raise ValueError("Unknown admitted user")
            # Re-enabling an account never revives old browser sessions.
            db.execute("DELETE FROM sessions WHERE subject=?", (subject,))

    def mark_deleted(self, tenant: str) -> None:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("INSERT INTO deleted_tenants VALUES (?) ON CONFLICT DO NOTHING", (tenant,))
            db.execute("UPDATE users SET active=0 WHERE tenant_id=?", (tenant,))
            db.execute("DELETE FROM sessions WHERE subject IN "
                       "(SELECT subject FROM users WHERE tenant_id=?)", (tenant,))

    def active_tenants(self) -> list[str]:
        with self.connect() as db:
            return [str(row[0]) for row in db.execute("SELECT tenant_id FROM users WHERE active=1")]

    def subject(self, tenant: str) -> str:
        with self.connect() as db:
            row = db.execute("SELECT subject FROM users WHERE tenant_id=?", (tenant,)).fetchone()
        return str(row[0]) if row else ""

    def require_active(self, tenant: str) -> None:
        with self.connect() as db:
            if not db.execute("SELECT 1 FROM users WHERE tenant_id=? AND active=1",
                              (tenant,)).fetchone():
                raise AccessDenied

    def require_access(self, tenant: str) -> None:
        if tenant in shutdown_tenants.get():
            return  # Trusted lifespan cleanup may persist execution fences for disabled owners.
        self.require_active(tenant)
        credential = request_credential.get()
        if credential is not None:
            registry, token = credential
            if registry is not self or self.authenticate(token).tenant_id != tenant:
                raise AccessDenied

    def begin_login(self) -> tuple[str, str, str, str]:
        state, browser, verifier, nonce = (secrets.token_urlsafe(32) for _ in range(4))
        with self.connect() as db:
            db.execute("DELETE FROM logins WHERE expires<=?", (time.time(),))
            db.execute("INSERT INTO logins VALUES (?,?,?,?,?)",
                       (digest(state), digest(browser), verifier, nonce, time.time() + 300))
        return state, browser, verifier, nonce

    def consume_login(self, state: str, browser: str) -> tuple[str, str]:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT verifier,nonce FROM logins WHERE state_hash=? "
                             "AND browser_hash=? AND expires>?",
                             (digest(state), digest(browser), time.time())).fetchone()
            if row is None:
                raise AccessDenied
            db.execute("DELETE FROM logins WHERE state_hash=?", (digest(state),))
            return str(row[0]), str(row[1])

    def issue(self, subject: str, seconds: int) -> tuple[str, Identity]:
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM sessions WHERE expires<=?", (time.time(),))
            row = db.execute("SELECT tenant_id FROM users WHERE subject=? AND active=1",
                             (subject,)).fetchone()
            if row is None:
                raise AccessDenied
            db.execute("INSERT INTO sessions VALUES (?,?,?,?)",
                       (digest(token), subject, csrf, time.time() + seconds))
        return token, Identity(str(row[0]), subject, digest(token), csrf)

    def authenticate(self, token: str) -> Identity:
        if len(token) != 43:
            raise AccessDenied
        with self.connect() as db:
            row = db.execute(
                "SELECT u.tenant_id,u.subject,s.csrf FROM sessions s JOIN users u "
                "ON u.subject=s.subject WHERE s.token_hash=? AND s.expires>? AND u.active=1",
                (digest(token), time.time()),
            ).fetchone()
            if row is None:
                raise AccessDenied
            return Identity(str(row[0]), str(row[1]), digest(token), str(row[2]))

    def revoke(self, token: str) -> None:
        with self.connect() as db:
            db.execute("DELETE FROM sessions WHERE token_hash=?", (digest(token),))
