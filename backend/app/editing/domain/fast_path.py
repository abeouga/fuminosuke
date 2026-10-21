"""Recognize only complete, explicit value clauses; all other language uses a planner."""

from __future__ import annotations

import re

from app.editing.domain.command import canonical
from app.editing.domain.planning import Clause, evidence
from app.editing.domain.values import numeric_literal, ordinal

_NUMBER = r"[0-9\uff10-\uff19零\u3007一二三四五六七八九十百千]+"
ROW = re.compile(rf"(?:第)?({_NUMBER})\s*行目?")
COLUMN = re.compile(rf"(?:第)?({_NUMBER})\s*列目?")
# 「日令の3」「日令3番」: a column name followed by a bare number is that row.
BARE_ROW = re.compile(rf"(?:の\s*({_NUMBER})\s*(?:番目?)?|({_NUMBER})\s*番目?)$")


def bare_row(target: str) -> tuple[str, int] | None:
    """Split 日令の3 into (日令, 3). Only a trailing number after の, or N番."""
    match = BARE_ROW.search(target.strip())
    if not match or ROW.search(target) or COLUMN.search(target):
        return None
    base, row = target.strip()[: match.start()].strip(), ordinal(match[1] or match[2])
    return (base, row) if base and row is not None else None
_EDIT = re.compile(
    r"(.+?)(?:を|は|:|\uff1a)\s*(.+?)\s*(?:に|へ)(?:変更(?:して)?|修正(?:して)?|直して|設定(?:して)?|して)(?:ください)?[。.!\uff01]?$"
)
_RECENT_EDIT = re.compile(
    # Live transcription may close 違う with 。 rather than 、.
    r"(?:(?:違うよ?|いや)[、,。.\s]*)?(.+?)\s*(?:に|へ)"
    r"(?:変更(?:して)?|修正(?:して)?|直して|設定(?:して)?|して)(?:ください)?[。.!\uff01]?$"
)
_COMPLEX = re.compile(
    r"いや|ではなく|じゃなく|違う|それから|ただし|以外|すべて|全部|ずつ|なら|場合|しない|しなく|取り消|取消"
)


# Plain words that refer to something else rather than being the value itself.
_REFERENTIAL = re.compile(
    r"同じ|前の|元の|もと|それ|これ|あれ|そこ|上の|下の|次の|空|全|値|欄|行|列|の|て$"
)


def value_intent(source: str, *, plain_text: bool = False) -> dict[str, object] | None:
    if source in {"空欄", "空白"}:
        return {"kind": "blank", "evidence": evidence(source)}
    if (source.startswith("「") and source.endswith("」")) or (
        source.startswith('"') and source.endswith('"')
    ):
        if source[1:-1]:
            return {"kind": "text", "text": source[1:-1], "evidence": evidence(source)}
    elif numeric_literal(source) is not None:
        return {"kind": "text", "text": source, "evidence": evidence(source)}
    elif (
        plain_text
        and 1 <= len(source) <= 30
        and not re.search(r"[\s、。,.!?「」\"]", source)
        and not _REFERENTIAL.search(source)
    ):
        # An unquoted word such as こよみ in 名号をこよみにして is the literal value.
        return {"kind": "text", "text": source, "evidence": evidence(source)}
    return None


def spoken_row(text: str) -> int | None:
    """An answer to "何行目ですか": 3行目 / 三行目 / 3 / 3番目."""
    match = ROW.search(text)
    if match:
        return ordinal(match[1])
    return ordinal(re.sub(r"[\s。、,.!?]|番目?|です|でお願いします|で$", "", text))


def compile_recent_edit(instruction: str) -> Clause | None:
    """A complete single value edit with its address omitted, never a bare number."""
    if len(instruction) > 1000:
        return None
    match = _RECENT_EDIT.fullmatch(instruction.strip())
    if match is None:
        return None
    source = match[1].strip()
    # Quoted text can contain punctuation, but cannot hide another edit clause.
    if len(source) > 500 or re.search(r"[「」\"]", source[1:-1]):
        return None
    value = value_intent(source)
    return (
        Clause(0, len(instruction), instruction, "", canonical(value), None, None)
        if value
        else None
    )


def explicit_target(instruction: str) -> str | None:
    """Read an address independently of the value syntax (including units)."""
    match = _EDIT.fullmatch(instruction.strip())
    return match[1].strip() if match else None


def compile_fast(instruction: str) -> tuple[Clause, ...] | None:
    recent = compile_recent_edit(instruction)
    if recent:
        return (recent,)
    if len(instruction) > 1000 or _COMPLEX.search(instruction):
        return None
    # Split outside quoted values. A value containing punctuation remains intact.
    spans, start, quote_end = [], 0, None
    for index, char in enumerate(instruction):
        if quote_end:
            if char == quote_end:
                quote_end = None
        elif char in {"「", '"'}:
            quote_end = "」" if char == "「" else '"'
        elif char in "。、,\n;\uff1b":
            if instruction[start:index].strip():
                spans.append((start, index))
            start = index + 1
    if quote_end:
        return None
    if instruction[start:].strip():
        spans.append((start, len(instruction)))
    if not 1 <= len(spans) <= 4:
        return None
    clauses = []
    for start, end in spans:
        quote = instruction[start:end]
        match = _EDIT.fullmatch(quote.strip())
        if match is None:
            return None
        target, source = (part.strip() for part in match.groups())
        if len(target) > 160 or len(source) > 500:
            return None
        value = value_intent(source, plain_text=True)
        if value is None:
            return None
        bare = bare_row(target)
        if bare:
            target = bare[0]
        rows, columns = list(ROW.finditer(target)), list(COLUMN.finditer(target))
        if len(rows) > 1 or len(columns) > 1:
            return None
        row = bare[1] if bare else ordinal(rows[0][1]) if rows else None
        column = ordinal(columns[0][1]) if columns else None
        if (rows and row is None) or (columns and column is None):
            return None
        clauses.append(Clause(start, end, quote, target, canonical(value), row, column))
    return tuple(clauses)
