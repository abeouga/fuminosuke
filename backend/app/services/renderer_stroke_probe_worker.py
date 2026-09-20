"""Render causal stroke probes only inside the existing fixed vector sandbox."""
from __future__ import annotations

import hashlib
import subprocess
import time
from pathlib import Path
from typing import Any

from app.services.renderer_stroke_probe_pdf import StrokeProbeDocument, _content
from app.services.renderer_stroke_probe_pixels import (
    DIFFERENTIAL_POLICY,
    MAX_PIXELS,
    candidate_operations,
    pack_delta,
    read_poppler_ppm,
)

POLICY = DIFFERENTIAL_POLICY
MAX_PROBES = 32
MAX_SECONDS = 60


def candidates(vectors: dict[str, Any]) -> list[tuple[int, int]]:
    """Probe dashed pure paths; never select by form name, label, or field text."""
    return candidate_operations(vectors, MAX_PROBES)


def _render(pdf: Path, page: int, prefix: Path, remaining: float) -> tuple[int, int, bytes]:
    if remaining <= 0:
        raise RuntimeError("Stroke raster deadline exceeded")
    output = prefix.with_suffix(".ppm")
    try:
        subprocess.run(
            ["/usr/bin/pdftoppm", "-f", str(page), "-l", str(page), "-r", "150",
             "-singlefile", str(pdf), str(prefix)],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            check=True, timeout=min(15.0, remaining),
        )
        if output.is_symlink() or not output.is_file() \
                or not 0 < output.stat().st_size <= MAX_PIXELS * 3 + 64:
            raise RuntimeError("Invalid stroke raster output")
        data = output.read_bytes()
        return read_poppler_ppm(data)
    finally:
        # This private scratch filename is owned by this one helper invocation.
        output.unlink(missing_ok=True)


def inspect_stroke_probes(pdf: bytes, glyphs: dict[str, Any],
                          scratch: Path) -> dict[str, Any]:
    """Requires the caller's successful glyph verification of this exact PDF.

    Original raster equality is checked by the host against its owned reference
    PNG. XOR deltas reconstruct entire pages, including any unexpected changes
    away from the selected stroke. No crop or visibility approval is issued here.
    """
    source = StrokeProbeDocument(pdf, glyphs)
    targets = candidates(source.vectors)
    started = time.monotonic()
    directory = scratch / "stroke-probes"
    directory.mkdir(mode=0o700, exist_ok=False)
    original_path = directory / "source.pdf"
    variant_path = directory / "probe.pdf"
    original_path.write_bytes(pdf)
    report: dict[str, Any] = {
        "policy": POLICY, "sourcePdfSha256": source.sha, "dpi": 150,
        "rasterizer": "poppler-pdftoppm", "probes": [],
        "visibilityVerified": False, "productionQualified": False,
    }
    prior_page = 0
    width, height, original = 0, 0, b""
    try:
        for page, operation in targets:
            # A combined fill/stroke is not a pure stroke suppression. Retain it
            # as unsupported; do not remove the fill or certify a partial edit.
            operator = _content(source.reader.pages[page - 1]).operations[operation - 1][1]
            if operator not in {b"S", b"s"}:
                report["probes"].append({"renderedPage": page, "operation": operation,
                                          "status": "UNSUPPORTED", "reason": "COMBINED_FILL"})
                continue
            if page != prior_page:
                width, height, original = _render(
                    original_path, page, directory / "source",
                    MAX_SECONDS - (time.monotonic() - started))
                prior_page = page
            copied, mutation = source.suppress(page, operation)
            variant_path.write_bytes(copied)
            w, h, changed = _render(
                variant_path, page, directory / "probe", MAX_SECONDS - (time.monotonic() - started))
            if (width, height) != (w, h):
                raise ValueError("Stroke probe changed raster dimensions")
            delta = pack_delta(original, changed, width, height)
            report["probes"].append({"renderedPage": page, "operation": operation,
                                      "status": "MEASURED", "mutation": mutation, "delta": delta})
            if time.monotonic() - started > MAX_SECONDS:
                raise RuntimeError("Stroke raster deadline exceeded")
        if (original_path.read_bytes() != pdf
                or hashlib.sha256(original_path.read_bytes()).hexdigest() != source.sha):
            raise RuntimeError("Stroke probe source changed during rendering")
        return report
    finally:
        # Both names are fixed files in the freshly created private directory.
        variant_path.unlink(missing_ok=True)
        original_path.unlink(missing_ok=True)
        directory.rmdir()
