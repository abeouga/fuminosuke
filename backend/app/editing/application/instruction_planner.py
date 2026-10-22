"""Decisions once, or one complete Responses plan; never a multi-tool model loop."""

from __future__ import annotations

import json
import time
from collections import OrderedDict
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from app.core.errors import AppError
from app.editing.application import column_rewrite
from app.editing.application.ports import DecisionSelector, PlanBinding, TextPlanner
from app.editing.domain.candidates import build_candidates, select_operations
from app.editing.domain.command import ResolvedInstruction, canonical, digest
from app.editing.domain.fast_path import compile_fast, compile_recent_edit
from app.editing.domain.planning import InvalidPlan, PlannerPolicy, PlanningIssue, PlanningTrace
from app.editing.domain.planning_catalog import PlanningCatalog
from app.editing.errors import reject

QUESTIONS = {
    "target": "どの欄を変更しますか。",
    "table": "どの表を変更しますか。",
    "row": "何行目を変更しますか。",
    "column": "どの列を変更しますか。",
    "value": "どの内容に変更しますか。",
    "meaning": "どの意味を使いますか。",
    "name": "どの名前に変更しますか。",
    "operation": "どの変更を行いますか。",
    "scope": "現在選択している表の外です。対象範囲を切り替えますか。",
}


def row_column(catalog: PlanningCatalog | None, refs: list[str]) -> str | None:
    cells = [(catalog.targets.get(r) or {}).get("recordCell") for r in refs] if catalog else []
    names = {c["columnName"] for c in cells if c}
    return next(iter(names)) if len(names) == 1 and all(cells) and cells else None


