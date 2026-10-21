"""Mark semantics may reuse geometry but never invent option rectangles."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from app.editing.errors import reject
from app.editing.operation_contracts import MarkClear, MarkConfigure

if TYPE_CHECKING:
    from app.editing.semantic_commands import SemanticCommands


def node_id(*parts: str) -> str:
    return json.dumps(parts, ensure_ascii=False, separators=(",", ":"))


def owned(ctx: SemanticCommands, handle: str, kind: str, prefix: list[str]) -> list[str]:
    parts: list[str] = json.loads(ctx.ref(handle, kind))
    if parts[: len(prefix)] != prefix:
        raise reject("TARGET_OWNER_MISMATCH", "別の欄・選択肢群の対象を流用できません。")
    return parts


def edit_marks(ctx: SemanticCommands, op: MarkConfigure | MarkClear) -> None:
    from app.editing.commands import CommandCompiler, name

    cell = ctx.cell(op.cell, "cell")
    if (
        cell.get("virtual")
        or cell.get("compositeSubfields")
        or cell["role"] not in {"input", "mark"}
    ):
        raise reject("CAPABILITY_UNAVAILABLE", "通常の記入欄またはマーク欄を指定してください。")
    old_sets = {m["id"]: m for m in cell["markSets"]}
    relations = [r for r in ctx.layout["relations"] if r["inputCellId"] == cell["id"]]
    if isinstance(op, MarkClear):
        if len(old_sets) > 1:
            ctx.required_bindings.add(cell["id"])
        if len(old_sets) > 1 and relations:
            raise reject(
                "DEPENDENCY_CONFLICT", "複数の意味があるため接続解除と再割当を明示してください。"
            )
        if len(old_sets) == 1:
            path = next(iter(old_sets.values()))["targetPath"]
            if relations and any(r["targetPath"] != path for r in relations):
                raise reject("DEPENDENCY_CONFLICT", "既存接続とマークの意味が異なります。")
            if not relations and path:
                ctx.layout["relations"].append(
                    CommandCompiler._relation(cell["id"], path, [], "horizontal")
                )
        cell["role"], cell["markSets"] = "input", []
        return
    if cell.get("readingMode"):
        raise reject(
            "DEPENDENCY_CONFLICT", "番号の読取設定を解除してからマークを設定してください。"
        )
    result: list[dict[str, Any]] = []
    for spec in op.sets:
        if ctx.catalog.pages and spec.recognition_mode not in {"checklist", "symbol"}:
            raise reject(
                "CAPABILITY_UNAVAILABLE",
                "ページ別テンプレートのマークはチェック一覧と記号だけです。",
            )
        old = None
        if spec.set:
            parts = owned(ctx, spec.set, "mark_set", [cell["id"]])
            old = old_sets.get(parts[1])
            if old is None:
                raise reject("TARGET_STALE", "削除済みの選択肢群です。")
            set_id = old["id"]
        else:
            set_id = ctx.create(str(spec.local_ref), "mark_set")
            ctx.local[str(spec.local_ref)] = ctx.plan.created[str(spec.local_ref)] = (
                "mark_set",
                node_id(cell["id"], set_id),
            )
        entry = ctx.meaning(spec.meaning) if spec.meaning is not None else None
        options = []
        old_options = {o["id"]: o for o in old["options"]} if old else {}
        for option in spec.options:
            if option.option:
                parts = owned(ctx, option.option, "mark_option", [cell["id"], set_id])
                if parts[2] not in old_options:
                    raise reject("TARGET_STALE", "削除済みの選択肢です。")
                option_id = parts[2]
            else:
                option_id = ctx.create(str(option.local_ref), "mark_option")
                ctx.local[str(option.local_ref)] = ctx.plan.created[str(option.local_ref)] = (
                    "mark_option",
                    node_id(cell["id"], set_id, option_id),
                )
            whole = spec.recognition_mode in {"symbol", "checklist"}
            if whole:
                if option.region not in {None, "whole-cell"}:
                    raise reject(
                        "INVALID_ARGUMENT", "記号・チェック一覧は欄全体を使用します。", 422
                    )
                geometry = {"x": 0, "y": 0, "width": cell["width"], "height": cell["height"]}
            else:
                if option.region is None or option.region == "whole-cell":
                    raise reject("GEOMETRY_REQUIRED", "手動で指定済みの選択肢領域が必要です。", 422)
                parts = owned(ctx, option.region, "region", [cell["id"]])
                source = next(
                    (
                        o
                        for m in old_sets.values()
                        if m["id"] == parts[1]
                        for o in m["options"]
                        if o["id"] == parts[2]
                    ),
                    None,
                )
                if source is None:
                    raise reject("TARGET_STALE", "保存済みの選択肢領域を参照してください。")
                geometry = {k: source[k] for k in ("x", "y", "width", "height")}
            write_in = (
                old_options.get(option_id, {}).get("writeInTargetPath")
                if option.write_in_target_path is None
                else option.write_in_target_path
            )
            options.append(
                {
                    "id": option_id,
                    "label": name(option.label),
                    "value": name(option.value),
                    **geometry,
                    # A write-in link set by hand survives a spoken change of the same option.
                    **(
                        {"writeInTargetPath": write_in}
                        if write_in and spec.recognition_mode in {"checklist", "symbol"}
                        else {}
                    ),
                }
            )
        result.append(
            {
                "id": set_id,
                "targetPath": entry["targetPath"] if entry else "",
                "recognitionMode": spec.recognition_mode,
                "selectionMode": spec.selection_mode,
                "options": options,
            }
        )
    paths = [s["targetPath"] for s in result if s["targetPath"]]
    if len({s["id"] for s in result}) != len(result) or len(set(paths)) != len(paths):
        raise reject("INVALID_GRAPH", "選択肢群または保存先が重複しています。", 422)
    if any(r["targetPath"] not in {s["targetPath"] for s in result} for r in relations):
        raise reject("DEPENDENCY_CONFLICT", "既存の意味との接続を同じ操作群で解除してください。")
    cell["role"], cell["markSets"] = "mark", result
