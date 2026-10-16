"""A single runtime OR an offline maintenance operation owns the cloud root."""
from __future__ import annotations

import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def runtime_lease(root: Path) -> Iterator[None]:
    if root.is_symlink() or (root / "app.db").exists():
        raise ValueError("Use a dedicated cloud root")
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = root / ".runtime.lock"
    if path.is_symlink():
        raise ValueError("Runtime lock cannot be a symlink")
    with path.open("a+b") as stream:
        if not path.stat().st_size:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        if sys.platform == "win32":
            import msvcrt
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            if sys.platform == "win32":
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
