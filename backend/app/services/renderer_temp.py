"""Private render directories with ownership + live leases for stale collection."""
from __future__ import annotations

import csv
import importlib
import io
import json
import os
import shutil
import stat
import subprocess
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from uuid import uuid4

TEMP_POLICY = "user-system-dacl-lease-v1"
OWNER = "utsushibi-reference-v1"


def private_root() -> Path:
    root = Path(tempfile.gettempdir()) / OWNER
    root.mkdir(exist_ok=True)
    if root.is_symlink() or root.stat().st_file_attributes & 0x400:
        raise ValueError("Reparse render root is forbidden")
    rows = subprocess.check_output(["whoami", "/user", "/fo", "csv", "/nh"],
                                   creationflags=subprocess.CREATE_NO_WINDOW)
    sid = next(csv.reader(io.StringIO(rows.decode(errors="replace"))))[1]
    if not sid.startswith("S-1-"):
        raise ValueError("Cannot establish render owner")
    subprocess.run(["icacls", str(root), "/inheritance:r", "/grant:r",
                    f"*{sid}:(OI)(CI)F", "*S-1-5-18:(OI)(CI)F"], check=True,
                   capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
    return root


def remove_owned(root: Path, path: Path) -> None:
    if path.resolve().parent != root.resolve() or path.is_symlink():
        raise ValueError("Render cleanup escaped root")
    # Refuse reparse points anywhere in the tree. Never traverse user targets.
    for directory, dirs, files in os.walk(path, followlinks=False):
        for name in [*dirs, *files]:
            if (Path(directory) / name).lstat().st_file_attributes & 0x400:
                raise ValueError("Reparse render content is forbidden")
    from app.services.renderer_appcontainer import clean_profile
    for marker in path.rglob("appcontainer.json"):
        clean_profile(marker.parent)
    def clear_readonly(function: Any, target: str, error: BaseException) -> None:
        item = Path(target)
        if (isinstance(error, PermissionError) and item.lstat().st_file_attributes & 1
                and item.resolve().is_relative_to(path.resolve())):
            item.chmod(stat.S_IWRITE)
            function(target)
        else:
            raise error
    shutil.rmtree(path, onexc=clear_readonly)


def clean_stale(root: Path, *, age: float = 86400) -> list[str]:
    """Called while renderer_mutex is held; active leases are never collected."""
    locks = importlib.import_module("msvcrt")
    removed = []
    for path in root.glob("render-*"):
        if not path.is_dir() or path.lstat().st_file_attributes & 0x400:
            continue
        try:
            marker = path / "owner.json"
            lease_path = path / "lease"
            if (marker.lstat().st_file_attributes | lease_path.lstat().st_file_attributes) & 0x400:
                continue
            data = json.loads(marker.read_text())
            if (data.get("owner") != OWNER or data.get("nonce") != path.name[7:]
                    or time.time() - data["created"] < age):
                continue
            with lease_path.open("r+b") as lease:
                locks.locking(lease.fileno(), locks.LK_NBLCK, 1)
                locks.locking(lease.fileno(), locks.LK_UNLCK, 1)
            remove_owned(root, path)
            removed.append(path.name)
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return removed


@contextmanager
def render_directory() -> Iterator[Path]:
    root = private_root()
    clean_stale(root)
    nonce = uuid4().hex
    path = root / ("render-" + nonce)
    path.mkdir()
    (path / "owner.json").write_text(json.dumps({"owner": OWNER, "nonce": nonce,
                                               "created": time.time(), "pid": os.getpid()}))
    locks = importlib.import_module("msvcrt")
    try:
        with (path / "lease").open("w+b") as lease:
            lease.write(b"0")
            lease.flush()
            lease.seek(0)
            locks.locking(lease.fileno(), locks.LK_NBLCK, 1)
            try:
                yield path
            finally:
                lease.seek(0)
                locks.locking(lease.fileno(), locks.LK_UNLCK, 1)
    finally:
        remove_owned(root, path)
