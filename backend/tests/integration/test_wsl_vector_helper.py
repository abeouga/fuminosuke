"""Real fixed vector entrypoint; synthetic workbook, no production admission."""
import hashlib
from copy import deepcopy

import pytest

from app.services.renderer_splash_calibration import assess as assess_calibration
from app.services.renderer_vector_bridge import code_bundle, inspect_wsl_vectors
from tests.integration import test_wsl_glyph_helper as glyph_tests

glyph_runtime = glyph_tests.glyph_runtime
reference = glyph_tests.reference


def test_real_vector_helper_covers_every_page_and_preserves_glyph_proof(reference):
    pdf, renderer = reference
    report = inspect_wsl_vectors(pdf, renderer)
    assert report["codeBundleSha256"] == hashlib.sha256(code_bundle()).hexdigest()
    assert report["runtimeImage"] == renderer["runtimeImage"]
    assert report["networkDenied"] is True and report["uid"] == 65534
    assert report["productionQualified"] is False
    assert [p["textInstructionGlyphs"] for p in report["glyphs"]["pages"]] == [2, 0]
    vectors = report["vectors"]
    assert vectors["pageCount"] == 2
    assert [p["renderedPage"] for p in vectors["pages"]] == [1, 2]
    assert all(p["strokes"]["segments"] for p in vectors["pages"])
    assert all(p["textProgram"]["policy"] == "wsl-pdf-nonstroke-text-program-v1"
               and p["textProgram"]["status"] == "RECORDED"
               and p["textProgram"]["visibilityVerified"] is False for p in vectors["pages"])
    assert vectors["segmentCount"] == sum(len(p["strokes"]["segments"]) for p in vectors["pages"])
    assert vectors["visibilityVerified"] is False and vectors["productionQualified"] is False
    assert [p["contentSha256"] for p in vectors["pages"]] == [
        p["contentSha256"] for p in report["glyphs"]["pages"]]
    assert report["strokeProbes"]["sourcePdfSha256"] == renderer["pdfSha256"]
    assert report["strokeProbes"]["visibilityVerified"] is False
    assert report["strokeProbes"]["productionQualified"] is False
    calibration = assess_calibration(report["splashCalibration"])
    assert calibration["status"] == "OBSERVED" and calibration["unmatchedCases"] == []
    assert calibration["caseCount"] == 256 and calibration["sampleCount"] == 4352
    assert calibration["productionQualified"] is False


def test_real_vector_report_cannot_claim_different_reference_raster_dimensions(reference):
    pdf, renderer = reference
    changed = deepcopy(renderer)
    changed["imageSizes"][1][0] += 1
    with pytest.raises(ValueError, match="raster page dimensions differ"):
        inspect_wsl_vectors(pdf, changed)
