"""Determinism requires identical input/environment and all ordered page bytes."""
import copy
import hashlib

import pytest

from app.services import prepared_reference_evidence as evidence
from tests.bootstrap_python_fixture import bootstrap_python_evidence
from tests.core_guard_fixture import core_guard_evidence
from tests.wsl_execution_fixture import wsl_execution_evidence


def sample():
    images = [b"first fixture page", b"second fixture page"]
    report = {
        **wsl_execution_evidence(),
        "policy": "wsl-bwrap-cgroup-v1", "format": "reference", "status": "PASS",
        "sourceSha256": "a" * 64, "fontSource": "windows",
        "fontRequestPolicy": "wsl-fontconfig-face-v2", "dpi": 150,
        "rasterizer": "poppler-pdftoppm",
        "runtimeImage": {"policy": "wsl-squashfs-runtime-v1", "sha256": "b" * 64,
                         "filesystem": "squashfs", "mountsReadOnly": True, "kernel": "fixture"},
        "fontDirectoryIdentity": {"policy": "wsl-font-directory-v1", "sha256": "c" * 64,
                                  "fileCount": 2, "totalBytes": 100},
        "workerIdentity": {"policy": "wsl-worker-source-v1", "sha256": "d" * 64},
        "resolvedFonts": [{"requestedFamily": "arbitrary", "sha256": "e" * 64}],
        "pageCount": 2, "imageSizes": [[100, 200], [100, 200]],
        "imageSha256": [hashlib.sha256(image).hexdigest() for image in images],
        "pdfSha256": "f" * 64,
    }
    return report, images


def test_replay_compares_ordered_pages_without_requiring_pdf_timestamp_identity():
    original, images = sample()
    replay = copy.deepcopy(original)
    replay["pdfSha256"] = "0" * 64
    assert evidence.compare_reference_replay(original, replay, images) is True


@pytest.mark.parametrize("field", ["sourceSha256", "runtimeImage", "workerIdentity",
                                   "fontDirectoryIdentity", "resolvedFonts", "dpi", "rasterizer"])
def test_changed_input_or_environment_is_not_a_determinism_measurement(field):
    original, images = sample()
    replay = copy.deepcopy(original)
    replay[field] = "changed"
    with pytest.raises(ValueError, match="environment|input"):
        evidence.compare_reference_replay(original, replay, images)


@pytest.mark.parametrize("field", ["runtimeImage", "workerIdentity", "fontDirectoryIdentity"])
def test_both_sides_missing_environment_is_not_equal_evidence(field):
    original, images = sample()
    original.pop(field)
    with pytest.raises(ValueError, match="environment"):
        evidence.compare_reference_replay(original, copy.deepcopy(original), images)


@pytest.mark.parametrize("change", ["order", "missing", "pixels"])
def test_every_page_must_match_in_the_saved_order(change):
    original, images = sample()
    if change == "order":
        images.reverse()
    elif change == "missing":
        images.pop()
    else:
        images[-1] += b" changed pixel fixture"
    replay = copy.deepcopy(original)
    replay["imageSha256"] = [hashlib.sha256(image).hexdigest() for image in images]
    replay["pageCount"] = len(images)
    replay["imageSizes"] = replay["imageSizes"][:len(images)]
    assert evidence.compare_reference_replay(original, replay, images) is False


def test_replay_report_cannot_claim_image_hashes_that_do_not_match_its_bytes():
    original, images = sample()
    images[-1] += b" corrupted"
    with pytest.raises(ValueError, match="image"):
        evidence.compare_reference_replay(original, copy.deepcopy(original), images)


@pytest.mark.parametrize("key,factory", [
    ("coreDumpGuard", core_guard_evidence), ("brokerCoreDumpGuard", core_guard_evidence),
    ("bootstrapPythonIdentity", bootstrap_python_evidence),
])
@pytest.mark.parametrize("change", ["missing", "changed", "invalid"])
def test_new_execution_proof_cannot_be_dropped_or_changed(key, factory, change):
    original, images = sample()
    original[key] = factory()
    replay = copy.deepcopy(original)
    assert evidence.compare_reference_replay(original, replay, images) is True
    if change == "missing":
        replay.pop(key)
    elif change == "invalid":
        original[key] = replay[key] = {"qualified": True}
    else:
        changed = "sha256" if key == "bootstrapPythonIdentity" else "librarySha256"
        replay[key][changed] = "f" * 64
    with pytest.raises(ValueError, match="environment|execution evidence"):
        evidence.compare_reference_replay(original, replay, images)


def test_old_reports_do_not_receive_current_execution_proof_on_read():
    original, images = sample()
    previous = copy.deepcopy(original)
    assert evidence.compare_reference_replay(original, copy.deepcopy(original), images) is True
    assert original == previous
    assert "bootstrapPythonIdentity" not in evidence._replay_identity(original)
