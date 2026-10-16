"""Validate model-only plans, then bind finite selectors to shared business operations."""

from __future__ import annotations

import copy
from typing import Any

from jsonschema import Draft202012Validator  # type: ignore[import-untyped]
from pydantic import TypeAdapter, ValidationError

from app.editing.domain.command import BoundOperation, ResolvedInstruction, canonical
from app.editing.domain.fast_path import compile_fast, compile_recent_edit
from app.editing.domain.plan_conflicts import reject_competing_writes
from app.editing.domain.planning import InvalidPlan, PlanningIssue, check_evidence
from app.editing.domain.planning_catalog import PlanningCatalog, normalized
from app.editing.domain.values import resolve_value
from app.editing.infrastructure.planner_schema import planner_schema
from app.editing.operation_contracts import Operation


class PlanBinder:
    def __init__(self) -> None:
        self.validator = Draft202012Validator(planner_schema())
        self.operations = TypeAdapter(list[Operation])

    def selector(
        self, selector: dict[str, Any], turns: dict[str, str], catalog: PlanningCatalog
    ) -> str:
        address = selector["address"]
        quote = check_evidence(address["evidence"], turns)
        row, column = catalog.coordinates(quote)
        kind = address["kind"]
        if kind == "recent_target":
            if compile_recent_edit(quote):
                return catalog.recent_value_target()
            if not any(
                word in quote for word in ("そこ", "それ", "今の", "違う", "さっき", "直前")
            ):
                raise InvalidPlan("Recent target has no deictic evidence")
            matches = catalog.recent_targets()
            if len(matches) != 1:
                raise PlanningIssue("target", tuple(matches))
            return matches[0]
        if kind == "record_cell":
            if address["tableMention"]:
                from app.editing.domain.fast_path import ROW

                coordinate = ROW.search(quote)
                prefix = quote[: coordinate.start()].strip() if coordinate else ""
                if address["tableMention"] not in prefix:
                    raise InvalidPlan("Table mention is not supported by its coordinate phrase")
            if row is not None and row != address["row"]:
                raise InvalidPlan("Row contradicts spoken coordinate")
            # A row must actually have been supplied, not guessed from occupied cells.
            if row is None:
                pending = (catalog.dialogue or {}).get("known", {}).get("unresolved", [])
                if any(v.get("row") == address["row"] for v in pending):
                    row = address["row"]
                else:
                    raise PlanningIssue("row")
            col = address["column"]
            col_index = col["index"] if col["kind"] == "index" else column
            if column is not None and col_index != column:
                raise InvalidPlan("Column contradicts spoken coordinate")
            if col["kind"] == "index" and column is None:
                raise InvalidPlan("Generated column index")
            pool = catalog.pool(
                quote,
                row=row,
                column=col_index,
                table_ref=address["tableRef"],
                table_mention=address["tableMention"],
            )
            if col["kind"] == "catalog":
                column_refs = {
                    c["ref"]: (key, c["number"])
                    for key, t in catalog.tables.items()
                    for c in t["columns"].values()
                }
                if col["ref"] not in column_refs:
                    raise InvalidPlan("Unissued column reference")
                table_id, number = column_refs[col["ref"]]
                matches = [
                    r
                    for r in pool
                    if catalog.targets[r]["recordCell"]["tableId"] == table_id
                    and catalog.targets[r]["recordCell"]["columnNumber"] == number
                ]
            elif col["kind"] == "name":
                if col["name"] not in quote:
                    # Recognition correction is represented by a catalog column ref.
                    raise InvalidPlan("Column name has no quoted evidence")
                matches = [r for r in pool if normalized(col["name"]) in catalog.names(r)]
            else:
                matches = pool
            if len(matches) != 1:
                raise PlanningIssue("column" if not matches else "table", tuple(matches))
            catalog.unambiguous(matches[0], pool, quote)
            return matches[0]
        if kind == "catalog":
            ref = address["ref"]
            if ref not in catalog.targets or not catalog.known_ref(ref, public=True):
                raise InvalidPlan("Unissued target reference")
            target_kind = catalog.targets[ref]["kind"]
            pool = catalog.pool(quote, row=row, column=column, kind=target_kind)
            if "選択" in quote or quote.strip() in {"この欄", "このセル"}:
                selected = catalog.scope["selectedHandles"]
                pool = [r for r in pool if catalog.targets[r]["handle"] in selected]
            catalog.unambiguous(ref, pool, quote)
            if catalog.targets[ref].get("recordCell") and row is None:
                raise PlanningIssue("row")
            return str(ref)
        # Query selectors are exact after language interpretation. Approximate interpretation
        # uses an issued catalog ref; this is not the old string-similarity gate.
        if address["mention"] not in quote:
            raise InvalidPlan("Query mention was not spoken")
        pool = catalog.pool(quote, row=row, column=column, kind=address["targetKind"])
        if address["pageRef"]:
            page = catalog.targets.get(address["pageRef"])
            if not page or page["kind"] != "page":
                raise InvalidPlan("Unissued page reference")
            pool = [r for r in pool if catalog.targets[r]["pageId"] == page["pageId"]]
        if address["heading"]:
            if address["heading"] not in quote:
                raise InvalidPlan("Heading was not spoken")
            pool = [
                r
                for r in pool
                if address["heading"] in (catalog.targets[r].get("headingPath") or [])
            ]
        matches = [r for r in pool if normalized(address["mention"]) in catalog.names(r)]
        if address["position"] and matches:
            word = {"left": "左", "right": "右", "top": "上", "bottom": "下"}[address["position"]]
            if word not in quote:
                raise InvalidPlan("Position was not spoken")
            axis = "x" if address["position"] in {"left", "right"} else "y"
            positions = {r: catalog.targets[r].get("position", {}).get(axis) for r in matches}
            if any(v is None for v in positions.values()):
                raise PlanningIssue("target", tuple(matches))
            edge = (max if address["position"] in {"right", "bottom"} else min)(positions.values())
            matches = [r for r, v in positions.items() if v == edge]
        if len(matches) != 1:
            raise PlanningIssue("target", tuple(matches))
        return matches[0]

    @staticmethod
    def replace_refs(value: Any, selectors: dict[str, str], catalog: PlanningCatalog) -> Any:
        if isinstance(value, dict):
            if set(value) == {"targetRef"}:
                ref = selectors.get(value["targetRef"])
                if ref is None:
                    raise InvalidPlan("Unknown selector")
                return catalog.targets[ref]["handle"]
            return {k: PlanBinder.replace_refs(v, selectors, catalog) for k, v in value.items()}
        if isinstance(value, list):
            return [PlanBinder.replace_refs(v, selectors, catalog) for v in value]
        return value

    @staticmethod
    def names_have_evidence(value: Any, quote: str) -> None:
        if isinstance(value, list):
            for item in value:
                PlanBinder.names_have_evidence(item, quote)
        elif isinstance(value, dict):
            for key, item in value.items():
                if key in {"name", "label", "prefix", "suffix", "rowKeyValue", "groupLabel"}:
                    if item and item not in quote:
                        raise InvalidPlan("New text has no instruction evidence")
                elif key == "aliases":
                    if any(alias not in quote for alias in item):
                        raise InvalidPlan("Alias was invented")
                else:
                    PlanBinder.names_have_evidence(item, quote)

    @staticmethod
    def selector_evidence(value: Any, quote: str, quotes: dict[str, str]) -> None:
        if isinstance(value, dict):
            if "targetRef" in value and quotes.get(value["targetRef"], "\x00") not in quote:
                raise InvalidPlan("Selector evidence belongs to another operation")
            for child in value.values():
                PlanBinder.selector_evidence(child, quote, quotes)
        elif isinstance(value, list):
            for child in value:
                PlanBinder.selector_evidence(child, quote, quotes)

    def bind(
        self, plan: dict[str, Any], instruction: str, catalog: PlanningCatalog
    ) -> ResolvedInstruction:
        if next(self.validator.iter_errors(plan), None) is not None:
            raise InvalidPlan("Model plan violates the closed schema")
        decision = plan["decision"]
        turns = catalog.turns or {"current": instruction}
        if decision["kind"] == "no_action":
            return ResolvedInstruction(no_action=True)
        if decision["kind"] == "clarify":
            first = decision["missing"][0]
            if not any(first["focusQuote"] in text for text in turns.values()):
                raise InvalidPlan("Question does not refer to this instruction")
            if any(not catalog.known_ref(r, public=True) for r in first["candidateRefs"]):
                raise InvalidPlan("Unissued clarification choice")
            raise PlanningIssue(first["kind"], tuple(first["candidateRefs"]))
        if decision["kind"] == "undo_previous":
            quote = check_evidence(decision["evidence"], turns)
            if not any(word in quote for word in ("取り消", "取消", "戻して", "元に戻")):
                raise PlanningIssue("operation")
            if not catalog.receipt or not catalog.receipt["canUndo"]:
                raise PlanningIssue("operation")
            return ResolvedInstruction(undo_operation_id=catalog.receipt["operationId"])
        if decision["kind"] == "retarget_previous":
            check_evidence(decision["evidence"], turns)
            ref = self.selector(decision["selector"], turns, catalog)
            if not catalog.receipt or not catalog.receipt["canUndo"]:
                raise PlanningIssue("operation")
            return ResolvedInstruction(
                retarget_operation_id=catalog.receipt["operationId"],
                retarget_handle=catalog.targets[ref]["handle"],
            )
        change = decision["change"]
        raw_operations, evidences = change["operations"], change["operationEvidence"]
        if len(raw_operations) != len(evidences):
            raise InvalidPlan("Evidence count does not match operations")
        selectors, quotes = {}, {}
        for selector in change["selectors"]:
            if selector["id"] in selectors:
                raise InvalidPlan("Duplicate selector")
            selectors[selector["id"]] = self.selector(selector, turns, catalog)
            quotes[selector["id"]] = check_evidence(selector["address"]["evidence"], turns)
        operations, assigned = [], set()
        for raw, evidence in zip(raw_operations, evidences, strict=True):
            quote = check_evidence(evidence, turns)
            if raw["op"] not in catalog.capabilities:
                raise InvalidPlan("Operation is outside resource capabilities")
            if (
                raw["op"] == "cell.set_reading"
                and raw["reading"] == "sequence"
                and not any(word in quote for word in ("自動採番", "連番", "通し番号"))
            ):
                raise InvalidPlan("Number transcription is not automatic sequencing")
            self.names_have_evidence(raw, quote)
            if not catalog.dialogue:
                self.selector_evidence(raw, quote, quotes)
            operation = self.replace_refs(raw, selectors, catalog)
            if raw["op"] == "ocr.set_value":
                selector_id = raw["target"]["targetRef"]
                if quotes[selector_id] not in quote and not catalog.dialogue:
                    raise InvalidPlan("Target evidence belongs to another operation")
                operation["value"] = resolve_value(raw["value"], turns, quote)
                ref = catalog.handles[operation["target"]]
                catalog.validate_value_address(ref, quote)
                if catalog.dialogue:
                    original_row, original_column = catalog.coordinates(quote)
                    target = catalog.targets[catalog.handles[operation["target"]]]
                    record = target.get("recordCell") or {}
                    if (original_row and record.get("rowNumber") != original_row) or (
                        original_column and record.get("columnNumber") != original_column
                    ):
                        raise InvalidPlan("An answer transferred a value to another coordinate")
                fast = compile_fast(quote)
                if fast and len(fast) != 1:
                    raise InvalidPlan("Each value operation needs its own clause")
                if fast:
                    import json

                    expected = resolve_value(
                        json.loads(fast[0].value_json), {"current": quote}, quote
                    )
                    if operation["value"] != expected:
                        raise InvalidPlan("Value belongs to another clause")
                if operation["target"] in assigned:
                    raise PlanningIssue("operation")
                assigned.add(operation["target"])
            operations.append(operation)
        reject_competing_writes(operations)
        try:
            validated = self.operations.validate_python(operations)
        except ValidationError as error:
            raise InvalidPlan("Invalid shared operation") from error
        return ResolvedInstruction(
            tuple(
                BoundOperation.from_value(v.model_dump(mode="json", by_alias=True))
                for v in validated
            ),
            partial_plan_json=canonical(copy.deepcopy(plan)),
        )
