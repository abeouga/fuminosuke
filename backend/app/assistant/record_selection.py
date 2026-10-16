"""Spoken table coordinates are constraints, never model-issued authority."""

import re
import unicodedata
from dataclasses import dataclass, replace
from typing import Any

from app.assistant.spoken_numbers import digit_sequence, integer_quantity
from app.core.errors import AppError
from app.editing.contracts import TargetSearch
from app.editing.errors import reject
from app.editing.record_cells import normalized

_DIGITS = {char: index for index, char in enumerate("零一二三四五六七八九")}
_DIGITS["\u3007"] = 0
_READINGS = {
    "ぜろ": "零",
    "ゼロ": "零",
    "いち": "一",
    "に": "二",
    "さん": "三",
    "し": "四",
    "よん": "四",
    "ご": "五",
    "ろく": "六",
    "なな": "七",
    "しち": "七",
    "はち": "八",
    "きゅう": "九",
    "く": "九",
    "じゅう": "十",
    "ひゃく": "百",
}
_READINGS.update(
    {
        "".join(chr(ord(char) + 96) for char in key): value
        for key, value in list(_READINGS.items())
        if all("ぁ" <= char <= "ゖ" for char in key)
    }
)
_NUMBER = r"[0-9\u3007零一二三四五六七八九十百千]+|(?:" + "|".join(_READINGS) + r")+"
_ROW = re.compile(rf"(?:第)?(?P<number>{_NUMBER})\s*(?:行|ぎょう|ギョウ)(?:目|め|メ)?")
_COLUMN = re.compile(rf"(?:第)?(?P<number>{_NUMBER})\s*(?:列|れつ|レツ)(?:目|め|メ)?")


def ordinal(value: str) -> int:
    value = re.sub(
        "|".join(sorted(_READINGS, key=len, reverse=True)),
        lambda match: _READINGS[match[0]],
        value,
    )
    if value.isascii() and value.isdecimal():
        number = int(value)
    elif all(char in _DIGITS for char in value):
        number = int("".join(str(_DIGITS[char]) for char in value))
    else:
        number, digit, last_unit = 0, 0, 10000
        for char in value:
            if char in _DIGITS:
                if digit:
                    raise reject("TARGET_AMBIGUOUS", "行・列の番号を指定してください。")
                digit = _DIGITS[char]
            elif char in {"十", "百", "千"}:
                unit = {"十": 10, "百": 100, "千": 1000}[char]
                if unit >= last_unit:
                    raise reject("TARGET_AMBIGUOUS", "行・列の番号を指定してください。")
                number += (digit or 1) * unit
                digit, last_unit = 0, unit
            else:
                raise reject("TARGET_AMBIGUOUS", "行・列の番号を指定してください。")
        number += digit
    if not 1 <= number <= 9999:
        raise reject("TARGET_AMBIGUOUS", "行・列は1から始まる番号を指定してください。")
    return number


def coordinate(match: re.Match[str], text: str) -> int:
    if text[: match.start()].rstrip().endswith(("マイナス", "-", "\u2212", ".", "万", "億", "兆")):
        raise reject("TARGET_AMBIGUOUS", "行・列は1から始まる整数を指定してください。")
    return ordinal(match["number"])


@dataclass(frozen=True)
class RecordSelection:
    row: int | None
    column: int | None
    column_name: str | None
    table_name: str | None
    value: str | None = None
    value_known: bool = False

    def search(self, recent_table: dict[str, Any] | None) -> TargetSearch:
        return TargetSearch(
            query=self.column_name or "",
            kinds=["field"],
            row=self.row,
            column=self.column,
            table_name=self.table_name,
            table_id=recent_table["tableId"] if recent_table and not self.table_name else None,
        )


def _name(value: str) -> str:
    return normalized(value.strip(" 、。\n「」"))


def _answer(value: str, known_names: set[str]) -> str:
    value = _name(value)
    return value if value in known_names else re.sub(r"(?:だよ|です|だ)$", "", value)


def _table(prefix: str, tables: set[str]) -> str | None:
    prefix = normalized(prefix)
    known = next(
        (name for name in sorted(tables, key=len, reverse=True) if prefix.endswith(name + "の")),
        None,
    )
    return known or (_name(prefix[:-1]) if prefix.endswith("の") else None)


def _target_phrase(text: str, column_names: set[str]) -> tuple[str, str]:
    text = text.strip()
    if text.startswith("の"):
        text = text[1:].lstrip()
    # Known labels can contain particles (はば, もの). Remove syntax only
    # after matching the whole label, never strip arbitrary label characters.
    for name in sorted(column_names, key=len, reverse=True):
        match = re.match(re.escape(name) + r"\s*(?:を|は|:)(.*)$", text, re.I | re.S)
        if match:
            return name, match[1]
    parts = re.split(r"[をは:]", text, maxsplit=1)
    return parts[0].strip(), parts[1].strip() if len(parts) == 2 else ""


