"""Read-only isolated same-import dimensional evidence; never a qualification issuer."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.calc_workbook_geometry import dimensions, verify_dimension_observation
from app.services.renderer_wsl import import_wsl_geometry


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workbook", type=Path)
    parser.add_argument("--sheet", required=True)
    parser.add_argument("--rows", type=int, required=True)
    parser.add_argument("--columns", type=int, required=True)
    parser.add_argument("--runtime", required=True)
    args = parser.parse_args()
    content = args.workbook.read_bytes()
    request = {"sheetName": args.sheet, "rows": args.rows, "columns": args.columns}
    fods, report = import_wsl_geometry(
        content, runtime_id=args.runtime, font_source="windows", dimension_request=request)
    geometry = dimensions(fods, args.sheet, args.rows, args.columns)
    verify_dimension_observation(geometry, report, args.sheet, hashlib.sha256(content).hexdigest())
    if args.workbook.read_bytes() != content:
        raise RuntimeError("Source changed during measurement")
    # Raw evidence only: acceptance and wrap detection remain a separate consumer.
    output = Path(tempfile.mkdtemp(prefix="llmocr-uno-same-import-"))
    (output / "dimensions.fods").write_bytes(fods)
    (output / "report.json").write_text(json.dumps(report, ensure_ascii=True, indent=2),
                                         encoding="utf-8")
    (output / "geometry.json").write_text(json.dumps(geometry, ensure_ascii=True, indent=2),
                                           encoding="utf-8")
    report_sha = hashlib.sha256((output / "report.json").read_bytes()).hexdigest()
    print(json.dumps({"directory": str(output), "sourceUnchanged": True,
                      "reportSha256": report_sha,
                      "qualified": False, "request": request}, ensure_ascii=True))


if __name__ == "__main__":
    main()
