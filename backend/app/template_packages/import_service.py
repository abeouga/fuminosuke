from __future__ import annotations

import hashlib
from collections.abc import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import JobArtifact, TemplatePackageInstallation, TemplateVersion
from app.db.session import Database
from app.services.template_mutation import begin_immediate, template_mutation_lock
from app.storage.local_storage import LocalStorageService
from app.template_packages.asset_committer import TemplateAssetCommitter
from app.template_packages.canonical_importer import CanonicalTemplateImporter
from app.template_packages.conflict_service import TemplateImportConflictService
from app.template_packages.import_models import (
    TemplateImportAssetInspection,
    TemplateImportConflictStatus,
    TemplateImportInspection,
    TemplatePackageImportError,
    TemplatePackageImportResult,
    ValidatedTemplatePackage,
)
from app.template_packages.package_reader import TemplatePackageReader
from app.template_packages.security_limits import (
    DEFAULT_TEMPLATE_PACKAGE_SECURITY_LIMITS,
    TemplatePackageSecurityLimits,
)


class TemplateImportInspectionService:
    def __init__(
        self,
        database: Database,
        *,
        limits: TemplatePackageSecurityLimits = DEFAULT_TEMPLATE_PACKAGE_SECURITY_LIMITS,
    ) -> None:
        self._database = database
        self._reader = TemplatePackageReader(limits)

    def inspect(self, content: bytes) -> TemplateImportInspection:
        package = self._reader.read(content)
        with self._database.session() as session:
            conflict = TemplateImportConflictService.inspect(
                session,
                publisher_id=package.manifest.publisher_id,
                package_id=package.manifest.package_id,
                release_version=package.manifest.version,
                content_digest=package.manifest.content_digest,
            )
            return self._inspection(package, conflict.status, conflict.installation)

    @staticmethod
    def _inspection(
        package: ValidatedTemplatePackage,
        status: TemplateImportConflictStatus,
        existing: TemplatePackageInstallation | None,
    ) -> TemplateImportInspection:
        return TemplateImportInspection(
            format_version=package.manifest.format_version,
            schema_version=package.manifest.schema_version,
            publisher_id=package.manifest.publisher_id,
            package_id=package.manifest.package_id,
            version=package.manifest.version,
            name=package.manifest.name,
            content_digest=package.manifest.content_digest,
            package_sha256=package.package_sha256,
            minimum_app_version=package.manifest.minimum_app_version,
            required_features=package.manifest.required_features,
            optional_features=package.manifest.optional_features,
            mode=package.template.mode.value,
            conflict_status=status,
            existing_local_template_id=(existing.local_template_id if existing else None),
            existing_local_template_version_id=(
                existing.local_template_version_id if existing else None
            ),
            document_identity_field_count=len(
                package.template.runtime.document_identity_fields
            ),
            document_name_field_paths=(
                package.template.runtime.document_name.field_paths
                if package.template.runtime.document_name is not None
                else []
            ),
            candidate_set_count=len(package.template.candidate_sets),
            assets=[
                TemplateImportAssetInspection(
                    path=item.path,
                    role=item.role,
                    media_type=item.media_type,
                    size=item.size,
                    sha256=item.sha256,
                    width=package.image_dimensions.get(item.path, (None, None))[0],
                    height=package.image_dimensions.get(item.path, (None, None))[1],
                )
                for item in package.manifest.files
                if item.path not in {"template.json", "quality.json"}
            ],
            spreadsheet_source_included=(package.template.assets.spreadsheet_source is not None),
            output_layout_included=(package.output_layout is not None),
        )


