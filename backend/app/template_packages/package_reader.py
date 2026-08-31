from __future__ import annotations

import hashlib
import html
import io
import json
import re
import stat
import threading
import unicodedata
import zipfile
from pathlib import PurePosixPath
from typing import Any

from PIL import Image, UnidentifiedImageError

from app.template_packages.canonical_json import canonical_json_bytes
from app.template_packages.canonical_models import (
    CanonicalOutputLayout,
    CanonicalTemplate,
    CanonicalTemplateQuality,
)
from app.template_packages.content_digest import build_content_digest
from app.template_packages.feature_registry import evaluate_compatibility
from app.template_packages.import_models import (
    TemplatePackageImportError,
    ValidatedTemplatePackage,
)
from app.template_packages.manifest_models import PackageFile, TemplatePackageManifest
from app.template_packages.package_paths import path_collision_key, validate_package_path
from app.template_packages.security_limits import (
    DEFAULT_TEMPLATE_PACKAGE_SECURITY_LIMITS,
    TemplatePackageSecurityLimits,
)

_SUPPORTED_COMPRESSION = {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_IMAGE_LIMIT_LOCK = threading.Lock()
_XLSX_FORBIDDEN_PREFIXES = (
    "xl/externalLinks/",
    "xl/embeddings/",
    "xl/oleObjects/",
    "customXml/",
    "xl/comments",
)
_XLSX_FORBIDDEN_NAMES = {"xl/vbaProject.bin", "xl/vbaData.xml"}
_EXTERNAL_RELATIONSHIP = re.compile(
    rb"TargetMode\s*=\s*[\"']External[\"']",
    re.IGNORECASE,
)
_EXTERNAL_FORMULA = re.compile(
    rb"<f(?:\s[^>]*)?>[^<]*(?:\[[^<\]]+\]|(?:https?|file):)",
    re.IGNORECASE,
)
_HIDDEN_XML = re.compile(
    rb"\b(?:state\s*=\s*[\"'](?:hidden|veryHidden)[\"']|hidden\s*=\s*[\"']1[\"'])",
    re.IGNORECASE,
)
_SHEET_NAME = re.compile(rb"<sheet\b[^>]*\bname\s*=\s*[\"']([^\"']*)[\"']", re.IGNORECASE)


class TemplatePackageReader:
    """Bounded reader for untrusted .llmocr-template input.

    It deliberately does not call GeneratedTemplatePackageValidator and never extracts ZIPs.
    """

    def __init__(
        self,
        limits: TemplatePackageSecurityLimits = DEFAULT_TEMPLATE_PACKAGE_SECURITY_LIMITS,
    ) -> None:
        self._limits = limits

    @property
    def limits(self) -> TemplatePackageSecurityLimits:
        return self._limits

    def read(self, content: bytes) -> ValidatedTemplatePackage:
        if len(content) > self._limits.max_package_bytes:
            raise self._error("PACKAGE_TOO_LARGE", "package sizeが上限を超えています。")
        package_sha256 = hashlib.sha256(content).hexdigest()
        try:
            archive = zipfile.ZipFile(io.BytesIO(content))
        except (zipfile.BadZipFile, zipfile.LargeZipFile) as exc:
            raise self._error("PACKAGE_ZIP_INVALID", "packageをZIPとして読み込めません。") from exc
        with archive:
            if archive.comment:
                raise self._error("PACKAGE_ZIP_METADATA_FORBIDDEN", "ZIP commentは使用できません。")
            infos = archive.infolist()
            self._validate_archive_structure(infos)
            info_by_name = {info.filename: info for info in infos}
            manifest_info = info_by_name.get("manifest.json")
            if manifest_info is None:
                raise self._error("PACKAGE_MANIFEST_MISSING", "manifest.jsonがありません。")
            manifest_bytes = self._read_entry(
                archive,
                manifest_info,
                self._limits.max_manifest_bytes,
                "PACKAGE_MANIFEST_TOO_LARGE",
            )
            self._parse_canonical_json(
                manifest_bytes,
                path="manifest.json",
            )
            try:
                manifest = TemplatePackageManifest.model_validate_json(manifest_bytes)
            except ValueError as exc:
                raise self._error(
                    "PACKAGE_MANIFEST_INVALID", "manifest.jsonの形式が不正です。"
                ) from exc
            compatibility = evaluate_compatibility(
                minimum_application_version=manifest.minimum_app_version,
                required_features=manifest.required_features,
            )
            if not compatibility.compatible:
                if compatibility.unsupported_required_features:
                    raise self._error(
                        "PACKAGE_REQUIRED_FEATURE_UNSUPPORTED",
                        "対応していないrequired featureが含まれています。",
                    )
                raise self._error(
                    "PACKAGE_APP_VERSION_UNSUPPORTED",
                    "このpackageにはより新しいアプリversionが必要です。",
                )
            expected_names = {"manifest.json", *(item.path for item in manifest.files)}
            if set(info_by_name) != expected_names:
                raise self._error(
                    "PACKAGE_UNDECLARED_ENTRY",
                    "manifest未記載または不足しているZIP entryがあります。",
                )

            payloads: dict[str, bytes] = {}
            image_dimensions: dict[str, tuple[int, int]] = {}
            documents: dict[str, Any] = {}
            binary_hashes: dict[str, str] = {}
            for item in manifest.files:
                info = info_by_name[item.path]
                limit = self._entry_limit(item)
                payload = self._read_entry(
                    archive,
                    info,
                    limit,
                    "PACKAGE_ENTRY_TOO_LARGE",
                )
                if len(payload) != item.size:
                    raise self._error(
                        "PACKAGE_SIZE_MISMATCH", f"sizeがmanifestと一致しません: {item.path}"
                    )
                digest = hashlib.sha256(payload).hexdigest()
                if digest != item.sha256:
                    raise self._error(
                        "PACKAGE_HASH_MISMATCH", f"SHA-256が一致しません: {item.path}"
                    )
                payloads[item.path] = payload
                if item.media_type == "application/json":
                    documents[item.path] = self._parse_canonical_json(payload, path=item.path)
                else:
                    binary_hashes[item.path] = digest
                    if item.media_type == "image/png":
                        image_dimensions[item.path] = self._validate_png(payload, item.path)
                    elif item.role == "spreadsheet-source":
                        if not payload.startswith(b"PK\x03\x04"):
                            raise self._error(
                                "PACKAGE_XLSX_INVALID", "XLSX signatureが不正です。"
                            )
                    else:
                        raise self._error(
                            "PACKAGE_MEDIA_TYPE_UNSUPPORTED",
                            f"未対応のmedia typeです: {item.media_type}",
                        )

        template = self._model(
            CanonicalTemplate,
            documents.get("template.json"),
            "PACKAGE_TEMPLATE_INVALID",
        )
        quality = self._model(
            CanonicalTemplateQuality,
            documents.get("quality.json"),
            "PACKAGE_QUALITY_INVALID",
        )
        output_layout = None
        if "optional/output-layout.json" in documents:
            output_layout = self._model(
                CanonicalOutputLayout,
                documents["optional/output-layout.json"],
                "PACKAGE_OUTPUT_LAYOUT_INVALID",
            )
        handwriting_candidates = documents.get("optional/handwriting-candidates.json")
        if template.spreadsheet is not None:
            spreadsheet_payload = payloads.get("optional/spreadsheet-source.xlsx")
            if spreadsheet_payload is None:
                raise self._error(
                    "PACKAGE_XLSX_MISSING", "spreadsheet source XLSXがありません。"
                )
            XlsxPackageValidator(self._limits).validate(
                spreadsheet_payload,
                required_sheet_name=template.spreadsheet.sheet_name,
            )
        calculated_digest = build_content_digest(
            documents=documents,
            asset_sha256=binary_hashes,
        )
        if calculated_digest != manifest.content_digest:
            raise self._error(
                "PACKAGE_CONTENT_DIGEST_MISMATCH", "content digestが一致しません。"
            )
        self._validate_cross_references(
            manifest,
            template,
            quality,
            output_layout,
            payloads,
            image_dimensions,
        )
        return ValidatedTemplatePackage(
            manifest=manifest,
            template=template,
            quality=quality,
            output_layout=output_layout,
            handwriting_candidates=handwriting_candidates,
            payloads=payloads,
            image_dimensions=image_dimensions,
            package_sha256=package_sha256,
        )

    def _validate_archive_structure(self, infos: list[zipfile.ZipInfo]) -> None:
        if not infos or len(infos) > self._limits.max_entry_count:
            raise self._error("PACKAGE_ENTRY_LIMIT", "ZIP entry数が上限を超えています。")
        exact_names: set[str] = set()
        collision_keys: set[str] = set()
        total_size = 0
        for info in infos:
            name = self._validate_entry_name(info.filename)
            if name in exact_names:
                raise self._error("PACKAGE_DUPLICATE_ENTRY", "重複ZIP entryがあります。")
            exact_names.add(name)
            collision_key = path_collision_key(name)
            if collision_key in collision_keys:
                raise self._error(
                    "PACKAGE_PATH_COLLISION",
                    "Unicode/casefold後に衝突するZIP entryがあります。",
                )
            collision_keys.add(collision_key)
            self._validate_zip_info(info)
            total_size += info.file_size
            if total_size > self._limits.max_total_uncompressed_bytes:
                raise self._error(
                    "PACKAGE_UNCOMPRESSED_LIMIT", "展開後総容量が上限を超えています。"
                )
            self._validate_ratio(
                info.file_size,
                info.compress_size,
                self._limits.max_compression_ratio,
                "PACKAGE_COMPRESSION_RATIO",
            )

    @staticmethod
    def _validate_entry_name(value: str) -> str:
        if value.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", value):
            raise TemplatePackageReader._error(
                "PACKAGE_PATH_INVALID", "絶対pathまたはdrive指定は使用できません。"
            )
        if any(part in {"", ".", ".."} for part in value.split("/")):
            raise TemplatePackageReader._error(
                "PACKAGE_PATH_INVALID", "空segmentまたは相対directory指定は使用できません。"
            )
        try:
            name = validate_package_path(value)
        except ValueError as exc:
            raise TemplatePackageReader._error(
                "PACKAGE_PATH_INVALID", "ZIP entry pathが安全な相対pathではありません。"
            ) from exc
        if any(part == ".." for part in PurePosixPath(name).parts):
            raise TemplatePackageReader._error(
                "PACKAGE_PATH_INVALID", "親directory参照は使用できません。"
            )
        return name

    @staticmethod
    def _validate_zip_info(info: zipfile.ZipInfo) -> None:
        if info.is_dir() or info.filename.endswith("/"):
            raise TemplatePackageReader._error(
                "PACKAGE_DIRECTORY_ENTRY_FORBIDDEN", "directory entryは使用できません。"
            )
        unix_mode = info.external_attr >> 16
        file_type = stat.S_IFMT(unix_mode)
        if file_type == stat.S_IFLNK:
            raise TemplatePackageReader._error(
                "PACKAGE_SYMLINK_FORBIDDEN", "symlink entryは使用できません。"
            )
        if file_type not in {0, stat.S_IFREG}:
            raise TemplatePackageReader._error(
                "PACKAGE_SPECIAL_FILE_FORBIDDEN", "特殊file entryは使用できません。"
            )
        if info.flag_bits & 0x1:
            raise TemplatePackageReader._error(
                "PACKAGE_ENCRYPTED_ENTRY", "暗号化ZIP entryは使用できません。"
            )
        if info.compress_type not in _SUPPORTED_COMPRESSION:
            raise TemplatePackageReader._error(
                "PACKAGE_COMPRESSION_UNSUPPORTED", "未対応のZIP圧縮方式です。"
            )
        if info.comment or info.extra:
            raise TemplatePackageReader._error(
                "PACKAGE_ZIP_METADATA_FORBIDDEN",
                "ZIP entry commentまたはextra fieldは使用できません。",
            )

    @staticmethod
    def _validate_ratio(
        file_size: int,
        compressed_size: int,
        maximum: float,
        code: str,
    ) -> None:
        if file_size == 0:
            return
        if compressed_size <= 0 or file_size / compressed_size > maximum:
            raise TemplatePackageReader._error(code, "圧縮率が安全上限を超えています。")

    @staticmethod
    def _read_entry(
        archive: zipfile.ZipFile,
        info: zipfile.ZipInfo,
        maximum: int,
        error_code: str,
    ) -> bytes:
        if info.file_size > maximum:
            raise TemplatePackageReader._error(error_code, "ZIP entry sizeが上限を超えています。")
        chunks: list[bytes] = []
        total = 0
        try:
            with archive.open(info, "r") as source:
                while True:
                    chunk = source.read(min(1024 * 1024, maximum - total + 1))
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > maximum:
                        raise TemplatePackageReader._error(
                            error_code, "ZIP entry sizeが上限を超えています。"
                        )
                    chunks.append(chunk)
        except (zipfile.BadZipFile, RuntimeError, EOFError, OSError) as exc:
            raise TemplatePackageReader._error(
                "PACKAGE_ENTRY_READ_FAILED", "ZIP entryを安全に読み込めません。"
            ) from exc
        content = b"".join(chunks)
        if len(content) != info.file_size:
            raise TemplatePackageReader._error(
                "PACKAGE_ENTRY_SIZE_INVALID", "ZIP headerと実データのsizeが一致しません。"
            )
        return content

    @staticmethod
    def _parse_canonical_json(content: bytes, *, path: str) -> Any:
        def pairs(values: list[tuple[str, Any]]) -> dict[str, Any]:
            result: dict[str, Any] = {}
            for key, value in values:
                if key in result:
                    raise ValueError("duplicate key")
                result[key] = value
            return result

        def reject_constant(value: str) -> None:
            raise ValueError(f"non-finite number: {value}")

        try:
            decoded = content.decode("utf-8")
            value = json.loads(
                decoded,
                object_pairs_hook=pairs,
                parse_constant=reject_constant,
            )
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            raise TemplatePackageReader._error(
                "PACKAGE_JSON_INVALID", f"JSONを厳格に解析できません: {path}"
            ) from exc
        try:
            canonical = canonical_json_bytes(value)
        except (ValueError, RecursionError) as exc:
            raise TemplatePackageReader._error(
                "PACKAGE_JSON_INVALID", f"JSONを正規化できません: {path}"
            ) from exc
        if canonical != content:
            raise TemplatePackageReader._error(
                "PACKAGE_JSON_NOT_CANONICAL", f"Canonical JSONではありません: {path}"
            )
        return value

    @staticmethod
    def _model(model: type[Any], value: Any, code: str) -> Any:
        if value is None:
            raise TemplatePackageReader._error(code, "必須Canonical documentがありません。")
        try:
            return model.model_validate(value)
        except ValueError as exc:
            raise TemplatePackageReader._error(code, "Canonical documentが不正です。") from exc

    def _entry_limit(self, item: PackageFile) -> int:
        if item.media_type == "application/json":
            return self._limits.max_json_bytes
        if item.media_type == "image/png":
            return self._limits.max_image_bytes
        if item.role == "spreadsheet-source":
            return self._limits.max_xlsx_bytes
        raise self._error(
            "PACKAGE_MEDIA_TYPE_UNSUPPORTED", f"未対応のmedia typeです: {item.media_type}"
        )

    def _validate_png(self, content: bytes, path: str) -> tuple[int, int]:
        if not content.startswith(_PNG_SIGNATURE):
            raise self._error("PACKAGE_PNG_SIGNATURE_INVALID", f"PNG signature不一致: {path}")
        try:
            with _IMAGE_LIMIT_LOCK:
                previous_limit = Image.MAX_IMAGE_PIXELS
                Image.MAX_IMAGE_PIXELS = self._limits.max_image_pixels
                try:
                    with Image.open(io.BytesIO(content)) as image:
                        if image.format != "PNG":
                            raise ValueError("not png")
                        width, height = image.size
                        self._validate_image_dimensions(width, height, path)
                        image.load()
                finally:
                    Image.MAX_IMAGE_PIXELS = previous_limit
        except TemplatePackageImportError:
            raise
        except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as exc:
            raise self._error("PACKAGE_PNG_INVALID", f"PNGをdecodeできません: {path}") from exc
        return width, height

    def _validate_image_dimensions(self, width: int, height: int, path: str) -> None:
        if (
            width <= 0
            or height <= 0
            or width > self._limits.max_image_width
            or height > self._limits.max_image_height
            or width * height > self._limits.max_image_pixels
        ):
            raise self._error(
                "PACKAGE_IMAGE_DIMENSION_LIMIT", f"画像寸法またはpixel数が上限外です: {path}"
            )

    @staticmethod
    def _validate_cross_references(
        manifest: TemplatePackageManifest,
        template: CanonicalTemplate,
        quality: CanonicalTemplateQuality,
        output_layout: CanonicalOutputLayout | None,
        payloads: dict[str, bytes],
        image_dimensions: dict[str, tuple[int, int]],
    ) -> None:
        references = {
            value
            for value in template.assets.model_dump(mode="json", by_alias=True).values()
            if isinstance(value, str)
        }
        asset_paths = {
            item.path
            for item in manifest.files
            if item.path not in {"template.json", "quality.json"}
        }
        if references != asset_paths:
            raise TemplatePackageReader._error(
                "PACKAGE_ASSET_REFERENCE_MISMATCH",
                "template asset参照とmanifest entryが一致しません。",
            )
        if (output_layout is not None) != (template.assets.output_layout is not None):
            raise TemplatePackageReader._error(
                "PACKAGE_OUTPUT_LAYOUT_REFERENCE_MISMATCH",
                "output layout参照が一致しません。",
            )
        expected_features = template.required_features()
        if manifest.required_features != expected_features:
            raise TemplatePackageReader._error(
                "PACKAGE_FEATURE_MISMATCH",
                "required featureとCanonical内容が一致しません。",
            )
        enabled_mapping_count = sum(1 for item in template.runtime.field_mappings if item.enabled)
        if quality.field_mapping_count != enabled_mapping_count:
            raise TemplatePackageReader._error(
                "PACKAGE_QUALITY_MISMATCH", "field mapping件数がCanonical内容と一致しません。"
            )
        if quality.record_structure_verified != template.layout.record_structure_verified:
            raise TemplatePackageReader._error(
                "PACKAGE_QUALITY_MISMATCH", "record structure状態が一致しません。"
            )
        if quality.definition_approved != (template.approved_definition is not None):
            raise TemplatePackageReader._error(
                "PACKAGE_QUALITY_MISMATCH", "definition承認状態が一致しません。"
            )
        if quality.definition_valid != (template.approved_definition is not None):
            raise TemplatePackageReader._error(
                "PACKAGE_QUALITY_MISMATCH", "definition検証状態が一致しません。"
            )
        if output_layout is not None:
            known_bindings = {
                item.target_path for item in template.runtime.field_mappings if item.enabled
            }
            unknown_binding = next(
                (
                    cell.binding
                    for cell in output_layout.cells
                    if cell.binding is not None and cell.binding not in known_bindings
                ),
                None,
            )
            if unknown_binding is not None:
                raise TemplatePackageReader._error(
                    "PACKAGE_OUTPUT_BINDING_UNKNOWN",
                    "output layoutが存在しないfield bindingを参照しています。",
                )
        canonical_size = (template.canonical_width, template.canonical_height)
        for path in (
            "assets/clean-template.png",
            "assets/handwriting-mask.png",
            "assets/handwriting-mask-edited.png",
        ):
            if path in image_dimensions and image_dimensions[path] != canonical_size:
                raise TemplatePackageReader._error(
                    "PACKAGE_IMAGE_DIMENSION_MISMATCH",
                    f"画像寸法がCanonical templateと一致しません: {path}",
                )
        if template.assets.clean_template not in payloads:
            raise TemplatePackageReader._error(
                "PACKAGE_CLEAN_TEMPLATE_MISSING", "clean template画像がありません。"
            )

    @staticmethod
    def _error(code: str, detail: str) -> TemplatePackageImportError:
        return TemplatePackageImportError(code, detail)


class XlsxPackageValidator:
    def __init__(self, limits: TemplatePackageSecurityLimits) -> None:
        self._limits = limits

    def validate(self, content: bytes, *, required_sheet_name: str) -> None:
        if len(content) > self._limits.max_xlsx_bytes or not content.startswith(b"PK\x03\x04"):
            raise TemplatePackageImportError(
                "PACKAGE_XLSX_INVALID", "XLSX signatureまたはsizeが不正です。"
            )
        try:
            archive = zipfile.ZipFile(io.BytesIO(content))
        except (zipfile.BadZipFile, zipfile.LargeZipFile) as exc:
            raise TemplatePackageImportError(
                "PACKAGE_XLSX_INVALID", "XLSX内部ZIPを読み込めません。"
            ) from exc
        with archive:
            infos = archive.infolist()
            if not infos or len(infos) > self._limits.max_xlsx_entry_count:
                raise TemplatePackageImportError(
                    "PACKAGE_XLSX_ENTRY_LIMIT", "XLSX内部entry数が上限外です。"
                )
            names: set[str] = set()
            collisions: set[str] = set()
            total = 0
            info_by_name: dict[str, zipfile.ZipInfo] = {}
            for info in infos:
                name = self._validate_xlsx_path(info.filename)
                collision = name.casefold()
                if name in names or collision in collisions:
                    raise TemplatePackageImportError(
                        "PACKAGE_XLSX_DUPLICATE_ENTRY", "XLSX内部entryが重複しています。"
                    )
                names.add(name)
                collisions.add(collision)
                TemplatePackageReader._validate_zip_info(info)
                if info.file_size > self._limits.max_xlsx_entry_bytes:
                    raise TemplatePackageImportError(
                        "PACKAGE_XLSX_ENTRY_LIMIT", "XLSX内部entry sizeが上限外です。"
                    )
                total += info.file_size
                if total > self._limits.max_xlsx_total_uncompressed_bytes:
                    raise TemplatePackageImportError(
                        "PACKAGE_XLSX_UNCOMPRESSED_LIMIT", "XLSX展開後総容量が上限外です。"
                    )
                TemplatePackageReader._validate_ratio(
                    info.file_size,
                    info.compress_size,
                    self._limits.max_xlsx_compression_ratio,
                    "PACKAGE_XLSX_COMPRESSION_RATIO",
                )
                info_by_name[name] = info
            if "[Content_Types].xml" not in names or "xl/workbook.xml" not in names:
                raise TemplatePackageImportError(
                    "PACKAGE_XLSX_INVALID", "XLSX必須entryがありません。"
                )
            if any(
                name in _XLSX_FORBIDDEN_NAMES
                or any(name.startswith(prefix) for prefix in _XLSX_FORBIDDEN_PREFIXES)
                for name in names
            ):
                raise TemplatePackageImportError(
                    "PACKAGE_XLSX_UNSAFE_CONTENT",
                    "XLSXに外部link、macro、埋め込みobject等が含まれています。",
                )
            for name, info in info_by_name.items():
                if not name.endswith((".xml", ".rels")):
                    continue
                raw = TemplatePackageReader._read_entry(
                    archive,
                    info,
                    self._limits.max_xlsx_entry_bytes,
                    "PACKAGE_XLSX_ENTRY_LIMIT",
                )
                if name.endswith(".rels") and _EXTERNAL_RELATIONSHIP.search(raw):
                    raise TemplatePackageImportError(
                        "PACKAGE_XLSX_EXTERNAL_RELATIONSHIP",
                        "XLSXに外部relationshipが含まれています。",
                    )
                if _EXTERNAL_FORMULA.search(raw):
                    raise TemplatePackageImportError(
                        "PACKAGE_XLSX_EXTERNAL_FORMULA",
                        "XLSXに外部参照式が含まれています。",
                    )
                if _HIDDEN_XML.search(raw):
                    raise TemplatePackageImportError(
                        "PACKAGE_XLSX_HIDDEN_CONTENT",
                        "XLSXに非表示情報が含まれています。",
                    )
            workbook_xml = TemplatePackageReader._read_entry(
                archive,
                info_by_name["xl/workbook.xml"],
                self._limits.max_xlsx_entry_bytes,
                "PACKAGE_XLSX_ENTRY_LIMIT",
            )
            sheet_names = {
                html.unescape(match.group(1).decode("utf-8"))
                for match in _SHEET_NAME.finditer(workbook_xml)
            }
            if required_sheet_name not in sheet_names:
                raise TemplatePackageImportError(
                    "PACKAGE_XLSX_SHEET_MISSING", "指定されたsheetがXLSXに存在しません。"
                )

    @staticmethod
    def _validate_xlsx_path(value: str) -> str:
        if (
            not value
            or value.startswith(("/", "\\"))
            or "\\" in value
            or ":" in value
            or re.match(r"^[A-Za-z]:", value)
            or value != value.strip()
        ):
            raise TemplatePackageImportError(
                "PACKAGE_XLSX_PATH_INVALID", "XLSX内部pathが不正です。"
            )
        if unicodedata.normalize("NFC", value) != value:
            raise TemplatePackageImportError(
                "PACKAGE_XLSX_PATH_INVALID", "XLSX内部pathはNFCである必要があります。"
            )
        path = PurePosixPath(value)
        if any(part in {"", ".", ".."} for part in value.split("/")) or any(
            part.endswith((" ", ".")) for part in path.parts
        ):
            raise TemplatePackageImportError(
                "PACKAGE_XLSX_PATH_INVALID", "XLSX内部pathが不正です。"
            )
        try:
            return validate_package_path(value)
        except ValueError as exc:
            raise TemplatePackageImportError(
                "PACKAGE_XLSX_PATH_INVALID", "XLSX内部pathが不正です。"
            ) from exc
