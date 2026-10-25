"""Run the same write flows on SQLite and on PostgreSQL copies and compare the outcomes.

    python tools/pg_migration/flows.py --sqlite-data D:/copy/flow-sqlite \
        --pg-data D:/copy/flow-pg --pg-url postgresql+psycopg://... [--schema app] \
        --image D:/copy/photo.jpg --template-id <id>

Both data folders must hold the same files; the PostgreSQL schema holds a copy of the SQLite
app.db (`python -m app.db.sqlite_to_postgres`). Mock OCR, no AI keys: nothing leaves the PC.
Steps: upload -> wait -> correct a value -> confirm -> XLSX -> reopen -> cancel reopen ->
display name / document name / item joins -> trash + restore a template -> delete the job.
"""

from __future__ import annotations

import argparse
import io
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from compare_api import BACKEND, app_env, normalize  # noqa: E402

UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def stable(value: Any, names: dict[str, str]) -> Any:
    """New IDs differ per run: number them in order of first appearance."""
    if isinstance(value, dict):
        return {stable(k, names): stable(v, names) for k, v in value.items()}
    if isinstance(value, list):
        return [stable(v, names) for v in value]
    if isinstance(value, str):
        if value.startswith("time:"):
            return "<time>"  # the two runs happen at different moments
        return UUID.sub(lambda m: names.setdefault(m.group(0), f"<id{len(names)}>"), value)
    return value


def flows(data: Path, url: str, schema: str | None, image: Path, template_id: str) -> list[Any]:
    app_env(data, url, schema)
    os.environ["EDITING_API_ENABLED"] = "false"  # v1 corrections; v2 is checked on screen
    for name in [m for m in sys.modules if m == "app" or m.startswith("app.")]:
        del sys.modules[name]
    from fastapi.testclient import TestClient
    from openpyxl import load_workbook

    from app.core.config import Settings
    from app.main import create_business_app

    app = create_business_app(Settings.load(env_file=BACKEND / ".env"))
    log: list[Any] = []
    stats: dict[str, int] = {}

    def record(step: str, response: Any) -> Any:
        kind = response.headers.get("content-type", "")
        content: Any
        if "json" in kind:
            content = normalize(response.json(), stats)
        elif "spreadsheetml" in kind:
            book = load_workbook(io.BytesIO(response.content))
            content = {s.title: [list(r) for r in s.iter_rows(values_only=True)] for s in book.worksheets}
        else:
            content = len(response.content)
        log.append({"step": step, "status": response.status_code, "body": content})
        if response.status_code >= 400:
            print(f"  {step}: HTTP {response.status_code} {str(content)[:300]}")
        return response.json() if "json" in kind else None

    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        template = record("template", client.get(f"/api/v1/templates/{template_id}"))
        version_id = template["activeVersionId"]
        with image.open("rb") as handle:
            job = record("upload", client.post(
                "/api/v1/ocr-jobs", data={"templateVersionId": version_id, "ocrEnabled": "true"},
                files={"file": (image.name, handle, "image/jpeg")},
                headers={"Idempotency-Key": "ocr-pg-flow-1"}))
        job_id = job["jobId"]
        for _ in range(300):
            state = client.get(f"/api/v1/jobs/{job_id}").json()
            if state["status"] not in ("QUEUED", "PROCESSING", "RUNNING", "PENDING"):
                break
            time.sleep(0.5)
        record("job", client.get(f"/api/v1/jobs/{job_id}"))
        result = record("result", client.get(f"/api/v1/ocr-jobs/{job_id}/result"))
        fields = result["result"]["fields"]
        # Mock readings are blank or uncertain: fill them so the identity check lets it confirm.
        edited = {key: {**reading, "raw": reading.get("raw") or "テスト",
                        "normalized": reading.get("normalized") or "テスト",
                        "status": "readable", "reviewReason": None}
                  for key, reading in fields.items()}
        edited[sorted(fields)[0]].update(raw="PG移行テスト", normalized="PG移行テスト")
        result = record("correct", client.put(f"/api/v1/ocr-jobs/{job_id}/corrections", json={
            "baseRevision": result["revision"], "fields": edited, "reviewItems": []}))
        result = record("confirm", client.post(f"/api/v1/ocr-jobs/{job_id}/confirm", json={
            "baseRevision": result["revision"], "documentName": "PG移行テスト書類"}))
        record("export", client.get(f"/api/v1/ocr-jobs/{job_id}/export"))
        record("excel-table", client.get(f"/api/v1/ocr-jobs/{job_id}/excel-table"))
        record("structured", client.get(f"/api/v2/structured/documents/{job_id}"))
        result = record("reopen", client.post(f"/api/v1/ocr-jobs/{job_id}/reopen",
                                              json={"baseRevision": result["revision"]}))
        record("cancel-reopen", client.post(f"/api/v1/ocr-jobs/{job_id}/cancel-reopen",
                                            json={"baseRevision": result["revision"]}))
        target = "fields." + sorted(fields)[0]
        record("display-name", client.put(f"/api/v1/templates/{template_id}/display-names",
                                          json={"targetPath": target, "displayName": "PG移行テスト欄"}))
        record("display-name-reset", client.put(f"/api/v1/templates/{template_id}/display-names",
                                                json={"targetPath": target, "displayName": ""}))
        record("document-name", client.get(f"/api/v1/templates/{template_id}/document-name"))
        record("item-joins", client.get(f"/api/v1/templates/{template_id}/item-joins"))
        record("documents", client.get("/api/v2/structured/documents"))
        record("delete-job", client.delete(f"/api/v1/jobs/{job_id}?confirm=true"))
        record("job-after-delete", client.get(f"/api/v1/jobs/{job_id}"))
        record("jobs", client.get("/api/v1/jobs"))
        # Template trash round trip on a template without documents.
        templates = client.get("/api/v1/templates").json()
        spare = next((t for t in templates if t["id"] != template_id
                      and client.get(f"/api/v1/templates/{t['id']}/delete-impact").json().get("documents", 1) == 0), None)
        if spare:
            record("trash", client.delete(f"/api/v1/templates/{spare['id']}"))
            record("trash-list", client.get("/api/v1/template-trash"))
            record("restore", client.post(f"/api/v1/template-trash/{spare['id']}/restore"))
            record("templates", client.get("/api/v1/templates"))
    log.append({"step": "time-strings", "body": stats})
    return log


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sqlite-data", type=Path, required=True)
    parser.add_argument("--pg-data", type=Path, required=True)
    parser.add_argument("--pg-url", required=True)
    parser.add_argument("--schema", default="app")
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--template-id", required=True)
    args = parser.parse_args()
    sqlite_url = "sqlite:///" + (args.sqlite_data / "app.db").resolve().as_posix()
    first = stable(flows(args.sqlite_data, sqlite_url, None, args.image, args.template_id), {})
    second = stable(flows(args.pg_data, args.pg_url, args.schema, args.image, args.template_id), {})
    different = 0
    for a, b in zip(first, second, strict=True):
        same = a.get("status") == b.get("status") and a.get("body") == b.get("body")
        if a["step"] == "time-strings":
            print(f"time strings  sqlite: {a['body']}  postgres: {b['body']}")
            continue
        different += not same
        print(f"{'same' if same else 'DIFF'}  {a['step']}: {a['status']} / {b['status']}")
    print("RESULT:", "OK" if not different else f"{different} step(s) differ")
    return 1 if different else 0


if __name__ == "__main__":
    sys.exit(main())
