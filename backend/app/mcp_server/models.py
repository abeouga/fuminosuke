"""道具の出力の型。これが structuredContent の形になり、同じ中身が JSON の文章としても返る。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class Box(BaseModel):
    """欄の矩形（位置合わせ済みのページ画像の画素）。"""

    x: float
    y: float
    width: float
    height: float


class DocumentLocation(BaseModel):
    template_id: str
    template_name: str
    document_name: str | None
    field_id: str | None
    # 欄の名前（文之助の保存上の名前。get_template_fields・aggregate で指定する名前と同じ）。
    field_label: str
    section: str | None = None
    table_id: str | None = None
    table_title: str | None = None
    row_number: int | None = None
    column_id: str | None = None
    page_id: str | None = None
    box: Box | None = None
    crop_url: str | None = Field(default=None, description="欄の切り抜き画像の絶対URL")
    image_url: str | None = Field(default=None, description="その欄があるページの画像の絶対URL")


class Evidence(BaseModel):
    """値と、その根拠（どの書類のどの欄か）。"""

    kind: Literal["document"] = "document"
    source_id: str = Field(description="書類ID（jobId）")
    label: str = Field(description="人が読む根拠の名前（例：繁殖台帳1021「はじめ」の毛色）")
    location: DocumentLocation
    value: str | None = Field(description="画面（ライブラリ）に出す値。空欄は null")
    stored_value: str | None = Field(
        default=None, description="保存値が画面の値と違うときだけ（例：黒2）"
    )
    status: Literal["readable", "uncertain", "blank"]
