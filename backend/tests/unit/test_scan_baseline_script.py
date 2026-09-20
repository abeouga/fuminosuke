import hashlib
import json
import sqlite3
import tempfile
from pathlib import Path

import pytest
from PIL import Image

from app.domain.template_config import FieldMappingConfig, load_template_config
from scripts.inspect_print_scans import inspect


@pytest.fixture
def scan_fixture(tmp_path, monkeypatch):
    root = tmp_path / "data"
    root.mkdir()
    reference, source = root / "reference.png", tmp_path / "scan.png"
    for path in (reference, source):
        Image.new("RGB", (128, 128), "white").save(path)
    sha = hashlib.sha256(reference.read_bytes()).hexdigest()
    config = load_template_config().model_copy(update={
        "canonical_width": 128, "canonical_height": 128, "regions": [], "marks": [],
        "field_mappings": [FieldMappingConfig(
            mapping_id="arbitrary-map", target_path="customFields.anyName", label="Arbitrary",
            x=20, y=20, width=40, height=40, padding=0, enabled=True,
        )],
    })
    (root / "config.json").write_text(config.model_dump_json(by_alias=True), encoding="utf-8")
    page = {
        "page": 1, "range": "A1:D4", "width": 128, "height": 128, "cells": [],
        "referenceImage": {"artifactId": "arbitrary-ref", "relativePath": "reference.png",
                           "sha256": sha},
        "fields": [{"cellId": "arbitrary-cell", "sourceRange": "B2:C3",
                    "rectangle": {"x": 20, "y": 20, "width": 40, "height": 40},
                    "bindings": [{"mappingId": "arbitrary-map", "sourceRegionId": "arbitrary-map",
                                  "targetPath": "customFields.anyName", "mappingType": "text"}]}],
    }
    with sqlite3.connect(root / "app.db") as connection:
        connection.execute("CREATE TABLE template_versions "
                           "(id TEXT,status TEXT,quality_json TEXT,layout_json TEXT,"
                           "config_relative_path TEXT)")
        connection.execute("INSERT INTO template_versions VALUES (?,?,?,?,?)", (
            "version", "DRAFT", json.dumps({"printLayout": {"pages": [page]}}), "{}", "config.json",
        ))
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    return root, source


def test_rejected_scan_keeps_originals_and_records_owned_evidence(scan_fixture):
    root, source = scan_fixture
    paths = [*root.iterdir(), source]
    before = {p: p.read_bytes() for p in paths}
    result = inspect("version", root, [(1, source)])
    assert result["originalUnchanged"] is True
    assert result["externalApiCalls"] == 0
    assert result["productionQualified"] is False
    item = result["results"][0]
    assert item["status"] == "REJECTED"
    assert item["code"] == "LOW_REGISTRATION_CONFIDENCE"
    assert item["inputSha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    folder = Path(item["outputDirectory"])
    assert "rejected.png" in item["imageSha256"]
    for name, sha in item["imageSha256"].items():
        assert hashlib.sha256((folder / name).read_bytes()).hexdigest() == sha
    assert all(p.read_bytes() == content for p, content in before.items())


@pytest.mark.parametrize("pages", [[], [2]])
def test_missing_or_unknown_explicit_binding_is_rejected(scan_fixture, pages):
    root, source = scan_fixture
    with pytest.raises(ValueError, match="explicit page binding"):
        inspect("version", root, [(page, source) for page in pages])


def test_changed_reference_is_not_used(scan_fixture):
    root, source = scan_fixture
    Image.new("RGB", (128, 128), "black").save(root / "reference.png")
    database_before = (root / "app.db").read_bytes()
    with pytest.raises(ValueError, match="artifact SHA mismatch"):
        inspect("version", root, [(1, source)])
    assert (root / "app.db").read_bytes() == database_before
