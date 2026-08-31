from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal, cast

from fastapi import APIRouter, Query, Request, Response, status
from pydantic import Field

from app.core.errors import ApiModel
from app.domain.ocr_models import OcrDocument
from app.services.document_library_service import DocumentLibraryService
from app.services.individual_record_service import IndividualRecordService
from app.services.print_preview_service import PrintPreviewService
from app.services.windows_printer_service import WindowsPrinterService

router = APIRouter(tags=["individual-records"])
windows_printer = WindowsPrinterService()
print_preview = PrintPreviewService()


class IndividualRecord(ApiModel):
    record_id: str
    individual_id: str
    source_job_id: str
    ocr_result_revision: int
    confirmed_at: datetime
    data: OcrDocument
    needs_review: bool
    review_warnings: list[str]


class IndividualRecordSearchResponse(ApiModel):
    query: str
    individual_id: str
    count: int
    records: list[IndividualRecord]


class IndividualRecordSummary(ApiModel):
    individual_id: str
    cattle_name: str | None
    record_count: int
    last_confirmed_at: datetime
    needs_review: bool


class IndividualRecordCatalogResponse(ApiModel):
    count: int
    items: list[IndividualRecordSummary]


class SaleCalfRecordMatch(ApiModel):
    record_field_id: str
    confirmed_result_id: str
    individual_id: str | None
    source_job_id: str
    confirmed_at: datetime
    section_key: str
    section_title: str
    record_order: int
    record_key: str | None
    matched_value: str | None
    data: dict[str, Any]
    review_warnings: list[str]


class SaleCalfSireSearchResponse(ApiModel):
    query: str
    normalized_query: str
    count: int
    matches: list[SaleCalfRecordMatch]


class DocumentFolderCreate(ApiModel):
    name: str = Field(min_length=1, max_length=100)
    parent_id: str | None = None


class DocumentFolderRename(ApiModel):
    name: str = Field(min_length=1, max_length=100)


class DocumentLocationUpdate(ApiModel):
    folder_id: str | None = None
    expected_version: int = Field(ge=1)


class DocumentRename(ApiModel):
    name: str = Field(min_length=1, max_length=180)


class DocumentBulkDelete(ApiModel):
    document_ids: list[str] = Field(min_length=1, max_length=100)


class DocumentPrint(ApiModel):
    mode: Literal["spreadsheet", "image"]
    copies: int = Field(default=1, ge=1, le=3)
    paper_size: Literal["A4", "A5", "B5", "LETTER"] = "A4"
    orientation: Literal["portrait", "landscape"] = "portrait"
    fit_mode: Literal["contain", "shrink"] = "contain"
    margin_mm: int = Field(default=10, ge=0, le=30)


@router.get(
    "/individual-records/catalog",
    response_model=IndividualRecordCatalogResponse,
    response_model_by_alias=True,
)
def list_individual_records(request: Request) -> dict[str, Any]:
    service = cast(IndividualRecordService, request.app.state.individual_record_service)
    return service.list_individuals()


@router.get("/document-library")
def document_library(request: Request) -> dict[str, Any]:
    service = cast(DocumentLibraryService, request.app.state.document_library_service)
    return service.list_library()


@router.get("/printer/status")
def printer_status() -> dict[str, Any]:
    return windows_printer.status()


@router.post("/document-library/folders", status_code=status.HTTP_201_CREATED)
def create_document_folder(request: Request, body: DocumentFolderCreate) -> dict[str, Any]:
    service = cast(DocumentLibraryService, request.app.state.document_library_service)
    return service.create_folder(name=body.name, parent_id=body.parent_id)


@router.patch("/document-library/folders/{folder_id}")
def rename_document_folder(
    request: Request,
    folder_id: str,
    body: DocumentFolderRename,
) -> dict[str, Any]:
    service = cast(DocumentLibraryService, request.app.state.document_library_service)
    return service.rename_folder(folder_id, name=body.name)


