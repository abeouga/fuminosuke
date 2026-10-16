"""Bounded text/complete-STT controller. It resolves one command for the shared queue.

Resolution is not approval and is not a successful save. The client immediately
journals and submits the frozen command, then presents only the durable receipt.
No raw transcript, model continuation, or clarification is written to storage.
"""

from __future__ import annotations

import asyncio
import copy
import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, Protocol
from uuid import uuid4

from pydantic import ValidationError

from app.assistant.conversation_contracts import (
    BatchCommand,
    ConversationStart,
    ResolveTurn,
    StartedTurn,
    TurnResolution,
    UndoCommand,
)
from app.assistant.conversation_provider import ModelReply
from app.assistant.spoken_numbers import normalize_values
from app.assistant.target_resolution import TargetResolver, normalized
from app.core.errors import AppError
from app.editing.contracts import Batch, EmptyArgs, ReceiptArgs, TargetSearch, ToolBatch, Undo
from app.editing.errors import reject
from app.editing.models import EditingTurn
from app.editing.record_cells import cell_label
from app.editing.schemas import tool_definitions
from app.editing.service import EditingService


class Provider(Protocol):
    async def complete(
        self, items: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> ModelReply: ...


@dataclass
class MemoryTurn:
    session_id: str
    binding: tuple[Any, ...]
    expires: float
    digest: str | None = None
    resolving: bool = False
    result: TurnResolution | None = None
    instruction: str | None = None
    target_confirmation: str | None = None
    missing: str | None = None
    clarifications: list[tuple[str, str]] = field(default_factory=list)


def context_binding(context: dict[str, Any]) -> tuple[Any, ...]:
    return tuple(
        context[k]
        for k in (
            "contextRevision",
            "resourceRevision",
            "localEditEpoch",
            "selectionEpoch",
            "catalogRevision",
        )
    )


class ConversationController:
    max_model_requests = 4
    max_read_calls = 6
    memory_seconds = 60
    max_memory_turns = 128

    def __init__(self, editing: EditingService, provider: Provider) -> None:
        self.editing, self.provider = editing, provider
        self.turns: dict[str, MemoryTurn] = {}

    def _prune(self) -> None:
        now = time.monotonic()
        for key in list(self.turns):
            if self.turns[key].expires <= now:
                self.turns.pop(key, None)

    def start(self, session_id: str, token: str, body: ConversationStart) -> StartedTurn:
        self._prune()
        if len(self.turns) >= self.max_memory_turns:
            raise reject(
                "VOICE_CAPACITY_REACHED", "処理中の発話が多いため、少し待ってください。", 429
            )
        turn_id = self.editing.begin_turn(
            session_id, token, expected=body.model_dump(by_alias=True)
        )
        context = self.editing.turn_context(session_id, token, turn_id)
        with self.editing.database.session() as session:
            row = session.get(EditingTurn, turn_id)
            assert row is not None
            sequence = row.sequence
        self.turns[turn_id] = MemoryTurn(
            session_id, context_binding(context), time.monotonic() + self.memory_seconds
        )
        # No stale conversation is retained merely because the client goes idle.
        asyncio.get_running_loop().call_later(self.memory_seconds, self.turns.pop, turn_id, None)
        return StartedTurn(turn_id=turn_id, sequence=sequence, context=context)

    def cancel(self, session_id: str, token: str, turn_id: str) -> dict[str, Any]:
        result = self.editing.turn_status(session_id, token, turn_id, cancel=True)
        self.turns.pop(turn_id, None)
        return result

    def clear(self, session_id: str) -> None:
        for key in list(self.turns):
            if self.turns[key].session_id == session_id:
                self.turns.pop(key, None)

    def _targets(self, session_id: str, token: str) -> list[dict[str, Any]]:
        with self.editing.database.session() as session:
            owner = self.editing._owner(session, session_id, token)
            return [
                t.public() for t in self.editing.catalogs.build(session, owner).targets.values()
            ]

    async def resolve(
        self, session_id: str, token: str, turn_id: str, body: ResolveTurn
    ) -> TurnResolution:
        # Owner verification also applies to cached results and duplicate requests.
        status = self.editing.turn_status(session_id, token, turn_id)
        self._prune()
        memory = self.turns.get(turn_id)
        if memory is None or memory.session_id != session_id:
            raise reject("TURN_EXPIRED", "発話の解析情報が失効しました。再指示してください。")
        digest = self.editing.receipts.fingerprint(body.model_dump(mode="json", by_alias=True))
        if memory.digest is not None and memory.digest != digest:
            raise reject("TURN_INPUT_CONFLICT", "同じ発話IDを別の文章へ変更できません。")
        if memory.result is not None:
            if memory.result.state == "READY" and status["state"] not in {"READY", "CONSUMED"}:
                raise reject("TURN_CANCELLED", "解決済みの発話は中止されています。")
            return memory.result
        if memory.resolving:
            raise reject(
                "TURN_RESOLVING", "この発話は既に解析中です。新しい要求へ置き換えないでください。"
            )
        memory.digest, memory.resolving = digest, True
        try:
            context = self.editing.turn_context(session_id, token, turn_id)
            instruction = body.text
            confirmed_target = None
            clarification = None
            target_answer = None
            if body.reply_to is not None:
                pending = self.turns.get(body.reply_to)
                if (
                    pending is None
                    or pending.session_id != session_id
                    or pending.binding != context_binding(context)
                    or pending.result is None
                    or pending.result.state != "CLARIFYING"
                    or pending.instruction is None
                ):
                    raise reject(
                        "CLARIFICATION_EXPIRED",
                        "質問の対象状態が変わりました。指示全体を話してください。",
                    )
                clarification = {
                    "missing": pending.missing,
                    "pendingInstruction": pending.instruction,
                    "answer": body.text,
                }
                memory.clarifications = [
                    *pending.clarifications, (pending.missing or "instruction", body.text),
                ]
                if pending.missing == "target":
                    target_answer = body.text
                instruction = pending.instruction + "\n不足情報への回答: " + body.text
                answer = re.sub(r"[\s、。!?」「]", "", normalized(body.text))
                if pending.target_confirmation and answer in {
                    "はい", "うん", "そう", "そうです", "そうだよ", "それ", "それです",
                    "それそれ", "その欄", "その欄です", "それで合ってる", "その欄で合ってる",
                    "それで合っています", "その欄で合っています",
                }:
                    confirmed_target = pending.target_confirmation
                    candidate = pending.result.candidates[0]
                    instruction += "\n質問した対象「" + candidate.name + "」への肯定回答。"
                if len(instruction) > 4000:
                    raise reject(
                        "VOICE_INSTRUCTION_TOO_LONG", "指示全体を短く言い直してください。", 422
                    )
                self.turns.pop(body.reply_to, None)
            targets = self._targets(session_id, token)
            resolver = TargetResolver(
                targets, context, instruction, target_answer=target_answer,
                clarifications=memory.clarifications,
            )
            if confirmed_target:
                resolver.seen.add(confirmed_target)
                resolver.resolved.add(confirmed_target)
            value = re.sub(r"[\s、。!?~ー「」]", "", normalized(body.text))
            if not confirmed_target and value in {
                "はい",
                "うん",
                "ok",
                "おっけ",
                "オッケ",
                "承認する",
                "それでよろしく",
                "よろしく",
                "ありがとう",
                "変更を適用",
                "おっけじゃあそれでよろしく",
            }:
                result = self._dialogue(turn_id, "instruction", [], no_action=True)
            elif (
                re.search(
                    r"(?:^|[、。\s])(?:ここ|そこ|これ|それ)(?:を|の|は)|(?:この|その)欄",
                    instruction,
                )
                and not context["selectedHandles"]
                and not context.get("recentEdit")
                and not body.reply_to
            ):
                # A name appearing as the new value does not bind "here".
                # Require a selected/recent target or an explicit clarification.
                result = self._dialogue(turn_id, "target", [])
            else:
                result = await self._loop(
                    session_id, token, turn_id, memory, context, resolver,
                    instruction, clarification,
                )
            if result.state == "READY":
                assert result.command is not None
                self.editing.authorize_turn(session_id, token, result.command.body)
            else:
                self.editing.turn_context(session_id, token, turn_id)
                self.editing.turn_status(session_id, token, turn_id, cancel=True)
            memory.result = result
            memory.instruction = instruction if result.state == "CLARIFYING" else None
            if (
                result.state == "CLARIFYING" and len(result.candidates) == 1
                and "の欄ですか" in result.message
            ):
                memory.target_confirmation = result.candidates[0].handle
            return result
        except BaseException:
            self.editing.turn_status(session_id, token, turn_id, cancel=True)
            self.turns.pop(turn_id, None)
            raise
        finally:
            memory.resolving = False

    def _dialogue(
        self, turn_id: str, missing: str, candidates: list[dict[str, Any]],
        *, no_action: bool = False, message_override: str | None = None,
    ) -> TurnResolution:
        self.turns[turn_id].missing = None if no_action else missing
        if no_action:
            message = "変更指示として受け付けていません。変更する欄と内容を指定してください。"
        else:
            message = {
                "target": "どの欄ですか。欄名・位置・見出し・行を指定してください。",
                "value": "どの内容に変更しますか。変更後の値を指定してください。",
                "structure": "どの項目と欄を対応付けますか。不足している接続先を指定してください。",
                "instruction": "変更する欄と内容を指定してください。",
            }[missing]
            if len(candidates) > 1:
                labels = "、".join(dict.fromkeys(cell_label(candidate) for candidate in candidates))
                message = f"候補は「{labels}」です。欄名・位置・見出し・行を指定してください。"
            elif missing == "target" and len(candidates) == 1:
                message = (f"「{cell_label(candidates[0])}」の欄ですか。"
                           "違う場合は欄名や位置を指定してください。")
            if message_override:
                message = message_override
        return TurnResolution(
            turn_id=turn_id,
            state="NO_ACTION" if no_action else "CLARIFYING",
            message=message,
            candidates=candidates,
        )

    def _target_dialogue(
        self, turn_id: str, resolver: TargetResolver, candidates: list[dict[str, Any]],
    ) -> TurnResolution:
        return self._dialogue(
            turn_id, "target", candidates,
            message_override=resolver.records.message(candidates),
        )

    async def _loop(
        self,
        session_id: str,
        token: str,
        turn_id: str,
        memory: MemoryTurn,
        context: dict[str, Any],
        resolver: TargetResolver,
        instruction: str,
        clarification: dict[str, Any] | None,
    ) -> TurnResolution:
        # Exclude owner/view identifiers; neither authority nor raw graph enters the model.
        public_context = {
            k: v for k, v in context.items() if k not in {"viewInstanceId", "resource"}
        }
        items: list[dict[str, Any]] = [
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "context": public_context,
                        "instruction": instruction,
                        "clarification": clarification,
                    },
                    ensure_ascii=False,
                ),
            }
        ]
        tools = tool_definitions(
            capabilities=tuple(context["capabilities"]),
            paged=any(t["pageId"] is not None for t in resolver.targets.values()),
        )
        allowed_tools = {tool["name"] for tool in tools}
        reads = 0
        for _ in range(self.max_model_requests):
            self.editing.turn_context(session_id, token, turn_id)
            remaining = memory.expires - time.monotonic()
            if remaining <= 0:
                raise reject("TURN_EXPIRED", "発話が期限切れです。再指示してください。")
            try:
                async with asyncio.timeout(remaining):
                    reply = await self.provider.complete(copy.deepcopy(items), tools)
            except TimeoutError as error:
                raise reject(
                    "TURN_EXPIRED", "指示の解析が期限内に終わりませんでした。", 408
                ) from error
            self.editing.turn_context(session_id, token, turn_id)
            if len(reply.calls) > 1:
                raise reject(
                    "VOICE_MULTIPLE_TOOL_CALLS",
                    "一度に複数の実行応答が返されたため保存していません。",
                    422,
                )
            if not reply.calls:
                if reply.dialogue is None:
                    raise reject("VOICE_RESPONSE_INVALID", "指示の解析形式が不正です。", 422)
                candidates = []
                for handle in reply.dialogue.candidate_handles:
                    if handle not in resolver.seen or handle not in resolver.targets:
                        raise reject("TARGET_STALE", "質問の対象が現在の候補に属していません。")
                    candidates.append(resolver.targets[handle])
                if reply.dialogue.missing == "target" and reply.dialogue.kind != "no_action":
                    return self._target_dialogue(turn_id, resolver, candidates)
                return self._dialogue(
                    turn_id,
                    reply.dialogue.missing,
                    candidates,
                    no_action=reply.dialogue.kind == "no_action",
                )
            call = reply.calls[0]
            if call.name not in allowed_tools:
                raise reject(
                    "CAPABILITY_UNAVAILABLE", "公開されていない操作が要求されました。", 422
                )
            try:
                arguments = json.loads(call.arguments)
                envelope = {
                    "contractVersion": "editing.v1",
                    "requestId": uuid4().hex,
                    "expectedRevision": context["resourceRevision"],
                    "contextRevision": context["contextRevision"],
                    "localEditEpoch": context["localEditEpoch"],
                    "turnId": turn_id,
                    "origin": "local_voice" if context["mode"] == "local" else "continuous_voice",
                }
                if call.name == "apply_edit_batch":
                    try:
                        proposed = normalize_values(
                            ToolBatch.model_validate(arguments), instruction, resolver.value_source
                        )
                        resolver.check(proposed.model_dump(by_alias=True))
                    except AppError as error:
                        if error.code == "VALUE_AMBIGUOUS":
                            if all(s.value_known for s in resolver.records.selections):
                                # The source already supplies the values. Let the
                                # bounded model loop repair its mapping instead
                                # of asking the user to repeat known information.
                                items.extend(reply.items)
                                items.append({
                                    "type": "function_call_output", "call_id": call.call_id,
                                    "output": json.dumps({
                                        "error": "VALUE_BINDING_MISMATCH",
                                        "saved": False,
                                        "instructions": [
                                            {**s.search(resolver.records.recent).model_dump(
                                                by_alias=True, exclude_none=True,
                                            ), "requestedValue": s.value}
                                            for s in resolver.records.selections
                                        ],
                                    }, ensure_ascii=False),
                                })
                                continue
                            return self._dialogue(turn_id, "value", [])
                        if error.code != "TARGET_AMBIGUOUS":
                            raise
                        return self._target_dialogue(
                            turn_id,
                            resolver,
                            [
                                resolver.targets[h]
                                for h in error.metadata.get("candidateHandles", [])
                            ],
                        )
                    body = Batch.model_validate({**envelope, **proposed.model_dump(by_alias=True)})
                    return TurnResolution(
                        turn_id=turn_id,
                        state="READY",
                        message="",
                        command=BatchCommand(kind="batch", body=body),
                    )
                if call.name == "undo_edit":
                    EmptyArgs.model_validate(arguments)
                    previous = next((r for r in context["history"] if r["canUndo"]), None)
                    if previous is None:
                        raise reject("UNDO_UNAVAILABLE", "現在の直前操作を取り消せません。")
                    body_undo = Undo.model_validate(
                        {**envelope, "operationId": previous["operationId"]}
                    )
                    return TurnResolution(
                        turn_id=turn_id,
                        state="READY",
                        message="",
                        command=UndoCommand(kind="undo", body=body_undo),
                    )
                reads += 1
                if reads > self.max_read_calls:
                    break
                if call.name == "get_editing_context":
                    EmptyArgs.model_validate(arguments)
                    output = public_context
                elif call.name == "find_targets":
                    query = TargetSearch.model_validate(arguments)
                    try:
                        query = resolver.prepare_search(query)
                    except AppError as error:
                        if error.code != "TARGET_AMBIGUOUS":
                            raise
                        return self._target_dialogue(turn_id, resolver, [])
                    output = self.editing.find(session_id, token, query)
                    exact = [target for target in output["targets"] if target.get("score") == 100]
                    if (
                        len(exact) == 1 and not query.cursor and query.query
                        and resolver.names_query(query.query)
                    ):
                        # Exact matches are ordered before every substring hit.
                        # A single exact alias is not ambiguous merely because
                        # another name contains that alias as a substring.
                        output = {**output, "targets": exact, "total": 1, "nextCursor": None}
                    if output["total"] == 0 and query.query:
                        nearby = resolver.nearby(query)
                        if (
                            len(nearby) == 1 and not query.cursor
                            and resolver.names_query(query.query)
                        ):
                            # Correct only the target search result. The original
                            # instruction and all mutation values remain intact.
                            output = {**output, "targets": nearby, "total": 1, "nextCursor": None}
                        else:
                            return self._target_dialogue(turn_id, resolver, nearby)
                    if output["total"] == 0 and resolver.records.selections:
                        return self._target_dialogue(turn_id, resolver, [])
                    resolver.observe(output, query)
                else:
                    receipt = ReceiptArgs.model_validate(arguments)
                    # Only this owner's receipts are accessible, read-only. No
                    # receipt result is adopted as a new current execution context.
                    output = self.editing.receipt(session_id, token, receipt.request_id)
                items.extend(reply.items)
                items.append(
                    {
                        "type": "function_call_output",
                        "call_id": call.call_id,
                        "output": json.dumps(output, ensure_ascii=False),
                    }
                )
            except (ValidationError, json.JSONDecodeError) as error:
                raise reject(
                    "VOICE_ARGUMENT_INVALID", "操作の引数が不正なため保存していません。", 422
                ) from error
        raise reject(
            "VOICE_RESOLUTION_LIMIT",
            "指示を短く分けて話してください。解析の上限に達しました。",
            422,
        )
