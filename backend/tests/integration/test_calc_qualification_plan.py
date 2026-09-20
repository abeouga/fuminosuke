"""Source/plan revalidation with renderer stubs; never production certification."""
import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import JobArtifact, ProcessingJob, Template, TemplateVersion
from app.domain.calc_print_grid import MODEL
from app.services import print_preparation_service as preparation
from app.services import reference_qualification_service as qualification
from app.services.prepared_print_contract import render_key
from tests.integration import test_calc_print_preparation as existing

calc_fixture = existing.calc_fixture
prepared_fixture = existing.prepared_fixture
print_template = existing.print_template
saved_template = existing.saved_template


@pytest.fixture
def calc_reference(client, calc_fixture, monkeypatch):
    version, _, _, url, state, _, _ = calc_fixture
    original_import = preparation.import_wsl_geometry
    original_render = preparation.render_wsl_reference

    def imported(data, **kwargs):
        fods, report = original_import(data, **kwargs)
        return fods, {**report, "sourceSha256": hashlib.sha256(data).hexdigest(),
                      "format": "fods", "fontSource": "windows"}

    def rendered(data, **kwargs):
        pdf, images, report = original_render(data, **kwargs)
        return pdf, images, {
            **report, "sourceSha256": hashlib.sha256(data).hexdigest(),
            "pdfSha256": hashlib.sha256(pdf).hexdigest(),
            "imageSha256": [hashlib.sha256(image).hexdigest() for image in images],
            "status": "PASS", "format": "reference", "fontSource": "windows",
            "policy": "wsl-bwrap-cgroup-v1", "dpi": 150,
            "fontRequestPolicy": "wsl-fontconfig-face-v2",
        }

    monkeypatch.setattr(preparation, "import_wsl_geometry", imported)
    monkeypatch.setattr(preparation, "render_wsl_reference", rendered)
    response = client.post(url + "/prepare", params={
        "base_revision": state["revision"], "geometry_model": MODEL})
    assert response.status_code == 200, response.text
    calls = []

    def not_measured(*args, **kwargs):
        calls.append("measurement")
        return None

    for name in ("run_reference_replay", "run_prepared_glyphs", "run_prepared_geometry"):
        monkeypatch.setattr(qualification, name, not_measured)
    return version, url, response.json(), calls


def test_unchanged_calc_plan_is_checked_without_rewriting_or_certifying(client, calc_reference):
    version, url, ready, calls = calc_reference
    response = client.post(url + "/qualification", params={"base_revision": ready["revision"]})
    assert response.status_code == 200, response.text
    report = response.json()
    assert "preparedEvidence" in report
    assert report["qualified"] is False and report["environmentQualified"] is False
    assert "PRINT_GEOMETRY_CHANGED" not in report["codes"]
    assert len(calls) == 3
    assert client.get(url + "/qualification").json() == report
    assert len(calls) == 3  # GET does not invoke native measurement.
    assert client.get(url).json()["saved"] == ready["saved"]
    with client.app.state.database.session() as db:
        assert db.get(TemplateVersion, version).status == "DRAFT"


@pytest.mark.parametrize('module', ['calc_pdf_coordinates.py', 'calc_reference_precision.py'])
def test_coordinate_implementation_change_invalidates_qualification(
    client, calc_reference, monkeypatch, module,
):
    _, url, ready, calls = calc_reference
    assert client.post(url + '/qualification', params={
        'base_revision': ready['revision']}).status_code == 200
    original = Path.read_bytes

    def changed(path):
        raw = original(path)
        return raw + b'\n# changed coordinate implementation\n' if path.name == module else raw

    monkeypatch.setattr(Path, 'read_bytes', changed)
    assert client.get(url + '/qualification').json() == {
        'status': 'UNVERIFIED', 'qualified': False, 'codes': ['PRINT_LAYOUT_CHANGED']}
    assert len(calls) == 3


