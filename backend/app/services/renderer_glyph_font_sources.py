"""Find source-face candidates inside the already pinned read-only font set.

Name matches only find candidates. They are not evidence until the used glyphs,
Unicode mappings, metrics and hints pass the subsequent comparison.
"""

from __future__ import annotations

import hashlib
import io
import re
import struct
from dataclasses import dataclass
from pathlib import Path

from fontTools.ttLib import TTFont  # type: ignore[import-untyped]

from app.services.renderer_glyph_fonts import (
    MAX_FONT_BYTES,
    MAX_SOURCE_BYTES,
    GlyphValidationError,
    SourceFace,
)
from app.services.renderer_wsl_worker import _font_entries


@dataclass(frozen=True)
class SourceCandidate:
    source: SourceFace
    file: str
    postscript_name: str


def find_source_faces(root: Path, required_names: set[str]) -> list[SourceCandidate]:
    if not 1 <= len(required_names) <= 64 or any(
        re.fullmatch(r"[A-Za-z0-9_.-]{1,127}", name) is None for name in required_names
    ):
        raise GlyphValidationError("Invalid source face lookup names")
    candidates: dict[str, SourceCandidate] = {}
    matched_names: dict[str, tuple[str, int]] = {}
    retained_ids: set[tuple[str, int]] = set()
    scanned_faces = retained_bytes = 0
    # Validate all entries with the same flat-file policy as the full-directory
    # identity before selecting a subset. The caller repeats that identity after
    # matching; this routine does not turn a metadata-only scan into a certificate.
    for name, metadata in sorted(_font_entries(root).items()):
        path = root / name
        with path.open("rb") as stream:
            header = stream.read(12)
            if header[:4] not in {b"ttcf", b"\x00\x01\x00\x00", b"true"}:
                continue
            data = header + stream.read(MAX_FONT_BYTES + 1 - len(header))
        if not 0 < len(data) == metadata.st_size <= MAX_FONT_BYTES:
            raise GlyphValidationError("Source font changed during candidate discovery")
        count = struct.unpack(">I", data[8:12])[0] if data[:4] == b"ttcf" else 1
        if not 1 <= count <= 256:
            raise GlyphValidationError("Invalid source collection face count")
        scanned_faces += count
        if scanned_faces > 4096:
            raise GlyphValidationError("Source face discovery budget exceeded")
        for index in range(count):
            try:
                with TTFont(io.BytesIO(data), fontNumber=index, lazy=True) as font:
                    names = {entry.toUnicode() for entry in font["name"].names if entry.nameID == 6}
            except Exception as exc:
                raise GlyphValidationError("Cannot inspect source font name metadata") from exc
            overlap = names & required_names
            if not overlap:
                continue
            sha = hashlib.sha256(data).hexdigest()
            identity = (sha, index)
            for postscript_name in sorted(overlap):
                if postscript_name in matched_names and matched_names[postscript_name] != identity:
                    raise GlyphValidationError("Source face candidate identity is ambiguous")
                matched_names[postscript_name] = identity
                if postscript_name in candidates:
                    continue
                if identity not in retained_ids:
                    retained_ids.add(identity)
                    retained_bytes += len(data)
                    if retained_bytes > MAX_SOURCE_BYTES:
                        raise GlyphValidationError("Source face candidate byte budget exceeded")
                candidates[postscript_name] = SourceCandidate(
                    SourceFace(data=data, sha256=sha, collection_index=index),
                    str(path),
                    postscript_name,
                )
    if set(matched_names) != required_names:
        raise GlyphValidationError("Embedded source face is missing from pinned font set")
    return list(candidates.values())
