"""Printed choices of a selection box, read by the model once per look-alike group.

A box set to 「印刷された選択肢を読む」 has its printed choices read from the template
image (GPT-6 Luna, one Responses request per group of boxes that look the same), so the
person checks the result instead of typing it. Nothing is saved here: the screen applies
the suggestion to its draft. Only the template image is sent, never a filled document.
"""

# ruff: noqa: RUF001  (the model instruction uses Japanese fullwidth punctuation)

from __future__ import annotations

import asyncio
import base64
import json
from typing import Any

import cv2
import httpx
import numpy as np
from numpy.typing import NDArray
from pydantic import BaseModel, Field, ValidationError

from app.ai.api_budget import ApiBudget
from app.core.config import Settings
from app.core.errors import AppError
from app.layout_analysis.models import LayoutCell, LayoutDefinition

MODEL = "gpt-6-luna"
PURPOSE = "template.mark_options"
TIMEOUT_SECONDS = 30.0
# Printed choices that look this alike (cosine of the darkness of the box interior at
# 32x16) carry the same choices; a filled box differs from its blank twin far more.
SIMILARITY = 0.85
SIZE_TOLERANCE = 0.25
MAX_CELLS = 50
MAX_OPTIONS = 20
INSTRUCTIONS = (
    "画像は帳票の1つの欄です。この欄に印刷された選択肢（丸で囲む・チェックする・"
    "線を引くなどして選ぶ項目）を、左上から読む順に列挙してください。"
    "印刷された文字だけを返し、手書きの印や文字（丸、チェック、記入）は無視します。"
    "項目名・単位・注記・罫線など、選ぶ対象でない印字は含めません。"
    "各選択肢の位置は画像の幅・高さに対する割合（0〜1）で、left<right、top<bottom。"
    "選択肢が1つもなければ空の配列を返します。"
)


class SuggestedOption(BaseModel):
    label: str = Field(min_length=1, max_length=100)
    left: float = Field(ge=0, le=1)
    top: float = Field(ge=0, le=1)
    right: float = Field(ge=0, le=1)
    bottom: float = Field(ge=0, le=1)


class Answer(BaseModel):
    options: list[SuggestedOption] = Field(max_length=MAX_OPTIONS)


ANSWER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["options"],
    "properties": {
        "options": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["label", "left", "top", "right", "bottom"],
                "properties": {
                    "label": {"type": "string"},
                    "left": {"type": "number"},
                    "top": {"type": "number"},
                    "right": {"type": "number"},
                    "bottom": {"type": "number"},
                },
            },
        }
    },
}


def refused(code: str, detail: str, status: int) -> AppError:
    return AppError(status=status, code=code, title=code, detail=detail)


def is_choice_box(cell: LayoutCell) -> bool:
    """A box whose printed choices the model may read (not a symbol or checklist box)."""
    return cell.role == "mark" and all(
        mark_set.recognition_mode in {"automatic", "manual"} for mark_set in cell.mark_sets
    )


def darkness(gray: NDArray[np.uint8], cell: LayoutCell) -> NDArray[np.float64]:
    inset = max(2, round(0.06 * min(cell.width, cell.height)))
    x0, y0 = round(cell.x) + inset, round(cell.y) + inset
    x1, y1 = round(cell.x + cell.width) - inset, round(cell.y + cell.height) - inset
    if x1 - x0 < 2 or y1 - y0 < 2:
        return np.zeros(32 * 16)
    small = cv2.resize(gray[y0:y1, x0:x1], (32, 16), interpolation=cv2.INTER_AREA)
    return (255.0 - small.astype(np.float64)).ravel()


def alike(a: LayoutCell, b: LayoutCell, va: NDArray[np.float64], vb: NDArray[np.float64]) -> bool:
    if a.width <= 0 or b.width <= 0 or a.height <= 0 or b.height <= 0:
        return False
    if abs(a.width - b.width) > SIZE_TOLERANCE * max(a.width, b.width) or abs(
        a.height - b.height
    ) > SIZE_TOLERANCE * max(a.height, b.height):
        return False
    na, nb = float(np.linalg.norm(va)), float(np.linalg.norm(vb))
    if not na or not nb:
        return na == nb
    return float(np.dot(va, vb)) / (na * nb) >= SIMILARITY


