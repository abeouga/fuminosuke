from __future__ import annotations

import copy
from io import BytesIO
from xml.etree import ElementTree as ET
from zipfile import ZipFile

import pytest

from app.core.errors import AppError
from app.domain.print_layout import page_plan
from app.services.office_reference_renderer import inspect_package, page_copy
from app.services.print_layout_renderer import render_page
from app.services.print_layout_source import read_source
from app.services.reference_geometry import measure_page
from tests.unit.test_print_layout import request, workbook_bytes


def case(rows=12, breaks=None):
    content = workbook_bytes(rows=rows, manual=5)
    source = read_source(content, "帳票", {"cells": [{"id": "xlsx_C2", "role": "input"}]})
    return content, page_plan(source, request(break_after_rows=breaks or []))


def mutate(content, part, callback):
    out = BytesIO()
    with ZipFile(BytesIO(content)) as archive, ZipFile(out, "w") as result:
        for entry in archive.infolist():
            data = archive.read(entry)
            result.writestr(entry, callback(data) if entry.filename == part else data)
    return out.getvalue()


def test_temporary_copy_changes_only_print_xml_and_preserves_saved_ranges():
    content, plan = case(rows=30, breaks=[15])
    with ZipFile(BytesIO(content)) as original:
        for page in plan["pages"]:
            rendered = page_copy(content, plan, page)
            with ZipFile(BytesIO(rendered)) as changed:
                assert changed.namelist() == original.namelist()
                for name in original.namelist():
                    if name not in {"xl/workbook.xml", "xl/worksheets/sheet1.xml"}:
                        assert original.read(name) == changed.read(name)
                before = ET.fromstring(original.read("xl/worksheets/sheet1.xml"))
                after = ET.fromstring(changed.read("xl/worksheets/sheet1.xml"))
                ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
                for tag in ("sheetData", "sheetFormatPr", "sheetViews"):
                    assert ET.tostring(before.find(ns + tag)) == ET.tostring(after.find(ns + tag))
                assert after.find(ns + "rowBreaks") is None
                assert f"$B${page['startRow']}:$D${page['endRow']}".encode() in changed.read(
                    "xl/workbook.xml")


@pytest.mark.parametrize("fragment", [b'<f>1+1</f>', b'<drawing/>', b'<controls/>',
                                      b'<row hidden="1"/>', b'<col hidden="true"/>'])
def test_unsupported_content_is_rejected_before_office(fragment):
    content, _ = case()
    changed = mutate(content, "xl/worksheets/sheet1.xml",
                     lambda b: b.replace(b"</worksheet>", fragment + b"</worksheet>"))
    with pytest.raises(AppError) as caught:
        inspect_package(changed)
    assert caught.value.code == "UNSUPPORTED_XLSX_FEATURE"


def test_external_relationship_and_bad_zip_are_rejected():
    content, _ = case()
    changed = mutate(content, "xl/_rels/workbook.xml.rels", lambda b: b.replace(
        b"</Relationships>", b'<Relationship Id="bad" TargetMode="External" '
        b'Target="https://example.invalid/"/></Relationships>'))
    for candidate in (changed, b"not zip"):
        with pytest.raises(AppError):
            inspect_package(candidate)


def test_utf16_entities_are_rejected_without_parsing():
    content, _ = case()
    changed = mutate(content, "xl/worksheets/sheet1.xml", lambda b: (
        '<?xml version="1.0" encoding="UTF-16"?>'
        '<!DOCTYPE worksheet [<!ENTITY internal "hidden">]><worksheet>&internal;</worksheet>'
    ).encode("utf-16"))
    with pytest.raises(AppError, match="UTF-8"):
        inspect_package(changed)


def test_source_and_fractional_scale_are_not_silently_rewritten():
    content, plan = case()
    plan["sourceSha256"] = "0" * 64
    with pytest.raises(AppError) as caught:
        page_copy(content, plan, plan["pages"][0])
    assert caught.value.code == "SOURCE_CHANGED"
    content, plan = case()
    plan["commonScale"] = 0.987
    with pytest.raises(AppError, match="fractional"):
        page_copy(content, plan, plan["pages"][0])


def test_measurement_rejects_field_shift_missing_edges_and_page_size():
    _, plan = case()
    page = plan["pages"][0]
    # Synthetic raster verifies measurement logic only, never Office qualification.
    image = render_page(page)
    baseline = measure_page(page, image)
    assert baseline["status"] == "PASS"
    assert baseline["thresholdQualified"] is False
    shifted = copy.deepcopy(page)
    shifted["fields"][0]["rectangle"]["x"] += 20
    result = measure_page(shifted, image)
    assert result["status"] == "FAIL"
    assert "MAX_ERROR_EXCEEDED" in result["codes"]
    unruled = copy.deepcopy(page)
    for cell in unruled["cells"]:
        cell["borders"] = {}
    assert measure_page(unruled, image)["status"] == "FAIL"
    wrong = copy.deepcopy(page)
    wrong["height"] += 1
    assert measure_page(wrong, image)["code"] == "PAGE_GEOMETRY_MISMATCH"


def test_renderer_concurrency_limit_is_fail_fast(monkeypatch):
    from app.services import office_reference_renderer as renderer
    content, plan = case()
    renderer._RENDER_LOCK.acquire()
    try:
        with pytest.raises(AppError) as caught:
            renderer.render_office(content, plan)
        assert caught.value.code == "RENDERER_BUSY"
    finally:
        renderer._RENDER_LOCK.release()


def test_missing_renderer_has_no_pillow_fallback(monkeypatch):
    from app.services import office_reference_renderer as renderer
    monkeypatch.setattr(renderer.shutil, "which", lambda name: None)
    monkeypatch.setattr(renderer.Path, "is_file", lambda self: False)
    with pytest.raises(AppError) as caught:
        renderer.renderer_identity()
    assert caught.value.code == "RENDERER_UNAVAILABLE"


def test_timeout_cleans_temporary_files_and_releases_renderer(monkeypatch):
    import importlib
    import sys
    from pathlib import Path

    from app.services import office_reference_renderer as renderer
    from app.services.renderer_process import run_contained
    content, plan = case()
    directories = []
    pids = []

    def timeout_process(command, **kwargs):
        directory = Path(command[-1]).parent
        directories.append(directory)
        marker = directory / "pid"
        code = (f"import os,time;open({str(marker)!r},'w').write(str(os.getpid()));"
                "time.sleep(60)")
        try:
            return run_contained([sys._base_executable, "-c", code], timeout=.5)
        finally:
            pids.append(int(marker.read_text()))

    monkeypatch.setattr(renderer, "renderer_identity", lambda: {"executable": "fixture"})
    monkeypatch.setattr(renderer, "run_contained", timeout_process)
    with pytest.raises(AppError) as caught:
        renderer.render_office(content, plan)
    assert caught.value.code == "RENDER_FAILED"
    assert pids and all(not importlib.import_module("psutil").pid_exists(pid) for pid in pids)
    assert all(not path.exists() for path in directories)
    assert not renderer._RENDER_LOCK.locked()
