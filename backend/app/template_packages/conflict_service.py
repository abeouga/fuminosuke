from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import TemplatePackageIdentity, TemplatePackageInstallation
from app.template_packages.import_models import TemplateImportConflictStatus
from app.template_packages.semantic_version import compare_semantic_versions


@dataclass(frozen=True)
class TemplateImportConflict:
    status: TemplateImportConflictStatus
    identity: TemplatePackageIdentity | None
    installation: TemplatePackageInstallation | None


class TemplateImportConflictService:
    @staticmethod
    def inspect(
        session: Session,
        *,
        publisher_id: str,
        package_id: str,
        release_version: str,
        content_digest: str,
    ) -> TemplateImportConflict:
        identity = session.get(TemplatePackageIdentity, (publisher_id, package_id))
        exact = session.scalar(
            select(TemplatePackageInstallation).where(
                TemplatePackageInstallation.publisher_id == publisher_id,
                TemplatePackageInstallation.package_id == package_id,
                TemplatePackageInstallation.release_version == release_version,
            )
        )
        if exact is not None:
            status = (
                TemplateImportConflictStatus.SAME_RELEASE
                if exact.content_digest == content_digest
                else TemplateImportConflictStatus.VERSION_CONFLICT
            )
            return TemplateImportConflict(status, identity, exact)
        if identity is None:
            return TemplateImportConflict(
                TemplateImportConflictStatus.NEW_IDENTITY,
                None,
                None,
            )
        installations = list(
            session.scalars(
                select(TemplatePackageInstallation).where(
                    TemplatePackageInstallation.publisher_id == publisher_id,
                    TemplatePackageInstallation.package_id == package_id,
                )
            )
        )
        latest = max(
            installations,
            key=lambda item: _SemanticVersionSortKey(item.release_version),
        )
        comparison = compare_semantic_versions(release_version, latest.release_version)
        status = (
            TemplateImportConflictStatus.NEWER_RELEASE
            if comparison > 0
            else TemplateImportConflictStatus.OLDER_RELEASE
        )
        return TemplateImportConflict(status, identity, latest)


class _SemanticVersionSortKey:
    def __init__(self, value: str) -> None:
        self.value = value

    def __lt__(self, other: _SemanticVersionSortKey) -> bool:
        return compare_semantic_versions(self.value, other.value) < 0
