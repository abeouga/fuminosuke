"""Bounded TrueType correspondence checks for a future isolated PDF helper.

Do not call this on untrusted fonts/PDF streams from the backend process. The
caller must supply the renderer's read-only source faces and enforce process,
network, memory and time limits. This module is not a qualification issuer.
"""

from __future__ import annotations

import hashlib
import io
import json
import math
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from fontTools.pens.recordingPen import DecomposingRecordingPen  # type: ignore[import-untyped]
from fontTools.ttLib import TTFont  # type: ignore[import-untyped]

MAX_FONT_BYTES = 64 * 1024 * 1024
MAX_SOURCE_BYTES = 256 * 1024 * 1024
MAX_SOURCE_FACES = 64
MAX_CMAP_BYTES = 64 * 1024
MAX_COMPONENTS = 4096
MAX_POINTS = 65536


class GlyphValidationError(ValueError):
    """Unsupported, ambiguous or inconsistent font evidence; never partial PASS."""


@dataclass(frozen=True)
class SourceFace:
    data: bytes
    sha256: str
    collection_index: int


@dataclass(frozen=True)
class LoadedFace:
    source: SourceFace
    font: Any


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode()).hexdigest()


def _font(data: bytes, index: int = 0) -> Any:
    if not isinstance(data, bytes) or not 0 < len(data) <= MAX_FONT_BYTES:
        raise GlyphValidationError("Invalid font bytes")
    if type(index) is not int or not 0 <= index < 256:
        raise GlyphValidationError("Invalid font collection index")
    if data[:4] != b"ttcf" and index != 0:
        raise GlyphValidationError("Non-collection font index must be zero")
    if data[:4] not in {b"ttcf", b"\x00\x01\x00\x00", b"true"}:
        raise GlyphValidationError("Unsupported TrueType font container")
    try:
        font = TTFont(io.BytesIO(data), fontNumber=index, lazy=False)
        required = {"head", "hmtx", "maxp", "glyf", "cmap"}
        unsupported = {"CFF ", "CFF2", "fvar", "gvar", "COLR", "SVG ", "CBDT", "sbix"}
        if not required.issubset(font.keys()) or unsupported.intersection(font.keys()):
            raise GlyphValidationError("Unsupported font tables")
        if not 16 <= font["head"].unitsPerEm <= 16384:
            raise GlyphValidationError("Invalid glyph units")
        if not 0 < font["maxp"].numGlyphs <= 65535:
            raise GlyphValidationError("Invalid glyph count")
        return font
    except GlyphValidationError:
        raise
    except Exception as exc:
        raise GlyphValidationError("Invalid TrueType font or collection index") from exc


def load_source_faces(sources: Sequence[SourceFace]) -> dict[str, LoadedFace]:
    if not 0 < len(sources) <= MAX_SOURCE_FACES:
        raise GlyphValidationError("Invalid source face count")
    result: dict[str, LoadedFace] = {}
    identities: set[tuple[str, int]] = set()
    total = 0
    for source in sources:
        if (not isinstance(source.data, bytes) or not 0 < len(source.data) <= MAX_FONT_BYTES
                or re.fullmatch(r"[0-9a-f]{64}", source.sha256) is None
                or hashlib.sha256(source.data).hexdigest() != source.sha256):
            raise GlyphValidationError("Source font identity mismatch")
        if type(source.collection_index) is not int or not 0 <= source.collection_index < 256:
            raise GlyphValidationError("Invalid source collection index")
        identity = (source.sha256, source.collection_index)
        if identity in identities:
            continue
        identities.add(identity)
        total += len(source.data)
        if total > MAX_SOURCE_BYTES:
            raise GlyphValidationError("Source font byte budget exceeded")
        font = _font(source.data, source.collection_index)
        preferred = font.getBestCmap()
        if not preferred:
            raise GlyphValidationError("Source face has no Unicode glyph map")
        for table in font["cmap"].tables:
            if table.isUnicode() and any(
                scalar in preferred and preferred[scalar] != glyph
                for scalar, glyph in table.cmap.items()
            ):
                raise GlyphValidationError("Source Unicode glyph map is ambiguous")
        try:
            names = {entry.toUnicode() for entry in font["name"].names if entry.nameID == 6}
        except Exception as exc:
            raise GlyphValidationError("Missing source face name") from exc
        # Exact name records can alias one face; different SHA/index identities
        # must never be merged merely because a name matches.
        names = {name for name in names if re.fullmatch(r"[A-Za-z0-9_.-]{1,127}", name)}
        if not names or len(names) > 64:
            raise GlyphValidationError("Unsupported source face name")
        loaded = LoadedFace(source, font)
        for name in names:
            if name in result:
                raise GlyphValidationError("Same source face name has ambiguous identity")
            result[name] = loaded
    return result


