"""Disposable feasibility probe. Never issues production renderer evidence."""

import ctypes
import errno
import hashlib
import json
import os
import resource
import signal
import socket
import subprocess
import time
from contextlib import suppress
from pathlib import Path

import seccomp

INPUT = Path("/input/page.xlsx")
OUT = Path("/output")
LIMIT = 64 * 1024 * 1024


def sha(data):
    return hashlib.sha256(data).hexdigest()


def guard():
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(38, 1, 0, 0, 0) or libc.prctl(4, 0, 0, 0, 0):
        raise ValueError("GUARD_UNAVAILABLE")
    for kind, value in (
        (resource.RLIMIT_CORE, 1),
        (resource.RLIMIT_FSIZE, LIMIT),
        (resource.RLIMIT_NPROC, 64),
        (resource.RLIMIT_CPU, 45),
    ):
        resource.setrlimit(kind, (value, value))
    filt = seccomp.SyscallFilter(defaction=seccomp.ALLOW)
    # UNIX IPC is needed by LibreOffice. Every other socket family is denied.
    filt.add_rule(
        seccomp.ERRNO(errno.EPERM), "socket", seccomp.Arg(0, seccomp.NE, socket.AF_UNIX)
    )
    filt.add_rule(
        seccomp.ERRNO(errno.EPERM),
        "socketpair",
        seccomp.Arg(0, seccomp.NE, socket.AF_UNIX),
    )
    for name in (
        "io_uring_setup",
        "ptrace",
        "process_vm_readv",
        "process_vm_writev",
        "setns",
        "unshare",
        "mount",
        "bpf",
        "perf_event_open",
        "setrlimit",
    ):
        filt.add_rule(seccomp.ERRNO(errno.EPERM), name)
    filt.add_rule(
        seccomp.ERRNO(errno.EPERM), "prlimit64", seccomp.Arg(2, seccomp.NE, 0)
    )
    filt.add_rule(
        seccomp.ERRNO(errno.EPERM),
        "prctl",
        seccomp.Arg(0, seccomp.EQ, 4),
        seccomp.Arg(1, seccomp.NE, 0),
    )
    filt.load()


