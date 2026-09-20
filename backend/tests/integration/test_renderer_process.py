from __future__ import annotations

import importlib
import json
import socket
import subprocess
import sys
import time
from threading import Event

import pytest

from app.core.errors import AppError
from app.services.renderer_process import renderer_mutex, run_contained
from app.services.renderer_temp import OWNER, clean_stale, render_directory

PYTHON = sys._base_executable


def wait_file(path):
    deadline = time.monotonic() + 10
    while not path.exists() and time.monotonic() < deadline:
        time.sleep(.025)
    assert path.exists()


def test_job_normal_and_memory_denial(tmp_path):
    marker = tmp_path / "memory"
    code = ("from pathlib import Path\ntry:\n b=bytearray(128*1024*1024)\n"
            f"except MemoryError:\n Path({str(marker)!r}).write_text('denied')\n")
    result = run_contained([PYTHON, "-c", code], memory_bytes=64 * 1024 * 1024)
    assert result["exitCode"] == 0
    assert result["activeProcesses"] == 0
    assert marker.read_text() == "denied"


def test_timeout_tree_and_unrelated_process(tmp_path):
    psutil = importlib.import_module("psutil")
    marker = tmp_path / "child"
    child = f"import os,time;open({str(marker)!r},'w').write(str(os.getpid()));time.sleep(60)"
    code = f"import subprocess,time;subprocess.Popen({[PYTHON, '-c', child]!r});time.sleep(60)"
    sentinel = subprocess.Popen([PYTHON, "-c", "import time;time.sleep(60)"],
                                creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        with pytest.raises(AppError) as caught:
            run_contained([PYTHON, "-c", code], timeout=2)
        assert caught.value.code == "RENDER_TIMEOUT"
        assert marker.exists()
        assert not psutil.pid_exists(int(marker.read_text()))
        assert sentinel.poll() is None
    finally:
        sentinel.terminate()
        sentinel.wait(timeout=5)


def test_cancel_and_process_count_limit(tmp_path):
    cancel = Event()
    cancel.set()
    with pytest.raises(AppError) as caught:
        run_contained([PYTHON, "-c", "import time;time.sleep(60)"], cancel=cancel)
    assert caught.value.code == "RENDER_CANCELLED"
    marker = tmp_path / "spawn"
    code = (f"import subprocess\nfrom pathlib import Path\ntry:\n"
            f" subprocess.Popen({[PYTHON, '-c', 'pass']!r})\n"
            f"except OSError:\n Path({str(marker)!r}).write_text('denied')")
    result = run_contained([PYTHON, "-c", code], process_limit=1)
    assert result["exitCode"] == 0
    assert marker.read_text() == "denied"


def test_parent_death_closes_job(tmp_path):
    psutil = importlib.import_module("psutil")
    marker = tmp_path / "orphan"
    child = f"import os,time;open({str(marker)!r},'w').write(str(os.getpid()));time.sleep(60)"
    code = ("from app.services.renderer_process import run_contained;"
            f"run_contained({[PYTHON, '-c', child]!r})")
    parent = subprocess.Popen([sys.executable, "-c", code],
                              creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        wait_file(marker)
        # The venv launcher can own a child interpreter. Kill the interpreter
        # that actually owns the Job handle, not unrelated process names.
        descendants = psutil.Process(parent.pid).children(recursive=True)
        owners = [p for p in descendants if p.pid != int(marker.read_text())]
        for p in reversed(owners):
            p.kill()
        parent.terminate()
        parent.wait(timeout=5)
        deadline = time.monotonic() + 5
        while psutil.pid_exists(int(marker.read_text())) and time.monotonic() < deadline:
            time.sleep(.025)
        assert not psutil.pid_exists(int(marker.read_text()))
    finally:
        if parent.poll() is None:
            parent.terminate()
            parent.wait(timeout=5)


def test_cross_process_mutex_and_local_network_not_isolated(tmp_path):
    marker = tmp_path / "lock"
    code = ("from app.services.renderer_process import renderer_mutex\n"
            "from app.core.errors import AppError\nfrom pathlib import Path\ntry:\n"
            " with renderer_mutex(): pass\nexcept AppError as e:\n"
            f" Path({str(marker)!r}).write_text(e.code)")
    with renderer_mutex():
        result = run_contained([sys.executable, "-c", code])
    assert result["exitCode"] == 0
    assert marker.read_text() == "RENDERER_BUSY"
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        address = listener.getsockname()
        result = run_contained([PYTHON, "-c",
                                f"import socket;socket.create_connection({address!r},2).close()"])
    # Deliberate negative qualification: Job Objects DO NOT block networking.
    assert result["exitCode"] == 0


def test_private_temp_error_cleanup_and_stale_ownership(tmp_path, monkeypatch):
    from app.services import renderer_temp

    # Exercise the real private-root ACL and lease code, but do not enumerate
    # unrelated renderer leftovers from another run in the user's system temp.
    monkeypatch.setattr(renderer_temp.tempfile, "gettempdir", lambda: str(tmp_path))
    with renderer_mutex():
        with pytest.raises(RuntimeError), render_directory() as path:
            (path / "source.xlsx").write_bytes(b"synthetic")
            raise RuntimeError("render failure")
        assert not path.exists()
        with render_directory() as active:
            assert active.parent == tmp_path / OWNER
            assert clean_stale(active.parent, age=0) == []
            stale = active.parent / ("render-" + "a" * 32)
            stale.mkdir()
            (stale / "owner.json").write_text(json.dumps({
                "owner": OWNER, "nonce": "a" * 32, "created": 0}))
            (stale / "lease").write_bytes(b"0")
            assert clean_stale(active.parent, age=0) == [stale.name]
            assert active.exists()
