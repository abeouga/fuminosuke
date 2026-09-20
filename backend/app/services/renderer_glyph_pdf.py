"""All-page font-code inventory for the isolated glyph helper, not an OCR gate.

Untrusted PDF/font parsing requires the helper's external process limits. This
module performs no filesystem, network, database or renderer operations.
"""

from __future__ import annotations

import hashlib
import io
import math
import re
from collections import Counter
from collections.abc import Callable, Sequence
from typing import Any

from pypdf import PdfReader
from pypdf.generic import (
    ArrayObject,
    ByteStringObject,
    DictionaryObject,
    IndirectObject,
    TextStringObject,
)

from app.services.renderer_glyph_fonts import (
    MAX_CMAP_BYTES,
    MAX_FONT_BYTES,
    GlyphValidationError,
    SourceFace,
    compare_font_codes,
    load_source_faces,
    parse_to_unicode,
)

MAX_PDF_BYTES = 64 * 1024 * 1024
MAX_CONTENT_BYTES = 16 * 1024 * 1024
MAX_TOTAL_CONTENT_BYTES = 64 * 1024 * 1024
MAX_GLYPH_OCCURRENCES = 2_000_000
MAX_FONT_OBJECTS = 128
MAX_EMBEDDED_FONT_BYTES = 256 * 1024 * 1024
MAX_OPERATIONS = 500_000
_GRAPHICS = {
    b"cm",
    b"w",
    b"J",
    b"j",
    b"M",
    b"d",
    b"ri",
    b"i",
    b"m",
    b"l",
    b"c",
    b"v",
    b"y",
    b"h",
    b"re",
    b"S",
    b"s",
    b"f",
    b"F",
    b"f*",
    b"B",
    b"B*",
    b"b",
    b"b*",
    b"n",
    b"W",
    b"W*",
    b"CS",
    b"cs",
    b"SC",
    b"sc",
    b"G",
    b"g",
    b"RG",
    b"rg",
    b"K",
    b"k",
    b"sh",
}
_TEXT_PARAMETERS = {
    b"Tc": 1,
    b"Tw": 1,
    b"Tz": 1,
    b"TL": 1,
    b"Ts": 1,
    b"Td": 2,
    b"TD": 2,
    b"Tm": 6,
    b"T*": 0,
}
FontId = tuple[int, int]


def _numeric(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, int | float) and math.isfinite(value)


def _stream_bytes(value: Any, maximum: int) -> bytes:
    stream = value.get_object()
    data = stream.get_data()
    if not isinstance(data, bytes) or not 0 < len(data) <= maximum:
        raise GlyphValidationError("PDF stream byte budget exceeded")
    return data


def _embedded_font(reference: IndirectObject) -> dict[str, Any]:
    font = reference.get_object()
    if not isinstance(font, DictionaryObject):
        raise GlyphValidationError("Invalid PDF font dictionary")
    if font.get("/Subtype") != "/TrueType" or "/Encoding" in font:
        raise GlyphValidationError("Unsupported PDF font encoding or subtype")
    descriptor = font["/FontDescriptor"].get_object()
    if not isinstance(descriptor, DictionaryObject):
        raise GlyphValidationError("Invalid PDF font descriptor")
    flags = descriptor["/Flags"]
    if not isinstance(flags, int) or not flags & 4 or flags & 32:
        raise GlyphValidationError("Unsupported PDF font descriptor flags")
    base_font = str(font["/BaseFont"])
    if str(descriptor["/FontName"]) != base_font:
        raise GlyphValidationError("Ambiguous PDF font descriptor identity")
    if "/FontFile" in descriptor or "/FontFile3" in descriptor:
        raise GlyphValidationError("Ambiguous embedded PDF font source")
    first, last = font["/FirstChar"], font["/LastChar"]
    if not isinstance(first, int) or not isinstance(last, int) or not 0 <= first <= last < 256:
        raise GlyphValidationError("Unsupported PDF font code range")
    widths = font["/Widths"].get_object()
    if (
        not isinstance(widths, ArrayObject)
        or len(widths) != last - first + 1
        or not all(_numeric(v) for v in widths)
    ):
        raise GlyphValidationError("Invalid PDF font widths")
    raw_map = _stream_bytes(font["/ToUnicode"], MAX_CMAP_BYTES)
    return {
        "baseFont": base_font,
        "embedded": _stream_bytes(descriptor["/FontFile2"], MAX_FONT_BYTES),
        "toUnicode": raw_map,
        "mapping": parse_to_unicode(raw_map),
        "first": first,
        "last": last,
    }


