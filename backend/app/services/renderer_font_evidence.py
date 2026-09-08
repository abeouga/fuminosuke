"""Embedded PDF font evidence; compare simple TrueType outlines with host files.

No extracted font file is persisted. Composite/CFF glyphs remain unverified.
"""

from __future__ import annotations

import hashlib
import importlib
import struct
from itertools import pairwise
from pathlib import Path
from typing import Any

FONT_FILES = {
    "Calibri": "calibri.ttf",
    "Arial": "arial.ttf",
    "Meiryo": "meiryo.ttc",
    "Yu Gothic": "YuGothR.ttc",
    "Aptos": "aptos.ttf",
}


def simple_outlines(data: bytes) -> set[str]:
    offsets = [0]
    if data[:4] == b"ttcf":
        count = struct.unpack_from(">I", data, 8)[0]
        if count > 32:
            raise ValueError("Excessive font faces")
        offsets = list(struct.unpack_from(f">{count}I", data, 12))
    result = set()
    for base in offsets:
        count = struct.unpack_from(">H", data, base + 4)[0]
        if count > 128:
            raise ValueError("Excessive font tables")
        tables = {}
        for index in range(count):
            tag, _, start, length = struct.unpack_from(">4sIII", data, base + 12 + index * 16)
            if start + length > len(data):
                raise ValueError("Invalid font table")
            tables[tag] = data[start : start + length]
        if not all(tag in tables for tag in [b"head", b"maxp", b"loca", b"glyf"]):
            continue
        count = struct.unpack_from(">H", tables[b"maxp"], 4)[0]
        long = struct.unpack_from(">h", tables[b"head"], 50)[0] != 0
        locations = struct.unpack_from(f">{count + 1}{'I' if long else 'H'}", tables[b"loca"])
        for start, end in pairwise(locations):
            glyph = tables[b"glyf"][start * (1 if long else 2) : end * (1 if long else 2)]
            if len(glyph) >= 10 and struct.unpack_from(">h", glyph)[0] > 0:
                result.add(hashlib.sha256(glyph.rstrip(b"\0")).hexdigest())
    return result


def pdf_fonts(source: Path) -> list[dict[str, Any]]:
    reader = importlib.import_module("pypdf").PdfReader(source, strict=True)
    reports = []
    resources = reader.pages[0]["/Resources"]
    fonts = resources["/Font"].get_object() if "/Font" in resources else {}
    if len(fonts) > 64:
        raise ValueError("Excessive PDF fonts")
    for resource, reference in fonts.items():
        font = reference.get_object()
        descendant = font.get("/DescendantFonts", [font])[0].get_object()
        descriptor = (
            descendant["/FontDescriptor"].get_object() if "/FontDescriptor" in descendant else {}
        )
        embedded_stream = descriptor.get("/FontFile2")
        content = embedded_stream.get_object().get_data() if embedded_stream else b""
        if len(content) > 32 * 1024 * 1024:
            raise ValueError("Excessive embedded font")
        glyphs = simple_outlines(content) if content else set()
        matches = []
        for requested, filename in FONT_FILES.items():
            path = Path("C:/Windows/Fonts") / filename
            if path.is_file() and glyphs:
                host = path.read_bytes()
                overlap = glyphs & simple_outlines(host)
                matches.append(
                    {
                        "family": requested,
                        "path": str(path),
                        "sha256": hashlib.sha256(host).hexdigest(),
                        "matchedSimpleGlyphs": len(overlap),
                        "allSimpleGlyphsMatch": glyphs == overlap,
                    }
                )
        reports.append(
            {
                "resource": str(resource),
                "baseFont": str(font.get("/BaseFont", "")),
                "descriptorName": str(descriptor.get("/FontName", "")),
                "subtype": str(descendant.get("/Subtype", "")),
                "embedded": bool(embedded_stream),
                "embeddedSha256": hashlib.sha256(content).hexdigest(),
                "simpleGlyphCount": len(glyphs),
                "hostCandidates": matches,
                "completeGlyphVerification": False,
            }
        )
    return reports
