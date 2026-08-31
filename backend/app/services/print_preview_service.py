from __future__ import annotations

from io import BytesIO
from typing import Literal

from PIL import Image, ImageChops, ImageOps
import pypdfium2 as pdfium

from app.core.errors import AppError

PaperSize = Literal["A4", "A5", "B5", "LETTER"]
Orientation = Literal["portrait", "landscape"]
FitMode = Literal["contain", "shrink"]


class PrintPreviewService:
    _DPI = 200
    _PAPER_MM: dict[str, tuple[float, float]] = {
        "A4": (210.0, 297.0),
        "A5": (148.0, 210.0),
        "B5": (182.0, 257.0),
        "LETTER": (215.9, 279.4),
    }

    def from_pdf(
        self,
        content: bytes,
        *,
        paper_size: PaperSize,
        orientation: Orientation,
        fit_mode: FitMode,
        margin_mm: int,
        single_page: bool = False,
    ) -> bytes:
        try:
            document = pdfium.PdfDocument(content)
        except Exception as exc:
            raise self._invalid_source() from exc
        sources: list[Image.Image] = []
        try:
            for index in range(len(document)):
                page = document[index]
                bitmap = page.render(scale=self._DPI / 72)
                try:
                    sources.append(bitmap.to_pil().convert("RGB").copy())
                finally:
                    bitmap.close()
                    page.close()
        finally:
            document.close()
        if single_page and len(sources) > 1:
            combined = self._combine_pages(sources)
            for source in sources:
                source.close()
            sources = [combined]
        return self._compose(
            sources,
            paper_size=paper_size,
            orientation=orientation,
            fit_mode=fit_mode,
            margin_mm=margin_mm,
        )

    @staticmethod
    def _combine_pages(sources: list[Image.Image]) -> Image.Image:
        cropped_pages: list[Image.Image] = []
        for source in sources:
            background = Image.new("RGB", source.size, "white")
            difference = ImageChops.difference(source, background)
            background.close()
            bounds = difference.getbbox()
            difference.close()
            if bounds is not None:
                cropped_pages.append(source.crop(bounds))
        if not cropped_pages:
            return Image.new("RGB", sources[0].size, "white")
        width = max(page.width for page in cropped_pages)
        height = sum(page.height for page in cropped_pages)
        combined = Image.new("RGB", (width, height), "white")
        top = 0
        for page in cropped_pages:
            left = (width - page.width) // 2
            combined.paste(page, (left, top))
            top += page.height
            page.close()
        return combined

    def from_image(
        self,
        content: bytes,
        *,
        paper_size: PaperSize,
        orientation: Orientation,
        fit_mode: FitMode,
        margin_mm: int,
    ) -> bytes:
        try:
            with Image.open(BytesIO(content)) as opened:
                source = ImageOps.exif_transpose(opened).convert("RGB").copy()
        except Exception as exc:
            raise self._invalid_source() from exc
        return self._compose(
            [source],
            paper_size=paper_size,
            orientation=orientation,
            fit_mode=fit_mode,
            margin_mm=margin_mm,
        )

    def _compose(
        self,
        sources: list[Image.Image],
        *,
        paper_size: PaperSize,
        orientation: Orientation,
        fit_mode: FitMode,
        margin_mm: int,
    ) -> bytes:
        if not sources:
            raise self._invalid_source()
        width_mm, height_mm = self._PAPER_MM[paper_size]
        if orientation == "landscape":
            width_mm, height_mm = height_mm, width_mm
        paper_width = round(width_mm * self._DPI / 25.4)
        paper_height = round(height_mm * self._DPI / 25.4)
        margin = round(margin_mm * self._DPI / 25.4)
        printable_width = paper_width - margin * 2
        printable_height = paper_height - margin * 2
        if printable_width <= 0 or printable_height <= 0:
            raise AppError(
                status=422,
                code="PRINT_MARGIN_INVALID",
                title="Print margin is invalid",
                detail="指定した余白では印刷領域を確保できません。",
            )
        pages: list[Image.Image] = []
        try:
            for source in sources:
                scale = min(
                    printable_width / max(1, source.width),
                    printable_height / max(1, source.height),
                )
                if fit_mode == "shrink":
                    scale = min(1.0, scale)
                target_width = max(1, round(source.width * scale))
                target_height = max(1, round(source.height * scale))
                resized = source.resize(
                    (target_width, target_height),
                    Image.Resampling.LANCZOS,
                )
                page = Image.new("RGB", (paper_width, paper_height), "white")
                left = (paper_width - target_width) // 2
                top = (paper_height - target_height) // 2
                page.paste(resized, (left, top))
                resized.close()
                pages.append(page)
        finally:
            for source in sources:
                source.close()
        output = BytesIO()
        first, *remaining = pages
        first.save(
            output,
            format="PDF",
            save_all=True,
            append_images=remaining,
            resolution=float(self._DPI),
            quality=95,
            subsampling=0,
        )
        for page in pages:
            page.close()
        return output.getvalue()

    @staticmethod
    def _invalid_source() -> AppError:
        return AppError(
            status=422,
            code="PRINT_SOURCE_INVALID",
            title="Print source is invalid",
            detail="印刷プレビューを生成できる書類データではありません。",
        )
