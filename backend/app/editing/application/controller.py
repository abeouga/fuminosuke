"""Serial session commands; interpretation has no database write lock."""

from __future__ import annotations

import asyncio
import json
import time
from collections import deque
from typing import Any
from uuid import uuid4

from app.core.errors import AppError
from app.editing.application.ports import CommandRepository, InstructionResolver, MutationUnitOfWork
from app.editing.domain.command import BoundOperation, ResolvedInstruction, canonical, digest
from app.editing.domain.dialogue import PendingInstruction, known_slots, pending_cancellation
from app.editing.domain.fast_path import spoken_row
from app.editing.domain.planning import InvalidPlan
from app.editing.domain.planning_catalog import PlanningCatalog
from app.editing.errors import reject


class CommandController:
    def __init__(
        self,
        repository: CommandRepository,
        mutations: MutationUnitOfWork,
        resolver: InstructionResolver,
    ) -> None:
        self.repository, self.mutations, self.resolver = repository, mutations, resolver
        self.queues: dict[str, deque[tuple[str, dict[str, Any]]]] = {}
        self.tasks: dict[str, asyncio.Task[None]] = {}
        self.resolutions: dict[str, tuple[str, asyncio.Task[ResolvedInstruction], int]] = {}
        self.closed = False
        self.pending: dict[str, PendingInstruction] = {}
        self.inputs: dict[str, tuple[str, dict[str, Any] | None, str | None]] = {}
        self.admission_locks: dict[str, asyncio.Lock] = {}
        self.voice_input: Any = None

    async def submit(self, session_id: str, token: str, value: dict[str, Any]) -> dict[str, Any]:
        async with self.admission_locks.setdefault(session_id, asyncio.Lock()):
            return await self._submit(session_id, token, value)

    async def _submit(
        self,
        session_id: str,
        token: str,
        value: dict[str, Any],
        continuation: tuple[str, str] | None = None,
        rebinding: PendingInstruction | None = None,
        fingerprint: str | None = None,
    ) -> dict[str, Any]:
        if self.closed:
            raise reject("COMMAND_INTERRUPTED", "編集処理が停止しています。", 503)
        command = value.get("command", {})
        prepared_input = None
        provider_free = False
        if command.get("kind") == "instruction":
            # Durable replay precedes access to volatile transcripts/questions.
            try:
                await asyncio.to_thread(
                    self.repository.query, session_id, token, value["requestId"]
                )
            except AppError as error:
                if error.code != "COMMAND_NOT_FOUND":
                    raise
            else:
                return {
                    k: v
                    for k, v in (
                        await asyncio.to_thread(
                            self.repository.admit, session_id, token, value, fingerprint=fingerprint
                        )
                    ).items()
                    if k != "new"
                }
            source, reply = command["input"], command["replyTo"]
            choice = source.get("choiceId") if source["kind"] == "choice" else None
            if source["kind"] == "voice":
                if self.voice_input is None:
                    raise reject("VOICE_UNAVAILABLE", "音声取得が接続されていません。", 503)
                text = self.voice_input(session_id, value["binding"], source)
            else:
                text = source.get("text", "")
            pending = self.pending.get(session_id)
            dialogue = None
            if reply:
                if (
                    not pending
                    or not pending.question
                    or (
                        pending.question["questionId"] != reply["questionId"]
                        or pending.question["version"] != reply["version"]
                    )
                ):
                    raise reject("QUESTION_STALE", "質問と元の指示が失効しています。", 409)
                if min(pending.deadline, pending.question_deadline) <= time.monotonic():
                    self.pending.pop(session_id, None)
                    raise reject(
                        "QUESTION_EXPIRED",
                        "質問の期限が切れました。元の指示から再開してください。",
                        409,
                    )
                if pending.binding != value["binding"]:
                    raise reject("CONTEXT_STALE", "質問の対象世代が変わりました。", 409)
                replacement = pending_cancellation(text) if not choice else None
                if replacement is not None:
                    original_fingerprint = digest(value)
                    cancel = {
                        **value,
                        "requestId": value["requestId"] + "_cancel"
                        if replacement
                        else value["requestId"],
                        "afterCommandId": None,
                        "command": {"kind": "cancel", "commandId": pending.command_id},
                    }
                    cancelled = await self._submit(
                        session_id,
                        token,
                        cancel,
                        fingerprint=None if replacement else original_fingerprint,
                    )
                    if not replacement:
                        return cancelled
                    fresh = {
                        **value,
                        "afterCommandId": None,
                        "command": {
                            "kind": "instruction",
                            "input": {"kind": "text", "text": replacement},
                            "replyTo": None,
                        },
                    }
                    return await self._submit(
                        session_id, token, fresh, fingerprint=original_fingerprint
                    )
                if choice:
                    issued = next(
                        (v for v in pending.question["choices"] if v["choiceId"] == choice), None
                    )
                    if not issued:
                        raise reject(
                            "QUESTION_CHOICE_INVALID", "発行された候補を選択してください。", 422
                        )
                    text = issued["label"]
                if (
                    not choice
                    and pending.question["missingKind"] in {"target", "column"}
                    and len(pending.question["choices"]) == 1
                    and text.strip(" 。、!")
                    in {"はい", "そう", "それ", "それで", "それでよろしく", "よろしく", "そうです"}
                ):
                    choice = pending.question["choices"][0]["choiceId"]
                    text = pending.question["choices"][0]["label"]
                if not choice and pending.question["missingKind"] in {"row", "target", "column"}:
                    # 「3」/「3行目」 picks the one offered cell in that row, no model call.
                    row = spoken_row(text)
                    picked = [
                        v
                        for v in pending.question["choices"]
                        if row is not None and f"・{row}行目・" in v["label"]
                    ]
                    if len(picked) == 1:
                        choice, text = picked[0]["choiceId"], picked[0]["label"]
                try:
                    dialogue = pending.dialogue(text)
                except InvalidPlan as error:
                    raise reject(
                        "INSTRUCTION_TOO_LONG", "指示と質問回答の合計が上限を超えています。", 413
                    ) from error
                provider_free = pending.empty_acknowledgement(text)
                if choice:
                    context = await asyncio.to_thread(
                        self.repository.input_context, session_id, token
                    )
                    try:
                        provider_free = (
                            pending.deterministic(choice, PlanningCatalog(context)) is not None
                        )
                    except InvalidPlan:
                        provider_free = False
            elif choice:
                raise reject("QUESTION_STALE", "候補の回答には質問の指定が必要です。", 409)
            prepared_input = (text, dialogue, choice)
            if continuation:
                prepared_input = (continuation[1] + "\n" + text, None, None)
            if rebinding:
                prepared_input = (text, rebinding.dialogue(text), None)
        result = await asyncio.to_thread(
            self.repository.admit,
            session_id,
            token,
            value,
            continuation_of=rebinding.command_id
            if rebinding
            else continuation[0]
            if continuation
            else None,
            scope_rebinding=rebinding is not None,
            fingerprint=fingerprint,
            provider_free=provider_free,
        )
        if value.get("command", {}).get("kind") == "cancel":
            self.abort_resolution(session_id, value["command"]["commandId"])
            self.pending.pop(session_id, None)
        if result.pop("new"):
            command_id = result["commandId"]
            if prepared_input:
                self.inputs[command_id] = prepared_input
                if prepared_input[1] and session_id in self.pending:
                    self.pending[session_id].command_id = command_id
            self.mutations.register(command_id, value)
            self.queues.setdefault(session_id, deque()).append((command_id, value))
            if session_id not in self.tasks:
                self.tasks[session_id] = asyncio.create_task(self.run(session_id))
        return result

    def abort_resolution(
        self,
        session_id: str,
        command_id: str | None = None,
        *,
        if_context_revision: int | None = None,
    ) -> None:
        pending = self.pending.get(session_id)
        if pending and min(pending.deadline, pending.question_deadline) <= time.monotonic():
            self.pending.pop(session_id, None)
        current = self.resolutions.get(session_id)
        if current is not None and if_context_revision == current[2]:
            return
        if current is not None and (command_id is None or current[0] == command_id):
            current[1].cancel()
        pending = self.pending.get(session_id)
        if (
            pending
            and (command_id is None or command_id == pending.command_id)
            and (
                if_context_revision is None
                or if_context_revision != pending.binding["contextRevision"]
            )
        ):
            self.pending.pop(session_id, None)

    async def run(self, session_id: str) -> None:
        queue = self.queues[session_id]
        try:
            while queue:
                command_id, value = queue.popleft()
                command = value.get("command", {})
                fence, prepared = 1, None
                waiting_information = False
                try:
                    claimed = await asyncio.to_thread(self.repository.claim, command_id)
                    if claimed is None:
                        continue
                    fence = claimed["fence"]
                    command = value.get("command", {})
                    operations: tuple[BoundOperation, ...] = ()
                    resolution = None
                    if claimed["kind"] == "instruction":
                        text, dialogue, choice = self.inputs[command_id]
                        context = await asyncio.to_thread(
                            self.repository.context_for_command, command_id, fence
                        )
                        pending = self.pending.get(session_id)
                        if dialogue:
                            context["instructionDialogue"] = dialogue
                        direct = None
                        if choice and pending:
                            direct = pending.deterministic(choice, PlanningCatalog(context))
                        ambiguous_affirmation = bool(
                            dialogue and pending and pending.empty_acknowledgement(text)
                        )
                        task = asyncio.create_task(
                            self.unchanged()
                            if ambiguous_affirmation
                            else self.resolver.resolve(text, context)
                            if direct is None
                            else self.direct(direct)
                        )
                        self.resolutions[session_id] = (
                            command_id,
                            task,
                            context["scopeContext"]["contextRevision"],
                        )
                        try:
                            resolution = await asyncio.wait_for(task, timeout=30)
                        except asyncio.CancelledError:
                            parent = asyncio.current_task()
                            if parent is not None and parent.cancelling():
                                raise
                            # A scope change/cancel has already fenced the durable
                            # command. Release this queue immediately, not after a model timeout.
                            continue
                        finally:
                            self.resolutions.pop(session_id, None)
                        while await asyncio.to_thread(
                            self.repository.hold_status, command_id, fence
                        ):
                            await asyncio.sleep(0.025)
                        repeat_question = bool(dialogue and pending and resolution.no_action)
                        if repeat_question and pending and pending.question:
                            question = {
                                **pending.question,
                                "questionId": "q_" + uuid4().hex,
                                "version": pending.question["version"] + 1,
                            }
                            resolution = ResolvedInstruction(
                                question_json=canonical(question),
                                partial_plan_json=canonical(pending.partial or {}),
                            )
                        if resolution.question_json is not None:
                            if not dialogue:
                                pending = PendingInstruction(
                                    claimed["instructionId"],
                                    command_id,
                                    text,
                                    value["binding"],
                                    time.monotonic() + claimed["remainingSeconds"],
                                    known=known_slots(text, PlanningCatalog(context)),
                                )
                            if pending is None:
                                raise reject("QUESTION_STALE", "元の指示が失効しています。", 409)
                            if dialogue:
                                pending.turns.append(
                                    {"turnId": f"answer{len(pending.turns) + 1}", "text": text}
                                )
                            pending.command_id = command_id
                            pending.question = json.loads(resolution.question_json)
                            pending.partial = (
                                json.loads(resolution.partial_plan_json)
                                if resolution.partial_plan_json
                                else None
                            )
                            if pending.partial:
                                pending.establish(pending.partial.get("knownOperations", []))
                            if not repeat_question:
                                pending.question_deadline = min(
                                    time.monotonic() + 300, pending.deadline
                                )
                            self.pending[session_id] = pending
                            waiting_information = await asyncio.to_thread(
                                self.repository.finish,
                                command_id,
                                fence,
                                "WAITING_INFORMATION",
                                {
                                    "type": "clarification_required",
                                    "question": json.loads(resolution.question_json),
                                },
                            )
                            continue
                        if resolution.no_action:
                            await asyncio.to_thread(
                                self.repository.finish,
                                command_id,
                                fence,
                                "NO_ACTION",
                                {
                                    "type": "no_action",
                                    "reason": "not_an_edit",
                                    "message": "変更の指示ではありません。",
                                },
                            )
                            continue
                        operations = resolution.operations
                        if dialogue and pending and operations:
                            pending.validate(operations, text, PlanningCatalog(context))
                        await asyncio.to_thread(
                            self.repository.stage, command_id, fence, "VALIDATING"
                        )
                    elif claimed["kind"] == "manual_changes":
                        operations = tuple(
                            BoundOperation.from_value(op) for op in command["operations"]
                        )
                    prepared = await asyncio.to_thread(
                        self.mutations.prepare, command_id, operations, resolution
                    )
                    while True:
                        while await asyncio.to_thread(
                            self.repository.hold_status, command_id, fence
                        ):
                            await asyncio.sleep(0.025)
                        try:
                            await asyncio.to_thread(
                                self.mutations.execute, command_id, fence, prepared
                            )
                            break
                        except AppError as error:
                            if error.code != "COMMAND_HELD":
                                raise
                except asyncio.CancelledError:
                    await asyncio.to_thread(
                        self.repository.finish,
                        command_id,
                        fence,
                        "INTERRUPTED",
                        {
                            "type": "failed",
                            "error": {
                                "code": "COMMAND_INTERRUPTED",
                                "message": "未保存の指示を中止しました。",
                                "retryable": True,
                                "currentRevision": None,
                            },
                        },
                    )
                    raise
                except AppError as error:
                    if error.code == "EDITING_SESSION_EXPIRED":
                        await asyncio.to_thread(
                            self.repository.finish,
                            command_id,
                            fence,
                            "EXPIRED",
                            {
                                "type": "expired",
                                "message": "編集session又は指示の期限が切れました。",
                            },
                        )
                        continue
                    await asyncio.to_thread(
                        self.repository.finish,
                        command_id,
                        fence,
                        "CONFLICT" if error.status == 409 else "FAILED",
                        {
                            "type": "conflict" if error.status == 409 else "failed",
                            "error": {
                                "code": error.code,
                                "message": error.detail[:1000],
                                "retryable": error.status >= 500,
                                "currentRevision": error.metadata.get("currentRevision"),
                            },
                        },
                    )
                except InvalidPlan:
                    await asyncio.to_thread(
                        self.repository.finish,
                        command_id,
                        fence,
                        "FAILED",
                        {
                            "type": "failed",
                            "error": {
                                "code": "PLANNER_PLAN_INVALID",
                                "message": "質問への答えを元の指示に当てはめられませんでした。"
                                "何も変更していません。行・欄名・値をまとめて話し直してください。",
                                "retryable": False,
                                "currentRevision": None,
                            },
                        },
                    )
                except Exception:
                    await asyncio.to_thread(
                        self.repository.finish,
                        command_id,
                        fence,
                        "FAILED",
                        {
                            "type": "failed",
                            "error": {
                                "code": "COMMAND_FAILED",
                                "message": "保存できませんでした。保存状態を再取得してください。",
                                "retryable": True,
                                "currentRevision": None,
                            },
                        },
                    )
                finally:
                    if prepared is not None:
                        self.mutations.discard(prepared)
                    self.mutations.forget(command_id)
                    self.inputs.pop(command_id, None)
                    pending = self.pending.get(session_id)
                    if pending and pending.command_id == command_id and not waiting_information:
                        self.pending.pop(session_id, None)
        finally:
            self.tasks.pop(session_id, None)
            self.queues.pop(session_id, None)

    @staticmethod
    async def direct(operations: tuple[BoundOperation, ...]) -> ResolvedInstruction:
        return ResolvedInstruction(operations)

    @staticmethod
    async def unchanged() -> ResolvedInstruction:
        return ResolvedInstruction(no_action=True)

    async def continue_from(
        self, session_id: str, token: str, value: dict[str, Any], prior_id: str
    ) -> dict[str, Any]:
        async with self.admission_locks.setdefault(session_id, asyncio.Lock()):
            prior = self.inputs.get(prior_id)
            if prior is None:
                raise reject("CONTINUATION_UNAVAILABLE", "未保存の元の指示が失効しています。", 409)
            cancel = {
                **value,
                "requestId": value["requestId"] + "_cancel",
                "command": {"kind": "cancel", "commandId": prior_id},
            }
            await self._submit(session_id, token, cancel)
            return await self._submit(session_id, token, value, (prior_id, prior[0]))

    async def rebind_scope(
        self, session_id: str, token: str, request: dict[str, Any]
    ) -> dict[str, Any]:
        async with self.admission_locks.setdefault(session_id, asyncio.Lock()):
            replay = await asyncio.to_thread(
                self.repository.scope_replay,
                session_id,
                token,
                request["requestId"],
                digest(request),
            )
            if replay:
                return replay
            pending = self.pending.get(session_id)
            reply = request["replyTo"]
            if (
                not pending
                or not pending.question
                or pending.question["missingKind"] != "scope"
                or (
                    pending.question["questionId"] != reply["questionId"]
                    or pending.question["version"] != reply["version"]
                )
            ):
                raise reject("QUESTION_STALE", "有効な対象範囲の質問を指定してください。", 409)
            if min(pending.deadline, pending.question_deadline) <= time.monotonic():
                raise reject("QUESTION_EXPIRED", "元の指示の期限が切れました。", 409)
            context = request["context"]
            current = await asyncio.to_thread(self.repository.input_context, session_id, token)
            if all(
                context[k] == current["scopeContext"][k]
                for k in (
                    "resource",
                    "mode",
                    "pageScope",
                    "selectedTableId",
                    "selectedHandles",
                    "localTargetHandle",
                )
            ):
                raise reject("SCOPE_UNCHANGED", "変更する対象範囲を指定してください。", 409)
            if context["interactionEpoch"] != pending.binding["interactionEpoch"]:
                raise reject("CONTEXT_STALE", "手動編集を越えて指示を移すことはできません。", 409)
            updated = await asyncio.to_thread(
                self.repository.rebind_context, session_id, token, context
            )
            scope = updated["scopeContext"]
            pending.binding = {
                "expectedRevision": scope["resourceRevision"],
                **{
                    k: scope[k]
                    for k in (
                        "contextRevision",
                        "interactionEpoch",
                        "catalogRevision",
                        "scopeDigest",
                    )
                },
            }
            pending.known["operations"] = []
            pending.partial = None
            self.pending[session_id] = pending
            value = {
                "contractVersion": "editing.v2",
                "requestId": request["requestId"],
                "binding": pending.binding,
                "afterCommandId": None,
                "command": {
                    "kind": "instruction",
                    "input": {
                        "kind": "text",
                        "text": "指定した対象範囲へ切り替えて元の指示を実行してください。",
                    },
                    "replyTo": None,
                },
            }
            try:
                return await self._submit(
                    session_id, token, value, rebinding=pending, fingerprint=digest(request)
                )
            except Exception:
                self.pending.pop(session_id, None)
                raise

    async def close(self) -> None:
        self.closed = True
        self.pending.clear()
        await asyncio.to_thread(self.repository.stop)
        for session_id in list(self.resolutions):
            self.abort_resolution(session_id)
        # Do not cancel an in-flight database commit. Fence queued/planning work,
        # then let the running task drain before disposing the database.
        for queue in self.queues.values():
            for command_id, _ in queue:
                await asyncio.to_thread(
                    self.repository.finish,
                    command_id,
                    1,
                    "INTERRUPTED",
                    {
                        "type": "failed",
                        "error": {
                            "code": "COMMAND_INTERRUPTED",
                            "message": "終了時に未保存の指示を中止しました。",
                            "retryable": True,
                            "currentRevision": None,
                        },
                    },
                )
        if self.tasks:
            await asyncio.gather(*list(self.tasks.values()), return_exceptions=True)
