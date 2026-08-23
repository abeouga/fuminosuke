from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, cast

from fastapi import APIRouter, Query, Request, Response, status
from pydantic import Field

from app.core.errors import ApiModel
from app.domain.ocr_models import OcrDocument
from app.services.document_library_service import DocumentLibraryService
from app.services.individual_record_service import IndividualRecordService

router = APIRouter(tags=["individual-records"])


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
