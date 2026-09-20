"""Separate activation evidence from checks requiring a submitted scan.

This is an assessment description, not a certificate issuer or an admission
token. Callers must first revalidate owned evidence. No saved stage/boolean can
authorize activation or provider calls.
"""
from __future__ import annotations

from typing import Any

POLICY = "paged-two-stage-safety-v1"
INPUT_CHECKS = ("PAGE_INPUT_IDENTITY", "SCAN_REGISTRATION", "CROP_CORRESPONDENCE",
                "INK_CLEARANCE")


def deferred_crop_checks(pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{
        "page": page["page"], "policy": POLICY, "qualified": False,
        "status": "DEFERRED_UNTIL_INPUT", "requiredBefore": "PROVIDER_REQUEST",
        "fields": [{"cellId": field["cellId"], "sourceRange": field["sourceRange"],
                    "status": "DEFERRED_UNTIL_INPUT"}
                   for field in page["fields"] if field.get("bindings")],
    } for page in pages]


def stage_assessment(
    codes: list[str], blockers: list[str], *, certificate_issued: bool = False
) -> dict[str, Any]:
    # Inputs are newly computed diagnostic codes, never the stored stage result.
    activation = sorted(set(codes) | set(blockers))
    if type(certificate_issued) is not bool or (certificate_issued and activation):
        raise ValueError("Invalid activation certificate assessment")
    return {
        "policy": POLICY,
        "activation": {
            "status": (
                "BLOCKED" if activation else
                "QUALIFIED" if certificate_issued else
                "READY_FOR_QUALIFICATION"
            ),
            "codes": activation,
            "requiresSubmittedScan": False,
            # Empty diagnostics alone still do not issue a certificate.  Only
            # the formal issuer may pass True after revalidating owned evidence.
            "certificateIssued": certificate_issued,
        },
        "execution": {
            "status": "NOT_EVALUATED", "requiredBefore": "PROVIDER_REQUEST",
            "checks": list(INPUT_CHECKS), "requiresSubmittedScan": True,
        },
    }
