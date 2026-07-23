from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.error_handlers import register_error_handlers
from app.api.v1.router import router as v1_router
from app.core.config import Settings, get_settings
from app.core.lifecycle import application_lifespan


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved_settings = settings or get_settings()
    app = FastAPI(
        title="Cow Ledger OCR Local API",
        version="0.1.0",
        docs_url="/docs",
        redoc_url=None,
        lifespan=application_lifespan,
    )
    app.state.settings = resolved_settings
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(resolved_settings.app.frontend_origins),
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Accept", "Content-Type", "If-Match", "Idempotency-Key"],
    )
    register_error_handlers(app)
    app.include_router(v1_router)
    return app


app = create_app()

