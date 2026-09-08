"""Safe native IPC contrast using installed LibreOffice OSL; no network calls."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.renderer_appcontainer import container
from app.services.renderer_process import run_contained
from app.services.renderer_temp import render_directory

PROBE = r"""
import ctypes as c,json,pathlib
k=c.WinDLL('kernel32',use_last_error=True)
k.CreateNamedPipeW.argtypes=[c.c_wchar_p,c.c_uint,c.c_uint,c.c_uint,c.c_uint,c.c_uint,c.c_uint,c.c_void_p]
k.CreateNamedPipeW.restype=c.c_void_p
k.CloseHandle.argtypes=[c.c_void_p]
pipes=[]
for name in [r'\\.\pipe\utsushibi-closure-test',r'\\.\pipe\LOCAL\utsushibi-closure-test']:
 h=k.CreateNamedPipeW(name,3,0,1,4096,4096,100,None)
 ok=h!=c.c_void_p(-1).value
 pipes.append(dict(name=name,success=ok,error=c.get_last_error()))
 if ok:k.CloseHandle(h)
s=c.WinDLL('C:/Program Files/LibreOffice/program/sal3.dll',use_last_error=True)
s.osl_getCurrentSecurity.restype=c.c_void_p
s.osl_freeSecurityHandle.argtypes=[c.c_void_p]
s.rtl_uString_newFromAscii.argtypes=[c.c_void_p,c.c_char_p]
s.rtl_uString_release.argtypes=[c.c_void_p]
s.osl_createPipe.argtypes=[c.c_void_p,c.c_int,c.c_void_p]
s.osl_createPipe.restype=c.c_void_p
s.osl_releasePipe.argtypes=[c.c_void_p]
n=c.c_void_p()
s.rtl_uString_newFromAscii(c.byref(n),b'ClosureProbe')
security=s.osl_getCurrentSecurity()
p=s.osl_createPipe(n,1,security)
osl=dict(pipeCreated=bool(p),winerror=c.get_last_error())
if p:s.osl_releasePipe(p)
s.rtl_uString_release(n)
s.osl_freeSecurityHandle(security)
pathlib.Path('ipc.json').write_text(json.dumps(dict(pipes=pipes,osl=osl)))
"""


def main():
    with render_directory() as directory, container(directory) as ac:
        result = run_contained(
            ["C:/Program Files/LibreOffice/program/python.exe", "-I", "-c", PROBE], appcontainer=ac
        )
        evidence = json.loads((directory / "ipc.json").read_text())
        assert result["exitCode"] == 0
        assert evidence["pipes"][0]["error"] == 5
        assert evidence["pipes"][1]["success"]
        assert evidence["osl"] == {"pipeCreated": False, "winerror": 5}
    path = Path(__file__).resolve().parents[2] / "docs/reference-renderer-evidence/closure/ipc.json"
    path.write_text(json.dumps({**result, **evidence}, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
