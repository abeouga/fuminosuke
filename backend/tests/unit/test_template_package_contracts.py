from __future__ import annotations

import json
import tomllib
import unicodedata
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.domain.enums import TemplateMode
from app.template_packages.canonical_json import CanonicalJsonError, canonical_json_bytes
from app.template_packages.canonical_models import (
    CanonicalOutputLayout,
    CanonicalTemplate,
    CanonicalTemplateQuality,
)
from app.template_packages.content_digest import build_content_digest
from app.template_packages.feature_registry import (
    APPLICATION_VERSION,
    TemplatePackageFeature,
    evaluate_compatibility,
)
from app.template_packages.manifest_models import TemplatePackageManifest
from app.template_packages.package_paths import validate_package_path
from app.template_packages.schema_export import schemas_are_current
from app.template_packages.semantic_version import (
    SemanticVersion,
    compare_semantic_versions,
)


def _generic_template_payload() -> dict[str, object]:
    return {
        "schemaVersion": 1,
        "name": "面接カード",
        "documentType": "interview-card",
        "mode": TemplateMode.REFERENCE_TEMPLATE.value,
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
                    "mappingId": "person_name",
                    "targetPath": "document.personName",
                    "label": "氏名",
                    "x": 100,
                    "y": 10,
                    "width": 150,
                    "height": 30,
                }
            ],
            "documentIdentityFields": [
                {
                    "mappingId": "person_name",
                    "targetPath": "document.personName",
                    "label": "氏名",
                    "matchRole": "primary",
                    "displayRole": "title",
                }
            ],
            "documentName": {
                "fieldPaths": ["document.personName"],
                "separator": "_",
                "prefix": "面接カード_",
                "suffix": "",
            },
            "marks": [],
            "export": {"csvColumns": ["document.personName"]},
        },
        "layout": {
            "layoutSchemaVersion": 3,
            "canonicalWidth": 300,
            "canonicalHeight": 100,
            "cells": [
                {
                    "id": "label-name",
                    "role": "label",
                    "x": 10,
                    "y": 10,
                    "width": 80,
                    "height": 30,
                    "confidence": 1,
                    "text": "氏名",
                },
                {
                    "id": "value-name",
                    "role": "input",
                    "x": 100,
                    "y": 10,
                    "width": 150,
                    "height": 30,
                    "confidence": 1,
                },
            ],
            "relations": [
                {
                    "id": "relation-name",
                    "inputCellId": "value-name",
                    "labelCellIds": ["label-name"],
                    "axis": "horizontal",
                    "order": 1,
                    "targetPath": "document.personName",
                }
            ],
            "recordStructureVerified": False,
        },
        "approvedDefinition": {
            "schemaVersion": "1.0",
            "canonicalWidth": 300,
            "canonicalHeight": 100,
            "cells": [
                {
                    "id": "label-name",
                    "type": "LABEL",
                    "text": "氏名",
                    "bbox": {"x": 10, "y": 10, "width": 80, "height": 30},
                    "detectionConfidence": 1,
                },
                {
                    "id": "value-name",
                    "type": "VALUE",
                    "bbox": {"x": 100, "y": 10, "width": 150, "height": 30},
                    "detectionConfidence": 1,
                },
            ],
            "geometry": [
                {
                    "cellId": "label-name",
                    "rowCluster": 0,
                    "columnCluster": 0,
                    "normalizedBbox": {
                        "left": 33,
                        "top": 100,
                        "right": 300,
                        "bottom": 400,
                    },
                },
                {
                    "cellId": "value-name",
                    "rowCluster": 0,
                    "columnCluster": 1,
                    "normalizedBbox": {
                        "left": 333,
                        "top": 100,
                        "right": 833,
                        "bottom": 400,
                    },
                },
            ],
            "standaloneFields": [
                {
                    "id": "field-name",
                    "key": "personName",
                    "name": "氏名",
                    "labelCellId": "label-name",
                    "valueCellId": "value-name",
                    "confidence": 1,
                }
            ],
            "unassignedCellIds": [],
            "confidence": 1,
        },
        "candidateSets": [
            {
                "id": "candidate.person-name",
                "name": "氏名候補",
                "values": ["山田太郎", "佐藤花子"],
                "targetPatterns": ["document.personName"],
            }
        ],
        "assets": {
            "cleanTemplate": "assets/clean-template.png",
            "handwritingMask": "assets/handwriting-mask.png",
            "preview": "assets/preview.png",
        },
    }


