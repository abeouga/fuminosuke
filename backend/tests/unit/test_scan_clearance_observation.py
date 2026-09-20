import hashlib
import json
import tempfile

import numpy as np
import pytest
from PIL import Image

from scripts.inspect_scan_clearance import inspect, observe


def test_empty_observation_is_not_a_clearance_certificate():
    blank = np.full((40, 40), 255, dtype=np.uint8)
    mask, uncertain, fields = observe(blank, blank.copy(), [
        {"regionId": "arbitrary-id", "bbox": [5, 5, 30, 30]},
    ])
    assert not mask.any() and not uncertain.any()
    assert fields == [{"regionId": "arbitrary-id", "bbox": [5, 5, 30, 30],
                       "newDarkPixels": 0, "observedInsetPx": None,
                       "uncertainDarkPixels": 0, "inkClearanceVerified": False}]


@pytest.mark.parametrize("position,expected", [(5, 0), (6, 1), (20, 14)])
def test_single_boundary_pixel_is_not_filtered_away(position, expected):
    blank = np.full((40, 40), 255, dtype=np.uint8)
    filled = blank.copy()
    filled[position, position] = 0
    _, _, fields = observe(blank, filled, [{"regionId": "different", "bbox": [5, 5, 30, 30]}])
    assert fields[0]["newDarkPixels"] == 1
    assert fields[0]["observedInsetPx"] == expected
    assert fields[0]["inkClearanceVerified"] is False
    assert np.all(blank == 255)


def test_ink_on_or_near_print_is_explicitly_unknown_not_safe():
    blank = np.full((40, 40), 255, dtype=np.uint8)
    blank[:, 10] = 0
    filled = blank.copy()
    filled[20, 12] = 0
    candidate, uncertain, fields = observe(
        blank, filled, [{"regionId": "not-label-dependent", "bbox": [5, 5, 30, 30]}])
    assert not candidate.any()
    assert uncertain[20, 12] == 255
    assert fields[0]["uncertainDarkPixels"] == 31
    assert fields[0]["observedInsetPx"] is None
    assert fields[0]["inkClearanceVerified"] is False


@pytest.mark.parametrize("bbox", [[-1, 0, 3, 3], [0, 0, 41, 3], [1, 1, 0, 3],
                                  [True, 1, 3, 3], [1.5, 1, 3, 3]])
def test_invalid_crops_are_not_clipped_or_coerced(bbox):
    blank = np.full((40, 40), 255, dtype=np.uint8)
    with pytest.raises(ValueError, match="canonical crop"):
        observe(blank, blank, [{"regionId": "r", "bbox": bbox}])


def test_shape_mismatch_is_not_resized():
    with pytest.raises(ValueError, match="canonical gray"):
        observe(np.zeros((40, 40), dtype=np.uint8), np.zeros((40, 39), dtype=np.uint8), [])


@pytest.fixture
def paired_report(tmp_path, monkeypatch):
    results = []
    for label, filled in (("blank", False), ("filled", True)):
        pixels = np.full((40, 40), 255, dtype=np.uint8)
        if filled:
            pixels[20, 20] = 0
        folder = tmp_path / label
        folder.mkdir()
        original = tmp_path / f"{label}.png"
        aligned = folder / "aligned.png"
        for path in (original, aligned):
            Image.fromarray(pixels).save(path)
        sha = hashlib.sha256(original.read_bytes()).hexdigest()
        results.append({
            "inputSha256": sha, "inputPath": str(original), "outputDirectory": str(folder),
            "status": "ALIGNED_DIAGNOSTIC_ONLY", "templatePageId": "explicit-page", "page": 1,
            "referenceSha256": "a" * 64, "imageSha256": {"aligned.png": sha},
            "fields": [{"regionId": "arbitrary", "bbox": [5, 5, 30, 30]}],
        })
    report = tmp_path / "report.json"
    report.write_text(json.dumps({"policy": "print-scan-diagnostic-v1",
                                  "originalUnchanged": True, "results": results}))
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    return report, results


def test_explicit_pair_retains_identities_without_certifying(paired_report):
    path, results = paired_report
    output = inspect(path, [(results[0]["inputSha256"], results[1]["inputSha256"])])
    report = json.loads(output.read_bytes())
    assert report["baselineSha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert report["productionQualified"] is False
    assert report["results"][0]["templatePageId"] == "explicit-page"
    assert report["results"][0]["fields"][0]["newDarkPixels"] == 1


@pytest.mark.parametrize("change", ["source", "aligned", "page", "missing_sha", "same_input"])
def test_modified_or_misbound_pair_is_rejected(paired_report, change):
    path, results = paired_report
    pairs = [(results[0]["inputSha256"], results[1]["inputSha256"])]
    report = json.loads(path.read_bytes())
    if change == "source":
        Image.new("RGB", (40, 40), "black").save(results[1]["inputPath"])
    elif change == "aligned":
        Image.new("RGB", (40, 40), "black").save(path.parent / "filled/aligned.png")
    elif change == "page":
        report["results"][1]["templatePageId"] = "different-page"
    elif change == "missing_sha":
        report["results"][1].pop("imageSha256")
    else:
        pairs = [(results[0]["inputSha256"], results[0]["inputSha256"])]
    path.write_text(json.dumps(report))
    with pytest.raises(ValueError):
        inspect(path, pairs)
