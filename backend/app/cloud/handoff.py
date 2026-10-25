"""Rolling update on ECS: a new task relays requests to the previous task until it takes over.

Only one runtime may own an installation (the PostgreSQL lease). A new task therefore waits for
the previous task to stop, but the load balancer already sends it requests. Until it owns the
lease it forwards plain HTTP requests, unchanged, to the previous task of the same ECS service
(inside the VPC), which still serves them with its own checks. When the previous task is gone
the request waits for this task to take over instead (application.runtime_ready).
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any

import httpx
from starlette.types import ASGIApp, Message, Receive, Scope, Send

logger = logging.getLogger(__name__)

HOP_BY_HOP = {b"connection", b"keep-alive", b"transfer-encoding", b"upgrade", b"te",
              b"proxy-connection", b"content-length"}
MAX_BODY = 64 * 1024**2


class PreviousTask:
    """The private address of another running task of this ECS service (cached briefly)."""

    def __init__(self, service: str | None, port: int) -> None:
        self.service, self.port = service, port
        self.metadata = os.environ.get("ECS_CONTAINER_METADATA_URI_V4")
        self.cached: tuple[float, str | None] = (0.0, None)
        self.own: tuple[str, str] | None = None

    async def address(self) -> str | None:
        if not self.service or not self.metadata:
            return None
        checked, value = self.cached
        if time.monotonic() - checked < 3:
            return value
        try:
            value = await asyncio.to_thread(self._lookup)
        except Exception:  # relaying is best effort; waiting still works
            logger.warning("Previous task lookup failed", exc_info=True)
            value = None
        self.cached = (time.monotonic(), value)
        return value

    def _lookup(self) -> str | None:
        import boto3  # type: ignore[import-untyped]  # only on ECS

        if self.own is None and self.metadata:
            task = httpx.get(self.metadata + "/task", timeout=2).json()
            self.own = (str(task["Cluster"]), str(task["TaskARN"]))
        if self.own is None:
            return None
        cluster, own_arn = self.own
        ecs = boto3.client("ecs")
        # The previous task is "desired STOPPED" as soon as ECS starts replacing it, but it keeps
        # serving until it actually stops: look at both lists and keep what is still running.
        arns = [arn for desired in ("RUNNING", "STOPPED") for arn in ecs.list_tasks(
            cluster=cluster, serviceName=self.service, desiredStatus=desired,
        )["taskArns"] if arn != own_arn][:100]
        if not arns:
            return None
        tasks = ecs.describe_tasks(cluster=cluster, tasks=arns)["tasks"]
        for task in sorted(tasks, key=lambda item: str(item.get("startedAt", ""))):
            # DEACTIVATING: leaving the load balancer, but still serving until it is told to stop.
            if task.get("lastStatus") not in {"RUNNING", "DEACTIVATING"}:
                continue
            for attachment in task.get("attachments", []):
                for detail in attachment.get("details", []):
                    if detail.get("name") == "privateIPv4Address":
                        return f"http://{detail['value']}:{self.port}"
        return None


class HandoffRelay:
    """ASGI wrapper: relay HTTP while this task's runtime is not ready yet."""

    def __init__(self, app: ASGIApp, ready: Any, previous: PreviousTask) -> None:
        self.app, self.ready, self.previous = app, ready, previous
        self.client = httpx.AsyncClient(timeout=httpx.Timeout(55, connect=2),
                                        follow_redirects=False, trust_env=False)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (scope["type"] != "http" or scope["path"] == "/api/health"
                or self.ready() or not await self.relay(scope, receive, send)):
            await self.app(scope, scope.pop("fuminosuke.replay", receive), send)

    async def relay(self, scope: Scope, receive: Receive, send: Send) -> bool:
        target = await self.previous.address()
        if target is None:
            return False
        body = bytearray()
        more = True
        while more:
            message = await receive()
            body += message.get("body", b"")
            more = message.get("more_body", False)
            if len(body) > MAX_BODY:
                await send({"type": "http.response.start", "status": 413, "headers": []})
                await send({"type": "http.response.body", "body": b""})
                return True
        headers = [(k, v) for k, v in scope["headers"] if k.lower() not in HOP_BY_HOP]
        client = scope.get("client")
        # The scheme this task saw (https behind the load balancer), so the checks match.
        headers += [(b"x-forwarded-proto", str(scope.get("scheme", "https")).encode())]
        if client:
            headers += [(b"x-forwarded-for", client[0].encode())]
        path = scope.get("raw_path") or scope["path"].encode()
        url = target + path.decode("latin-1")
        if scope.get("query_string"):
            url += "?" + scope["query_string"].decode("latin-1")
        try:
            request = self.client.build_request(scope["method"], url, headers=headers,
                                                content=bytes(body))
            response = await self.client.send(request, stream=True)
        except httpx.HTTPError:
            # The previous task has stopped: replay the body to the local runtime (it waits).
            self._replay(scope, bytes(body))
            return False
        try:
            await send({"type": "http.response.start", "status": response.status_code,
                        "headers": [(k.encode("latin-1"), v.encode("latin-1"))
                                    for k, v in response.headers.multi_items()
                                    if k.lower().encode() not in HOP_BY_HOP]})
            async for chunk in response.aiter_raw():
                await send({"type": "http.response.body", "body": chunk, "more_body": True})
            await send({"type": "http.response.body", "body": b"", "more_body": False})
        finally:
            await response.aclose()
        return True

    @staticmethod
    def _replay(scope: Scope, body: bytes) -> None:
        sent = False

        async def receive() -> Message:
            nonlocal sent
            if sent:
                return {"type": "http.disconnect"}
            sent = True
            return {"type": "http.request", "body": body, "more_body": False}

        scope["fuminosuke.replay"] = receive
