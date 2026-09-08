from __future__ import annotations

import importlib
import json
import socket
import sys

import pytest

from app.core.errors import AppError
from app.services.renderer_appcontainer import container, render_environment
from app.services.renderer_process import run_contained
from app.services.renderer_raster import rasterize
from app.services.renderer_temp import render_directory

LO_PYTHON = "C:/Program Files/LibreOffice/program/python.exe"


def test_actual_appcontainer_token_blocks_loopback():
    with socket.socket() as listener, render_directory() as directory:
        listener.bind(("127.0.0.1", 0))
        listener.listen(4)
        command = [
            LO_PYTHON,
            "-I",
            "-c",
            f"import socket;socket.create_connection({listener.getsockname()!r},1).close()",
        ]
        assert run_contained(command)["exitCode"] == 0
        listener.accept()[0].close()
        marker = directory / "probe.json"
        code = (
            "import socket,json,pathlib,ctypes as c\n"
            "a=c.WinDLL('advapi32');k=c.WinDLL('kernel32');"
            "k.GetCurrentProcess.restype=c.c_void_p;"
            "a.OpenProcessToken.argtypes=[c.c_void_p,c.c_uint,c.c_void_p];"
            "a.GetTokenInformation.argtypes=[c.c_void_p,c.c_int,c.c_void_p,c.c_uint,c.c_void_p];"
            "h=c.c_void_p();a.OpenProcessToken(k.GetCurrentProcess(),8,c.byref(h));"
            "v=c.c_uint();n=c.c_uint();"
            "a.GetTokenInformation(h,29,c.byref(v),4,c.byref(n));r={'appContainer':v.value}\n"
            f"try:\n socket.create_connection({listener.getsockname()!r},1);r['connected']=True\n"
            "except OSError:r['connected']=False\n"
            f"pathlib.Path({str(marker)!r}).write_text(json.dumps(r))"
        )
        with container(directory) as ac:
            result = run_contained([LO_PYTHON, "-I", "-c", code], appcontainer=ac)
            assert result["exitCode"] == 0
            assert ac["capabilityCount"] == 0
        assert json.loads(marker.read_text()) == {"appContainer": 1, "connected": False}
        listener.settimeout(0.2)
        with pytest.raises(TimeoutError):
            listener.accept()
    assert not directory.exists()


@pytest.mark.parametrize("kind", ["malformed", "many-pages", "huge-page"])
def test_pdf_helper_rejects_invalid_and_oversized_documents_without_backend_failure(kind):
    with render_directory() as directory:
        source = directory / "page.pdf"
        if kind == "malformed":
            source.write_bytes(b"not a PDF")
        else:
            writer = importlib.import_module("pypdf").PdfWriter()
            writer.add_blank_page(width=100000 if kind == "huge-page" else 595, height=842)
            if kind == "many-pages":
                writer.add_blank_page(width=595, height=842)
            writer.write(source)
        with pytest.raises(AppError) as caught:
            rasterize(directory, 150)
        assert caught.value.code == (
            "PAGE_COUNT_MISMATCH" if kind == "many-pages" else "RENDER_FAILED"
        )
        assert not (directory / "page.png").exists()
    assert not directory.exists()


def test_native_fail_fast_after_pdfium_load_cannot_kill_broker():
    command = [
        sys.executable,
        "-I",
        "-c",
        "import pypdfium2,ctypes as c; k=c.WinDLL('kernel32');"
        "k.SetErrorMode(3); k.RaiseFailFastException(None,None,0)",
    ]
    result = run_contained(command, timeout=10, memory_bytes=512 * 1024 * 1024)
    assert result["exitCode"] == 0xC0000602  # STATUS_FAIL_FAST_EXCEPTION
    assert result["activeProcesses"] == 0


def test_renderer_environment_does_not_forward_provider_credentials(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-not-a-key")
    monkeypatch.setenv("HTTPS_PROXY", "synthetic")
    env = render_environment(tmp_path)
    assert "OPENAI_API_KEY" not in env and "HTTPS_PROXY" not in env
    assert env["TEMP"] == str(tmp_path)
