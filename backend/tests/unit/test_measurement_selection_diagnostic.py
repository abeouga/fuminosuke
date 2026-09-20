"""Read-only diagnostic boundary tests; no native renderer or qualification."""
import hashlib
import json
import sqlite3
from contextlib import closing
from unittest.mock import Mock

import pytest

from scripts import inspect_measurement_selection as diagnostic
from tests.unit.test_prepared_reference_geometry import sample
from tests.unit.test_renderer_glyph_bridge import reference

sample = sample
reference = reference


@pytest.fixture
def inputs(tmp_path, sample, monkeypatch):
    prepared, plan, evidence = sample
    root = tmp_path / "storage"
    root.mkdir()
    reference_dir = tmp_path / "reference"
    reference_dir.mkdir()
    plan["printFiles"] = {"xlsx": {"relativePath": "print.xlsx",
        "sha256": hashlib.sha256(prepared.workbook).hexdigest()}}
    with closing(sqlite3.connect(root / "app.db")) as db:
        db.execute("CREATE TABLE template_versions (id TEXT, status TEXT, quality_json TEXT)")
        db.execute("INSERT INTO template_versions VALUES (?, ?, ?)",
                   ("explicit-version", "DRAFT", json.dumps({"printLayout": plan})))
        db.commit()
    (root / "print.xlsx").write_bytes(prepared.workbook)
    for name, content in (("original.pdf", prepared.pdf),
                          ("renderer.json", json.dumps(prepared.report).encode()),
                          ("vectors.json", json.dumps(evidence.original_vectors).encode()),
                          ("original-1.png", prepared.images[0])):
        (reference_dir / name).write_bytes(content)
    native = Mock(side_effect=AssertionError("Read-only diagnostic must not render or copy"))
    for name in ("render_wsl_reference", "inspect_wsl_vectors", "probe_copy", "renderer_mutex"):
        monkeypatch.setattr(diagnostic, name, native)
    return root, "explicit-version", reference_dir, reference_dir / "vectors.json", native


def test_default_only_observes_owned_bytes_and_explicit_fields(inputs, capsys):
    root, version, directory, vectors, native = inputs
    before = {p: p.read_bytes() for parent in (root, directory) for p in parent.iterdir()}
    diagnostic.run(root, version, directory, vectors)
    result = json.loads(capsys.readouterr().out)
    assert result["inputsUnchanged"] is True
    assert result["selected"] == []
    assert result["productionQualified"] is False
    assert result["visibilityVerified"] is False
    assert result["original"][0]["page"] == 7
    assert result["original"][0]["unverifiedRasterSegments"] == []
    assert before == {p: p.read_bytes() for parent in (root, directory) for p in parent.iterdir()}
    native.assert_not_called()


@pytest.mark.parametrize("part", ["workbook", "image", "pdf", "vectors"])
def test_changed_input_bytes_are_not_observed_or_repaired(inputs, part):
    root, version, directory, vectors, native = inputs
    path = {"workbook": root / "print.xlsx", "image": directory / "original-1.png",
            "pdf": directory / "original.pdf", "vectors": vectors}[part]
    if part == "vectors":
        value = json.loads(path.read_bytes())
        value["codeBundleSha256"] = "0" * 64
        path.write_text(json.dumps(value))
    else:
        path.write_bytes(path.read_bytes() + b"changed")
    expected = RuntimeError if part == "vectors" else ValueError
    match = "Glyph helper identity or dependency mismatch" if part == "vectors" else None
    with pytest.raises(expected, match=match):
        diagnostic.run(root, version, directory, vectors)
    native.assert_not_called()
