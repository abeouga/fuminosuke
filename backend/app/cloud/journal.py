"""Append-only remote receipts; the relational index is recoverable, never the authority."""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol
from uuid import UUID, uuid4

from app.ai.api_budget import refused
from app.cloud.relational import ControlDatabase


class ReceiptStore(Protocol):
    def records(self) -> list[dict[str, Any]]: ...
    def put(self, record: dict[str, Any]) -> None: ...


def encoded(record: dict[str, Any]) -> bytes:
    return json.dumps(record, sort_keys=True, separators=(",", ":")).encode()


def checksum(record: dict[str, Any]) -> str:
    return hashlib.sha256(encoded(record)).hexdigest()


class S3Receipts:
    def __init__(self, client: Any, bucket: str, installation: str) -> None:
        self.client, self.bucket = client, bucket
        self.prefix = f"{installation}/receipts/"

    def records(self) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        for page in self.client.get_paginator("list_objects_v2").paginate(
            Bucket=self.bucket, Prefix=self.prefix,
        ):
            for item in page.get("Contents", []):
                if len(records) >= 100_000 or item["Size"] > 2048:
                    raise ValueError("Invalid remote receipt collection")
                name = item["Key"].removeprefix(self.prefix)
                if not re.fullmatch(r"[0-9]{12}\.json", name):
                    raise ValueError("Unexpected receipt object")
                body = self.client.get_object(Bucket=self.bucket, Key=item["Key"])["Body"]
                try:
                    record = json.loads(body.read(2049))
                finally:
                    body.close()
                if name != f"{record['seq']:012d}.json":
                    raise ValueError("Receipt sequence does not match its key")
                records.append(record)
        return sorted(records, key=lambda record: record["seq"])

    def put(self, record: dict[str, Any]) -> None:
        # SDK retries are disabled by the client factory. An unknown result closes sending.
        self.client.put_object(
            Bucket=self.bucket, Key=self.prefix + f"{record['seq']:012d}.json",
            Body=encoded(record), ContentType="application/json", IfNoneMatch="*",
            ServerSideEncryption="AES256",
        )


def s3_client() -> Any:
    import os

    import boto3
    from botocore.config import Config

    # Fargate sets AWS_REGION; the original beta ran in Tokyo.
    region = os.environ.get("AWS_REGION") or "ap-northeast-1"
    return boto3.client(
        "s3", region_name=region, endpoint_url=f"https://s3.{region}.amazonaws.com",
        config=Config(retries={"total_max_attempts": 1}, connect_timeout=5, read_timeout=15),
    )


