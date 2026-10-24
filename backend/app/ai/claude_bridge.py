"""Answer OpenAI Responses requests with Claude, so every caller keeps its request/response code.

Only `POST https://api.openai.com/v1/responses` is translated. Decisions, realtime transcription and
anything the bridge cannot express (conversations with several tools or function results) still go
to OpenAI unchanged. `AI_RESPONSES_PROVIDER=openai` turns the bridge off entirely.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import logging
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any

import anthropic
import httpx

logger = logging.getLogger(__name__)

FALLBACK_BETA = "server-side-fallback-2026-07-01"
# Claude rejects images over 5MB; keep a margin for the base64 overhead accounting.
IMAGE_BYTES_LIMIT = 3_600_000
# Structured outputs accept a subset of JSON Schema. The callers validate the answer again.
UNSUPPORTED_SCHEMA_KEYS = {
    "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf",
    "minLength", "maxLength", "pattern", "format", "maxItems", "minProperties",
    "maxProperties", "uniqueItems", "title", "default", "examples",
}


# Schemas Claude refused to compile ("grammar is too large"); they are asked for in the prompt.
_PROMPT_SCHEMAS: set[str] = set()
PROMPT_SCHEMA_INSTRUCTION = (
    "回答は次のJSON Schemaに従うJSONオブジェクト1つだけにしてください。"
    "説明文やコードブロックの記号は付けないでください。\n"
)


class Untranslatable(Exception):
    """The request uses a Responses feature the bridge does not map; send it to OpenAI."""


def _schema(node: Any) -> Any:
    if isinstance(node, dict):
        out = {k: _schema(v) for k, v in node.items() if k not in UNSUPPORTED_SCHEMA_KEYS}
        if isinstance(out.get("minItems"), int) and out["minItems"] > 1:
            out["minItems"] = 1
        return out
    if isinstance(node, list):
        return [_schema(item) for item in node]
    return node


def _image(part: dict[str, Any]) -> dict[str, Any]:
    url = part.get("image_url")
    if not isinstance(url, str):
        raise Untranslatable("image without image_url")
    if not url.startswith("data:"):
        return {"type": "image", "source": {"type": "url", "url": url}}
    header, _, data = url.partition(",")
    media = header[5:].split(";")[0] or "image/png"
    raw = base64.b64decode(data)
    if len(raw) > IMAGE_BYTES_LIMIT:
        raw, media = _shrink(raw)
        data = base64.b64encode(raw).decode()
    return {"type": "image", "source": {"type": "base64", "media_type": media, "data": data}}


def _shrink(raw: bytes) -> tuple[bytes, str]:
    from PIL import Image

    image = Image.open(io.BytesIO(raw)).convert("RGB")
    quality = 90
    while True:
        buffer = io.BytesIO()
        image.save(buffer, "JPEG", quality=quality)
        if buffer.tell() <= IMAGE_BYTES_LIMIT or quality <= 50:
            return buffer.getvalue(), "image/jpeg"
        quality -= 10
        if quality <= 60:
            image = image.resize((image.width * 3 // 4, image.height * 3 // 4))


def _content(content: Any) -> list[dict[str, Any]]:
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    blocks: list[dict[str, Any]] = []
    for part in content or []:
        kind = part.get("type")
        if kind in {"input_text", "output_text", "text"}:
            if part.get("text"):
                blocks.append({"type": "text", "text": part["text"]})
        elif kind == "input_image":
            blocks.append(_image(part))
        else:
            raise Untranslatable(f"content part {kind}")
    return blocks


def _messages(body: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    system = [body["instructions"]] if body.get("instructions") else []
    messages: list[dict[str, Any]] = []
    items = body.get("input")
    if isinstance(items, str):
        items = [{"role": "user", "content": items}]
    for item in items or []:
        if item.get("type", "message") != "message":
            raise Untranslatable(f"input item {item.get('type')}")
        role = item.get("role")
        if role in {"system", "developer"}:
            system.extend(b["text"] for b in _content(item.get("content")) if b["type"] == "text")
            continue
        if role not in {"user", "assistant"}:
            raise Untranslatable(f"role {role}")
        blocks = _content(item.get("content"))
        if messages and messages[-1]["role"] == role:
            messages[-1]["content"].extend(blocks)
        else:
            messages.append({"role": role, "content": blocks})
    if not messages or messages[0]["role"] != "user":
        raise Untranslatable("conversation must start with the user")
    return "\n\n".join(system), messages


def _forced_tool(body: dict[str, Any]) -> dict[str, Any] | None:
    """A single forced function becomes a structured answer (Claude does not force tools)."""
    tools = body.get("tools")
    if not tools:
        return None
    choice = body.get("tool_choice")
    if (
        len(tools) == 1
        and tools[0].get("type") == "function"
        and isinstance(choice, dict)
        and choice.get("name") == tools[0].get("name")
    ):
        return dict(tools[0])
    raise Untranslatable("tools")


def _thinking(effort: str | None) -> tuple[dict[str, Any], str]:
    if effort in {None, "none", "minimal"}:
        return {"type": "between_tools"}, "low"
    levels = {"xhigh": "xhigh", "high": "high", "medium": "medium"}
    return {"type": "adaptive"}, levels.get(effort or "", "low")


def translate(body: dict[str, Any], model: str) -> tuple[dict[str, Any], dict[str, Any] | None]:
    system, messages = _messages(body)
    tool = _forced_tool(body)
    thinking, effort = _thinking((body.get("reasoning") or {}).get("effort"))
    output_config: dict[str, Any] = {"effort": effort}
    schema = (
        tool.get("parameters")
        if tool is not None
        else ((body.get("text") or {}).get("format") or {}).get("schema")
    )
    if schema is not None:
        output_config["format"] = {"type": "json_schema", "schema": _schema(schema)}
    request: dict[str, Any] = {
        "model": model,
        "messages": messages,
        # Thinking shares this ceiling; never go below what the caller allowed for the answer.
        "max_tokens": min(64000, max(16000, int(body.get("max_output_tokens") or 0) * 2)),
        "thinking": thinking,
        "output_config": output_config,
    }
    if system:
        request["system"] = system
    return request, tool


def _schema_key(params: dict[str, Any]) -> str | None:
    schema = params["output_config"].get("format")
    if schema is None:
        return None
    return hashlib.sha256(json.dumps(schema, sort_keys=True).encode()).hexdigest()


def _in_prompt(params: dict[str, Any]) -> dict[str, Any]:
    """The schema as an instruction instead of enforced decoding; the caller validates it."""
    output_config = dict(params["output_config"])
    schema = output_config.pop("format")["schema"]
    rule = PROMPT_SCHEMA_INSTRUCTION + json.dumps(schema, ensure_ascii=False)
    system = params.get("system")
    return {
        **params,
        "output_config": output_config,
        "system": f"{system}\n\n{rule}" if system else rule
    }


def _json_text(text: str) -> str:
    start, end = text.find("{"), text.rfind("}")
    return text[start : end + 1] if 0 <= start < end else text


def response_json(
    message: Any, request_body: dict[str, Any], tool: dict[str, Any] | None, loose: bool = False
) -> dict[str, Any]:
    text = "".join(b.text for b in message.content if getattr(b, "type", None) == "text")
    if loose:
        text = _json_text(text)
    stop = message.stop_reason
    reason = {"max_tokens": "max_output_tokens", "refusal": "content_filter"}.get(stop or "")
    item_id = "msg_" + uuid.uuid4().hex
    if tool is not None:
        output = [{
            "type": "function_call", "id": "fc_" + uuid.uuid4().hex,
            "call_id": "call_" + uuid.uuid4().hex, "name": tool["name"],
            "arguments": text, "status": "completed",
        }]
    else:
        output = [{
            "type": "message", "id": item_id, "role": "assistant", "status": "completed",
            "content": [{"type": "output_text", "text": text, "annotations": []}],
        }]
    usage = message.usage
    return {
        "id": "resp_" + message.id,
        "object": "response",
        "created_at": int(time.time()),
        "status": "incomplete" if reason else "completed",
        "incomplete_details": {"reason": reason} if reason else None,
        "error": None,
        "model": message.model,
        "instructions": request_body.get("instructions"),
        "output": output,
        "parallel_tool_calls": False,
        "tool_choice": request_body.get("tool_choice", "auto"),
        "tools": request_body.get("tools", []),
        "metadata": {},
        "usage": {
            "input_tokens": usage.input_tokens,
            "input_tokens_details": {"cached_tokens": usage.cache_read_input_tokens or 0},
            "output_tokens": usage.output_tokens,
            "output_tokens_details": {"reasoning_tokens": 0},
            "total_tokens": usage.input_tokens + usage.output_tokens,
        },
    }


def _error(status: int, message: str) -> httpx.Response:
    return httpx.Response(
        status, json={"error": {"message": message, "type": "claude_error", "code": None}}
    )


class ClaudeBridgeTransport(httpx.AsyncBaseTransport):
    """Send translated Responses requests to Claude; everything else goes to `upstream`."""

    def __init__(self, api_key: str, model: str, upstream: httpx.AsyncBaseTransport) -> None:
        self.model = model
        self.upstream = upstream
        self.client = anthropic.AsyncAnthropic(api_key=api_key, max_retries=0, timeout=600.0)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if request.method != "POST" or request.url.path != "/v1/responses":
            return await self.upstream.handle_async_request(request)
        body = json.loads(await request.aread())
        try:
            claude, tool = translate(body, self.model)
        except Untranslatable as reason:
            logger.info("Responses request sent to OpenAI (not translated: %s)", reason)
            return await self.upstream.handle_async_request(request)
        params = {**claude, "betas": [FALLBACK_BETA], "fallbacks": "default"}
        if body.get("stream"):
            return httpx.Response(
                200, headers={"content-type": "text/event-stream"},
                stream=_SseStream(self._events(params, body, tool)), request=request,
            )
        try:
            message, loose = await self._create(params)
        except anthropic.APITimeoutError as error:
            raise httpx.ReadTimeout("Claude timed out", request=request) from error
        except anthropic.APIConnectionError as error:
            raise httpx.ConnectError("Claude connection failed", request=request) from error
        except anthropic.APIStatusError as error:
            logger.warning("Claude rejected request: %s %s", error.status_code, error.message)
            return _error(503 if error.status_code == 529 else error.status_code, error.message)
        return httpx.Response(
            200, json=response_json(message, body, tool, loose), request=request
        )

    async def _create(self, params: dict[str, Any]) -> tuple[Any, bool]:
        key = _schema_key(params)
        if key in _PROMPT_SCHEMAS:
            return await self.client.beta.messages.create(**_in_prompt(params)), True
        try:
            return await self.client.beta.messages.create(**params), False
        except anthropic.BadRequestError as error:
            if key is None or "grammar" not in error.message:
                raise
            _PROMPT_SCHEMAS.add(key)
            return await self.client.beta.messages.create(**_in_prompt(params)), True

    async def aclose(self) -> None:
        await self.client.close()
        await self.upstream.aclose()

    async def _events(
        self, params: dict[str, Any], body: dict[str, Any], tool: dict[str, Any] | None
    ) -> AsyncIterator[bytes]:
        def sse(event: dict[str, Any]) -> bytes:
            data = json.dumps(event, ensure_ascii=False)
            return f"event: {event['type']}\ndata: {data}\n\n".encode()

        item_id = "msg_" + uuid.uuid4().hex
        yield sse({"type": "response.created", "response": {"status": "in_progress"}})
        key = _schema_key(params)
        loose = key in _PROMPT_SCHEMAS
        try:
            for _attempt in range(2):
                try:
                    sent = _in_prompt(params) if loose else params
                    async with self.client.beta.messages.stream(**sent) as stream:
                        async for event in stream:
                            if (
                                event.type == "content_block_delta"
                                and event.delta.type == "text_delta"
                            ):
                                yield sse({
                                    "type": "response.output_text.delta", "item_id": item_id,
                                    "output_index": 0, "content_index": 0,
                                    "delta": event.delta.text,
                                })
                        message = await stream.get_final_message()
                    break
                except anthropic.BadRequestError as error:
                    if loose or key is None or "grammar" not in error.message:
                        raise
                    _PROMPT_SCHEMAS.add(key)
                    loose = True
        except anthropic.APIError as error:
            logger.warning("Claude stream failed: %s", error)
            yield sse({"type": "response.failed", "response": {
                "status": "failed", "error": {"message": str(error)}, "output": [],
            }})
            return
        result = response_json(message, body, tool, loose)
        text = "".join(
            p["text"] for item in result["output"] for p in item.get("content", [])
        )
        yield sse({"type": "response.output_text.done", "item_id": item_id, "text": text})
        done = result["status"] == "completed"
        terminal = "response.completed" if done else "response.incomplete"
        yield sse({"type": terminal, "response": result})


class _SseStream(httpx.AsyncByteStream):
    def __init__(self, events: AsyncIterator[bytes]) -> None:
        self.events = events

    async def __aiter__(self) -> AsyncIterator[bytes]:
        async for chunk in self.events:
            yield chunk

    async def aclose(self) -> None:
        await self.events.aclose()  # type: ignore[attr-defined]


def bridged(upstream: httpx.AsyncBaseTransport | None = None) -> httpx.AsyncBaseTransport:
    """The transport for OpenAI clients: Claude for Responses when configured, else OpenAI."""
    from app.core.config import get_settings

    upstream = upstream or httpx.AsyncHTTPTransport()
    ocr = get_settings().ocr
    key = ocr.anthropic_api_key.get_secret_value().strip() if ocr.anthropic_api_key else ""
    if ocr.responses_provider != "claude" or not key:
        return upstream
    return ClaudeBridgeTransport(key, ocr.claude_model, upstream)
