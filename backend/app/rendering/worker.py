"""Fixed Linux image entry point. No network, host command or host path inputs."""

from __future__ import annotations

import hashlib
import json
import os
import resource
import signal
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from zipfile import ZipFile

MAX_FILE = 64 * 1024 * 1024
INPUT = Path("/input/page.xlsx")
OUTPUT = Path("/output")


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def run(command: list[str], timeout: int = 55) -> bytes:
    # Output goes to a bounded regular file; workbook-controlled diagnostics are never returned.
    with open("/tmp/tool.log", "w+b") as log:
        result = subprocess.run(
            command, stdin=subprocess.DEVNULL, stdout=log, stderr=log, timeout=timeout, check=False
        )
        if result.returncode:
            raise ValueError("RENDER_FAILED")
        log.seek(0)
        return log.read(1024 * 1024)


def fonts() -> list[dict[str, str]]:
    with ZipFile(INPUT) as source:
        item = source.getinfo("xl/styles.xml")
        if item.file_size > 4 * 1024 * 1024:
            raise ValueError("FONT_REQUEST_TOO_LARGE")
        data = source.read(item)
    if b"<!DOCTYPE" in data.upper() or b"<!ENTITY" in data.upper():
        raise ValueError("INVALID_FONT_XML")
    root = ET.fromstring(data)
    requests = sorted(
        {
            node.attrib["val"]
            for node in root.iter()
            if node.tag.endswith("}name") and "val" in node.attrib
        }
    )
    if not requests or len(requests) > 256:
        raise ValueError("INVALID_FONT_REQUEST")
    result = []
    for family in requests:
        if len(family) > 200 or any(c in family for c in ":\\\n\r"):
            raise ValueError("INVALID_FONT_REQUEST")
        raw = run(["/usr/bin/fc-match", "--format=%{family}\n%{file}\n", "--", family])
        resolved, filename = raw.decode().splitlines()[:2]
        font = Path(filename).resolve(strict=True)
        if not font.is_relative_to("/usr/share/fonts"):
            raise ValueError("INVALID_FONT_SOURCE")
        result.append(
            {
                "requestedFamily": family,
                "resolvedFamily": resolved,
                "sha256": sha(font.read_bytes()),
            }
        )
    return result


