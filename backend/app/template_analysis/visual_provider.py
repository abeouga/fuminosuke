"""Bounded image-to-semantics request using the shared paid transport."""

from __future__ import annotations

import base64
import json
import time
from collections.abc import Awaitable, Callable
from typing import Any

from jsonschema import Draft202012Validator  # type: ignore[import-untyped]
from jsonschema.exceptions import (  # type: ignore[import-untyped]
    ValidationError as SchemaValidationError,
)

from app.editing.domain.command import digest
from app.editing.errors import reject
from app.template_analysis.binding_resolution import POLICY as RESOLUTION_POLICY
from app.template_analysis.draft_answer import INSTRUCTIONS as DRAFT_INSTRUCTIONS
from app.template_analysis.draft_answer import MAX_OUTPUT_TOKENS as DRAFT_MAX_OUTPUT_TOKENS
from app.template_analysis.draft_answer import POLICY as DRAFT_POLICY
from app.template_analysis.draft_answer import REASONING_EFFORT as DRAFT_REASONING_EFFORT
from app.template_analysis.draft_answer import TIMEOUT_SECONDS as DRAFT_TIMEOUT_SECONDS
from app.template_analysis.draft_answer import DraftVisualAnswer
from app.template_analysis.provider import OpenAIAnalysisProvider
from app.template_analysis.region_contracts import RegionAnswer
from app.template_analysis.region_pack import (
    INSTRUCTIONS as REGION_INSTRUCTIONS,
)
from app.template_analysis.region_pack import (
    POLICY as REGION_PACK_POLICY,
)
from app.template_analysis.region_pack import (
    region_pack,
)
from app.template_analysis.region_plan import POLICY as REGION_POLICY
from app.template_analysis.region_schema import POLICY as REGION_SCHEMA_POLICY
from app.template_analysis.region_schema import policy_parameters as region_schema_parameters
from app.template_analysis.region_schema import region_schema
from app.template_analysis.structure_contracts import POLICY as STRUCTURE_POLICY
from app.template_analysis.structure_contracts import StructureAnswer
from app.template_analysis.visual_bindings import POLICY as BINDING_POLICY
from app.template_analysis.visual_bindings import policy_parameters
from app.template_analysis.visual_form_answer import INSTRUCTIONS as FORM_INSTRUCTIONS
from app.template_analysis.visual_form_answer import POLICY as FORM_POLICY
from app.template_analysis.visual_form_answer import FormVisualAnswer
from app.template_analysis.visual_grid_answer import INSTRUCTIONS as GRID_INSTRUCTIONS
from app.template_analysis.visual_grid_answer import POLICY as GRID_POLICY
from app.template_analysis.visual_grid_answer import GridVisualAnswer
from app.template_analysis.visual_input import VisualEvidence
from app.template_analysis.visual_isolation import POLICY as ACCEPTANCE_POLICY
from app.template_analysis.visual_pack import POLICY as PACK_POLICY
from app.template_analysis.visual_pack import build_model_pack

INSTRUCTIONS = FORM_INSTRUCTIONS
STRUCTURE_INSTRUCTIONS = (
    FORM_INSTRUCTIONS
    + "\n"
    + """
回答はtemplate-analysis.structure.v1。catalogDigestは元の候補集合へ束縛する。
geometryとsemanticsを一つの回答で返す。全ページを確認し、枠がない記入位置も検討する。
geometryEditableにある既存c参照だけ変更可能。追加先はgeometryBounds内。
geometryはadd/resize/split/merge/remove_duplicate。座標は元画像上の整数。
新規枠には重複しないn参照を付ける。source、正規ID、保存先は指定しない。
splitの子は元枠の中で互いに重ねない。mergeは隣接矩形だけで、間の別セルを巻き込まない。
remove_duplicateは残す枠survivorsを指定し、元枠の95%以上を覆う重複外枠に限定する。
既存の手動枠・設定済みの欄は保持する。変更しない場合はgeometry=[]。
semanticsはreadings/groups/headings/annotations/tables/unresolvedを持つ。
新規n参照も意味付けの対象にできる。削除・変形した端点の古いb候補は使えない。
変更又は新規の枠との接続はbinding={kind:derived_link,input,labels,axis}で指定する。
両端が未変更なら元のlinkedのb候補を使う。derived_linkも同じ幾何候補検査を通る。
見出しなしは同じ根拠を要求する。矩形と依存する意味付けは一体で反映される。
""".strip()
)