def requested_value(text: str, *, answer: bool = False) -> tuple[str | None, bool]:
    value = unicodedata.normalize("NFKC", text).strip(" 、。\n")
    match = re.fullmatch(
        r"(.*?)(?:に|へ)?(?:変更(?:して)?|直して|設定(?:して)?|して)(?:ください)?",
        value,
        re.S,
    )
    if match:
        value = match[1].strip()
    elif not answer:
        return None, False
    if not value:
        return None, False
    if value in {"空欄", "空白"}:
        return None, True
    return value, True


def value_matches(expected: str | None, actual: str | None) -> bool:
    if expected is None or actual is None:
        return expected is actual
    actual = unicodedata.normalize("NFKC", actual)
    if len(expected) >= 2 and (expected[0], expected[-1]) in {("「", "」"), ('"', '"')}:
        return expected[1:-1] == actual
    if (
        expected == actual
        or digit_sequence(expected, expected) == actual
        or integer_quantity(expected) == actual
    ):
        return True
    if re.fullmatch(r"[零一二三四五六七八九十百千]+", expected) and re.search(
        r"[十百千]", expected
    ):
        try:
            return str(ordinal(expected)) == actual
        except AppError:
            return False
    return False


def parse_selections(
    text: str,
    targets: list[dict[str, Any]],
    target_answer: str | None,
    clarifications: list[tuple[str, str]] | None = None,
) -> list[RecordSelection]:
    original, *answers = unicodedata.normalize("NFKC", text).strip().split("\n不足情報への回答: ")
    if clarifications is not None:
        answers = [answer for missing, answer in clarifications if missing == "target"]
    apply_answers = bool(answers) and (clarifications is not None or bool(target_answer))
    tables = {
        normalized(t["recordCell"][key])
        for t in targets
        if t.get("recordCell")
        for key in ("tableName", "tableTitle")
    }
    column_names = {
        normalized(t["recordCell"]["columnName"]) for t in targets if t.get("recordCell")
    }
    rows = [
        match
        for match in _ROW.finditer(original)
        if not re.search(r"[をは:]", re.split(r"[、。\n]", original[: match.start()])[-1])
        or normalized(re.split(r"[、。\n]", original[: match.start()])[-1])
        in {name + "の" for name in tables}
    ]
    selections = []
    for index, row in enumerate(rows):
        start = rows[index - 1].end() if index else 0
        prefix = re.split(r"[、。\n]", original[start : row.start()])[-1].strip()
        table = _table(prefix, tables)
        tail = original[row.end() : rows[index + 1].start() if index + 1 < len(rows) else None]
        tail = re.split(r"[。\n]", tail, maxsplit=1)[0]
        # Only the target phrase precedes を/は/:; new values cannot supply coordinates.
        phrase, value_text = _target_phrase(tail, column_names)
        value, value_known = requested_value(value_text)
        column = _COLUMN.search(phrase)
        column_name = None if column else _name(phrase) or None
        selections.append(
            RecordSelection(
                coordinate(row, original),
                coordinate(column, phrase) if column else None,
                column_name,
                table,
                value,
                value_known,
            )
        )
    if not rows:
        phrase = re.split(r"[をは:]", original, maxsplit=1)[0]
        column = _COLUMN.search(phrase)
        if column:
            table = _table(phrase[: column.start()], tables)
            _, value_text = _target_phrase(original[column.end() :], column_names)
            value, value_known = requested_value(value_text)
            selections.append(
                RecordSelection(
                    None,
                    coordinate(column, phrase),
                    None,
                    table,
                    value,
                    value_known,
                )
            )
    if not selections and apply_answers and any(_ROW.search(a) for a in answers):
        phrase = _name(re.split(r"[をは:]", original, maxsplit=1)[0])
        table = next(
            (
                name
                for name in sorted(tables, key=len, reverse=True)
                if phrase.startswith(name + "の")
            ),
            None,
        )
        column_name = phrase[len(table) + 1 :] if table else phrase
        if column_name:
            _, value_text = _target_phrase(original, {phrase})
            value, value_known = requested_value(value_text)
            selections.append(RecordSelection(None, None, column_name, table, value, value_known))
    # Answers only extend an existing live target question. Retain its value and
    # every earlier table/row/column answer; do not reinterpret value answers.
    if apply_answers and len(selections) == 1:
        selected = selections[0]
        for answer in answers:
            answer = _answer(answer.splitlines()[0], tables | column_names)
            if answer in {
                "はい",
                "うん",
                "そう",
                "そうです",
                "そうだよ",
                "それ",
                "それです",
                "それそれ",
                "その欄",
                "その欄です",
                "それで合ってる",
                "その欄で合ってる",
                "それで合っています",
                "その欄で合っています",
            }:
                continue
            answer_row, answer_column = _ROW.search(answer), _COLUMN.search(answer)
            table = next((name for name in tables if answer in {name, name + "の表"}), None)
            if table:
                selected = replace(selected, table_name=table)
            elif answer_row or answer_column:
                coordinate_match = answer_row or answer_column
                assert coordinate_match is not None
                explicit_table = _table(answer[: coordinate_match.start()], tables)
                selected = replace(
                    selected,
                    table_name=explicit_table or selected.table_name,
                    row=coordinate(answer_row, answer) if answer_row else selected.row,
                    column=coordinate(answer_column, answer) if answer_column else selected.column,
                    column_name=None if answer_column else selected.column_name,
                )
                if answer_row and not answer_column:
                    phrase, _ = _target_phrase(answer[answer_row.end() :], column_names)
                    if _name(phrase):
                        selected = replace(selected, column=None, column_name=_name(phrase))
            elif answer and not re.search(r"[をは:]", answer):
                selected = replace(selected, column=None, column_name=answer)
        selections = [selected]
    if clarifications and len(selections) == 1:
        for missing, answer in clarifications:
            if missing == "value":
                value, known = requested_value(answer, answer=True)
                selections = [replace(selections[0], value=value, value_known=known)]
    return selections


