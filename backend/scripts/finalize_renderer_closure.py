"""Link independently saved observations to the current, unqualified environment."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.office_reference_renderer import sha
from app.services.renderer_environment import (
    environment_identity,
    environment_report,
    file_identity,
)


def main():
    root = Path(__file__).resolve().parents[2] / "docs/reference-renderer-evidence"
    output = root / "closure"

    def reference(name):
        return {"path": name, "sha256": sha((output / name).read_bytes())}

    report = environment_report(
        environment_identity((root / "calibration/explicit843/source.xlsx").read_bytes())
    )
    report["networkIsolation"].update(
        implementation="AppContainer zero capabilities + Windows Job Object",
        implementationVersion="appcontainer-zero-capabilities-v1",
        implementationFile=file_identity(
            Path(__file__).resolve().parents[1] / "app/services/renderer_appcontainer.py"
        ),
        requiredPrivileges="standard user",
        networkCapabilities=[],
        probeEvidence=reference("network.json"),
        OfficeRenderEvidence=reference("network.json"),
        ipcEvidence=reference("ipc.json"),
        candidateOfficeCompatible=False,
        incompatibility="LibreOffice OSL pipe lacks the AppContainer LOCAL namespace",
    )
    report["fontVerification"] = {
        "evidence": reference("fonts.json"),
        "observedKnownSimpleGlyphFonts": ["Calibri", "Arial", "Meiryo", "Yu Gothic"],
        "fullTemplateFontQualified": False,
        "fallbackCode": "FONT_FALLBACK_UNVERIFIED",
    }
    report["rasterizerIsolation"] = {
        "processSeparated": True,
        "jobMemoryBytes": 512 * 1024 * 1024,
        "processLimit": 2,
        "wallSeconds": 30,
        "cpuRate": 2000,
        "networkIsolated": False,
        "nativeFailure": reference("native-failure.json"),
    }
    report["runtimeDependencies"]["evidence"] = reference("resources.json")
    report["cropSafety"].update(
        evidence=reference("crop-geometry-verification.json"),
        physicalPolicyEvidence=reference("crop-clearance.json"),
    )
    report["dpiVerification"] = reference("dpi-verification.json")
    report["profileLifecycle"] = reference("appcontainer-parent-death.json")
    report["originalWorkbookIntegrity"] = reference("originals.json")
    report["productionQualified"] = False
    (output / "environment.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(report["fingerprint"])


if __name__ == "__main__":
    main()
