from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import os
import secrets
import signal
import time
from contextlib import AsyncExitStack, asynccontextmanager
from http.cookies import SimpleCookie
from typing import Any
from urllib.parse import urlencode

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from starlette.types import Message, Receive, Scope, Send

from app.cloud.cognito import Cognito, CognitoUnavailable
from app.cloud.config import CloudSettings
from app.cloud.handoff import HandoffRelay, PreviousTask
from app.cloud.journal import Journal, ReceiptStore, S3Receipts, s3_client
from app.cloud.operations import Operations
from app.cloud.registry import AccessDenied, Registry, request_credential
from app.cloud.runtime import cloud_runtime
from app.cloud.tenants import TenantApplications
from app.core.config import Settings
from app.core.errors import AppError
from app.core.logging import configure_logging

SESSION = "__Host-fuminosuke"
LOGIN = "__Host-fuminosuke-login"


def cookie(scope: Scope, name: str) -> str:
    try:
        headers = dict(scope.get("headers", []))
        values = SimpleCookie()
        values.load(headers.get(b"cookie", b"").decode("latin-1"))
        return values[name].value if name in values else ""
    except (ValueError, KeyError):
        return ""


def everyone_correction_rates(registry: Registry, operations: Operations) -> list[dict[str, Any]]:
    """The correction rate of each admitted user (named by their sign-in subject)."""
    from app.services.correction_rate_service import CorrectionRateService

    users: list[dict[str, Any]] = []
    for tenant in registry.active_tenants():
        database = operations.database(tenant)
        if database is None:
            continue
        try:
            summary = CorrectionRateService(database).summary()
        finally:
            database.dispose()
        users.append({"user": registry.subject(tenant), **summary})
    return sorted(users, key=lambda item: -item["total"]["read"])


def problem(code: str, status: int) -> JSONResponse:
    return JSONResponse({"code": code, "detail": "ログイン状態または要求元を確認してください。"},
                        status_code=status, headers={"Cache-Control": "no-store"})


# Rolling update: a new task waits for the previous one to release the single-runtime lease.
# Requests that reach it meanwhile wait (no error) instead of failing; under CloudFront's 60 s.
HANDOFF_REQUEST_WAIT = 50
HANDOFF_LEASE_WAIT = 900


async def runtime_ready(app: FastAPI) -> bool:
    try:
        await asyncio.wait_for(app.state.ready.wait(), HANDOFF_REQUEST_WAIT)
        return True
    except TimeoutError:
        return False


def lease_busy(error: Exception) -> bool:
    """Only the "another runtime owns it" refusals are worth waiting for."""
    return isinstance(error, OSError) or "owns this installation" in str(error)


