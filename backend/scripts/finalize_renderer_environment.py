"""Bind saved measurements to current identity and an explicitly unqualified policy proposal."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.office_reference_renderer import sha
from app.services.renderer_environment import (
    THRESHOLD_POLICY,
    environment_identity,
    environment_report,
    font_identity,
)


def main():
    evidence = Path(__file__).resolve().parents[2] / "docs/reference-renderer-evidence"
    output = evidence / "environment"
    calibration = evidence / "calibration"
    content = (calibration / "explicit843/source.xlsx").read_bytes()
    report = environment_report(environment_identity(content))
    dpi_data = json.loads((output / "dpi-dataset.json").read_text())
    analysis = {"schemaVersion": 1, "environmentQualified": False,
                "rendererFingerprint": report["fingerprint"],
                "geometryModelVersion": report["identity"]["geometry"]["modelVersion"],
                "datasetSha256": sha((output / "dpi-dataset.json").read_bytes()),
                "summary": dpi_data["summary"],
                "thresholdPolicyVersion": THRESHOLD_POLICY,
                "currentDiagnosticBudgetPx": 2.0, "productionThresholdQualified": False,
                "proposal": {"physicalAllowanceMm": .1, "rasterAllowancePx": 1.0,
                             "requiresIndependentFieldClearanceValidation": True,
                             "status": "UNQUALIFIED_ENGINEERING_ALLOCATION",
                             "scanRegistrationIncluded": False},
                "limits": ["No actual Office font selection proof", "No network containment",
                           "No real-form crop clearance validation", "PDFium executes in backend"]}
    (output / "dpi-analysis.json").write_text(json.dumps(analysis, indent=2), encoding="utf-8")
    report["dpiCalibration"] = {"datasetSha256": analysis["datasetSha256"],
                                "analysisSha256": sha((output / "dpi-analysis.json").read_bytes()),
                                "qualified": False}
    (output / "environment.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    fonts = [font_identity((calibration / name / "source.xlsx").read_bytes()) for name in
             ["explicit843", "font-Arial", "font-Meiryo", "font-YuGothic", "font-Aptos"]]
    (output / "fonts.json").write_text(json.dumps(fonts, indent=2), encoding="utf-8")
    print(report["fingerprint"], report["identity"]["environment"]["osRevision"], flush=True)


if __name__ == "__main__":
    main()
