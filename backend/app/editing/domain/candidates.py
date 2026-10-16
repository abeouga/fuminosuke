"""Closed candidate snapshots and complete Decisions-answer validation."""

from __future__ import annotations

import json
import math
import uuid
from typing import Any

from app.editing.domain.command import BoundOperation, canonical, digest
from app.editing.domain.planning import (
    CandidateSet,
    Clause,
    InvalidPlan,
    PlannerPolicy,
    PlanningIssue,
)
from app.editing.domain.planning_catalog import PlanningCatalog
from app.editing.domain.values import resolve_value


def build_candidates(
    instruction: str,
    clauses: tuple[Clause, ...],
    catalog: PlanningCatalog,
    policy: PlannerPolicy,
) -> CandidateSet | None:
    questions, evidence_clauses, proposals = [], [], {}
    for i, clause in enumerate(clauses, 1):
        pool = (
            catalog.pool(clause.target, row=clause.row, column=clause.column)
            if clause.target
            else [catalog.recent_value_target()]
        )
        pool = [r for r in pool if "ocr.set_value" in catalog.targets[r]["allowedOperations"]]
        if not pool:
            raise PlanningIssue("target")
        if len(pool) > 252:
            return None
        value_intent = json.loads(clause.value_json)
        value = resolve_value(value_intent, {"current": instruction}, clause.quote)
        choices = []
        for ref in pool:
            choice = f"q{i}_{ref}"
            proposals[choice] = {
                "targetRef": ref,
                "operation": {
                    "op": "ocr.set_value",
                    "target": catalog.targets[ref]["handle"],
                    "value": value,
                },
            }
            description = catalog.label(ref)
            aliases = catalog.targets[ref].get("aliases") or []
            if aliases:
                description += "(別名: " + "、".join(aliases) + ")"
            if len(description) > 450:
                return None
            choices.append(
                {
                    "kind": "apply",
                    "value": choice,
                    "proposalRef": choice,
                    "description": description
                    + "の値を変更"
                    + ("(直前に保存した唯一のセル)" if not clause.target else ""),
                }
            )
        for sentinel, description in (
            ("clarify", "対象又は値が不足・複数候補なので質問"),
            ("responses", "候補では表せない複雑な変更・言い直し"),
            ("no_action", "変更の指示ではない"),
        ):
            choices.append({"kind": "sentinel", "value": sentinel, "description": description})
        evidence_clauses.append(
            {
                "clauseId": f"c{i}",
                "turnId": "current",
                "span": {"start": clause.start, "end": clause.end},
                "quote": clause.quote,
                "valueIntent": value_intent,
            }
        )
        questions.append({"name": f"q{i}", "clauseId": f"c{i}", "choices": choices})
    snapshot = {
        "kind": "candidate_snapshot",
        "contractVersion": "editing.v2",
        "candidateSetId": uuid.uuid4().hex,
        "bindingDigest": digest(catalog.scope),
        "policyVersion": policy.version,
        "catalogRevision": catalog.scope["catalogRevision"],
        "scopeDigest": catalog.scope["scopeDigest"],
        "coverageComplete": True,
        "clauses": evidence_clauses,
        "questions": questions,
    }
    return CandidateSet(canonical(snapshot), canonical(proposals))


