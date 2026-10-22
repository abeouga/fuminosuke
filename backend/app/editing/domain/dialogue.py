"""Volatile instruction evidence and independently provable slots."""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from app.editing.domain.command import BoundOperation, canonical
from app.editing.domain.fast_path import compile_fast
from app.editing.domain.planning import InvalidPlan, PlanningIssue
from app.editing.domain.planning_catalog import PlanningCatalog, normalized
from app.editing.domain.values import fit_value, resolve_value


def pending_cancellation(text: str) -> str | None:
    """Recognize explicit dialogue control, never infer an editing operation."""
    match = re.fullmatch(
        r"\s*(?:それは中止|この指示は中止|中止して|キャンセルして|キャンセル)"
        r"[。、,\s]*(?:(?:次は|代わりに)[、,\s]*(.+))?[。\s]*",
        text,
        re.DOTALL,
    )
    return match[1].strip() if match and match[1] else "" if match else None


def known_slots(instruction: str, catalog: PlanningCatalog) -> dict[str, Any]:
    operations, values, clauses = [], [], compile_fast(instruction) or ()
    unresolved = []
    for clause in clauses:
        value = resolve_value(json.loads(clause.value_json), {"current": instruction}, clause.quote)
        values.append(value)
        try:
            pool = (
                catalog.pool(clause.target, row=clause.row, column=clause.column)
                if clause.target
                else [catalog.recent_value_target()]
            )
            matches = [
                r for r in pool if any(n in normalized(clause.target) for n in catalog.names(r))
            ]
            if clause.column is not None or not clause.target:
                matches = pool
            if len(matches) != 1:
                unresolved.append({"quote": clause.quote, "value": value, "row": clause.row})
                continue
            ref = matches[0]
            catalog.unambiguous(ref, pool, clause.target)
            operations.append(
                {"op": "ocr.set_value", "target": catalog.targets[ref]["handle"], "value": value}
            )
        except PlanningIssue:
            unresolved.append({"quote": clause.quote, "value": value, "row": clause.row})
    return {"operations": operations, "values": values, "unresolved": unresolved}


@dataclass
class PendingInstruction:
    instruction_id: str
    command_id: str
    original: str
    binding: dict[str, Any]
    deadline: float
    turns: list[dict[str, str]] = field(default_factory=list)
    question: dict[str, Any] | None = None
    partial: dict[str, Any] | None = None
    question_deadline: float = 0
    known: dict[str, Any] = field(default_factory=dict)

    def empty_acknowledgement(self, text: str) -> bool:
        return bool(
            self.question
            and self.question["missingKind"] in {"value", "name"}
            and text.strip(" 。、!")
            in {"はい", "よろしく", "それでよろしく", "そうです", "承認する"}
        )

    def dialogue(self, answer: str) -> dict[str, Any]:
        if (
            len(self.turns) >= 32
            or len(self.original) + sum(len(v["text"]) for v in self.turns) + len(answer) > 20000
        ):
            raise InvalidPlan("Instruction dialogue exceeds its memory limit")
        turns = [
            {"turnId": "original", "text": self.original},
            *self.turns,
            {"turnId": "current", "text": answer},
        ]
        return {"turns": turns, "question": self.question, "known": self.known}

    def establish(self, operations: list[dict[str, Any]]) -> None:
        current = self.known.setdefault("operations", [])
        for operation in operations:
            if operation not in current:
                current.append(operation)
        values = Counter(canonical(v) for v in self.known.setdefault("values", []))
        required = Counter(canonical(v["value"]) for v in current if v["op"] == "ocr.set_value")
        for value, count in (required - values).items():
            self.known["values"].extend(json.loads(value) for _ in range(count))

    def validate(
        self, operations: tuple[BoundOperation, ...], answer: str, catalog: PlanningCatalog
    ) -> None:
        decoded = [v.value() for v in operations]
        # Explicit new edit/correction is reinterpreted as a complete instruction.
        # A bare missing-slot answer cannot remove independently bound operations.
        changed = compile_fast(answer)
        exemptions = (
            {v["target"] for v in known_slots(answer, catalog)["operations"]} if changed else set()
        )
        established = self.known.get("operations", [])
        if any(v not in decoded for v in established if v.get("target") not in exemptions):
            raise InvalidPlan("An answer dropped or changed an established binding")
        expected = Counter(canonical(v) for v in self.known.get("values", []))
        expected.subtract(
            canonical(v["value"])
            for v in established
            if v.get("target") in exemptions and v["op"] == "ocr.set_value"
        )
        if (
            not established
            and sum(expected.values()) == 1
            and len(decoded) == 1
            and any(v in answer for v in ("違う", "訂正"))
        ):
            expected.clear()
        actual = Counter(canonical(v["value"]) for v in decoded if v["op"] == "ocr.set_value")
        if expected - actual:
            raise InvalidPlan("An answer dropped an original value")

    def deterministic(
        self, choice_id: str, catalog: PlanningCatalog
    ) -> tuple[BoundOperation, ...] | None:
        if not self.question or not self.partial:
            return None
        choices = self.question["choices"]
        index = next((i for i, v in enumerate(choices) if v["choiceId"] == choice_id), None)
        refs = self.partial.get("candidateRefs", [])
        unresolved = self.known.get("unresolved", [])
        if index is None:
            raise InvalidPlan("An answer refers to an unissued choice")
        if (
            len(unresolved) != 1
            or index >= len(refs)
            or self.question["missingKind"] not in {"target", "column", "row"}
        ):
            return None
        ref, slot = refs[index], unresolved[0]
        target = catalog.targets.get(ref)
        if not target or "ocr.set_value" not in target["allowedOperations"]:
            return None
        cell = target.get("recordCell")
        if slot["row"] is not None and (not cell or cell["rowNumber"] != slot["row"]):
            raise InvalidPlan("A choice contradicts the original row")
        if ref not in catalog.pool(slot["quote"], row=slot["row"], other_tables="include"):
            raise InvalidPlan("A choice is outside the owned instruction scope")
        operations = [
            *self.known.get("operations", []),
            {
                "op": "ocr.set_value",
                "target": target["handle"],
                "value": fit_value(slot["value"], target),
            },
        ]
        if len({v["target"] for v in operations}) != len(operations):
            raise InvalidPlan("A choice creates competing writes")
        return tuple(BoundOperation.from_value(v) for v in operations)
