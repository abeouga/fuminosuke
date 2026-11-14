from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Any


def canonical(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


@dataclass(frozen=True)
class BoundOperation:
    """Immutable command arguments, never an ORM model or transport DTO."""

    op: str
    arguments_json: str

    @classmethod
    def from_value(cls, value: dict[str, Any]) -> BoundOperation:
        return cls(value["op"], canonical(value))

    def value(self) -> dict[str, Any]:
        return json.loads(self.arguments_json)  # type: ignore[no-any-return]


@dataclass(frozen=True)
class Binding:
    expected_revision: int
    context_revision: int
    interaction_epoch: int
    catalog_revision: str
    scope_digest: str


@dataclass(frozen=True)
class ChangeSet:
    before_json: str
    after_json: str
    operations: tuple[BoundOperation, ...]

    @property
    def changed(self) -> bool:
        return self.before_json != self.after_json

    @property
    def fingerprint(self) -> str:
        return digest(
            {
                "before": self.before_json,
                "after": self.after_json,
                "operations": [op.value() for op in self.operations],
            }
        )


@dataclass(frozen=True)
class PreparedChange:
    token: str
    change_set: ChangeSet


class State(StrEnum):
    ACCEPTED = "ACCEPTED"
    PLANNING = "PLANNING"
    VALIDATING = "VALIDATING"
    COMMITTING = "COMMITTING"
    APPLIED = "APPLIED"
    WAITING_INFORMATION = "WAITING_INFORMATION"
    NO_ACTION = "NO_ACTION"
    CONFLICT = "CONFLICT"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"
    INTERRUPTED = "INTERRUPTED"


ACTIVE = frozenset({State.ACCEPTED, State.PLANNING, State.VALIDATING, State.COMMITTING})
TERMINAL = frozenset(State) - ACTIVE
# Final states that never advanced the resource revision.
UNSAVED_FINAL = frozenset(
    {State.NO_ACTION, State.CONFLICT, State.FAILED, State.CANCELLED, State.EXPIRED}
)
_NEXT = {
    State.ACCEPTED: frozenset({State.PLANNING, State.VALIDATING}),
    State.PLANNING: frozenset({State.VALIDATING, State.WAITING_INFORMATION, State.NO_ACTION}),
    State.VALIDATING: frozenset({State.COMMITTING, State.NO_ACTION}),
    State.COMMITTING: frozenset({State.APPLIED, State.NO_ACTION}),
}


def transition(current: str, following: str) -> None:
    source, target = State(current), State(following)
    failures = {State.CONFLICT, State.FAILED, State.CANCELLED, State.EXPIRED, State.INTERRUPTED}
    if source == State.WAITING_INFORMATION and target in {
        State.CANCELLED,
        State.EXPIRED,
        State.INTERRUPTED,
    }:
        return
    if source not in ACTIVE or (target not in _NEXT.get(source, ()) and target not in failures):
        raise ValueError(f"Invalid editing state transition: {source} -> {target}")


@dataclass(frozen=True)
class ResolvedInstruction:
    operations: tuple[BoundOperation, ...] = ()
    no_action: bool = False
    # A versioned question is returned as-is; it is never turned into a
    # partial successful save.
    question_json: str | None = None
    partial_plan_json: str | None = None
    undo_operation_id: str | None = None
    retarget_operation_id: str | None = None
    retarget_handle: str | None = None
    # A checked rewrite of existing values (column_rewrite), not a dialogue slot answer.
    rewrite: bool = False
