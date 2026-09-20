"""Bounded exact RGB differences; no PDF parser, fitting or visibility decision."""
from __future__ import annotations

import base64
import hashlib
import re
import zlib
from typing import Any

POLICY = "rgb-xor-zlib-v1"
MAX_PIXELS = 20_000_000
MAX_COMPRESSED = 512 * 1024
DIFFERENTIAL_POLICY = "wsl-stroke-raster-differential-v1"
MAX_PROBES = 32


def candidate_operations(vectors: dict[str, Any], limit: int = MAX_PROBES) -> list[tuple[int, int]]:
    targets = {(page["renderedPage"], segment["operation"])
               for page in vectors["pages"] for segment in page["strokes"]["segments"]
               if segment["dash"] and segment["width"] > 0
               and not segment["clippingUnverified"] and not segment["sharesPathWithCurve"]}
    if len(targets) > limit:
        raise ValueError("Dashed stroke probe count exceeds budget")
    return sorted(targets)


def _size(width: Any, height: Any) -> int:
    if (type(width) is not int or type(height) is not int or min(width, height) <= 0
            or width * height > MAX_PIXELS):
        raise ValueError("Stroke raster size is invalid")
    return int(width * height * 3)


def read_poppler_ppm(content: bytes) -> tuple[int, int, bytes]:
    """Accept only the canonical P6/8-bit RGB header emitted by the fixed tool."""
    if not isinstance(content, bytes) or len(content) > MAX_PIXELS * 3 + 64:
        raise ValueError("Stroke PPM budget exceeded")
    match = re.match(rb"P6\n([1-9][0-9]{0,7}) ([1-9][0-9]{0,7})\n255\n", content[:64])
    if match is None:
        raise ValueError("Unsupported stroke PPM header")
    width, height = (int(match[index]) for index in (1, 2))
    size = _size(width, height)
    data = content[match.end():]
    if len(data) != size:
        raise ValueError("Stroke PPM is truncated or has trailing bytes")
    return width, height, data


def pack_delta(original: bytes, changed: bytes, width: int, height: int) -> dict[str, Any]:
    size = _size(width, height)
    if not isinstance(original, bytes) or not isinstance(changed, bytes) \
            or len(original) != size or len(changed) != size:
        raise ValueError("Stroke raster byte count changed")
    delta = bytes(a ^ b for a, b in zip(original, changed, strict=True))
    compressed = zlib.compress(delta)
    if len(compressed) > MAX_COMPRESSED:
        raise ValueError("Stroke raster delta exceeds output budget")
    count = sum(bool(delta[i] or delta[i + 1] or delta[i + 2]) for i in range(0, size, 3))
    return {
        "encoding": POLICY, "width": width, "height": height,
        "sourcePixelSha256": hashlib.sha256(original).hexdigest(),
        "probePixelSha256": hashlib.sha256(changed).hexdigest(),
        "xorSha256": hashlib.sha256(delta).hexdigest(),
        "xorBase64": base64.b64encode(compressed).decode("ascii"), "changedPixels": count,
        "visibilityVerified": False, "productionQualified": False,
    }


def unpack_delta(original: bytes, report: dict[str, Any]) -> bytes:
    """Reconstruct every changed pixel and check counts instead of trusting flags."""
    if (not isinstance(report, dict) or report.get("encoding") != POLICY
            or report.get("visibilityVerified") is not False
            or report.get("productionQualified") is not False):
        raise ValueError("Invalid stroke raster delta policy")
    size = _size(report.get("width"), report.get("height"))
    if (not isinstance(original, bytes) or len(original) != size
            or hashlib.sha256(original).hexdigest() != report.get("sourcePixelSha256")
            or not isinstance(report.get("xorBase64"), str)
            or len(report["xorBase64"]) > 4 * ((MAX_COMPRESSED + 2) // 3)):
        raise ValueError("Stroke raster source or delta size changed")
    try:
        compressed = base64.b64decode(report["xorBase64"], validate=True)
        if len(compressed) > MAX_COMPRESSED:
            raise ValueError("Stroke raster compressed delta is oversized")
        decoder = zlib.decompressobj()
        delta = decoder.decompress(compressed, size + 1)
    except (ValueError, zlib.error) as exc:
        raise ValueError("Invalid stroke raster delta encoding") from exc
    if (len(delta) != size or not decoder.eof or decoder.unused_data or decoder.unconsumed_tail
            or hashlib.sha256(delta).hexdigest() != report.get("xorSha256")):
        raise ValueError("Stroke raster decompressed delta is incomplete or changed")
    changed = bytes(a ^ b for a, b in zip(original, delta, strict=True))
    count = sum(bool(delta[i] or delta[i + 1] or delta[i + 2]) for i in range(0, size, 3))
    if (type(report.get("changedPixels")) is not int or report["changedPixels"] != count
            or hashlib.sha256(changed).hexdigest() != report.get("probePixelSha256")):
        raise ValueError("Stroke raster pixel identity or count changed")
    return changed
