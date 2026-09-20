import hashlib
import json

from app.db.models import TemplateVersion
from tests.integration import test_prepared_reference_qualification as reference_tests

prepared_fixture = reference_tests.prepared_fixture
print_template = reference_tests.print_template
saved_template = reference_tests.saved_template
reference_fixture = reference_tests.reference_fixture


def test_wsl_api_separates_stages_but_does_not_admit_unqualified_template(
    client, reference_fixture,
):
    version, _, url, ready, _ = reference_fixture
    response = client.post(url + "/qualification", params={"base_revision": ready["revision"]})
    assert response.status_code == 200, response.text
    report = response.json()
    stages = report["safetyStages"]
    assert stages["policy"] == "paged-two-stage-safety-v1"
    assert stages["activation"]["requiresSubmittedScan"] is False
    assert stages["activation"]["status"] == "BLOCKED"
    assert "CROP_CLEARANCE_UNVERIFIED" not in stages["activation"]["codes"]
    assert "WSL_ENVIRONMENT_QUALIFICATION_UNVERIFIED" in stages["activation"]["codes"]
    assert stages["execution"]["status"] == "NOT_EVALUATED"
    assert stages["execution"]["requiredBefore"] == "PROVIDER_REQUEST"
    assert all(page["status"] == "DEFERRED_UNTIL_INPUT" for page in report["cropSafety"])
    assert report["qualified"] is False and report["environmentQualified"] is False
    assert client.get(url + "/qualification").json() == report
    assert client.post(f"/api/v1/template-versions/{version}/approve").json()["code"] \
        == "PRINT_LAYOUT_OCR_NOT_AVAILABLE"


def test_saved_stage_and_deferred_crop_flags_are_recomputed_not_trusted(
    client, reference_fixture,
):
    version, _, url, ready, _ = reference_fixture
    response = client.post(url + "/qualification", params={"base_revision": ready["revision"]})
    assert response.status_code == 200, response.text
    expected = response.json()
    storage = client.app.state.storage
    with client.app.state.database.session() as session:
        record = session.get(TemplateVersion, version)
        ref = record.quality_json["referenceQualification"]
        forged = json.loads(storage.read_bytes(ref["relativePath"]))
        forged["safetyStages"] = {"activation": {"status": "PASS", "certificateIssued": True},
                                   "execution": {"status": "PASS", "checks": []}}
        forged["cropSafety"] = [{"status": "PASS", "qualified": True}]
        encoded = json.dumps(forged).encode()
        storage.write_bytes(ref["relativePath"], encoded)
        record.quality_json = {**record.quality_json, "referenceQualification": {
            **ref, "sha256": hashlib.sha256(encoded).hexdigest()}}
        session.commit()
    checked = client.get(url + "/qualification").json()
    assert checked["safetyStages"] == expected["safetyStages"]
    assert checked["cropSafety"] == expected["cropSafety"]
    assert checked["qualified"] is False