def test_source_plan_change_during_measurement_does_not_publish_evidence(
    client, calc_reference, monkeypatch,
):
    version, url, ready, _ = calc_reference
    service = qualification.ReferenceQualificationService
    original_source = service._source
    with client.app.state.database.session() as db:
        before = copy.deepcopy(db.get(TemplateVersion, version).quality_json)

    def changed_source(self, session, record, proposed=None):
        source = copy.deepcopy(original_source(self, session, record, proposed))
        source["fields"][0]["x"] += 1
        return source

    def change_during_measurement(*args, **kwargs):
        monkeypatch.setattr(service, "_source", changed_source)
        return None

    monkeypatch.setattr(qualification, "run_reference_replay", change_during_measurement)
    response = client.post(url + "/qualification", params={"base_revision": ready["revision"]})
    assert response.status_code == 409, response.text
    assert response.json()["code"] == "PRINT_GEOMETRY_CHANGED"
    with client.app.state.database.session() as db:
        assert db.get(TemplateVersion, version).quality_json == before


@pytest.mark.parametrize("change", ["rectangle", "range", "binding", "missing_field",
                                    "transform", "cell", "page_order", "shrink",
                                    "observation_missing", "observation_wrapped"])
def test_inconsistent_calc_plan_is_rejected_before_measurement(client, calc_reference, change):
    version, url, _, calls = calc_reference
    with client.app.state.database.session() as db:
        record = db.get(TemplateVersion, version)
        plan = copy.deepcopy(record.quality_json["printLayout"])
        page = next(p for p in plan["pages"] if any(f["bindings"] for f in p["fields"]))
        field = next(f for f in page["fields"] if f["bindings"])
        if change == "rectangle":
            field["rectangle"]["x"] += 1
        elif change == "range":
            field["sourceRange"] = "D32"
        elif change == "binding":
            field["bindings"][0]["targetPath"] = "document.birthDate"
        elif change == "missing_field":
            page["fields"].remove(field)
        elif change == "transform":
            page["transform"]["offsetX"] += 1
        elif change == "cell":
            page["cells"][0]["rectangle"]["width"] += 1
        elif change == "shrink":
            page["cells"][0]["shrinkToFit"] = True
        elif change == "observation_missing":
            plan["geometryEvidence"]["importReport"].pop("dimensionObservation")
        elif change == "observation_wrapped":
            plan["geometryEvidence"]["importReport"]["dimensionObservation"]["rows"][0][1] += 65536
        else:
            plan["pages"].reverse()
        # Integrity hashes cannot prove that saved geometry/meaning is derived
        # from the owned source. Rehashing must not authorize native work.
        plan["printFiles"]["renderKey"] = render_key(plan)
        record.quality_json = {**record.quality_json, "printLayout": plan}
        db.commit()
        revision = qualification.ReferenceQualificationService.revision(record)
    response = client.post(url + "/qualification", params={"base_revision": revision})
    assert response.status_code == 200, response.text
    report = response.json()
    assert report["qualified"] is False and report["status"] == "FAIL"
    assert report["codes"] == ["PRINT_GEOMETRY_CHANGED"]
    assert "preparedEvidence" not in report
    assert calls == []
    with client.app.state.database.session() as db:
        record = db.get(TemplateVersion, version)
        assert record.quality_json["printLayout"] == plan
        assert record.status == "DRAFT"
    if change == "observation_missing":
        # Older drafts remain readable without injecting new evidence or geometry.
        assert client.get(url).json()["saved"] == plan


def test_get_rechecks_source_plan_instead_of_trusting_saved_summary(
    client, calc_reference, monkeypatch,
):
    _, url, ready, calls = calc_reference
    assert client.post(url + "/qualification", params={
        "base_revision": ready["revision"]}).status_code == 200
    original_source = qualification.ReferenceQualificationService._source

    def changed_source(self, session, version, proposed=None):
        source = copy.deepcopy(original_source(self, session, version, proposed))
        owner = next(f for f in source["fields"] if f["bindings"])
        owner["bindings"][0]["targetPath"] = "document.birthDate"
        return source

    monkeypatch.setattr(qualification.ReferenceQualificationService, "_source", changed_source)
    result = client.get(url + "/qualification").json()
    assert result == {"status": "UNVERIFIED", "qualified": False,
                      "codes": ["REFERENCE_EVIDENCE_CHANGED"]}
    assert len(calls) == 3


