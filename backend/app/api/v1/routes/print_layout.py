from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Request, Response

from app.api.template_revision import template_revision_guard
from app.domain.print_layout import PrintLayoutRequest, print_error
from app.services.candidate_readiness_service import CandidateReadinessService
from app.services.print_layout_service import PrintLayoutService
from app.services.print_preparation_service import PrintPreparationService
from app.services.reference_qualification_service import ReferenceQualificationService
from app.services.template_print_service import TemplatePrintService

router = APIRouter(prefix="/template-versions/{version_id}/print-layout", tags=["templates"])


@router.post("/prepare", dependencies=[Depends(template_revision_guard)])
def prepare_print_files(
    request: Request, version_id: str,
    base_revision: Annotated[str, Query(min_length=64, max_length=64)],
    geometry_model: Literal["wsl-calc-print-grid-v1"] | None = None,
) -> dict[str, Any]:
    backend = request.app.state.settings.app.print_pdf_renderer
    if backend not in {"wsl", "linux"}:
        raise print_error("隔離された印刷用変換環境を設定してください。",
                          "RENDERER_UNAVAILABLE", 409)
    selected_geometry = ((geometry_model or "wsl-calc-print-grid-v1")
                         if backend == "wsl" else geometry_model)
    return PrintPreparationService(request.app.state.database, request.app.state.storage).prepare(
        version_id, base_revision, request.app.state.settings.app.print_wsl_distribution,
        runtime_id=request.app.state.settings.app.print_wsl_runtime_id,
        geometry_model=selected_geometry, backend=backend)


@router.get("/files/{format}")
def printable_file(
    request: Request,
    version_id: str,
    format: Literal["xlsx", "pdf"],
    base_revision: Annotated[str, Query(min_length=64, max_length=64)],
) -> Response:
    content = TemplatePrintService(request.app.state.database, request.app.state.storage).export(
        version_id, format, base_revision,
        pdf_renderer=request.app.state.settings.app.print_pdf_renderer,
        wsl_distribution=request.app.state.settings.app.print_wsl_distribution,
    )
    return Response(
        content,
        media_type=(
            "application/pdf"
            if format == "pdf"
            else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        ),
        headers={
            "Cache-Control": "no-store",
            "Content-Disposition": f'attachment; filename="print-{base_revision[:12]}.{format}"',
            "X-Print-Layout-Revision": base_revision,
            "X-Renderer-Qualified": "false",
        },
    )


@router.get("/qualification")
def qualification_status(request: Request, version_id: str) -> dict[str, Any]:
    return CandidateReadinessService(
        request.app.state.database, request.app.state.storage).status_for_use(version_id)


@router.post("/candidate-readiness", dependencies=[Depends(template_revision_guard)])
def prepare_candidate_readiness(
    request: Request, version_id: str,
    base_revision: Annotated[str, Query(min_length=64, max_length=64)],
) -> dict[str, Any]:
    return CandidateReadinessService(
        request.app.state.database, request.app.state.storage).prepare_candidate(
            version_id, base_revision, request.app.state.settings.app.print_wsl_distribution)


@router.post("/ocr-readiness", dependencies=[Depends(template_revision_guard)])
def prepare_reviewed_ocr(
    request: Request, version_id: str,
    base_revision: Annotated[str, Query(min_length=64, max_length=64)],
) -> dict[str, Any]:
    return CandidateReadinessService(
        request.app.state.database, request.app.state.storage).prepare_for_ocr(
            version_id, base_revision)


@router.post("/qualification", dependencies=[Depends(template_revision_guard)])
def qualify_reference(
    request: Request, version_id: str,
    base_revision: Annotated[str, Query(min_length=64, max_length=64)],
) -> dict[str, Any]:
    return ReferenceQualificationService(
        request.app.state.database, request.app.state.storage).qualify(
            version_id, base_revision,
            wsl_distribution=request.app.state.settings.app.print_wsl_distribution)


def _service(request: Request) -> PrintLayoutService:
    return PrintLayoutService(request.app.state.database, request.app.state.storage)


@router.get("")
def get_layout(request: Request, version_id: str) -> dict[str, Any]:
    return _service(request).get(version_id)


@router.post("/preview")
def preview_layout(request: Request, version_id: str, body: PrintLayoutRequest) -> dict[str, Any]:
    return _service(request).preview(version_id, body)


@router.put("", dependencies=[Depends(template_revision_guard)])
def save_layout(request: Request, version_id: str, body: PrintLayoutRequest) -> dict[str, Any]:
    return _service(request).save(version_id, body)


@router.delete("", dependencies=[Depends(template_revision_guard)])
def clear_layout(
    request: Request,
    version_id: str,
    base_revision: Annotated[str, Query(min_length=64, max_length=64)],
) -> dict[str, Any]:
    return _service(request).clear(version_id, base_revision)




@router.get("/pages/{page_number}/image")
def reference_image(request: Request, version_id: str, page_number: int) -> Response:
    return Response(
        _service(request).image(version_id, page_number),
        media_type="image/png",
        headers={"Cache-Control": "no-store"},
    )
