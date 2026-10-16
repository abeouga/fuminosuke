"""Shared, versioned wire contract; no Docker or native document dependencies."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

POLICY = "linux-container-renderer-v1"
RENDERER_VERSION = "linux-print-reference-v1"
GRAPH_POLICY = "linux-prepared-artifact-graph-v1"
FONT_POLICY = "linux-fontconfig-face-v1"
MAX_FILE = 64 * 1024 * 1024
MAX_HEADER = 1024 * 1024
MODES = {"pdf", "fods", "reference", "preview"}
SHA = re.compile(r"[0-9a-f]{64}")


def implementation_fingerprint() -> str:
    return digest(
        encode({p.name: digest(p.read_bytes()) for p in sorted(Path(__file__).parent.glob("*.py"))})
    )


def digest(value: bytes | bytearray) -> str:
    return hashlib.sha256(value).hexdigest()


def encode(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def validate_request(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {"mode", "size", "sha256"}:
        raise ValueError("Invalid renderer request fields")
    if (
        value["mode"] not in MODES
        or type(value["size"]) is not int
        or not 0 < value["size"] <= MAX_FILE
        or not isinstance(value["sha256"], str)
        or not SHA.fullmatch(value["sha256"])
    ):
        raise ValueError("Invalid renderer request")
    return value


def validate_evidence(report: dict[str, Any]) -> dict[str, Any]:
    """Verify stored admission and image identities without starting a renderer."""
    if (
        report.get("policy") != POLICY
        or report.get("status") != "PASS"
        or report.get("fontSource") != "linux-image"
        or report.get("fontRequestPolicy") != FONT_POLICY
        or report.get("format") not in MODES
    ):
        raise ValueError("Invalid Linux renderer evidence")
    execution = report.get("execution")
    expected = {
        "network": "none",
        "user": "10001:10001",
        "readOnlyRoot": True,
        "capDrop": ["ALL"],
        "noNewPrivileges": True,
        "pidsLimit": 64,
        "memoryBytes": 2147483648,
        "memorySwapBytes": 2147483648,
        "nanoCpus": 1000000000,
        "wallSeconds": 80,
        "coreLimit": 1,
        "outputBytes": MAX_FILE,
        "inputReadOnly": True,
    }
    if (
        not isinstance(execution, dict)
        or execution.get("controls") != expected
        or execution.get("policy") != POLICY
        or not isinstance(execution.get("imageId"), str)
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", execution["imageId"])
        or not isinstance(execution.get("hostCorePatternSha256"), str)
        or not SHA.fullmatch(execution["hostCorePatternSha256"])
        or not isinstance(execution.get("workerSha256"), str)
        or not SHA.fullmatch(execution["workerSha256"])
    ):
        raise ValueError("Invalid Linux execution admission")
    if (
        report.get("workerSha256") != execution["workerSha256"]
        or report.get("admissionSha256") != digest(encode(execution))
        or report.get("coreDumpSuppressed") is not True
        or report.get("coreGuardMutationDenied") is not True
        or report.get("inputSha256") != report.get("sourceSha256")
    ):
        raise ValueError("Linux worker admission mismatch")
    for key in ("sourceSha256", "fontDirectorySha256", "packagesSha256"):
        if not isinstance(report.get(key), str) or not SHA.fullmatch(report[key]):
            raise ValueError("Invalid Linux dependency identity")
    if not isinstance(report.get("resolvedFonts"), list) or not report["resolvedFonts"]:
        raise ValueError("Missing Linux font evidence")
    return {
        "execution": execution,
        "fontDirectorySha256": report["fontDirectorySha256"],
        "packagesSha256": report["packagesSha256"],
        "resolvedFonts": report["resolvedFonts"],
    }