def test_formal_record_reuses_saved_evidence_and_get_is_read_only(
    client, calc_reference, monkeypatch,
):
    version, url, ready, _ = calc_reference
    native_calls = []
    field_geometry = [{"page": 1, "codes": []}]
    glyph_report = {
        "policy": "fixture-glyph-v1",
        "allMatchedFacesDeclared": True,
        "glyphs": {
            "pdfSha256": "a" * 64,
            "textInstructionGlyphs": 1,
            "distinctFontCodes": 1,
        },
    }
    lease = {"issuedAt": 100, "expiresAt": 4_000_000_000, "identity": {"id": "x"}}
    refs = {
        "replay": {"kind": "replay"},
        "glyph": {"kind": "glyph"},
        "geometry": {"kind": "geometry"},
        "environment": {"kind": "environment"},
    }

    def replay(*_args, **_kwargs):
        native_calls.append("replay")
        return SimpleNamespace(deterministic=True)

    def glyphs(*_args, **_kwargs):
        native_calls.append("glyph")
        return glyph_report

    def geometry(*_args, **_kwargs):
        native_calls.append("geometry")
        return SimpleNamespace()

    def new_lease(*_args, **_kwargs):
        native_calls.append("environment")
        return lease

    monkeypatch.setattr(qualification, "run_reference_replay", replay)
    monkeypatch.setattr(qualification, "run_prepared_glyphs", glyphs)
    monkeypatch.setattr(qualification, "run_prepared_geometry", geometry)
    monkeypatch.setattr(qualification, "assess_geometry", lambda *_: field_geometry)
    monkeypatch.setattr(qualification, "save_reference_replay", lambda *_: refs["replay"])
    monkeypatch.setattr(qualification, "save_prepared_glyphs", lambda *_: refs["glyph"])
    monkeypatch.setattr(qualification, "save_prepared_geometry", lambda *_: refs["geometry"])
    monkeypatch.setattr(qualification.environment_evidence, "new_lease", new_lease)
    monkeypatch.setattr(
        qualification.environment_evidence,
        "save_lease",
        lambda *_: refs["environment"],
    )
    monkeypatch.setattr(qualification, "load_reference_replay", lambda *_: True)
    monkeypatch.setattr(
        qualification, "load_prepared_glyphs", lambda *_: copy.deepcopy(glyph_report)
    )
    monkeypatch.setattr(
        qualification, "load_prepared_geometry", lambda *_: copy.deepcopy(field_geometry)
    )
    monkeypatch.setattr(
        qualification.environment_evidence,
        "load_lease",
        lambda *_: copy.deepcopy(lease),
    )
    monkeypatch.setattr(
        qualification.environment_evidence,
        "load_saved_lease",
        lambda *_: copy.deepcopy(lease),
    )

    def environment_issued(*_args, qualification_issued_at=None, **_kwargs):
        return {
            "policy": "fixture-environment-v1",
            "qualified": True,
            "status": "PASS",
            "issuedAt": qualification_issued_at,
            "expiresAt": lease["expiresAt"],
            "unresolvedBlockers": [],
        }

    monkeypatch.setattr(
        qualification.environment_qualification, "issue", environment_issued
    )
    monkeypatch.setattr(
        qualification.geometry_qualification,
        "issue",
        lambda *_: {
            "policy": "fixture-geometry-v1",
            "qualified": True,
            "status": "PASS",
            "unresolvedBlockers": [],
        },
    )

    first = client.post(
        url + "/qualification", params={"base_revision": ready["revision"]}
    )
    assert first.status_code == 200, first.text
    assert first.json()["qualified"] is True
    assert native_calls == ["replay", "glyph", "geometry", "environment"]

    second = client.post(
        url + "/qualification", params={"base_revision": ready["revision"]}
    )
    assert second.status_code == 200, second.text
    report = second.json()
    assert native_calls == ["replay", "glyph", "geometry", "environment"]
    assert report["evidenceSource"] == "SAVED_PREPARED_GRAPH"
    assert report["qualified"] is True and report["environmentQualified"] is True
    assert report["anchorDiagnostic"]["status"] == "NON_BLOCKING_DIAGNOSTIC"

    with client.app.state.database.session() as db:
        before_get = copy.deepcopy(db.get(TemplateVersion, version).quality_json)
        assert db.get(TemplateVersion, version).status == "DRAFT"
    assert client.get(url + "/qualification").json() == report
    with client.app.state.database.session() as db:
        assert db.get(TemplateVersion, version).quality_json == before_get

    original_read = qualification.environment_qualification.Path.read_bytes

    def changed_issuer(path):
        content = original_read(path)
        return (
            content + b"\n# changed issuer"
            if path.name == "wsl_environment_qualification.py"
            else content
        )

    monkeypatch.setattr(
        qualification.environment_qualification.Path, "read_bytes", changed_issuer
    )
    implementation_changed = client.get(url + "/qualification").json()
    assert implementation_changed == {
        "status": "UNVERIFIED",
        "qualified": False,
        "codes": ["REFERENCE_EVIDENCE_CHANGED"],
    }
    monkeypatch.setattr(
        qualification.environment_qualification.Path, "read_bytes", original_read
    )

    monkeypatch.setattr(
        qualification.environment_evidence,
        "load_lease",
        lambda *_: (_ for _ in ()).throw(ValueError("expired")),
    )
    invalid = client.get(url + "/qualification").json()
    assert invalid == {
        "status": "UNVERIFIED",
        "qualified": False,
        "codes": ["REFERENCE_EVIDENCE_CHANGED"],
    }
    with client.app.state.database.session() as db:
        assert db.get(TemplateVersion, version).quality_json == before_get


