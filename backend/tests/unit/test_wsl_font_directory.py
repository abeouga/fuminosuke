import hashlib
import json
import os
import stat
from types import SimpleNamespace

import pytest

from app.services import renderer_wsl_worker as worker


def test_complete_directory_identity_includes_nonfont_and_hidden_names(tmp_path):
    contents = {"z.ttf": b"font", ".hidden": b"metadata", "StaticCache.dat": b"cache"}
    for name, content in contents.items():
        (tmp_path / name).write_bytes(content)
    expected = [{"name": name, "bytes": len(content),
                 "sha256": hashlib.sha256(content).hexdigest()}
                for name, content in sorted(contents.items())]
    identity = worker.font_directory_identity(tmp_path)
    assert identity == {
        "policy": "wsl-font-directory-v1", "fileCount": 3, "totalBytes": 17,
        "sha256": hashlib.sha256(json.dumps(
            expected, sort_keys=True, separators=(",", ":"),
        ).encode()).hexdigest(),
    }
    stamp = (tmp_path / "z.ttf").stat()
    os.utime(tmp_path / "z.ttf", ns=(stamp.st_atime_ns, stamp.st_mtime_ns + 1_000_000))
    assert worker.font_directory_identity(tmp_path) == identity
    worker.verify_font_directory(tmp_path, identity)


@pytest.mark.parametrize("change", ["same_metadata", "rename", "add", "remove"])
def test_changed_font_set_is_rejected_before_report_publication(tmp_path, change):
    path = tmp_path / "font.ttf"
    path.write_bytes(b"one")
    (tmp_path / "retained.ttf").write_bytes(b"kept")
    before = worker.font_directory_identity(tmp_path)
    stamp = path.stat()
    if change == "same_metadata":
        path.write_bytes(b"two")
        os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    elif change == "rename":
        path.rename(tmp_path / "renamed.ttf")
    elif change == "add":
        (tmp_path / "new.ttf").write_bytes(b"new")
    else:
        path.unlink()
    with pytest.raises(RuntimeError, match="Font directory changed"):
        worker.verify_font_directory(tmp_path, before)


@pytest.mark.parametrize("kind", ["directory", "symlink", "fifo"])
def test_uninspectable_entries_are_rejected_not_omitted(tmp_path, monkeypatch, kind):
    path = tmp_path / "entry"
    if kind == "directory":
        path.mkdir()
    else:
        path.write_bytes(b"fixture")
        original = worker.Path.lstat
        mode = stat.S_IFLNK if kind == "symlink" else stat.S_IFIFO
        monkeypatch.setattr(worker.Path, "lstat", lambda self: (
            SimpleNamespace(st_mode=mode) if self == path else original(self)
        ))
    with pytest.raises(RuntimeError, match="Unsupported font directory entry"):
        worker.font_directory_identity(tmp_path)


@pytest.mark.parametrize("budget", ["empty", "file", "total", "count"])
def test_font_directory_resource_bounds(tmp_path, monkeypatch, budget):
    if budget != "empty":
        (tmp_path / "a.ttf").write_bytes(b"123")
        (tmp_path / "b.ttf").write_bytes(b"456")
    if budget == "file":
        monkeypatch.setattr(worker, "MAX_FILE", 2)
    elif budget == "total":
        monkeypatch.setattr(worker, "MAX_FONT_DIRECTORY_BYTES", 5)
    elif budget == "count":
        monkeypatch.setattr(worker, "MAX_FONT_DIRECTORY_FILES", 1)
    with pytest.raises(RuntimeError, match="Font directory.*limit"):
        worker.font_directory_identity(tmp_path)


def test_file_change_during_stream_hash_is_rejected(tmp_path, monkeypatch):
    path = tmp_path / "font.ttf"
    path.write_bytes(b"one")
    original = worker.os.read
    changed = False

    def read(descriptor, size):
        nonlocal changed
        data = original(descriptor, size)
        if data and not changed:
            changed = True
            path.write_bytes(b"longer")
        return data

    monkeypatch.setattr(worker.os, "read", read)
    with pytest.raises(RuntimeError, match="Font directory changed"):
        worker.font_directory_identity(tmp_path)


def test_collection_change_after_last_file_hash_is_rejected(tmp_path, monkeypatch):
    (tmp_path / "font.ttf").write_bytes(b"one")
    original = worker._font_entries
    calls = 0

    def entries(root):
        nonlocal calls
        calls += 1
        if calls == 2:
            (root / "late.ttf").write_bytes(b"added")
        return original(root)

    monkeypatch.setattr(worker, "_font_entries", entries)
    with pytest.raises(RuntimeError, match="Font directory changed"):
        worker.font_directory_identity(tmp_path)
