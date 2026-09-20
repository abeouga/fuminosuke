"""Check pinned glyph dependencies inside an image; never qualify a PDF or OCR.

Run from backend with: unshare --mount --propagation private python3 -m
scripts.probe_wsl_glyph_runtime RUNTIME_SHA256. This accepts no document input.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

from app.services.renderer_wsl_worker import pinned_runtime
from scripts.build_wsl_renderer_runtime import probe_command

PREFIX = "/usr/local/lib/llmocr-glyph-v1"
VERSIONS = {"fonttools": "4.65.0", "pypdf": "6.10.0", "typing_extensions": "4.15.0"}
PROBE_CODE = """
import importlib.metadata
import json
import os
import pathlib
import socket
import sys
import fontTools
import pypdf
import typing_extensions
from fontTools.pens.recordingPen import DecomposingRecordingPen
from fontTools.ttLib import TTFont
from pypdf.generic import ContentStream

prefix = pathlib.Path('/usr/local/lib/llmocr-glyph-v1')
status = pathlib.Path('/proc/self/status').read_text()
assert sys.version_info[:2] == (3, 10)
assert pathlib.Path(sys.prefix) == prefix
assert os.getuid() == 65534 and os.getgid() == 65534
assert 'NoNewPrivs:\\t1' in status
assert 'CapEff:\\t0000000000000000' in status
assert os.statvfs(prefix).f_flag & os.ST_RDONLY
assert sorted(n for _, n in socket.if_nameindex()) == ['lo']
assert not pathlib.Path('/mnt/c').exists()
assert not pathlib.Path('/reference-fonts').exists()
modules = {'fonttools': fontTools, 'pypdf': pypdf, 'typing_extensions': typing_extensions}
paths = {name: str(pathlib.Path(module.__file__).resolve()) for name, module in modules.items()}
assert all(pathlib.Path(path).is_relative_to(prefix) for path in paths.values())
print(json.dumps({
    'versions': {name: importlib.metadata.version(name) for name in modules},
    'paths': paths, 'python': sys.version, 'uid': os.getuid(),
    'runtimeReadOnly': True, 'networkInterfaces': ['lo'],
    'noNewPrivileges': True, 'capabilities': 0,
}))
"""


def glyph_probe_command(root: Path) -> list[str]:
    return probe_command(root, [PREFIX + "/bin/python", "-I", "-B", "-c", PROBE_CODE])


def probe(runtime_id: str) -> dict[str, Any]:
    with pinned_runtime(runtime_id) as root:
        if root is None:
            raise RuntimeError("A pinned runtime image is required")
        result = subprocess.run(
            glyph_probe_command(root), capture_output=True, check=True, timeout=20,
        )
        if len(result.stdout) > 64 * 1024:
            raise RuntimeError("Unexpected dependency probe output")
        evidence = json.loads(result.stdout)
        if not isinstance(evidence, dict) or evidence.get("versions") != VERSIONS:
            raise RuntimeError("Pinned glyph dependencies do not match")
    return {
        "policy": "wsl-glyph-dependency-probe-v1", "runtimeId": runtime_id,
        "probeSha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "evidence": evidence, "productionQualified": False,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("runtime_id")
    print(json.dumps(probe(parser.parse_args().runtime_id), sort_keys=True))