def _manifest_payload() -> dict[str, object]:
    return {
        "format": "llm-ocr-template",
        "formatVersion": 1,
        "schemaVersion": 1,
        "publisherId": "jp.example",
        "packageId": "interview-card",
        "name": "面接カード",
        "version": "1.2.0",
        "minimumAppVersion": "0.1.0",
        "requiredFeatures": [
            "field-mappings-v1",
            "template-package-core-v1",
        ],
        "optionalFeatures": [],
        "exportedAt": "2026-09-01T12:00:00Z",
        "contentDigest": f"sha256:{'0' * 64}",
        "files": [
            {
                "path": "assets/clean-template.png",
                "role": "clean-template",
                "mediaType": "image/png",
                "size": 100,
                "sha256": "1" * 64,
            },
            {
                "path": "quality.json",
                "role": "quality",
                "mediaType": "application/json",
                "size": 100,
                "sha256": "2" * 64,
            },
            {
                "path": "template.json",
                "role": "template",
                "mediaType": "application/json",
                "size": 100,
                "sha256": "3" * 64,
            },
        ],
    }


def test_generic_canonical_template_has_no_local_ids_and_derives_features() -> None:
    template = CanonicalTemplate.model_validate(_generic_template_payload())

    serialized = template.model_dump(mode="json", by_alias=True)
    serialized_text = json.dumps(serialized, ensure_ascii=False)
    assert "templateId" not in serialized_text
    assert "templateVersionId" not in serialized_text
    assert template.required_features() == sorted(
        [
            TemplatePackageFeature.CANDIDATE_SETS,
            TemplatePackageFeature.COMPOSITE_DOCUMENT_DETECTION,
            TemplatePackageFeature.CORE,
            TemplatePackageFeature.DOCUMENT_NAME,
            TemplatePackageFeature.FIELD_MAPPINGS,
            TemplatePackageFeature.LAYOUT,
            TemplatePackageFeature.TEMPLATE_DEFINITION,
        ]
    )


def test_canonical_template_rejects_local_model_fields() -> None:
    payload = _generic_template_payload()
    payload["templateId"] = "local-database-id"

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        CanonicalTemplate.model_validate(payload)


def test_package_v1_cannot_silently_drop_virtual_heading_page_ownership() -> None:
    payload = _generic_template_payload()
    layout = payload["layout"]
    assert isinstance(layout, dict)
    heading = {"id": "virtual", "role": "parent_label", "source": "manual", "virtual": True,
               "text": "分類", "confidence": 1, "x": 1, "y": 1, "width": 30, "height": 20}
    layout["cells"].append(heading)
    # Legacy virtual headings remain portable; only page-owned nodes are unsupported.
    CanonicalTemplate.model_validate(payload)
    heading["pageRange"] = "A1:D31"
    with pytest.raises(ValidationError, match="Page virtual headings require printLayout"):
        CanonicalTemplate.model_validate(payload)


def test_spreadsheet_template_requires_a_sanitized_source_workbook() -> None:
    payload = _generic_template_payload()
    payload["mode"] = TemplateMode.SPREADSHEET_TEMPLATE.value

    with pytest.raises(ValidationError, match="sanitized source workbook"):
        CanonicalTemplate.model_validate(payload)

    assets = payload["assets"]
    assert isinstance(assets, dict)
    assets["spreadsheetSource"] = "optional/spreadsheet-source.xlsx"
    payload["spreadsheet"] = {
        "sheetName": "面接カード",
        "fileName": "面接カード.xlsx",
    }
    template = CanonicalTemplate.model_validate(payload)
    assert TemplatePackageFeature.SPREADSHEET_TEMPLATE in template.required_features()
    assert TemplatePackageFeature.XLSX_LAYOUT in template.required_features()


def test_candidate_binding_must_reference_an_enabled_text_mapping() -> None:
    payload = _generic_template_payload()
    candidate_sets = payload["candidateSets"]
    assert isinstance(candidate_sets, list)
    assert isinstance(candidate_sets[0], dict)
    candidate_sets[0]["targetPatterns"] = ["document.unknown"]

    with pytest.raises(ValidationError, match="references unknown target"):
        CanonicalTemplate.model_validate(payload)


def test_quality_contract_rejects_an_invalid_approved_definition() -> None:
    with pytest.raises(ValidationError, match="approved definition must also be valid"):
        CanonicalTemplateQuality(
            document_boundary_verified=True,
            layout_verified=True,
            layout_finalized=True,
            record_structure_verified=False,
            definition_approved=True,
            definition_valid=False,
            field_mapping_count=1,
        )


def test_manifest_accepts_sorted_complete_contract() -> None:
    manifest = TemplatePackageManifest.model_validate_json(
        json.dumps(_manifest_payload(), ensure_ascii=False)
    )

    assert manifest.publisher_id == "jp.example"
    assert manifest.exported_at == datetime(2026, 9, 1, 12, tzinfo=UTC)


