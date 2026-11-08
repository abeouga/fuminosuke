# ruff: noqa: RUF001  (messages and notes use Japanese fullwidth punctuation)
"""Confirmed documents filtered and aggregated, for callers other than the chat (the MCP server).

The documents are the structured service's: confirmed only, the newest version of each series,
none from the trash. Values are matched and read as numbers with the chat's own rules
(``matching`` / ``aggregation``); nothing is guessed. Read-only.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from app.editing.errors import reject
from app.structured.aggregation import compute, extremes, read_numbers
from app.structured.contracts import AggregationRequest, DocumentFilter, DocumentSearch
from app.structured.matching import compact, shown_value, value_equals
from app.structured.service import StructuredDocumentService

JST = timezone(timedelta(hours=9))
# Confirmed documents read for one request; more are reported as truncated, not read.
DOCUMENT_LIMIT = 200
# Items listed per kind (used / excluded) in one answer.
ITEM_LIMIT = 500
_LIST_LIMIT = 1000

NOTES = {
    "dots_as_thousands": "「.」を千の区切りとして読んだ",
    "dots_as_decimal": "小数として読んだ（千の区切りかもしれない）",
    "skipped": "数として読めない",
}


@dataclass(slots=True)
class _Value:
    """One value of the selected field, with where it is."""

    document: dict[str, Any]
    field: dict[str, Any] | None
    label: str
    section: str | None
    table_title: str | None
    row_number: int | None


def confirmed_day(value: Any) -> date | None:
    """The confirmed date in Japan time (a stored time without a zone is UTC)."""
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    if not isinstance(value, datetime):
        return None
    moment: datetime = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    return moment.astimezone(JST).date()


class StructuredQueryService:
    def __init__(self, documents: StructuredDocumentService) -> None:
        self._documents = documents

    # --- Which documents -------------------------------------------------------------------

    def documents(self, request: DocumentFilter) -> tuple[list[dict[str, Any]], bool]:
        """The matching confirmed documents (newest first, as the list), read in full; and
        whether more matched before the field conditions than DOCUMENT_LIMIT."""
        summaries, truncated = self._summaries(request)
        documents = [self._documents.get(s["jobId"]) for s in summaries]
        if request.field_equals:
            documents = [d for d in documents if self._holds(d, request)]
        return documents, truncated

    def search(self, request: DocumentSearch) -> dict[str, Any]:
        """The matching confirmed documents as the list's summaries (newest first). A document
        is read in full only when a field condition needs its values."""
        summaries, truncated = self._summaries(request)
        if request.field_equals:
            summaries = [
                s for s in summaries if self._holds(self._documents.get(s["jobId"]), request)
            ]
        return {
            "items": summaries[: request.limit],
            "total": len(summaries),
            "truncated": truncated,
        }

    def _summaries(self, request: DocumentFilter) -> tuple[list[dict[str, Any]], bool]:
        """The list's summaries narrowed by template, name, ids and confirmed date; at most
        DOCUMENT_LIMIT of them, and whether there were more."""
        summaries = self._documents.list(
            limit=_LIST_LIMIT, template_id=request.template_id, query=request.query
        )
        if request.document_ids is not None:
            wanted = set(request.document_ids)
            summaries = [s for s in summaries if s["jobId"] in wanted]
        if request.confirmed_from or request.confirmed_to:
            summaries = [s for s in summaries if self._in_range(s, request)]
        return summaries[:DOCUMENT_LIMIT], len(summaries) > DOCUMENT_LIMIT

    @staticmethod
    def _in_range(summary: dict[str, Any], request: DocumentFilter) -> bool:
        day = confirmed_day(summary["confirmedAt"])
        if day is None:
            return False
        if request.confirmed_from and day < request.confirmed_from:
            return False
        return not (request.confirmed_to and day > request.confirmed_to)

    @staticmethod
    def _holds(document: dict[str, Any], request: DocumentFilter) -> bool:
        """Every condition is met by some field outside the tables with that name, by its
        stored value or as people see it."""
        outside = [f for f in document["fields"] if f["tableId"] is None]
        return all(
            any(
                compact(f["label"]) == compact(condition.label)
                and (
                    value_equals(f["value"], condition.value)
                    # A selection as people see it: 「黒」 for the stored 「黒2」.
                    or value_equals(shown_value(f["value"], f.get("choiceLabels")), condition.value)
                )
                for f in outside
            )
            for condition in request.field_equals
        )

    # --- Aggregation -----------------------------------------------------------------------

    def aggregate(self, request: AggregationRequest) -> dict[str, Any]:
        documents, truncated = self.documents(request)
        if not documents:
            raise reject("STRUCTURED_NO_DOCUMENTS", "条件に合う確定済みの書類がありません。", 404)
        field = request.field
        values = self._values(documents, field.label, field.section, field.table_title)
        numbers = read_numbers([v.field["value"] if v.field else None for v in values])
        result = compute(numbers, request.operation)

        def item(
            index: int, number: Decimal | None = None, note: str | None = None
        ) -> dict[str, Any]:
            value = values[index]
            field = value.field or {}
            return {
                "jobId": value.document["jobId"],
                "documentName": value.document.get("documentName"),
                "fieldId": field.get("fieldId"),
                "label": value.label,
                "section": value.section,
                "tableTitle": value.table_title,
                "rowNumber": value.row_number,
                "pageId": field.get("pageId"),
                "value": field.get("value"),
                "number": None if number is None else f"{number:f}",
                "note": note,
            }

        read = {n.index: n for n in numbers.read}
        if request.operation == "count":
            used_indexes = sorted([*read, *(s.index for s in numbers.skipped)])
            used = [item(i, read[i].number if i in read else None) for i in used_indexes]
            excluded: list[dict[str, Any]] = []
        else:
            used = [item(n.index, n.number, NOTES.get(n.how)) for n in numbers.read]
            excluded = [item(s.index, None, NOTES["skipped"]) for s in numbers.skipped]
        at: list[dict[str, Any]] = []
        if request.operation in {"max", "min"} and isinstance(result, Decimal):
            at = [item(n.index, n.number, NOTES.get(n.how)) for n in extremes(numbers, result)]
        cut = len(used) > ITEM_LIMIT or len(excluded) > ITEM_LIMIT
        return {
            "operation": request.operation,
            "field": request.field.model_dump(by_alias=True),
            "result": None
            if result is None
            else (str(result) if isinstance(result, int) else f"{result:f}"),
            "countUsed": len(used),
            "blank": numbers.blank,
            "used": used[:ITEM_LIMIT],
            "excluded": excluded[:ITEM_LIMIT],
            "at": at[:ITEM_LIMIT],
            "documents": len(documents),
            "truncated": truncated or cut,
        }

    @staticmethod
    def _values(
        documents: list[dict[str, Any]],
        label: str,
        section: str | None,
        table_title: str | None,
    ) -> list[_Value]:
        """The selected field's values in every document, in document then row order.

        Without a table title a field outside the tables is looked for first, then a table
        column. The same name meaning two different fields (two sections, two tables) is not
        picked from: the candidates are returned for the caller to choose.
        """
        wanted = compact(label)
        wanted_table = compact(table_title) if table_title else None
        wanted_section = compact(section) if section else None

        def fields(document: dict[str, Any]) -> list[_Value]:
            return [
                _Value(document, f, f["label"], f.get("section"), None, None)
                for f in document["fields"]
                if f["tableId"] is None
                and compact(f["label"]) == wanted
                and (wanted_section is None or compact(f.get("section") or "") == wanted_section)
            ]

        def columns(document: dict[str, Any]) -> list[_Value]:
            by_id = {f["fieldId"]: f for f in document["fields"]}
            found: list[_Value] = []
            for table in document["tables"]:
                if wanted_table is not None and compact(table["title"]) != wanted_table:
                    continue
                for column in table["columns"]:
                    if compact(column["label"]) != wanted:
                        continue
                    for row in table["rows"]:
                        cell = next(
                            (c for c in row["cells"] if c["columnId"] == column["id"]), None
                        )
                        if cell is None or cell["fieldId"] is None:
                            continue  # A row without this cell: nothing written there.
                        found.append(
                            _Value(
                                document,
                                by_id.get(cell["fieldId"]),
                                column["label"],
                                None,
                                table["title"],
                                row["rowNumber"],
                            )
                        )
            return found

        values: list[_Value] = []
        if wanted_table is None:
            values = [v for d in documents for v in fields(d)]
        if not values and wanted_section is None:
            values = [v for d in documents for v in columns(d)]
        if not values:
            heading = table_title or section
            where = f"「{heading}」の" if heading else ""
            raise reject(
                "STRUCTURED_FIELD_NOT_FOUND",
                f"{where}「{label}」という欄は、条件に合う確定済みの書類にありません。",
                404,
            )
        # The same field of the same template in many documents is one field.
        kinds: dict[tuple[str, str, str, str], dict[str, Any]] = {}
        for value in values:
            key = (
                value.document["templateId"],
                compact(value.table_title or ""),
                compact(value.section or ""),
                compact(value.label),
            )
            kinds.setdefault(
                key,
                {
                    "templateName": value.document.get("templateName"),
                    "tableTitle": value.table_title,
                    "section": value.section,
                    "label": value.label,
                },
            )
        if len(kinds) > 1:
            names = "、".join(
                " ".join(
                    p for p in (c["templateName"], c["tableTitle"] or c["section"], c["label"]) if p
                )
                for c in kinds.values()
            )
            raise reject(
                "STRUCTURED_FIELD_AMBIGUOUS",
                f"「{label}」に当てはまる欄が複数あります（{names}）。見出しか表の名前を添えて指定してください。",
                409,
                candidateHandles=list(kinds.values()),
            )
        return values
