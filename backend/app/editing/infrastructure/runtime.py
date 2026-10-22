from __future__ import annotations

import json
from contextlib import suppress
from typing import Any

from app.ai.api_budget import ApiBudget
from app.core.config import Settings
from app.db.models import utc_now
from app.db.session import Database
from app.editing.application.controller import CommandController
from app.editing.application.instruction_planner import InstructionPlanner
from app.editing.application.ports import InstructionResolver
from app.editing.application.voice_acquisition import VoiceAcquisition
from app.editing.catalog import CatalogService
from app.editing.commands import CommandCompiler
from app.editing.domain.command import ResolvedInstruction
from app.editing.domain.planning import PlannerPolicy
from app.editing.errors import reject
from app.editing.infrastructure.command_models import CommandRequest
from app.editing.infrastructure.command_repository import SqlCommandRepository
from app.editing.infrastructure.mutation_core import MutationCore
from app.editing.infrastructure.mutation_uow import BusinessMutationUnitOfWork
from app.editing.infrastructure.plan_binder import PlanBinder
from app.editing.infrastructure.planner_provider import OpenAIPlanner
from app.editing.infrastructure.sessions import Sessions
from app.editing.infrastructure.voice_stream import VoiceConnections
from app.services.ocr_result_service import OcrResultService
from app.services.template_authoring_service import TemplateAuthoringService
from app.storage.local_storage import LocalStorageService


class UnavailableResolver:
    async def resolve(self, instruction: str, context: dict[str, Any]) -> ResolvedInstruction:
        raise reject("PLANNER_NOT_CONFIGURED", "編集用AIの接続設定がありません。", 503)


class EditingRuntime:
    def __init__(
        self,
        database: Database,
        storage: LocalStorageService,
        resolver: InstructionResolver | None = None,
        *,
        settings: Settings | None = None,
        budget: ApiBudget | None = None,
    ) -> None:
        authoring, ocr = (
            TemplateAuthoringService(database, storage),
            OcrResultService(database, storage),
        )
        catalogs = CatalogService(authoring, ocr)
        self.sessions = Sessions(database, catalogs)
        self.repository = SqlCommandRepository(self.sessions)
        self.mutations = BusinessMutationUnitOfWork(
            self.repository, MutationCore(authoring, ocr, catalogs, CommandCompiler())
        )
        self.provider = None
        if resolver is None and settings is not None:
            policy = PlannerPolicy()
            self.provider = OpenAIPlanner(settings, budget or ApiBudget(database), policy)
            self.repository.instruction_guard = self.provider.check_available
            resolver = InstructionPlanner(self.provider, self.provider, PlanBinder(), policy)
        self.controller = CommandController(
            self.repository, self.mutations, resolver or UnavailableResolver()
        )
        self.acquisition = VoiceAcquisition()
        self.controller.voice_input = self.acquisition.text
        self.settings = settings
        if settings is not None and settings.voice.transcript_log:
            self.controller.instruction_log = self.log_instruction
        self.voice = (
            VoiceConnections(self, settings, budget or ApiBudget(database)) if settings else None
        )

    def log_instruction(self, session_id: str, command_id: str, entry: dict[str, Any]) -> None:
        """Opt-in local log (VOICE_TRANSCRIPT_LOG): typed/spoken instruction, AI answer, outcome."""
        assert self.settings is not None
        with self.sessions.database.session() as session:
            row = session.get(CommandRequest, command_id)
            if row is None:
                return
            final = (row.final_event_json or {}).get("payload") or {}
            outcome: dict[str, Any] = {"state": row.state}
            if final.get("type") == "applied":
                outcome["message"] = (final.get("receipt") or {}).get("notification")
            elif isinstance(final.get("error"), dict):
                outcome["code"] = final["error"].get("code")
                outcome["message"] = final["error"].get("message")
            elif final.get("message"):
                outcome["message"] = final["message"]
            line = {
                "at": utc_now().isoformat(),
                "sessionId": session_id,
                "commandId": command_id,
                "resource": f"{row.resource_kind}:{row.resource_id}",
                **entry,
                "outcome": outcome,
            }
        path = self.settings.storage.data_dir / "logs" / "ai-instructions.jsonl"
        with suppress(OSError):
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as file:
                file.write(json.dumps(line, ensure_ascii=False, default=str) + "\n")

    async def close(self) -> None:
        if self.voice:
            await self.voice.close()
        self.acquisition.clear()
        await self.controller.close()
