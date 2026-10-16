"""Provider-independent inputs and policy for a single, atomic instruction."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal


@dataclass(frozen=True)
class PlannerPolicy:
    version: str = "decisions-first-v2"
    model: str = "gpt-6-luna"
    decision_seconds: float = 3
    response_seconds: float = 15
    probability: float = 0.98
    margin: float = 0.20
    confidence: float = 0.90
    exact_probability: float = 0.85
    exact_confidence: float = 0.80
    semantic_effort: Literal["none", "low"] = "low"
    catalog_tokens: int = 12000
    route: Literal["decisions_first", "responses_first"] = "decisions_first"


class PlanningIssue(Exception):
    """A missing slot, not permission to save the other resolved operations."""

    def __init__(self, kind: str, candidates: tuple[str, ...] = ()) -> None:
        self.kind, self.candidates = kind, candidates
        self.probabilities: tuple[float, ...] = ()
        self.confidences: tuple[float, ...] = ()
        super().__init__(kind)


class InvalidPlan(Exception):
    pass


@dataclass(frozen=True)
class Clause:
    start: int
    end: int
    quote: str
    target: str
    value_json: str
    row: int | None
    column: int | None


@dataclass(frozen=True)
class CandidateSet:
    snapshot_json: str
    proposals_json: str


@dataclass(frozen=True)
class PlanningTrace:
    route: str
    decision_ms: int = 0
    response_ms: int = 0
    candidate_counts: tuple[int, ...] = ()
    probabilities: tuple[float, ...] = ()
    confidences: tuple[float, ...] = ()


def evidence(quote: str, turn_id: str = "current") -> dict[str, str]:
    return {"turnId": turn_id, "quote": quote}


def check_evidence(value: dict[str, Any], turns: dict[str, str]) -> str:
    quote = value.get("quote")
    if (
        not isinstance(quote, str)
        or not quote
        or quote not in turns.get(str(value.get("turnId")), "")
    ):
        raise InvalidPlan("Evidence is not part of an authorized turn")
    return quote
