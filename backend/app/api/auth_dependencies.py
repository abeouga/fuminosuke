from __future__ import annotations

from fastapi import Request

from app.core.errors import AppError
from app.services.google_oauth_service import AuthenticatedUser, GoogleOAuthService


def require_authenticated_user(request: Request) -> AuthenticatedUser | None:
    service: GoogleOAuthService = request.app.state.google_oauth
    if not service.enabled:
        return None
    user = service.current_user(request.cookies.get(service.session_cookie_name))
    if user is None:
        raise AppError(
            status=401,
            code="AUTHENTICATION_REQUIRED",
            title="Authentication required",
            detail="Googleアカウントでログインしてください。",
        )
    return user
