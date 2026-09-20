from copy import deepcopy

import pytest

from app.services.paged_safety_stages import deferred_crop_checks, stage_assessment


def test_missing_scan_is_deferred_not_a_template_geometry_failure():
    pages = [{"page": 7, "fields": [
        {"cellId": "cell-a", "sourceRange": "B2:C3", "bindings": [{"mappingId": "m-a"}]},
        {"cellId": "heading-a", "sourceRange": "A1", "bindings": []},
    ]}]
    before = deepcopy(pages)
    checks = deferred_crop_checks(pages)
    assert pages == before
    assert checks[0]["page"] == 7 and checks[0]["qualified"] is False
    assert checks[0]["status"] == "DEFERRED_UNTIL_INPUT"
    assert checks[0]["requiredBefore"] == "PROVIDER_REQUEST"
    assert checks[0]["fields"] == [{"cellId": "cell-a", "sourceRange": "B2:C3",
                                     "status": "DEFERRED_UNTIL_INPUT"}]


def test_empty_diagnostic_codes_do_not_issue_a_certificate_or_bypass_runtime_checks():
    stages = stage_assessment([], [])
    assert stages["activation"] == {
        "status": "READY_FOR_QUALIFICATION", "codes": [],
        "requiresSubmittedScan": False, "certificateIssued": False,
    }
    assert stages["execution"] == {
        "status": "NOT_EVALUATED", "requiredBefore": "PROVIDER_REQUEST",
        "requiresSubmittedScan": True,
        "checks": ["PAGE_INPUT_IDENTITY", "SCAN_REGISTRATION", "CROP_CORRESPONDENCE",
                   "INK_CLEARANCE"],
    }


def test_geometry_and_environment_failures_remain_activation_blockers():
    stages = stage_assessment(["GEOMETRY_FAILED"], ["ENVIRONMENT_UNVERIFIED", "GEOMETRY_FAILED"])
    assert stages["activation"]["status"] == "BLOCKED"
    assert stages["activation"]["codes"] == ["ENVIRONMENT_UNVERIFIED", "GEOMETRY_FAILED"]
    assert stages["activation"]["certificateIssued"] is False


def test_returned_stage_edits_do_not_change_future_assessments():
    stages = stage_assessment([], [])
    stages["execution"]["checks"].clear()
    stages["activation"]["certificateIssued"] = True
    fresh = stage_assessment([], [])
    assert len(fresh["execution"]["checks"]) == 4
    assert fresh["activation"]["certificateIssued"] is False


def test_only_formal_issuer_can_mark_activation_evidence_qualified():
    stages = stage_assessment([], [], certificate_issued=True)
    assert stages["activation"]["status"] == "QUALIFIED"
    assert stages["activation"]["certificateIssued"] is True
    assert stages["execution"]["status"] == "NOT_EVALUATED"
    with pytest.raises(ValueError, match="certificate"):
        stage_assessment(["GEOMETRY_FAILED"], [], certificate_issued=True)
