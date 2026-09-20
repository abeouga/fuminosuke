"""Issue a scoped qualification for one prepared WSL renderer environment.

The identity lease proves freshness only.  This issuer combines that lease with
the owned preparation report's execution controls and dependency identities.
It qualifies the fixed prepared-reference invocation boundary, not every host
process, every possible dynamic load, or any submitted scan/OCR operation.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any

from app.services import wsl_environment_evidence as environment_evidence
from app.services.prepared_reference_evidence import PreparedReference, _replay_identity
from app.services.renderer_wsl_worker import validate_core_guard
from app.services.wsl_execution_evidence import validate_execution

POLICY = "wsl-prepared-environment-qualification-v1"
SCOPE = "fixed-prepared-reference-invocation-v1"


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def implementation_fingerprint() -> str:
    directory = Path(__file__).parent
    files = (
        "wsl_environment_qualification.py",
        "wsl_environment_evidence.py",
        "wsl_host_identity.py",
        "wsl_execution_evidence.py",
        "prepared_reference_evidence.py",
        "renderer_wsl_worker.py",
        "reference_qualification_service.py",
    )
    identity = {
        "policy": POLICY,
        "scope": SCOPE,
        "implementation": {
            name: hashlib.sha256((directory / name).read_bytes()).hexdigest() for name in files
        },
    }
    return hashlib.sha256(_canonical(identity)).hexdigest()


def issue(
    prepared: PreparedReference,
    lease: dict[str, Any],
    version_id: str,
    qualification_fingerprint: str,
    *,
    now: int | None = None,
    qualification_issued_at: int | None = None,
) -> dict[str, Any]:
    """Derive a PASS only from freshly validated, version-bound evidence."""
    if (
        not isinstance(version_id, str)
        or not version_id
        or not isinstance(qualification_fingerprint, str)
        or re.fullmatch(r"[0-9a-f]{64}", qualification_fingerprint) is None
    ):
        raise ValueError("Invalid environment qualification binding")
    evaluated_at = int(time.time()) if now is None else now
    if type(evaluated_at) is not int:
        raise ValueError("Invalid environment qualification time")
    issued_at = evaluated_at if qualification_issued_at is None else qualification_issued_at
    if (
        type(issued_at) is not int
        or not lease.get("issuedAt", 0) <= issued_at <= evaluated_at
        or issued_at >= lease.get("expiresAt", 0)
    ):
        raise ValueError("Invalid environment qualification issuance time")

    replay_identity = _replay_identity(prepared.report)
    execution = validate_execution(prepared.report)
    validate_core_guard(prepared.report.get("coreDumpGuard"))
    validate_core_guard(prepared.report.get("brokerCoreDumpGuard"))
    expected_identity = environment_evidence.identity(prepared.report)
    environment_evidence.validate_lease(lease, prepared, version_id, now=evaluated_at)
    if lease["identity"] != expected_identity:
        raise ValueError("Environment lease differs from the prepared invocation")

    bootstrap = expected_identity["bootstrapPythonIdentity"]
    native = bootstrap["nativeDependencies"]
    graph_sha = hashlib.sha256(environment_evidence.canonical(prepared.evidence)).hexdigest()
    return {
        "policy": POLICY,
        "scope": SCOPE,
        "status": "PASS",
        "qualified": True,
        "templateVersionId": version_id,
        "qualificationFingerprint": qualification_fingerprint,
        "implementationFingerprint": implementation_fingerprint(),
        "preparedEvidenceSha256": graph_sha,
        "issuedAt": issued_at,
        "leaseIssuedAt": lease["issuedAt"],
        "expiresAt": lease["expiresAt"],
        "identity": {
            "runtimeImageSha256": expected_identity["runtimeImage"]["sha256"],
            "hostIdentitySha256": hashlib.sha256(
                environment_evidence.canonical(lease["hostIdentity"])
            ).hexdigest(),
            "workerSha256": expected_identity["workerIdentity"]["sha256"],
            "fontDirectorySha256": expected_identity["fontDirectoryIdentity"]["sha256"],
            "bootstrapPythonSha256": bootstrap["sha256"],
            "nativeDependenciesSha256": native["sha256"],
            "executionBoundarySha256": hashlib.sha256(_canonical(execution)).hexdigest(),
            "rendererIdentitySha256": hashlib.sha256(_canonical(replay_identity)).hexdigest(),
            "coreGuardSha256": hashlib.sha256(
                _canonical(prepared.report["coreDumpGuard"])
            ).hexdigest(),
            "brokerCoreGuardSha256": hashlib.sha256(
                _canonical(prepared.report["brokerCoreDumpGuard"])
            ).hexdigest(),
            "unitControlsSha256": hashlib.sha256(
                _canonical(execution["unitControls"])
            ).hexdigest(),
        },
        "requirements": {
            "fixedRuntime": True,
            "currentHostAndInstallation": True,
            "workerAndDependencies": True,
            "nonPrivilegedExecution": True,
            "networkIsolation": True,
            "noNewPrivileges": True,
            "hostFilesystemIsolation": True,
            "readOnlyWindowsFonts": True,
            "resourceLimits": True,
            "effectiveUnitControls": True,
            "workerAndBrokerCoreGuards": True,
            "versionAndPreparedGraphBinding": True,
            "leaseFreshness": True,
        },
        "excludedFromScope": [
            "host-wide-complete-dependency-closure",
            "complete-dynamic-load-trace",
            "submitted-scan-registration-and-ink-clearance",
        ],
        "unresolvedBlockers": [],
    }