class Journal:
    def __init__(
        self, root: Path, installation: str, store: ReceiptStore,
        *, clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        control: ControlDatabase | None = None,
    ) -> None:
        if str(UUID(installation)) != installation:
            raise ValueError("Use a fixed installation UUID")
        self.installation, self.store, self.clock = installation, store, clock
        self.control = control
        self.path = root / "operations.db"
        if self.path.is_symlink():
            raise ValueError("Operations DB cannot be a symlink")
        self.lock = threading.RLock()
        self.healthy = False
        with self.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS receipts "
                       "(seq INTEGER PRIMARY KEY, payload TEXT NOT NULL)")

    @contextmanager
    def connect(self) -> Iterator[Any]:
        if self.control is not None:
            with self.control.connect("journal") as db:
                yield db
            return
        with sqlite3.connect(self.path, timeout=30) as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA synchronous=FULL")
            yield db

    def initialize(self) -> None:
        if self.store.records():
            raise ValueError("The remote journal is already initialized")
        record = self.make(1, "init", "", self.installation, "", "", "0" * 64)
        self.store.put(record)
        self.reconcile()

    def make(self, seq: int, kind: str, tenant: str, purpose: str,
             resource_kind: str, resource_id: str, previous: str) -> dict[str, Any]:
        record = {
            "seq": seq, "id": uuid4().hex, "at": self.clock().astimezone(UTC).isoformat(),
            "kind": kind, "tenant": tenant, "purpose": purpose,
            "resourceKind": resource_kind, "resourceId": resource_id, "previous": previous,
        }
        return {**record, "hash": checksum(record)}

    def validate(self, records: list[dict[str, Any]]) -> None:
        previous = "0" * 64
        for seq, record in enumerate(records, 1):
            unsigned = {k: v for k, v in record.items() if k != "hash"}
            if (set(record) != {"seq", "id", "at", "kind", "tenant", "purpose",
                                "resourceKind", "resourceId", "previous", "hash"}
                    or record["seq"] != seq or record["previous"] != previous
                    or record["hash"] != checksum(unsigned)
                    or not re.fullmatch(r"[0-9a-f]{32}", record["id"])
                    or datetime.fromisoformat(record["at"]).utcoffset() != timedelta(0)):
                raise ValueError("Invalid receipt chain")
            if seq == 1:
                if (record["kind"], record["purpose"], record["tenant"],
                    record["resourceKind"], record["resourceId"]) != (
                    "init", self.installation, "", "", "",
                ):
                    raise ValueError("Different installation journal")
            elif not re.fullmatch(r"[0-9a-f]{32}", record["tenant"]):
                raise ValueError("Invalid tenant receipt")
            elif record["kind"] == "api":
                if (not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", record["purpose"])
                        or record["resourceKind"] or record["resourceId"]):
                    raise ValueError("Invalid API receipt")
            elif record["kind"] == "delete":
                if record["resourceKind"] not in {"job", "template", "tenant"}:
                    raise ValueError("Invalid deletion receipt")
                if record["resourceKind"] == "tenant":
                    if record["resourceId"] != record["tenant"]:
                        raise ValueError("Invalid tenant deletion")
                elif str(UUID(record["resourceId"])) != record["resourceId"]:
                    raise ValueError("Invalid deletion resource")
                if record["purpose"]:
                    raise ValueError("Invalid deletion metadata")
            else:
                raise ValueError("Unexpected receipt kind")
            previous = record["hash"]
        if not records:
            raise ValueError("Explicit remote journal initialization is required")

    def reconcile(self) -> None:
        with self.lock:
            self.healthy = False
            records = self.store.records()
            self.validate(records)
            with self.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                local = [json.loads(row[0]) for row in db.execute(
                    "SELECT payload FROM receipts ORDER BY seq",
                )]
                if local != records[:len(local)]:
                    raise ValueError("Remote journal is missing or differs from local receipts")
                for record in records[len(local):]:
                    db.execute("INSERT INTO receipts VALUES (?,?)",
                               (record["seq"], encoded(record).decode()))
            self.healthy = True

    def read(self, db: Any) -> list[dict[str, Any]]:
        if not self.healthy:
            raise refused("利用記録を確認できません。AI送信を停止しています。",
                          "BETA_JOURNAL_UNAVAILABLE", 503)
        return [json.loads(row[0]) for row in db.execute(
            "SELECT payload FROM receipts ORDER BY seq",
        )]

    def append(self, db: Any, records: list[dict[str, Any]],
               kind: str, tenant: str, purpose: str = "", resource_kind: str = "",
               resource_id: str = "") -> str:
        record = self.make(len(records) + 1, kind, tenant, purpose,
                           resource_kind, resource_id, records[-1]["hash"])
        self.validate([*records, record])
        try:
            self.store.put(record)
            db.execute("INSERT INTO receipts VALUES (?,?)",
                       (record["seq"], encoded(record).decode()))
            # Commit before returning permission to send. Caller must not refund this receipt.
            db.commit()
        except Exception as error:
            self.healthy = False
            raise refused("利用記録の保存を確認できません。送信を停止しました。",
                          "BETA_JOURNAL_UNAVAILABLE", 503) from error
        return str(record["id"])

    def delete(self, tenant: str, kind: str, resource_id: str) -> None:
        with self.lock, self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            records = self.read(db)
            if any(r["kind"] == "delete" and r["tenant"] == tenant
                   and r["resourceKind"] == kind and r["resourceId"] == resource_id
                   for r in records):
                return
            self.append(db, records, "delete", tenant, resource_kind=kind,
                        resource_id=resource_id)


class TenantBudget:
    """All provider paths bind to this shared budget through their existing Database."""
    def __init__(self, journal: Journal, tenant: str, access_check: Callable[[], None]) -> None:
        self.journal, self.tenant, self.access_check = journal, tenant, access_check

    def remaining(self, records: list[dict[str, Any]]) -> int:
        day = self.journal.clock().astimezone(UTC) + timedelta(hours=9)
        api = [r for r in records if r["kind"] == "api"]
        today = [r for r in api if (datetime.fromisoformat(r["at"])
                                   + timedelta(hours=9)).date() == day.date()]
        mine = [r for r in today if r["tenant"] == self.tenant]
        return max(0, min(1000 - len(api), 100 - len(today), 20 - len(mine)))

    def status(self) -> dict[str, int]:
        self.access_check()
        with self.journal.connect() as db:
            records = self.journal.read(db)
        remaining = self.remaining(records)
        # Do not disclose other users' counts. This is the currently usable personal allowance.
        day = (self.journal.clock().astimezone(UTC) + timedelta(hours=9)).date()
        used = sum(r["kind"] == "api" and r["tenant"] == self.tenant
                   and (datetime.fromisoformat(r["at"]) + timedelta(hours=9)).date() == day
                   for r in records)
        return {"used": used, "limit": 20, "remaining": remaining}

    def reserve(self, purpose: str) -> str:
        self.access_check()
        with self.journal.lock, self.journal.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            records = self.journal.read(db)
            if self.remaining(records) < 1:
                raise refused("AI利用上限に達しました。手動編集は継続できます。",
                              "AI_API_BUDGET_EXHAUSTED", 429)
            if purpose.endswith("connect"):
                seconds = 300 * sum(r["kind"] == "api" and r["tenant"] == self.tenant
                                    and r["purpose"].endswith("connect") for r in records)
                if seconds + 300 > 1200:
                    raise refused("音声接続時間の上限に達しました。",
                                  "VOICE_TIME_EXHAUSTED", 429)
            identity = self.journal.append(db, records, "api", self.tenant, purpose)
            self.access_check()  # Revocation during remote I/O still prevents provider sending.
            return identity
