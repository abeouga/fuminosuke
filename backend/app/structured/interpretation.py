"""Generic readings of stored date/number strings, produced per answer and never persisted.

No template vocabulary: only calendar forms (西暦, 年号, 2桁年) and plain numerals.
A string with several possible readings returns every candidate; callers must not pick one
silently.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation

# (name, offset so that year = offset + n, maximum era year, aliases)
ERAS: tuple[tuple[str, int, int | None, tuple[str, ...]], ...] = (
    ("令和", 2018, None, ("R", "令")),
    ("平成", 1988, 31, ("H", "平")),
    ("昭和", 1925, 64, ("S", "昭")),
    ("大正", 1911, 15, ("T",)),
    ("明治", 1867, 45, ("M",)),
)
ERA_BY_TOKEN = {alias: era for era, *_rest, aliases in ERAS for alias in (era, *aliases)}
BARE_YEAR_ERAS = ("令和", "平成", "昭和")
WESTERN = "西暦"

_DATE = re.compile(
    r"^(?P<era>令和|平成|昭和|大正|明治|[RHSTM])?\s*(?P<y>元|\d{1,4})\s+(?P<m>\d{1,2})\s+(?P<d>\d{1,2})"
    # A short non-numeric suffix written after the day (e.g. 「生」「頃」) is not part of the date.
    r"(?:\s*[^\d\s]{1,3})?$"
)
_NUMBER = re.compile(r"[-+]?\d+(?:\.\d+)?")
_GROUPED_DOTS = re.compile(r"^\d{1,3}(?:\.\d{3}){2,}$")


@dataclass(frozen=True, slots=True)
class DateReading:
    value: date
    label: str
    era: str | None


def _normalize_date_text(text: str) -> str:
    folded = unicodedata.normalize("NFKC", text).strip()
    folded = folded.replace("元年", "元 ")
    folded = re.sub(r"[年月/.\-]", " ", folded)
    folded = folded.replace("日", " ")
    return re.sub(r"\s+", " ", folded).strip()


def date_readings(text: str | None, *, today: date) -> list[DateReading]:
    """Every calendar reading of ``text`` that is not after ``today``."""
    if not text:
        return []
    match = _DATE.match(_normalize_date_text(text))
    if match is None:
        return []
    month, day = int(match["m"]), int(match["d"])
    year_text = match["y"]
    era_token = match["era"]
    readings: list[DateReading] = []

    def add(year: int, label: str, era: str | None) -> None:
        try:
            value = date(year, month, day)
        except ValueError:
            return
        if value <= today and all(r.value != value for r in readings):
            readings.append(DateReading(value, label, era))

    if era_token:
        era = ERA_BY_TOKEN[era_token]
        number = 1 if year_text == "元" else int(year_text)
        offset, maximum = next((o, m) for e, o, m, _ in ERAS if e == era)
        if number >= 1 and (maximum is None or number <= maximum):
            add(offset + number, f"{era}{number}年{month}月{day}日", era)
        return readings
    if year_text == "元":
        return []
    number = int(year_text)
    if len(year_text) == 4:
        add(number, f"{number}年{month}月{day}日", None)
        return readings
    for era, offset, maximum, _ in ERAS:
        if era in BARE_YEAR_ERAS and number >= 1 and (maximum is None or number <= maximum):
            add(offset + number, f"{era}{number}年{month}月{day}日", era)
    add(2000 + number, f"{2000 + number}年{month}月{day}日", WESTERN)
    return readings


@dataclass(frozen=True, slots=True)
class Elapsed:
    years: int
    months: int
    days: int

    @property
    def total_months(self) -> int:
        return self.years * 12 + self.months

    def label(self) -> str:
        parts = []
        if self.years:
            parts.append(f"{self.years}年")
        parts.append(f"{self.months}か月")
        if self.days:
            parts.append(f"{self.days}日")
        return "".join(parts)


def elapsed_between(start: date, end: date) -> Elapsed:
    if end < start:
        raise ValueError("end precedes start")
    years = end.year - start.year
    months = end.month - start.month
    days = end.day - start.day
    if days < 0:
        months -= 1
        previous_month_end = date(end.year, end.month, 1).toordinal() - 1
        days += date.fromordinal(previous_month_end).day
    if months < 0:
        years -= 1
        months += 12
    return Elapsed(years, months, days)


def number_value(text: str | None) -> Decimal | None:
    """Plain numeral in ``text`` (units and thousands commas ignored); None if unreadable."""
    if not text:
        return None
    folded = unicodedata.normalize("NFKC", text).replace(",", "").replace(" ", "")
    if _GROUPED_DOTS.match(folded):
        return None  # 1.234.567: dots may be thousands separators; never guess.
    numbers = _NUMBER.findall(folded)
    if len(numbers) != 1:
        return None  # 「826-100」「1万2千」: several numerals are not one number; never pick one.
    try:
        return Decimal(numbers[0])
    except InvalidOperation:
        return None
