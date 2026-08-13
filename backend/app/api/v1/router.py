from fastapi import APIRouter

from app.api.v1.routes import (
    artifacts,
    health,
    individual_records,
    jobs,
    ocr,
    sire_names,
    templates,
)

router = APIRouter(prefix="/api/v1")
router.include_router(health.router)
router.include_router(templates.router)
router.include_router(ocr.router)
router.include_router(sire_names.router)
router.include_router(individual_records.router)
router.include_router(jobs.router)
router.include_router(artifacts.router)
