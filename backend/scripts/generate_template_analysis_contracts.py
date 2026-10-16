"""Generate template-analysis HTTP, SSE and provider contracts from Python DTOs."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from pydantic import RootModel

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.editing.schema_tools import typescript
from app.template_analysis.contracts import (
    AnalysisCreate,
    AnalysisEventResult,
    AnalysisMergeRequest,
    AnalysisPlanResult,
    AnalysisResult,
    AnalysisRevertRequest,
    MergeResult,
    PageProposal,
)
from app.template_analysis.region_contracts import RegionAnswer
from app.template_analysis.structure_contracts import StructureAnswer
from app.template_analysis.visual_contracts import VisualProposal
from app.template_analysis.visual_form_answer import FormVisualAnswer
from app.template_analysis.visual_grid_answer import GridVisualAnswer


class TemplateAnalysisTransport(
    RootModel[
        AnalysisCreate
        | AnalysisMergeRequest
        | AnalysisPlanResult
        | AnalysisRevertRequest
        | AnalysisResult
        | MergeResult
        | AnalysisEventResult
    ]
):
    pass


def main() -> None:
    root = Path(__file__).resolve().parents[2]
    output = root / "docs/contracts/template-analysis-v3"
    output.mkdir(parents=True, exist_ok=True)
    for name, model in (
        ("proposal", PageProposal),
        ("visual-proposal", VisualProposal),
        ("structure-answer", StructureAnswer),
        ("region-answer", RegionAnswer),
        ("form-answer", FormVisualAnswer),
        ("grid-answer", GridVisualAnswer),
        ("http", TemplateAnalysisTransport),
        ("events", AnalysisEventResult),
    ):
        schema = model.model_json_schema(by_alias=True)
        (output / f"{name}.schema.json").write_text(
            json.dumps(
                {"$schema": "https://json-schema.org/draft/2020-12/schema", **schema},
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
            newline="\n",
        )
    types = typescript(TemplateAnalysisTransport.model_json_schema(by_alias=True))
    types = types.replace("app.editing.contracts", "app.template_analysis.contracts")
    (root / "frontend/src/editing/template-analysis.generated.ts").write_text(
        types,
        encoding="utf-8",
        newline="\n",
    )


if __name__ == "__main__":
    main()
