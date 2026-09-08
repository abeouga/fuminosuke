from __future__ import annotations

import importlib
from pathlib import Path

import pytest

from app.core.errors import AppError
from app.services import office_reference_renderer as office
from app.services.renderer_temp import private_root
from tests.unit.test_reference_qualification import case


@pytest.mark.parametrize("failure", ["process", "missing-pdf", "invalid-pdf", "png"])
def test_render_failures_release_private_source_profile_and_mutex(monkeypatch, failure):
    content, plan = case()
    root = private_root()
    before = set(root.iterdir())
    monkeypatch.setattr(office, "renderer_identity", lambda: {"executable": "fixture-only"})

    def convert(command, **kwargs):
        if failure == "process":
            raise RuntimeError("synthetic process exception")
        directory = Path(command[command.index("--outdir") + 1])
        if failure == "invalid-pdf":
            (directory / "page.pdf").write_bytes(b"invalid")
        elif failure == "png":
            pdfium = importlib.import_module("pypdfium2")
            with pdfium.PdfDocument.new() as pdf:
                page = pdf.new_page(595, 842)
                page.close()
                pdf.save(str(directory / "page.pdf"))
        return {"exitCode": 0}

    monkeypatch.setattr(office, "run_contained", convert)
    if failure == "png":
        def fail_png(*args, **kwargs):
            raise RuntimeError("synthetic raster failure")

        monkeypatch.setattr(office, "rasterize", fail_png)
    with pytest.raises((RuntimeError, AppError, importlib.import_module("pypdfium2").PdfiumError)):
        office.render_office(content, plan)
    assert set(root.iterdir()) == before
    assert office._RENDER_LOCK.acquire(blocking=False)
    office._RENDER_LOCK.release()


def test_malformed_package_never_reaches_converter(monkeypatch):
    _, plan = case()
    monkeypatch.setattr(office, "renderer_identity", lambda: {"executable": "fixture-only"})

    def unexpected(*args, **kwargs):
        raise AssertionError("malformed source reached Office")

    monkeypatch.setattr(office, "run_contained", unexpected)
    with pytest.raises(AppError) as caught:
        office.render_office(b"invalid xlsx", plan)
    assert caught.value.code == "UNSUPPORTED_XLSX_FEATURE"