def contract() -> dict[str, str]:
    return {
        "policy": "template-analysis.visual-runner.v9",
        "regionPolicy": REGION_POLICY,
        "regionScopePolicy": "exact-planned-bounds.v1",
        "regionPackPolicy": REGION_PACK_POLICY,
        "regionSchemaDigest": digest(RegionAnswer.model_json_schema(by_alias=True)),
        "regionSchemaPolicy": REGION_SCHEMA_POLICY,
        "regionSchemaParametersDigest": digest(region_schema_parameters()),
        "regionInstructionsDigest": digest(REGION_INSTRUCTIONS),
        "regionReasoningEffort": "none",
        "inputPolicy": PACK_POLICY,
        "answerAcceptancePolicy": ACCEPTANCE_POLICY,
        "model": OpenAIAnalysisProvider.model,
        "instructionsDigest": digest(FORM_INSTRUCTIONS),
        "schemaDigest": digest(FormVisualAnswer.model_json_schema(by_alias=True)),
        "formPolicy": FORM_POLICY,
        "bindingPolicy": BINDING_POLICY,
        "bindingResolutionPolicy": RESOLUTION_POLICY,
        "bindingParametersDigest": digest(policy_parameters()),
        "gridPolicy": GRID_POLICY,
        "gridInstructionsDigest": digest(GRID_INSTRUCTIONS),
        "gridSchemaDigest": digest(GridVisualAnswer.model_json_schema(by_alias=True)),
        "formReasoningEffort": "low",
        "gridReasoningEffort": "none",
        "structurePolicy": STRUCTURE_POLICY,
        "structureInstructionsDigest": digest(STRUCTURE_INSTRUCTIONS),
        "structureSchemaDigest": digest(StructureAnswer.model_json_schema(by_alias=True)),
        "draftPolicy": DRAFT_POLICY,
        "draftInstructionsDigest": digest(DRAFT_INSTRUCTIONS),
        "draftSchemaDigest": digest(DraftVisualAnswer.model_json_schema(by_alias=True)),
        "draftReasoningEffort": DRAFT_REASONING_EFFORT,
    }


def request_body(
    evidence: VisualEvidence, *, metrics: dict[str, Any] | None = None
) -> dict[str, Any]:
    start = time.perf_counter()
    meta = json.loads(evidence.metadata_json)
    is_region = bool(meta.get("regionPolicy")) and meta.get("regionKind") == "region"
    pack = region_pack(evidence) if is_region else build_model_pack(evidence)
    packed_at = time.perf_counter()
    is_grid = pack.grid is not None
    is_draft = bool(meta.get("draft")) and not is_region and not is_grid
    is_structure = not is_grid and not is_draft and meta.get("structure", False)
    schema_model = (
        RegionAnswer
        if is_region
        else DraftVisualAnswer
        if is_draft
        else GridVisualAnswer
        if is_grid
        else StructureAnswer
        if is_structure
        else FormVisualAnswer
    )

    def image(content: bytes, media: str, detail: str) -> dict[str, str]:
        return {
            "type": "input_image",
            "detail": detail,
            "image_url": f"data:{media};base64," + base64.b64encode(content).decode(),
        }

    # Capture admits these three image formats before this boundary.
    media = (
        "image/png"
        if evidence.full_image.startswith(b"\x89PNG")
        else "image/jpeg"
        if evidence.full_image.startswith(b"\xff\xd8")
        else "image/webp"
    )
    schema = region_schema(meta) if is_region else schema_model.model_json_schema(by_alias=True)
    body = {
        "model": OpenAIAnalysisProvider.model,
        "instructions": REGION_INSTRUCTIONS
        if is_region
        else DRAFT_INSTRUCTIONS
        if is_draft
        else GRID_INSTRUCTIONS
        if is_grid
        else STRUCTURE_INSTRUCTIONS
        if is_structure
        else FORM_INSTRUCTIONS,
        "input": [
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": pack.metadata_json},
                    image(evidence.full_image, media, "low"),
                    image(evidence.crop_png, "image/png", "high"),
                    *(image(sheet, "image/png", "high") for sheet in pack.sheets),
                ],
            }
        ],
        "reasoning": {
            "effort": DRAFT_REASONING_EFFORT
            if is_draft
            else "none"
            if is_grid or is_region
            else "low"
        },
        "store": False,
        "max_output_tokens": DRAFT_MAX_OUTPUT_TOKENS if is_draft else 8192 if is_region else 24576,
        "stream": True,
        "text": {
            "format": {
                "type": "json_schema",
                "name": "template_region"
                if is_region
                else "template_draft"
                if is_draft
                else "template_visual_grid"
                if is_grid
                else "template_structure"
                if is_structure
                else "template_visual_form",
                "strict": True,
                "schema": schema,
            }
        },
    }
    if metrics is not None:
        metrics.update(
            modelPackMs=round((packed_at - start) * 1000, 3),
            requestBodyAssemblyMs=round((time.perf_counter() - packed_at) * 1000, 3),
            metadataBytes=len(pack.metadata_json.encode("utf-8")),
            imageCount=2 + len(pack.sheets),
        )
        if is_region:
            metrics.update(
                regionSchemaDigest=digest(schema),
                regionScopeDigest=digest(
                    [
                        REGION_SCHEMA_POLICY,
                        sorted(meta["writeRefs"]),
                        sorted(set(meta["geometryEditable"]) & set(meta["writeRefs"])),
                        sorted(c["ref"] for c in meta["cells"]),
                        meta["geometryBounds"],
                    ]
                ),
            )
    return body


