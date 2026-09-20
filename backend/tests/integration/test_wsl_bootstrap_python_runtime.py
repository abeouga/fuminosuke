"""Read-only host inventory; failure injection changes Python callbacks, not files."""
import json
import subprocess
from pathlib import Path

from app.services.renderer_wsl import linux_path
from app.services.renderer_wsl_worker import validate_bootstrap_python

SCRIPT = r'''
import json, os, runpy, sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

collect = runpy.run_path(sys.argv[1])['bootstrap_python_identity']
original_open, original_read = os.open, os.read
original_link, original_stat, original_list = os.readlink, Path.lstat, os.listdir
opened = set()

def tracked_open(path, *args, **kwargs):
    opened.add(str(path))
    return original_open(path, *args, **kwargs)

with patch.object(os, 'open', tracked_open):
    before = collect()
assert '/etc/python3.10/sitecustomize.py' in opened
assert '/usr/lib/x86_64-linux-gnu/libpython3.10.so.1.0' in opened
assert collect() == before

def changed_read(fd, size):
    data = original_read(fd, size)
    if data and original_link('/proc/self/fd/' + str(fd)) == '/usr/lib/python3.10/os.py':
        return bytes([data[0] ^ 1]) + data[1:]
    return data

with patch.object(os, 'read', changed_read):
    assert collect()['sha256'] != before['sha256']
assert collect() == before

cases = 1
def rejects(message):
    global cases
    try:
        collect()
    except RuntimeError as error:
        assert message in str(error), str(error)
        cases += 1
    else:
        raise AssertionError('Unsafe inventory was accepted')

link = '/usr/lib/python3.10/sitecustomize.py'
for target, message in (
    ('/tmp/outside.py', 'escapes'), (link, 'Cyclic'),
    ('/usr/lib/python310.zip', 'link target'),
    ('/usr/lib/python3.10/collections', 'link target'),
):
    def changed_link(path, *args, **kwargs):
        return target if str(path) == link else original_link(path, *args, **kwargs)
    with patch.object(os, 'readlink', changed_link):
        rejects(message)

keys = ('st_dev', 'st_ino', 'st_mode', 'st_uid', 'st_gid',
        'st_size', 'st_mtime_ns', 'st_ctime_ns')
for owner in (False, True):
    def changed_stat(path, *args, **kwargs):
        info = original_stat(path, *args, **kwargs)
        if str(path) == '/usr/lib/python3.10/os.py':
            values = {key: getattr(info, key) for key in keys}
            if owner:
                values['st_uid'] = 1
            else:
                values['st_mode'] |= 0o002
            return SimpleNamespace(**values)
        return info
    with patch.object(Path, 'lstat', changed_stat):
        rejects('root-owned' if owner else 'writable')

lists = 0
def changed_list(path):
    global lists
    result = original_list(path)
    if str(path) == '/usr/lib/python3.10':
        lists += 1
        if lists == 2:
            return result + ['injected-inventory-change']
    return result
with patch.object(os, 'listdir', changed_list):
    rejects('inventory changed')
with patch.object(sys, 'path', ['/tmp', *sys.path]):
    rejects('search policy')
assert collect() == before
print(json.dumps({'identity': before, 'checkedConditions': cases,
                  'originalFilesUnchanged': True}))
'''


def test_real_python_inventory_checks_content_links_permissions_and_membership():
    worker = Path(__file__).parents[2] / "app/services/renderer_wsl_worker.py"
    result = subprocess.run([
        "wsl.exe", "-d", "Ubuntu-22.04", "-u", "root", "--exec",
        "/usr/bin/python3", "-I", "-S", "-B", "-c", SCRIPT, linux_path(worker),
    ], capture_output=True, text=True, timeout=45, creationflags=subprocess.CREATE_NO_WINDOW)
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    validate_bootstrap_python(report["identity"])
    assert report["checkedConditions"] == 9
    assert report["originalFilesUnchanged"] is True