@router.delete(
    "/document-library/folders/{folder_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_document_folder(request: Request, folder_id: str) -> Response:
    service = cast(DocumentLibraryService, request.app.state.document_library_service)
    service.delete_folder(folder_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.patch("/document-library/documents/{document_id}/location")
def move_library_document(
    request: Request,
    document_id: str,
    body: DocumentLocationUpdate,
) -> dict[str, Any]:
    service = cast(DocumentLibraryService, request.app.state.document_library_service)
    return service.move_document(
        document_id,
        folder_id=body.folder_id,
        expected_version=body.expected_version,
    )


@router.patch("/document-library/documents/{document_id}")
def rename_library_document(
    request: Request,
    document_id: str,
    body: DocumentRename,
) -> dict[str, Any]:
    service = cast(DocumentLibraryService, request.app.state.document_library_service)
    return service.rename_document(document_id, name=body.name)


@router.get("/document-library/documents/{document_id}/spreadsheet-print")
def print_library_document_spreadsheet(request: Request, document_id: str) -> Response:
    service = cast(DocumentLibraryService, request.app.state.document_library_service)
    content = service.export_spreadsheet_pdf(document_id)
    return Response(
        content=content,
        media_type="application/pdf",
        headers={"Content-Disposition": "inline"},
    )


@router.get("/document-library/documents/{document_id}/print-preview")
def preview_library_document(
    request: Request,
    document_id: str,
    mode: Annotated[Literal["spreadsheet", "image"], Query()],
    paper_size: Annotated[
        Literal["A4", "A5", "B5", "LETTER"],
        Query(alias="paperSize"),
    ] = "A4",
    orientation: Annotated[Literal["portrait", "landscape"], Query()] = "portrait",
    fit_mode: Annotated[
        Literal["contain", "shrink"],
        Query(alias="fitMode"),
    ] = "contain",
    margin_mm: Annotated[int, Query(alias="marginMm", ge=0, le=30)] = 10,
) -> Response:
    service = cast(DocumentLibraryService, request.app.state.document_library_service)
    if mode == "spreadsheet":
        source = service.export_spreadsheet_pdf(
            document_id,
            paper_size=paper_size,
            orientation=orientation,
        )
        content = print_preview.from_pdf(
            source,
            paper_size=paper_size,
            orientation=orientation,
            fit_mode=fit_mode,
            margin_mm=margin_mm,
            single_page=True,
        )
    else:
        source, _ = service.first_page_source(document_id)
        content = print_preview.from_image(
            source,
            paper_size=paper_size,
            orientation=orientation,
            fit_mode=fit_mode,
            margin_mm=margin_mm,
        )
    return Response(
        content=content,
        media_type="application/pdf",
        headers={
            "Content-Disposition": "inline",
            "Cache-Control": "no-store",
        },
    )


@router.post("/document-library/documents/{document_id}/print")
def print_library_document(
    request: Request,
    document_id: str,
    body: DocumentPrint,
) -> dict[str, Any]:
    service = cast(DocumentLibraryService, request.app.state.document_library_service)
    if body.mode == "spreadsheet":
        source = service.export_spreadsheet_pdf(
            document_id,
            paper_size=body.paper_size,
            orientation=body.orientation,
        )
        content = print_preview.from_pdf(
            source,
            paper_size=body.paper_size,
            orientation=body.orientation,
            fit_mode=body.fit_mode,
            margin_mm=body.margin_mm,
            single_page=True,
        )
    else:
        source, _ = service.first_page_source(document_id)
        content = print_preview.from_image(
            source,
            paper_size=body.paper_size,
            orientation=body.orientation,
            fit_mode=body.fit_mode,
            margin_mm=body.margin_mm,
        )
    return windows_printer.print_bytes(content, suffix=".pdf", copies=body.copies)


@router.post("/document-library/documents/bulk-delete")
def delete_library_documents(
    request: Request,
    body: DocumentBulkDelete,
) -> dict[str, int | bool]:
    service = cast(DocumentLibraryService, request.app.state.document_library_service)
    return service.delete_documents(body.document_ids)


@router.get(
    "/individual-records",
    response_model=IndividualRecordSearchResponse,
    response_model_by_alias=True,
)
def search_individual_records(
    request: Request,
    individual_id: Annotated[str, Query(alias="individualId", min_length=1, max_length=50)],
) -> dict[str, Any]:
    service = cast(IndividualRecordService, request.app.state.individual_record_service)
    return service.search(individual_id)


@router.get(
    "/sale-calf-records",
    response_model=SaleCalfSireSearchResponse,
    response_model_by_alias=True,
)
def search_sale_calf_records(
    request: Request,
    sire_name: Annotated[str, Query(alias="sireName", min_length=1, max_length=200)],
) -> dict[str, Any]:
    service = cast(IndividualRecordService, request.app.state.individual_record_service)
    return service.search_sale_calves_by_sire(sire_name)