def main() -> None:
    signal.alarm(75)
    # The supervisor supplies a read-only admission after inspecting the created container.
    deadline = time.monotonic() + 8
    admission_path = Path("/admission/controls.json")
    while not admission_path.is_file():
        if time.monotonic() > deadline:
            raise ValueError("ADMISSION_MISSING")
        time.sleep(0.02)
    admission_bytes = admission_path.read_bytes()
    admission = json.loads(admission_bytes)
    if (
        os.getuid() != 10001
        or os.getgid() != 10001
        or admission["controls"]["network"] != "none"
        or sha(Path(__file__).read_bytes()) != admission["workerSha256"]
    ):
        raise ValueError("ADMISSION_MISMATCH")
    # Disable dumps in the Python worker; limit=1 plus seccomp also protects exec children.
    import ctypes

    import seccomp  # type: ignore[import-not-found]

    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(4, 0, 0, 0, 0) or libc.prctl(3, 0, 0, 0, 0) != 0:
        raise ValueError("CORE_GUARD_FAILED")
    # Linux rejects pipe core handlers at limit=1 (fs/coredump.c), unlike limit=0.
    resource.setrlimit(resource.RLIMIT_CORE, (1, 1))
    resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_FILE, MAX_FILE))
    guard = seccomp.SyscallFilter(defaction=seccomp.ALLOW)
    # Subprocesses cannot re-enable process dumps or increase resource limits.
    guard.add_rule(
        seccomp.ERRNO(1), "prctl", seccomp.Arg(0, seccomp.EQ, 4), seccomp.Arg(1, seccomp.NE, 0)
    )
    guard.add_rule(seccomp.ERRNO(1), "setrlimit")
    guard.add_rule(seccomp.ERRNO(1), "prlimit64", seccomp.Arg(2, seccomp.NE, 0))
    guard.load()
    try:
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    except (OSError, ValueError):
        mutation_denied = True
    else:
        raise ValueError("CORE_GUARD_MUTATION_ALLOWED")
    if resource.getrlimit(resource.RLIMIT_CORE) != (1, 1):
        raise ValueError("CORE_GUARD_LIMIT_CHANGED")
    mode = sys.argv[1]
    if mode not in {"pdf", "fods", "reference", "preview"}:
        raise ValueError("INVALID_MODE")
    source_sha = sha(INPUT.read_bytes())
    resolved = fonts()
    font_files = sorted(p for p in Path("/usr/share/fonts").rglob("*") if p.is_file())
    font_sha = sha(
        json.dumps(
            [(str(p), sha(p.read_bytes())) for p in font_files], separators=(",", ":")
        ).encode()
    )
    profile = Path("/tmp/profile/user")
    profile.mkdir(parents=True)
    (profile / "registrymodifications.xcu").write_text(
        '<?xml version="1.0"?><oor:items xmlns:oor="http://openoffice.org/2001/registry">'
        '<item oor:path="/org.openoffice.Office.Common/Security/Scripting">'
        '<prop oor:name="DisableMacrosExecution" oor:op="fuse"><value>true</value></prop>'
        '<prop oor:name="DisableActiveContent" oor:op="fuse"><value>true</value></prop>'
        "</item></oor:items>"
    )
    base = [
        "/usr/bin/soffice",
        "--headless",
        "--nologo",
        "--nodefault",
        "--norestore",
        "--nofirststartwizard",
        "-env:UserInstallation=file:///tmp/profile",
        "--convert-to",
    ]
    formats = ["fods"] if mode == "fods" else ["pdf", *(["fods"] if mode == "preview" else [])]
    for output_format in formats:
        run([*base, output_format, "--outdir", str(OUTPUT), str(INPUT)])
    report = {
        "policy": "linux-container-renderer-v1",
        "status": "PASS",
        "format": mode,
        "fontSource": "linux-image",
        "fontRequestPolicy": "linux-fontconfig-face-v1",
        "sourceSha256": source_sha,
        "inputSha256": source_sha,
        "resolvedFonts": resolved,
        "fontDirectorySha256": font_sha,
        "packagesSha256": sha(Path("/opt/renderer/packages.txt").read_bytes()),
        "workerSha256": sha(Path(__file__).read_bytes()),
        "admissionSha256": sha(admission_bytes),
        "coreDumpSuppressed": True,
        "coreGuardMutationDenied": mutation_denied,
    }
    if mode != "fods":
        info = run(["/usr/bin/pdfinfo", str(OUTPUT / "page.pdf")]).decode()
        count = int(
            next(line.split(":", 1)[1] for line in info.splitlines() if line.startswith("Pages:"))
        )
        if not 1 <= count <= 50:
            raise ValueError("PAGE_COUNT_MISMATCH")
        sizes = []
        for number in range(1, count + 1):
            raw = run(
                ["/usr/bin/pdfinfo", "-f", str(number), "-l", str(number), str(OUTPUT / "page.pdf")]
            ).decode()
            line = next(line for line in raw.splitlines() if " size:" in line)
            values = line.split("size:", 1)[1].strip().split()
            sizes.append([float(values[0]), float(values[2])])
        report.update(
            pageCount=count, sizes=sizes, pdfSha256=sha((OUTPUT / "page.pdf").read_bytes())
        )
        if mode in {"reference", "preview"}:
            from PIL import Image

            hashes, image_sizes = [], []
            for number in range(1, count + 1):
                run(
                    [
                        "/usr/bin/pdftoppm",
                        "-f",
                        str(number),
                        "-l",
                        str(number),
                        "-singlefile",
                        "-r",
                        "150",
                        "-png",
                        str(OUTPUT / "page.pdf"),
                        str(OUTPUT / f"page-{number}"),
                    ]
                )
                path = OUTPUT / f"page-{number}.png"
                with Image.open(path) as image:
                    if image.width * image.height > 20_000_000:
                        raise ValueError("PAGE_GEOMETRY_MISMATCH")
                    image_sizes.append([image.width, image.height])
                hashes.append(sha(path.read_bytes()))
            report.update(
                imageSha256=hashes, imageSizes=image_sizes, dpi=150, rasterizer="poppler-pdftoppm"
            )
    if mode in {"fods", "preview"}:
        report["fodsSha256"] = sha((OUTPUT / "page.fods").read_bytes())
    if sum(p.stat().st_size for p in OUTPUT.iterdir()) > MAX_FILE - 1024 * 1024:
        raise ValueError("OUTPUT_TOO_LARGE")
    (OUTPUT / "report.json").write_text(json.dumps(report, sort_keys=True))
    # Keep the bounded tmpfs mounted until the supervisor copies the outputs.
    # The independent alarm also stops the container if the supervisor dies.
    while True:
        time.sleep(0.2)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        # Fixed classification only; never emit native diagnostics or workbook text.
        code = str(exc)
        print(
            json.dumps(
                {
                    "failureType": type(exc).__name__,
                    "code": code if code.isidentifier() and code.isupper() else "FAILED",
                }
            ),
            flush=True,
        )
        sys.exit(1)
