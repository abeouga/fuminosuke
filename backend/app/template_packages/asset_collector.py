from __future__ import annotations

import hashlib
import io
import json
from dataclasses import dataclass

from PIL import Image, UnidentifiedImageError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import JobArtifact, TemplateVersion
from app.storage.local_storage import LocalStorageService
from app.template_packages.canonical_json import canonical_json_bytes
from app.template_packages.export_models import ExportAssetInspection, TemplatePackageExportError
from app.template_packages.spreadsheet_sanitizer import SpreadsheetSanitizer

MAX_IMAGE_PIXELS = 100_000_000
XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@dataclass(frozen=True)
class CollectedTemplateAssets:
    payloads: dict[str, bytes]
    inspections: list[ExportAssetInspection]
    source_sheet_name: str | None
    source_file_name: str | None


class TemplateAssetCollector:
    def __init__(
        self,
        storage: LocalStorageService,
        spreadsheet_sanitizer: SpreadsheetSanitizer | None = None,
    ) -> None:
        self._storage = storage
        self._spreadsheet_sanitizer = spreadsheet_sanitizer or SpreadsheetSanitizer()

    def collect(
        self,
        session: Session,
        version: TemplateVersion,
        *,
        include_preview: bool,
        include_handwriting_candidates: bool,
    ) -> CollectedTemplateAssets:
        clean = self._required_artifact(
            session, version.clean_template_artifact_id, "cleanTemplate"
        )
        payloads: dict[str, bytes] = {}
        inspections: list[ExportAssetInspection] = []
        self._add_png(
            payloads,
            inspections,
            path="assets/clean-template.png",
            role="clean-template",
            artifact=clean,
            expected_dimensions=(version.canonical_width, version.canonical_height),
            mode="RGB",
        )

        job_id = version.source_job_id or clean.job_id
        artifacts = list(
            session.scalars(
                select(JobArtifact)
                .where(JobArtifact.job_id == job_id)
                .order_by(JobArtifact.attempt_number.desc(), JobArtifact.created_at.desc())
            )
        )
        by_name: dict[str, JobArtifact] = {}
        for artifact in artifacts:
            by_name.setdefault(artifact.artifact_name, artifact)

        original_mask = by_name.get("handwritingMask")
        selected_mask = (
            session.get(JobArtifact, version.mask_artifact_id)
            if version.mask_artifact_id
            else None
        )
        edited_mask = (
            selected_mask
            if selected_mask and selected_mask.artifact_name.split("~", 1)[0] == "handwritingMaskEdited"
            else None
        )
        if original_mask is not None:
            self._add_png(
                payloads,
                inspections,
                path="assets/handwriting-mask.png",
                role="handwriting-mask",
                artifact=original_mask,
                expected_dimensions=(version.canonical_width, version.canonical_height),
                mode="L",
            )
        elif selected_mask is not None and edited_mask is None:
            self._add_png(
                payloads,
                inspections,
                path="assets/handwriting-mask.png",
                role="handwriting-mask",
                artifact=selected_mask,
                expected_dimensions=(version.canonical_width, version.canonical_height),
                mode="L",
            )
        if edited_mask is not None:
            self._add_png(
                payloads,
                inspections,
                path="assets/handwriting-mask-edited.png",
                role="handwriting-mask-edited",
                artifact=edited_mask,
                expected_dimensions=(version.canonical_width, version.canonical_height),
                mode="L",
            )

        if include_preview and (preview := by_name.get("preview")) is not None:
            self._add_png(
                payloads,
                inspections,
                path="assets/preview.png",
                role="preview",
                artifact=preview,
                expected_dimensions=None,
                mode="RGB",
            )
        if include_handwriting_candidates and (
            candidates := by_name.get("handwritingCandidates")
        ) is not None:
            raw = self._verified_artifact_bytes(candidates, expected_media_type="application/json")
            try:
                value = json.loads(raw)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise TemplatePackageExportError(
                    "TEMPLATE_ASSET_INVALID",
                    "手書き候補JSONを読み込めません。",
                ) from exc
            normalized = canonical_json_bytes(value)
            self._add_payload(
                payloads,
                inspections,
                path="optional/handwriting-candidates.json",
                role="handwriting-candidates",
                media_type="application/json",
                content=normalized,
                sanitized=True,
            )

        source_sheet_name: str | None = None
        source_file_name: str | None = None
        if version.mode == "SPREADSHEET_TEMPLATE":
            source = by_name.get("sourceWorkbook")
            if source is None:
                raise TemplatePackageExportError(
                    "SPREADSHEET_SOURCE_REQUIRED",
                    "Spreadsheet templateの元XLSXが見つかりません。",
                )
            source_sheet_name = self._required_quality_string(
                version,
                "sourceSheetName",
                preserve_whitespace=True,
            )
            source_file_name = self._required_quality_string(version, "sourceFileName")
            if not source_file_name.lower().endswith(".xlsx"):
                raise TemplatePackageExportError(
                    "SPREADSHEET_SOURCE_INVALID",
                    "元XLSXのファイル名が.xlsxではありません。",
                )
            raw = self._verified_artifact_bytes(source, expected_media_type=XLSX_MEDIA_TYPE)
            sanitized = self._spreadsheet_sanitizer.sanitize(
                raw,
                source_sheet_name=source_sheet_name,
            )
            self._add_payload(
                payloads,
                inspections,
                path="optional/spreadsheet-source.xlsx",
                role="spreadsheet-source",
                media_type=XLSX_MEDIA_TYPE,
                content=sanitized.content,
                sanitized=True,
            )
            printable = by_name.get("printablePdf")
            if printable is not None:
                self._add_payload(
                    payloads,
                    inspections,
                    path="optional/printable.pdf",
                    role="printable-pdf",
                    media_type="application/pdf",
                    content=self._verified_artifact_bytes(
                        printable, expected_media_type="application/pdf"
                    ),
                    sanitized=False,
                )
        elif by_name.get("sourceWorkbook") is not None:
            raise TemplatePackageExportError(
                "SPREADSHEET_SOURCE_MODE_MISMATCH",
                "Spreadsheet template以外に元XLSXは含められません。",
            )

        return CollectedTemplateAssets(
            payloads=payloads,
            inspections=sorted(inspections, key=lambda item: item.role),
            source_sheet_name=source_sheet_name,
            source_file_name=source_file_name,
        )

    def _add_png(
        self,
        payloads: dict[str, bytes],
        inspections: list[ExportAssetInspection],
        *,
        path: str,
        role: str,
        artifact: JobArtifact,
        expected_dimensions: tuple[int, int] | None,
        mode: str,
    ) -> None:
        raw = self._verified_artifact_bytes(artifact, expected_media_type="image/png")
        try:
            Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS
            with Image.open(io.BytesIO(raw)) as image:
                image.load()
                if image.format != "PNG":
                    raise TemplatePackageExportError(
                        "TEMPLATE_IMAGE_INVALID", "テンプレート画像はPNGではありません。"
                    )
                width, height = image.size
                if width * height > MAX_IMAGE_PIXELS:
                    raise TemplatePackageExportError(
                        "TEMPLATE_IMAGE_TOO_LARGE",
                        "テンプレート画像のpixel数が上限を超えています。",
                    )
                if expected_dimensions is not None and (width, height) != expected_dimensions:
                    raise TemplatePackageExportError(
                        "TEMPLATE_IMAGE_DIMENSION_MISMATCH",
                        "テンプレート画像の寸法が登録値と一致しません。",
                    )
                if artifact.width is not None and artifact.width != width:
                    raise TemplatePackageExportError(
                        "TEMPLATE_IMAGE_DIMENSION_MISMATCH",
                        "テンプレート画像の幅がartifact情報と一致しません。",
                    )
                if artifact.height is not None and artifact.height != height:
                    raise TemplatePackageExportError(
                        "TEMPLATE_IMAGE_DIMENSION_MISMATCH",
                        "テンプレート画像の高さがartifact情報と一致しません。",
                    )
                clean = image.convert(mode)
                output = io.BytesIO()
                clean.save(output, format="PNG", optimize=False)
        except (UnidentifiedImageError, OSError) as exc:
            raise TemplatePackageExportError(
                "TEMPLATE_IMAGE_INVALID", "テンプレート画像をdecodeできません。"
            ) from exc
        self._add_payload(
            payloads,
            inspections,
            path=path,
            role=role,
            media_type="image/png",
            content=output.getvalue(),
            width=width,
            height=height,
            sanitized=True,
        )

    def _verified_artifact_bytes(self, artifact: JobArtifact, *, expected_media_type: str) -> bytes:
        if artifact.media_type != expected_media_type:
            raise TemplatePackageExportError(
                "TEMPLATE_ASSET_MEDIA_TYPE_MISMATCH", "artifactのmedia typeが一致しません。"
            )
        try:
            content = self._storage.read_bytes(artifact.relative_path)
        except OSError as exc:
            raise TemplatePackageExportError(
                "TEMPLATE_ASSET_MISSING", "テンプレートassetが見つかりません。"
            ) from exc
        if hashlib.sha256(content).hexdigest() != artifact.sha256:
            raise TemplatePackageExportError(
                "TEMPLATE_ASSET_HASH_MISMATCH", "テンプレートassetのhashが登録値と一致しません。"
            )
        return content

    @staticmethod
    def _required_artifact(
        session: Session, artifact_id: str | None, artifact_name: str
    ) -> JobArtifact:
        artifact = session.get(JobArtifact, artifact_id) if artifact_id else None
        if artifact is None or artifact.artifact_name != artifact_name:
            raise TemplatePackageExportError(
                "TEMPLATE_ASSET_REQUIRED", "clean template assetが見つかりません。"
            )
        return artifact

    @staticmethod
    def _required_quality_string(
        version: TemplateVersion,
        key: str,
        *,
        preserve_whitespace: bool = False,
    ) -> str:
        value = version.quality_json.get(key)
        if not isinstance(value, str) or not value.strip():
            raise TemplatePackageExportError(
                "SPREADSHEET_SOURCE_METADATA_REQUIRED", f"{key}が設定されていません。"
            )
        return value if preserve_whitespace else value.strip()

    @staticmethod
    def _add_payload(
        payloads: dict[str, bytes],
        inspections: list[ExportAssetInspection],
        *,
        path: str,
        role: str,
        media_type: str,
        content: bytes,
        width: int | None = None,
        height: int | None = None,
        sanitized: bool,
    ) -> None:
        payloads[path] = content
        inspections.append(
            ExportAssetInspection(
                role=role,
                media_type=media_type,
                size=len(content),
                sha256=hashlib.sha256(content).hexdigest(),
                width=width,
                height=height,
                sanitized=sanitized,
            )
        )
