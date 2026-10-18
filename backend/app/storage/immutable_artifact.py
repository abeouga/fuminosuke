"""Atomically publish content-addressed files without replacing an existing file."""

from __future__ import annotations

import hashlib
import os
import tempfile
from contextlib import suppress
from pathlib import Path

from app.storage.local_storage import LocalStorageService


def publish_bytes(storage: LocalStorageService, namespace: str, content: bytes, suffix: str) -> str:
    digest = hashlib.sha256(content).hexdigest()
    relative = f"{namespace}/{digest}{suffix}"
    if storage.requires_immutable_paths:
        return storage.write_bytes(relative, content)
    # Establish the parent first. On Windows, resolving a missing file while
    # its parent is concurrently created can change ERROR_PATH_NOT_FOUND into
    # ERROR_FILE_NOT_FOUND and make pathlib retain its extended path prefix.
    storage.resolve(namespace).mkdir(parents=True, exist_ok=True)
    target = storage.resolve(relative)
    if not target.exists():
        # A short temporary filename avoids MAX_PATH expansion on Windows.
        # Windows rename is atomic and refuses an existing target. Avoid a
        # hard-link alias there so verification sees only the published name.
        with tempfile.NamedTemporaryFile(
            dir=target.parent, prefix=".publish-", suffix=".tmp", delete=False
        ) as stream:
            temporary = Path(stream.name)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            with suppress(FileExistsError):
                if os.name == "nt":
                    os.rename(temporary, target)
                else:
                    os.link(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
    if storage.read_bytes(relative) != content:
        raise OSError("Immutable artifact verification failed")
    return relative


def publish_config(storage: LocalStorageService, content: str | bytes) -> str:
    encoded = content.encode("utf-8") if isinstance(content, str) else content
    return publish_bytes(storage, "templates/configs", encoded, ".yaml")
