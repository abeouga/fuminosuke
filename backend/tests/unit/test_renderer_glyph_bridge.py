import hashlib
import io
from copy import deepcopy
from zipfile import ZipFile

import pytest

from app.services import renderer_glyph_bridge as bridge
from app.services import renderer_glyph_worker as worker
from tests.bootstrap_python_fixture import bootstrap_python_evidence
from tests.core_guard_fixture import core_guard_evidence
from tests.wsl_execution_fixture import unit_control_evidence


@pytest.fixture
def reference():
    pdf = b"Input fixture: never parsed by these bridge tests"
    return pdf, {
        "policy": "wsl-bwrap-cgroup-v1",
        "status": "PASS",
        "format": "reference",
        "fontSource": "windows",
        "fontRequestPolicy": "wsl-fontconfig-face-v2",
        "sourceSha256": "a" * 64,
        "pdfSha256": hashlib.sha256(pdf).hexdigest(),
        "pageCount": 1,
        "runtimeImage": {
            "policy": "wsl-squashfs-runtime-v1",
            "sha256": "b" * 64,
            "filesystem": "squashfs",
            "mountsReadOnly": True,
            "kernel": "fixture",
        },
        "fontDirectoryIdentity": {
            "policy": "wsl-font-directory-v1",
            "sha256": "c" * 64,
            "fileCount": 1,
            "totalBytes": 100,
        },
        "workerIdentity": {"policy": "wsl-worker-source-v1", "sha256": "d" * 64},
        "resolvedFonts": [
            {"file": "/reference-fonts/Arbitrary.ttc", "collectionIndex": "2", "sha256": "e" * 64}
        ],
    }


def evidence(manifest):
    return {
        "unitControls": unit_control_evidence(),
       "sourceFaceBasis": "pinned-font-directory-and-embedded-glyph-match-v1",
        "coreDumpGuard": core_guard_evidence(),
        "brokerCoreDumpGuard": core_guard_evidence(),
        "bootstrapPythonIdentity": bootstrap_python_evidence(),
        "sourceFaces": [{**manifest["resolvedFonts"][0], "sourcePostScriptName": "Arbitrary"}],
        "allMatchedFacesDeclared": True,
        "policy": bridge.POLICY,
        "status": "PASS",
        "codeBundleSha256": "1" * 64,
        "inputSha256": "2" * 64,
        "manifestSha256": hashlib.sha256(bridge.canonical_bytes(manifest)).hexdigest(),
        "runtimeImage": deepcopy(manifest["runtimeImage"]),
        "fontDirectoryIdentity": deepcopy(manifest["fontDirectoryIdentity"]),
        "dependencies": {"fonttools": "4.65.0", "pypdf": "6.10.0", "typing_extensions": "4.15.0"},
        "productionQualified": False,
        "uid": 65534,
        "networkInterfaces": ["lo"],
        "networkDenied": True,
        "noNewPrivileges": True,
        "hostFilesystemHidden": True,
        "resourceLimits": {
            "memory.max": "1073741824",
            "memory.swap.max": "0",
            "pids.max": "64",
            "cpu.max": "100000 100000",
        },
        "glyphs": {
            "policy": "wsl-pdf-used-glyphs-v1",
            "pdfSha256": manifest["pdfSha256"],
            "pageCount": 1,
            "productionQualified": False,
            "visibilityAndLayoutVerified": False,
            "textInstructionGlyphs": 1,
            "distinctFontCodes": 1,
            "pages": [
                {
                    "renderedPage": 1,
                    "textInstructionGlyphs": 1,
                    "distinctFontCodes": 1,
                    "instructionUnicodeSha256": "3" * 64,
                    "contentSha256": "4" * 64,
                    "contentOperations": 4,
                }
            ],
            "fonts": [
                {
                    "fontObject": [1, 0],
                    "policy": "wsl-truetype-glyph-codes-v1",
                    "sourcePostScriptName": "Arbitrary",
                    "sourceSha256": "e" * 64,
                    "sourceCollectionIndex": 2,
                    "embeddedSha256": "5" * 64,
                    "toUnicodeSha256": "6" * 64,
                    "verifiedCodesSha256": "7" * 64,
                    "usedFontCodes": 1,
                    "productionQualified": False,
                }
            ],
        },
    }


def test_manifest_is_deeply_detached_and_bounds_are_preserved(reference):
    pdf, report = reference
    manifest = bridge.input_manifest(pdf, report)
    report["resolvedFonts"][0]["collectionIndex"] = "0"
    assert manifest["resolvedFonts"][0]["collectionIndex"] == "2"
    bridge.validate_result(evidence(manifest), manifest, "1" * 64, "2" * 64)


@pytest.mark.parametrize(
    "key,value",
    [
        ("runtimeImage", None),
        ("fontDirectoryIdentity", None),
        ("workerIdentity", None),
        ("pageCount", True),
        ("pageCount", 51),
        ("pdfSha256", "0" * 64),
        ("fontSource", "linux"),
        ("format", "pdf"),
    ],
)
def test_missing_or_wrong_reference_identity_is_rejected(reference, key, value):
    pdf, report = reference
    report[key] = value
    with pytest.raises(ValueError):
        bridge.input_manifest(pdf, report)


