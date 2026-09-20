"""Single-operation PDF measurement copies. Import only inside isolation.

Keep decoded stream bytes exact except for one pure stroke operator. No original
PDF is overwritten. Raster differences still need independent validation; this
module does not issue a geometry, visibility, or production certificate.
"""
from __future__ import annotations

import hashlib
import io
from decimal import Decimal
from typing import Any

from pypdf import PdfReader, PdfWriter
from pypdf._utils import read_non_whitespace, read_until_regex
from pypdf.generic import (
    ArrayObject,
    ContentStream,
    DecodedStreamObject,
    DictionaryObject,
    FloatObject,
    NameObject,
    read_object,
)

from app.services.renderer_pdf_strokes import MAX_OPERATIONS
from app.services.renderer_vector_pdf import MAX_PDF, inspect_vectors, text_program

POLICY = "wsl-single-stroke-suppression-v1"


class _ExactFloat(FloatObject):
    def myrepr(self) -> str:
        # pypdf's default eight-digit writer changes Office page dimensions.
        # Preserve the parsed numeric value without changing library globals.
        # Fixed notation is required because PDF numbers do not use exponents.
        return format(Decimal(repr(float(self))), "f")


def _preserve_numbers(writer: PdfWriter) -> None:
    visited: set[int] = set()

    def visit(value: Any) -> Any:
        if isinstance(value, FloatObject):
            return _ExactFloat(value)
        if id(value) in visited:
            return value
        visited.add(id(value))
        if isinstance(value, DictionaryObject):
            for key in list(value):
                value[key] = visit(value.raw_get(key))
        elif isinstance(value, ArrayObject):
            for index in range(len(value)):
                value[index] = visit(value[index])
        return value

    # Cloned indirect targets are owned by this writer. Do not dereference or
    # mutate the source reader, and do not serialize decoded content again.
    for index, value in enumerate(writer._objects):
        writer._objects[index] = visit(value)


def _content(page: Any) -> ContentStream:
    content = page.get_contents()
    if not isinstance(content, ContentStream):
        raise ValueError("Missing stroke probe page content")
    return content


def _operator_offsets(content: bytes) -> list[tuple[int, bytes]]:
    """Use the pinned pypdf object reader, never search raw bytes for 'S'.

    This mirrors its content-stream token boundary rules while recording offsets.
    Objects, strings, comments and font names can contain operator-looking bytes.
    Unsupported inline images and trailing operands fail closed.
    """
    if not 0 < len(content) <= MAX_PDF:
        raise ValueError("Stroke probe content budget exceeded")
    stream = io.BytesIO(content)
    offsets = []
    operands = 0
    while True:
        peek = read_non_whitespace(stream)
        if not peek:
            break
        stream.seek(-1, 1)
        if peek.isalpha() or peek in (b"'", b'"'):
            start = stream.tell()
            operator = read_until_regex(stream, NameObject.delimiter_pattern)
            if operator == b"BI":
                raise ValueError("Inline image cannot be a stroke probe")
            offsets.append((start, operator))
            operands = 0
            if len(offsets) > MAX_OPERATIONS:
                raise ValueError("Stroke probe operation budget exceeded")
        elif peek == b"%":
            while peek not in (b"\r", b"\n", b""):
                peek = stream.read(1)
        else:
            read_object(stream, None)
            operands += 1
            if operands > 256:
                raise ValueError("Stroke probe operand budget exceeded")
    if operands:
        raise ValueError("Unfinished stroke probe operands")
    return offsets


