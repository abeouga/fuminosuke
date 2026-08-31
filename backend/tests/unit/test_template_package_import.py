from __future__ import annotations

import hashlib
import io
import json
import stat
import zipfile
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime

import pytest
from openpyxl import Workbook  # type: ignore[import-untyped]
from PIL import Image
from sqlalchemy import func, select

from app.core.config import Settings
from app.db.base import Base
from app.db.models import (
    JobArtifact,
    ProcessingJob,
    Template,
    TemplateCandidateSet,
    TemplatePackageIdentity,
    TemplatePackageInstallation,
    TemplateVersion,
)
from app.db.session import Database
from app.domain.enums import TemplateMode, TemplateVersionStatus
from app.domain.template_config import parse_template_config
from app.storage.local_storage import LocalStorageService
from app.template_packages.canonical_json import canonical_json_bytes
from app.template_packages.canonical_models import CanonicalTemplate, CanonicalTemplateQuality
from app.template_packages.import_models import (
    TemplateImportConflictStatus,
    TemplatePackageImportError,
)
from app.template_packages.import_service import (
    TemplateImportInspectionService,
    TemplateImportService,
)
from app.template_packages.package_reader import TemplatePackageReader
from app.template_packages.package_serializer import (
    GeneratedTemplatePackageValidator,
    TemplatePackageSerializer,
)
from app.template_packages.security_limits import (
    DEFAULT_TEMPLATE_PACKAGE_SECURITY_LIMITS,
    TemplatePackageSecurityLimits,
)
from app.template_packages.spreadsheet_sanitizer import SpreadsheetSanitizer


def _png(width: int = 300, height: int = 100) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (width, height), color=(250, 250, 250)).save(output, format="PNG")
    return output.getvalue()


def _template_payload(
    *,
    name: str = "面接カード",
    preview_reference: bool = False,
    spreadsheet: bool = False,
) -> dict[str, object]:
    assets: dict[str, object] = {"cleanTemplate": "assets/clean-template.png"}
    spreadsheet_value: dict[str, str] | None = None
    mode = TemplateMode.REFERENCE_TEMPLATE.value
    if preview_reference:
        assets["preview"] = "assets/preview.png"
    if spreadsheet:
        mode = TemplateMode.SPREADSHEET_TEMPLATE.value
        assets["spreadsheetSource"] = "optional/spreadsheet-source.xlsx"
        spreadsheet_value = {"sheetName": "帳票", "fileName": "source.xlsx"}
    return {
        "schemaVersion": 1,
        "name": name,
        "documentType": "interview-card",
        "mode": mode,
        "canonicalWidth": 300,
        "canonicalHeight": 100,
        "runtime": {
            "registration": {
                "preferredMethod": "ORB_HOMOGRAPHY",
                "minFeatureMatches": 4,
                "minInliers": 4,
                "minInlierRatio": 0.3,
                "maxReprojectionErrorPx": 8,
                "minEccScore": 0.4,
            },
            "regions": [],
            "fieldMappings": [
                {
                    "mappingId": "person-name",
                    "targetPath": "document.personName",
                    "label": "氏名",
                    "x": 100,
                    "y": 10,
                    "width": 150,
                    "height": 30,
                }
            ],
            "documentIdentityFields": [],
            "documentName": {
                "fieldPaths": ["document.personName"],
                "prefix": "面接カード_",
            },
            "marks": [],
            "export": {"csvColumns": ["document.personName"]},
        },
        "spreadsheet": spreadsheet_value,
        "layout": {
            "layoutSchemaVersion": 3,
            "canonicalWidth": 300,
            "canonicalHeight": 100,
            "cells": [
                {
                    "id": "value-name",
                    "role": "input",
                    "x": 100,
                    "y": 10,
                    "width": 150,
                    "height": 30,
                    "confidence": 1,
                }
            ],
            "relations": [
                {
                    "id": "relation-name",
                    "inputCellId": "value-name",
                    "labelCellIds": [],
                    "axis": "horizontal",
                    "order": 1,
                    "targetPath": "document.personName",
                }
            ],
            "recordStructureVerified": False,
        },
        "candidateSets": [
            {
                "id": "candidate-set-001",
                "name": "氏名候補",
                "values": ["山田太郎", "佐藤花子"],
                "targetPatterns": ["document.personName"],
            }
        ],
        "assets": assets,
    }


