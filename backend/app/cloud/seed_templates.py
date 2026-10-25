"""Install the default template packages into a tenant the first time its runtime starts.

Each package is imported and activated through the regular template-package path, so the
mapping, layout and runtime config are the exported ones. A marker in the tenant's own storage
records the install; deleting the template later does not bring it back.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

from app.db.models import TemplateVersion
from app.db.session import Database
from app.storage.local_storage import LocalStorageService
from app.template_packages.activation_service import TemplatePackageActivationService
from app.template_packages.import_service import TemplateImportService

logger = logging.getLogger(__name__)

PACKAGE_SUFFIX = ".llmocr-template"


def seed_templates(directory: Path, database: Database, storage: LocalStorageService) -> None:
    for package in sorted(directory.glob("*" + PACKAGE_SUFFIX)):
        content = package.read_bytes()
        sha = hashlib.sha256(content).hexdigest()
        marker = f"exports/seed-templates/{sha}.done"
        if storage.exists(marker):
            continue
        try:
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
        except Exception:
            # Login continues without the template; the next runtime start retries.
            logger.exception("Default template %s could not be installed", package.name)
            continue
        storage.write_bytes(marker, package.name.encode())
        logger.info("Installed default template %s", package.name)