class StrokeProbeDocument:
    """The worker must validate source glyphs before creating this object."""

    def __init__(self, pdf: bytes, glyphs: dict[str, Any]) -> None:
        self.sha = hashlib.sha256(pdf).hexdigest()
        self.vectors = inspect_vectors(pdf, self.sha, glyphs)
        self.reader = PdfReader(io.BytesIO(pdf), strict=True)
        self.pdf = pdf
        self.contents = [_content(page).get_data() for page in self.reader.pages]

    def suppress(self, page_number: int, operation: int) -> tuple[bytes, dict[str, Any]]:
        if (type(page_number) is not int or not 1 <= page_number <= len(self.reader.pages)
                or type(operation) is not int or operation < 1):
            raise ValueError("Invalid stroke probe page or operation identity")
        page_index = page_number - 1
        source_page = self.reader.pages[page_index]
        source_content = self.contents[page_index]
        source_operations = _content(source_page).operations
        if operation > len(source_operations):
            raise ValueError("Stroke probe operation is missing")
        args, operator = source_operations[operation - 1]
        if args or operator not in {b"S", b"s"}:
            raise ValueError("Stroke probe requires a pure stroke operation")
        segments = [segment for segment in self.vectors["pages"][page_index]["strokes"]["segments"]
                    if segment["operation"] == operation]
        if (not segments or any(s["clippingUnverified"] or s["sharesPathWithCurve"]
                                or s["width"] <= 0 for s in segments)):
            raise ValueError("Unsupported clipped, curved or hairline stroke probe")
        offsets = _operator_offsets(source_content)
        if (len(offsets) != len(source_operations)
                or [op for _, op in offsets] != [op for _, op in source_operations]):
            raise ValueError("Stroke probe token boundaries differ from PDF parser")
        offset, token = offsets[operation - 1]
        if token != operator or source_content[offset:offset + 1] != operator:
            raise ValueError("Stroke probe operator identity mismatch")
        # n consumes the current path without painting. There is no pending clip
        # in this supported case. s's close has no later effect after path reset.
        changed = source_content[:offset] + b"n" + source_content[offset + 1:]
        writer = PdfWriter()
        writer.clone_document_from_reader(self.reader)
        replacement = DecodedStreamObject()
        replacement.set_data(changed)
        writer.pages[page_index][NameObject("/Contents")] = writer._add_object(replacement)
        _preserve_numbers(writer)
        output = io.BytesIO()
        writer.write(output)
        copied = output.getvalue()
        if not 0 < len(copied) <= MAX_PDF:
            raise ValueError("Stroke probe output budget exceeded")
        self._verify(copied, page_index, changed, operation)
        return copied, {
            "policy": POLICY, "sourcePdfSha256": self.sha,
            "probePdfSha256": hashlib.sha256(copied).hexdigest(),
            "renderedPage": page_number, "operation": operation,
            "sourceContentSha256": hashlib.sha256(source_content).hexdigest(),
            "probeContentSha256": hashlib.sha256(changed).hexdigest(),
            "byteOffset": offset, "originalOperator": operator.decode("ascii"),
            "replacementOperator": "n", "changedDecodedBytes": 1,
            "segmentCount": len(segments), "visibilityVerified": False,
            "productionQualified": False,
        }

    def _verify(self, copied: bytes, selected: int, changed: bytes, operation: int) -> None:
        reader = PdfReader(io.BytesIO(copied), strict=True)
        if reader.is_encrypted or len(reader.pages) != len(self.reader.pages):
            raise ValueError("Stroke probe changed page count or encryption")
        for index, (original, page) in enumerate(zip(self.reader.pages, reader.pages, strict=True)):
            expected = changed if index == selected else self.contents[index]
            content = page.get_contents()
            if (content is None or content.get_data() != expected
                    or _content(original).get_data() != self.contents[index]):
                raise ValueError("Stroke probe changed unrelated content or source bytes")
            if (list(page.mediabox) != list(original.mediabox)
                    or list(page.cropbox) != list(original.cropbox)
                    or page.get("/UserUnit", 1) != original.get("/UserUnit", 1)
                    or page.get("/Rotate", 0) != original.get("/Rotate", 0)):
                raise ValueError("Stroke probe changed page geometry")
            # Restore only the selected token in a comparison stream. This proves
            # non-stroke programs/resources stayed equal without treating n as S
            # in the actual probe or weakening the existing transcript verifier.
            restored = ContentStream(None, reader)
            restored.set_data(self.contents[index])
            if index == selected and content.operations[operation - 1] != ([], b"n"):
                raise ValueError("Stroke probe failed to suppress the selected operation")
            before = text_program(original, _content(original).operations)
            after = text_program(page, restored.operations)
            if before != after:
                raise ValueError("Stroke probe changed text or font resources")
        if hashlib.sha256(self.pdf).hexdigest() != self.sha:
            raise ValueError("Stroke probe source identity changed")
