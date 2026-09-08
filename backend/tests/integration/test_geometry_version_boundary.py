from __future__ import annotations

import copy

from app.db.models import TemplateVersion
from app.domain.print_layout import PrintLayoutRequest, page_plan
from app.services.print_layout_service import PrintLayoutService, digest
from app.services.print_layout_source import read_source
from app.services.reference_qualification_service import ReferenceQualificationService
from tests.integration.test_print_layout_api import print_template  # noqa: F401


def test_legacy_plan_is_never_silently_upgraded(client, request, monkeypatch):
    from app.services import print_layout_service as module
    version_id, _, content, layout = request.getfixturevalue("print_template")
    source = read_source(content, "帳票", layout)
    old = page_plan(source, PrintLayoutRequest(base_revision="0" * 64, break_after_rows=[31]))
    old.pop("geometryModelVersion")
    old.pop("geometryEvidence")
    old["layoutSha256"] = digest(layout)
    with client.app.state.database.session() as session:
        version = session.get(TemplateVersion, version_id)
        version.quality_json = {**version.quality_json, "printLayout": copy.deepcopy(old)}
        session.commit()

    def unexpected(*args):
        raise AssertionError("Historical plan reached new Office geometry")

    monkeypatch.setattr(module, "office_geometry", unexpected)
    service = PrintLayoutService(client.app.state.database, client.app.state.storage)
    state = service.get(version_id)
    assert state["saved"] == old
    assert state["source"]["width"] == source["width"]
    assert state["source"]["geometryModelVersion"] == "fixed-7px-plus-5-v1"
    with client.app.state.database.session() as session:
        version = session.get(TemplateVersion, version_id)
        assert version.layout_json == layout
        assert version.quality_json["printLayout"] == old


def test_qualification_fingerprint_changes_with_model_implementation(client, request, monkeypatch):
    from app.services import reference_qualification_service as module
    version_id = request.getfixturevalue("print_template")[0]
    service = ReferenceQualificationService(client.app.state.database, client.app.state.storage)
    with client.app.state.database.session() as session:
        version = session.get(TemplateVersion, version_id)
        before = service._fingerprint(version)
        monkeypatch.setattr(module, "GEOMETRY_MODEL_VERSION", "future-geometry-v2")
        assert service._fingerprint(version) != before
