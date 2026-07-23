from pathlib import Path

import pytest

from app.storage.local_storage import LocalStorageService, StoragePathError


def test_atomic_write_and_read(tmp_path: Path) -> None:
    storage = LocalStorageService(tmp_path / "data")
    storage.initialize()

    relative = storage.write_bytes("artifacts/job/result.json", b'{"ok":true}')

    assert relative == "artifacts/job/result.json"
    assert storage.read_bytes(relative) == b'{"ok":true}'
    assert not list((tmp_path / "data" / "artifacts" / "job").glob("*.tmp"))


@pytest.mark.parametrize("unsafe", ["../outside.txt", "nested/../../outside.txt"])
def test_cannot_escape_data_directory(tmp_path: Path, unsafe: str) -> None:
    storage = LocalStorageService(tmp_path / "data")
    storage.initialize()

    with pytest.raises(StoragePathError):
        storage.write_bytes(unsafe, b"unsafe")


def test_absolute_paths_are_rejected(tmp_path: Path) -> None:
    storage = LocalStorageService(tmp_path / "data")
    storage.initialize()

    with pytest.raises(StoragePathError, match="Absolute"):
        storage.write_bytes(tmp_path / "outside.txt", b"unsafe")