def crop_png(rgb: NDArray[np.uint8], cell: LayoutCell) -> bytes:
    height, width = rgb.shape[:2]
    margin = 2
    x0, y0 = max(0, round(cell.x) - margin), max(0, round(cell.y) - margin)
    x1 = min(width, round(cell.x + cell.width) + margin)
    y1 = min(height, round(cell.y + cell.height) + margin)
    if x1 - x0 < 2 or y1 - y0 < 2:
        raise refused("INVALID_LAYOUT_DIMENSIONS", "選択欄が画像の外にあります。", 422)
    crop = rgb[y0:y1, x0:x1]
    # Small boxes are enlarged so the printed text stays legible to the model.
    scale = max(1.0, min(4.0, 160.0 / max(1, crop.shape[0])))
    if scale > 1.0:
        crop = np.asarray(
            cv2.resize(crop, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC),
            dtype=np.uint8,
        )
    ok, encoded = cv2.imencode(".png", cv2.cvtColor(crop, cv2.COLOR_RGB2BGR))
    if not ok:
        raise refused("INVALID_TEMPLATE_IMAGE", "選択欄の画像を作成できませんでした。", 422)
    return bytes(encoded)


def to_cell_options(answer: Answer, cell: LayoutCell) -> list[dict[str, Any]]:
    """Fractions of the crop → whole-pixel rectangles inside the box, in reading order."""
    options: list[dict[str, Any]] = []
    seen: set[str] = set()
    for option in answer.options:
        label = option.label.strip()
        if not label or label in seen or option.right <= option.left or option.bottom <= option.top:
            continue
        seen.add(label)
        x = min(cell.width - 1, max(0, round(option.left * cell.width)))
        y = min(cell.height - 1, max(0, round(option.top * cell.height)))
        width = max(1, min(cell.width - x, round((option.right - option.left) * cell.width)))
        height = max(1, min(cell.height - y, round((option.bottom - option.top) * cell.height)))
        options.append({"label": label, "x": x, "y": y, "width": width, "height": height})
    return options