@pytest.mark.parametrize(
    "key,value",
    [
        ("file", "/reference-fonts/../outside.ttf"),
        ("file", "/usr/share/fonts/file.ttf"),
        ("file", "/reference-fonts/subdir/file.ttf"),
        ("file", "/reference-fonts/a\\b.ttf"),
        ("collectionIndex", "01"),
        ("collectionIndex", "256"),
        ("collectionIndex", True),
        ("sha256", "not a hash"),
    ],
)
def test_source_font_records_do_not_allow_arbitrary_paths_or_faces(reference, key, value):
    pdf, report = reference
    report["resolvedFonts"][0][key] = value
    with pytest.raises(ValueError):
        bridge.input_manifest(pdf, report)


@pytest.mark.parametrize(
    "path,value",
    [
        (("codeBundleSha256",), "0" * 64),
        (("runtimeImage", "sha256"), "0" * 64),
        (("fontDirectoryIdentity", "sha256"), "0" * 64),
        (("uid",), 0),
        (("noNewPrivileges",), False),
        (("coreDumpGuard",), None),
        (("coreDumpGuard", "coreLimits"), [0, 1]),
        (("coreDumpGuard", "filterSha256"), "0" * 64),
        (("brokerCoreDumpGuard",), None),
        (("brokerCoreDumpGuard", "coreLimits"), [0, 1]),
        (("brokerCoreDumpGuard", "filterSha256"), "0" * 64),
        (("resourceLimits", "pids.max"), "max"),
        (("dependencies", "fonttools"), "0.0"),
        (("productionQualified",), True),
        (("glyphs", "pdfSha256"), "0" * 64),
        (("glyphs", "pages", 0, "renderedPage"), True),
        (("glyphs", "pages", 0, "contentSha256"), "invalid"),
        (("glyphs", "pages", 0, "textInstructionGlyphs"), 2),
        (("glyphs", "fonts", 0, "sourceSha256"), "0" * 64),
        (("glyphs", "fonts", 0, "sourceCollectionIndex"), True),
        (("glyphs", "fonts", 0, "usedFontCodes"), 0),
        (("glyphs", "distinctFontCodes"), 2),
        (("glyphs", "textInstructionGlyphs"), 2),
    ],
)
def test_changed_helper_report_is_not_accepted(reference, path, value):
    manifest = bridge.input_manifest(*reference)
    report = evidence(manifest)
    target = report
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    with pytest.raises(RuntimeError):
        bridge.validate_result(report, manifest, "1" * 64, "2" * 64)


@pytest.mark.parametrize("value", [None, {}, {"policy": "unverified", "properties": {}}])
def test_missing_or_invalid_unit_controls_are_rejected(reference, value):
    manifest = bridge.input_manifest(*reference)
    report = evidence(manifest)
    report["unitControls"] = value
    with pytest.raises(ValueError, match="effective unit controls"):
        bridge.validate_result(report, manifest, "1" * 64, "2" * 64)


def test_input_archive_contains_only_fixed_names_and_canonical_manifest(reference):
    pdf, report = reference
    manifest = bridge.input_manifest(pdf, report)
    payload = bridge._bundle(
        [("reference.pdf", pdf), ("manifest.json", bridge.canonical_bytes(manifest))]
    )
    actual, decoded = worker._input(payload)
    assert actual == pdf and decoded == manifest
    for entries in [
        [("../reference.pdf", pdf), ("manifest.json", bridge.canonical_bytes(manifest))],
        [
            ("reference.pdf", pdf),
            ("manifest.json", bridge.canonical_bytes(manifest)),
            ("extra", b"x"),
        ],
        [("reference.pdf", pdf), ("manifest.json", b"{}")],
    ]:
        with pytest.raises(RuntimeError):
            worker._input(bridge._bundle(entries))


def test_code_zip_is_deterministic_and_contains_only_owned_code():
    first, second = bridge.code_bundle(), bridge.code_bundle()
    assert first == second
    with ZipFile(io.BytesIO(first)) as archive:
        assert archive.namelist() == [
            "__main__.py",
            "app/__init__.py",
            "app/services/__init__.py",
            *["app/services/" + module + ".py" for module in bridge.CODE_MODULES],
        ]
        assert all(entry.date_time == (1980, 1, 1, 0, 0, 0) for entry in archive.infolist())


def test_glyph_sandbox_retains_legacy_isolation_without_office_execution(tmp_path):
    command = worker.glyph_sandbox_command(tmp_path, "fixture-unit", tmp_path / "image", "a" * 64)
    assert command[-5:] == [worker.PYTHON, "-I", "/glyph-code.pyz", "--inner", "a" * 64]
    assert "--unshare-all" in command and "--clearenv" in command
    assert command[command.index("--uid") + 1] == "65534"
    assert command[command.index("--cap-drop") + 1] == "ALL"
    for target in ("/glyph-input.zip", "/glyph-code.pyz"):
        index = command.index(target)
        assert command[index - 2] == "--ro-bind"
    assert "/worker.py" not in command and "/page.xlsx" not in command
    assert "/usr/bin/libreoffice" not in command
    assert "/usr/lib/libreoffice/program/soffice" not in command