def _page_usage(
    page: Any,
    fonts: dict[FontId, dict[str, Any]],
) -> tuple[Counter[tuple[FontId, int]], dict[str, Any], int]:
    resources = page["/Resources"].get_object()
    # These can contain nested/conditional text or replace the text font. They
    # need explicit traversal/state handling, never a partial top-level PASS.
    if any(resources.get(key) for key in ("/XObject", "/Pattern", "/ExtGState", "/Properties")):
        raise GlyphValidationError("Unsupported nested or conditional PDF resources")
    local: dict[str, FontId] = {}
    declared = resources.get("/Font", {}).get_object() if "/Font" in resources else {}
    if len(declared) > MAX_FONT_OBJECTS:
        raise GlyphValidationError("PDF font object budget exceeded")
    for key, reference in declared.items():
        if not isinstance(reference, IndirectObject):
            raise GlyphValidationError("PDF font requires explicit indirect identity")
        identity = (reference.idnum, reference.generation)
        local[str(key)] = identity
        if identity not in fonts:
            if len(fonts) >= MAX_FONT_OBJECTS:
                raise GlyphValidationError("PDF font object budget exceeded")
            fonts[identity] = _embedded_font(reference)
            if sum(len(item["embedded"]) for item in fonts.values()) > MAX_EMBEDDED_FONT_BYTES:
                raise GlyphValidationError("PDF embedded font byte budget exceeded")
    content = page.get_contents()
    if content is None:
        raise GlyphValidationError("PDF page has no content stream")
    raw = content.get_data()
    if not 0 < len(raw) <= MAX_CONTENT_BYTES:
        raise GlyphValidationError("PDF page content budget exceeded")
    operations = content.operations
    if len(operations) > MAX_OPERATIONS:
        raise GlyphValidationError("PDF content operation budget exceeded")
    current: FontId | None = None
    render_mode = 0
    stack: list[tuple[FontId | None, int]] = []
    in_text = False
    usage: Counter[tuple[FontId, int]] = Counter()
    character_hash = hashlib.sha256()
    count = 0
    for operands, op in operations:
        if op == b"q" and not operands:
            if len(stack) >= 256:
                raise GlyphValidationError("PDF graphics state budget exceeded")
            stack.append((current, render_mode))
        elif op == b"Q" and not operands:
            if not stack:
                raise GlyphValidationError("Unbalanced PDF graphics state")
            current, render_mode = stack.pop()
        elif op == b"BT" and not operands and not in_text:
            in_text = True
        elif op == b"ET" and not operands and in_text:
            in_text = False
        elif op == b"Tf" and in_text and len(operands) == 2:
            if str(operands[0]) not in local or not _numeric(operands[1]) or operands[1] <= 0:
                raise GlyphValidationError("Invalid PDF text font selection")
            current = local[str(operands[0])]
        elif op == b"Tr" and in_text and len(operands) == 1:
            if operands[0] != 0:
                raise GlyphValidationError("Unsupported PDF text rendering mode")
            render_mode = 0
        elif op in _TEXT_PARAMETERS and in_text:
            if len(operands) != _TEXT_PARAMETERS[op] or not all(_numeric(v) for v in operands):
                raise GlyphValidationError("Invalid PDF text state parameters")
        elif op in {b"Tj", b"TJ", b"'", b'"'} and in_text and current is not None:
            if render_mode != 0:
                raise GlyphValidationError("Unsupported PDF text rendering mode")
            if op == b'"':
                if len(operands) != 3 or not all(_numeric(v) for v in operands[:2]):
                    raise GlyphValidationError("Invalid PDF quoted text operands")
                strings = [operands[2]]
            elif len(operands) != 1:
                raise GlyphValidationError("Invalid PDF text operands")
            elif op == b"TJ":
                if not isinstance(operands[0], list):
                    raise GlyphValidationError("Invalid PDF text array")
                strings = operands[0]
            else:
                strings = operands
            for value in strings:
                if isinstance(value, TextStringObject):
                    codes = value.get_original_bytes()
                elif isinstance(value, ByteStringObject):
                    codes = bytes(value)
                elif op == b"TJ" and _numeric(value):
                    continue
                else:
                    raise GlyphValidationError("Invalid PDF text string")
                count += len(codes)
                if count > MAX_GLYPH_OCCURRENCES:
                    raise GlyphValidationError("PDF glyph occurrence budget exceeded")
                selected = fonts[current]
                for code in codes:
                    if (
                        not selected["first"] <= code <= selected["last"]
                        or code not in selected["mapping"]
                    ):
                        raise GlyphValidationError("PDF text uses an unmapped glyph code")
                    usage[(current, code)] += 1
                    character_hash.update(chr(selected["mapping"][code]).encode("utf-8"))
        elif op in _GRAPHICS:
            continue
        else:
            raise GlyphValidationError("Unsupported PDF operator or text state")
    if stack or in_text:
        raise GlyphValidationError("Unbalanced PDF text or graphics state")
    return (
        usage,
        {
            "textInstructionGlyphs": count,
            "distinctFontCodes": len(usage),
            "instructionUnicodeSha256": character_hash.hexdigest(),
            "contentSha256": hashlib.sha256(raw).hexdigest(),
            "contentOperations": len(operations),
        },
        len(raw),
    )


