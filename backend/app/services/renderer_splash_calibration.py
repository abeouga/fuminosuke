"""Fixed synthetic evidence for Splash's simple subpixel stroke adjustment.

The PDF contains only trusted, code-defined paths: no document input or fonts.
Native rendering uses the existing isolated worker's renderer, never a host PDF
parser. Raw RGB evidence is retained so the host can recheck, not trust a PASS.
"""
from __future__ import annotations

import hashlib
import math
from itertools import product
from pathlib import Path
from typing import Any

from app.services.renderer_stroke_probe_pixels import pack_delta, unpack_delta

POLICY = "poppler-splash-simple-stroke-calibration-v1"
WIDTH, HEIGHT = 300, 1100


def cases() -> list[dict[str, Any]]:
    result = []
    for index, (phase, width, vertical, reverse, red) in enumerate(product(
        (.1, .25, .49, .5, .59, .61, .75, .9), (.12, .2, .45, .8),
        (True, False), (True, False), (True, False),
    )):
        x, y = 8 + index % 8 * 32, 8 + index // 8 * 32
        center = (x if vertical else y) + 8 + phase
        start = [center, y + 6] if vertical else [x + 6, center]
        end = [center, y + 26] if vertical else [x + 26, center]
        result.append({"case": index, "width": width, "center": center,
                       "vertical": vertical, "reverse": reverse,
                       "color": [255, 0, 0] if red else [0, 0, 0],
                       "from": end if reverse else start, "to": start if reverse else end})
    return result


def fixture_pdf() -> bytes:
    """Deterministic fixed PDF, not a serializer for user-controlled content."""
    commands = []
    for case in cases():
        start, end = case["from"], case["to"]
        commands.append(
            f"q {case['color'][0] // 255} 0 0 RG 0 J {case['width'] * .48:.8f} w "
            f"{start[0] * .48:.8f} {(HEIGHT - start[1]) * .48:.8f} m "
            f"{end[0] * .48:.8f} {(HEIGHT - end[1]) * .48:.8f} l S Q\n"
        )
    content = "".join(commands).encode("ascii")
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>",
               b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
               (f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {WIDTH * .48:g} "
                f"{HEIGHT * .48:g}] /Resources << >> /Contents 4 0 R >>").encode(),
               f"<< /Length {len(content)} >>\nstream\n".encode() + content + b"endstream"]
    output = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, obj in enumerate(objects, 1):
        offsets.append(len(output))
        output.extend(f"{number} 0 obj\n".encode() + obj + b"\nendobj\n")
    xref = len(output)
    output.extend(b"xref\n0 5\n0000000000 65535 f \n")
    for offset in offsets:
        output.extend(f"{offset:010d} 00000 n \n".encode())
    output.extend(f"trailer\n<< /Size 5 /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return bytes(output)


def collect(scratch: Path) -> dict[str, Any]:
    """Only call from inside the fixed vector helper's existing isolation."""
    from app.services.renderer_stroke_probe_worker import _render

    directory = scratch / "splash-calibration"
    directory.mkdir(mode=0o700, exist_ok=False)
    pdf = directory / "fixture.pdf"
    source = fixture_pdf()
    try:
        pdf.write_bytes(source)
        width, height, rgb = _render(pdf, 1, directory / "fixture-raster", 10.0)
        if (width, height) != (WIDTH, HEIGHT) or pdf.read_bytes() != source:
            raise ValueError("Splash calibration source or raster dimensions changed")
        return {"policy": POLICY, "fixtureSha256": hashlib.sha256(source).hexdigest(),
                "dpi": 150, "raster": pack_delta(b"\xff" * (WIDTH * HEIGHT * 3),
                                                rgb, WIDTH, HEIGHT),
                "visibilityVerified": False, "productionQualified": False}
    finally:
        pdf.unlink(missing_ok=True)
        directory.rmdir()


def assess(report: dict[str, Any]) -> dict[str, Any]:
    """Reconstruct raw pixels; verify every phase/color/direction independently."""
    if (not isinstance(report, dict) or not isinstance(report.get("raster"), dict)
            or report.get("policy") != POLICY or report.get("dpi") != 150
            or report.get("fixtureSha256") != hashlib.sha256(fixture_pdf()).hexdigest()
            or report.get("visibilityVerified") is not False
            or report.get("productionQualified") is not False
            or (report["raster"].get("width"), report["raster"].get("height")) != (WIDTH, HEIGHT)):
        raise ValueError("Invalid Splash calibration evidence")
    rgb = unpack_delta(b"\xff" * (WIDTH * HEIGHT * 3), report["raster"])
    failures = []
    sample_count = 0
    for case in cases():
        normal = math.floor(case["center"] - case["width"] / 2 + .5)
        axis = 1 if case["vertical"] else 0
        lo, hi = sorted([case["from"][axis], case["to"][axis]])
        matched = True
        for along in range(math.ceil(lo + 2), math.floor(hi - 2) + 1):
            sample_count += 1
            for n in (normal - 1, normal, normal + 1):
                x, y = (n, along) if case["vertical"] else (along, n)
                offset = (y * WIDTH + x) * 3
                expected = bytes(case["color"]) if n == normal else b"\xff\xff\xff"
                matched &= rgb[offset:offset + 3] == expected
        if not matched:
            failures.append(case["case"])
    return {"policy": POLICY, "caseCount": len(cases()), "sampleCount": sample_count,
            "unmatchedCases": failures, "status": "UNVERIFIED" if failures else "OBSERVED",
            "visibilityVerified": False, "productionQualified": False}