def _quality() -> CanonicalTemplateQuality:
    return CanonicalTemplateQuality(
        document_boundary_verified=True,
        layout_verified=True,
        layout_finalized=True,
        record_structure_verified=False,
        definition_approved=False,
        definition_valid=False,
        field_mapping_count=1,
        warnings=[],
    )


def _build_package(
    *,
    version: str = "1.0.0",
    package_id: str = "interview-card",
    name: str = "面接カード",
    clean: bytes | None = None,
    preview_reference: bool = False,
    spreadsheet_payload: bytes | None = None,
) -> bytes:
    spreadsheet = spreadsheet_payload is not None
    template = CanonicalTemplate.model_validate(
        _template_payload(
            name=name,
            preview_reference=preview_reference,
            spreadsheet=spreadsheet,
        )
    )
    assets = {"assets/clean-template.png": clean if clean is not None else _png()}
    if spreadsheet_payload is not None:
        assets["optional/spreadsheet-source.xlsx"] = spreadsheet_payload
    return TemplatePackageSerializer().build(
        canonical_template=template,
        quality=_quality(),
        output_layout=None,
        asset_payloads=assets,
        publisher_id="jp.example",
        package_id=package_id,
        release_version=version,
        exported_at=datetime(2026, 9, 1, tzinfo=UTC),
    ).content


def _unsafe_xlsx() -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", b"<Types />")
        archive.writestr(
            "xl/workbook.xml",
            (
                b'<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                b'<sheets><sheet name="\xe5\xb8\xb3\xe7\xa5\xa8" sheetId="1" /></sheets></workbook>'
            ),
        )
        archive.writestr("xl/externalLinks/externalLink1.xml", b"<externalLink />")
    return output.getvalue()


def _valid_xlsx() -> bytes:
    workbook = Workbook()
    workbook.active.title = "帳票"
    workbook.active["A1"] = "氏名"
    output = io.BytesIO()
    workbook.save(output)
    return SpreadsheetSanitizer().sanitize(
        output.getvalue(),
        source_sheet_name="帳票",
    ).content


def _zip_entries(content: bytes) -> list[tuple[zipfile.ZipInfo, bytes]]:
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        return [(info, archive.read(info)) for info in archive.infolist()]


