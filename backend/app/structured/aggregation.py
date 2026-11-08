"""Reading written values as numbers and computing over them (shared by the chat and the APIs).

The rules are the chat's: a value is read with ``number_value`` and never guessed; 「762.300」 next
to plain integers of four or more digits is read with 「.」 as a thousands separator.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from app.structured.interpretation import number_value

Operation = Literal["sum", "avg", "count", "max", "min"]
How = Literal["plain", "dots_as_thousands", "dots_as_decimal"]

DOTTED_THOUSANDS = re.compile(r"^\d{1,3}\.\d{3}$")
PLAIN_INTEGER = re.compile(r"^\d{4,}$")


@dataclass(slots=True, frozen=True)
class NumberRead:
    index: int  # the position in the values given
    value: str
    number: Decimal
    how: How


@dataclass(slots=True, frozen=True)
class NumberSkipped:
    index: int
    value: str


@dataclass(slots=True, frozen=True)
class Numbers:
    read: list[NumberRead]
    skipped: list[NumberSkipped]  # written, but not a number
    blank: int  # no value at all
    written: int  # values present (read or skipped)


def _folded(value: str | None) -> str:
    return unicodedata.normalize("NFKC", value or "").replace(",", "").strip()


def read_numbers(values: Sequence[str | None]) -> Numbers:
    """Each written value as a number, in order; what could not be read is kept, not guessed."""
    large_integers = any(PLAIN_INTEGER.match(_folded(v)) for v in values if v)
    read: list[NumberRead] = []
    skipped: list[NumberSkipped] = []
    blank = 0
    for index, value in enumerate(values):
        if value is None:
            blank += 1
            continue
        folded = _folded(value)
        dotted = DOTTED_THOUSANDS.match(folded) is not None
        if dotted and large_integers:
            read.append(
                NumberRead(index, value, Decimal(folded.replace(".", "")), "dots_as_thousands")
            )
            continue
        number = number_value(value)
        if number is None:
            skipped.append(NumberSkipped(index, value))
            continue
        read.append(NumberRead(index, value, number, "dots_as_decimal" if dotted else "plain"))
    return Numbers(read, skipped, blank, len(values) - blank)


def compute(numbers: Numbers, operation: Operation) -> Decimal | int | None:
    """``count`` is the written values (read or not); the others use the numbers read, or None
    when there is none. The average is rounded to two decimals."""
    if operation == "count":
        return numbers.written
    values = [n.number for n in numbers.read]
    if not values:
        return None
    total = sum(values, Decimal(0))
    return {
        "sum": total,
        "avg": (total / len(values)).quantize(Decimal("0.01")),
        "max": max(values),
        "min": min(values),
    }[operation]


def average(values: Sequence[Decimal]) -> Decimal:
    return (sum(values, Decimal(0)) / len(values)).quantize(Decimal("0.01"))


def extremes(numbers: Numbers, result: Decimal) -> list[NumberRead]:
    """The values that are the maximum or minimum (all of them when several are equal)."""
    return [n for n in numbers.read if n.number == result]
