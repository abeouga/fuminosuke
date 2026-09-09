"""Deterministic cell-plan reference raster, deliberately not an Excel renderer."""

from __future__ import annotations

import io
from functools import lru_cache
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from app.domain.print_layout import print_error


@lru_cache(maxsize=128)
def _font(size: int, bold: bool) -> ImageFont.FreeTypeFont:
    candidates = [
        Path("C:/Windows/Fonts/meiryob.ttc" if bold else "C:/Windows/Fonts/meiryo.ttc"),
        Path(
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"
            if bold
            else "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"
        ),
    ]
    for path in candidates:
        if path.is_file():
            return ImageFont.truetype(str(path), size=size)
    raise print_error(
        "参考描画に必要な日本語フォント(Meiryo/Noto CJK)がありません。",
        "PRINT_LAYOUT_FONT_UNAVAILABLE",
        503,
    )


def render_page(page: dict[str, Any]) -> bytes:
    width, height = page["width"], page["height"]
    if width * height > 20_000_000 or max(width, height) > 10_000:
        raise print_error("ページ画像が許容サイズを超えています。")
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    scale = page["transform"]["scale"]
    for cell in page["cells"]:
        rect = cell["rectangle"]
        x, y, w, h = (rect[k] for k in ("x", "y", "width", "height"))
        draw.rectangle((x, y, x + w - 1, y + h - 1), fill=cell["fill"])
        if cell["text"]:
            font_size = max(1, round(cell["fontSize"] * scale))
            font = _font(font_size, cell["bold"])
            # Draft-only approximation: fit complete explicit lines without
            # altering the source text, style, or worksheet dimensions.
            if cell.get("shrinkToFit") and not cell["wrap"]:
                low, high = 1, font_size
                paragraphs = cell["text"].split("\n")
                while low < high:
                    size = (low + high + 1) // 2
                    candidate = _font(size, cell["bold"])
                    if (max(draw.textlength(line, font=candidate) for line in paragraphs)
                            <= max(1, w - 4) and size * 1.4 * len(paragraphs) <= h):
                        low = size
                    else:
                        high = size - 1
                font_size = low
                font = _font(font_size, cell["bold"])
            tile = Image.new("RGB", (w, h), cell["fill"])
            td = ImageDraw.Draw(tile)
            lines: list[str] = []
            for paragraph in cell["text"].split("\n"):
                line = ""
                for char in paragraph:
                    if (
                        cell["wrap"]
                        and line
                        and td.textlength(line + char, font=font) > max(1, w - 4)
                    ):
                        lines.append(line)
                        line = ""
                    line += char
                lines.append(line)
            line_h = max(1, round((font_size if cell.get("shrinkToFit") and not cell["wrap"]
                                  else cell["fontSize"] * scale) * 1.4))
            for i, line in enumerate(lines):
                if i * line_h >= h:
                    break
                text_w = td.textlength(line, font=font)
                dx = (
                    (w - text_w) / 2
                    if cell["align"] == "center"
                    else w - text_w - 2
                    if cell["align"] == "right"
                    else 2
                )
                td.text((max(0, dx), i * line_h), line, font=font, fill="#171717")
            image.paste(tile, (x, y))
        lines_xy = {
            "left": (x, y, x, y + h),
            "right": (x + w, y, x + w, y + h),
            "top": (x, y, x + w, y),
            "bottom": (x, y + h, x + w, y + h),
        }
        for side, enabled in cell["borders"].items():
            if enabled:
                draw.line(lines_xy[side], fill="#222222", width=1)
    output = io.BytesIO()
    image.save(output, format="PNG")
    image.close()
    return output.getvalue()
