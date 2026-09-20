"""Build and probe an immutable local WSL runtime; never issue OCR qualification.

Run as root through unshare --mount --propagation private. No user documents or
Windows fonts are packaged. Existing images are never overwritten or selected.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import subprocess
import tempfile
from pathlib import Path

STORE = Path("/opt/llmocr-renderer-runtimes")
POLICY = "wsl-squashfs-runtime-v1"
CONFIG_ROOTS = ("/etc/fonts", "/etc/libreoffice")
ALIASES = {name: "usr/" + name for name in ("bin", "sbin", "lib", "lib64")}


def secure_store(path: Path) -> None:
    """Only use root-owned directories, without symlink or writable ancestors."""
    for ancestor in reversed((path, *path.parents)):
        if not ancestor.exists() and not ancestor.is_symlink():
            ancestor.mkdir(mode=0o700)
        info = ancestor.lstat()
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != 0
                or info.st_mode & 0o022):
            raise RuntimeError("Runtime store ownership or permissions are unsafe")


def file_sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def publish(image: Path, store: Path) -> tuple[Path, str]:
    digest = file_sha(image)
    target = store / (digest + ".squashfs")
    # link is exclusive: unlike rename, it cannot overwrite an existing image.
    os.link(image, target)
    image.unlink()
    target.chmod(0o444)
    return target, digest


def probe_command(root: Path, command: list[str]) -> list[str]:
    return [
        "/usr/bin/bwrap", "--unshare-all", "--die-with-parent", "--new-session",
        "--cap-drop", "ALL", "--uid", "65534", "--gid", "65534", "--clearenv",
        "--ro-bind", str(root), "/", "--proc", "/proc", "--dev", "/dev",
        "--tmpfs", "/tmp", "--dir", "/tmp/home", "--setenv", "HOME", "/tmp/home",
        "--setenv", "PATH", "/usr/bin:/bin", "--setenv", "LC_ALL", "C.UTF-8",
        "--setenv", "SAL_USE_VCLPLUGIN", "svp", "--chdir", "/tmp", *command,
    ]


def build() -> dict[str, object]:
    if os.name != "posix" or os.geteuid() != 0:
        raise RuntimeError("Run in the dedicated WSL distribution as root")
    if os.readlink("/proc/self/ns/mnt") == os.readlink("/proc/1/ns/mnt"):
        raise RuntimeError("A private mount namespace is required")
    # Reject shared propagation before mounting, even in a distinct namespace.
    for line in Path("/proc/self/mountinfo").read_text().splitlines():
        if any(part.startswith("shared:") for part in line.split()):
            raise RuntimeError("Run with unshare --mount --propagation private")
    secure_store(STORE)
    directory = Path(tempfile.mkdtemp(prefix="build-", dir=STORE))
    root, mounted = directory / "root", directory / "mounted"
    root.mkdir()
    mounted.mkdir()
    for name in ("usr", "etc", "proc", "dev", "tmp", "home", "run", "var"):
        (root / name).mkdir()
    for name, alias_target in ALIASES.items():
        if os.readlink("/" + name) != alias_target:
            raise RuntimeError("Unsupported runtime alias")
        (root / name).symlink_to(alias_target)
    for source in CONFIG_ROOTS:
        shutil.copytree(source, root / source.lstrip("/"), symlinks=True)
    image = directory / "runtime.squashfs"
    usr_mounted = image_mounted = False
    try:
        subprocess.run(["/usr/bin/mount", "--bind", "/usr", str(root / "usr")],
                       check=True, timeout=10)
        usr_mounted = True
        subprocess.run(["/usr/bin/mount", "-o", "remount,bind,ro", str(root / "usr")],
                       check=True, timeout=10)
        with (directory / "build.log").open("xb") as log:
            subprocess.run([
                "/usr/bin/mksquashfs", str(root), str(image), "-noappend", "-no-xattrs",
                "-comp", "gzip", "-Xcompression-level", "3", "-processors", "2",
                "-mem", "256M", "-no-progress", "-exit-on-error",
            ], stdout=log, stderr=subprocess.STDOUT, check=True, timeout=900)
        subprocess.run(["/usr/bin/umount", str(root / "usr")], check=True, timeout=10)
        usr_mounted = False
        subprocess.run(["/usr/bin/mount", "-t", "squashfs", "-o", "loop,ro,nodev,nosuid",
                        str(image), str(mounted)], check=True, timeout=10)
        image_mounted = True
        check_code = (
            "import errno,json,os,pathlib,socket;"
            "s=pathlib.Path('/proc/self/status').read_text();"
            "assert os.getuid()==65534;"
            "assert 'NoNewPrivs:\\t1' in s;"
            "assert 'CapEff:\\t0000000000000000' in s;"
            "assert os.statvfs('/usr').f_flag & os.ST_RDONLY;"
            "assert sorted(n for _,n in socket.if_nameindex())==['lo'];"
            "assert not pathlib.Path('/mnt/c').exists();"
            "print(json.dumps({'uid':os.getuid(),'runtimeReadOnly':True,"
            "'networkInterfaces':['lo'],'noNewPrivileges':True,'capabilities':0}))"
        )
        probe = subprocess.run(probe_command(mounted, ["/usr/bin/python3", "-I", "-c", check_code]),
                               capture_output=True, check=True, timeout=20)
        version = subprocess.run(probe_command(mounted, ["/usr/bin/libreoffice", "--version"]),
                                 capture_output=True, check=True, timeout=20)
        subprocess.run(["/usr/bin/umount", str(mounted)], check=True, timeout=10)
        image_mounted = False
        target, digest = publish(image, STORE)
        report = {
            "policy": POLICY, "compression": "gzip", "image": str(target), "sha256": digest,
            "bytes": target.stat().st_size, "includedRoots": ["/usr", *CONFIG_ROOTS],
            "aliases": ALIASES, "probe": json.loads(probe.stdout),
            "officeVersion": version.stdout.decode().strip(),
            "builderSha256": file_sha(Path(__file__)),
            "mksquashfsSha256": file_sha(Path("/usr/bin/mksquashfs")),
            "kernel": os.uname().release, "windowsFontsIncluded": False,
            "workerIncluded": False, "productionQualified": False,
            "buildDirectory": str(directory),
        }
        with target.with_suffix(".json").open("x", encoding="utf-8") as stream:
            json.dump(report, stream, sort_keys=True, indent=2)
        return report
    finally:
        # Only owned mountpoints are unmounted. On failure keep the build files;
        # never recursively remove a directory that might still contain a bind.
        if image_mounted:
            subprocess.run(["/usr/bin/umount", str(mounted)], check=True, timeout=10)
        if usr_mounted:
            subprocess.run(["/usr/bin/umount", str(root / "usr")], check=True, timeout=10)


if __name__ == "__main__":
    print(json.dumps(build(), sort_keys=True))