class TemplateImportService:
    def __init__(
        self,
        database: Database,
        storage: LocalStorageService,
        *,
        limits: TemplatePackageSecurityLimits = DEFAULT_TEMPLATE_PACKAGE_SECURITY_LIMITS,
    ) -> None:
        self._database = database
        self._storage = storage
        self._reader = TemplatePackageReader(limits)
        self._committer = TemplateAssetCommitter(storage)
        self._importer = CanonicalTemplateImporter()

    def import_package(
        self,
        content: bytes,
        *,
        expected_package_sha256: str,
        allow_older_version: bool = False,
        source_type: str = "FILE",
        failure_injector: Callable[[str], None] | None = None,
    ) -> TemplatePackageImportResult:
        package = self._reader.read(content)
        if package.package_sha256 != expected_package_sha256:
            raise TemplatePackageImportError(
                "IMPORT_PACKAGE_CHANGED",
                "inspection後にpackage bytesが変更されています。",
            )
        operation_id = package.package_sha256[:24]
        committed_paths: list[str] = []
        plan = None
        try:
            with template_mutation_lock(), self._database.session() as session:
                begin_immediate(session)
                conflict = TemplateImportConflictService.inspect(
                    session,
                    publisher_id=package.manifest.publisher_id,
                    package_id=package.manifest.package_id,
                    release_version=package.manifest.version,
                    content_digest=package.manifest.content_digest,
                )
                if conflict.status is TemplateImportConflictStatus.SAME_RELEASE:
                    assert conflict.installation is not None
                    self._validate_existing_installation(session, conflict.installation)
                    session.rollback()
                    return self._result(conflict.installation, created=False)
                if conflict.status is TemplateImportConflictStatus.VERSION_CONFLICT:
                    raise TemplatePackageImportError(
                        "IMPORT_VERSION_CONFLICT",
                        "同一identity/versionに異なるcontent digestが既に登録されています。",
                    )
                if (
                    conflict.status is TemplateImportConflictStatus.OLDER_RELEASE
                    and not allow_older_version
                ):
                    raise TemplatePackageImportError(
                        "IMPORT_OLDER_VERSION_CONFIRMATION_REQUIRED",
                        "古いversionの取り込みには明示確認が必要です。",
                    )
                plan = self._importer.build_plan(
                    session,
                    package,
                    existing_identity=conflict.identity,
                    source_type=source_type,
                )
                staged = self._committer.stage(operation_id, plan.files)
                if failure_injector is not None:
                    failure_injector("after_stage")
                self._importer.add_plan(session, plan)
                session.flush()
                if failure_injector is not None:
                    failure_injector("after_db_flush")
                committed_paths = self._committer.commit(
                    staged,
                    failure_injector=failure_injector,
                )
                if failure_injector is not None:
                    failure_injector("before_db_commit")
                session.commit()
                return self._result(plan.installation, created=True)
        except BaseException:
            persisted = (
                self._persisted_installation(plan.installation.id) if plan is not None else None
            )
            if persisted is None:
                self._committer.rollback(committed_paths)
            else:
                return self._result(persisted, created=True)
            raise
        finally:
            self._committer.cleanup_stage(operation_id)

    def _persisted_installation(self, installation_id: str) -> TemplatePackageInstallation | None:
        with self._database.session() as session:
            return session.scalar(
                select(TemplatePackageInstallation).where(
                    TemplatePackageInstallation.id == installation_id
                )
            )

    def _validate_existing_installation(
        self,
        session: Session,
        installation: TemplatePackageInstallation,
    ) -> None:
        version = session.get(TemplateVersion, installation.local_template_version_id)
        if version is None or not self._storage.exists(version.config_relative_path):
            raise TemplatePackageImportError(
                "IMPORT_EXISTING_INSTALLATION_DAMAGED",
                "既存のimport済みtemplate設定が見つかりません。",
            )
        mapping = installation.local_mapping_json.get("assetArtifactIds")
        if not isinstance(mapping, dict):
            raise TemplatePackageImportError(
                "IMPORT_EXISTING_INSTALLATION_DAMAGED",
                "既存installationのasset mappingが不正です。",
            )
        for artifact_id in mapping.values():
            if not isinstance(artifact_id, str):
                raise TemplatePackageImportError(
                    "IMPORT_EXISTING_INSTALLATION_DAMAGED",
                    "既存installationのartifact IDが不正です。",
                )
            artifact = session.get(JobArtifact, artifact_id)
            if artifact is None or not self._storage.exists(artifact.relative_path):
                raise TemplatePackageImportError(
                    "IMPORT_EXISTING_INSTALLATION_DAMAGED",
                    "既存のimport済みassetが見つかりません。",
                )
            content = self._storage.read_bytes(artifact.relative_path)
            if hashlib.sha256(content).hexdigest() != artifact.sha256:
                raise TemplatePackageImportError(
                    "IMPORT_EXISTING_INSTALLATION_DAMAGED",
                    "既存のimport済みassetのhashが一致しません。",
                )

    @staticmethod
    def _result(
        installation: TemplatePackageInstallation,
        *,
        created: bool,
    ) -> TemplatePackageImportResult:
        return TemplatePackageImportResult(
            installation_id=installation.id,
            local_template_id=installation.local_template_id,
            local_template_version_id=installation.local_template_version_id,
            local_job_id=installation.local_job_id,
            content_digest=installation.content_digest,
            release_version=installation.release_version,
            created=created,
            active_version_changed=False,
        )
