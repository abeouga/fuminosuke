"""Validate observed WSL execution controls, not a complete host certificate.

These observations are produced by the inner worker before parsing input.
Consumers must ALSO bind the owned report to its worker/runtime/input hashes.
Equal installation identities cannot turn an uncontained execution into proof.
"""
from __future__ import annotations

import copy
from typing import Any

from app.services.renderer_wsl_worker import validate_unit_controls

FIELDS = (
    "uid", "networkInterfaces", "networkDenied", "hostFilesystemHidden",
    "noNewPrivileges", "resourceLimits", "referenceFontMountReadOnly", "unitControls",
)
RESOURCE_LIMITS = {
    "memory.max": "1073741824", "memory.swap.max": "0",
    "pids.max": "64", "cpu.max": "100000 100000",
}


def validate_execution(report: dict[str, Any]) -> dict[str, Any]:
    if (
        not isinstance(report, dict)
        or report.get("policy") != "wsl-bwrap-cgroup-v1"
        or report.get("status") != "PASS"
        or report.get("format") not in ("pdf", "fods", "reference")
        or report.get("fontSource") not in ("linux", "windows")
        or type(report.get("uid")) is not int or report["uid"] != 65534
        or report.get("networkInterfaces") != ["lo"]
        or any(report.get(key) is not True for key in (
            "networkDenied", "hostFilesystemHidden", "noNewPrivileges"))
        or report.get("referenceFontMountReadOnly") is not (report.get("fontSource") == "windows")
        or report.get("resourceLimits") != RESOURCE_LIMITS
    ):
        raise ValueError("WSL execution isolation evidence is missing or invalid")
    try:
        validate_unit_controls(report.get("unitControls"))
    except ValueError as exc:
        raise ValueError("WSL execution isolation evidence is missing or invalid") from exc
    return {key: copy.deepcopy(report[key]) for key in FIELDS}
