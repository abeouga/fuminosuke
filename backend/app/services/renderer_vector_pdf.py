"""Collect page geometry and strokes after glyph validation, inside isolation only."""
from __future__ import annotations

import hashlib
import io
import json
import math
from collections import Counter
from typing import Any

from pypdf import PdfReader
from pypdf.generic import ByteStringObject, NameObject, TextStringObject

from app.services.renderer_glyph_pdf import _embedded_font
from app.services.renderer_pdf_strokes import MAX_OPERATIONS, inventory, numbers

POLICY = "wsl-pdf-vector-pages-v1"
MAX_PDF = 64 * 1024 * 1024
TEXT_POLICY = "wsl-pdf-nonstroke-text-program-v1"


def text_program(page: Any, operations: list[tuple[list[Any], bytes]]) -> dict[str, Any]:
    """Strict comparison transcript, not a text bounding box/visibility verifier.

    Retain q/Q and cm, all text parameters/strings/spacing, fill state and paths
    used for fills or clipping. Only pure-stroke paths/state are excluded. Elide
    q/Q pairs only when NOTHING remains between them after that exclusion. Other
    equivalent state programs are not normalized. Never ignore a clipping path.
    Glyph validation and the bounded stroke decoder must precede this function.
    """
    omitted = {b"m", b"l", b"c", b"v", b"y", b"h", b"re", b"S", b"s",
               b"w", b"J", b"j", b"M", b"d", b"G", b"RG", b"K"}
    path_ops = {b"m", b"l", b"c", b"v", b"y", b"h", b"re"}
    paint_ops = {b"S", b"s", b"f", b"f*", b"F", b"B", b"B*", b"b", b"b*", b"n"}
    fill_ops = {b"f", b"f*", b"F", b"B", b"B*", b"b", b"b*"}
    retained_path_indices: set[int] = set()
    path_indices: list[int] = []
    clips = False
    # First choose which path instructions matter, then hash in ORIGINAL order.
    # Moving a buffered path past cm/q/Q would erase its construction context.
    for index, (_, op) in enumerate(operations):
        if op in path_ops:
            path_indices.append(index)
        elif op in {b"W", b"W*"}:
            clips = True
        elif op in paint_ops:
            if clips or op in fill_ops:
                retained_path_indices.update(path_indices)
                retained_path_indices.add(index)
            path_indices = []
            clips = False

    def encode(value: Any, depth: int = 0) -> Any:
        if isinstance(value, TextStringObject):
            return {"bytes": value.get_original_bytes().hex()}
        if isinstance(value, ByteStringObject):
            return {"bytes": bytes(value).hex()}
        if isinstance(value, NameObject):
            return {"name": str(value)}
        if isinstance(value, list) and depth < 2:
            return [encode(item, depth + 1) for item in value]
        return numbers([value], 1)[0]

    def canonical(value: Any) -> bytes:
        return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"),
                          allow_nan=False).encode()

    program = hashlib.sha256()
    operator_counts: Counter[str] = Counter()
    operator_hashes: dict[str, Any] = {}
    retained = 0

    def emit(operands: list[Any], op: bytes) -> None:
        nonlocal retained
        name = op.decode("ascii")
        encoded = canonical([name, encode(operands)]) + b"\n"
        program.update(encoded)
        operator_counts[name] += 1
        operator_hashes.setdefault(name, hashlib.sha256()).update(encoded)
        retained += 1

    pending_saves = 0
    for index, (operands, op) in enumerate(operations):
        if op in omitted and index not in retained_path_indices:
            continue
        if op == b"q":
            pending_saves += 1
        elif op == b"Q" and pending_saves:
            pending_saves -= 1
        else:
            for _ in range(pending_saves):
                emit([], b"q")
            pending_saves = 0
            emit(operands, op)
    if pending_saves:
        raise ValueError("Unbalanced text transcript graphics state")
    resources = page["/Resources"].get_object()
    declared = resources["/Font"].get_object() if "/Font" in resources else {}
    fonts = []
    for name, reference in sorted(declared.items()):
        font = _embedded_font(reference)
        fonts.append({"name": str(name), "baseFont": font["baseFont"],
                      "embeddedSha256": hashlib.sha256(font["embedded"]).hexdigest(),
                      "toUnicodeSha256": hashlib.sha256(font["toUnicode"]).hexdigest(),
                      "first": font["first"], "last": font["last"],
                      "widths": encode(list(reference.get_object()["/Widths"].get_object()))})
    return {"policy": TEXT_POLICY, "status": "RECORDED",
            "programSha256": program.hexdigest(),
            "fontResourcesSha256": hashlib.sha256(canonical(fonts)).hexdigest(),
            "retainedOperations": retained, "visibilityVerified": False,
            "operatorCounts": dict(operator_counts),
            "operatorSequenceHashes": {name: value.hexdigest()
                                       for name, value in operator_hashes.items()}}