def _write_entries(entries: list[tuple[zipfile.ZipInfo, bytes]]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for original, payload in entries:
            info = zipfile.ZipInfo(original.filename, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = original.compress_type
            info.create_system = original.create_system
            info.external_attr = original.external_attr
            info.flag_bits = original.flag_bits
            archive.writestr(info, payload)
    return output.getvalue()


def _with_extra_entry(
    content: bytes,
    name: str,
    payload: bytes = b"extra",
    *,
    external_attr: int = 0o100644 << 16,
) -> bytes:
    entries = _zip_entries(content)
    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3
    info.external_attr = external_attr
    entries.append((info, payload))
    return _write_entries(entries)


def _replace_entry(content: bytes, name: str, payload: bytes) -> bytes:
    return _write_entries(
        [
            (info, payload if info.filename == name else value)
            for info, value in _zip_entries(content)
        ]
    )


def _mutate_manifest(
    content: bytes,
    mutate: Callable[[dict[str, object]], None],
) -> bytes:
    entries = _zip_entries(content)
    manifest = json.loads(
        next(value for info, value in entries if info.filename == "manifest.json")
    )
    mutate(manifest)
    return _write_entries(
        [
            (info, canonical_json_bytes(manifest) if info.filename == "manifest.json" else value)
            for info, value in entries
        ]
    )


def _replace_payload_and_manifest_hash(content: bytes, name: str, payload: bytes) -> bytes:
    changed = _replace_entry(content, name, payload)

    def update(manifest: dict[str, object]) -> None:
        files = manifest["files"]
        assert isinstance(files, list)
        item = next(item for item in files if isinstance(item, dict) and item.get("path") == name)
        item["size"] = len(payload)
        item["sha256"] = hashlib.sha256(payload).hexdigest()

    return _mutate_manifest(changed, update)


def _mark_encrypted(content: bytes) -> bytes:
    data = bytearray(content)
    for signature, flag_offset in ((b"PK\x03\x04", 6), (b"PK\x01\x02", 8)):
        start = 0
        while True:
            index = data.find(signature, start)
            if index < 0:
                break
            flags = int.from_bytes(data[index + flag_offset : index + flag_offset + 2], "little")
            data[index + flag_offset : index + flag_offset + 2] = (flags | 1).to_bytes(2, "little")
            start = index + 4
    return bytes(data)


def _services(settings: Settings) -> tuple[Database, LocalStorageService]:
    database = Database(settings.database)
    Base.metadata.create_all(database.engine)
    storage = LocalStorageService(settings.storage.data_dir)
    storage.initialize()
    return database, storage


def test_reader_validates_generated_package_without_generated_validator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    content = _build_package()

    def forbidden_call(_self: object, _content: bytes) -> object:
        raise AssertionError("Generated validator must not read untrusted input")

    monkeypatch.setattr(GeneratedTemplatePackageValidator, "validate", forbidden_call)
    package = TemplatePackageReader().read(content)

    assert package.template.name == "面接カード"
    assert package.manifest.version == "1.0.0"
    assert package.image_dimensions["assets/clean-template.png"] == (300, 100)


@pytest.mark.parametrize(
    ("mutate", "expected_code"),
    [
        (lambda package: _with_extra_entry(package, "../escape.txt"), "PACKAGE_PATH_INVALID"),
        (lambda package: _with_extra_entry(package, "C:/escape.txt"), "PACKAGE_PATH_INVALID"),
        (lambda package: _with_extra_entry(package, "safe:stream"), "PACKAGE_PATH_INVALID"),
        (lambda package: _with_extra_entry(package, "CON.txt"), "PACKAGE_PATH_INVALID"),
        (lambda package: _with_extra_entry(package, "name. "), "PACKAGE_PATH_INVALID"),
        (lambda package: _with_extra_entry(package, "a//b.txt"), "PACKAGE_PATH_INVALID"),
        (lambda package: _with_extra_entry(package, "e\u0301.json"), "PACKAGE_PATH_INVALID"),
        (lambda package: _with_extra_entry(package, "Template.json"), "PACKAGE_PATH_COLLISION"),
        (
            lambda package: _with_extra_entry(
                package,
                "link",
                external_attr=(stat.S_IFLNK | 0o777) << 16,
            ),
            "PACKAGE_SYMLINK_FORBIDDEN",
        ),
        (lambda package: _mark_encrypted(package), "PACKAGE_ENCRYPTED_ENTRY"),
        (lambda package: _with_extra_entry(package, "extra.txt"), "PACKAGE_UNDECLARED_ENTRY"),
    ],
)
def test_attack_paths_and_zip_metadata_are_rejected(
    mutate: Callable[[bytes], bytes],
    expected_code: str,
) -> None:
    with pytest.raises(TemplatePackageImportError) as caught:
        TemplatePackageReader().read(mutate(_build_package()))
    assert caught.value.code == expected_code


def test_duplicate_entry_is_rejected() -> None:
    entries = _zip_entries(_build_package())
    entries.append(entries[0])
    with pytest.warns(UserWarning), pytest.raises(TemplatePackageImportError) as caught:
        package = _write_entries(entries)
        TemplatePackageReader().read(package)
    assert caught.value.code == "PACKAGE_DUPLICATE_ENTRY"


def test_directory_and_unsupported_compression_are_rejected() -> None:
    with pytest.raises(TemplatePackageImportError) as caught:
        TemplatePackageReader().read(_with_extra_entry(_build_package(), "folder/"))
    assert caught.value.code == "PACKAGE_PATH_INVALID"

    entries = _zip_entries(_build_package())
    info = zipfile.ZipInfo("unsupported.bin", date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_BZIP2
    info.create_system = 3
    info.external_attr = 0o100644 << 16
    entries.append((info, b"unsupported"))
    with pytest.raises(TemplatePackageImportError) as caught:
        TemplatePackageReader().read(_write_entries(entries))
    assert caught.value.code == "PACKAGE_COMPRESSION_UNSUPPORTED"


@pytest.mark.parametrize(
    ("limits", "expected_code"),
    [
        (
            replace(DEFAULT_TEMPLATE_PACKAGE_SECURITY_LIMITS, max_package_bytes=100),
            "PACKAGE_TOO_LARGE",
        ),
        (
            replace(DEFAULT_TEMPLATE_PACKAGE_SECURITY_LIMITS, max_entry_count=2),
            "PACKAGE_ENTRY_LIMIT",
        ),
        (
            replace(DEFAULT_TEMPLATE_PACKAGE_SECURITY_LIMITS, max_compression_ratio=1.0),
            "PACKAGE_COMPRESSION_RATIO",
        ),
        (
            replace(
                DEFAULT_TEMPLATE_PACKAGE_SECURITY_LIMITS,
                max_total_uncompressed_bytes=100,
            ),
            "PACKAGE_UNCOMPRESSED_LIMIT",
        ),
        (
            replace(DEFAULT_TEMPLATE_PACKAGE_SECURITY_LIMITS, max_image_width=100),
            "PACKAGE_IMAGE_DIMENSION_LIMIT",
        ),
    ],
)
def test_zip_bomb_limits_are_enforced(
    limits: TemplatePackageSecurityLimits,
    expected_code: str,
) -> None:
    with pytest.raises(TemplatePackageImportError) as caught:
        TemplatePackageReader(limits).read(_build_package())
    assert caught.value.code == expected_code


def test_hash_size_and_mime_mismatches_are_rejected() -> None:
    package = _build_package()
    clean = next(
        value
        for info, value in _zip_entries(package)
        if info.filename == "assets/clean-template.png"
    )
    changed = bytes([clean[0] ^ 0xFF]) + clean[1:]
    hash_mismatch = _replace_entry(package, "assets/clean-template.png", changed)
    with pytest.raises(TemplatePackageImportError) as caught:
        TemplatePackageReader().read(hash_mismatch)
    assert caught.value.code == "PACKAGE_HASH_MISMATCH"

    def change_size(manifest: dict[str, object]) -> None:
        files = manifest["files"]
        assert isinstance(files, list)
        item = next(
            item
            for item in files
            if isinstance(item, dict) and item.get("path") == "assets/clean-template.png"
        )
        item["size"] = 1

    size_mismatch = _mutate_manifest(package, change_size)
    with pytest.raises(TemplatePackageImportError) as caught:
        TemplatePackageReader().read(size_mismatch)
    assert caught.value.code == "PACKAGE_SIZE_MISMATCH"

    def change_mime(manifest: dict[str, object]) -> None:
        files = manifest["files"]
        assert isinstance(files, list)
        item = next(
            item
            for item in files
            if isinstance(item, dict) and item.get("path") == "assets/clean-template.png"
        )
        item["mediaType"] = "application/octet-stream"

    with pytest.raises(TemplatePackageImportError) as caught:
        TemplatePackageReader().read(_mutate_manifest(package, change_mime))
    assert caught.value.code == "PACKAGE_MANIFEST_INVALID"


def test_corrupt_json_png_xlsx_and_broken_reference_are_rejected() -> None:
    invalid_json = _replace_payload_and_manifest_hash(_build_package(), "template.json", b"{")
    with pytest.raises(TemplatePackageImportError) as caught:
        TemplatePackageReader().read(invalid_json)
    assert caught.value.code == "PACKAGE_JSON_INVALID"

    with pytest.raises(TemplatePackageImportError) as caught:
        TemplatePackageReader().read(_build_package(clean=b"not-a-png"))
    assert caught.value.code == "PACKAGE_PNG_SIGNATURE_INVALID"

    with pytest.raises(TemplatePackageImportError) as caught:
        TemplatePackageReader().read(_build_package(spreadsheet_payload=b"PK\x03\x04broken"))
    assert caught.value.code == "PACKAGE_XLSX_INVALID"

    with pytest.raises(TemplatePackageImportError) as caught:
        TemplatePackageReader().read(_build_package(spreadsheet_payload=_unsafe_xlsx()))
    assert caught.value.code == "PACKAGE_XLSX_UNSAFE_CONTENT"

    with pytest.raises(TemplatePackageImportError) as caught:
        TemplatePackageReader().read(_build_package(clean=_png(10, 10)))
    assert caught.value.code == "PACKAGE_IMAGE_DIMENSION_MISMATCH"

    with pytest.raises(TemplatePackageImportError) as caught:
        TemplatePackageReader().read(_build_package(preview_reference=True))
    assert caught.value.code == "PACKAGE_ASSET_REFERENCE_MISMATCH"


def test_manifest_compatibility_and_canonical_encoding_are_enforced() -> None:
    package = _build_package()

    def future_feature(manifest: dict[str, object]) -> None:
        features = manifest["requiredFeatures"]
        assert isinstance(features, list)
        features.append("future-feature-v1")
        features.sort()

    with pytest.raises(TemplatePackageImportError) as caught:
        TemplatePackageReader().read(_mutate_manifest(package, future_feature))
    assert caught.value.code == "PACKAGE_REQUIRED_FEATURE_UNSUPPORTED"

    with pytest.raises(TemplatePackageImportError) as caught:
        TemplatePackageReader().read(
            _mutate_manifest(
                package,
                lambda manifest: manifest.update({"minimumAppVersion": "99.0.0"}),
            )
        )
    assert caught.value.code == "PACKAGE_APP_VERSION_UNSUPPORTED"

    entries = _zip_entries(package)
    manifest = json.loads(
        next(value for info, value in entries if info.filename == "manifest.json")
    )
    pretty = json.dumps(manifest, ensure_ascii=False, indent=2).encode()
    noncanonical = _write_entries(
        [(info, pretty if info.filename == "manifest.json" else value) for info, value in entries]
    )
    with pytest.raises(TemplatePackageImportError) as caught:
        TemplatePackageReader().read(noncanonical)
    assert caught.value.code == "PACKAGE_JSON_NOT_CANONICAL"

    without_manifest = _write_entries(
        [(info, value) for info, value in entries if info.filename != "manifest.json"]
    )
    with pytest.raises(TemplatePackageImportError) as caught:
        TemplatePackageReader().read(without_manifest)
    assert caught.value.code == "PACKAGE_MANIFEST_MISSING"


def test_spreadsheet_package_import_restores_runtime_source(
    settings_factory: Callable[..., Settings],
) -> None:
    database, storage = _services(settings_factory())
    package = _build_package(spreadsheet_payload=_valid_xlsx())
    try:
        inspection = TemplateImportInspectionService(database).inspect(package)
        assert inspection.spreadsheet_source_included is True
        result = TemplateImportService(database, storage).import_package(
            package,
            expected_package_sha256=inspection.package_sha256,
        )
        with database.session() as session:
            version = session.get(TemplateVersion, result.local_template_version_id)
            assert version is not None
            assert version.mode == TemplateMode.SPREADSHEET_TEMPLATE.value
            source = session.scalar(
                select(JobArtifact).where(
                    JobArtifact.job_id == result.local_job_id,
                    JobArtifact.artifact_name == "sourceWorkbook",
                )
            )
            assert source is not None and storage.exists(source.relative_path)
            assert version.quality_json["sourceSheetName"] == "帳票"
    finally:
        database.dispose()


def test_import_maps_to_local_ids_and_is_idempotent(
    settings_factory: Callable[..., Settings],
) -> None:
    database, storage = _services(settings_factory())
    package = _build_package()
    try:
        inspection = TemplateImportInspectionService(database).inspect(package)
        assert inspection.conflict_status is TemplateImportConflictStatus.NEW_IDENTITY
        service = TemplateImportService(database, storage)
        with pytest.raises(TemplatePackageImportError) as caught:
            service.import_package(
                package,
                expected_package_sha256="0" * 64,
            )
        assert caught.value.code == "IMPORT_PACKAGE_CHANGED"
        first = service.import_package(
            package,
            expected_package_sha256=inspection.package_sha256,
        )
        second = service.import_package(
            package,
            expected_package_sha256=inspection.package_sha256,
        )

        assert first.created is True
        assert second.created is False
        assert second.installation_id == first.installation_id
        assert first.active_version_changed is False
        with database.session() as session:
            template = session.get(Template, first.local_template_id)
            version = session.get(TemplateVersion, first.local_template_version_id)
            installation = session.get(TemplatePackageInstallation, first.installation_id)
            assert template is not None and template.active_version_id is None
            assert version is not None and version.status == TemplateVersionStatus.DRAFT.value
            assert installation is not None
            assert installation.trust_status == "UNVERIFIED"
            assert installation.local_mapping_json["candidateMaterialization"] == (
                "DEFERRED_UNTIL_ACTIVATION"
            )
            assert session.scalar(select(func.count()).select_from(TemplateCandidateSet)) == 0
            assert (
                session.scalar(select(func.count()).select_from(TemplatePackageInstallation))
                == 1
            )
            config = parse_template_config(
                storage.read_bytes(version.config_relative_path).decode()
            )
            assert config.template_id == template.id
            assert config.template_version == version.version_number
            assert config.name == "面接カード"
            assert version.clean_template_artifact_id is not None
            artifact = session.get(JobArtifact, version.clean_template_artifact_id)
            assert artifact is not None and storage.exists(artifact.relative_path)
    finally:
        database.dispose()


def test_conflict_rules_and_active_version_are_preserved(
    settings_factory: Callable[..., Settings],
) -> None:
    database, storage = _services(settings_factory())
    try:
        service = TemplateImportService(database, storage)
        inspection_service = TemplateImportInspectionService(database)
        v1 = _build_package(version="1.0.0")
        first = service.import_package(
            v1,
            expected_package_sha256=inspection_service.inspect(v1).package_sha256,
        )
        with database.session() as session:
            template = session.get(Template, first.local_template_id)
            version = session.get(TemplateVersion, first.local_template_version_id)
            assert template is not None and version is not None
            template.active_version_id = version.id
            version.status = TemplateVersionStatus.ACTIVE.value
            session.commit()

        v2 = _build_package(version="2.0.0")
        assert inspection_service.inspect(v2).conflict_status is (
            TemplateImportConflictStatus.NEWER_RELEASE
        )
        second = service.import_package(
            v2,
            expected_package_sha256=inspection_service.inspect(v2).package_sha256,
        )
        assert second.local_template_id == first.local_template_id
        with database.session() as session:
            template = session.get(Template, first.local_template_id)
            assert template is not None
            assert template.active_version_id == first.local_template_version_id

        conflict = _build_package(version="2.0.0", name="内容変更")
        assert inspection_service.inspect(conflict).conflict_status is (
            TemplateImportConflictStatus.VERSION_CONFLICT
        )
        with pytest.raises(TemplatePackageImportError) as caught:
            service.import_package(
                conflict,
                expected_package_sha256=inspection_service.inspect(conflict).package_sha256,
            )
        assert caught.value.code == "IMPORT_VERSION_CONFLICT"

        older = _build_package(version="0.9.0")
        with pytest.raises(TemplatePackageImportError) as caught:
            service.import_package(
                older,
                expected_package_sha256=inspection_service.inspect(older).package_sha256,
            )
        assert caught.value.code == "IMPORT_OLDER_VERSION_CONFIRMATION_REQUIRED"
        imported_older = service.import_package(
            older,
            expected_package_sha256=inspection_service.inspect(older).package_sha256,
            allow_older_version=True,
        )
        assert imported_older.created is True

        different_identity = _build_package(package_id="another-card", name="面接カード")
        separate = service.import_package(
            different_identity,
            expected_package_sha256=(
                inspection_service.inspect(different_identity).package_sha256
            ),
        )
        assert separate.local_template_id != first.local_template_id
    finally:
        database.dispose()


@pytest.mark.parametrize(
    "failure_stage",
    ["after_db_flush", "after_asset_move", "before_db_commit"],
)
def test_failure_injection_rolls_back_database_and_assets(
    settings_factory: Callable[..., Settings],
    failure_stage: str,
) -> None:
    database, storage = _services(settings_factory())
    package = _build_package()
    inspection = TemplateImportInspectionService(database).inspect(package)

    def inject(stage: str) -> None:
        if stage == failure_stage:
            raise RuntimeError(f"injected failure: {stage}")

    try:
        with pytest.raises(RuntimeError, match="injected failure"):
            TemplateImportService(database, storage).import_package(
                package,
                expected_package_sha256=inspection.package_sha256,
                failure_injector=inject,
            )
        with database.session() as session:
            for model in (
                TemplatePackageInstallation,
                TemplatePackageIdentity,
                TemplateVersion,
                Template,
                ProcessingJob,
                JobArtifact,
            ):
                assert session.scalar(select(func.count()).select_from(model)) == 0
        for directory in ("artifacts", "templates", "tmp"):
            assert not any(path.is_file() for path in storage.resolve(directory).rglob("*"))
    finally:
        database.dispose()