def observed(initial_credentials):
    result = {
        "uid": os.getuid(),
        "gid": os.getgid(),
        "pid": os.getpid(),
        "limits": {
            name: list(resource.getrlimit(kind))
            for name, kind in [
                ("core", resource.RLIMIT_CORE),
                ("file", resource.RLIMIT_FSIZE),
                ("processes", resource.RLIMIT_NPROC),
                ("cpu", resource.RLIMIT_CPU),
            ]
        },
    }
    status = Path("/proc/self/status").read_text().splitlines()
    result["process"] = {
        k: next(x.split(":", 1)[1].strip() for x in status if x.startswith(k + ":"))
        for k in ("CapEff", "NoNewPrivs", "Seccomp")
    }
    if result["process"] != {
        "CapEff": "0000000000000000",
        "NoNewPrivs": "1",
        "Seccomp": "2",
    }:
        raise ValueError("PROCESS_GUARD_FAILED")
    visible_transport = False
    for p in Path("/proc").glob("[0-9]*/cmdline"):
        with suppress(PermissionError, FileNotFoundError):
            visible_transport |= b"/opt/probe/transport.py" in p.read_bytes()
    result["transportProcessInvisible"] = not visible_transport
    result["credentialEnvironmentAbsentBeforeClear"] = not initial_credentials
    result["credentialEnvironmentAbsent"] = not any(
        x.startswith(("AWS_", "OPENAI_", "INPUT_URL", "OUTPUT_URL"))
        for x in os.environ
        if x not in {"AWS_EXECUTION_ENV", "AWS_REGION", "AWS_DEFAULT_REGION"}
    )
    # Fargate injects container metadata URLs, not credentials, even without a task role.
    result["taskCredentialUriAbsent"] = (
        "AWS_CONTAINER_CREDENTIALS_RELATIVE_URI" not in os.environ
    )
    result["taskCredentialFullUriAbsent"] = (
        "AWS_CONTAINER_CREDENTIALS_FULL_URI" not in os.environ
    )
    for name, family in [
        ("ipv4", socket.AF_INET),
        ("ipv6", socket.AF_INET6),
        ("netlink", socket.AF_NETLINK),
        ("packet", socket.AF_PACKET),
    ]:
        try:
            with socket.socket(family, socket.SOCK_STREAM):
                pass
        except OSError as exc:
            result[name] = exc.errno
        else:
            raise ValueError("NETWORK_ALLOWED")
        if result[name] != errno.EPERM:
            raise ValueError("NETWORK_GUARD_UNVERIFIED")
    a, b = socket.socketpair()
    a.sendall(b"ipc")
    result["unixIpc"] = b.recv(3) == b"ipc"
    a.close()
    b.close()
    (OUT / "preflight.json").write_text(json.dumps(result))
    child = subprocess.run(
        [
            "/usr/bin/python3",
            "-I",
            "-B",
            "-c",
            "import socket,errno\ntry: socket.socket(socket.AF_INET,socket.SOCK_STREAM)\n"
            "except OSError as e: raise SystemExit(0 if e.errno==errno.EPERM else 2)\n"
            "raise SystemExit(3)",
        ],
        check=False,
    )
    result["execNetworkDenied"] = child.returncode == 0
    try:
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    except (OSError, ValueError):
        result["limitMutationDenied"] = True
    else:
        raise ValueError("LIMIT_MUTATION_ALLOWED")
    for name, path in [
        ("inputReadOnly", INPUT),
        ("rootReadOnly", Path("/opt/probe/write-probe")),
    ]:
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT, 0o600)
        except OSError as exc:
            result[name] = exc.errno in (errno.EROFS, errno.EACCES)
        else:
            os.close(fd)
            raise ValueError("READONLY_GUARD_FAILED")
    result["hostPaths"] = {}
    for x in [
        "/data",
        "/var/run/docker.sock",
        "/root/.aws",
        "/opt/llmocr/backend/.env",
    ]:
        try:
            present = Path(x).exists()
        except PermissionError:
            result["hostPaths"][x] = "permission-denied"
        else:
            result["hostPaths"][x] = "present" if present else "absent"
    result["hostPathsInaccessible"] = all(
        x != "present" for x in result["hostPaths"].values()
    )
    cg = Path("/proc/self/cgroup").read_text()
    result["cgroupMembership"] = cg
    for name in ("memory.max", "memory.limit_in_bytes", "pids.max", "cpu.max"):
        for path in (
            Path("/sys/fs/cgroup") / name,
            Path("/sys/fs/cgroup/memory") / name,
        ):
            if path.is_file():
                result[name] = path.read_text().strip()
    if not all(
        result[k]
        for k in (
            "unixIpc",
            "execNetworkDenied",
            "limitMutationDenied",
            "inputReadOnly",
            "rootReadOnly",
            "hostPathsInaccessible",
            "transportProcessInvisible",
            "credentialEnvironmentAbsentBeforeClear",
            "taskCredentialUriAbsent",
            "taskCredentialFullUriAbsent",
        )
    ):
        raise ValueError("ISOLATION_OBSERVATION_FAILED")
    return result


def run(args):
    with open("/scratch/tool.log", "w+b") as log:
        result = subprocess.run(
            args,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=log,
            timeout=50,
            check=False,
            close_fds=True,
        )
        if result.returncode:
            log.seek(0)
            diagnostic = log.read(1024 * 1024)
            (OUT / "tool-failure.json").write_text(
                json.dumps(
                    {
                        "tool": Path(args[0]).name,
                        "returncode": result.returncode,
                        "logSha256": sha(diagnostic),
                        "patterns": {
                            word: word.encode().lower() in diagnostic.lower()
                            for word in (
                                "Read-only file system",
                                "Permission denied",
                                "osl::Pipe",
                                "pipe",
                                "socket",
                                "Resource temporarily unavailable",
                            )
                        },
                    }
                )
            )
            raise ValueError("RENDER_FAILED")
        log.seek(0)
        return log.read(1024 * 1024)