class MarkOptionSuggestionService:
    def __init__(self, settings: Settings, budget: ApiBudget) -> None:
        self._settings, self._budget = settings, budget

    def check_available(self) -> None:
        if not self._settings.ocr.openai_api_key:
            raise refused("PROVIDER_NOT_CONFIGURED", "選択肢を読むAIの接続設定がありません。", 503)
        if self._budget.status()["remaining"] < 1:
            raise refused("AI_API_BUDGET_EXHAUSTED", "外部AI APIの許可枠に達しました。", 429)

    @staticmethod
    def groups(
        gray: NDArray[np.uint8], layout: LayoutDefinition, cell_ids: list[str]
    ) -> list[tuple[list[LayoutCell], list[LayoutCell]]]:
        """(requested boxes, other look-alike choice boxes) per group of alike requested boxes."""
        by_id = {cell.id: cell for cell in layout.cells}
        requested = [by_id[i] for i in dict.fromkeys(cell_ids) if i in by_id]
        if len(requested) != len(set(cell_ids)):
            raise refused("INVALID_ARGUMENT", "選択欄が下書きにありません。", 422)
        if any(not is_choice_box(cell) for cell in requested):
            raise refused("INVALID_ARGUMENT", "印刷された選択肢を読む欄だけを選んでください。", 422)
        others = [
            cell
            for cell in layout.cells
            if is_choice_box(cell)
            and cell.id not in set(cell_ids)
            and not any(mark_set.options for mark_set in cell.mark_sets)
        ]
        vectors = {cell.id: darkness(gray, cell) for cell in [*requested, *others]}
        result: list[tuple[list[LayoutCell], list[LayoutCell]]] = []
        for cell in requested:
            group = next(
                (g for g in result if alike(g[0][0], cell, vectors[g[0][0].id], vectors[cell.id])),
                None,
            )
            if group is None:
                result.append(([cell], []))
            else:
                group[0].append(cell)
        for group in result:
            head = group[0][0]
            group[1].extend(
                other for other in others if alike(head, other, vectors[head.id], vectors[other.id])
            )
        return result

    async def read(self, png: bytes, cell: LayoutCell, hint: str = "") -> Answer:
        self.check_available()
        key = self._settings.ocr.openai_api_key
        assert key is not None
        name = (cell.field_label or cell.text or "").strip()
        body = {
            "model": MODEL,
            "instructions": INSTRUCTIONS,
            "input": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": (f"欄の名前: {name}" if name else "欄の名前: 不明")
                            + (
                                "\n利用者のヒント（選択肢の数・名前。画像の印字を優先し、"
                                f"画像に無い選択肢は作らない）: {hint}"
                                if hint
                                else ""
                            ),
                        },
                        {
                            "type": "input_image",
                            "detail": "high",
                            "image_url": "data:image/png;base64," + base64.b64encode(png).decode(),
                        },
                    ],
                }
            ],
            "reasoning": {"effort": "low"},
            "store": False,
            "max_output_tokens": 2048,
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "mark_options",
                    "strict": True,
                    "schema": ANSWER_SCHEMA,
                }
            },
        }
        try:
            # ApiBudget reserves before the actual send and never refunds failures.
            async with asyncio.timeout(TIMEOUT_SECONDS):
                async with self._budget.client(PURPOSE) as client:
                    response = await client.post(
                        "https://api.openai.com/v1/responses",
                        headers={"Authorization": "Bearer " + key.get_secret_value()},
                        json=body,
                        timeout=TIMEOUT_SECONDS,
                    )
            if not response.is_success:
                raise refused("PROVIDER_FAILED", "選択肢を読むAIへの要求が拒否されました。", 503)
            value = response.json()
        except (TimeoutError, httpx.TimeoutException) as error:
            raise refused(
                "PROVIDER_TIMEOUT", "選択肢の読み取りが時間内に終わりませんでした。", 504
            ) from error
        except httpx.HTTPError as error:
            raise refused(
                "PROVIDER_FAILED", "選択肢を読むAIに接続できませんでした。", 503
            ) from error
        except (ValueError, json.JSONDecodeError) as error:
            raise refused(
                "PROVIDER_RESPONSE_INVALID", "選択肢の読み取り結果の形式が不正です。", 422
            ) from error
        if not isinstance(value, dict) or value.get("status") != "completed":
            raise refused(
                "PROVIDER_INCOMPLETE", "選択肢の読み取り結果を取得できませんでした。", 422
            )
        content = [
            c
            for item in value.get("output", [])
            if item.get("type") == "message"
            for c in item.get("content", [])
        ]
        if any(c.get("type") == "refusal" for c in content):
            raise refused("PROVIDER_REFUSED", "AIが選択肢を読み取れませんでした。", 422)
        texts = [c["text"] for c in content if c.get("type") == "output_text"]
        if len(texts) != 1:
            raise refused("PROVIDER_INCOMPLETE", "選択肢の読み取り結果がありません。", 422)
        try:
            return Answer.model_validate(json.loads(texts[0]))
        except (ValueError, ValidationError) as error:
            raise refused(
                "PROVIDER_RESPONSE_INVALID", "選択肢の読み取り結果の形式が不正です。", 422
            ) from error

    async def suggest(
        self, rgb: NDArray[np.uint8], layout: LayoutDefinition, cell_ids: list[str], hint: str = ""
    ) -> dict[str, Any]:
        if not cell_ids or len(cell_ids) > MAX_CELLS:
            raise refused("INVALID_ARGUMENT", f"選択欄は1〜{MAX_CELLS}件で指定してください。", 422)
        gray = np.asarray(cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY), dtype=np.uint8)
        groups = self.groups(gray, layout, cell_ids)
        if self._budget.status()["remaining"] < len(groups):
            raise refused("AI_API_BUDGET_EXHAUSTED", "外部AI APIの許可枠が足りません。", 429)
        cells: list[dict[str, Any]] = []
        similar: list[dict[str, Any]] = []
        for requested, others in groups:
            head = requested[0]
            answer = await self.read(crop_png(rgb, head), head, hint)
            options = to_cell_options(answer, head)
            for cell in requested:
                cells.append({"cellId": cell.id, "options": scaled(options, head, cell)})
            if others and options:
                similar.append(
                    {
                        "cellId": head.id,
                        "cells": [
                            {"cellId": c.id, "options": scaled(options, head, c)} for c in others
                        ],
                    }
                )
        return {"cells": cells, "similar": similar}


def scaled(
    options: list[dict[str, Any]], source: LayoutCell, target: LayoutCell
) -> list[dict[str, Any]]:
    """The same choices placed in a box of a slightly different size."""
    if source.id == target.id or not source.width or not source.height:
        return [dict(option) for option in options]
    fx, fy = target.width / source.width, target.height / source.height
    result = []
    for option in options:
        x = min(target.width - 1, max(0, round(option["x"] * fx)))
        y = min(target.height - 1, max(0, round(option["y"] * fy)))
        result.append(
            {
                "label": option["label"],
                "x": x,
                "y": y,
                "width": max(1, min(target.width - x, round(option["width"] * fx))),
                "height": max(1, min(target.height - y, round(option["height"] * fy))),
            }
        )
    return result
