"""Compact projections of owned targets. No OCR binding or layout is regenerated."""

from __future__ import annotations

import copy
import re
import unicodedata
from typing import Any

from app.editing.domain.fast_path import (
    COLUMN,
    ROW,
    bare_row,
    compile_recent_edit,
    explicit_target,
)
from app.editing.domain.planning import InvalidPlan, PlanningIssue
from app.editing.domain.values import ordinal


def normalized(text: str) -> str:
    return unicodedata.normalize("NFKC", text).casefold().strip()


class PlanningCatalog:
    def __init__(self, context: dict[str, Any]) -> None:
        if context["catalog"].get("complete") is not True or not context["editable"]:
            raise InvalidPlan("Catalog is incomplete or not editable")
        self.scope = copy.deepcopy(context["scopeContext"])
        self.dialogue = copy.deepcopy(context.get("instructionDialogue"))
        self.turns = (
            {v["turnId"]: v["text"] for v in self.dialogue["turns"]} if self.dialogue else None
        )
        self.capabilities = set(context["capabilities"])
        self.targets = {
            f"r{i}": copy.deepcopy(t) for i, t in enumerate(context["catalog"]["targets"], 1)
        }
        self.handles = {t["handle"]: ref for ref, t in self.targets.items()}
        if len(self.handles) != len(self.targets):
            raise InvalidPlan("Duplicate owned handles")
        self.tables: dict[str, dict[str, Any]] = {}
        for ref, target in self.targets.items():
            cell = target.get("recordCell")
            if not cell:
                continue
            key = cell["tableId"]
            table = self.tables.setdefault(
                key,
                {
                    "ref": f"b{len(self.tables) + 1}",
                    "name": cell["tableName"],
                    "title": cell["tableTitle"],
                    "columns": {},
                    "cells": {},
                },
            )
            column = table["columns"].setdefault(
                cell["columnNumber"],
                {
                    "ref": table["ref"] + "c" + str(cell["columnNumber"]),
                    "number": cell["columnNumber"],
                    "name": cell["columnName"],
                    "id": cell["columnId"],
                    "aliases": set(),
                },
            )
            if column["id"] != cell["columnId"]:
                raise InvalidPlan("Duplicate column identity")
            column["aliases"].update(target["aliases"])
            coordinate = (cell["rowNumber"], cell["columnNumber"])
            if coordinate in table["cells"]:
                raise InvalidPlan("Duplicate physical cell")
            table["cells"][coordinate] = ref
        self.receipt = copy.deepcopy(context.get("lastReceipt"))
        self.recent_handles = tuple(context.get("recentTargetHandles", ()))
        # Receipt metadata cannot make stale history an edit target.
        if self.receipt and self.receipt["afterRevision"] != self.scope["resourceRevision"]:
            self.receipt = None

    def recent_targets(self) -> list[str]:
        focus = self.scope.get("currentFocus")
        if not self.receipt or not focus or focus["viewRevision"] != self.scope["resourceRevision"]:
            return []
        return [self.handles[h] for h in self.recent_handles if h in self.handles]

    def recent_value_target(self) -> str:
        matches = self.recent_targets()
        focus = self.scope.get("currentFocus") or {}
        if (
            len(matches) != 1
            or len(self.recent_handles) != 1
            or len(focus.get("anchors", [])) != 1
            or focus.get("contextRevision") != self.scope["contextRevision"]
            or focus.get("interactionEpoch") != self.scope["interactionEpoch"]
        ):
            raise PlanningIssue("target", tuple(matches))
        ref = matches[0]
        if (
            ref not in self.pool("")
            or "ocr.set_value" not in self.targets[ref]["allowedOperations"]
        ):
            raise PlanningIssue("target")
        if self.dialogue:
            # An unfinished edit to a named target has priority over older history.
            # Only another omitted-address correction may inherit this saved cell.
            original = (self.turns or {}).get("original", "")
            if compile_recent_edit(original) is None:
                raise PlanningIssue("target")
            established = self.dialogue.get("known", {}).get("operations", [])
            if any(v.get("target") != self.targets[ref]["handle"] for v in established):
                raise PlanningIssue("target")
        return ref

    def validate_value_address(self, ref: str, quote: str) -> None:
        """An answer may supply a row, but cannot contradict the original column."""
        mention = explicit_target(quote)
        if mention is None:
            return
        row, column = self.coordinates(mention)
        cell = self.targets[ref].get("recordCell") or {}
        pool = self.pool(
            mention, row=row or cell.get("rowNumber"), column=column, other_tables="include"
        )
        self.unambiguous(ref, pool, mention)

    def names(self, ref: str) -> set[str]:
        target = self.targets[ref]
        cell = target.get("recordCell")
        return {
            normalized(v)
            for v in [
                target["name"],
                *(target.get("aliases") or []),
                cell["columnName"] if cell else "",
                target.get("printedName") or "",
            ]
            if v
        }

    def table_for_ref(self, ref: str) -> str:
        matches = [key for key, table in self.tables.items() if table["ref"] == ref]
        if len(matches) != 1:
            raise InvalidPlan("Unissued table reference")
        return matches[0]

    def known_ref(self, ref: str, *, public: bool = False) -> bool:
        if ref in self.targets:
            return not public or not self.targets[ref].get("recordCell")
        return any(
            table["ref"] == ref or any(c["ref"] == ref for c in table["columns"].values())
            for table in self.tables.values()
        )

    def pool(
        self,
        mention: str,
        *,
        row: int | None = None,
        column: int | None = None,
        table_ref: str | None = None,
        table_mention: str | None = None,
        kind: str = "field",
        other_tables: str = "ask",
    ) -> list[str]:
        """Cells an address may mean.

        ``other_tables``: when the recent table has no cell of the named column, cells of
        that name in other tables are offered as a question ("ask"), or, for checking a
        choice the user already made from that question, included ("include").
        """
        # Explicit table names override history, but never a manual scope restriction.
        text = normalized(table_mention or mention)
        explicit = {
            key
            for key, table in self.tables.items()
            if any(normalized(name) in text for name in (table["name"], table["title"]) if name)
        }
        if table_ref:
            chosen = self.table_for_ref(table_ref)
            if explicit and chosen not in explicit:
                raise InvalidPlan("Table selector contradicts explicit table")
            if len(explicit) > 1:
                raise PlanningIssue("table")
            # A model-issued ref alone is not spoken table selection. Keep the
            # whole table pool when the user has only supplied row/column.
            if explicit or table_mention:
                explicit = {chosen}
        if len(explicit) > 1:
            raise PlanningIssue("table")
        selected = self.scope["selectedTableId"]
        if selected and explicit and selected not in explicit:
            raise PlanningIssue("scope", (self.tables[next(iter(explicit))]["ref"],))
        table_id = selected or next(iter(explicit), None) or self.scope["recentTableId"]
        result = self._pool(table_id, row, column, kind)
        if (
            kind == "field"
            and table_id
            and not selected
            and not explicit
            and not any(self.names(r) & self._mentioned(text) for r in result)
        ):
            # The recent table is only a preference for table cells. A name that matches
            # nothing in it but names an independent field (e.g. 名号 after a table edit)
            # selects that field; another table is still never switched to silently.
            mentioned = self._mentioned(text)
            independent = [
                r
                for r in self._pool(None, row, column, kind)
                if not self.targets[r].get("recordCell") and self.names(r) & mentioned
            ]
            if independent:
                result = independent
            else:
                # Another table is never switched to silently: its cells of that name become
                # the choices of a question (2026-10-22), not a pool the planner picks from.
                elsewhere = [
                    r
                    for r in self._pool(None, row, column, kind)
                    if (self.targets[r].get("recordCell") or {}).get("tableId") != table_id
                    and self.names(r) & mentioned
                ]
                if elsewhere and other_tables == "include":
                    result = elsewhere
                elif elsewhere:
                    raise PlanningIssue("target", tuple(elsewhere[:5]))
        return result

    def column_rows(self, mention: str) -> tuple[str, ...] | None:
        """Cells of one table column named without a row (体重を600にして), by row."""
        text = normalized(mention)
        pool = self.pool(mention)
        lengths = {
            r: max((len(n) for n in self.names(r) if n and n in text), default=0) for r in pool
        }
        longest = max(lengths.values(), default=0)
        named = [r for r, n in lengths.items() if longest and n == longest]
        cells = [self.targets[r].get("recordCell") for r in named]
        if (
            len(named) < 2
            or any(not c for c in cells)
            or len({(c["tableId"], c["columnName"]) for c in cells if c}) != 1
            or len({c["rowNumber"] for c in cells if c}) != len(named)
        ):
            return None
        return tuple(sorted(named, key=lambda r: self.targets[r]["recordCell"]["rowNumber"]))

    def unavailable_mention(self, mention: str) -> str | None:
        """The name of a field the user named exactly but which cannot be edited here."""
        text = normalized(mention)

        def longest(ref: str) -> int:
            return max((len(n) for n in self.names(ref) if n and n in text), default=0)

        available = max(
            (longest(r) for r, t in self.targets.items() if not t.get("unavailableReason")),
            default=0,
        )
        blocked = [
            (longest(r), t["name"])
            for r, t in self.targets.items()
            if t.get("unavailableReason") and t["kind"] == "field"
        ]
        best = max(blocked, default=(0, ""))
        return best[1] if best[0] > available else None

    def _mentioned(self, text: str) -> set[str]:
        return {name for r in self.targets for name in self.names(r) if name and name in text}

    def _pool(
        self, table_id: str | None, row: int | None, column: int | None, kind: str
    ) -> list[str]:
        result = []
        for ref, target in self.targets.items():
            if target["kind"] != kind or target.get("unavailableReason"):
                continue
            if (
                self.scope["mode"] == "local"
                and target["handle"] != self.scope["localTargetHandle"]
            ):
                continue
            cell = target.get("recordCell")
            if kind == "field":
                if table_id and (not cell or cell["tableId"] != table_id):
                    continue
                if row is not None and (not cell or cell["rowNumber"] != row):
                    continue
                if column is not None and (not cell or cell["columnNumber"] != column):
                    continue
            result.append(ref)
        return result

    def unambiguous(self, selected: str, pool: list[str], mention: str) -> None:
        if selected not in pool:
            raise InvalidPlan("Selected target is outside the complete structural pool")
        # Exact equal labels remain ambiguous even if a provider gives one a high score.
        matched_lengths = {
            r: max((len(name) for name in self.names(r) if name in normalized(mention)), default=0)
            for r in pool
        }
        longest = max(matched_lengths.values(), default=0)
        # A full name (受付番号) is more specific than component labels (番, 号).
        # Keep every equally specific match, including duplicated names in other tables.
        exact = [r for r, length in matched_lengths.items() if longest and length == longest]
        if exact and selected not in exact:
            raise InvalidPlan("Selected target contradicts an explicit owned name")
        if len(exact) > 1:
            raise PlanningIssue("target", tuple(exact))
        aliases = self.names(selected)
        duplicates = [r for r in pool if r != selected and aliases & self.names(r)]
        if not exact and duplicates:
            raise PlanningIssue("target", (selected, *duplicates))

    def coordinates(self, quote: str) -> tuple[int | None, int | None]:
        rows, cols = list(ROW.finditer(quote)), list(COLUMN.finditer(quote))
        if not rows and not cols:
            bare = bare_row(explicit_target(quote) or quote)
            if bare:
                return bare[1], None
        if len(rows) > 1 or len(cols) > 1:
            raise PlanningIssue("target")
        if any(re.search(r"[-\u2212.万億兆]$", quote[: m.start()].strip()) for m in [*rows, *cols]):
            raise PlanningIssue("row")
        row, col = ordinal(rows[0][1]) if rows else None, ordinal(cols[0][1]) if cols else None
        if (rows and row is None) or (cols and col is None):
            raise PlanningIssue("row")
        return row, col

    def exact_address(self, ref: str, mention: str, pool: list[str]) -> bool:
        row, column = self.coordinates(mention)
        if row is not None and column is not None and pool == [ref]:
            return True
        bare = bare_row(mention)
        text = ROW.sub("", bare[0] if bare else mention)
        for table in self.tables.values():
            for name in sorted({table["name"], table["title"]}, key=len, reverse=True):
                if name:
                    text = text.removeprefix(name + "の")
        text = text.strip().removeprefix("の").strip()
        for suffix in ("の欄", "のセル", "の項目"):
            text = text.removesuffix(suffix)
        matches = [r for r in pool if normalized(text) in self.names(r)]
        return matches == [ref]

    def public(self) -> dict[str, Any]:
        targets = []
        operation_sets: dict[tuple[str, ...], str] = {}
        for ref, target in self.targets.items():
            if target.get("recordCell"):
                continue
            allowed = tuple(target["allowedOperations"])
            operation_ref = operation_sets.setdefault(allowed, f"o{len(operation_sets) + 1}")
            item = {
                "ref": ref,
                "kind": target["kind"],
                "name": target["name"],
                "operations": operation_ref,
            }
            if target["aliases"]:
                item["aliases"] = target["aliases"]
            for key in (
                "role",
                "headingPath",
                "position",
                "size",
                "sheetCell",
                "textSpillsRight",
                "order",
                "printedName",
                "virtual",
                "recognitionMode",
                "selectionMode",
                "optionValue",
                "rowKeyValue",
                "mappingType",
            ):
                if target.get(key) is not None and target[key] != []:
                    if key == "printedName" and target[key] == target["name"]:
                        continue
                    item[key] = target[key]
            for key in (
                "memberHandles",
                "ownerHandle",
                "meaningHandle",
                "parentHandle",
                "rowKeyHandle",
            ):
                value = target.get(key)
                if value is not None:
                    item[key.replace("Handles", "Refs").replace("Handle", "Ref")] = (
                        [self.handles[x] for x in value]
                        if isinstance(value, list)
                        else self.handles[value]
                    )
            targets.append(item)
        tables = []
        for table in self.tables.values():
            columns = [
                {k: sorted(v) if isinstance(v, set) else v for k, v in c.items() if k != "id"}
                for _, c in sorted(table["columns"].items())
            ]
            rows = sorted({r for r, _ in table["cells"]})
            # Sparse rows explicitly list available columns. Missing cells are never invented.
            tables.append(
                {
                    "ref": table["ref"],
                    "name": table["name"],
                    "columns": columns,
                    "rows": [
                        {"number": r, "columns": sorted(c for rr, c in table["cells"] if rr == r)}
                        for r in rows
                    ],
                }
            )
        return {
            "targets": targets,
            "operationSets": {ref: list(ops) for ops, ref in operation_sets.items()},
            "tables": tables,
            "capabilities": sorted(self.capabilities),
            "selected": [
                self.handles[h] for h in self.scope["selectedHandles"] if h in self.handles
            ],
            "selectedTable": next(
                (v["ref"] for k, v in self.tables.items() if k == self.scope["selectedTableId"]),
                None,
            ),
            "recentTable": next(
                (v["ref"] for k, v in self.tables.items() if k == self.scope["recentTableId"]), None
            ),
            "recentTargets": self.recent_targets(),
            "canUndo": bool(self.receipt and self.receipt["canUndo"]),
            "mode": self.scope["mode"],
            "complete": True,
        }

    def label(self, ref: str) -> str:
        for table in self.tables.values():
            if ref == table["ref"]:
                return str(table["name"])
            for column in table["columns"].values():
                if ref == column["ref"]:
                    return f"{table['name']}・{column['number']}列目 {column['name']}"
        target = self.targets[ref]
        cell = target.get("recordCell")
        return (
            f"{cell['tableName']}・{cell['rowNumber']}行目・"
            f"{cell['columnNumber']}列目 {cell['columnName']}"
            if cell
            else target["name"] or "欄"
        )
