"""Hand-only correction, preserving the accepted physical field binding."""

import copy

from app.editing.catalog import Catalog
from app.editing.commands import Plan
from app.editing.errors import reject
from app.editing.manual_draft import ManualOcrDraft


def compile_manual_ocr(catalog: Catalog, draft: ManualOcrDraft) -> Plan:
    if catalog.state["kind"] != "ocr":
        raise reject("CAPABILITY_UNAVAILABLE", "手動OCR session専用です。")
    after = {"kind": "ocr", **draft.model_dump(mode="json", by_alias=True)}
    if set(after["fields"]) != set(catalog.state["fields"]) or any(
        after["fields"][key][prop] != value[prop]
        for key, value in catalog.state["fields"].items()
        for prop in ("sourceRegionId", "boundingBox")
    ):
        raise reject("FIELD_BINDING_CHANGED", "補正で読み取り欄と切り出し対応を変更できません。")
    return Plan(copy.deepcopy(catalog.state), after)
