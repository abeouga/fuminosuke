"""A separate fixed vector entrypoint, reusing the glyph worker's isolation ABI."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

from app.services import renderer_glyph_worker as glyph
from app.services.renderer_wsl_worker import verify_font_directory


def inner(runtime_id: str) -> None:
    # The same read-only input undergoes actual full glyph/resource validation
    # before any text operators can be ignored by the stroke inventory.
    glyph.inner(runtime_id)
    from app.services.renderer_splash_calibration import collect as collect_splash_calibration
    from app.services.renderer_stroke_probe_worker import inspect_stroke_probes
    from app.services.renderer_vector_pdf import inspect_vectors

    code = glyph._read(Path("/glyph-code.pyz"), glyph.MAX_CODE)
    payload = glyph._read(Path("/glyph-input.zip"), glyph.MAX_INPUT)
    pdf, manifest = glyph._input(payload)
    output = Path("/output/glyph-report.json")
    report = json.loads(glyph._read(output, glyph.MAX_REPORT))
    if (report["codeBundleSha256"] != hashlib.sha256(code).hexdigest()
            or report["inputSha256"] != hashlib.sha256(payload).hexdigest()):
        raise RuntimeError("Vector helper input or code changed")
    report["vectors"] = inspect_vectors(pdf, manifest["pdfSha256"], report["glyphs"])
    report["strokeProbes"] = inspect_stroke_probes(pdf, report["glyphs"], Path("/output"))
    report["splashCalibration"] = collect_splash_calibration(Path("/output"))
    # Recheck after the additional native/library work, not merely before it.
    verify_font_directory(Path("/reference-fonts"), report["fontDirectoryIdentity"])
    if (glyph._read(Path("/glyph-code.pyz"), glyph.MAX_CODE) != code
            or glyph._read(Path("/glyph-input.zip"), glyph.MAX_INPUT) != payload
            or glyph._environment(runtime_id)["runtimeImage"] != report["runtimeImage"]):
        raise RuntimeError("Vector helper environment changed")
    raw = glyph._canonical(report)
    if len(raw) > glyph.MAX_REPORT:
        raise RuntimeError("Vector helper report budget exceeded")
    output.write_bytes(raw)


def main() -> None:
    if len(sys.argv) == 3 and sys.argv[1] == "--inner":
        inner(sys.argv[2])
    else:
        glyph.main()  # Same broker lease, owned unit, fixed paths, and limits.


if __name__ == "__main__":
    main()
