"""Download the unchanged workbook and saved PDF for a legacy XLSX template."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import JobArtifact, Template, TemplateVersion
from app.db.session import Database
from app.domain.enums import TemplateMode, TemplateVersionStatus
from app.domain.print_layout import print_error
from app.storage.local_storage import LocalStorageService, StoragePathError


@dataclass(frozen=True, slots=True)
class LegacyTemplatePrintFile:
    content: bytes
    sha256: str


class LegacyTemplatePrintService:
    def __init__(self, database: Database, storage: LocalStorageService) -> None:
        self.database = database
        self.storage = storage

    def export(
        self, version_id: str, format_name: Literal["xlsx", "pdf"],
    ) -> LegacyTemplatePrintFile:
        with self.database.session() as session:
            version = session.get(TemplateVersion, version_id)
            if version is None:
                raise print_error("テンプレートがありません。", "TEMPLATE_NOT_FOUND", 404)
            owner = session.get(Template, version.template_id)
            quality = version.quality_json
            # A template imported from a package keeps the package's (sanitized) workbook, and the
            # printed blank form when the package carried one.
            imported = (
                quality.get("sourceType") in {"template-package", "template-package-drive"}
                and bool(quality.get("spreadsheetSourcePath"))
            )
            if (
                version.mode != TemplateMode.SPREADSHEET_TEMPLATE.value
                or version.status != TemplateVersionStatus.ACTIVE.value
                or owner is None
                or owner.active_version_id != version.id
                or not (quality.get("sourceType") == "xlsx" or imported)
                or quality.get("printLayout") is not None
                or (not imported and quality.get("spreadsheetPrintablePageCount") != 1)
                or not version.source_job_id
            ):
                raise print_error(
                    "単一ページのXLSX由来の使用中テンプレートだけが対象です。",
                    "PRINT_FILE_LEGACY_UNAVAILABLE", 409,
                )
            clean = (
                session.get(JobArtifact, version.clean_template_artifact_id)
                if version.clean_template_artifact_id else None
            )
            if clean is None or clean.job_id != version.source_job_id:
                raise print_error(
                    "テンプレートの元画像を確認できません。", "PRINT_FILE_SOURCE_MISSING", 409,
                )
            source = self._verified_content(
                session, version.source_job_id, clean.attempt_number,
                "sourceWorkbook", quality.get("spreadsheetSourcePath"),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
            if format_name == "xlsx":
                return source
            if imported and not quality.get("spreadsheetPrintablePdfPath"):
                raise print_error(
                    "取り込んだテンプレートには印刷用PDFがありません。印刷用XLSXを使ってください。",
                    "PRINT_FILE_SOURCE_MISSING", 409,
                )
            return self._verified_content(
                session, version.source_job_id, clean.attempt_number,
                "printablePdf", quality.get("spreadsheetPrintablePdfPath"),
                "application/pdf",
            )

    def _verified_content(
        self, session: Session, job_id: str, attempt: int, artifact_name: str,
        quality_path: object, media_type: str,
    ) -> LegacyTemplatePrintFile:
        artifact = session.scalar(select(JobArtifact).where(
            JobArtifact.job_id == job_id,
            JobArtifact.attempt_number == attempt,
            JobArtifact.artifact_name == artifact_name,
        ))
        if artifact is None or not isinstance(quality_path, str) or not quality_path:
            raise print_error("印刷用の元ファイルがありません。", "PRINT_FILE_SOURCE_MISSING", 409)
        if artifact.media_type != media_type:
            raise print_error("印刷用ファイルの形式が一致しません。", "PRINT_FILE_CHANGED", 409)
        try:
            content = self.storage.read_bytes(artifact.relative_path)
            quality_content = (
                content if quality_path == artifact.relative_path
                else self.storage.read_bytes(quality_path)
            )
        except (OSError, StoragePathError) as exc:
            raise print_error(
                "印刷用の元ファイルがありません。", "PRINT_FILE_SOURCE_MISSING", 409,
            ) from exc
        actual_sha = hashlib.sha256(content).hexdigest()
        if (
            actual_sha != artifact.sha256
            or hashlib.sha256(quality_content).hexdigest() != actual_sha
        ):
            raise print_error("印刷用ファイルの内容が一致しません。", "PRINT_FILE_CHANGED", 409)
        return LegacyTemplatePrintFile(content, actual_sha)
