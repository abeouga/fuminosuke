from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.error_handlers import register_error_handlers
from app.api.v1.router import router as v1_router
from app.api.v2.editing import router as v2_editing_router
from app.api.v2.editing import voice_router
from app.api.v2.structured import router as structured_router
from app.api.v2.structured import voice_router as structured_voice_router
from app.api.v2.template_analysis import router as template_analysis_router
from app.core.config import Settings, get_settings
from app.core.lifecycle import application_lifespan


def create_business_app(settings: Settings | None = None) -> FastAPI:
    resolved_settings = settings or get_settings()
    app = FastAPI(
        title="Document OCR Local API",
        version="0.1.0",
        docs_url=None if resolved_settings.app.environment == "cloud" else "/docs",
        openapi_url=None if resolved_settings.app.environment == "cloud" else "/openapi.json",
        redoc_url=None,
        lifespan=application_lifespan,
    )
    app.state.settings = resolved_settings
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(resolved_settings.app.frontend_origins),
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=[
            "Accept",
            "Content-Type",
            "If-Match",
            "Idempotency-Key",
            "X-Assistant-Session",
            "X-Editing-Session",
            "Last-Event-ID",
        ],
        expose_headers=["X-Template-Revision"],
    )
    register_error_handlers(app)
    app.include_router(v1_router)
    app.include_router(v2_editing_router)
    app.include_router(voice_router)
    app.include_router(template_analysis_router)
    app.include_router(structured_router)
    app.include_router(structured_voice_router)
    if resolved_settings.app.environment != "cloud":
        @app.get("/api/auth/session")
        async def local_auth_session() -> dict[str, object]:
            return {"mode": "local", "authenticated": True, "admin": True}
    return app


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved = settings or get_settings()
    if resolved.app.environment == "cloud":
        from app.cloud.application import create_cloud_app
        from app.cloud.config import get_cloud_settings

        return create_cloud_app(resolved, get_cloud_settings())
    return create_business_app(resolved)


app = create_app()
