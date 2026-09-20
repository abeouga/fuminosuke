from pydantic import Field

from app.domain.ocr_models import OcrModel, ReadingStatus


class PageReadingEdit(OcrModel):
    # Identity and crop are server-owned, never accepted as editable properties.
    mapping_id: str = Field(min_length=1, max_length=220)
    raw: str | None = Field(default=None, max_length=500)
    normalized: str | None = Field(default=None, max_length=500)
    status: ReadingStatus
    alternatives: list[str] = Field(default_factory=list, max_length=10)
    review_reason: str | None = Field(default=None, max_length=100)


class PageCorrection(OcrModel):
    base_revision: int = Field(ge=1)
    fields: list[PageReadingEdit] = Field(min_length=1, max_length=1000)


class PageConfirmation(OcrModel):
    base_revision: int = Field(ge=1)
    reviewed_page_ids: list[str] = Field(min_length=1, max_length=100)
    document_name: str = Field(min_length=1, max_length=180)


class PageReopen(OcrModel):
    base_revision: int = Field(ge=1)
