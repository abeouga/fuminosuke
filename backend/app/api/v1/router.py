from fastapi import APIRouter

from app.api.v1.routes import (
    artifacts,
    assistant,
    editing,
    editing_voice,
    health,
    job_pages,
    jobs,
    ocr,
    print_layout,
    template_packages,
    templates,
)

router = APIRouter(prefix="/api/v1")
for module in (
    health,
    templates,
    print_layout,
    template_packages,
    ocr,
    job_pages,
    jobs,
    artifacts,
    assistant,
    editing,
    editing_voice,
):
    router.include_router(module.router)