def test_manifest_rejects_unsorted_or_mismatched_files() -> None:
    payload = _manifest_payload()
    files = payload["files"]
    assert isinstance(files, list)
    files.reverse()
    with pytest.raises(ValidationError, match="sorted by path"):
        TemplatePackageManifest.model_validate_json(json.dumps(payload))

    payload = _manifest_payload()
    files = payload["files"]
    assert isinstance(files, list)
    assert isinstance(files[0], dict)
    files[0]["mediaType"] = "application/octet-stream"
    with pytest.raises(ValidationError, match="must use media type"):
        TemplatePackageManifest.model_validate_json(json.dumps(payload))


def test_compatibility_requires_app_version_and_all_required_features() -> None:
    compatible = evaluate_compatibility(
        minimum_application_version="0.1.0",
        required_features=[TemplatePackageFeature.CORE],
    )
    incompatible = evaluate_compatibility(
        minimum_application_version="0.2.0",
        required_features=[TemplatePackageFeature.CORE, "future-capability-v1"],
    )

    assert compatible.compatible is True
    assert incompatible.compatible is False
    assert incompatible.unsupported_required_features == ("future-capability-v1",)


def test_compatibility_version_matches_backend_and_frontend_release_versions() -> None:
    backend_root = Path(__file__).resolve().parents[2]
    repository_root = backend_root.parent
    backend_project = tomllib.loads((backend_root / "pyproject.toml").read_text(encoding="utf-8"))
    frontend_project = json.loads(
        (repository_root / "frontend" / "package.json").read_text(encoding="utf-8")
    )

    assert backend_project["project"]["version"] == APPLICATION_VERSION
    assert frontend_project["version"] == APPLICATION_VERSION


def test_semantic_version_uses_semver_prerelease_precedence() -> None:
    ordered = [
        "1.0.0-alpha",
        "1.0.0-alpha.1",
        "1.0.0-alpha.beta",
        "1.0.0-beta",
        "1.0.0-beta.2",
        "1.0.0-beta.11",
        "1.0.0-rc.1",
        "1.0.0",
    ]

    assert [str(SemanticVersion.parse(value)) for value in ordered] == ordered
    assert all(compare_semantic_versions(left, right) < 0 for left, right in pairwise(ordered))
    assert compare_semantic_versions("1.0.0+build.1", "1.0.0+build.2") == 0
    assert hash(SemanticVersion.parse("1.0.0+build.1")) == hash(
        SemanticVersion.parse("1.0.0+build.2")
    )
    with pytest.raises(ValueError, match="Invalid semantic version"):
        SemanticVersion.parse("01.0.0")


def test_canonical_json_is_order_and_unicode_stable() -> None:
    nfc = "氏名é"
    nfd = unicodedata.normalize("NFD", nfc)
    left = {"b": [2, 1], "a": nfc}
    right = {"a": nfd, "b": [2, 1]}

    assert canonical_json_bytes(left) == canonical_json_bytes(right)
    assert canonical_json_bytes({"negativeZero": -0.0}) == canonical_json_bytes(
        {"negativeZero": 0.0}
    )
    with pytest.raises(CanonicalJsonError, match="Duplicate object key"):
        canonical_json_bytes({nfc: 1, nfd: 2})


def test_content_digest_ignores_mapping_order_but_changes_with_asset_content() -> None:
    documents = {
        "quality.json": {"schemaVersion": 1},
        "template.json": {"schemaVersion": 1, "name": "面接カード"},
    }
    assets = {"assets/clean-template.png": "a" * 64}

    first = build_content_digest(documents=documents, asset_sha256=assets)
    second = build_content_digest(
        documents=dict(reversed(list(documents.items()))),
        asset_sha256=assets,
    )
    changed = build_content_digest(
        documents=documents,
        asset_sha256={"assets/clean-template.png": "b" * 64},
    )

    assert first == second
    assert first != changed


def test_output_layout_replaces_local_template_note_with_explicit_binding() -> None:
    layout = CanonicalOutputLayout(
        sheet_name="帳票レイアウト",
        row_count=2,
        column_count=2,
        cells=[
            {
                "row": 0,
                "column": 0,
                "value": {"stringValue": "氏名"},
                "binding": "document.personName",
            }
        ],
    )
    assert layout.cells[0].binding == "document.personName"

    with pytest.raises(ValidationError, match="internal LLM OCR references"):
        CanonicalOutputLayout(
            sheet_name="帳票レイアウト",
            row_count=1,
            column_count=1,
            cells=[
                {
                    "row": 0,
                    "column": 0,
                    "note": "llm-ocr:template-version:local-id",
                }
            ],
        )


@pytest.mark.parametrize(
    "path",
    [
        "../template.json",
        "C:/template.json",
        "assets\\preview.png",
        "assets/CON.png",
        "assets/preview.png.",
    ],
)
def test_package_paths_reject_non_portable_values(path: str) -> None:
    with pytest.raises(ValueError):
        validate_package_path(path)


def test_committed_json_schemas_match_the_pydantic_contracts() -> None:
    assert schemas_are_current() is True