class TenantGateway:
    def __init__(self, app: FastAPI, config: CloudSettings) -> None:
        self.app, self.config = app, config

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in {"http", "websocket"}:
            return
        headers = dict(scope["headers"])
        if (headers.get(b"host", b"").decode("latin-1")
                != self.config.public_origin.split("://", 1)[1]
                or scope["scheme"] not in (
                    {"http", "ws"} if self.config.loopback else {"https", "wss"})):
            await self.deny(scope, receive, send, 403, "ORIGIN_REJECTED")
            return
        if not await runtime_ready(self.app):
            await self.deny(scope, receive, send, 503, "RUNTIME_STARTING")
            return
        origin = headers.get(b"origin", b"").decode("latin-1")
        token = cookie(scope, SESSION)
        try:
            identity = self.app.state.registry.authenticate(token)
        except AccessDenied:
            await self.deny(scope, receive, send, 401, "AUTH_REQUIRED")
            return
        mutating = scope["type"] == "http" and scope["method"] not in {"GET", "HEAD", "OPTIONS"}
        if (scope["type"] == "websocket" or mutating) and origin != self.config.public_origin:
            await self.deny(scope, receive, send, 403, "CSRF_REJECTED")
            return
        if mutating and not secrets.compare_digest(
            headers.get(b"x-csrf-token", b"").decode("latin-1"), identity.csrf,
        ):
            await self.deny(scope, receive, send, 403, "CSRF_REJECTED")
            return
        try:
            tenant = await self.app.state.tenants.get(identity.tenant_id)
        except AccessDenied:
            await self.deny(scope, receive, send, 401, "AUTH_REQUIRED")
            return
        scope = dict(scope, cloud_identity=identity)
        if mutating and scope["method"] != "DELETE":
            try:
                length = int(headers.get(b"content-length", b"0"))
                if length < 0 or length > 64 * 1024**2:
                    raise ValueError("Upload request too large")
                operations = self.app.state.operations
                cancelling = scope["path"].startswith("/api/v1/jobs/") and scope["path"].endswith(
                    "/cancel",
                )
                if operations is not None and not cancelling:
                    operations.admission(identity.tenant_id, length)
            except ValueError:
                await self.deny(scope, receive, send, 413, "BETA_UPLOAD_LIMIT")
                return
            except AppError as error:
                await JSONResponse({"code": error.code, "detail": error.detail},
                                   status_code=error.status)(scope, receive, send)
                return

        original_receive = receive
        received_bytes = 0

        async def bounded_receive() -> Message:
            nonlocal received_bytes
            message = await original_receive()
            received_bytes += len(message.get("body", b""))
            if received_bytes > 64 * 1024**2:
                raise AppError(status=413, code="BETA_UPLOAD_LIMIT", title="Upload limit",
                               detail="1回の送信は64MiB以内にしてください。")
            return message

        async def private_send(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.start":
                message = dict(message)
                headers = message.get("headers", [])
                # Owner data is never stored by shared caches. An image the app marks
                # "private, max-age" (content-addressed or ETag-checked) may stay in this
                # browser only; logout clears it with Clear-Site-Data.
                cache = next((v for k, v in headers if k.lower() == b"cache-control"), b"")
                if not (cache.startswith(b"private, max-age") and b"public" not in cache):
                    cache = b"private, no-store"
                message["headers"] = [
                    (k, v) for k, v in headers
                    if k.lower() not in {b"cache-control", b"referrer-policy"}
                ] + [(b"cache-control", cache), (b"referrer-policy", b"no-referrer")]
            await send(message)

        async def watch() -> None:
            while True:
                await asyncio.sleep(1)
                self.app.state.registry.authenticate(token)

        context = request_credential.set((self.app.state.registry, token))
        request = asyncio.create_task(tenant(scope, bounded_receive, private_send))
        watchdog = asyncio.create_task(watch())
        try:
            done, _ = await asyncio.wait({request, watchdog}, return_when=asyncio.FIRST_COMPLETED)
            if request in done:
                await request
            else:
                # No session may keep an SSE/voice connection alive after revocation or expiry.
                request.cancel()
                await asyncio.gather(request, return_exceptions=True)
                if scope["type"] == "websocket":
                    await send({"type": "websocket.close", "code": 1008})
        finally:
            request.cancel()
            watchdog.cancel()
            await asyncio.gather(request, watchdog, return_exceptions=True)
            request_credential.reset(context)

    @staticmethod
    async def deny(scope: Scope, receive: Receive, send: Send, status: int, code: str) -> None:
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
        else:
            await problem(code, status)(scope, receive, send)


def create_cloud_app(settings: Settings, config: CloudSettings,
                     *, receipt_store: ReceiptStore | None = None) -> FastAPI:
    if config.root != settings.storage.data_dir or (config.root / "app.db").exists():
        raise ValueError("Cloud mode requires a new dedicated DATA_DIR=BETA_ROOT, without app.db")
    if (config.root / ".restore-incomplete").exists():
        raise ValueError("Incomplete restore cannot be served")
    paid = (settings.ocr.provider == "openai" or settings.voice.provider != "disabled"
            or settings.ocr.openai_api_key is not None)
    if paid and not config.ai_enabled:
        raise ValueError("Cloud paid AI requires explicit activation and the shared beta journal")

    async def own_runtime(stack: AsyncExitStack) -> Any:
        # Wait until the previous task (rolling update) has stopped and released the lease.
        deadline = time.monotonic() + HANDOFF_LEASE_WAIT
        while True:
            try:
                return stack.enter_context(cloud_runtime(config, serving=True))
            except (RuntimeError, OSError) as error:
                if not lease_busy(error) or time.monotonic() > deadline:
                    raise
                await asyncio.sleep(1)

    async def serve(stop: asyncio.Event) -> None:
        async with AsyncExitStack() as stack:
            control = await own_runtime(stack)
            registry = Registry(config.root, config.max_users, control=control)
            operations = None
            if config.journal_bucket:
                store = receipt_store or S3Receipts(
                    s3_client(), config.journal_bucket, str(config.installation_id),
                )
                journal = Journal(config.root, str(config.installation_id), store, control=control)
                await asyncio.to_thread(journal.reconcile)
                operations = Operations(settings, config, registry, journal)
                await asyncio.to_thread(operations.replay_deletions)
            app.state.operations = operations
            client = await stack.enter_async_context(httpx.AsyncClient(
                timeout=10, follow_redirects=False, trust_env=False,
            ))
            app.state.registry = registry
            app.state.cognito = Cognito(config, client)
            app.state.tenants = TenantApplications(settings, config, registry, operations)
            try:
                await app.state.tenants.start()
                app.state.ready.set()
                await stop.wait()
            finally:
                await app.state.tenants.close()

    @asynccontextmanager
    async def lifespan(app: FastAPI):  # type: ignore[no-untyped-def]
        configure_logging(settings.logging, bounded=True)
        app.state.ready = asyncio.Event()
        stop = asyncio.Event()
        runtime = asyncio.create_task(serve(stop))

        def failed(task: asyncio.Task[None]) -> None:
            # Never keep answering health checks without a runtime: let ECS replace the task.
            if not task.cancelled() and task.exception() is not None:
                logging.getLogger(__name__).error("Cloud runtime failed", exc_info=task.exception())
                os.kill(os.getpid(), signal.SIGTERM)

        runtime.add_done_callback(failed)
        try:
            yield
        finally:
            runtime.remove_done_callback(failed)
            stop.set()
            if not app.state.ready.is_set():
                runtime.cancel()
            await asyncio.gather(runtime, return_exceptions=True)

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)

    # Session token hash -> when to look at it again (renewal through the refresh token).
    renew_at: dict[str, float] = {}
    renew_locks: dict[str, asyncio.Lock] = {}

    async def renew(token: str) -> None:
        """Keep a long session only while Cognito still accepts its refresh token.

        Every ID token lifetime (an hour) the refresh token is exchanged for a new verified ID
        token, so a user disabled or signed out at Cognito loses the session within the hour.
        """
        if len(token) != 43:
            return
        key = hashlib.sha256(token.encode()).hexdigest()
        if renew_at.get(key, 0.0) > time.time():
            return
        lock = renew_locks.setdefault(key, asyncio.Lock())
        async with lock:
            if renew_at.get(key, 0.0) > time.time():
                return
            registry = app.state.registry
            state = await asyncio.to_thread(registry.renewal, token)
            if state is None:
                renew_at.pop(key, None)
                return
            when, refresh_token, subject = state
            if when - 60 > time.time() or refresh_token is None:
                # Sessions without a refresh token simply end at their expiry.
                renew_at[key] = min(when - 60, time.time() + 300)
                return
            try:
                login = await app.state.cognito.refresh(refresh_token)
                if login.subject != subject:
                    raise AccessDenied
            except CognitoUnavailable:
                logging.getLogger(__name__).warning("Session renewal deferred: Cognito unavailable")
                renew_at[key] = time.time() + 60
                return
            except AccessDenied:
                await asyncio.to_thread(registry.revoke, token)
                renew_at.pop(key, None)
                return
            await asyncio.to_thread(registry.renew, token, login.lifetime, login.refresh_token)
            renew_at[key] = time.time() + min(login.lifetime - 60, 300)
        if len(renew_locks) > 1000:
            renew_locks.clear()
            for stale in [k for k, t in renew_at.items() if t <= time.time()]:
                renew_at.pop(stale, None)

    @app.middleware("http")
    async def boundary(request: Request, call_next: Any):  # type: ignore[no-untyped-def]
        # Never trust forwarded host headers to construct callback or tenant identity.
        if str(request.base_url).rstrip("/") != config.public_origin:
            return problem("ORIGIN_REJECTED", 403)
        if request.url.path != "/api/health" and not await runtime_ready(app):
            return problem("RUNTIME_STARTING", 503)
        if request.url.path not in {"/api/health", "/api/auth/logout"}:
            await renew(request.cookies.get(SESSION, ""))
        response = await call_next(request)
        # Only a browser-private image cache the owner app allowed (see private_send).
        cache = response.headers.get("Cache-Control", "")
        if not (cache.startswith("private, max-age") and "public" not in cache):
            response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.get("/api/auth/session")
    async def session(request: Request) -> JSONResponse:
        try:
            identity = app.state.registry.authenticate(request.cookies.get(SESSION, ""))
            return JSONResponse({"mode": "cognito", "authenticated": True,
                                 "csrfToken": identity.csrf, "storageScope": identity.tenant_id,
                                 "admin": config.is_admin(identity.subject)})
        except AccessDenied:
            return JSONResponse({"mode": "cognito", "authenticated": False})

    @app.get("/api/admin/correction-rates")
    async def correction_rates(request: Request) -> JSONResponse:
        """Every user's correction rate, for the admin only (each user's database is read)."""
        try:
            identity = app.state.registry.authenticate(request.cookies.get(SESSION, ""))
        except AccessDenied:
            return problem("AUTH_REQUIRED", 401)
        if not config.is_admin(identity.subject):
            return problem("ADMIN_REQUIRED", 403)
        if app.state.operations is None:
            return problem("ADMIN_UNAVAILABLE", 503)
        users = await asyncio.to_thread(
            everyone_correction_rates, app.state.registry, app.state.operations
        )
        return JSONResponse({"users": users}, headers={"Cache-Control": "no-store"})

    @app.get("/api/auth/login")
    async def login() -> RedirectResponse:
        state, browser, verifier, nonce = app.state.registry.begin_login()
        challenge = base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode()).digest(),
        ).rstrip(b"=")
        response = RedirectResponse(config.cognito_domain + "/oauth2/authorize?" + urlencode({
            "response_type": "code", "client_id": config.cognito_client_id,
            "redirect_uri": config.callback, "scope": "openid", "state": state, "nonce": nonce,
            "code_challenge": challenge.decode(), "code_challenge_method": "S256",
            "prompt": "login",
        }))
        response.set_cookie(
            LOGIN, browser, max_age=300, secure=True, httponly=True, samesite="lax",
        )
        return response

    @app.get("/api/auth/callback", response_model=None)
    async def callback(request: Request) -> JSONResponse | RedirectResponse:
        try:
            state = request.query_params.get("state", "")
            code = request.query_params.get("code", "")
            if len(state) != 43 or not code or len(code) > 4096:
                raise AccessDenied
            verifier, nonce = app.state.registry.consume_login(
                state, request.cookies.get(LOGIN, ""),
            )
            login = await app.state.cognito.identity(code, verifier, nonce)
            token, _, lifetime = app.state.registry.issue(
                login.subject, login.lifetime, login.refresh_token, config.session_keep_seconds,
            )
        except AccessDenied:
            return problem("LOGIN_REJECTED", 401)
        app.state.registry.revoke(request.cookies.get(SESSION, ""))
        response = RedirectResponse(config.public_origin + "/", status_code=303)
        response.delete_cookie(LOGIN, secure=True, httponly=True, samesite="lax")
        response.set_cookie(
            SESSION, token, max_age=lifetime, secure=True, httponly=True, samesite="lax",
        )
        return response

    # Failed password logins per client address and per user name, in the last 5 minutes.
    failures: dict[str, list[float]] = {}

    def throttled(*keys: str) -> bool:
        now = time.monotonic()
        for key in keys:
            failures[key] = [t for t in failures.get(key, []) if now - t < 300]
        return any(len(failures[key]) >= 5 for key in keys)

    @app.post("/api/auth/password")
    async def password_login(request: Request) -> JSONResponse:
        """The app's own login page; no Cognito-hosted session is left behind on logout."""
        if request.headers.get("origin") != config.public_origin:
            return problem("CSRF_REJECTED", 403)
        try:
            body = await request.json()
            username = str(body.get("username", "")).strip()
            password = str(body.get("password", ""))
        except (ValueError, AttributeError):
            return problem("LOGIN_REJECTED", 400)
        if not (0 < len(username) <= 128) or not (0 < len(password) <= 256):
            return problem("LOGIN_REJECTED", 400)
        keys = ("ip:" + (request.client.host if request.client else ""), "user:" + username.lower())
        if throttled(*keys):
            detail = "失敗が続いたため、5分ほど待ってからもう一度ログインしてください。"
            return JSONResponse({"code": "LOGIN_THROTTLED", "detail": detail},
                                status_code=429, headers={"Cache-Control": "no-store"})
        try:
            login = await app.state.cognito.password_identity(username, password)
            token, identity, lifetime = app.state.registry.issue(
                login.subject, login.lifetime, login.refresh_token, config.session_keep_seconds,
            )
        except AccessDenied:
            for key in keys:
                failures[key].append(time.monotonic())
            return JSONResponse({"code": "LOGIN_REJECTED",
                                 "detail": "ユーザー名またはパスワードが違います。"},
                                status_code=401, headers={"Cache-Control": "no-store"})
        app.state.registry.revoke(request.cookies.get(SESSION, ""))
        response = JSONResponse({"mode": "cognito", "authenticated": True,
                                 "csrfToken": identity.csrf, "storageScope": identity.tenant_id})
        response.set_cookie(
            SESSION, token, max_age=lifetime, secure=True, httponly=True, samesite="lax",
        )
        return response

    @app.post("/api/auth/logout")
    async def logout(request: Request) -> JSONResponse:
        token = request.cookies.get(SESSION, "")
        try:
            identity = app.state.registry.authenticate(token)
            if request.headers.get("origin") != config.public_origin or not secrets.compare_digest(
                request.headers.get("x-csrf-token", ""), identity.csrf,
            ):
                return problem("CSRF_REJECTED", 403)
        except AccessDenied:
            return problem("AUTH_REQUIRED", 401)
        state = app.state.registry.renewal(token)
        app.state.registry.revoke(token)
        if state is not None and state[1] is not None:
            await app.state.cognito.revoke(state[1])
        # Cached document images stay in this browser until logout (see private_send).
        response = JSONResponse({"authenticated": False}, headers={"Clear-Site-Data": '"cache"'})
        response.delete_cookie(SESSION, secure=True, httponly=True, samesite="lax")
        return response

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        # Healthy while waiting for the lease, so ECS can stop the previous task (rolling update).
        if not app.state.ready.is_set():
            return {"status": "starting"}
        with app.state.registry.connect() as db:
            db.execute("SELECT 1")
        return {"status": "ok"}

    app.mount("/", TenantGateway(app, config))

    def ready() -> bool:
        event = getattr(app.state, "ready", None)
        return event is not None and event.is_set()

    # Outermost: until this task owns the runtime, relay to the previous ECS task.
    app.add_middleware(HandoffRelay, ready=ready,
                       previous=PreviousTask(config.ecs_service, config.relay_port))
    return app
