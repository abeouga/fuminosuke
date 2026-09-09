"""Display-only page geometry. This model is never reference-qualified."""

from __future__ import annotations

import copy
import math
from typing import Any

from app.domain.print_layout import print_error
from app.services.print_layout_source import DRAFT_GEOMETRY_VERSION


def draft_geometry(source: dict[str, Any], evidence: dict[str, Any] | None) -> dict[str, Any]:
    result = copy.deepcopy(source)
    if evidence is not None:
        columns, rows = evidence.get("columns", []), evidence.get("rows", [])
        if (
            evidence.get("sourceSha256") != source["sha256"]
            or evidence.get("sheetName", source["sheetName"]) != source["sheetName"]
            or len(columns) < source["endCol"] or len(rows) < source["endRow"]
            or any(not isinstance(v, int | float) or isinstance(v, bool)
                   or not math.isfinite(v) or v <= 0
                   for v in [*columns, *rows])
        ):
            raise print_error("下書きの元セル寸法を確認できません。", "PRINT_GEOMETRY_CHANGED", 409)
        xs, ys = {source["startCol"]: 0.0}, {source["startRow"]: 0.0}
        for c in range(source["startCol"], source["endCol"] + 1):
            xs[c + 1] = xs[c] + (0 if c in source["hiddenColumns"] else columns[c - 1])
        for r in range(source["startRow"], source["endRow"] + 1):
            ys[r + 1] = ys[r] + (0 if r in source["hiddenRows"] else rows[r - 1])
        for item in [*result["cells"], *result["fields"]]:
            c, r = item["startCol"], item["startRow"]
            item.update(x=xs[c], y=ys[r], width=xs[item["endCol"] + 1] - xs[c],
                        height=ys[item["endRow"] + 1] - ys[r])
        result.update(width=xs[source["endCol"] + 1], height=ys[source["endRow"] + 1],
                      rowBoundaries=[{"row": r, "y": y} for r, y in ys.items()])
    if result["width"] <= 0 or result["height"] <= 0:
        raise print_error("表示可能な印刷範囲がありません。")
    result.update(geometryModelVersion=DRAFT_GEOMETRY_VERSION, geometryEvidence=evidence)
    result["warnings"].append(
        "目視編集用の参考表示です。非表示行・列を除外し、セル内縮小を参考描画します。"
        "原本の組版・フォントとの一致は未認定です。"
    )
    return result
