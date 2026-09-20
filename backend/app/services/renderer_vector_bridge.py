"""Fixed vector-helper invocation; Windows never parses the PDF or font files."""
from __future__ import annotations

import hashlib
import json
import math
import subprocess
import tempfile
import time
from contextlib import suppress
from pathlib import Path
from typing import Any

from app.services import renderer_glyph_bridge as glyph
from app.services.reference_stroke_differential import validate_manifest
from app.services.renderer_pdf_strokes import POLICY as STROKE_POLICY
from app.services.renderer_pdf_strokes import numbers
from app.services.renderer_splash_calibration import assess as assess_splash_calibration
from app.services.renderer_temp import render_directory
from app.services.renderer_wsl import linux_path

POLICY = "wsl-pdf-vector-pages-v1"


def code_bundle() -> bytes:
    modules = (*glyph.CODE_MODULES, "renderer_pdf_strokes", "renderer_vector_pdf",
               "renderer_stroke_probe_pdf", "renderer_stroke_probe_pixels",
               "renderer_stroke_probe_worker", "renderer_splash_calibration",
               "renderer_vector_worker")
    entries = [("__main__.py", b"from app.services.renderer_vector_worker import main\nmain()\n"),
               ("app/__init__.py", b""), ("app/services/__init__.py", b"")]
    for name in modules:
        data = Path(__file__).with_name(name + ".py").read_bytes()
        if not 0 < len(data) <= glyph.MAX_REPORT:
            raise ValueError("Invalid vector helper code size")
        entries.append(("app/services/" + name + ".py", data))
    return glyph._bundle(entries)


def validate_vectors(report: dict[str, Any], reference: dict[str, Any]) -> None:
    value = report.get("vectors")
    if (not isinstance(value, dict) or value.get("policy") != POLICY
            or value.get("pdfSha256") != reference["pdfSha256"]
            or type(value.get("pageCount")) is not int
            or value["pageCount"] != reference["pageCount"]
            or value.get("productionQualified") is not False
            or value.get("visibilityVerified") is not False
            or not isinstance(value.get("pages"), list)
            or len(value["pages"]) != reference["pageCount"]):
        raise ValueError("Invalid vector helper page inventory")
    total = 0
    for number, page in enumerate(value["pages"], 1):
        if not isinstance(page, dict) or type(page.get("renderedPage")) is not int:
            raise ValueError("Invalid vector page identity")
        checked = report["glyphs"]["pages"][number - 1]
        if (page["renderedPage"] != number or page.get("contentSha256") != checked["contentSha256"]
                or type(page.get("contentOperations")) is not int
                or page["contentOperations"] != checked["contentOperations"]):
            raise ValueError("Vector content identity changed")
        # Older vector reports have no transcript. A diagnostic using it must
        # require its presence explicitly; an absent transcript is never proof.
        if "textProgram" in page:
            trace = page["textProgram"]
            if (not isinstance(trace, dict)
                    or trace.get("policy") != "wsl-pdf-nonstroke-text-program-v1"
                    or trace.get("visibilityVerified") is not False):
                raise ValueError("Invalid vector text transcript")
            if trace.get("status") == "RECORDED":
                if (any(not glyph._sha(trace.get(key)) for key in
                        ("programSha256", "fontResourcesSha256"))
                        or not glyph._integer(trace.get("retainedOperations"),
                                              0, page["contentOperations"])):
                    raise ValueError("Invalid vector text transcript identity")
            elif trace.get("status") != "UNSUPPORTED" or trace.get("reason") != "CLIPPING_PATH":
                raise ValueError("Unsupported vector text transcript status")
        for key in ("mediaBox", "cropBox", "effectiveBox"):
            if not isinstance(page.get(key), list):
                raise ValueError("Invalid vector page box")
            box = numbers(page[key], 4)
            if box[2] <= box[0] or box[3] <= box[1]:
                raise ValueError("Invalid vector page extent")
        media, crop, box = page["mediaBox"], page["cropBox"], page["effectiveBox"]
        if box != [max(media[0], crop[0]), max(media[1], crop[1]),
                   min(media[2], crop[2]), min(media[3], crop[3])]:
            raise ValueError("Vector effective page box mismatch")
        unit = numbers([page.get("userUnit")], 1)[0]
        rotation = page.get("rotation")
        if not 0 < unit <= 75000 or type(rotation) is not int or rotation not in {0, 90, 180, 270}:
            raise ValueError("Invalid vector page transform")
        size = [math.ceil((box[2] - box[0]) * unit * 150 / 72),
                math.ceil((box[3] - box[1]) * unit * 150 / 72)]
        if rotation in {90, 270}:
            size.reverse()
        if size != reference["imageSizes"][number - 1] or size[0] * size[1] > 20_000_000:
            raise ValueError("Vector and raster page dimensions differ")
        strokes = page.get("strokes")
        if (not isinstance(strokes, dict) or strokes.get("policy") != STROKE_POLICY
                or strokes.get("coordinateSpace") != "default-page-points"
                or strokes.get("visibilityVerified") is not False
                or strokes.get("productionQualified") is not False
                or not isinstance(strokes.get("segments"), list)
                or not glyph._integer(strokes.get("paintOperations"), 0, page["contentOperations"])
                or not glyph._integer(strokes.get("curvedStrokePaths"), 0,
                                      strokes["paintOperations"])):
            raise ValueError("Invalid vector stroke inventory")
        total += len(strokes["segments"])
        if total > 100_000:
            raise ValueError("Vector document segment budget exceeded")
        for line in strokes["segments"]:
            _validate_line(line, page["contentOperations"])
    if type(value.get("segmentCount")) is not int or value["segmentCount"] != total:
        raise ValueError("Vector document totals mismatch")
    if "strokeProbes" in report:
        validate_manifest(report["strokeProbes"], value, reference["pdfSha256"])
    if "splashCalibration" in report:
        # Validate raw pixels even when the model does not match. A mismatch is
        # retained as diagnostic evidence, never converted to approval.
        assess_splash_calibration(report["splashCalibration"])


