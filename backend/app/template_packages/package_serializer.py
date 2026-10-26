from __future__ import annotations

import hashlib
import io
import json
import re
import zipfile
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.template_packages.canonical_json import canonical_json_bytes
from app.template_packages.canonical_models import (
    CanonicalOutputLayout,
    CanonicalTemplate,
    CanonicalTemplateQuality,
)
from app.template_packages.content_digest import build_content_digest
from app.template_packages.export_models import TemplatePackageExportError
from app.template_packages.feature_registry import APPLICATION_VERSION
from app.template_packages.manifest_models import PackageFile, TemplatePackageManifest

_PATH_ROLE_MEDIA: dict[str, tuple[str, str]] = {
    "template.json": ("template", "application/json"),
    "quality.json": ("quality", "application/json"),
    "assets/clean-template.png": ("clean-template", "image/png"),
    "assets/handwriting-mask.png": ("handwriting-mask", "image/png"),
    "assets/handwriting-mask-edited.png": ("handwriting-mask-edited", "image/png"),
    "assets/preview.png": ("preview", "image/png"),
    "optional/handwriting-candidates.json": ("handwriting-candidates", "application/json"),
    "optional/output-layout.json": ("output-layout", "application/json"),
    "optional/spreadsheet-source.xlsx": (
        "spreadsheet-source",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ),
    "optional/printable.pdf": ("printable-pdf", "application/pdf"),
}
_FORBIDDEN_KEYS = {
    "apikey",
    "accesstoken",
    "refreshtoken",
    "oauthtoken",
    "ownerkey",
    "folderid",
    "spreadsheetid",
    "googlefileid",
    "templateid",
    "templateversionid",
    "sourcejobid",
    "relativepath",
}
_SECRET_VALUE = re.compile(
    r"(?:-----BEGIN [A-Z ]*PRIVATE KEY-----|\bsk-[A-Za-z0-9_-]{16,}\b|\bya29\.[A-Za-z0-9_-]{12,}\b)"
)


@dataclass(frozen=True)
class BuiltTemplatePackage:
    content: bytes
    manifest: TemplatePackageManifest


