"""Owned, expiring environment identity evidence; NOT a production certificate.

Current runtime/font/bootstrap bytes are re-read without submitting a document.
Isolation conformance, manager/host closure and input ink safety are separate
qualification requirements. This identity lease cannot authorize ACTIVE/OCR.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy.orm import Session

from app.db.models import JobArtifact, ProcessingJob, TemplateVersion
from app.services import wsl_host_identity as host_identity
from app.services.prepared_reference_evidence import PreparedReference, _replay_identity
from app.services.renderer_temp import render_directory
from app.services.renderer_wsl import linux_path
from app.services.renderer_wsl_worker import validate_bootstrap_python, validate_core_guard
from app.services.wsl_execution_evidence import validate_execution
from app.storage.base import StorageService

POLICY = "wsl-environment-identity-lease-v2"
CURRENT_POLICY = "wsl-current-environment-identity-v1"
MAX_BYTES = 1024 * 1024
LIFETIME_SECONDS = 24 * 60 * 60
IDENTITY_KEYS = ("runtimeImage", "fontDirectoryIdentity", "bootstrapPythonIdentity",
                 "workerIdentity", "brokerCoreDumpGuard")


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def identity(report: dict[str, Any]) -> dict[str, Any]:
    validate_bootstrap_python(report.get("bootstrapPythonIdentity"))
    validate_core_guard(report.get("brokerCoreDumpGuard"))
    policies = {"runtimeImage": "wsl-squashfs-runtime-v1",
                "fontDirectoryIdentity": "wsl-font-directory-v1",
                "workerIdentity": "wsl-worker-source-v1"}
    for key, policy in policies.items():
        value = report.get(key)
        if (not isinstance(value, dict) or value.get("policy") != policy
                or not isinstance(value.get("sha256"), str)
                or re.fullmatch(r"[0-9a-f]{64}", value["sha256"]) is None):
            raise ValueError("Invalid current environment identity")
    runtime, fonts = report["runtimeImage"], report["fontDirectoryIdentity"]
    if (runtime.get("filesystem") != "squashfs" or runtime.get("mountsReadOnly") is not True
            or not isinstance(runtime.get("kernel"), str) or not runtime["kernel"]
            or type(fonts.get("fileCount")) is not int or not 1 <= fonts["fileCount"] <= 4096
            or type(fonts.get("totalBytes")) is not int
            or not 0 < fonts["totalBytes"] <= 1024 * 1024 * 1024):
        raise ValueError("Invalid current environment conditions")
    return {key: copy.deepcopy(report[key]) for key in IDENTITY_KEYS}


def inspect_current(runtime_id: str, distribution: str) -> dict[str, Any]:
    if (re.fullmatch(r"[0-9a-f]{64}", runtime_id) is None
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", distribution) is None):
        raise ValueError("Invalid environment selector")
    source = Path(__file__).with_name("renderer_wsl_worker.py")
    with source.open("rb") as stream:
        code = stream.read(MAX_BYTES + 1)
    if not 0 < len(code) <= MAX_BYTES:
        raise ValueError("Invalid environment inspector size")
    expected_worker = hashlib.sha256(code).hexdigest()
    with render_directory() as directory, tempfile.TemporaryFile() as output:
        worker = directory / "worker.py"
        worker.write_bytes(code)
        try:
            result = subprocess.run([
                "wsl.exe", "--distribution", distribution, "--user", "root", "--exec",
                "/usr/bin/unshare", "--mount", "--propagation", "private",
                "/usr/bin/python3", "-I", "-S", "-B", linux_path(worker),
                "--inspect-environment", runtime_id,
            ], stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
                timeout=45, check=False, creationflags=subprocess.CREATE_NO_WINDOW)
        except subprocess.TimeoutExpired as exc:
            raise ValueError("Current environment inspection timed out") from exc
        output.seek(0)
        content = output.read(MAX_BYTES + 1)
    if result.returncode or not 0 < len(content) <= MAX_BYTES:
        raise ValueError("Current environment inspection failed")
    report = json.loads(content)
    if not isinstance(report, dict) or report.get("policy") != CURRENT_POLICY:
        raise ValueError("Invalid environment inspection response")
    observed = identity(report)
    if (observed["runtimeImage"]["sha256"] != runtime_id
            or observed["workerIdentity"]["sha256"] != expected_worker):
        raise ValueError("Environment inspector invocation changed")
    return observed


def new_lease(prepared: PreparedReference, version_id: str,
              distribution: str) -> dict[str, Any] | None:
    # Historical evidence remains historical; do not fabricate a current lease.
    if "bootstrapPythonIdentity" not in prepared.report:
        return None
    _replay_identity(prepared.report)
    validate_execution(prepared.report)
    validate_core_guard(prepared.report.get("coreDumpGuard"))
    expected = identity(prepared.report)
    host = host_identity.validate(host_identity.current())
    current = inspect_current(expected["runtimeImage"]["sha256"], distribution)
    if current != expected:
        raise ValueError("Prepared environment differs from current installation")
    if host_identity.current() != host:
        raise ValueError("Windows/WSL host changed during environment inspection")
    issued = int(time.time())
    return {"policy": POLICY, "scope": "identity-freshness-only",
            "templateVersionId": version_id, "distribution": distribution,
            "preparedEvidenceSha256": hashlib.sha256(canonical(prepared.evidence)).hexdigest(),
            "identity": expected, "hostIdentity": host,
            "issuedAt": issued, "expiresAt": issued + LIFETIME_SECONDS}


def validate_lease(record: dict[str, Any], prepared: PreparedReference, version_id: str,
                   *, now: int) -> None:
    if (not isinstance(record, dict) or set(record) != {
        "policy", "scope", "templateVersionId", "distribution", "preparedEvidenceSha256",
        "identity", "hostIdentity", "issuedAt", "expiresAt",
    } or record.get("policy") != POLICY or record.get("scope") != "identity-freshness-only"
            or record.get("templateVersionId") != version_id
            or record.get("preparedEvidenceSha256")
            != hashlib.sha256(canonical(prepared.evidence)).hexdigest()
            or not isinstance(record.get("distribution"), str)
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", record["distribution"]) is None
            or type(record.get("issuedAt")) is not int or type(record.get("expiresAt")) is not int
            or record["expiresAt"] != record["issuedAt"] + LIFETIME_SECONDS
            or type(now) is not int or not record["issuedAt"] <= now < record["expiresAt"]):
        raise ValueError("Environment identity lease is invalid or expired")
    host_identity.validate(record["hostIdentity"])
    _replay_identity(prepared.report)
    validate_execution(prepared.report)
    validate_core_guard(prepared.report.get("coreDumpGuard"))
    if record["identity"] != identity(prepared.report):
        raise ValueError("Environment identity lease no longer matches preparation")


def save_lease(session: Session, storage: StorageService, version: TemplateVersion,
               generation: str, prepared: PreparedReference,
               record: dict[str, Any]) -> dict[str, Any]:
    validate_lease(record, prepared, version.id, now=int(time.time()))
    if re.fullmatch(r"[0-9a-f]{32}", generation) is None:
        raise ValueError("Invalid environment evidence generation")
    job = session.get(ProcessingJob, version.source_job_id)
    if job is None:
        raise ValueError("Environment evidence source job missing")
    payload = canonical(record)
    if not 0 < len(payload) <= MAX_BYTES:
        raise ValueError("Environment identity evidence is oversized")
    name = f"qualification-{generation}-environment"
    path = storage.write_bytes(
        f"artifacts/{job.id}/qualification/{generation}/environment.json", payload)
    artifact = JobArtifact(id=str(uuid4()), job_id=job.id, attempt_number=job.attempt_number,
                          artifact_name=name, relative_path=path,
                          sha256=hashlib.sha256(payload).hexdigest(), media_type="application/json")
    session.add(artifact)
    return {"policy": POLICY, "artifactId": artifact.id, "relativePath": path,
            "sha256": artifact.sha256, "name": name, "attemptNumber": job.attempt_number}


def load_saved_lease(session: Session, storage: StorageService, version: TemplateVersion,
                     prepared: PreparedReference, ref: dict[str, Any]) -> dict[str, Any]:
    """Load and validate the immutable saved lease without inspecting the live host."""
    if not isinstance(ref, dict) or ref.get("policy") != POLICY:
        raise ValueError("Invalid environment artifact reference")
    item = session.get(JobArtifact, ref["artifactId"])
    if (item is None or item.job_id != version.source_job_id
            or item.relative_path != ref["relativePath"] or item.sha256 != ref["sha256"]
            or item.artifact_name != ref["name"] or item.media_type != "application/json"
            or type(ref.get("attemptNumber")) is not int
            or item.attempt_number != ref["attemptNumber"]):
        raise ValueError("Environment artifact ownership changed")
    payload = storage.read_bytes(item.relative_path)
    if not 0 < len(payload) <= MAX_BYTES or hashlib.sha256(payload).hexdigest() != item.sha256:
        raise ValueError("Environment artifact contents changed")
    loaded = json.loads(payload)
    if not isinstance(loaded, dict):
        raise ValueError("Environment artifact contents are invalid")
    record: dict[str, Any] = loaded
    validate_lease(record, prepared, version.id, now=int(time.time()))
    return record


def load_lease(session: Session, storage: StorageService, version: TemplateVersion,
               prepared: PreparedReference, ref: dict[str, Any]) -> dict[str, Any]:
    record = load_saved_lease(session, storage, version, prepared, ref)
    if host_identity.current() != record["hostIdentity"]:
        raise ValueError("Windows/WSL host changed after qualification")
    current = inspect_current(record["identity"]["runtimeImage"]["sha256"], record["distribution"])
    if current != record["identity"]:
        raise ValueError("Environment installation changed after qualification")
    if host_identity.current() != record["hostIdentity"]:
        raise ValueError("Windows/WSL host changed during environment inspection")
    validate_lease(record, prepared, version.id, now=int(time.time()))
    return record