class RecordSelectors:
    def __init__(
        self,
        targets: list[dict[str, Any]],
        context: dict[str, Any],
        text: str,
        target_answer: str | None,
        clarifications: list[tuple[str, str]] | None = None,
    ) -> None:
        self.targets = targets
        self.recent = context.get("recentTable")
        self.problem: AppError | None = None
        try:
            self.selections = (
                parse_selections(text, targets, target_answer, clarifications)
                if context["resource"]["kind"] == "ocr"
                else []
            )
        except AppError as error:
            self.selections = []
            self.problem = error

    def prepare(self, query: TargetSearch) -> TargetSearch:
        if self.problem:
            raise self.problem
        if not self.selections:
            return query
        eligible = [s for s in self.selections if query.row is None or s.row == query.row]
        if query.table_name:
            eligible = [s for s in eligible if s.table_name == normalized(query.table_name)]
        if len(eligible) > 1:
            eligible = [
                s
                for s in eligible
                if (query.column is not None and s.column == query.column)
                or (query.query and normalized(query.query) == s.column_name)
            ]
        if len(eligible) != 1:
            raise reject("TARGET_AMBIGUOUS", "どの行と列を変更するか指定してください。")
        expected = eligible[0].search(self.recent)
        if expected.row is None or not (expected.column or expected.query):
            raise reject("TARGET_AMBIGUOUS", "行番号と列名または列番号を指定してください。")
        for key in ("row", "column", "table_id", "table_name"):
            supplied = getattr(query, key)
            actual = getattr(expected, key)
            if supplied is not None and (
                normalized(supplied) != normalized(actual or "")
                if isinstance(supplied, str)
                else supplied != actual
            ):
                raise reject("TARGET_AMBIGUOUS", "指定された表・行・列と検索条件が一致しません。")
        if query.kinds and query.kinds != ["field"]:
            raise reject("TARGET_AMBIGUOUS", "指定された表の読み取り欄を選択してください。")
        return expected.model_copy(
            update={
                "cursor": query.cursor,
                "page_scope": query.page_scope,
                "position": query.position,
                "heading": query.heading,
                "role": query.role,
            }
        )

    def message(self, candidates: list[dict[str, Any]]) -> str | None:
        if self.problem:
            return self.problem.detail
        if not self.selections:
            return None
        tables = list(
            dict.fromkeys(t["recordCell"]["tableName"] for t in candidates if t.get("recordCell"))
        )
        if len(tables) > 1:
            return f"どの表ですか。候補は「{'、'.join(tables)}」です。表名だけを答えてください。"
        if len(self.selections) == 1:
            selection = self.selections[0]
            if selection.row is None:
                return "何行目ですか。行番号だけを答えてください。"
            if not (selection.column or selection.column_name):
                return "どの列ですか。列名または列番号だけを答えてください。"
            if not candidates:
                table = selection.table_name or (self.recent or {}).get("tableName")
                prefix = f"「{table}」に" if table else ""
                return (
                    f"{prefix}指定された欄が見つかりません。"
                    "表名・行・列の訂正部分だけを答えてください。"
                )
        return None
