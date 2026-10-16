from app.domain.ocr_factory import blank_ocr_document
from app.ocr.base import OcrProviderRequest, OcrProviderResponse


class DisabledOcrProvider:
    async def extract(self, request: OcrProviderRequest) -> OcrProviderResponse:
        regions = [item.region_id for item in request.regions]
        return OcrProviderResponse(
            document=blank_ocr_document(provider="disabled", source_regions=regions),
            provider="disabled",
            model_id=None,
            prompt_version=None,
            api_call_count=0,
        )