class TemplatePackageSerializer:
    def build(
        self,
        *,
        canonical_template: CanonicalTemplate,
        quality: CanonicalTemplateQuality,
        output_layout: CanonicalOutputLayout | None,
        asset_payloads: dict[str, bytes],
        publisher_id: str,
        package_id: str,
        release_version: str,
        exported_at: datetime,
    ) -> BuiltTemplatePackage:
        documents: dict[str, object] = {
            "template.json": canonical_template,
            "quality.json": quality,
        }
        if output_layout is not None:
            documents["optional/output-layout.json"] = output_layout
        if candidates := asset_payloads.get("optional/handwriting-candidates.json"):
            try:
                documents["optional/handwriting-candidates.json"] = json.loads(candidates)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise TemplatePackageExportError(
                    "TEMPLATE_PACKAGE_DOCUMENT_INVALID",
                    "手書き候補JSONを正規化できません。",
                ) from exc

        for path, document in documents.items():
            self._reject_nonportable_data(document, path=path)
        document_bytes = {path: canonical_json_bytes(value) for path, value in documents.items()}
        binary_assets = {
            path: content
            for path, content in asset_payloads.items()
            if path not in document_bytes
        }
        asset_hashes = {
            path: hashlib.sha256(content).hexdigest() for path, content in binary_assets.items()
        }
        content_digest = build_content_digest(documents=documents, asset_sha256=asset_hashes)
        payloads = {**document_bytes, **binary_assets}
        files = [self._manifest_file(path, content) for path, content in sorted(payloads.items())]
        manifest = TemplatePackageManifest(
            publisher_id=publisher_id,
            package_id=package_id,
            name=canonical_template.name,
            version=release_version,
            minimum_app_version=APPLICATION_VERSION,
            required_features=canonical_template.required_features(),
            optional_features=[],
            exported_at=exported_at,
            content_digest=content_digest,
            files=files,
        )
        manifest_bytes = canonical_json_bytes(manifest)
        content = self._write_zip({"manifest.json": manifest_bytes, **payloads})
        GeneratedTemplatePackageValidator().validate(content)
        return BuiltTemplatePackage(content=content, manifest=manifest)

    @staticmethod
    def _manifest_file(path: str, content: bytes) -> PackageFile:
        contract = _PATH_ROLE_MEDIA.get(path)
        if contract is None:
            raise TemplatePackageExportError(
                "TEMPLATE_PACKAGE_PATH_UNSUPPORTED", f"未対応のpackage pathです: {path}"
            )
        role, media_type = contract
        return PackageFile(
            path=path,
            role=role,  # type: ignore[arg-type]
            media_type=media_type,
            size=len(content),
            sha256=hashlib.sha256(content).hexdigest(),
        )

    @staticmethod
    def _write_zip(entries: dict[str, bytes]) -> bytes:
        output = io.BytesIO()
        with zipfile.ZipFile(
            output,
            mode="w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=9,
        ) as archive:
            for path, content in sorted(entries.items()):
                info = zipfile.ZipInfo(path, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.create_system = 3
                info.external_attr = 0o100644 << 16
                archive.writestr(info, content)
        return output.getvalue()

    @classmethod
    def _reject_nonportable_data(cls, value: object, *, path: str) -> None:
        if isinstance(value, CanonicalTemplate | CanonicalTemplateQuality | CanonicalOutputLayout):
            cls._reject_nonportable_data(
                value.model_dump(mode="json", by_alias=True, exclude_none=False), path=path
            )
            return
        if isinstance(value, dict):
            for key, item in value.items():
                normalized = re.sub(r"[^a-z0-9]", "", str(key).casefold())
                if normalized in _FORBIDDEN_KEYS:
                    raise TemplatePackageExportError(
                        "TEMPLATE_PACKAGE_FORBIDDEN_FIELD",
                        f"配布禁止項目が含まれています: {path}.{key}",
                    )
                cls._reject_nonportable_data(item, path=f"{path}.{key}")
            return
        if isinstance(value, list):
            for index, item in enumerate(value):
                cls._reject_nonportable_data(item, path=f"{path}[{index}]")
            return
        if isinstance(value, str) and _SECRET_VALUE.search(value):
            raise TemplatePackageExportError(
                "TEMPLATE_PACKAGE_SECRET_DETECTED",
                f"秘密情報とみられる値が含まれています: {path}",
            )


class GeneratedTemplatePackageValidator:
    """Validates packages generated in memory; Phase 3 will add the untrusted reader limits."""

    def validate(self, content: bytes) -> TemplatePackageManifest:
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                names = archive.namelist()
                if len(names) != len(set(names)) or "manifest.json" not in names:
                    raise TemplatePackageExportError(
                        "GENERATED_PACKAGE_INVALID", "生成packageのentry構造が不正です。"
                    )
                raw_entries = {name: archive.read(name) for name in names}
        except zipfile.BadZipFile as exc:
            raise TemplatePackageExportError(
                "GENERATED_PACKAGE_INVALID", "生成packageをZIPとして再読込できません。"
            ) from exc
        try:
            manifest = TemplatePackageManifest.model_validate_json(raw_entries["manifest.json"])
        except ValueError as exc:
            raise TemplatePackageExportError(
                "GENERATED_PACKAGE_INVALID", "生成manifestを再検証できません。"
            ) from exc
        expected_names = {"manifest.json", *(item.path for item in manifest.files)}
        if set(names) != expected_names:
            raise TemplatePackageExportError(
                "GENERATED_PACKAGE_INVALID", "生成manifestとZIP entryが一致しません。"
            )
        for item in manifest.files:
            payload = raw_entries[item.path]
            if len(payload) != item.size or hashlib.sha256(payload).hexdigest() != item.sha256:
                raise TemplatePackageExportError(
                    "GENERATED_PACKAGE_INVALID", "生成packageのsizeまたはhashが一致しません。"
                )

        try:
            template_value = json.loads(raw_entries["template.json"])
            quality_value = json.loads(raw_entries["quality.json"])
            CanonicalTemplate.model_validate(template_value)
            CanonicalTemplateQuality.model_validate(quality_value)
            documents: dict[str, Any] = {
                "template.json": template_value,
                "quality.json": quality_value,
            }
            if "optional/output-layout.json" in raw_entries:
                value = json.loads(raw_entries["optional/output-layout.json"])
                CanonicalOutputLayout.model_validate(value)
                documents["optional/output-layout.json"] = value
            if "optional/handwriting-candidates.json" in raw_entries:
                documents["optional/handwriting-candidates.json"] = json.loads(
                    raw_entries["optional/handwriting-candidates.json"]
                )
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            raise TemplatePackageExportError(
                "GENERATED_PACKAGE_INVALID", "生成packageのCanonical documentが不正です。"
            ) from exc
        binary_hashes = {
            item.path: item.sha256
            for item in manifest.files
            if item.media_type != "application/json"
        }
        calculated_digest = build_content_digest(
            documents=documents, asset_sha256=binary_hashes
        )
        if calculated_digest != manifest.content_digest:
            raise TemplatePackageExportError(
                "GENERATED_PACKAGE_INVALID", "生成packageのcontent digestが一致しません。"
            )
        return manifest
