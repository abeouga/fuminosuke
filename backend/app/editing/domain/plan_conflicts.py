"""A model must resolve corrections before submission, not rely on array order."""

from typing import Any

from app.editing.domain.command import canonical
from app.editing.domain.planning import PlanningIssue


def reject_competing_writes(operations: list[dict[str, Any]]) -> None:
    seen: set[tuple[str, str]] = set()
    scalar = {
        "ocr.set_value": ("value", "target"),
        "cell.rename": ("name", "cell"),
        "meaning.update": ("meaning", "meaning"),
        "heading.update": ("name", "heading"),
        "records.replace_rows": ("rows", "section"),
        "records.configure": ("table_settings", "section"),
        "mark.configure": ("marks", "cell"),
        "mark.clear": ("marks", "cell"),
    }
    plural = {
        "cell.set_role": ("role", "cells"),
        "cell.set_reading": ("reading", "cells"),
        "heading.attach": ("parent", "children"),
        "heading.detach": ("parent", "children"),
        "cell.merge": ("geometry", "cells"),
        "cell.delete": ("geometry", "cells"),
    }
    for operation in operations:
        op = operation["op"]
        keys: list[tuple[str, str]] = []
        if op in scalar:
            attribute, field = scalar[op]
            keys = [(canonical(operation[field]), attribute)]
        elif op in plural:
            attribute, field = plural[op]
            keys = [(canonical(ref), attribute) for ref in operation[field]]
        elif op in {"identity.configure", "document_name.configure"}:
            keys = [("resource", op)]
        for key in keys:
            if key in seen:
                raise PlanningIssue("operation")
            seen.add(key)
