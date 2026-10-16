"""Transport has per-object signed URLs only. No AWS task role, SDK or document parser."""

import hashlib
import io
import json
import os
import signal
import ssl
import tarfile
import time
import urllib.request
from pathlib import Path

LIMIT = 64 * 1024 * 1024
STAGE = "startup"


def main():
    global STAGE
    signal.alarm(160)
    incoming = os.environ.pop("INPUT_URL")
    outgoing = os.environ.pop("OUTPUT_URL")
    trust = ssl.create_default_context(cafile="/opt/probe/ca-certificates.crt")
    STAGE = "download"
    with urllib.request.urlopen(incoming, timeout=20, context=trust) as response:
        data = response.read(LIMIT + 1)
    if len(data) > LIMIT:
        raise ValueError("INPUT_TOO_LARGE")
    STAGE = "publish-input"
    path = Path("/input/page.xlsx")
    path.write_bytes(data)
    path.chmod(0o444)
    manifest = {
        "sha256": hashlib.sha256(data).hexdigest(),
        "mode": os.environ.pop("PROBE_MODE"),
    }
    tmp = Path("/input/ready.tmp")
    tmp.write_text(json.dumps(manifest))
    tmp.chmod(0o444)
    tmp.replace("/input/ready.json")
    STAGE = "wait-native"
    deadline = time.monotonic() + 110
    while not Path("/output/done").exists() and time.monotonic() < deadline:
        time.sleep(0.2)
    STAGE = "collect-output"
    items = sorted(Path("/output").iterdir())
    if len(items) > 60 or sum(p.lstat().st_size for p in items) > LIMIT:
        raise ValueError("OUTPUT_TOO_LARGE")
    result = io.BytesIO()
    with tarfile.open(fileobj=result, mode="w:gz") as archive:
        for item in items:
            if item.is_symlink() or not item.is_file() or item.stat().st_size > LIMIT:
                raise ValueError("INVALID_OUTPUT")
            archive.add(item, arcname=item.name, recursive=False)
    body = result.getvalue()
    if len(body) > LIMIT:
        raise ValueError("OUTPUT_TOO_LARGE")
    STAGE = "upload"
    with urllib.request.urlopen(
        urllib.request.Request(outgoing, data=body, method="PUT"),
        timeout=20,
        context=trust,
    ) as response:
        if response.status != 200:
            raise ValueError("UPLOAD_FAILED")
    print(
        json.dumps({"uploaded": True, "completed": Path("/output/done").exists()}),
        flush=True,
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(
            json.dumps(
                {
                    "transport": "FAILED",
                    "stage": STAGE,
                    "type": type(exc).__name__,
                    "reasonType": type(getattr(exc, "reason", None)).__name__,
                    "errno": getattr(exc, "errno", None),
                    "httpStatus": getattr(exc, "code", None),
                    "mounts": {
                        str(p): {
                            "uid": p.stat().st_uid,
                            "mode": oct(p.stat().st_mode & 0o777),
                        }
                        for p in (Path("/input"), Path("/output"))
                    },
                }
            ),
            flush=True,
        )
        raise SystemExit(1) from None
