"""Explicit new draft geometry; never reinterpret historical WSL measurements."""
from __future__ import annotations

import copy
import hashlib
import re
from typing import Any
from xml.etree import ElementTree as ET

from app.domain.calc_print_grid import MODEL, POLICY, dimension_grid
from app.domain.print_layout import print_error
from app.services import wsl_workbook_geometry as historical

EXPORT_POLICY = "calc-inch-mm100-uint16-v1"


def verify_dimension_observation(evidence: dict[str, Any], report: dict[str, Any],
                                 sheet_name: str, source_sha: str) -> None:
    """Check fixed-export inversion against same-import wide UNO dimensions.

    GetMMRect rounds cumulative twips before constructing the inclusive Size.
    Compare that exact value, not a guessed per-dimension +1 correction. This
    verifies non-overflow only; it does not issue geometry/environment approval.
    """
    from app.services.renderer_wsl_worker import dimension_request

    try:
        runtime = report["runtimeImage"]["sha256"]
        if (not isinstance(runtime, str) or re.fullmatch(r"[0-9a-f]{64}", runtime) is None
                or re.fullmatch(r"[0-9a-f]{64}", source_sha) is None
                or evidence["model"] != MODEL or evidence["exportPolicy"] != EXPORT_POLICY
                or report["sourceSha256"] != source_sha
                or report["fodsSha256"] != evidence["fodsSha256"]):
            raise ValueError("Dimension import identity mismatch")
        observation = report["dimensionObservation"]
        expected_request = {"sheetName": sheet_name, "rows": len(evidence["rows"]),
                            "columns": len(evidence["columns"])}
        if (observation["policy"] != "wsl-uno-dimension-v1"
                or dimension_request(observation["request"], "fods", runtime) != expected_request
                or observation["sourceSha256"] != source_sha
                or observation["fodsSha256"] != evidence["fodsSha256"]):
            raise ValueError("Dimension observation identity mismatch")
        for axis in ("columns", "rows"):
            grid = dimension_grid(evidence[axis], evidence["hidden" + axis.title()])
            records = observation[axis]
            if not isinstance(records, list) or len(records) != len(grid["twips"]):
                raise ValueError("Dimension observation inventory mismatch")
            hidden = set(evidence["hidden" + axis.title()])
            cumulative = 0
            for index, (twips, record) in enumerate(zip(grid["twips"], records, strict=True), 1):
                if (not isinstance(record, list) or len(record) != 3
                        or any(type(v) is not int for v in record[:2])
                        or not 0 <= record[0] <= 65535 or not 1 <= record[1] <= 65535
                        or type(record[2]) is not bool or record[2] != (index not in hidden)):
                    raise ValueError("Wide dimension overflow or visibility mismatch")
                if index not in hidden and record[0] != (twips * 127 + 36) // 72:
                    raise ValueError("Scalar dimension mismatch")
                first = (cumulative * 127 + 36) // 72
                cumulative += twips
                last = (cumulative * 127 + 36) // 72
                if record[1] != last - first + 1:
                    raise ValueError("Wide cumulative dimension mismatch")
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise print_error("寸法の独立観測と固定出力の対応を確認できません。",
                          "PRINT_GEOMETRY_CHANGED", 409) from exc


def dimensions(fods: bytes, sheet_name: str, rows: int, columns: int) -> dict[str, Any]:
    evidence = historical.dimensions(fods, sheet_name, rows, columns)
    root = ET.fromstring(fods)  # The historical parser has already applied XML budgets.
    for node in root.iter():
        for key in (historical.STYLE + "column-width", historical.STYLE + "row-height"):
            if (key in node.attrib
                    and re.fullmatch(r"\d+(?:\.\d{1,4})?in", node.attrib[key]) is None):
                raise print_error("固定inch出力の寸法ではありません。", "PAGE_GEOMETRY_MISMATCH")
    for kind in ("columns", "rows"):
        dimension_grid(evidence[kind], evidence["hidden" + kind.title()])
    evidence.update(model=MODEL, exportPolicy=EXPORT_POLICY,
                    fodsSha256=hashlib.sha256(fods).hexdigest())
    return evidence


def apply_dimensions(source: dict[str, Any], evidence: dict[str, Any]) -> dict[str, Any]:
    try:
        if (evidence["model"] != MODEL or evidence["exportPolicy"] != EXPORT_POLICY
                or re.fullmatch(r"[0-9a-f]{64}", evidence["fodsSha256"]) is None
                or evidence["importReport"]["fodsSha256"] != evidence["fodsSha256"]):
            raise ValueError("Fixed export identity changed")
        # Keep historical drafts readable, but never ignore new bound evidence.
        if "dimensionObservation" in evidence["importReport"]:
            verify_dimension_observation(evidence, evidence["importReport"],
                                         source["sheetName"], source["sha256"])
        columns = dimension_grid(evidence["columns"], evidence["hiddenColumns"])
        rows = dimension_grid(evidence["rows"], evidence["hiddenRows"])
        projected = copy.deepcopy(evidence)
        projected.update(model=historical.MODEL,
                         columns=[v * 72 / 2540 for v in columns["stepsMm100"]],
                         rows=[v * 72 / 2540 for v in rows["stepsMm100"]])
        result = historical.apply_dimensions(source, projected)
        start, end = source["startRow"], source["endRow"]
        nominal = [{"row": start, "y": 0.0}]
        for row in range(start, end + 1):
            nominal.append({"row": row + 1,
                            "y": nominal[-1]["y"] + rows["twips"][row - 1] / 20})
        result.update(
            geometryModelVersion=MODEL, geometryEvidence=copy.deepcopy(evidence),
            nominalRowBoundaries=nominal,
            nominalWidth=sum(columns["twips"][source["startCol"] - 1:source["endCol"]]) / 20,
            nominalHeight=nominal[-1]["y"],
            printGrid={"policy": POLICY, "columns": columns["twips"], "rows": rows["twips"]},
        )
        return result
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise print_error("保存したCalc寸法と元セルの対応を確認できません。",
                          "PRINT_GEOMETRY_CHANGED", 409) from exc
