import hashlib
import stat
from types import SimpleNamespace

import pytest

from scripts.build_wsl_renderer_runtime import probe_command, publish, secure_store


@pytest.mark.parametrize("mode,uid", [
    (stat.S_IFDIR | 0o775, 0),
    (stat.S_IFDIR | 0o777, 0),
    (stat.S_IFDIR | 0o700, 1000),
    (stat.S_IFLNK | 0o700, 0),
    (stat.S_IFREG | 0o600, 0),
])
def test_store_rejects_unsafe_ancestor_before_creating_destination(mode, uid):
    unsafe = SimpleNamespace(
        exists=lambda: True, lstat=lambda: SimpleNamespace(st_mode=mode, st_uid=uid),
    )
    destination = SimpleNamespace(parents=(unsafe,))
    with pytest.raises(RuntimeError, match="ownership or permissions"):
        secure_store(destination)


def test_store_rejects_dangling_symlink_without_creating_through_it():
    destination = SimpleNamespace(
        parents=(), exists=lambda: False, is_symlink=lambda: True,
        lstat=lambda: SimpleNamespace(st_mode=stat.S_IFLNK | 0o700, st_uid=0),
    )
    with pytest.raises(RuntimeError, match="ownership or permissions"):
        secure_store(destination)


def test_image_published_by_content_without_overwriting_existing_image(tmp_path):
    image = tmp_path / "new-image"
    image.write_bytes(b"runtime image fixture")
    store = tmp_path / "store"
    store.mkdir()
    target, digest = publish(image, store)
    assert digest == hashlib.sha256(b"runtime image fixture").hexdigest()
    assert target.name == digest + ".squashfs"
    assert target.read_bytes() == b"runtime image fixture"
    assert not image.exists()
    image.write_bytes(b"runtime image fixture")
    with pytest.raises(FileExistsError):
        publish(image, store)
    assert target.read_bytes() == b"runtime image fixture"
    assert image.read_bytes() == b"runtime image fixture"


def test_probe_uses_image_root_without_live_runtime_or_host_document_bindings(tmp_path):
    command = probe_command(tmp_path, ["/usr/bin/libreoffice", "--version"])
    assert command.count("--ro-bind") == 1
    index = command.index("--ro-bind")
    assert command[index + 1:index + 3] == [str(tmp_path), "/"]
    assert "--unshare-all" in command and "--clearenv" in command
    assert command[command.index("--uid") + 1] == "65534"
    assert command[command.index("--cap-drop") + 1] == "ALL"
    assert command[-2:] == ["/usr/bin/libreoffice", "--version"]
    assert "/mnt/c" not in command and "/reference-fonts" not in command