class VisualAnalysisProvider(OpenAIAnalysisProvider):
    async def analyze_visual(
        self,
        evidence: VisualEvidence,
        reserved: Callable[[str], Awaitable[None]],
        metrics: dict[str, Any],
    ) -> dict[str, Any]:
        self.check_available()
        start = time.perf_counter()
        metrics.update(model=self.model, firstEventMs=None, unitId=evidence.unit_id)
        try:
            body = request_body(evidence, metrics=metrics)
        except ValueError as error:
            if str(error).startswith("REGION_SCHEMA_"):
                raise reject(
                    str(error),
                    "解析対象に合う回答形式を作成できません。",
                    422,
                ) from error
            if str(error) == "VISUAL_BINDING_LIMIT":
                raise reject(
                    "VISUAL_BINDING_LIMIT",
                    "接続候補が解析上限を超えています。",
                    422,
                ) from error
            raise
        metrics["requestDigest"] = digest(body)
        metrics["requestPreparationMs"] = round((time.perf_counter() - start) * 1000, 3)
        try:
            result = await self.post(
                "responses",
                body,
                reserved,
                metrics,
                start,
                # Stop the stream just inside the run's own bound so the partial answer is kept.
                seconds=DRAFT_TIMEOUT_SECONDS - 2
                if body["text"]["format"]["name"] == "template_draft"
                else 60,
            )
            metrics["usage"] = result.get("usage")
            if result.get("model") != self.model:
                raise reject("PROVIDER_MODEL_MISMATCH", "解析モデルが指定と一致しません。", 422)
            if result.get("status") != "completed":
                code = (
                    "PROVIDER_OUTPUT_LIMIT"
                    if (result.get("incomplete_details") or {}).get("reason") == "max_output_tokens"
                    else "PROVIDER_INCOMPLETE"
                )
                raise reject(code, "AI案全体を取得できませんでした。", 422)
            content = [
                c
                for item in result.get("output", [])
                if item.get("type") == "message"
                for c in item.get("content", [])
            ]
            if any(c.get("type") == "refusal" for c in content):
                raise reject("PROVIDER_REFUSED", "AIが解析を実行できませんでした。", 422)
            texts = [c["text"] for c in content if c.get("type") == "output_text"]
            if len(texts) != 1:
                raise reject("PROVIDER_INCOMPLETE", "AI案の完成出力がありません。", 422)
            proposal = json.loads(texts[0])
            if body["text"]["format"]["name"] == "template_region":
                Draft202012Validator(body["text"]["format"]["schema"]).validate(proposal)
                return RegionAnswer.model_validate(proposal).model_dump(mode="json", by_alias=True)
            if body["text"]["format"]["name"] == "template_draft":
                return DraftVisualAnswer.model_validate(proposal).as_form()
            if body["text"]["format"]["name"] == "template_visual_grid":
                return GridVisualAnswer.model_validate(proposal).canonical()
            if body["text"]["format"]["name"] == "template_structure":
                return StructureAnswer.model_validate(proposal).model_dump(
                    mode="json", by_alias=True
                )
            return FormVisualAnswer.model_validate(proposal).model_dump(mode="json", by_alias=True)
        except (ValueError, KeyError, TypeError, SchemaValidationError) as error:
            raise reject("PROPOSAL_INVALID", "AI案の形式が不正です。", 422) from error
        finally:
            metrics["providerMs"] = round((time.perf_counter() - start) * 1000, 3)
