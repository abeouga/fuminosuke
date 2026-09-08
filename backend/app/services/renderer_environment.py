"""Host-owned environment identity and fail-closed evidence composition."""
from __future__ import annotations

import hashlib
import importlib
import importlib.metadata
import importlib.util
import json
import platform
import sys
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from app.services.renderer_process import (
    ACTIVE_PROCESSES,
    CPU_RATE,
    MEMORY_BYTES,
    NETWORK_POLICY,
    RESOURCE_POLICY,
    SANDBOX_VERSION,
)
from app.services.renderer_temp import TEMP_POLICY

THRESHOLD_POLICY = "clearance-bounded-physical-v1"
ENV_SCHEMA = 2


def digest(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()


def file_identity(path: Path) -> dict[str, Any]:
    from app.services.renderer_dependencies import file_record
    return file_record(path)



def font_identity(content: bytes) -> dict[str, Any]:
    from app.services.office_reference_renderer import inspect_package
    from app.services.workbook_geometry import normal_font
    parts = inspect_package(content)
    font = normal_font(content)
    names = {"Calibri": "calibri.ttf", "Arial": "arial.ttf", "Meiryo": "meiryo.ttc",
             "Yu Gothic": "YuGothR.ttc", "Aptos": "aptos.ttf"}
    filename = names.get(font["family"])
    file = file_identity(Path("C:/Windows/Fonts") / filename) if filename else {"available": False}
    styles = ET.fromstring(parts["xl/styles.xml"])
    sheets = [ET.fromstring(data) for name, data in parts.items()
              if name.startswith("xl/worksheets/") and name.endswith(".xml")]
    ns = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    normal = styles.findall("s:fonts/s:font", ns)[font["fontId"]]
    features = []
    for tag in ("scheme", "b", "i"):
        item = normal.find("s:" + tag, ns)
        if item is not None and item.get("val", "1") not in {"0", "false", "none"}:
            features.append("normal-" + tag)
    if any(sheet.findall(".//s:conditionalFormatting", ns) for sheet in sheets):
        features.append("conditional-formatting")
    if any(sheet.findall(".//s:is/s:r", ns) for sheet in sheets):
        features.append("rich-text")
    return {"requestedFamily": font["family"], "normal": font,
            "expectedHostFamily": font["family"], "file": file,
            "unresolvedSemantics": features, "actualOfficeFontVerified": False,
            "codes": ["FONT_FALLBACK_UNVERIFIED"] +
                     (["FONT_IDENTITY_UNAVAILABLE"] if not font["installed"] else []) +
                     (["FONT_SEMANTICS_UNVERIFIED"] if features else [])}


def environment_identity(content: bytes, dpi: int = 150) -> dict[str, Any]:
    from app.services.office_reference_renderer import renderer_identity
    from app.services.workbook_geometry import GEOMETRY_MODEL_VERSION
    renderer = renderer_identity()
    renderer["dpi"] = dpi
    folder = Path(renderer["executable"]).parent
    # Hash the installed executable/native code closure in program/. Share/config
    # trees and dynamically selected modules remain an explicitly open boundary.
    binaries = [file_identity(p) for p in sorted(folder.iterdir())
                if p.suffix.lower() in {".exe", ".com", ".bin", ".dll", ".rdb"}]
    raw = importlib.util.find_spec("pypdfium2_raw")
    if raw is None or raw.origin is None:
        raise ValueError("PDFium helper dependency missing")
    native = [file_identity(p) for p in sorted(Path(raw.origin).parent.glob("*.dll"))]
    dependencies = {}
    for name in ["PIL", "numpy", "cv2"]:
        module = importlib.import_module(name)
        dependencies[name] = {"version": module.__version__, "nativeFiles": [
            file_identity(p) for p in sorted(Path(str(module.__file__)).parent.glob("*.pyd"))]}
    registry = importlib.import_module("winreg")
    with registry.OpenKey(registry.HKEY_LOCAL_MACHINE,
                          r"SOFTWARE\Microsoft\Windows NT\CurrentVersion") as key:
        os_revision = {name: registry.QueryValueEx(key, name)[0]
                       for name in ["CurrentBuildNumber", "UBR", "DisplayVersion"]}
    services = Path(__file__).parent
    geometry_files = [services / "workbook_geometry.py", services / "print_layout_source.py",
                      services.parent / "domain/print_layout.py"]
    implementation = [services / p for p in ["office_reference_renderer.py", "renderer_process.py",
                                             "renderer_temp.py", "renderer_environment.py",
                                             "reference_geometry.py", "geometry_calibration.py",
                                             "renderer_appcontainer.py", "renderer_dependencies.py",
                                             "renderer_raster.py", "renderer_raster_worker.py",
                                             "renderer_font_evidence.py",
                                             "reference_crop_safety.py"]]
    return {"schemaVersion": ENV_SCHEMA, "renderer": {**renderer,
                "buildId": renderer["version"].split()[-1], "installationFiles": binaries,
                "installationSha256": digest(binaries), "completeInstallationVerified": False},
            "geometry": {"modelVersion": GEOMETRY_MODEL_VERSION,
                         "implementation": [file_identity(p) for p in geometry_files]},
            "rasterizer": {"type": "pypdfium2", "version": importlib.metadata.version("pypdfium2"),
                           "nativeLibraries": native, "dpi": dpi, "dependencies": dependencies,
                           "helperPolicy": "pdfium-helper-job-512m-2proc-20cpu-30s-v1",
                           "fontParserVersion": importlib.metadata.version("pypdf"),
                           "networkIsolated": False},
            "fonts": font_identity(content),
            "environment": {"os": platform.system(), "osBuild": platform.version(),
                            "osRevision": os_revision, "pythonVersion": platform.python_version(),
                            "pythonExecutable": file_identity(Path(
                                getattr(sys, "_base_executable", sys.executable))),
                            "architecture": platform.machine(), "sandboxVersion": SANDBOX_VERSION,
                            "networkPolicyVersion": NETWORK_POLICY,
                            "resourcePolicyVersion": RESOURCE_POLICY, "tempPolicy": TEMP_POLICY,
                            "memoryBytes": MEMORY_BYTES, "activeProcesses": ACTIVE_PROCESSES,
                            "cpuRate": CPU_RATE, "wallSeconds": 45},
            "implementation": [file_identity(p) for p in implementation],
            "thresholdPolicyVersion": THRESHOLD_POLICY}


def environment_report(identity: dict[str, Any]) -> dict[str, Any]:
    # There is no issuer for a production certificate in this implementation.
    # Identity evidence is useful and persisted even when qualification fails.
    return {"schemaVersion": ENV_SCHEMA, "environmentQualified": False,
            "fingerprint": digest(identity), "identity": identity,
            "networkIsolation": {"qualified": False, "policy": NETWORK_POLICY},
            "sandboxCandidate": {"implementation": "AppContainer",
                "implementationVersion": "appcontainer-zero-capabilities-v1",
                "requiredPrivileges": "standard user", "networkCapabilities": [],
                "qualified": False, "productionPathEnabled": False},
            "runtimeDependencies": {"method": "job-pids-psapi-poll-v1",
                "completeTrace": False, "hashCache": "path-size-mtime-ctime-v1"},
            "cropSafety": {"qualified": False, "policy": THRESHOLD_POLICY},
            "resourceIsolation": {"qualified": False, "policy": RESOURCE_POLICY,
                                  "osEnforcementImplemented": True},
            "thresholdPolicyVersion": THRESHOLD_POLICY,
            "codes": ["NETWORK_ISOLATION_UNVERIFIED", "RESOURCE_POLICY_UNQUALIFIED",
                      "INSTALLATION_CLOSURE_UNVERIFIED", "GEOMETRY_TOLERANCE_UNQUALIFIED",
                      "RASTER_NETWORK_ISOLATION_UNVERIFIED", "CROP_CLEARANCE_UNVERIFIED",
                      *identity["fonts"]["codes"]]}


def validate_environment(report: dict[str, Any], current: dict[str, Any]) -> list[str]:
    codes = []
    if (report.get("schemaVersion") != ENV_SCHEMA
            or report.get("fingerprint") != digest(current)
            or report.get("identity") != current
            or report.get("thresholdPolicyVersion") != THRESHOLD_POLICY):
        codes.append("RENDERER_ENVIRONMENT_CHANGED")
    # Recompute unresolved policy conditions, rather than trusting saved booleans.
    return codes + [str(code) for code in environment_report(current)["codes"]]
