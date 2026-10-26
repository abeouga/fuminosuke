"""Immutable durable files with a tenant-bound PostgreSQL catalog and disposable local copies."""

from __future__ import annotations

import base64
import hashlib
import re
import shutil
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from botocore.exceptions import ClientError

from app.ai.api_budget import refused
from app.cloud.relational import ControlDatabase
from app.storage.atomic_writer import atomic_write_bytes
from app.storage.local_storage import LocalStorageService, StoragePathError

MAX_FILE = 64 * 1024**2
BUSINESS = {"uploads", "artifacts", "templates", "exports"}
# Parallel uploads of one batch (one document is about 700 files).
UPLOAD_WORKERS = 8
# Keys per DeleteObjects call (the S3 maximum).
DELETE_BATCH = 1000


class S3StorageService(LocalStorageService):
    requires_immutable_paths = True

    def __init__(
        self,
        data_dir: Path,
        *,
        control: ControlDatabase,
        client: Any,
        bucket: str,
        tenant: str,
        access_check: Callable[[], None] | None = None,
    ) -> None:
        if not re.fullmatch(r"[0-9a-f]{32}", tenant):
            raise ValueError("Use the server-owned tenant identity")
        super().__init__(data_dir, access_check=access_check)
        self.control, self.client, self.bucket, self.tenant = control, client, bucket, tenant
        self.prefix = f"{control.installation}/tenants/{tenant}/objects/"
        if self.client.get_bucket_versioning(Bucket=bucket).get("Status") is not None:
            raise StoragePathError("Business objects require a dedicated unversioned bucket")
        with control.connect("objects") as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "CREATE TABLE IF NOT EXISTS objects (tenant TEXT NOT NULL, path TEXT NOT NULL, "
                "sha TEXT NOT NULL, bytes BIGINT NOT NULL CHECK(bytes>=0), "
                "state TEXT NOT NULL CHECK(state IN ('STAGING','READY','DELETED')), "
                "PRIMARY KEY(tenant,path))"
            )

    def initialize(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        for directory in self.REQUIRED_DIRECTORIES:
            super().resolve(directory).mkdir(parents=True, exist_ok=True)

    def _name(self, relative_path: str | Path) -> str:
        self._check_access()
        value = Path(relative_path).as_posix()
        if "\\" in value or ":" in value or len(value) > 1024:
            raise StoragePathError("Invalid cloud file path")
        supplied = Path(value)
        self._reject_symlink_components(supplied)
        self._path(supplied)  # Access was checked above.
        return supplied.as_posix()

    def _check_access(self) -> None:
        if self._access_check is not None:
            self._access_check()

    def _key(self, name: str, sha: str) -> str:
        return self.prefix + hashlib.sha256(name.encode()).hexdigest() + "/" + sha

    def _record(self, name: str) -> tuple[Any, ...] | None:
        with self.control.connect("objects") as db:
            row = db.execute(
                "SELECT sha,bytes,state FROM objects WHERE tenant=? AND path=?", (self.tenant, name)
            ).fetchone()
        return tuple(row) if row else None

    def _fetch(self, name: str, sha: str, size: int) -> bytes:
        if not 0 <= size <= MAX_FILE:
            raise StoragePathError("Invalid durable file size")
        response = self.client.get_object(Bucket=self.bucket, Key=self._key(name, sha))
        body = response["Body"]
        try:
            content = body.read(size + 1)
        finally:
            body.close()
        if (
            not isinstance(content, bytes)
            or len(content) != size
            or hashlib.sha256(content).hexdigest() != sha
        ):
            raise StoragePathError("Durable file SHA mismatch")
        self._check_access()
        return content

    def _materialize(self, name: str, record: tuple[Any, ...]) -> Path:
        sha, size, state = record
        if state != "READY":
            raise StoragePathError("File publication is incomplete or deleted")
        target = super().resolve(name)
        if target.is_file():
            # Content verification, not a metadata-only SHA cache.
            if (
                target.stat().st_size != size
                or hashlib.sha256(target.read_bytes()).hexdigest() != sha
            ):
                raise StoragePathError("Local durable-file copy was changed")
        else:
            atomic_write_bytes(target, self._fetch(name, sha, size))
        return target

    def resolve(self, relative_path: str | Path) -> Path:
        name = self._name(relative_path)
        if Path(name).parts[0] not in BUSINESS:
            return super().resolve(name)
        row = self._record(name)
        if row is not None:
            return self._materialize(name, row)
        target = super().resolve(name)
        if target.is_file():
            raise StoragePathError("Unpublished file in cloud workspace")
        # Directory consumers (package assembly, copies and recovery) need a real owned subtree.
        prefix = name.rstrip("/") + "/"
        with self.control.connect("objects") as db:
            rows = db.execute(
                "SELECT path,sha,bytes,state FROM objects WHERE tenant=? "
                "AND state='READY' AND left(path,?)=? ORDER BY path",
                (self.tenant, len(prefix), prefix),
            ).fetchall()
        for path, sha, size, state in rows:
            self._materialize(path, (sha, size, state))
        allowed = {path for path, *_ in rows}
        if target.is_dir():
            for cached in target.rglob("*"):
                if cached.is_symlink():
                    raise StoragePathError("Symlink in cloud workspace")
                if cached.is_file() and self.relative_name(cached) not in allowed:
                    raise StoragePathError("Unpublished file in cloud directory")
        return target

    def object_location(self, relative_path: str | Path) -> dict[str, Any]:
        """The immutable S3 object of a published business file (for the extraction Lambda)."""
        name = self._name(relative_path)
        row = self._record(name) if Path(name).parts[0] in BUSINESS else None
        if row is None or row[2] != "READY":
            raise StoragePathError("File publication is incomplete or deleted")
        sha, size, _state = row
        return {"key": self._key(name, sha), "sha256": sha, "size": int(size)}

    def write_bytes(self, relative_path: str | Path, content: bytes) -> str:
        return self.write_many([(str(relative_path), content)])[0]

    def write_many(self, files: list[tuple[str, bytes]]) -> list[str]:
        """Publish files: reserve them in one transaction, upload them in parallel outside the
        catalog lock, then mark them READY in one transaction.

        A deletion tombstone still wins over a late writer: deletion marks the row DELETED
        under the lock, and publication then removes the object it uploaded instead of
        marking it READY. An upload interrupted by a crash stays STAGING and counted, as before.
        """
        self._check_access()
        names = [self._name_checked(path) for path, _content in files]
        stored: list[str] = list(names)
        durable: dict[str, tuple[bytes, str]] = {}
        for index, (name, (_path, content)) in enumerate(zip(names, files, strict=True)):
            if Path(name).parts[0] not in BUSINESS:
                stored[index] = super().write_bytes(name, content)
                continue
            if len(content) > MAX_FILE:
                raise StoragePathError("Cloud file exceeds 64 MiB")
            sha = hashlib.sha256(content).hexdigest()
            if name in durable and durable[name][1] != sha:
                raise StoragePathError("Immutable cloud path cannot be replaced or revived")
            durable[name] = (content, sha)
        if not durable:
            return stored
        # Rows stay STAGING (counted) if an upload fails; the same content can be written again.
        uploads = self._reserve(durable)
        with ThreadPoolExecutor(max_workers=UPLOAD_WORKERS) as pool:
            list(pool.map(lambda name: self._upload(name, *durable[name]), uploads))
        self._check_access()  # Revocation during remote I/O still prevents publication.
        self._publish(durable, uploads)
        for name, (content, _sha) in durable.items():
            atomic_write_bytes(self._path(name), content)
        return stored

    def _name_checked(self, relative_path: str | Path) -> str:
        """_name() for a batch whose access the caller has just checked."""
        value = Path(relative_path).as_posix()
        if "\\" in value or ":" in value or len(value) > 1024:
            raise StoragePathError("Invalid cloud file path")
        supplied = Path(value)
        self._reject_symlink_components(supplied)
        self._path(supplied)
        return supplied.as_posix()

    def _reserve(self, durable: dict[str, tuple[bytes, str]]) -> list[str]:
        """Commit the capacity reservation first; returns the paths that still need uploading."""
        uploads: list[str] = []
        with self.control.connect("objects") as db:
            db.execute("BEGIN IMMEDIATE")
            rows = {
                path: (sha, size, state)
                for path, sha, size, state in db.execute(
                    "SELECT path,sha,bytes,state FROM objects WHERE tenant=? AND path=ANY(?)",
                    (self.tenant, list(durable)),
                ).fetchall()
            }
            added = 0
            for name, (content, sha) in durable.items():
                row = rows.get(name)
                if row:
                    if row[0] != sha or row[1] != len(content) or row[2] == "DELETED":
                        raise StoragePathError(
                            "Immutable cloud path cannot be replaced or revived"
                        )
                    if row[2] != "READY":
                        uploads.append(name)
                else:
                    added += len(content)
                    uploads.append(name)
            if added:
                mine = db.execute(
                    "SELECT COALESCE(sum(bytes),0) FROM objects "
                    "WHERE tenant=? AND state!='DELETED'",
                    (self.tenant,),
                ).scalar_one()
                total = db.execute(
                    "SELECT COALESCE(sum(bytes),0) FROM objects WHERE state!='DELETED'"
                ).scalar_one()
                if mine + added > 500 * 1024**2 or total + added > 5 * 1024**3:
                    raise refused(
                        "保存容量の上限に達しました。不要な帳票を削除してください。",
                        "BETA_STORAGE_LIMIT",
                        413,
                    )
                for name, (content, sha) in durable.items():
                    if name not in rows:
                        db.execute(
                            "INSERT INTO objects VALUES (?,?,?,?,'STAGING')",
                            (self.tenant, name, sha, len(content)),
                        )
        return uploads

    def _upload(self, name: str, content: bytes, sha: str) -> None:
        try:
            self.client.put_object(
                Bucket=self.bucket,
                Key=self._key(name, sha),
                Body=content,
                IfNoneMatch="*",
                ServerSideEncryption="AES256",
                ChecksumSHA256=base64.b64encode(bytes.fromhex(sha)).decode(),
            )
        except ClientError as exc:
            if exc.response["ResponseMetadata"]["HTTPStatusCode"] != 412:
                raise
            if self._fetch(name, sha, len(content)) != content:
                raise StoragePathError("Existing immutable object differs") from exc

    def _publish(self, durable: dict[str, tuple[bytes, str]], uploads: list[str]) -> None:
        with self.control.connect("objects") as db:
            db.execute("BEGIN IMMEDIATE")
            rows = {
                path: (sha, state)
                for path, sha, state in db.execute(
                    "SELECT path,sha,state FROM objects WHERE tenant=? AND path=ANY(?)",
                    (self.tenant, list(durable)),
                ).fetchall()
            }
            revoked = [
                name for name, (_content, sha) in durable.items()
                if name not in rows or rows[name][0] != sha or rows[name][1] == "DELETED"
            ]
            if revoked:
                # Deleted while uploading: the tombstone wins, so remove what was just uploaded.
                for name in revoked:
                    if name in uploads:
                        self.client.delete_object(
                            Bucket=self.bucket, Key=self._key(name, durable[name][1])
                        )
                raise StoragePathError("Cloud file publication was revoked")
            if uploads:
                db.execute(
                    "UPDATE objects SET state='READY' WHERE tenant=? AND path=ANY(?) "
                    "AND state='STAGING'",
                    (self.tenant, uploads),
                )

    def write_text(
        self, relative_path: str | Path, content: str, *, encoding: str = "utf-8"
    ) -> str:
        return self.write_bytes(relative_path, content.encode(encoding))

    def write_immutable(self, relative_path: str, content: bytes) -> str:
        return self.write_bytes(relative_path, content)

    def read_bytes(self, relative_path: str | Path) -> bytes:
        return self.resolve(relative_path).read_bytes()

    def exists(self, relative_path: str | Path) -> bool:
        name = self._name(relative_path)
        if Path(name).parts[0] not in BUSINESS:
            return super().exists(name)
        row = self._record(name)
        if row is not None:
            return bool(row[2] == "READY")
        prefix = name.rstrip("/") + "/"
        with self.control.connect("objects") as db:
            present = db.execute(
                "SELECT 1 FROM objects WHERE tenant=? AND state='READY' AND left(path,?)=? LIMIT 1",
                (self.tenant, len(prefix), prefix),
            ).fetchone()
        return bool(present) or super().resolve(name).is_dir()

    def _delete(self, name: str, tree: bool) -> None:
        target = super().resolve(name)
        if tree and target.exists() and any(p.is_symlink() for p in target.rglob("*")):
            raise StoragePathError("Symlink in recursive cloud deletion")
        prefix = name.rstrip("/") + "/"
        with self.control.connect("objects") as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute(
                "SELECT path,sha FROM objects WHERE tenant=? AND state!='DELETED' AND "
                "(path=? OR (? AND left(path,?)=?))",
                (self.tenant, name, tree, len(prefix), prefix),
            ).fetchall()
            # Still under the lock and confirmed before the tombstone, but batched: one
            # DeleteObjects call per 1000 keys and parallel confirmation (a document is ~700).
            keys = [self._key(path, sha) for path, sha in rows]
            for start in range(0, len(keys), DELETE_BATCH):
                response = self.client.delete_objects(
                    Bucket=self.bucket,
                    Delete={
                        "Objects": [{"Key": key} for key in keys[start:start + DELETE_BATCH]],
                        "Quiet": True,
                    },
                )
                if response.get("Errors"):
                    raise StoragePathError("Remote deletion was not confirmed")
            with ThreadPoolExecutor(max_workers=UPLOAD_WORKERS) as pool:
                list(pool.map(self._confirm_gone, keys))
            if rows:
                db.execute(
                    "UPDATE objects SET state='DELETED' WHERE tenant=? AND path=ANY(?)",
                    (self.tenant, [path for path, _sha in rows]),
                )
        if tree:
            if target.exists():
                shutil.rmtree(target)
        else:
            target.unlink(missing_ok=True)

    def _confirm_gone(self, key: str) -> None:
        try:
            self.client.head_object(Bucket=self.bucket, Key=key)
        except ClientError as exc:
            if exc.response["ResponseMetadata"]["HTTPStatusCode"] != 404:
                raise
        else:
            raise StoragePathError("Remote deletion was not confirmed")

    def delete_file(self, relative_path: str | Path) -> None:
        name = self._name(relative_path)
        if Path(name).parts[0] not in BUSINESS:
            return super().delete_file(name)
        self._delete(name, False)

    def delete_tree(self, relative_path: str | Path) -> None:
        name = self._name(relative_path)
        if Path(name).parts[0] not in BUSINESS:
            return super().delete_tree(name)
        self._delete(name, True)

    def move_file(self, source: str, target: str) -> None:
        self.write_bytes(target, self.read_bytes(source))
        self.delete_file(source)
