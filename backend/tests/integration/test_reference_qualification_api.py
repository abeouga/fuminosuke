from __future__ import annotations

import json

from app.db.models import TemplateVersion
from app.domain.print_layout import print_error
from tests.integration.test_print_layout_api import body, print_template  # noqa: F401


def save(client, version_id):
    url = f"/api/v1/template-versions/{version_id}/print-layout"
    state = client.get(url).json()
    response = client.put(url, json=body(state))
    assert response.status_code == 200, response.text
    return url, response.json()


def test_unavailable_renderer_evidence_persists_and_invalidates(client, request, monkeypatch):
    from app.services import reference_qualification_service as module
    version_id = request.getfixturevalue("print_template")[0]
    url, state = save(client, version_id)

    def unavailable(*args, **kwargs):
        raise print_error("LibreOffice missing", "RENDERER_UNAVAILABLE", 409)

    monkeypatch.setattr(module, "render_office", unavailable)
    response = client.post(url + "/qualification", params={"base_revision": state["revision"]})
    assert response.status_code == 200, response.text
    report = response.json()
    assert report["status"] == "UNSUPPORTED"
    assert report["qualified"] is False
    assert report["codes"] == ["RENDERER_UNAVAILABLE"]
    assert client.get(url + "/qualification").json() == report
    with client.app.state.database.session() as session:
        version = session.get(TemplateVersion, version_id)
        assert version.status == "DRAFT"
        before_plan = version.quality_json["printLayout"]
        assert before_plan == state["saved"]
    altered = {"baseRevision": state["revision"], "breakAfterRows": [20, 40]}
    assert client.put(url, json=altered).status_code == 200
    result = client.get(url + "/qualification").json()
    assert result["status"] == "UNVERIFIED"
    assert result["codes"] == ["PRINT_LAYOUT_CHANGED"]


def test_unsupported_environment_preflight_remains_visible_after_reload(
    client, request, monkeypatch,
):
    from app.services import reference_qualification_service as module

    version_id = request.getfixturevalue("print_template")[0]
    url, state = save(client, version_id)

    def unsupported(*args, **kwargs):
        raise print_error(
            "Unsupported active or advanced XML element: AlternateContent.",
            "UNSUPPORTED_XLSX_FEATURE",
            409,
        )

    monkeypatch.setattr(module, "environment_identity", unsupported)
    response = client.post(url + "/qualification", params={"base_revision": state["revision"]})
    assert response.status_code == 200, response.text
    report = response.json()
    assert report["status"] == "UNSUPPORTED"
    assert report["qualified"] is False
    assert report["codes"] == ["UNSUPPORTED_XLSX_FEATURE"]
    assert "AlternateContent" in report["detail"]
    assert "environmentQualification" not in report

    assert client.get(url + "/qualification").json() == report


def test_forged_evidence_and_changed_reference_cannot_be_qualified(client, request, monkeypatch):
    from app.services import reference_qualification_service as module
    from app.services.office_reference_renderer import sha
    version_id = request.getfixturevalue("print_template")[0]
    url, state = save(client, version_id)

    def unavailable(*args, **kwargs):
        raise print_error("LibreOffice missing", "RENDERER_UNAVAILABLE", 409)

    monkeypatch.setattr(module, "render_office", unavailable)
    assert client.post(url + "/qualification", params={
        "base_revision": state["revision"]}).status_code == 200
    with client.app.state.database.session() as session:
        version = session.get(TemplateVersion, version_id)
        record = version.quality_json["referenceQualification"]
        storage = client.app.state.storage
        payload = json.loads(storage.read_bytes(record["relativePath"]))
        payload.update(qualified=True, status="PASS")
        forged = json.dumps(payload).encode()
        storage.write_bytes(record["relativePath"], forged)
        version.quality_json = {**version.quality_json, "referenceQualification": {
            **record, "sha256": sha(forged)}}
        session.commit()
    assert client.get(url + "/qualification").json()["qualified"] is False
    reference = state["saved"]["pages"][0]["referenceImage"]
    storage.write_bytes(reference["relativePath"], b"changed image")
    result = client.get(url + "/qualification").json()
    assert result["status"] == "UNVERIFIED"
    assert result["qualified"] is False


def test_stale_request_never_starts_renderer(client, request, monkeypatch):
    from app.services import reference_qualification_service as module
    url, _ = save(client, request.getfixturevalue("print_template")[0])

    def unexpected(*args):
        raise AssertionError("stale revision reached renderer")

    monkeypatch.setattr(module, "render_office", unexpected)
    result = client.post(url + "/qualification", params={"base_revision": "0" * 64})
    assert result.status_code == 409


def test_diagnostic_images_are_preserved_separately_and_config_changes_expire_evidence(
    client, request, monkeypatch,
):
    from app.services import reference_qualification_service as module
    from app.services.print_layout_renderer import render_page
    version_id = request.getfixturevalue("print_template")[0]
    url, state = save(client, version_id)
    identity = {"type": "fixture", "version": "test-only"}
    monkeypatch.setattr(module, "renderer_identity", lambda: identity)
    monkeypatch.setattr(module, "render_office", lambda content, plan, **kwargs: (
        identity, [render_page(p) for p in plan["pages"]]))
    result = client.post(url + "/qualification", params={"base_revision": state["revision"]})
    assert result.status_code == 200, result.text
    report = result.json()
    assert report["deterministic"] is True
    assert report["qualified"] is False
    assert len(report["diagnosticImages"]) == 2
    assert client.get(url).json()["saved"] == state["saved"]
    with client.app.state.database.session() as session:
        version = session.get(TemplateVersion, version_id)
        assert version.status == "DRAFT"
        path = version.config_relative_path
        data = client.app.state.storage.read_bytes(path)
        client.app.state.storage.write_bytes(path, data + b"\n# revised\n")
    result = client.get(url + "/qualification").json()
    assert result["qualified"] is False
    assert result["codes"] == ["PRINT_LAYOUT_CHANGED"]
