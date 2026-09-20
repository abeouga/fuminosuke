"""Wire/identity and same-document tests; mocks are not native qualification."""
import copy
import hashlib
import json
import sys
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services import renderer_wsl as bridge
from app.services import renderer_wsl_worker as worker
from tests.bootstrap_python_fixture import bootstrap_python_evidence
from tests.core_guard_fixture import core_guard_evidence
from tests.wsl_execution_fixture import wsl_execution_evidence

REQUEST = {"sheetName": "計測シート", "rows": 2, "columns": 1}
RUNTIME = "a" * 64


@pytest.mark.parametrize("change", [
    {"rows": True}, {"rows": 0}, {"rows": 20001}, {"rows": 1.0},
    {"columns": 16385}, {"rows": 20000}, {"sheetName": ""},
    {"sheetName": "x\n"}, {"sheetName": "x" * 129}, {"path": "/other.xlsx"},
])
def test_request_rejects_invalid_values_before_launch(change, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Invalid request launched a renderer")
    monkeypatch.setattr(bridge.subprocess, "Popen", forbidden)
    with pytest.raises(ValueError, match="dimension request"):
        bridge.import_wsl_geometry(b"fixture", runtime_id=RUNTIME,
                                   dimension_request=REQUEST | change)


@pytest.mark.parametrize("format,runtime", [("pdf", RUNTIME), ("fods", None)])
def test_only_explicit_pinned_fods_accepts_observation(tmp_path, format, runtime):
    with pytest.raises(ValueError, match="dimension request"):
        worker.sandbox_command(tmp_path, "fixture", format, "linux",
                               tmp_path if runtime else None, runtime, REQUEST)


def test_request_is_one_json_argument_and_does_not_expand_mounts(tmp_path):
    args = (tmp_path, "fixture", "fods", "windows", tmp_path / "runtime", RUNTIME)
    original = worker.sandbox_command(*args)
    command = worker.sandbox_command(*args, dimensions=REQUEST)
    assert command[:-1] == original
    assert json.loads(command[-1]) == REQUEST


@pytest.mark.parametrize("change", [
    None, "missing", "policy", "sourceSha256", "fodsSha256", "request", "request_bool",
    "short", "negative", "float", "bool", "overflow", "visible", "record_type",
])
def test_bridge_checks_complete_dimension_observation(tmp_path, monkeypatch, change):
    workbook, fods = b"fixture XLSX", b"fixture FODS"
    def digest(value):
        return hashlib.sha256(value).hexdigest()
    observation = {"policy": worker.DIMENSION_POLICY, "request": copy.deepcopy(REQUEST),
                   "sourceSha256": digest(workbook), "fodsSha256": digest(fods),
                   "columns": [[688, 689, True]], "rows": [[409, 410, True], [409, 410, True]]}
    if change in {"policy", "sourceSha256", "fodsSha256"}:
        observation[change] = "changed"
    elif change == "request":
        observation["request"]["sheetName"] = "different"
    elif change == "request_bool":
        observation["request"]["columns"] = True
    elif change == "short":
        observation["rows"].pop()
    elif change in {"negative", "float", "bool", "overflow"}:
        observation["rows"][0][0] = {
            "negative": -1, "float": 409.0, "bool": True, "overflow": 2**31}[change]
    elif change == "visible":
        observation["rows"][0][2] = 1
    elif change == "record_type":
        observation["rows"][0] = "bad"
    report = {
        **wsl_execution_evidence(windows_fonts=False),
        "status": "PASS", "format": "fods", "fontSource": "linux",
        "policy": worker.POLICY, "fontRequestPolicy": worker.FONT_REQUEST_POLICY,
        "sourceSha256": digest(workbook), "fodsSha256": digest(fods),
        "coreDumpGuard": core_guard_evidence(),
        "brokerCoreDumpGuard": core_guard_evidence(),
        "bootstrapPythonIdentity": bootstrap_python_evidence(),
        "runtimeImage": {"policy": worker.RUNTIME_POLICY, "sha256": RUNTIME,
                         "filesystem": "squashfs", "mountsReadOnly": True},
        "workerIdentity": worker.worker_identity(Path(worker.__file__)),
    }
    if change != "missing":
        report["dimensionObservation"] = observation
    (tmp_path / "page.fods").write_bytes(fods)
    (tmp_path / "renderer-report.json").write_text(json.dumps(report), encoding="utf-8")
    monkeypatch.setattr(bridge, "render_directory", lambda: nullcontext(tmp_path))
    commands = []

    def start(command, **kwargs):
        commands.append(command)
        return SimpleNamespace(returncode=0, stdin=None, poll=lambda: 0, wait=lambda **kw: 0)

    monkeypatch.setattr(bridge.subprocess, "Popen", start)
    if change is None:
        assert bridge.import_wsl_geometry(
            workbook, runtime_id=RUNTIME, dimension_request=REQUEST) == (fods, report)
    else:
        with pytest.raises((ValueError, RuntimeError), match="dimension"):
            bridge.import_wsl_geometry(workbook, runtime_id=RUNTIME, dimension_request=REQUEST)
    assert json.loads(commands[0][-1]) == REQUEST


@pytest.mark.parametrize("change", [None, "export_changes_dimensions", "export_failure",
                                    "shutdown_timeout", "shutdown_failure"])
def test_uno_observation_and_export_use_one_readonly_document(monkeypatch, change):
    events = []
    sheet = object()

    def load(url, target, flags, props):
        values = {p.Name: p.Value for p in props}
        assert url == "file:///page.xlsx"
        assert values == {"Hidden": True, "ReadOnly": True,
                          "MacroExecutionMode": "never", "UpdateDocMode": "no-update"}
        events.append("load")
        return document

    def export(url, props):
        assert url == "file:///output/page.fods"
        assert {p.Name: p.Value for p in props} == {
            "FilterName": "OpenDocument Spreadsheet Flat XML", "Overwrite": False}
        events.append("export")
        if change == "export_failure":
            raise RuntimeError("export failed")

    def observe(actual_sheet, request):
        assert actual_sheet is sheet and request == REQUEST
        events.append("observe")
        return {"columns": [[688, 689, True]],
                "rows": [[410 if change == "export_changes_dimensions" and "export" in events
                          else 409, 410, True]] * 2}

    def get_sheet(name):
        assert name == REQUEST["sheetName"]
        return sheet

    document = SimpleNamespace(isReadonly=lambda: True, storeToURL=export,
                               Sheets=SimpleNamespace(getByName=get_sheet),
                               close=lambda value: events.append("close"))
    desktop = SimpleNamespace(loadComponentFromURL=load,
                              terminate=lambda: events.append("terminate"))
    remote = SimpleNamespace(ServiceManager=SimpleNamespace(
        createInstanceWithContext=lambda *args: desktop))
    resolver = SimpleNamespace(resolve=lambda value: remote)
    context = SimpleNamespace(ServiceManager=SimpleNamespace(
        createInstanceWithContext=lambda *args: resolver))
    monkeypatch.setitem(sys.modules, "uno", SimpleNamespace(
        getComponentContext=lambda: context, createUnoStruct=lambda name: SimpleNamespace()))
    monkeypatch.setitem(sys.modules, "com.sun.star.document.MacroExecMode",
                        SimpleNamespace(NEVER_EXECUTE="never"))
    monkeypatch.setitem(sys.modules, "com.sun.star.document.UpdateDocMode",
                        SimpleNamespace(NO_UPDATE="no-update"))
    monkeypatch.setattr(worker, "observe_dimensions", observe)
    def wait(**kwargs):
        events.append("wait")
        if change == "shutdown_timeout" and kwargs["timeout"] == 10:
            raise worker.subprocess.TimeoutExpired("fixture", 10)
        return 1 if change == "shutdown_failure" else 0

    monkeypatch.setattr(worker.subprocess, "Popen", lambda *args, **kwargs: SimpleNamespace(
        poll=lambda: None, wait=wait, kill=lambda: events.append("kill")))
    if change is None:
        result = worker.import_observed_fods(REQUEST)
        assert result["request"] == REQUEST
        assert result["rows"] == [[409, 410, True]] * 2
        assert events == ["load", "observe", "export", "observe", "close", "terminate", "wait"]
    else:
        with pytest.raises(RuntimeError):
            worker.import_observed_fods(REQUEST)
        assert events.count("load") == 1
        if change == "shutdown_timeout":
            assert events[-5:] == ["close", "terminate", "wait", "kill", "wait"]
        else:
            assert events[-3:] == ["close", "terminate", "wait"]


def test_raw_observation_retains_overflow_evidence_and_hidden_visibility():
    dimensions = [SimpleNamespace(Width=0, Height=0, IsVisible=True),
                  SimpleNamespace(Width=688, Height=688, IsVisible=False)]
    collection = SimpleNamespace(getByIndex=lambda i: dimensions[i])

    def cell(x, y, end_x, end_y):
        assert (x, y) == (end_x, end_y)
        return SimpleNamespace(Size=SimpleNamespace(Width=65537 if x == 0 else 1,
                                                    Height=65537 if y == 0 else 1))

    sheet = SimpleNamespace(Columns=collection, Rows=collection, getCellRangeByPosition=cell)
    result = worker.observe_dimensions(sheet, REQUEST | {"columns": 2})
    assert result == {axis: [[0, 65537, True], [688, 1, False]] for axis in ("columns", "rows")}
