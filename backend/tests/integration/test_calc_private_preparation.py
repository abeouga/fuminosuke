"""Private-copy preparation with renderer stubs; never native qualification."""
import copy
from uuid import uuid4

import pytest

from app.core.errors import AppError
from app.db.models import TemplateVersion
from app.services import calc_workbook_geometry as calc
from app.services import print_preparation_service as preparation
from app.services.renderer_process import renderer_mutex
from app.services.wsl_workbook_geometry import MODEL as OLD_MODEL
from scripts.dry_run_print_preparation import dry_run
from tests.integration import test_calc_print_preparation as existing

calc_fixture = existing.calc_fixture
prepared_fixture = existing.prepared_fixture
saved_template = existing.saved_template
print_template = existing.print_template


@pytest.mark.parametrize("failure", [False, True])
def test_explicit_private_copy_prepare_never_changes_source_database_or_artifacts(
    client, calc_fixture, monkeypatch, tmp_path, failure,
):
    version, _, _, url, state, _, _ = calc_fixture
    # Both import and render are explicit stubs in calc_fixture. Keep the real
    # mutex semantics but isolate this test's resource from native full tests.
    mutex_name = "Local\\CalcPrivateCopyFixture-" + uuid4().hex
    monkeypatch.setattr(preparation, "renderer_mutex", lambda: renderer_mutex(mutex_name))

    def old_fixed_dimensions(*args):
        measured = calc.dimensions(*args)
        measured["model"] = OLD_MODEL
        return measured

    monkeypatch.setattr(preparation, "dimensions", old_fixed_dimensions)
    response = client.post(url + "/prepare", params={"base_revision": state["revision"]})
    assert response.status_code == 200, response.text
    before = response.json()
    storage = client.app.state.storage
    with client.app.state.database.session() as session:
        row = session.get(TemplateVersion, version)
        original = copy.deepcopy((row.status, row.layout_json, row.quality_json))
    destination = tmp_path / "private-copy"
    destination.mkdir()
    monkeypatch.setattr("scripts.dry_run_print_preparation.tempfile.mkdtemp",
                        lambda **kwargs: str(destination))
    if failure:
        def reject(*args, **kwargs):
            raise RuntimeError("Injected renderer failure")
        monkeypatch.setattr(preparation, "render_wsl_reference", reject)
        with pytest.raises(AppError, match="隔離環境"):
            dry_run(version, existing.pinned.RUNTIME_ID, storage.root,
                    geometry_model="wsl-calc-print-grid-v1")
    else:
        result = dry_run(version, existing.pinned.RUNTIME_ID, storage.root,
                         geometry_model="wsl-calc-print-grid-v1")
        assert result["copyDirectory"] == str(destination)
        assert result["fieldComparison"] == "predicted-new-model"
        assert result["beforeModel"] == OLD_MODEL
        assert result["afterModel"] == "wsl-calc-print-grid-v1"
        assert all(result["preserved"].values())
        assert result["originalUnchanged"] is True
        assert result["productionQualified"] is False
    assert client.get(url).json() == before
    with client.app.state.database.session() as session:
        row = session.get(TemplateVersion, version)
        assert (row.status, row.layout_json, row.quality_json) == original
