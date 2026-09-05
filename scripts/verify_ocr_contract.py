from __future__ import annotations

import subprocess
import sys
import uuid
from pathlib import Path


def main() -> int:
    repository_root = Path(__file__).resolve().parents[1]
    backend = repository_root / "backend"
    targets = [
        "tests/unit/test_openai_call_budget.py",
        "tests/unit/test_deterministic_mapper.py",
        "tests/unit/test_openai_mapped_payload.py",
        "tests/unit/test_ocr_workflow_optimization.py",
        "tests/unit/test_ocr_result_previews.py",
        "tests/unit/test_review_display_structure.py",
        "tests/unit/test_ocr_confirmation_revision.py",
        "tests/unit/test_document_reconciliation_service.py",
        "tests/unit/test_spreadsheet_template_output_contract.py",
        "tests/unit/test_spreadsheet_edit_sync.py",
        "tests/unit/test_job_state.py",
        "tests/integration/test_ocr_review_api.py",
        "tests/integration/test_jobs_api.py",
        "tests/ocr_contract",
    ]
    base_temp = f".pt-ocr-{uuid.uuid4().hex[:8]}"
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", *targets, f"--basetemp={base_temp}"],
        cwd=backend,
        check=False,
    )
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
