"""Read-only installed ELF evidence; faults affect callbacks, never host files."""
import json
import subprocess
from pathlib import Path

from app.services.renderer_wsl import linux_path
from app.services.renderer_wsl_worker import validate_native_bootstrap

SCRIPT = r'''
import json, os, runpy, subprocess, sys
from pathlib import Path
from unittest.mock import patch

module = runpy.run_path(sys.argv[1])
collect = module['native_bootstrap_identity']
original_run, original_read, original_link = subprocess.run, os.read, os.readlink
calls = []
def tracked_run(command, **kwargs):
    assert command[:2] == [module['NATIVE_LOADER'], '--list']
    assert command[2].startswith('/usr/') and '/tmp/' not in command[2]
    assert kwargs['env'] == {'PATH': '/usr/bin:/bin', 'LC_ALL': 'C'}
    calls.append(command[2])
    return original_run(command, **kwargs)
with patch('subprocess.run', tracked_run):
    before = collect()
assert len(calls) == before['rootCount']
assert '/usr/lib/x86_64-linux-gnu/libseccomp.so.2' in calls
assert '/usr/bin/systemd-run' in calls
assert collect() == before

def changed_read(fd, size):
    data = original_read(fd, size)
    if len(data) > 32 and original_link('/proc/self/fd/' + str(fd)).endswith('/libc.so.6'):
        return data[:32] + bytes([data[32] ^ 1]) + data[33:]
    return data
with patch.object(os, 'read', changed_read):
    assert collect()['sha256'] != before['sha256']

checked = 1
def rejects(message):
    global checked
    try:
        collect()
    except RuntimeError as error:
        assert message in str(error), str(error)
        checked += 1
    else:
        raise AssertionError('Unsafe bootstrap was accepted')
with patch.dict(os.environ, {'LD_PRELOAD': '/tmp/not-loaded.so'}):
    rejects('loader policy')
original_exists = os.path.lexists
with patch.object(os.path, 'lexists', lambda p: p == '/etc/ld.so.preload' or original_exists(p)):
    rejects('loader policy')

def missing_dependency(command, **kwargs):
    result = original_run(command, **kwargs)
    kwargs['stdout'].write(b'\nlibmissing.so => not found\n')
    return result
with patch('subprocess.run', missing_dependency):
    rejects('dependency')
assert collect() == before
print(json.dumps({'identity': before, 'checkedConditions': checked, 'filesUnchanged': True}))
'''


def test_installed_dependencies_are_bound_and_unsafe_loader_state_is_rejected():
    worker = Path(__file__).parents[2] / "app/services/renderer_wsl_worker.py"
    result = subprocess.run([
        "wsl.exe", "-d", "Ubuntu-22.04", "-u", "root", "--exec",
        "/usr/bin/python3", "-I", "-S", "-B", "-c", SCRIPT, linux_path(worker),
    ], capture_output=True, text=True, timeout=45, creationflags=subprocess.CREATE_NO_WINDOW)
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    validate_native_bootstrap(report["identity"])
    assert report["checkedConditions"] == 4
    assert report["filesUnchanged"] is True
