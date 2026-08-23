from __future__ import annotations

from typing import Annotated
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse

from app.api.auth_dependencies import require_authenticated_user
from app.core.errors import AppError
from app.services.google_oauth_service import AuthenticatedUser, GoogleOAuthService

router = APIRouter(prefix="/auth", tags=["auth"])


def _service(request: Request) -> GoogleOAuthService:
    return request.app.state.google_oauth  # type: ignore[no-any-return]


@router.get("/session")
def auth_session(request: Request) -> dict[str, object]:
    service = _service(request)
    user = service.current_user(request.cookies.get(service.session_cookie_name))
    return {
        "required": service.enabled,
        "configured": service.configured,
        "authenticated": user is not None,
        "authorizationCurrent": (
            not service.enabled
            or (user is not None and service.authorization_is_current(user.subject))
        ),
        "user": user.payload() if user is not None else None,
    }


@router.get("/google/start")
def google_start(
    request: Request,
    return_to: Annotated[str | None, Query(alias="returnTo")] = None,
) -> RedirectResponse:
    service = _service(request)
    started = service.begin(return_to)
    response = RedirectResponse(started.authorization_url, status_code=302)
    response.set_cookie(
        service.state_cookie_name,
        started.state_cookie,
        max_age=service.state_ttl_seconds,
        httponly=True,
        secure=service.secure_cookie,
        samesite="lax",
        path="/api/v1/auth/google/callback",
    )
    return response


@router.get("/google/callback")
def google_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> RedirectResponse:
    service = _service(request)
    if error or not code or not state:
        return _oauth_failure_response(service, "GOOGLE_OAUTH_CANCELLED")
    try:
        completed = service.complete(
            code=code,
            state=state,
            state_cookie=request.cookies.get(service.state_cookie_name),
        )
    except AppError as exc:
        return _oauth_failure_response(service, exc.code)
    response = RedirectResponse(completed.return_to, status_code=302)
    response.set_cookie(
        service.session_cookie_name,
        completed.session_token,
        max_age=service.session_ttl_seconds,
        httponly=True,
        secure=service.secure_cookie,
        samesite="lax",
        path="/",
    )
    response.delete_cookie(service.state_cookie_name, path="/api/v1/auth/google/callback")
    request.app.state.google_sheets.reset_authorization()
    request.app.state.google_drive.reset_authorization()
    return response


def _oauth_failure_response(service: GoogleOAuthService, code: str) -> RedirectResponse:
    parsed = urlparse(service.frontend_url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query["authError"] = code
    location = urlunparse(parsed._replace(query=urlencode(query)))
    response = RedirectResponse(location, status_code=302)
    response.delete_cookie(service.state_cookie_name, path="/api/v1/auth/google/callback")
    return response


@router.post("/logout")
def logout(request: Request) -> JSONResponse:
    service = _service(request)
    response = JSONResponse({"authenticated": False})
    response.delete_cookie(service.session_cookie_name, path="/")
    return response


@router.post("/google/disconnect")
def disconnect_google(
    request: Request,
    user: Annotated[AuthenticatedUser | None, Depends(require_authenticated_user)],
) -> JSONResponse:
    service = _service(request)
    if user is not None:
        service.disconnect(user)
    request.app.state.google_sheets.reset_authorization()
    request.app.state.google_drive.reset_authorization()
    response = JSONResponse({"authenticated": False})
    response.delete_cookie(service.session_cookie_name, path="/")
    return response
