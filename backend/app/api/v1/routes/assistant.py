from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Request
from openai import OpenAIError
from pydantic import ValidationError

from app.api.dependencies import get_app_settings, get_database, get_storage
from app.api.editing_cutover import legacy_edit_guard
from app.assistant.contracts import (
    Apply,
    ContextUpdate,
    PlaybackEnd,
    Presentation,
    ProposalCreate,
    ReceiptSpeechRequest,
    SessionCreate,
    SpeechRequest,
    TextTurn,
    TurnStart,
    UndoProposalCreate,
)
from app.assistant.operations import refused
from app.assistant.service import AssistantService
from app.assistant.voice_service import VoiceService


def local_request(request: Request) -> None:
    if getattr(request.app.state, "cloud_authenticated", False):
        return
    origin = request.headers.get("origin")
    if request.url.hostname not in {"127.0.0.1", "localhost", "::1"} or (
        origin is not None and origin not in get_app_settings(request).app.frontend_origins
    ):
        raise refused(
            "ローカル画面からの操作だけを受け付けます。", "ASSISTANT_ORIGIN_REJECTED", 403
        )


router = APIRouter(
    prefix="/assistant", tags=["assistant"],
    dependencies=[Depends(local_request), Depends(legacy_edit_guard)],
)
Token = Annotated[str, Header(alias="X-Assistant-Session", min_length=32, max_length=100)]
RequestKey = Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)]


def service(request: Request) -> AssistantService:
    return AssistantService(get_database(request), get_storage(request))


def voice(request: Request) -> VoiceService:
    return VoiceService(
        service(request),
        get_app_settings(request),
        getattr(request.app.state, "voice_provider", None),
    )


@router.post("/sessions")
def create_session(request: Request, body: SessionCreate) -> dict[str, Any]:
    return service(request).create_session(body.context)


@router.put("/sessions/context")
def update_context(request: Request, body: ContextUpdate, token: Token) -> dict[str, Any]:
    return service(request).update_context(token, body)


@router.post("/proposals")
def propose(request: Request, body: ProposalCreate, token: Token) -> dict[str, Any]:
    return service(request).propose(token, body)


@router.post("/proposals/{proposal_id}/presented")
def presented(
    request: Request, proposal_id: str, body: Presentation, token: Token
) -> dict[str, Any]:
    return service(request).present(token, proposal_id, body)


@router.post("/proposals/{proposal_id}/apply")
def apply(
    request: Request, proposal_id: str, body: Apply, token: Token, key: RequestKey
) -> dict[str, Any]:
    return service(request).apply(token, proposal_id, key, body)


@router.post("/proposals/{proposal_id}/cancel")
def cancel(request: Request, proposal_id: str, token: Token) -> dict[str, Any]:
    return service(request).cancel(token, proposal_id)


@router.get("/requests/{key}")
def request_receipt(request: Request, key: str, token: Token) -> dict[str, Any]:
    return service(request).request(token, key)


@router.get("/history")
def history(request: Request, token: Token) -> dict[str, Any]:
    return service(request).history(token)


@router.post("/operations/{operation_id}/undo-proposal")
def undo_proposal(
    request: Request, operation_id: str, body: UndoProposalCreate, token: Token
) -> dict[str, Any]:
    return service(request).propose_undo(token, operation_id, body)


@router.get("/voice/status")
def voice_status(request: Request) -> dict[str, Any]:
    return voice(request).status()


@router.post("/turns")
def start_turn(request: Request, body: TurnStart, token: Token, key: RequestKey) -> dict[str, Any]:
    return voice(request).start(token, key, body)


@router.post("/turns/text")
async def text_turn(
    request: Request, body: TextTurn, token: Token, key: RequestKey
) -> dict[str, Any]:
    try:
        return await voice(request).text(token, key, body)
    except (OpenAIError, ValidationError) as error:
        raise refused(
            "提案APIに失敗しました。自動再送はしません。手入力を使用してください。",
            "VOICE_PROVIDER_ERROR",
            503,
        ) from error


@router.post("/turns/{turn_id}/audio")
async def audio_turn(request: Request, turn_id: str, token: Token) -> dict[str, Any]:
    engine = voice(request)
    with engine.database.session() as session:
        engine._turn(session, token, turn_id)
    data = bytearray()
    async for chunk in request.stream():
        if len(data) + len(chunk) > engine.settings.voice.max_audio_bytes:
            engine.cancel(token, turn_id)
            raise refused("音声サイズが10MiBの上限を超えています。", "VOICE_AUDIO_TOO_LARGE", 413)
        data.extend(chunk)
    try:
        return await engine.audio(
            token,
            turn_id,
            bytes(data),
            request.headers.get("content-type", "").split(";")[0].lower(),
        )
    except (OpenAIError, ValidationError) as error:
        raise refused(
            "音声APIに失敗しました。自動再送はしません。録り直すか手入力を使用してください。",
            "VOICE_PROVIDER_ERROR",
            503,
        ) from error
    finally:
        data.clear()


@router.post("/turns/{turn_id}/cancel")
def cancel_turn(request: Request, turn_id: str, token: Token) -> dict[str, str]:
    return voice(request).cancel(token, turn_id)


@router.post("/speech")
async def speech(
    request: Request, body: SpeechRequest, token: Token, key: RequestKey
) -> dict[str, Any]:
    try:
        return await voice(request).speech(token, key, body)
    except (OpenAIError, ValidationError, ValueError) as error:
        raise refused(
            "読み上げAPIに失敗しました。画面で案を確認してください。", "VOICE_PROVIDER_ERROR", 503
        ) from error


@router.post("/speech/{speech_id}/ended")
def speech_ended(
    request: Request, speech_id: str, body: PlaybackEnd, token: Token
) -> dict[str, str]:
    return voice(request).playback_end(token, speech_id, body.completed)


@router.post("/speech/receipt")
async def receipt_speech(
    request: Request, body: ReceiptSpeechRequest, token: Token, key: RequestKey
) -> dict[str, Any]:
    try:
        return await voice(request).receipt_speech(token, key, body.request_key)
    except (OpenAIError, ValidationError, ValueError) as error:
        raise refused(
            "読み上げAPIに失敗しました。保存状態は変わりません。", "VOICE_PROVIDER_ERROR", 503
        ) from error
