"""Host-only renderer launcher. Never mount this process or Docker socket into the API.

Only fixed modes and bounded workbook bytes cross the socket. Filesystem paths,
image names, container options and commands are operator configuration only.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import re
import select
import shutil
import signal
import socket
import stat
import subprocess
import tarfile
import tempfile
import time
from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

from app.rendering.contract import (
    MAX_FILE,
    POLICY,
    SHA,
    digest,
    encode,
    validate_evidence,
    validate_request,
)

CONTROLS = {
    "network": "none",
    "user": "10001:10001",
    "readOnlyRoot": True,
    "capDrop": ["ALL"],
    "noNewPrivileges": True,
    "pidsLimit": 64,
    "memoryBytes": 2147483648,
    "memorySwapBytes": 2147483648,
    "nanoCpus": 1000000000,
    "wallSeconds": 80,
    "coreLimit": 1,
    "outputBytes": MAX_FILE,
    "inputReadOnly": True,
}
OWNER_LABEL = "com.fuminosuke.renderer"


def remove_container(name: str) -> None:
    existing = docker("ps", "-a", "--filter", "name=^/" + name + "$", "--format", "{{.Names}}")
    if not existing.strip():
        return
    item = json.loads(docker("inspect", name))[0]
    if (
        re.fullmatch(r"llmocr-render-[0-9a-f]{32}", name) is None
        or item["Config"]["Labels"].get(OWNER_LABEL) != POLICY
    ):
        raise ValueError("Renderer container ownership changed")
    docker("rm", "--force", name)


def remove_volume(name: str) -> None:
    volumes = (
        docker(
            "volume",
            "ls",
            "--filter",
            "label=" + OWNER_LABEL + "=" + POLICY,
            "--format",
            "{{.Name}}",
        )
        .decode()
        .splitlines()
    )
    if name in volumes:
        docker("volume", "rm", name)


@contextlib.contextmanager
def request_directory(spool: Path) -> Iterator[Path]:
    # Retain the recovery marker if Docker becomes unavailable during cleanup.
    yield Path(tempfile.mkdtemp(prefix="render-", dir=spool))


def recover_owned(spool: Path) -> None:
    """Recover only supervisor-owned requests after an interrupted host service."""
    docker("version", "--format", "{{.Server.Version}}")
    for directory in spool.glob("render-*"):
        marker = directory / "owner.json"
        if (
            directory.is_symlink()
            or directory.resolve().parent != spool.resolve()
            or directory.stat().st_uid != 0
            or marker.is_symlink()
            or not marker.is_file()
            or marker.stat().st_uid != 0
            or marker.stat().st_size > 1024
        ):
            raise ValueError("Unknown renderer spool ownership")
        owner = json.loads(marker.read_bytes())
        name = owner.get("container", "")
        if (
            owner.get("policy") != POLICY
            or re.fullmatch(r"llmocr-render-[0-9a-f]{32}", name) is None
            or owner.get("volume") != name + "-output"
        ):
            raise ValueError("Unknown renderer recovery marker")
        remove_container(name)
        remove_volume(owner["volume"])
        # These directories are root-owned; native code only sees read-only mounts.
        if any(p.is_symlink() for p in directory.rglob("*")):
            raise ValueError("Renderer recovery found an unexpected link")
        shutil.rmtree(directory)


def docker(*args: str, timeout: int = 15) -> bytes:
    with tempfile.TemporaryFile() as stdout:
        result = subprocess.run(
            ["docker", *args],
            stdout=stdout,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            timeout=timeout,
            check=False,
        )
        stdout.seek(0)
        data = stdout.read(MAX_FILE + 1024 * 1024)
    if result.returncode or len(data) >= MAX_FILE + 1024 * 1024:
        raise RuntimeError("Renderer container operation failed")
    return data


def inspect_admission(container: str, image: str, worker_sha: str) -> dict[str, object]:
    # Persist the host dump handler identity; limit=1 blocks Linux pipe handlers too.
    core_pattern = Path("/proc/sys/kernel/core_pattern").read_bytes()
    item = json.loads(docker("inspect", container))[0]
    host, config = item["HostConfig"], item["Config"]
    expected = {
        "NetworkMode": "none",
        "ReadonlyRootfs": True,
        "CapDrop": ["ALL"],
        "PidsLimit": 64,
        "Memory": 2147483648,
        "MemorySwap": 2147483648,
        "NanoCpus": 1000000000,
        "Privileged": False,
    }
    mounts = item["Mounts"]
    if (
        item["Image"] != image
        or config["User"] != "10001:10001"
        or any(host[k] != v for k, v in expected.items())
        or host["SecurityOpt"] != ["no-new-privileges"]
        or config["Entrypoint"] != ["/usr/bin/python3", "-I", "-B", "/opt/renderer/worker.py"]
        or {m["Destination"] for m in mounts} != {"/input", "/output", "/admission"}
        or any(m["RW"] for m in mounts if m["Destination"] in {"/input", "/admission"})
        or {u["Name"]: (u["Soft"], u["Hard"]) for u in host["Ulimits"]}
        != {"core": (1, 1), "fsize": (MAX_FILE, MAX_FILE)}
    ):
        raise ValueError("Renderer effective controls changed")
    return {
        "policy": POLICY,
        "imageId": image,
        "workerSha256": worker_sha,
        "hostCorePatternSha256": digest(core_pattern),
        "controls": dict(CONTROLS),
    }


def copy_file(container: str, name: str) -> bytes:
    data = docker("cp", f"{container}:/output/{name}", "-")
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:") as archive:
        entries = archive.getmembers()
        if (
            len(entries) != 1
            or not entries[0].isfile()
            or entries[0].name != name
            or not 0 < entries[0].size <= MAX_FILE
        ):
            raise ValueError("Invalid renderer output entry")
        stream = archive.extractfile(entries[0])
        assert stream is not None
        return stream.read(MAX_FILE + 1)


def handle(connection: socket.socket, image: str, worker_sha: str, spool: Path) -> None:
    container = "llmocr-render-" + uuid4().hex
    volume = container + "-output"
    created = False
    volume_created = False
    stage = "request"
    directory: Path | None = None
    try:
        connection.settimeout(10)
        with connection.makefile("rb", buffering=0) as stream:
            header = bytearray()
            header_deadline = time.monotonic() + 10
            while not header.endswith(b"\n"):
                remaining = header_deadline - time.monotonic()
                if remaining <= 0 or len(header) >= 1024:
                    raise ValueError("Invalid renderer header deadline or size")
                connection.settimeout(remaining)
                byte = stream.read(1)
                if not byte:
                    raise ValueError("Incomplete renderer header")
                header.extend(byte)
            request = validate_request(json.loads(header))
            # Reject frames before allocating or invoking Docker; there are no client path inputs.
            with request_directory(spool) as directory:
                directory.chmod(0o755)
                (directory / "owner.json").write_bytes(
                    encode(
                        {
                            "policy": POLICY,
                            "container": container,
                            "volume": volume,
                        }
                    )
                )
                source = directory / "input"
                admission_dir = directory / "admission"
                source.mkdir(mode=0o755)
                admission_dir.mkdir(mode=0o755)
                content = bytearray()
                input_deadline = time.monotonic() + 10
                while len(content) < request["size"]:
                    remaining = input_deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError("Renderer input deadline exceeded")
                    connection.settimeout(remaining)
                    block = stream.read(min(1024 * 1024, request["size"] - len(content)))
                    if not block:
                        raise ValueError("Incomplete renderer input")
                    content.extend(block)
                if digest(content) != request["sha256"]:
                    raise ValueError("Renderer input identity mismatch")
                (source / "page.xlsx").write_bytes(content)
                (source / "page.xlsx").chmod(0o444)
                del content
                stage = "volume"
                volume_created = True
                docker(
                    "volume",
                    "create",
                    "--driver",
                    "local",
                    "--label",
                    OWNER_LABEL + "=" + POLICY,
                    "--opt",
                    "type=tmpfs",
                    "--opt",
                    "device=tmpfs",
                    "--opt",
                    "o=size=67108864,uid=10001,gid=10001,mode=0700",
                    volume,
                )
                stage = "create"
                created = True
                docker(
                    "create",
                    "--name",
                    container,
                    "--label",
                    OWNER_LABEL + "=" + POLICY,
                    "--network",
                    "none",
                    "--cgroup-parent",
                    "llmocr-beta.slice",
                    "--user",
                    "10001:10001",
                    "--read-only",
                    "--cap-drop",
                    "ALL",
                    "--security-opt",
                    "no-new-privileges",
                    "--pids-limit",
                    "64",
                    "--memory",
                    "2g",
                    "--memory-swap",
                    "2g",
                    "--cpus",
                    "1",
                    "--ulimit",
                    "core=1:1",
                    "--ulimit",
                    f"fsize={MAX_FILE}:{MAX_FILE}",
                    "--tmpfs",
                    "/tmp:rw,noexec,nosuid,size=268435456,uid=10001,gid=10001,mode=0700",
                    "--mount",
                    f"type=bind,src={source},dst=/input,readonly",
                    "--mount",
                    f"type=bind,src={admission_dir},dst=/admission,readonly",
                    "--mount",
                    f"type=volume,src={volume},dst=/output,volume-nocopy",
                    "--log-driver",
                    "local",
                    "--log-opt",
                    "max-size=1m",
                    image,
                    request["mode"],
                )
                stage = "admission"
                admission = inspect_admission(container, image, worker_sha)
                path = admission_dir / "controls.json"
                path.write_bytes(encode(admission))
                path.chmod(0o444)
                docker("start", container)
                stage = "execution"
                started, heartbeat = time.monotonic(), time.monotonic()
                while True:
                    if time.monotonic() - started > 80 or time.monotonic() - heartbeat > 4:
                        raise TimeoutError("Renderer lease expired")
                    ready, _, _ = select.select([connection], [], [], 0.15)
                    if ready:
                        beat = connection.recv(4096)
                        if not beat or any(b != ord(".") for b in beat):
                            raise ValueError("Renderer lease revoked")
                        heartbeat = time.monotonic()
                    state = json.loads(docker("inspect", "--format", "{{json .State}}", container))
                    if not state["Running"]:
                        print(docker("logs", container).decode("utf-8"), flush=True)
                        raise RuntimeError("Renderer execution failed")
                    try:
                        raw_report = copy_file(container, "report.json")
                    except RuntimeError:
                        continue
                    break
                stage = "outputs"
                if len(raw_report) > 1024 * 1024:
                    raise ValueError("Oversized renderer report")
                report = json.loads(raw_report)
                report["execution"] = admission
                validate_evidence(report)
                if (
                    report["sourceSha256"] != request["sha256"]
                    or report["format"] != request["mode"]
                ):
                    raise ValueError("Renderer request changed")
                files = {"report.json": encode(report)}
                if request["mode"] != "fods":
                    files["page.pdf"] = copy_file(container, "page.pdf")
                if request["mode"] in {"fods", "preview"}:
                    files["page.fods"] = copy_file(container, "page.fods")
                if request["mode"] in {"reference", "preview"}:
                    if type(report["pageCount"]) is not int or not 1 <= report["pageCount"] <= 50:
                        raise ValueError("Invalid renderer pages")
                    for n in range(1, report["pageCount"] + 1):
                        files[f"page-{n}.png"] = copy_file(container, f"page-{n}.png")
                if sum(map(len, files.values())) > MAX_FILE:
                    raise ValueError("Oversized renderer outputs")
                remove_container(container)
                created = False
                remove_volume(volume)
                volume_created = False
                connection.settimeout(10)
                connection.sendall(
                    encode(
                        {
                            "status": "PASS",
                            "files": [
                                {"name": k, "size": len(v), "sha256": digest(v)}
                                for k, v in files.items()
                            ],
                        }
                    )
                    + b"\n"
                )
                for data in files.values():
                    connection.sendall(data)
    except (
        OSError,
        ValueError,
        RuntimeError,
        KeyError,
        TypeError,
        subprocess.SubprocessError,
    ) as exc:
        print(encode({"stage": stage, "failureType": type(exc).__name__}).decode(), flush=True)
        with contextlib.suppress(OSError):
            connection.sendall(b'{"status":"FAILED"}\n')
    finally:
        if created:
            remove_container(container)
        if volume_created:
            remove_volume(volume)
        if directory is not None:
            if directory.resolve().parent != spool.resolve() or directory.is_symlink():
                raise ValueError("Renderer cleanup escaped its spool")
            shutil.rmtree(directory)


def main() -> None:
    image = os.environ["RENDERER_IMAGE_ID"]
    worker = os.environ["RENDERER_WORKER_SHA256"]
    if not image.startswith("sha256:") or not SHA.fullmatch(image.removeprefix("sha256:")):
        raise ValueError("Pinned image ID required")
    if not SHA.fullmatch(worker):
        raise ValueError("Pinned worker identity required")
    if shutil.which("docker") is None or os.getuid() != 0:
        raise ValueError("Host supervisor requires its dedicated Docker service account")
    path = Path(os.environ.get("RENDERER_SOCKET", "/run/llmocr-renderer/renderer.sock"))
    path.parent.mkdir(mode=0o750, parents=True, exist_ok=True)
    socket_gid = int(os.environ.get("RENDERER_SOCKET_GID", "10001"))
    os.chown(path.parent, 0, socket_gid)
    path.parent.chmod(0o750)
    spool = Path(os.environ.get("RENDERER_SPOOL", "/var/lib/llmocr-renderer"))
    spool.mkdir(mode=0o700, parents=True, exist_ok=True)
    if spool.is_symlink() or spool.stat().st_uid != 0 or spool.stat().st_mode & 0o077:
        raise ValueError("Renderer spool must be root-owned and private")
    if path.exists():
        if not stat.S_ISSOCK(path.lstat().st_mode) or path.lstat().st_uid != 0:
            raise ValueError("Unexpected renderer socket owner")
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
            probe.settimeout(1)
            try:
                probe.connect(str(path))
            except ConnectionRefusedError:
                path.unlink()
            else:
                raise ValueError("Renderer supervisor is already running")
    recover_owned(spool)

    def terminate(signum: int, frame: object) -> None:
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, terminate)
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
        server.bind(str(path))
        path.chmod(0o660)
        os.chown(path, 0, socket_gid)
        server.listen(3)
        try:
            while True:
                connection, _ = server.accept()
                with connection:
                    handle(connection, image, worker, spool)
        finally:
            path.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
