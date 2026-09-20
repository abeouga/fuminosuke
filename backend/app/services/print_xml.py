"""Locate XML elements for byte-preserving edits, without reserializing a package."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from xml.parsers import expat


@dataclass
class Element:
    name: str
    qname: bytes
    attrs: dict[str, str]
    start: int
    open_end: int
    close_start: int = 0
    end: int = 0
    children: list[Element] = field(default_factory=list)

    @property
    def prefix(self) -> bytes:
        return self.qname.rsplit(b":", 1)[0] + b":" if b":" in self.qname else b""

    def named(self, name: str) -> list[Element]:
        return [child for child in self.children if child.name == name]


def _tag_end(data: bytes, start: int) -> int:
    # '>' is legal inside a quoted attribute; it is not the tag terminator.
    quote = 0
    for offset in range(start, len(data)):
        char = data[offset]
        if quote:
            if char == quote:
                quote = 0
        elif char in (34, 39):
            quote = char
        elif char == 62:
            return offset + 1
    raise ValueError("Unterminated XML tag")


def parse(data: bytes) -> Element:
    """Validate UTF-8 XML and retain exact byte ranges, including prefixed nodes."""
    if len(data) > 16 * 1024 * 1024 or b"\x00" in data:
        raise ValueError("Excessive or non UTF-8 XML")
    data.decode("utf-8-sig")
    if any(token in data.upper() for token in (b"<!DOCTYPE", b"<!ENTITY")):
        raise ValueError("XML entities are unsupported")
    parser = expat.ParserCreate(namespace_separator="}")
    stack: list[Element] = []
    roots: list[Element] = []
    count = 0

    def declaration(version: str, encoding: str | None, standalone: int) -> None:
        if encoding and encoding.lower().replace("-", "") != "utf8":
            raise ValueError("Only UTF-8 XML declarations are supported")

    def start(name: str, attrs: dict[str, str]) -> None:
        nonlocal count
        count += 1
        if count > 200_000 or len(stack) >= 128:
            raise ValueError("Excessive XML structure")
        offset = parser.CurrentByteIndex
        match = re.match(rb"<([^\s/>]+)", data[offset : offset + 1024])
        if match is None:
            raise ValueError("Invalid XML element")
        node = Element(name, match[1], attrs, offset, _tag_end(data, offset))
        (stack[-1].children if stack else roots).append(node)
        stack.append(node)

    def end(name: str) -> None:
        node = stack.pop()
        if data[node.open_end - 2 : node.open_end] == b"/>":
            node.close_start = node.end = node.open_end
        else:
            node.close_start = parser.CurrentByteIndex
            node.end = _tag_end(data, node.close_start)

    parser.StartElementHandler = start
    parser.EndElementHandler = end
    parser.XmlDeclHandler = declaration
    try:
        parser.Parse(data, True)
    except expat.ExpatError as exc:
        raise ValueError("Malformed XML") from exc
    if len(roots) != 1:
        raise ValueError("Invalid XML root")
    return roots[0]


def edits(data: bytes, changes: list[tuple[int, int, bytes]]) -> bytes:
    """Apply non-overlapping replacements; untouched bytes remain exactly identical."""
    pieces: list[bytes] = []
    cursor = 0
    for start, end, replacement in sorted(changes, key=lambda item: (item[0], item[1])):
        if not cursor <= start <= end <= len(data):
            raise ValueError("Overlapping XML edits")
        pieces.extend((data[cursor:start], replacement))
        cursor = end
    pieces.append(data[cursor:])
    return b"".join(pieces)


def insert_children(data: bytes, node: Element, children: bytes) -> tuple[int, int, bytes]:
    if data[node.open_end - 2 : node.open_end] == b"/>":
        return (node.open_end - 2, node.open_end, b">" + children + b"</" + node.qname + b">")
    return (node.close_start, node.close_start, children)