@pytest.fixture
def qualified_calc_activation(client, calc_reference, monkeypatch):
    """Issue a structurally formal test record; native evidence is tested elsewhere."""
    version, url, ready, _ = calc_reference
    field_geometry = [{"page": 1, "codes": []}]
    glyph_report = {
        "policy": "fixture-glyph-v1",
        "allMatchedFacesDeclared": True,
        "glyphs": {
            "pdfSha256": "a" * 64,
            "textInstructionGlyphs": 1,
            "distinctFontCodes": 1,
        },
    }
    lease = {"issuedAt": 100, "expiresAt": 4_000_000_000, "identity": {"id": "x"}}
    refs = {
        "replay": {"kind": "replay"},
        "glyph": {"kind": "glyph"},
        "geometry": {"kind": "geometry"},
        "environment": {"kind": "environment"},
    }
    monkeypatch.setattr(
        qualification,
        "run_reference_replay",
        lambda *_args, **_kwargs: SimpleNamespace(deterministic=True),
    )
    monkeypatch.setattr(
        qualification, "run_prepared_glyphs", lambda *_args, **_kwargs: glyph_report
    )
    monkeypatch.setattr(
        qualification,
        "run_prepared_geometry",
        lambda *_args, **_kwargs: SimpleNamespace(),
    )
    monkeypatch.setattr(qualification, "assess_geometry", lambda *_: field_geometry)
    monkeypatch.setattr(qualification, "save_reference_replay", lambda *_: refs["replay"])
    monkeypatch.setattr(qualification, "save_prepared_glyphs", lambda *_: refs["glyph"])
    monkeypatch.setattr(qualification, "save_prepared_geometry", lambda *_: refs["geometry"])
    monkeypatch.setattr(
        qualification.environment_evidence, "new_lease", lambda *_args, **_kwargs: lease
    )
    monkeypatch.setattr(
        qualification.environment_evidence, "save_lease", lambda *_: refs["environment"]
    )
    monkeypatch.setattr(qualification, "load_reference_replay", lambda *_: True)
    monkeypatch.setattr(
        qualification, "load_prepared_glyphs", lambda *_: copy.deepcopy(glyph_report)
    )
    monkeypatch.setattr(
        qualification, "load_prepared_geometry", lambda *_: copy.deepcopy(field_geometry)
    )
    monkeypatch.setattr(
        qualification.environment_evidence,
        "load_lease",
        lambda *_: copy.deepcopy(lease),
    )
    monkeypatch.setattr(
        qualification.environment_evidence,
        "load_saved_lease",
        lambda *_: copy.deepcopy(lease),
    )

    def environment_issued(*_args, qualification_issued_at=None, **_kwargs):
        return {
            "policy": qualification.environment_qualification.POLICY,
            "implementationFingerprint": (
                qualification.environment_qualification.implementation_fingerprint()
            ),
            "qualified": True,
            "status": "PASS",
            "issuedAt": qualification_issued_at,
            "expiresAt": lease["expiresAt"],
            "unresolvedBlockers": [],
        }

    def geometry_issued(*_args, **_kwargs):
        return {
            "policy": qualification.geometry_qualification.POLICY,
            "implementationFingerprint": (
                qualification.geometry_qualification.implementation_fingerprint()
            ),
            "qualified": True,
            "status": "PASS",
            "unresolvedBlockers": [],
        }

    monkeypatch.setattr(
        qualification.environment_qualification, "issue", environment_issued
    )
    monkeypatch.setattr(qualification.geometry_qualification, "issue", geometry_issued)
    issued = client.post(
        url + "/qualification", params={"base_revision": ready["revision"]}
    )
    assert issued.status_code == 200, issued.text
    assert issued.json()["qualified"] is True
    return version, url, ready, lease


