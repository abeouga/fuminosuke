from __future__ import annotations

import shutil
from collections.abc import Callable
from pathlib import Path, PurePath

from app.storage.atomic_writer import atomic_write_bytes, atomic_write_text


class StoragePathError(ValueError):
    pass


class LocalStorageService:
    requires_immutable_paths = False
    REQUIRED_DIRECTORIES = (
        "uploads",
        "artifacts",
        "templates",
        "exports",
        "tmp",
        "logs",
    )

    def __init__(self, data_dir: Path, *, access_check: Callable[[], None] | None = None) -> None:
        self._root = data_dir.expanduser().resolve()
        self._access_check = access_check

    @property
    def root(self) -> Path:
        return self._root

    def initialize(self) -> None:
        self._root.mkdir(parents=True, exist_ok=True)
        for directory in self.REQUIRED_DIRECTORIES:
            self.resolve(directory).mkdir(parents=True, exist_ok=True)

    def resolve(self, relative_path: str | Path) -> Path:
        if self._access_check is not None:
            self._access_check()
        supplied = Path(relative_path)
        if supplied.is_absolute():
            raise StoragePathError("Absolute paths are not accepted")
        if not supplied.parts or any(part in {"", ".", ".."} for part in supplied.parts):
            raise StoragePathError("Invalid relative storage path")

        candidate = (self._root / supplied).resolve(strict=False)
        try:
            candidate.relative_to(self._root)
        except ValueError as exc:
            raise StoragePathError("Path escapes the data directory") from exc
        return candidate

    def relative_name(self, path: Path) -> str:
        resolved = path.expanduser().resolve(strict=False)
        try:
            relative = resolved.relative_to(self._root)
        except ValueError as exc:
            raise StoragePathError("Path is outside the data directory") from exc
        return relative.as_posix()

    def write_bytes(self, relative_path: str | Path, content: bytes) -> str:
        target = self.resolve(relative_path)
        atomic_write_bytes(target, content)
        return self.relative_name(target)

    def write_text(
        self, relative_path: str | Path, content: str, *, encoding: str = "utf-8"
    ) -> str:
        target = self.resolve(relative_path)
        atomic_write_text(target, content, encoding=encoding)
        return self.relative_name(target)

    def read_bytes(self, relative_path: str | Path) -> bytes:
        return self.resolve(relative_path).read_bytes()

    def exists(self, relative_path: str | Path) -> bool:
        return self.resolve(relative_path).exists()

    def delete_file(self, relative_path: str | Path) -> None:
        target = self.resolve(relative_path)
        if target.is_dir():
            raise StoragePathError("delete_file cannot delete a directory")
        target.unlink(missing_ok=True)

    def move_file(self, source: str, target: str) -> None:
        import os
        destination = self.resolve(target)
        destination.parent.mkdir(parents=True, exist_ok=True)
        os.replace(self.resolve(source), destination)

    def delete_tree(self, relative_path: str | Path) -> None:
        supplied = Path(relative_path)
        target = self.resolve(supplied)
        self._reject_symlink_components(supplied)
        if target == self._root:
            raise StoragePathError("The storage root cannot be deleted")
        if target.exists():
            shutil.rmtree(target)

    def _reject_symlink_components(self, relative_path: PurePath) -> None:
        current = self._root
        for part in relative_path.parts:
            current /= part
            if current.is_symlink():
                raise StoragePathError("Symlink paths are not accepted for recursive deletion")

