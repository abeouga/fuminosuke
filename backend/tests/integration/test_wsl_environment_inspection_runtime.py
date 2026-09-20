from pathlib import Path

import pytest

from app.services import wsl_environment_evidence as evidence
from app.services.renderer_wsl_worker import worker_identity
from tests.integration.test_wsl_frozen_runtime import installed_runtime

__all__ = ["installed_runtime"]


def test_no_document_inspection_rechecks_selected_real_environment(installed_runtime):
    first = evidence.inspect_current(installed_runtime[0], "Ubuntu-22.04")
    second = evidence.inspect_current(installed_runtime[0], "Ubuntu-22.04")
    assert first == second
    assert first["runtimeImage"]["sha256"] == installed_runtime[0]
    assert first["workerIdentity"] == worker_identity(
        Path(__file__).parents[2] / "app/services/renderer_wsl_worker.py")
    assert first["fontDirectoryIdentity"]["fileCount"] > 0
    assert "nativeDependencies" in first["bootstrapPythonIdentity"]
    assert not {"qualified", "environmentQualified", "productionQualified"} & first.keys()


def test_inspection_does_not_replace_a_missing_selected_image(installed_runtime):
    with pytest.raises(ValueError, match="inspection failed"):
        evidence.inspect_current(installed_runtime[1], "Ubuntu-22.04")