def validate_pdf_glyphs(
    pdf: bytes,
    expected_sha256: str,
    sources: Sequence[SourceFace],
    *,
    source_resolver: Callable[[set[str]], Sequence[SourceFace]] | None = None,
) -> dict[str, Any]:
    if (
        not isinstance(pdf, bytes)
        or not 0 < len(pdf) <= MAX_PDF_BYTES
        or hashlib.sha256(pdf).hexdigest() != expected_sha256
    ):
        raise GlyphValidationError("Reference PDF identity mismatch")
    if source_resolver is not None and sources:
        raise GlyphValidationError("Source faces and resolver cannot be mixed")
    catalog = load_source_faces(sources) if source_resolver is None else {}
    try:
        reader = PdfReader(io.BytesIO(pdf), strict=True)
        if reader.is_encrypted or not 1 <= len(reader.pages) <= 50:
            raise GlyphValidationError("Unsupported PDF encryption or page count")
        root = reader.trailer["/Root"].get_object()
        if not isinstance(root, DictionaryObject):
            raise GlyphValidationError("Invalid PDF root dictionary")
        if any(root.get(key) for key in ("/AcroForm", "/OCProperties")):
            raise GlyphValidationError("Unsupported interactive or conditional PDF content")
        fonts: dict[FontId, dict[str, Any]] = {}
        usage: Counter[tuple[FontId, int]] = Counter()
        pages = []
        total_bytes = 0
        for number, page in enumerate(reader.pages, 1):
            if page.get("/Annots"):
                raise GlyphValidationError("Unsupported PDF page annotations")
            page_usage, evidence, size = _page_usage(page, fonts)
            usage.update(page_usage)
            total_bytes += size
            if total_bytes > MAX_TOTAL_CONTENT_BYTES or sum(usage.values()) > MAX_GLYPH_OCCURRENCES:
                raise GlyphValidationError("PDF document content budget exceeded")
            pages.append({"renderedPage": number, **evidence})
            if sum(item["contentOperations"] for item in pages) > MAX_OPERATIONS:
                raise GlyphValidationError("PDF document operation budget exceeded")
        if not usage:
            raise GlyphValidationError("Reference PDF contains no supported text instructions")
        if source_resolver is not None:
            names = set()
            for identity in {font for font, _ in usage}:
                match = re.fullmatch(
                    r"/[A-Z]{6}\+([A-Za-z0-9_.-]{1,127})", fonts[identity]["baseFont"]
                )
                if match is None:
                    raise GlyphValidationError("Unsupported embedded subset identity")
                names.add(match[1])
            catalog = load_source_faces(source_resolver(names))
        reports = []
        for identity, item in sorted(fonts.items()):
            codes = sorted(code for (font, code) in usage if font == identity)
            if not codes:
                continue
            reports.append(
                {
                    "fontObject": list(identity),
                    **compare_font_codes(
                        item["embedded"], item["baseFont"], item["toUnicode"], codes, catalog
                    ),
                }
            )
        return {
            "policy": "wsl-pdf-used-glyphs-v1",
            "pdfSha256": expected_sha256,
            "pageCount": len(pages),
            "pages": pages,
            "fonts": reports,
            "textInstructionGlyphs": sum(usage.values()),
            "distinctFontCodes": len(usage),
            "productionQualified": False,
            "visibilityAndLayoutVerified": False,
        }
    except GlyphValidationError:
        raise
    except Exception as exc:
        raise GlyphValidationError("Unsupported or malformed reference PDF") from exc