def _assert_draft_is_unchanged(client, version: str, active_id: str | None = None) -> None:
    with client.app.state.database.session() as db:
        draft = db.get(TemplateVersion, version)
        assert draft.status == "DRAFT"
        assert db.get(Template, draft.template_id).active_version_id == active_id


def test_formally_qualified_calc_approval_archives_old_active_atomically(
    client, qualified_calc_activation,
):
    version, url, ready, _ = qualified_calc_activation
    with client.app.state.database.session() as db:
        draft = db.get(TemplateVersion, version)
        old_job = ProcessingJob(
            job_type="PREPARE_TEMPLATE",
            status="CONFIRMED",
            request_hash="d" * 64,
            config_snapshot_json={},
        )
        db.add(old_job)
        db.flush()
        old = TemplateVersion(
            template_id=draft.template_id,
            version_number=draft.version_number + 100,
            mode=draft.mode,
            status="ACTIVE",
            canonical_width=draft.canonical_width,
            canonical_height=draft.canonical_height,
            source_job_id=old_job.id,
            clean_template_artifact_id=draft.clean_template_artifact_id,
            mask_artifact_id=draft.mask_artifact_id,
            config_relative_path=draft.config_relative_path,
            quality_json={},
            layout_json={},
        )
        db.add(old)
        db.flush()
        old_id = old.id
        db.get(Template, draft.template_id).active_version_id = old_id
        db.commit()

    response = client.post(
        f"/api/v1/template-versions/{version}/approve",
        params={"base_revision": ready["revision"]},
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "ACTIVE"
    with client.app.state.database.session() as db:
        active = db.get(TemplateVersion, version)
        assert active.status == "ACTIVE"
        assert db.get(TemplateVersion, old_id).status == "ARCHIVED"
        assert db.get(Template, active.template_id).active_version_id == version
        assert db.get(ProcessingJob, active.source_job_id).status == "CONFIRMED"
    assert client.get(url + "/qualification").json()["qualified"] is True


def test_saved_qualification_booleans_cannot_approve_without_formal_record(
    client, calc_reference,
):
    version, _, ready, _ = calc_reference
    with client.app.state.database.session() as db:
        draft = db.get(TemplateVersion, version)
        draft.quality_json = {
            **draft.quality_json,
            "qualified": True,
            "environmentQualified": True,
            "geometryQualified": True,
        }
        db.commit()
    response = client.post(
        f"/api/v1/template-versions/{version}/approve",
        params={"base_revision": ready["revision"]},
    )
    assert response.status_code == 409
    assert response.json()["code"] == "PAGED_TEMPLATE_QUALIFICATION_INVALID"
    _assert_draft_is_unchanged(client, version)


@pytest.mark.parametrize(
    "failure",
    [
        "expired",
        "environment",
        "graph",
        "source",
        "implementation",
        "geometry",
        "blocker",
        "plan",
        "version_binding",
    ],
)
def test_paged_approval_revalidates_current_qualification_evidence(
    client, qualified_calc_activation, monkeypatch, failure,
):
    version, _, ready, lease = qualified_calc_activation
    if failure == "expired":
        monkeypatch.setattr(
            qualification.environment_qualification.time,
            "time",
            lambda: lease["expiresAt"] + 1,
        )
    elif failure == "environment":
        monkeypatch.setattr(
            qualification.environment_evidence,
            "load_lease",
            lambda *_: (_ for _ in ()).throw(ValueError("changed environment")),
        )
    elif failure == "graph":
        with client.app.state.database.session() as db:
            draft = db.get(TemplateVersion, version)
            reference = draft.quality_json["printLayout"]["pages"][0]["referenceImage"]
        client.app.state.storage.write_bytes(reference["relativePath"], b"changed graph artifact")
    elif failure == "source":
        with client.app.state.database.session() as db:
            draft = db.get(TemplateVersion, version)
            source = db.scalar(select(JobArtifact).where(
                JobArtifact.job_id == draft.source_job_id,
                JobArtifact.artifact_name == "sourceWorkbook",
            ))
        client.app.state.storage.write_bytes(source.relative_path, b"changed source workbook")
    elif failure == "implementation":
        original_read = qualification.environment_qualification.Path.read_bytes

        def changed_issuer(path):
            content = original_read(path)
            return (
                content + b"\n# changed issuer"
                if path.name == "wsl_environment_qualification.py"
                else content
            )

        monkeypatch.setattr(
            qualification.environment_qualification.Path, "read_bytes", changed_issuer
        )
    elif failure == "geometry":
        monkeypatch.setattr(
            qualification.geometry_qualification,
            "issue",
            lambda *_args, **_kwargs: {
                "policy": qualification.geometry_qualification.POLICY,
                "implementationFingerprint": (
                    qualification.geometry_qualification.implementation_fingerprint()
                ),
                "qualified": False,
                "status": "FAIL",
                "unresolvedBlockers": ["GEOMETRY_TOLERANCE_UNQUALIFIED"],
            },
        )
    elif failure == "blocker":
        original_measure = qualification.measure_page

        def blocked(page, image):
            measured = original_measure(page, image)
            return {**measured, "codes": ["REFERENCE_FIELD_EDGES_UNVERIFIED"]}

        monkeypatch.setattr(qualification, "measure_page", blocked)
    if failure == "plan":
        with client.app.state.database.session() as db:
            draft = db.get(TemplateVersion, version)
            plan = copy.deepcopy(draft.quality_json["printLayout"])
            plan["commonScale"] -= 0.01
            draft.quality_json = {**draft.quality_json, "printLayout": plan}
            db.commit()
            ready["revision"] = qualification.ReferenceQualificationService.revision(draft)
    elif failure == "version_binding":
        with client.app.state.database.session() as db:
            draft = db.get(TemplateVersion, version)
            record = copy.deepcopy(draft.quality_json["referenceQualification"])
            report = json.loads(client.app.state.storage.read_bytes(record["relativePath"]))
            report["templateVersionId"] = "another-template-version"
            payload = json.dumps(report, ensure_ascii=False, sort_keys=True).encode()
            client.app.state.storage.write_bytes(record["relativePath"], payload)
            record["sha256"] = hashlib.sha256(payload).hexdigest()
            draft.quality_json = {**draft.quality_json, "referenceQualification": record}
            db.commit()

    response = client.post(
        f"/api/v1/template-versions/{version}/approve",
        params={"base_revision": ready["revision"]},
    )
    assert response.status_code == 409, response.text
    assert response.json()["code"] == "PAGED_TEMPLATE_QUALIFICATION_INVALID"
    _assert_draft_is_unchanged(client, version)


def test_paged_approval_rejects_revision_conflict_before_activation(
    client, qualified_calc_activation,
):
    version, _, _, _ = qualified_calc_activation
    response = client.post(
        f"/api/v1/template-versions/{version}/approve",
        params={"base_revision": "0" * 64},
    )
    assert response.status_code == 409
    assert response.json()["code"] == "PRINT_LAYOUT_CONFLICT"
    _assert_draft_is_unchanged(client, version)


def test_paged_approval_commit_failure_leaves_active_pointer_and_statuses_unchanged(
    client, qualified_calc_activation, monkeypatch,
):
    version, _, ready, _ = qualified_calc_activation

    def fail_commit(session):
        session.flush()
        raise RuntimeError("injected paged approval commit failure")

    with monkeypatch.context() as patch:
        patch.setattr(Session, "commit", fail_commit)
        with pytest.raises(RuntimeError, match="injected paged approval commit failure"):
            client.post(
                f"/api/v1/template-versions/{version}/approve",
                params={"base_revision": ready["revision"]},
            )
    _assert_draft_is_unchanged(client, version)
