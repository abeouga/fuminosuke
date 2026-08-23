from fastapi import APIRouter, Depends

from app.api.auth_dependencies import require_authenticated_user
from app.api.v1.routes import (
    artifacts,
    auth,
    data_search,
    development,
    drive,
    health,
    individual_records,
    jobs,
    ocr,
    sire_names,
    templates,
)

router = APIRouter(prefix="/api/v1")
router.include_router(health.router)
router.include_router(auth.router)
router.include_router(development.router)
protected = APIRouter(dependencies=[Depends(require_authenticated_user)])
protected.include_router(templates.router)
protected.include_router(ocr.router)
protected.include_router(data_search.router)
protected.include_router(sire_names.router)
protected.include_router(individual_records.router)
protected.include_router(jobs.router)
protected.include_router(artifacts.router)
protected.include_router(drive.router)
router.include_router(protected)
