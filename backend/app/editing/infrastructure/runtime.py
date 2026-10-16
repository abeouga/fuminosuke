from __future__ import annotations

from typing import Any

from app.ai.api_budget import ApiBudget
from app.core.config import Settings
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
        self.voice = (
            VoiceConnections(self, settings, budget or ApiBudget(database)) if settings else None
        )

    async def close(self) -> None:
        if self.voice:
            await self.voice.close()
        self.acquisition.clear()
        await self.controller.close()