def inspect_vectors(pdf: bytes, expected_sha: str, glyphs: dict[str, Any]) -> dict[str, Any]:
    """The caller must first validate this exact PDF with validate_pdf_glyphs.

    A reused hash or externally supplied glyph boolean is not that precondition.
    This function is invoked only by the dedicated vector worker after its glyph
    worker has actually parsed and verified the same read-only input.
    """
    if (not isinstance(pdf, bytes) or not 0 < len(pdf) <= MAX_PDF
            or hashlib.sha256(pdf).hexdigest() != expected_sha
            or glyphs.get("pdfSha256") != expected_sha):
        raise ValueError("Vector PDF identity mismatch")
    reader = PdfReader(io.BytesIO(pdf), strict=True)
    if (reader.is_encrypted or not 1 <= len(reader.pages) <= 50
            or len(reader.pages) != glyphs.get("pageCount")
            or len(glyphs.get("pages", [])) != len(reader.pages)):
        raise ValueError("Vector PDF page count mismatch")
    pages = []
    total_operations = 0
    total_segments = 0
    for ordinal, page in enumerate(reader.pages, 1):
        media = numbers(list(page.mediabox), 4)
        crop = numbers(list(page.cropbox), 4)
        unit = numbers([page.get("/UserUnit", 1)], 1)[0]
        rotation = page.get("/Rotate", 0)
        if (type(rotation) is bool or not isinstance(rotation, int)
                or rotation % 90 or abs(rotation) > 36000 or not 0 < unit <= 75000
                or any(box[2] <= box[0] or box[3] <= box[1] for box in (media, crop))):
            raise ValueError("Invalid vector PDF page geometry")
        effective = [max(media[0], crop[0]), max(media[1], crop[1]),
                     min(media[2], crop[2]), min(media[3], crop[3])]
        width, height = (effective[2] - effective[0]) * unit, (effective[3] - effective[1]) * unit
        if (min(width, height) <= 0 or not math.isfinite(width * height)
                or math.ceil(width * 150 / 72) * math.ceil(height * 150 / 72) > 20_000_000):
            raise ValueError("Vector PDF page geometry budget exceeded")
        content = page.get_contents()
        if content is None:
            raise ValueError("Missing vector PDF page content")
        expected = glyphs["pages"][ordinal - 1]
        content_sha = hashlib.sha256(content.get_data()).hexdigest()
        if expected.get("renderedPage") != ordinal or content_sha != expected.get("contentSha256"):
            raise ValueError("Vector PDF content differs from glyph validation")
        ops = content.operations
        total_operations += len(ops)
        if total_operations > MAX_OPERATIONS or len(ops) != expected.get("contentOperations"):
            raise ValueError("Vector PDF operation count mismatch")
        vectors = inventory(ops)
        total_segments += len(vectors["segments"])
        if total_segments > 100_000:
            raise ValueError("Vector PDF document segment budget exceeded")
        pages.append({"renderedPage": ordinal, "mediaBox": media, "cropBox": crop,
                      "effectiveBox": effective, "rotation": rotation % 360, "userUnit": unit,
                      "contentSha256": content_sha, "contentOperations": len(ops),
                      "strokes": vectors, "textProgram": text_program(page, ops)})
    return {"policy": POLICY, "pdfSha256": expected_sha, "pageCount": len(pages),
            "pages": pages, "segmentCount": total_segments,
            "productionQualified": False, "visibilityVerified": False}