class InstructionPlanner:
    def __init__(
        self,
        decisions: DecisionSelector,
        responses: TextPlanner,
        binder: PlanBinding,
        policy: PlannerPolicy | None = None,
        trace: Callable[[PlanningTrace], None] | None = None,
    ) -> None:
        self.decisions, self.responses, self.binder = decisions, responses, binder
        self.policy = policy or PlannerPolicy()
        self.trace = trace
        self.catalogs: OrderedDict[str, PlanningCatalog] = OrderedDict()

    def catalog(self, context: dict[str, Any]) -> PlanningCatalog:
        key = digest(
            [
                context["scopeContext"],
                context.get("lastReceipt"),
                context.get("recentTargetHandles"),
                context.get("instructionDialogue"),
            ]
        )
        if key not in self.catalogs:
            self.catalogs[key] = PlanningCatalog(context)
            if len(self.catalogs) > 32:
                self.catalogs.popitem(last=False)
        self.catalogs.move_to_end(key)
        return self.catalogs[key]

    async def resolve(self, instruction: str, context: dict[str, Any]) -> ResolvedInstruction:
        """Plain plan, or a checked rewrite of existing values (column_rewrite).

        Rewrite-like wording goes to the rewrite first; a plain plan that fails or asks
        without any choice gets one rewrite attempt before giving up.
        """
        try:
            catalog = self.catalog(context)
        except InvalidPlan:
            return await self._resolve(instruction, context, "plain")
        usable = hasattr(self.responses, "rewrite") and column_rewrite.applicable(catalog)
        text = column_rewrite.instruction_text(instruction, catalog)
        if usable and column_rewrite.HINT.search(text):
            return await self._resolve(instruction, context, "rewrite_first")
        try:
            result = await self._resolve(instruction, context, "plain")
        except AppError as error:
            if not usable or error.code != "PLANNER_PLAN_INVALID":
                raise
            try:
                return await self._resolve(instruction, context, "rewrite_only")
            except AppError:
                raise error from None
        if usable and result.question_json and not json.loads(result.question_json)["choices"]:
            try:
                return await self._resolve(instruction, context, "rewrite_only")
            except AppError:
                return result
        return result

    async def _rewrite(
        self, instruction: str, catalog: PlanningCatalog
    ) -> ResolvedInstruction | None:
        payload = column_rewrite.payload(
            column_rewrite.instruction_text(instruction, catalog), catalog
        )
        if len(canonical(payload).encode("utf-8")) // 2 > self.policy.catalog_tokens:
            return None
        answer = await self.responses.rewrite(payload)  # type: ignore[attr-defined]
        return column_rewrite.resolve(answer, catalog)

    async def _resolve(
        self, instruction: str, context: dict[str, Any], mode: str
    ) -> ResolvedInstruction:
        catalog: PlanningCatalog | None = None
        plan = None
        route, decision_ms, response_ms = "responses", 0, 0
        counts: tuple[int, ...] = ()
        probabilities: tuple[float, ...] = ()
        confidences: tuple[float, ...] = ()
        try:
            if not instruction.strip() or len(instruction) > 20000:
                raise InvalidPlan("Instruction size is invalid")
            catalog = self.catalog(context)
            if mode != "plain":
                route = "rewrite"
                rewritten = await self._rewrite(instruction, catalog)
                if rewritten is not None:
                    return rewritten
                if mode == "rewrite_only":
                    raise InvalidPlan("Not a rewrite of existing values")
                route = "responses"
            clauses = (
                compile_fast(instruction)
                if (
                    "ocr.set_value" in catalog.capabilities
                    and (
                        not catalog.dialogue
                        or (
                            compile_recent_edit(instruction)
                            and compile_recent_edit((catalog.turns or {}).get("original", ""))
                        )
                    )
                    and self.policy.route == "decisions_first"
                )
                else None
            )
            if clauses:
                for clause in clauses:
                    blocked = clause.target and catalog.unavailable_mention(clause.target)
                    if clause.target and clause.row is None and clause.column is None:
                        # A column named without its row: ask which row, no model call.
                        rows = catalog.column_rows(clause.target)
                        if rows:
                            raise PlanningIssue("row", rows)
                    if blocked:
                        raise reject(
                            "TARGET_UNAVAILABLE",
                            f"「{blocked}」はこの書類では音声やAIで変更できない欄です。"
                            "画面で直接修正してください。",
                            409,
                        )
                candidates = build_candidates(instruction, clauses, catalog, self.policy)
                if candidates is not None:
                    route = "decisions"
                    counts = tuple(
                        len(q["choices"]) for q in json.loads(candidates.snapshot_json)["questions"]
                    )
                    start = time.perf_counter()
                    result = None
                    try:
                        result = await self.decisions.choose(instruction, candidates)
                    except AppError as error:
                        # A timed-out short route falls back to the full planner instead
                        # of dropping a spoken instruction.
                        if error.code != "PLANNER_TIMEOUT":
                            raise
                    finally:
                        decision_ms = round((time.perf_counter() - start) * 1000)
                    if result is not None:
                        action, operations, probabilities, confidences = select_operations(
                            candidates, result, catalog, clauses, self.policy
                        )
                        if action == "apply":
                            return ResolvedInstruction(operations)
                        if action == "no_action":
                            return ResolvedInstruction(no_action=True)
                    route = "decisions+responses"
            public = catalog.public()
            if catalog.dialogue:
                public["instructionDialogue"] = catalog.dialogue
            if len(canonical(public).encode("utf-8")) // 2 > self.policy.catalog_tokens:
                raise PlanningIssue("scope")
            start = time.perf_counter()
            try:
                plan = await self.responses.plan(instruction, public)
            finally:
                response_ms = round((time.perf_counter() - start) * 1000)
            return self.binder.bind(plan, instruction, catalog)
        except PlanningIssue as issue:
            probabilities = issue.probabilities or probabilities
            confidences = issue.confidences or confidences
            choice_refs = [r for r in issue.candidates if catalog and catalog.known_ref(r)]
            if not choice_refs and catalog and issue.kind in {"target", "column"}:
                # Never a question with only "cancel": offer the fields the words name.
                choice_refs = column_rewrite.suggested_refs(
                    column_rewrite.instruction_text(instruction, catalog), catalog
                )
            established: list[dict[str, Any]] = []
            change = (plan or {}).get("decision", {}).get("knownChange")
            if change and catalog:
                # Only independently validated portions of the supplied partial plan
                # may become protected bindings; the model's summary has no authority.
                for index, operation in enumerate(change["operations"]):
                    fragment = {
                        **change,
                        "operations": [operation],
                        "operationEvidence": [change["operationEvidence"][index]],
                    }
                    try:
                        bound = self.binder.bind(
                            {"decision": {"kind": "change", "change": fragment}},
                            instruction,
                            catalog,
                        )
                        established.extend(v.value() for v in bound.operations)
                    except (InvalidPlan, PlanningIssue):
                        pass
            question = {
                "questionId": "q_" + uuid4().hex,
                "version": 1,
                "missingKind": issue.kind,
                # One candidate is a confirmation, not a choice between fields.
                "message": f"「{catalog.label(choice_refs[0])[:60]}」を変更してよいですか。"
                "よければ欄名を押してください。"
                if issue.kind == "target" and catalog and len(choice_refs) == 1
                else f"何行目の{column_name}ですか。「3行目」のように話すか、選んでください。"
                if issue.kind == "row" and (column_name := row_column(catalog, choice_refs))
                else QUESTIONS[issue.kind],
                "choices": [
                    {
                        "choiceId": "c_" + uuid4().hex,
                        "label": catalog.label(r)[:200],
                        "handle": (catalog.targets.get(r) or {}).get("handle"),
                    }
                    for r in choice_refs[: 12 if issue.kind == "row" else 5]
                ]
                if catalog
                else [],
                "expiresAt": (datetime.now(UTC) + timedelta(seconds=300)).isoformat(),
            }
            # Private continuation data is never placed in the persisted question event.
            scope_proposal = None
            if (
                issue.kind == "scope"
                and catalog
                and catalog.scope["selectedTableId"]
                and len(choice_refs) <= 1
            ):
                proposed_table = next(
                    (
                        key
                        for key, table in catalog.tables.items()
                        if choice_refs and table["ref"] == choice_refs[0]
                    ),
                    None,
                )
                scope_proposal = {"selectedTableId": proposed_table}
            return ResolvedInstruction(
                question_json=canonical(question),
                partial_plan_json=canonical(
                    {
                        "instruction": instruction,
                        "plan": plan,
                        "missingKind": issue.kind,
                        "candidateRefs": choice_refs,
                        "binding": catalog.scope if catalog else None,
                        "knownOperations": established,
                        "scopeProposal": scope_proposal,
                    }
                ),
            )
        except InvalidPlan as error:
            raise reject(
                "PLANNER_PLAN_INVALID",
                "指示をうまく解釈できませんでした。何も変更していません。"
                "「3行目の体重を500にして」のように、行・欄名・値を続けて話してください。",
                422,
            ) from error
        finally:
            if self.trace:
                self.trace(
                    PlanningTrace(
                        route, decision_ms, response_ms, counts, probabilities, confidences
                    )
                )
