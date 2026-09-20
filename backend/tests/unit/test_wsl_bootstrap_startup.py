"""Root bootstrap must not load host site hooks; isolated inner dependencies remain usable."""

from contextlib import nullcontext

import pytest

from app.services import renderer_glyph_bridge as glyph
from app.services import renderer_glyph_worker as glyph_worker
from app.services import renderer_vector_bridge as vector
from app.services import renderer_wsl as office
from app.services import renderer_wsl_worker as office_worker
from tests.unit.test_renderer_glyph_bridge import reference

reference = reference


class LaunchIntercepted(Exception):
    pass


@pytest.mark.parametrize("kind", ["pdf", "fods", "reference", "glyph", "vector"])
def test_pinned_root_bootstrap_disables_site_and_bytecode(
    tmp_path, monkeypatch, reference, kind,
):
    bridge = glyph if kind == "glyph" else vector if kind == "vector" else office
    monkeypatch.setattr(bridge, "render_directory", lambda: nullcontext(tmp_path))
    commands = []

    def intercept(command, **kwargs):
        commands.append(command)
        raise LaunchIntercepted

    monkeypatch.setattr(bridge.subprocess, "Popen", intercept)
    pdf, renderer = reference
    with pytest.raises(LaunchIntercepted):
        if kind == "glyph":
            glyph.validate_wsl_pdf_glyphs(pdf, renderer)
        elif kind == "vector":
            vector.inspect_wsl_vectors(pdf, renderer)
        else:
            office._render(b"unparsed workbook fixture", distribution="Ubuntu-22.04",
                           format=kind, font_source="windows", runtime_id="a" * 64)
    assert len(commands) == 1
    command = commands[0]
    index = command.index("/usr/bin/python3")
    assert command[index + 1:index + 4] == ["-I", "-S", "-B"]
    assert command[command.index("--user") + 1] == "root"
    assert command[index - 4:index] == [
        "/usr/bin/unshare", "--mount", "--propagation", "private",
    ]
    assert command[index + 4].endswith(".pyz" if kind in {"glyph", "vector"} else ".py")


@pytest.mark.parametrize("kind", ["pdf", "fods", "reference"])
def test_unselected_runtime_keeps_legacy_bootstrap(tmp_path, monkeypatch, kind):
    monkeypatch.setattr(office, "render_directory", lambda: nullcontext(tmp_path))
    commands = []

    def intercept(command, **kwargs):
        commands.append(command)
        raise LaunchIntercepted

    monkeypatch.setattr(office.subprocess, "Popen", intercept)
    with pytest.raises(LaunchIntercepted):
        office._render(b"unparsed workbook fixture", distribution="Ubuntu-22.04",
                       format=kind, font_source="linux")
    command = commands[0]
    index = command.index("/usr/bin/python3")
    assert command[index + 1] == "-I"
    assert command[index + 2].endswith("/renderer_wsl_worker.py")
    assert "-S" not in command and "-B" not in command and "/usr/bin/unshare" not in command


def test_inner_interpreters_keep_their_pinned_package_search(tmp_path):
    office_command = office_worker.sandbox_command(
        tmp_path, "fixture", "reference", "windows", tmp_path / "runtime", "a" * 64,
    )
    glyph_command = glyph_worker.glyph_sandbox_command(
        tmp_path, "fixture", tmp_path / "runtime", "a" * 64,
    )
    for command, executable in ((office_command, "/usr/bin/python3"),
                                (glyph_command, glyph_worker.PYTHON)):
        index = command.index(executable)
        assert command[index + 1] == "-I"
        assert "-S" not in command[index + 1:]
        assert "--unshare-all" in command and "--clearenv" in command
        assert command[command.index("--uid") + 1] == "65534"
