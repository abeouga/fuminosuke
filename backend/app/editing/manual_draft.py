from pydantic import Field

from app.domain.ocr_models import FieldReading, ReviewItem
from app.domain.template_config import DocumentIdentityConfig, DocumentNameConfig
from app.editing.operation_contracts import Contract
from app.layout_analysis.models import LayoutDefinition


class ManualDraft(Contract):
    layout: LayoutDefinition
    document_identity_fields: list[DocumentIdentityConfig] = Field(max_length=20)
    document_name: DocumentNameConfig | None


class ManualOcrDraft(Contract):
    fields: dict[str, FieldReading] = Field(max_length=1000)
    review_items: list[ReviewItem] = Field(max_length=1000)
