"""Real fixed-runtime preparation + glyph qualification, on a synthetic workbook."""
from app.db.models import TemplateVersion
from app.services import print_preparation_service as preparation
from tests.integration import test_print_preparation as existing
from tests.integration.test_wsl_glyph_helper import glyph_runtime

glyph_runtime = glyph_runtime
print_template = existing.print_template
saved_template = existing.saved_template


def test_real_glyph_report_roundtrips_through_qualification_without_opening_gate(
    client, saved_template, glyph_runtime, monkeypatch,
):
    version, _, _, url, state = saved_template
    monkeypatch.setattr(preparation.PrintLayoutService, "_source", existing.REAL_SOURCE)
    monkeypatch.setattr(client.app.state.settings.app, "print_pdf_renderer", "wsl")
    monkeypatch.setattr(client.app.state.settings.app, "print_wsl_runtime_id", glyph_runtime)
    result = client.post(url + "/prepare", params={"base_revision": state["revision"]})
    assert result.status_code == 200, result.text
    prepared = result.json()
    response = client.post(url + "/qualification", params={"base_revision": prepared["revision"]})
    assert response.status_code == 200, response.text
    report = response.json()
    assert report["glyphVerification"]["status"] == "PASS", report
    assert report["glyphVerification"]["pdfSha256"] == prepared["saved"]["printFiles"][
        "pdf"]["sha256"]
    assert report["glyphVerification"]["textInstructionGlyphs"] > 0
    assert report["glyphVerification"]["visibilityAndLayoutVerified"] is False
    assert "FONT_GLYPH_UNVERIFIED" not in report["productionBlockers"]
    assert report["qualified"] is False and report["environmentQualified"] is True
    assert report["deterministic"] is True
    assert "geometryEvidence" in report
    assert len(report["fieldGeometry"]) == 2
    assert report["fieldGeometry"][-1]["fieldCount"] == 1
    assert report["fieldGeometry"][-1]["maxErrorPx"] is not None
    assert all(page["productionQualified"] is False for page in report["fieldGeometry"])
    assert client.get(url + "/qualification").json() == report
    assert client.get(url).json()["saved"] == prepared["saved"]
    with client.app.state.database.session() as db:
        assert db.get(TemplateVersion, version).status == "DRAFT"
    assert client.post(f"/api/v1/template-versions/{version}/approve").json()["code"] == (
        "PRINT_LAYOUT_OCR_NOT_AVAILABLE")
