from __future__ import annotations

import re
from dataclasses import dataclass
from functools import total_ordering
from typing import Self

SEMVER_PATTERN = (
    r"^(0|[1-9]\d*)\."
    r"(0|[1-9]\d*)\."
    r"(0|[1-9]\d*)"
    r"(?:-((?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*)"
    r"(?:\.(?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*))*))?"
    r"(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$"
)
_SEMVER_RE = re.compile(SEMVER_PATTERN)


@total_ordering
@dataclass(frozen=True, slots=True)
class SemanticVersion:
    major: int
    minor: int
    patch: int
    prerelease: tuple[str, ...] = ()
    build: tuple[str, ...] = ()

    @classmethod
    def parse(cls, value: str) -> Self:
        match = _SEMVER_RE.fullmatch(value)
        if match is None:
            raise ValueError(f"Invalid semantic version: {value}")
        prerelease = tuple(match.group(4).split(".")) if match.group(4) else ()
        build = tuple(match.group(5).split(".")) if match.group(5) else ()
        return cls(
            major=int(match.group(1)),
            minor=int(match.group(2)),
            patch=int(match.group(3)),
            prerelease=prerelease,
            build=build,
        )

    def __str__(self) -> str:
        value = f"{self.major}.{self.minor}.{self.patch}"
        if self.prerelease:
            value += f"-{'.'.join(self.prerelease)}"
        if self.build:
            value += f"+{'.'.join(self.build)}"
        return value

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, SemanticVersion):
            return NotImplemented
        return self._precedence_key() == other._precedence_key()

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, SemanticVersion):
            return NotImplemented
        own_core = (self.major, self.minor, self.patch)
        other_core = (other.major, other.minor, other.patch)
        if own_core != other_core:
            return own_core < other_core
        if not self.prerelease:
            return bool(other.prerelease)
        if not other.prerelease:
            return True
        for own, candidate in zip(self.prerelease, other.prerelease, strict=False):
            if own == candidate:
                continue
            own_numeric = own.isdigit()
            candidate_numeric = candidate.isdigit()
            if own_numeric and candidate_numeric:
                return int(own) < int(candidate)
            if own_numeric != candidate_numeric:
                return own_numeric
            return own < candidate
        return len(self.prerelease) < len(other.prerelease)

    def __hash__(self) -> int:
        return hash(self._precedence_key())

    def _precedence_key(self) -> tuple[int, int, int, tuple[str, ...]]:
        return (self.major, self.minor, self.patch, self.prerelease)


def validate_semantic_version(value: str) -> str:
    SemanticVersion.parse(value)
    return value


def compare_semantic_versions(left: str, right: str) -> int:
    left_version = SemanticVersion.parse(left)
    right_version = SemanticVersion.parse(right)
    if left_version < right_version:
        return -1
    if left_version > right_version:
        return 1
    return 0