# Deliberately interpret only this fixed, declarative single-byte CMap form.
# PostScript procedures, inherited CMaps and unsupported ranges are not executed
# or ignored. No registry/order/font/template literal is an identity allowlist.
_HEADER = re.compile(
    r"\s*/CIDInit\s*/ProcSet\s+findresource\s+begin\s+12\s+dict\s+begin\s+begincmap\s+"
    r"/CIDSystemInfo\s*<<\s*/Registry\s*\([A-Za-z0-9_.-]+\)\s*"
    r"/Ordering\s*\([A-Za-z0-9_.-]+\)\s*/Supplement\s+0\s*>>\s+def\s+"
    r"/CMapName\s*/[A-Za-z0-9_.+-]+\s+def\s+/CMapType\s+2\s+def\s+"
    r"1\s+begincodespacerange\s*<00>\s*<[fF]{2}>\s*endcodespacerange\s+"
)
_FOOTER = re.compile(
    r"endcmap\s+CMapName\s+currentdict\s+/CMap\s+defineresource\s+pop\s+end\s+end\s*"
)
_GROUP = re.compile(r"([0-9]{1,3})\s+beginbfchar\s+(.*?)\s*endbfchar\s+", re.S)
_PAIR = re.compile(r"\s*<([0-9a-fA-F]{2})>\s*<([0-9a-fA-F]{4}(?:[0-9a-fA-F]{4})?)>\s*")


def parse_to_unicode(raw: bytes) -> dict[int, int]:
    if not isinstance(raw, bytes) or not 0 < len(raw) <= MAX_CMAP_BYTES:
        raise GlyphValidationError("Invalid ToUnicode byte budget")
    try:
        text = re.sub(r"%[^\r\n]*", "", raw.decode("ascii"))
    except UnicodeDecodeError as exc:
        raise GlyphValidationError("Unsupported ToUnicode encoding") from exc
    header = _HEADER.match(text)
    if header is None:
        raise GlyphValidationError("Unsupported ToUnicode header or code space")
    position = header.end()
    mapping: dict[int, int] = {}
    while group := _GROUP.match(text, position):
        count = int(group[1])
        if not 1 <= count <= 100:
            raise GlyphValidationError("ToUnicode group count is outside its bound")
        body, offset = group[2], 0
        for _ in range(count):
            pair = _PAIR.match(body, offset)
            if pair is None:
                raise GlyphValidationError("Invalid ToUnicode entries or count")
            code = int(pair[1], 16)
            try:
                scalar = bytes.fromhex(pair[2]).decode("utf-16-be")
            except UnicodeDecodeError as exc:
                raise GlyphValidationError("Invalid ToUnicode scalar") from exc
            if len(scalar) != 1 or code in mapping:
                raise GlyphValidationError("Ambiguous ToUnicode scalar or duplicate code")
            mapping[code] = ord(scalar)
            offset = pair.end()
        if body[offset:].strip():
            raise GlyphValidationError("Unexpected ToUnicode group content")
        position = group.end()
    if not mapping or _FOOTER.fullmatch(text, position) is None:
        raise GlyphValidationError("Unsupported ToUnicode body or trailing program")
    return mapping


