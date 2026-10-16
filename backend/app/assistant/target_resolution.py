"""Deterministic ambiguity checks around model-selected finite handles."""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher
from typing import Any

from app.assistant.record_selection import RecordSelectors, value_matches
from app.editing.contracts import TargetSearch
from app.editing.errors import reject
from app.editing.record_cells import matches_location


def normalized(value: str) -> str:
    return unicodedata.normalize("NFKC", value).casefold().strip()


def names(target: dict[str, Any]) -> set[str]:
    return {
        normalized(value)
        for value in (
            target["name"], *target.get("aliases", []), target.get("printedName") or "",
            (target.get("recordCell") or {}).get("columnName", ""),
        )
        if value
    }


def close_score(value: str, name: str) -> float:
    if min(len(value), len(name)) < 2:
        return 0
    # Two-character names need a character in the same position. Sharing a
    # character at unrelated positions (名古 / 牛名) is not a useful suggestion.
    if len(value) == len(name) == 2 and not any(a == b for a, b in zip(value, name, strict=True)):
        return 0
    return SequenceMatcher(None, value, name).ratio()


class TargetResolver:
    def __init__(
        self, targets: list[dict[str, Any]], context: dict[str, Any], text: str,
        *, target_answer: str | None = None,
        clarifications: list[tuple[str, str]] | None = None,
    ) -> None:
        self.targets = {t["handle"]: t for t in targets}
        self.text = normalized(text)
        self.target_answer = normalized(target_answer) if target_answer else None
        self.local = context["mode"] == "local"
        self.selected = set(context["selectedHandles"])
        self.seen = {t["handle"] for t in context["catalog"]["targets"]}
        self.implicit = set(context["selectedHandles"])
        self.implicit.update([context["recentEdit"]["target"]] if context.get("recentEdit") else [])
        self.resolved = self.implicit.copy()
        self.seen.update(self.resolved)
        self.name_matches: dict[tuple[str, str], set[str]] = {}
        self.searched: set[str] = set()
        self.records = RecordSelectors(targets, context, text, target_answer, clarifications)
        self.recent_edit = context.get("recentEdit")

    def spoken_names(self) -> list[str]:
        return re.findall(
            r"(?:^|[、。\s])([^、。\s]{2,40}?)(?:という(?:入力)?欄|の欄|を|は|:)", self.text
        )

    def names_query(self, query: str) -> bool:
        value = normalized(query)
        if not value or value not in self.text:
            return False
        if self.target_answer and value in self.target_answer:
            # This answer belongs to a live, same-context target question.
            # A name-only answer replaces the misheard name, not its value.
            answer_names = re.findall(
                r"(?:^|[、。\s])([^、。\s]{2,40}?)(?:という(?:入力)?欄|の欄|を|は|:)",
                self.target_answer,
            )
            if not answer_names or any(value in mention for mention in answer_names):
                return True
        mentions = self.spoken_names()
        if not mentions or any(value in mention for mention in mentions):
            return True
        # A target correction may name the replacement after the old target,
        # without を/は. Do not treat the new field value as a target name.
        return any(
            value in tail for tail in re.split(r"(?:ではなく|でなく|じゃなく)", self.text)[1:]
        )

    def nearby(self, query: TargetSearch) -> list[dict[str, Any]]:
        """Return every eligible near name up to the display limit, not just the best."""
        value = normalized(query.query)
        if len(value) < 2:
            return []
        candidates = []
        for target in self.targets.values():
            if self.local and target["handle"] not in self.selected:
                continue
            if query.kinds and target["kind"] not in query.kinds:
                continue
            if query.role and target.get("role") != query.role:
                continue
            if query.heading and query.heading not in (target.get("headingPath") or []):
                continue
            if not matches_location(target, query):
                continue
            score = max((close_score(value, name) for name in names(target)), default=0)
            threshold = 0.5 if len(value) == 2 else 0.66
            if score >= threshold:
                candidates.append((score, target))
        # Apply the spoken location to the complete matching set, before the
        # display limit; a sixth/later candidate can own the extreme position.
        if query.position:
            axis = "y" if query.position in {"top", "bottom"} else "x"
            candidates = [item for item in candidates if item[1].get("position")]
            if candidates:
                extreme = (min if query.position in {"top", "left"} else max)(
                    item[1]["position"][axis] for item in candidates
                )
                candidates = [
                    item for item in candidates if item[1]["position"][axis] == extreme
                ]
        candidates.sort(key=lambda item: (-item[0], item[1]["name"], item[1]["handle"]))
        return [target for _, target in candidates[:5]]

    def prepare_search(self, query: TargetSearch) -> TargetSearch:
        query = self.records.prepare(query)
        required = {
            "top": ("上", "最初"),
            "bottom": ("下", "最後"),
            "left": ("左",),
            "right": ("右",),
        }
        if query.position and not any(word in self.text for word in required[query.position]):
            raise reject(
                "TARGET_AMBIGUOUS", "位置の指定がありません。対象の位置を指定してください。"
            )
        if query.heading and normalized(query.heading) not in self.text:
            raise reject("TARGET_AMBIGUOUS", "どの見出しの欄か指定してください。")
        if query.row is not None and not self.records.selections and not re.search(
            rf"(?:第)?{query.row}\s*行", self.text
        ):
            raise reject("TARGET_AMBIGUOUS", "何行目の欄か指定してください。")
        if not self.records.selections and (
            query.column is not None or query.table_id or query.table_name
        ):
            raise reject("TARGET_AMBIGUOUS", "表・行・列の指定がありません。")
        return query

    def observe(self, result: dict[str, Any], query: TargetSearch) -> None:
        self.seen.update(t["handle"] for t in result["targets"])
        # A unique alias/name or an explicitly spoken location may resolve ties.
        # Pagination never turns a multi-candidate result into a unique match.
        if result["total"] == 1 and result["targets"]:
            candidate = result["targets"][0]
            named = self.names_query(query.query)
            located = not query.query and (
                query.position or query.heading or query.row or query.column
            )
            if named or located:
                self.resolved.add(candidate["handle"])
                self.searched.add(candidate["handle"])
                if named:
                    self.name_matches[(normalized(query.query), candidate["kind"])] = {
                        candidate["handle"]
                    }

    def value_source(self, handle: str) -> str | None:
        if not self.records.selections:
            return None
        indices = self._record_handle(handle)
        values = {
            self.records.selections[index].value for index in indices
            if self.records.selections[index].value_known
        }
        if len(values) == 1:
            value = next(iter(values))
            return f"値を{value}にして" if value is not None else None
        return None

    def check(self, value: Any, *, reference: bool = False) -> None:
        if self.records.problem:
            raise self.records.problem
        if isinstance(value, dict) and "operations" in value and self.records.selections:
            covered: set[int] = set()
            for operation in value["operations"]:
                if operation.get("op") != "ocr.set_value":
                    raise reject("TARGET_AMBIGUOUS", "表の値を変更する操作を指定してください。")
                indices = self._record_handle(operation["target"])
                bound = {
                    index for index in indices
                    if self.records.selections[index].value_known
                    and value_matches(self.records.selections[index].value, operation.get("value"))
                }
                if (not bound and value.get("correction") and self.recent_edit
                    and operation.get("value") == self.recent_edit["value"]):
                    # The saved recent receipt, not a model value, is authority
                    # for a target-only correction. Persistence checks it again.
                    bound = {index for index in indices
                             if not self.records.selections[index].value_known}
                if not bound:
                    raise reject("VALUE_AMBIGUOUS", "指定された欄と変更後の値が一致しません。")
                covered.update(bound)
            if covered != set(range(len(self.records.selections))):
                raise reject("TARGET_AMBIGUOUS", "指定された行と列がすべて特定できていません。")
        if isinstance(value, list):
            for item in value:
                self.check(item, reference=reference)
        elif isinstance(value, dict):
            references = {
                "target",
                "cell",
                "cells",
                "meaning",
                "inputCells",
                "labelCells",
                "relations",
                "heading",
                "children",
                "anchor",
                "page",
                "section",
                "group",
                "rowKeyCell",
                "columnLabels",
                "set",
                "option",
                "region",
                "field",
                "fields",
            }
            for key, item in value.items():
                self.check(item, reference=key in references)
        elif reference and isinstance(value, str) and value.startswith("t_"):
            self._handle(value)

    def _handle(self, handle: str) -> None:
        if handle not in self.seen or handle not in self.targets:
            raise reject("TARGET_STALE", "会話で取得していない対象は変更できません。")
        target = self.targets[handle]
        if self.records.selections:
            self._record_handle(handle)
            return
        spoken_names = self.spoken_names()
        if not self.local and target["kind"] in {"cell", "field", "heading", "meaning"}:
            matched = False
            ambiguous: set[str] = set()
            for value in spoken_names:
                # Search evidence may include a spoken heading/row. A direct
                # model choice must instead be unique across this owned kind.
                matches = self.name_matches.get((value, target["kind"]))
                if matches is None:
                    # Exact names take precedence over approximate alternatives.
                    # Evaluate each mention independently so that a batch naming
                    # two similar fields does not make either one ambiguous.
                    matches = {
                        item["handle"] for item in self.targets.values()
                        if item["kind"] == target["kind"] and value in names(item)
                    }
                    if not matches:
                        matches = {
                            item["handle"] for item in self.nearby(
                                TargetSearch(query=value, kinds=[target["kind"]])
                            )
                        }
                if matches == {handle}:
                    matched = True
                elif handle in matches:
                    ambiguous.update(matches)
            if matched:
                self.resolved.add(handle)
            elif ambiguous:
                raise reject(
                    "TARGET_AMBIGUOUS", "候補の欄名・位置・見出し・行を指定してください。",
                    candidateHandles=sorted(ambiguous),
                )
            elif handle not in self.resolved or (
                handle in self.implicit and handle not in self.searched
                and any(value not in {
                    "ここ", "そこ", "これ", "それ", "この欄", "その欄", "選択した欄",
                    "選択中の欄", "選択欄", "今の", "さっきの", "値", "同じ値",
                } for value in spoken_names)
            ):
                # Visibility and a unique catalog name alone are not evidence
                # that the user requested this field.
                raise reject("TARGET_AMBIGUOUS", "変更する欄名・位置を指定してください。")
        if handle in self.resolved:
            return
        duplicates = [
            t
            for t in self.targets.values()
            if t["kind"] == target["kind"] and names(t) & names(target)
        ]
        if len(duplicates) > 1:
            # Relations/options can be identified by an explicitly resolved owner.
            owner = target.get("ownerHandle")
            if (
                owner
                and owner in self.resolved
                and sum(t.get("ownerHandle") == owner for t in duplicates) == 1
            ):
                return
            raise reject(
                "TARGET_AMBIGUOUS",
                "同じ名前の候補があります。位置・見出し・行を指定してください。",
                candidateHandles=[t["handle"] for t in duplicates[:50]],
            )

    def _record_handle(self, handle: str) -> set[int]:
        if handle not in self.seen or handle not in self.targets:
            raise reject("TARGET_STALE", "会話で取得していない対象は変更できません。")
        covered: set[int] = set()
        candidates: set[str] = set()
        for index, selection in enumerate(self.records.selections):
            query = selection.search(self.records.recent)
            if query.row is None or not (query.column or query.query):
                continue
            located = [t for t in self.targets.values() if t.get("recordCell")
                       and matches_location(t, query)
                       and (not self.local or t["handle"] in self.selected)]
            if query.query:
                exact = [t for t in located if normalized(query.query) in {
                    *names(t), normalized(t["recordCell"]["columnName"]),
                }]
                located = exact or [t for t in self.nearby(query) if t.get("recordCell")]
            matches = {t["handle"] for t in located}
            candidates.update(matches)
            if matches == {handle}:
                covered.add(index)
        if not covered:
            raise reject(
                "TARGET_AMBIGUOUS", "指定された表・行・列の対象が一つに定まりません。",
                candidateHandles=sorted(candidates),
            )
        self.resolved.add(handle)
        return covered
