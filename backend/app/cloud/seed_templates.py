"""Install the default template packages into a tenant the first time its runtime starts.

Each package is imported and activated through the regular template-package path, so the
mapping, layout and runtime config are the exported ones. A marker in the tenant's own storage
records the install; deleting the template later does not bring it back.

A package already installed under the same identity (a newer export of the same template) is not
imported again: only the printed blank form it now carries is added to the installed template, so
the documents read on it stay where they are.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

from app.db.models import (
    JobArtifact,
    Template,
    TemplatePackageIdentity,
    TemplateVersion,
    uuid_string,
)
from app.db.session import Database
from app.domain.enums import TemplateMode
from app.storage.local_storage import LocalStorageService
from app.template_packages.activation_service import TemplatePackageActivationService
from app.template_packages.import_models import ValidatedTemplatePackage
from app.template_packages.import_service import TemplateImportService
from app.template_packages.package_reader import TemplatePackageReader

logger = logging.getLogger(__name__)

PACKAGE_SUFFIX = ".llmocr-template"
PRINTABLE_PDF = "optional/printable.pdf"


def seed_templates(directory: Path, database: Database, storage: LocalStorageService) -> None:
    for package in sorted(directory.glob("*" + PACKAGE_SUFFIX)):
        content = package.read_bytes()
        sha = hashlib.sha256(content).hexdigest()
        marker = f"exports/seed-templates/{sha}.done"
        if storage.exists(marker):
            continue
        try:
            validated = TemplatePackageReader().read(content)
            if _installed(database, validated):
                _add_printable_pdf(database, storage, validated)
            else:
                _install(database, storage, content, sha)
        except Exception:
            # Login continues without the template; the next runtime start retries.
            logger.exception("Default template %s could not be installed", package.name)
            continue
        storage.write_bytes(marker, package.name.encode())
        logger.info("Installed default template %s", package.name)


def _install(database: Database, storage: LocalStorageService, content: bytes, sha: str) -> None:
    imported = TemplateImportService(database, storage).import_package(
        content, expected_package_sha256=sha,
    )
    with database.session() as session:
        version = session.get(TemplateVersion, imported.local_template_version_id)
        active = version is not None and version.status == "ACTIVE"
        revision = version.revision if version is not None else 0
    if not active:
        TemplatePackageActivationService(database).activate(
            imported.installation_id,
            expected_active_version_id=None,
            expected_revision=revision,
            owner_key="local",
        )


def _installed(database: Database, package: ValidatedTemplatePackage) -> bool:
    with database.session() as session:
        return session.get(
            TemplatePackageIdentity, (package.manifest.publisher_id, package.manifest.package_id),
        ) is not None


def _add_printable_pdf(
    database: Database, storage: LocalStorageService, package: ValidatedTemplatePackage,
) -> None:
    pdf = package.payloads.get(PRINTABLE_PDF)
    if pdf is None:
        return
    with database.session() as session:
        identity = session.get(
            TemplatePackageIdentity, (package.manifest.publisher_id, package.manifest.package_id),
        )
        owner = session.get(Template, identity.local_template_id) if identity else None
        version = (
            session.get(TemplateVersion, owner.active_version_id)
            if owner and owner.active_version_id else None
        )
        if (
            version is None
            or version.mode != TemplateMode.SPREADSHEET_TEMPLATE.value
            or not str(version.quality_json.get("sourceType", "")).startswith("template-package")
            or version.quality_json.get("spreadsheetPrintablePdfPath")
            or not version.source_job_id
        ):
            return
        clean = (
            session.get(JobArtifact, version.clean_template_artifact_id)
            if version.clean_template_artifact_id else None
        )
        if clean is None or clean.job_id != version.source_job_id:
            return
        path = f"artifacts/{version.source_job_id}/attempts/{clean.attempt_number}/printable.pdf"
        storage.write_bytes(path, pdf)
        session.add(JobArtifact(
            id=uuid_string(),
            job_id=version.source_job_id,
            attempt_number=clean.attempt_number,
            artifact_name="printablePdf",
            media_type="application/pdf",
            relative_path=path,
            sha256=hashlib.sha256(pdf).hexdigest(),
        ))
        version.quality_json = {**version.quality_json, "spreadsheetPrintablePdfPath": path}
        session.commit()
        logger.info("Added the printable PDF to template %s", owner.id if owner else "?")