def _validate_line(line: dict[str, Any], operations: int) -> None:
    if not isinstance(line, dict):
        raise ValueError("Invalid vector line")
    for key, count in (("from", 2), ("to", 2), ("strokeMatrix", 6)):
        if not isinstance(line.get(key), list):
            raise ValueError("Invalid vector line coordinates")
        numbers(line[key], count)
    a, b, c, d, _, _ = line["strokeMatrix"]
    if abs(a * d - b * c) < 1e-12:
        raise ValueError("Degenerate vector stroke transform")
    distance = line.get("subpathDistance")
    if distance is None:
        if "subpathDistance" not in line or line.get("sharesPathWithCurve") is not True:
            raise ValueError("Missing vector subpath distance")
    elif numbers([distance], 1)[0] < 0:
        raise ValueError("Invalid vector subpath distance")
    width, miter, phase = numbers([line.get("width"), line.get("miterLimit"),
                                   line.get("dashPhase")], 3)
    if (width < 0 or miter < 1 or phase < 0
            or not glyph._integer(line.get("operation"), 1, operations)
            or any(not glyph._integer(line.get(key), 0, 2) for key in ("lineCap", "lineJoin"))
            or any(type(line.get(key)) is not bool
                   for key in ("clippingUnverified", "sharesPathWithCurve"))):
        raise ValueError("Invalid vector line style")
    dash = line.get("dash")
    if not isinstance(dash, list) or len(dash) > 256:
        raise ValueError("Invalid vector dash array")
    numbers(dash, len(dash))
    if any(v < 0 for v in dash) or (dash and not any(dash)):
        raise ValueError("Invalid vector dash values")
    space = line.get("colorSpace")
    if not isinstance(space, str):
        raise ValueError("Invalid vector color space")
    size = {"DeviceGray": 1, "DeviceRGB": 3, "DeviceCMYK": 4}.get(space)
    if size is None or not isinstance(line.get("color"), list):
        raise ValueError("Invalid vector color")
    color = numbers(line["color"], size)
    if any(not 0 <= v <= 1 for v in color):
        raise ValueError("Invalid vector color components")


def inspect_wsl_vectors(pdf: bytes, reference: dict[str, Any],
                        *, distribution: str = "Ubuntu-22.04") -> dict[str, Any]:
    manifest = glyph.input_manifest(pdf, reference)
    payload = glyph._bundle([("reference.pdf", pdf),
                             ("manifest.json", glyph.canonical_bytes(manifest))])
    if len(payload) > glyph.MAX_INPUT:
        raise ValueError("Vector input bundle is oversized")
    code = code_bundle()
    # Separate fixed entrypoint: do not change the live glyph helper's module
    # globals/dispatch, and never accept an arbitrary caller-provided command.
    with render_directory() as directory, tempfile.TemporaryFile() as log:
        (directory / "input.zip").write_bytes(payload)
        code_path = directory / "glyph.pyz"
        code_path.write_bytes(code)
        command = ["wsl.exe", "--distribution", distribution, "--user", "root", "--exec",
                   "/usr/bin/unshare", "--mount", "--propagation", "private",
                   "/usr/bin/python3", "-I", "-S", "-B", linux_path(code_path), "--broker",
                   linux_path(directory), manifest["runtimeImage"]["sha256"]]
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=log, stderr=log,
                                   creationflags=subprocess.CREATE_NO_WINDOW)
        started = time.monotonic()
        try:
            while process.poll() is None:
                if time.monotonic() - started > 80:
                    raise RuntimeError("Vector helper timed out")
                try:
                    assert process.stdin is not None
                    process.stdin.write(b".\n")
                    process.stdin.flush()
                except (BrokenPipeError, OSError):
                    break
                time.sleep(0.2)
            process.wait(timeout=10)
            if process.returncode:
                log.seek(0)
                raise RuntimeError(log.read(4096).decode("utf-8", errors="replace"))
            output = directory / "glyph-report.json"
            if (output.is_symlink() or not output.is_file()
                    or not 0 < output.stat().st_size <= glyph.MAX_REPORT):
                raise RuntimeError("Invalid vector helper output file")
            report = json.loads(output.read_bytes())
            if not isinstance(report, dict):
                raise RuntimeError("Invalid vector helper output")
            glyph.validate_result(report, manifest, hashlib.sha256(code).hexdigest(),
                                  hashlib.sha256(payload).hexdigest())
            validate_vectors(report, reference)
            return report
        finally:
            if process.stdin:
                with suppress(OSError):
                    process.stdin.close()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
