"""Compare every read API of the app on SQLite and on PostgreSQL holding the same data.

    python tools/pg_migration/compare_api.py --data <dir>/data --pg-url postgresql+psycopg://... \
        [--schema app] [--out <dir>/compare]

`--data` is a COPY of backend/data whose app.db was copied into the PostgreSQL schema with
`python -m app.db.sqlite_to_postgres`. Both apps read the same files. The OCR provider is the mock
and no AI key is set, so nothing is sent outside. Prints a summary; details go to --out.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

BACKEND = Path(__file__).resolve().parents[2] / "backend"
sys.path.insert(0, str(BACKEND))

ISO = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:?\d{2})?$")
SKIP_ROUTES = (
    "/events", "/voice", "/editing/", "/analysis", "/operations/", "/assistant/",
    "/print-pdf", "/print", "/blank-files", "/print-layout", "/page-review/output.xlsx",
)


def app_env(data: Path, database_url: str, schema: str | None) -> None:
    os.environ.update({
        "DATA_DIR": str(data), "DATABASE_URL": database_url, "LOG_FILE": str(data / "logs" / "app.log"),
        "OCR_PROVIDER": "mock", "OPENAI_API_KEY": "", "ANTHROPIC_API_KEY": "",
        "AI_RESPONSES_PROVIDER": "openai", "VOICE_PROVIDER": "disabled",
        "VOICE_TRANSCRIPT_LOG": "false", "JOB_MAX_CONCURRENCY": "1",
    })
    if schema:
        os.environ["DATABASE_SCHEMA"] = schema


def normalize(value: Any, stats: dict[str, int]) -> Any:
    if isinstance(value, dict):
        return {k: normalize(v, stats) for k, v in value.items()}
    if isinstance(value, list):
        return [normalize(v, stats) for v in value]
    if isinstance(value, str) and ISO.match(value):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00").replace(" ", "T"))
        if parsed.tzinfo is None:
            stats["naive"] = stats.get("naive", 0) + 1
            parsed = parsed.replace(tzinfo=UTC)
        else:
            stats["aware"] = stats.get("aware", 0) + 1
        return "time:" + parsed.astimezone(UTC).isoformat()
    return value


def body(response: Any, stats: dict[str, int]) -> Any:
    kind = response.headers.get("content-type", "")
    if "json" in kind:
        return normalize(response.json(), stats)
    if "spreadsheetml" in kind:
        from openpyxl import load_workbook

        book = load_workbook(io.BytesIO(response.content))
        return {sheet.title: [[c for c in row] for row in sheet.iter_rows(values_only=True)]
                for sheet in book.worksheets}
    return "sha256:" + hashlib.sha256(response.content).hexdigest()


def ids(sqlite_path: Path) -> dict[str, list[str]]:
    db = sqlite3.connect(f"file:{sqlite_path}?mode=ro", uri=True)
    try:
        return {
            "template_id": [r[0] for r in db.execute("SELECT id FROM templates ORDER BY id")],
            "version_id": [r[0] for r in db.execute("SELECT id FROM template_versions ORDER BY id")],
            "job_id": [r[0] for r in db.execute("SELECT id FROM processing_jobs ORDER BY id")],
        }
    finally:
        db.close()


def requests(app: Any, found: dict[str, list[str]], documents: list[str]) -> tuple[list[str], list[str]]:
    paths, skipped = ["/api/v2/structured/documents", "/api/v2/structured/fields"], []
    found = {**found, "document_id": documents}
    for route in app.routes:
        if "GET" not in (getattr(route, "methods", None) or set()):
            continue
        path = route.path
        names = re.findall(r"{(\w+)}", path)
        if any(part in path for part in SKIP_ROUTES):
            skipped.append(path)
            continue
        if not names:
            paths.append(path)
            continue
        if names == ["job_id"] and path.startswith("/api/v2/structured/documents"):
            paths += [path.replace("{job_id}", j) for j in documents]
        elif len(names) == 1 and names[0] in found:
            paths += [path.replace("{" + names[0] + "}", v) for v in found[names[0]]]
        else:
            skipped.append(path)
    return sorted(set(paths)), sorted(set(skipped))


def run(data: Path, url: str, schema: str | None, paths: list[str] | None,
        found: dict[str, list[str]]) -> tuple[dict[str, Any], list[str], list[str], dict[str, int]]:
    app_env(data, url, schema)
    for name in [m for m in sys.modules if m == "app" or m.startswith("app.")]:
        del sys.modules[name]
    from fastapi.testclient import TestClient

    from app.core.config import Settings
    from app.main import create_business_app

    app = create_business_app(Settings.load(env_file=BACKEND / ".env"))
    stats: dict[str, int] = {}
    results: dict[str, Any] = {}
    skipped: list[str] = []
    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        if paths is None:
            listing = client.get("/api/v2/structured/documents")
            documents = [d["jobId"] for d in listing.json().get("items", [])] if listing.status_code == 200 else []
            paths, skipped = requests(app, found, documents)
            artifact_paths = []
            for job in found["job_id"]:
                listed = client.get(f"/api/v1/jobs/{job}/artifacts")
                if listed.status_code == 200:
                    items = listed.json()
                    if isinstance(items, dict):
                        items = items.get("artifacts") or items.get("items") or []
                    for item in items[:4]:
                        name = item.get("artifactName") or item.get("name")
                        if name:
                            artifact_paths.append(f"/api/v1/jobs/{job}/artifacts/{name}")
            paths = sorted(set(paths + artifact_paths))
        for path in paths:
            response = client.get(path)
            results[path] = {"status": response.status_code, "body": body(response, stats)}
    return results, paths, skipped, stats


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--pg-url", required=True)
    parser.add_argument("--schema", default="app")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    sqlite_path = (args.data / "app.db").resolve()
    found = ids(sqlite_path)
    sqlite_url = "sqlite:///" + sqlite_path.as_posix()
    first, paths, skipped, sqlite_stats = run(args.data, sqlite_url, None, None, found)
    second, _, _, pg_stats = run(args.data, args.pg_url, args.schema, paths, found)
    differences = [p for p in paths if first[p] != second[p]]
    statuses: dict[int, int] = {}
    for path in paths:
        statuses[first[path]["status"]] = statuses.get(first[path]["status"], 0) + 1
    print(f"requests: {len(paths)}  statuses(sqlite): {dict(sorted(statuses.items()))}")
    print(f"skipped routes: {len(skipped)}")
    print(f"time strings  sqlite: {sqlite_stats}  postgres: {pg_stats}")
    print(f"different: {len(differences)}")
    for path in differences[:40]:
        print("  DIFF", path, first[path]["status"], "->", second[path]["status"])
    if args.out:
        args.out.mkdir(parents=True, exist_ok=True)
        (args.out / "sqlite.json").write_text(json.dumps(first, ensure_ascii=False, default=str, indent=1), "utf-8")
        (args.out / "postgres.json").write_text(json.dumps(second, ensure_ascii=False, default=str, indent=1), "utf-8")
        (args.out / "skipped.json").write_text(json.dumps(skipped, indent=1), "utf-8")
    return 1 if differences else 0


if __name__ == "__main__":
    sys.exit(main())
