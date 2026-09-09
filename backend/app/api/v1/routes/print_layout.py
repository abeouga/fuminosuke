from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query, Request, Response

from app.domain.print_layout import PrintLayoutRequest
from app.layout_analysis.models import LayoutDefinition
from app.services.print_layout_service import PrintLayoutService
from app.services.reference_qualification_service import ReferenceQualificationService
from app.services.template_print_service import TemplatePrintService

router = APIRouter(prefix="/template-versions/{version_id}/print-layout", tags=["templates"])


@router.get("/files/{format}")
def printable_file(
    request: Request,
    version_id: str,
    format: Literal["xlsx", "pdf"],
    base_revision: Annotated[str, Query(min_length=64, max_length=64)],
) -> Response:
    content = TemplatePrintService(request.app.state.database, request.app.state.storage).export(
        version_id, format, base_revision
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
    return ReferenceQualificationService(
        request.app.state.database, request.app.state.storage).status(version_id)


@router.post("/qualification")
def qualify_reference(
    request: Request, version_id: str,
    base_revision: Annotated[str, Query(min_length=64, max_length=64)],
) -> dict[str, Any]:
    return ReferenceQualificationService(
        request.app.state.database, request.app.state.storage).qualify(version_id, base_revision)


def _service(request: Request) -> PrintLayoutService:
    return PrintLayoutService(request.app.state.database, request.app.state.storage)


@router.get("")
def get_layout(request: Request, version_id: str) -> dict[str, Any]:
    return _service(request).get(version_id)


@router.post("/preview")
def preview_layout(request: Request, version_id: str, body: PrintLayoutRequest) -> dict[str, Any]:
    return _service(request).preview(version_id, body)


@router.put("")
def save_layout(request: Request, version_id: str, body: PrintLayoutRequest) -> dict[str, Any]:
    return _service(request).save(version_id, body)


@router.delete("")
def clear_layout(
    request: Request,
    version_id: str,
    base_revision: Annotated[str, Query(min_length=64, max_length=64)],
) -> dict[str, Any]:
    return _service(request).clear(version_id, base_revision)


@router.put("/authoring")
def save_authoring(
    request: Request, version_id: str, body: LayoutDefinition,
    base_revision: Annotated[str, Query(min_length=64, max_length=64)],
) -> dict[str, Any]:
    return _service(request).save_authoring(version_id, base_revision, body)


@router.get("/pages/{page_number}/image")
def reference_image(request: Request, version_id: str, page_number: int) -> Response:
    return Response(
        _service(request).image(version_id, page_number),
        media_type="image/png",
        headers={"Cache-Control": "no-store"},
    )
