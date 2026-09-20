"""No-input WSL processes; only our explicitly owned child may abort."""
import json
import subprocess
from pathlib import Path

from app.services.renderer_wsl import linux_path
from app.services.renderer_wsl_worker import validate_core_guard

SCRIPT = r'''
import ctypes, errno, json, os, resource, runpy, signal, subprocess, sys, threading, time
from pathlib import Path

namespace = runpy.run_path(sys.argv[1])
before = Path('/proc/sys/kernel/core_pattern').read_bytes()
ready = threading.Event()
result = []

def verify():
    assert resource.getrlimit(resource.RLIMIT_CORE) == (1, 1)
    libc = ctypes.CDLL(None, use_errno=True)
    libc.syscall.restype = ctypes.c_long
    changed = (ctypes.c_ulong * 2)(0, 1)
    ctypes.set_errno(0)
    result = libc.syscall(ctypes.c_long(160), ctypes.c_ulong(resource.RLIMIT_CORE),
                          ctypes.byref(changed))
    assert result == -1 and ctypes.get_errno() == errno.EPERM
    assert resource.getrlimit(resource.RLIMIT_CORE) == (1, 1)
    other = resource.getrlimit(resource.RLIMIT_NOFILE)
    resource.setrlimit(resource.RLIMIT_NOFILE, other)
    status = Path('/proc/thread-self/status').read_text()
    assert 'NoNewPrivs:\t1' in status and 'Seccomp:\t2' in status

def old_thread():
    try:
        assert ready.wait(10)
        verify()
        result.append('PASS')
    except BaseException as error:
        result.append(type(error).__name__ + ': ' + str(error))

thread = threading.Thread(target=old_thread)
thread.start()
try:
    evidence = namespace['install_core_guard']()
finally:
    ready.set()
thread.join(timeout=10)
assert not thread.is_alive() and result == ['PASS'], result
verify()
child_code = r"""
import ctypes, errno, resource
assert resource.getrlimit(resource.RLIMIT_CORE) == (1, 1)
libc = ctypes.CDLL(None, use_errno=True)
libc.syscall.restype = ctypes.c_long
changed = (ctypes.c_ulong * 2)(0, 1)
result = libc.syscall(ctypes.c_long(302), ctypes.c_ulong(0),
                      ctypes.c_ulong(resource.RLIMIT_CORE), ctypes.byref(changed),
                      ctypes.c_void_p(0))
assert result == -1 and ctypes.get_errno() == errno.EPERM
print(resource.getrlimit(resource.RLIMIT_CORE))
"""
child = subprocess.run([sys.executable, '-I', '-S', '-B', '-c', child_code],
                       check=True, capture_output=True, timeout=10, text=True)
assert child.stdout.strip() == '(1, 1)'
pid = os.fork()
if pid == 0:
    try:
        verify()  # Do not crash if the actual guard is absent or ineffective.
        os.abort()
    finally:
        os._exit(99)
deadline = time.monotonic() + 10
while True:
    finished, status = os.waitpid(pid, os.WNOHANG)
    if finished:
        break
    if time.monotonic() > deadline:
        os.kill(pid, signal.SIGKILL)
        os.waitpid(pid, 0)
        raise AssertionError('Owned fault child timed out')
    time.sleep(.05)
assert os.WIFSIGNALED(status) and os.WTERMSIG(status) == signal.SIGABRT
assert not os.WCOREDUMP(status)
assert Path('/proc/sys/kernel/core_pattern').read_bytes() == before
print(json.dumps({'guard': evidence, 'preexistingThread': result[0],
                  'execChild': 'PASS', 'faultChildWaitStatus': status,
                  'kernelCoreDumpFlag': False, 'hostPolicyUnchanged': True}))
'''


def test_real_product_guard_survives_threads_exec_and_owned_fault():
    worker = Path(__file__).parents[2] / "app/services/renderer_wsl_worker.py"
    completed = subprocess.run([
        "wsl.exe", "-d", "Ubuntu-22.04", "-u", "nobody", "--exec",
        "/usr/bin/python3", "-I", "-S", "-B", "-c", SCRIPT, linux_path(worker),
    ], capture_output=True, text=True, timeout=35, creationflags=subprocess.CREATE_NO_WINDOW)
    assert completed.returncode == 0, completed.stderr
    report = json.loads(completed.stdout)
    validate_core_guard(report["guard"])
    assert report["preexistingThread"] == report["execChild"] == "PASS"
    assert report["faultChildWaitStatus"] == 6
    assert report["kernelCoreDumpFlag"] is False
    assert report["hostPolicyUnchanged"] is True
