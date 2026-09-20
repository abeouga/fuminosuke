import base64
import hashlib
import zlib

import pytest

from app.services import renderer_stroke_probe_pixels as pixels


def test_exact_color_delta_roundtrip_including_first_whitespace_valued_pixel():
    original = bytes([10, 32, 13, 255, 0, 100])
    width, height, data = pixels.read_poppler_ppm(b"P6\n2 1\n255\n" + original)
    assert (width, height, data) == (2, 1, original)
    changed = bytes([10, 32, 13, 0, 255, 101])
    report = pixels.pack_delta(original, changed, width, height)
    assert report["changedPixels"] == 1
    assert pixels.unpack_delta(original, report) == changed
    assert report["visibilityVerified"] is False and report["productionQualified"] is False


def test_zero_delta_is_not_inferred_visibility():
    source = b"\xff" * 12
    report = pixels.pack_delta(source, source, 2, 2)
    assert report["changedPixels"] == 0 and report["visibilityVerified"] is False
    assert pixels.unpack_delta(source, report) == source


@pytest.mark.parametrize("change", ["source", "target", "delta_sha", "count", "boolean_count",
                                    "base64", "size", "policy", "flag", "truncated", "trailing"])
def test_tampered_delta_is_rejected(change):
    source, changed = b"\xff" * 12, b"\0" * 12
    report = pixels.pack_delta(source, changed, 2, 2)
    if change == "source":
        source = b"\0" * 12
    elif change in {"target", "delta_sha"}:
        report["probePixelSha256" if change == "target" else "xorSha256"] = "0" * 64
    elif change in {"count", "boolean_count"}:
        report["changedPixels"] = 0 if change == "count" else True
    elif change == "base64":
        report["xorBase64"] = "not base64"
    elif change == "size":
        report["width"] = True
    elif change == "policy":
        report["encoding"] = "unknown"
    elif change == "flag":
        report["visibilityVerified"] = True
    else:
        data = base64.b64decode(report["xorBase64"])
        report["xorBase64"] = base64.b64encode(
            data[:-1] if change == "truncated" else data + b"garbage").decode()
    with pytest.raises(ValueError):
        pixels.unpack_delta(source, report)


def test_rehashed_decompression_bomb_is_bounded_to_expected_raster():
    report = pixels.pack_delta(b"\xff" * 3, b"\0" * 3, 1, 1)
    huge = b"\0" * 100_000
    report["xorBase64"] = base64.b64encode(zlib.compress(huge)).decode()
    report["xorSha256"] = hashlib.sha256(huge).hexdigest()
    with pytest.raises(ValueError):
        pixels.unpack_delta(b"\xff" * 3, report)


@pytest.mark.parametrize("header", [b"P5\n1 1\n255\n", b"P6\n0 1\n255\n",
                                    b"P6\n1 1\n65535\n", b"P6\n20000001 1\n255\n"])
def test_wrong_ppm_format_and_pixel_budgets_are_not_decoded(header):
    with pytest.raises(ValueError):
        pixels.read_poppler_ppm(header + b"\0" * 3)


@pytest.mark.parametrize("body", [b"\0\0", b"\0\0\0\0"])
def test_ppm_payload_must_be_exact(body):
    with pytest.raises(ValueError):
        pixels.read_poppler_ppm(b"P6\n1 1\n255\n" + body)
