"""Connected owned-evidence roundtrip, with no native rendering or certificate."""
import hashlib
import io
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PIL import Image, ImageDraw

from app.db.models import ProcessingJob
from app.services import prepared_reference_geometry as service
from app.services.renderer_pdf_strokes import inventory
from tests.unit import test_calc_reference_precision as precision
from tests.unit import test_prepared_reference_geometry as existing

reference = existing.reference
prepared_sample = existing.sample
precision_sample = precision.sample


@pytest.fixture
def combined(prepared_sample, precision_sample):
    prepared = prepared_sample[0]
    plan, page, vector, observation = precision_sample
    page.update(width=1250, height=1667, range="A2:C3", referenceImage={})
    with Image.new("RGB", (1250, 1667), "white") as image:
        ImageDraw.Draw(image).rectangle((66, 74, 81, 89), outline="black", width=2)
        out = io.BytesIO()
        image.save(out, "PNG")
    prepared.images = [out.getvalue()]
    prepared.report["imageSizes"] = [[1250, 1667]]
    existing.bind_images(prepared, plan)
    report = existing.vector_report(prepared)
    item = report["vectors"]["pages"][0]
    item.update(vector)
    left, right = 31.918, 39.118
    top, bottom = 764.482, 757.282
    operations = []
    for a, b in (((left, top), (right, top)), ((right, top), (right, bottom)),
                 ((right, bottom), (left, bottom)), ((left, bottom), (left, top))):
        operations.extend([(list(a), b"m"), (list(b), b"l"), ([], b"S")])
    item["strokes"] = inventory(operations)
    return prepared, plan, service.GeometryEvidence(report)


def test_connected_comparison_uses_full_owned_inputs_without_mutating_plan(combined):
    before = deepcopy(combined)
    result = service.assess_geometry(*combined)[0]
    assert combined == before
    assert result["coordinatePrecision"]["status"] == "MATCH"
    assert result["coordinatePrecision"]["fields"][0]["cellId"] == "merged-arbitrary"
    assert result["thresholdQualified"] is result["productionQualified"] is False


@pytest.mark.parametrize('failure', [False, True])
def test_precision_save_load_recomputes_and_retains_failure(combined, failure):
    prepared, plan, evidence = combined
    if failure:
        for line in evidence.original_vectors["vectors"]["pages"][0]["strokes"]["segments"]:
            line["from"][0] += .001
            line["to"][0] += .001
    expected = service.assess_geometry(*combined)
    assert expected[0]["coordinatePrecision"]["status"] == ("UNVERIFIED" if failure else "MATCH")
    if failure:
        assert "REFERENCE_PDF_COORDINATES_UNVERIFIED" in expected[0]["codes"]
    version = SimpleNamespace(id="version-arbitrary", source_job_id="job-arbitrary",
                              quality_json={"printLayout": plan})
    job = SimpleNamespace(id=version.source_job_id, attempt_number=1)
    items, contents = {}, {}
    session, storage = Mock(), Mock()
    session.get.side_effect = lambda model, key: job if model is ProcessingJob else items.get(key)
    session.add.side_effect = lambda item: items.update({item.id: item})

    def write(path, content):
        contents[path] = content
        return path

    storage.write_bytes.side_effect = write
    storage.read_bytes.side_effect = lambda path: contents[path]
    ref = service.save_prepared_geometry(session, storage, version, "f" * 32, prepared, evidence)
    snapshots = deepcopy(contents)
    loaded = service.load_prepared_geometry(session, storage, version, prepared, ref)
    assert loaded == expected and contents == snapshots
    # A caller-mutated summary cannot become the next authoritative response.
    loaded[0]["coordinatePrecision"]["status"] = "FORGED"
    assert service.load_prepared_geometry(session, storage, version, prepared, ref) == expected
    assert all(hashlib.sha256(contents[p["relativePath"]]).hexdigest() == p["sha256"]
               for p in ref["artifacts"])
