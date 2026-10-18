from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from app.storage.local_storage import LocalStorageService
from app.template_packages.import_models import TemplatePackageImportError

FailureInjector = Callable[[str], None]


@dataclass(frozen=True)
class StagedTemplateAsset:
    staged_relative_path: str
    final_relative_path: str


class TemplateAssetCommitter:
    def __init__(self, storage: LocalStorageService) -> None:
        self._storage = storage

    def stage(
        self,
        operation_id: str,
        files: Mapping[str, bytes],
    ) -> list[StagedTemplateAsset]:
        staged: list[StagedTemplateAsset] = []
        for index, (final_path, content) in enumerate(sorted(files.items())):
            if self._storage.exists(final_path):
                raise TemplatePackageImportError(
                    "IMPORT_ASSET_CONFLICT",
                    f"書き込み先assetが既に存在します: {final_path}",
                )
            staged_path = f"tmp/template-package-imports/{operation_id}/{index:03d}.stage"
            self._storage.write_bytes(staged_path, content)
            staged.append(
                StagedTemplateAsset(
                    staged_relative_path=staged_path,
                    final_relative_path=final_path,
                )
            )
        return staged

    def commit(
        self,
        staged: list[StagedTemplateAsset],
        *,
        failure_injector: FailureInjector | None = None,
    ) -> list[str]:
        committed: list[str] = []
        try:
            for item in staged:
                if self._storage.exists(item.final_relative_path):
                    raise TemplatePackageImportError(
                        "IMPORT_ASSET_CONFLICT",
                        f"書き込み先assetが既に存在します: {item.final_relative_path}",
                    )
                self._storage.move_file(item.staged_relative_path, item.final_relative_path)
                committed.append(item.final_relative_path)
                if failure_injector is not None:
                    failure_injector("after_asset_move")
            return committed
        except BaseException:
            self.rollback(committed)
            raise

    def rollback(self, committed: list[str]) -> None:
        for relative_path in reversed(committed):
            self._storage.delete_file(relative_path)

    def cleanup_stage(self, operation_id: str) -> None:
        relative = Path("tmp") / "template-package-imports" / operation_id
        if self._storage.exists(relative):
            self._storage.delete_tree(relative)