def glyph_signature(font: Any, name: str) -> str:
    """Include composite outlines, metrics and ordered component hint programs."""
    glyphs = font["glyf"]
    components = points = 0
    hints: list[str] = []

    def visit(key: str, ancestors: frozenset[str]) -> None:
        nonlocal components, points
        components += 1
        if (key in ancestors or key not in glyphs or len(ancestors) >= 32
                or components > MAX_COMPONENTS):
            raise GlyphValidationError("Invalid glyph component graph")
        glyph = glyphs[key]
        program = getattr(glyph, "program", None)
        hints.append(bytes(program.getBytecode()).hex() if program is not None else "")
        if glyph.isComposite():
            for component in glyph.components:
                visit(component.glyphName, ancestors | {key})
        else:
            points += len(getattr(glyph, "coordinates", ()))
            if points > MAX_POINTS:
                raise GlyphValidationError("Glyph component point budget exceeded")

    try:
        visit(name, frozenset())
        glyph_set = font.getGlyphSet()
        pen = DecomposingRecordingPen(glyph_set, skipMissingComponents=False)
        glyph_set[name].draw(pen)
        outline = []
        for operation, arguments in pen.value:
            if operation not in {"moveTo", "lineTo", "qCurveTo", "curveTo", "closePath", "endPath"}:
                raise GlyphValidationError("Unsupported glyph outline operation")
            coordinates = [None if point is None else [float(v) for v in point]
                           for point in arguments]
            if any(not math.isfinite(value) for point in coordinates if point for value in point):
                raise GlyphValidationError("Non-finite glyph outline")
            outline.append((operation, coordinates))
        return _digest({
            "outline": outline, "metrics": tuple(font["hmtx"].metrics[name]),
            "hints": hints, "unitsPerEm": font["head"].unitsPerEm,
        })
    except GlyphValidationError:
        raise
    except Exception as exc:
        raise GlyphValidationError("Invalid glyph component or outline") from exc


def compare_font_codes(
    embedded: bytes, base_font: str, to_unicode: bytes, used_codes: Sequence[int],
    sources: dict[str, LoadedFace],
) -> dict[str, object]:
    if not 0 < len(used_codes) <= 256 or any(type(c) is not int or not 0 <= c < 256
                                           for c in used_codes):
        raise GlyphValidationError("Invalid used font codes")
    subset = re.fullmatch(r"/[A-Z]{6}\+([A-Za-z0-9_.-]{1,127})", base_font)
    if subset is None:
        raise GlyphValidationError("Unsupported embedded subset identity")
    source = sources.get(subset[1])
    if source is None:
        raise GlyphValidationError("No unique source face for embedded subset")
    font = _font(embedded)
    tables = font["cmap"].tables
    if (len(tables) != 1 or (tables[0].platformID, tables[0].platEncID) != (1, 0)
            or tables[0].format not in {0, 6}):
        raise GlyphValidationError("Unsupported embedded glyph code map")
    if font["head"].unitsPerEm != source.font["head"].unitsPerEm:
        raise GlyphValidationError("Source and embedded glyph units differ")
    for tag in ("fpgm", "prep", "cvt "):
        original = source.font.getTableData(tag) if tag in source.font else None
        copied = font.getTableData(tag) if tag in font else None
        if original != copied:
            raise GlyphValidationError("Source and embedded global glyph hints differ")
    source_cmap = source.font.getBestCmap()
    if not source_cmap:
        raise GlyphValidationError("Source face has no Unicode glyph map")
    mapping = parse_to_unicode(to_unicode)
    verified = []
    for code in sorted(set(used_codes)):
        scalar = mapping.get(code)
        embedded_name = tables[0].cmap.get(code)
        original_name = source_cmap.get(scalar)
        if (scalar is None or embedded_name in {None, ".notdef"}
                or original_name in {None, ".notdef"}):
            raise GlyphValidationError("Used glyph or Unicode mapping is missing")
        copied = glyph_signature(font, embedded_name)
        if copied != glyph_signature(source.font, original_name):
            raise GlyphValidationError("Used glyph differs from source Unicode glyph")
        verified.append({"code": code, "unicode": scalar, "glyphSha256": copied})
    return {
        "policy": "wsl-truetype-glyph-codes-v1", "sourcePostScriptName": subset[1],
        "sourceSha256": source.source.sha256,
        "sourceCollectionIndex": source.source.collection_index,
        "embeddedSha256": hashlib.sha256(embedded).hexdigest(),
        "toUnicodeSha256": hashlib.sha256(to_unicode).hexdigest(),
        "usedFontCodes": len(verified), "verifiedCodesSha256": _digest(verified),
        "productionQualified": False,
    }