def select_operations(
    candidates: CandidateSet,
    result: dict[str, Any],
    catalog: PlanningCatalog,
    clauses: tuple[Clause, ...],
    policy: PlannerPolicy,
) -> tuple[str, tuple[BoundOperation, ...], tuple[float, ...], tuple[float, ...]]:
    snapshot, proposals = (
        json.loads(candidates.snapshot_json),
        json.loads(candidates.proposals_json),
    )
    if (
        result.get("candidateSetId") != snapshot["candidateSetId"]
        or result.get("bindingDigest") != snapshot["bindingDigest"]
        or snapshot["bindingDigest"] != digest(catalog.scope)
    ):
        raise InvalidPlan("Candidate snapshot binding changed")
    answers = result.get("answers")
    questions = snapshot["questions"]
    if not isinstance(answers, list) or len(answers) != len(questions):
        raise InvalidPlan("Incomplete Decisions answers")
    operations, kinds, selected_probabilities, confidences = [], [], [], []
    pending: PlanningIssue | None = None
    for question, answer, clause in zip(questions, answers, clauses, strict=True):
        if answer.get("name") != question["name"]:
            raise InvalidPlan("Unexpected or duplicate Decisions question")
        if answer.get("type") == "refusal":
            raise InvalidPlan("Provider refused a Decisions question")
        if answer.get("type") != "choice":
            raise InvalidPlan("Unexpected Decisions answer type")
        supplied = {c["value"] for c in question["choices"]}
        probabilities = answer.get("probabilities", [])
        returned = [p.get("value") for p in probabilities]
        if len(returned) != len(set(returned)) or set(returned) != supplied:
            raise InvalidPlan("Probability set does not match the complete candidate pool")
        numbers = [p.get("probability") for p in probabilities] + [answer.get("confidence")]
        if any(
            isinstance(p, bool)
            or not isinstance(p, int | float)
            or not math.isfinite(p)
            or not 0 <= p <= 1
            for p in numbers
        ):
            raise InvalidPlan("Invalid probability")
        if abs(sum(numbers[:-1]) - 1) > 0.025:
            raise InvalidPlan("Probabilities are not normalized")
        choice = answer.get("choice")
        if not isinstance(choice, str) or choice not in supplied:
            raise InvalidPlan("Unissued choice")
        scores = {p["value"]: p["probability"] for p in probabilities}
        first, second = scores[choice], max(v for k, v in scores.items() if k != choice)
        if first < second:
            raise InvalidPlan("Selected choice is not the highest probability")
        selected_probabilities.append(first)
        confidences.append(answer["confidence"])
        kind = choice if choice in {"responses", "clarify", "no_action"} else "apply"
        kinds.append(kind)
        pool = [
            proposals[c["value"]]["targetRef"] for c in question["choices"] if c["kind"] == "apply"
        ]
        exact = kind == "apply" and (
            not clause.target
            or catalog.exact_address(proposals[choice]["targetRef"], clause.target, pool)
        )
        probability_floor = policy.exact_probability if exact else policy.probability
        confidence_floor = policy.exact_confidence if exact else policy.confidence
        if (
            first < probability_floor
            or first - second < policy.margin
            or answer["confidence"] < confidence_floor
        ):
            pending = PlanningIssue(
                "target", tuple(proposals[c]["targetRef"] for c in scores if c in proposals)
            )
        if kind == "apply":
            pool = [
                proposals[c["value"]]["targetRef"]
                for c in question["choices"]
                if c["kind"] == "apply"
            ]
            try:
                catalog.unambiguous(proposals[choice]["targetRef"], pool, clause.target)
            except PlanningIssue as issue:
                pending = issue
            operations.append(BoundOperation.from_value(proposals[choice]["operation"]))
    if pending:
        pending.probabilities = tuple(selected_probabilities)
        pending.confidences = tuple(confidences)
        raise pending
    # Only a confident, explicit complexity selection can trigger another request.
    if "responses" in kinds or ("no_action" in kinds and len(set(kinds)) > 1):
        return "responses", (), tuple(selected_probabilities), tuple(confidences)
    if pending or "clarify" in kinds:
        raise pending or PlanningIssue("target")
    if kinds == ["no_action"] * len(kinds):
        return "no_action", (), tuple(selected_probabilities), tuple(confidences)
    targets = [op.value()["target"] for op in operations]
    if len(targets) != len(set(targets)):
        raise PlanningIssue("operation")
    return "apply", tuple(operations), tuple(selected_probabilities), tuple(confidences)