def main():
    signal.alarm(100)
    deadline = time.monotonic() + 35
    while not Path("/input/ready.json").is_file():
        if time.monotonic() > deadline:
            raise ValueError("INPUT_MISSING")
        time.sleep(0.1)
    # All environment data is replaced before reading workbook bytes or launching tools.
    initial_credentials = [
        x
        for x in os.environ
        if x
        in {
            "AWS_ACCESS_KEY_ID",
            "AWS_SECRET_ACCESS_KEY",
            "AWS_SESSION_TOKEN",
            "AWS_CONTAINER_CREDENTIALS_RELATIVE_URI",
            "AWS_CONTAINER_CREDENTIALS_FULL_URI",
            "OPENAI_API_KEY",
            "INPUT_URL",
            "OUTPUT_URL",
        }
    ]
    os.environ.clear()
    os.environ.update(
        HOME="/scratch/profile",
        LANG="C.UTF-8",
        TMPDIR="/scratch",
        PATH="/usr/bin:/bin",
        PYTHONDONTWRITEBYTECODE="1",
    )
    guard()
    report = {
        "policy": "fargate-renderer-feasibility-v1",
        "productionEvidence": False,
        "observations": observed(initial_credentials),
    }
    manifest = json.loads(Path("/input/ready.json").read_bytes())
    source = INPUT.read_bytes()
    if len(source) > LIMIT or sha(source) != manifest["sha256"]:
        raise ValueError("INPUT_MISMATCH")
    report["inputSha256"] = sha(source)
    mode = manifest["mode"]
    (OUT / "started.json").write_text(json.dumps(report))
    if mode == "timeout":
        signal.alarm(3)
        while True:
            time.sleep(1)
    if mode == "oom":
        blocks = []
        while True:
            blocks.append(bytearray(32 * 1024 * 1024))
    if mode == "cancel":
        while True:
            time.sleep(1)
    if mode != "render":
        raise ValueError("INVALID_MODE")
    profile = Path("/scratch/profile/user")
    profile.mkdir(parents=True)
    (profile / "registrymodifications.xcu").write_text(
        '<?xml version="1.0"?><oor:items xmlns:oor="http://openoffice.org/2001/registry">'
        '<item oor:path="/org.openoffice.Office.Common/Security/Scripting">'
        '<prop oor:name="DisableMacrosExecution" oor:op="fuse"><value>true</value></prop>'
        '<prop oor:name="DisableActiveContent" oor:op="fuse"><value>true</value></prop>'
        "</item></oor:items>"
    )
    started = time.monotonic()
    base = [
        "/usr/bin/soffice",
        "--headless",
        "--nologo",
        "--nodefault",
        "--norestore",
        "--nofirststartwizard",
        "-env:UserInstallation=file:///scratch/profile",
        "--convert-to",
    ]
    for fmt in ("pdf", "fods"):
        run([*base, fmt, "--outdir", "/output", str(INPUT)])
    info = run(["/usr/bin/pdfinfo", "/output/page.pdf"]).decode()
    count = int(
        next(x.split(":", 1)[1] for x in info.splitlines() if x.startswith("Pages:"))
    )
    if not 1 <= count <= 50:
        raise ValueError("PAGE_COUNT_MISMATCH")
    for n in range(1, count + 1):
        run(
            [
                "/usr/bin/pdftoppm",
                "-f",
                str(n),
                "-l",
                str(n),
                "-singlefile",
                "-r",
                "150",
                "-png",
                "/output/page.pdf",
                f"/output/page-{n}",
            ]
        )
    report.update(
        pageCount=count,
        renderSeconds=round(time.monotonic() - started, 3),
        status="PASS",
    )
    report["files"] = {
        p.name: {"sha256": sha(p.read_bytes()), "size": p.stat().st_size}
        for p in OUT.iterdir()
        if p.name != "started.json"
    }
    if sum(x["size"] for x in report["files"].values()) > LIMIT:
        raise ValueError("OUTPUT_TOO_LARGE")
    report["nativeSha256"] = sha(Path(__file__).read_bytes())
    (OUT / "report.json").write_text(json.dumps(report, sort_keys=True))
    (OUT / "done").write_text("done")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        # No document content, environment or signed URL is logged.
        (OUT / "failure.json").write_text(
            json.dumps(
                {
                    "status": "FAILED",
                    "type": type(exc).__name__,
                    "code": str(exc)
                    if str(exc).isidentifier() and str(exc).isupper()
                    else "FAILED",
                }
            )
        )
        (OUT / "done").write_text("failed")
        raise SystemExit(1) from None
