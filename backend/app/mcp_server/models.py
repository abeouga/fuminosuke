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


class DocumentInfo(BaseModel):
    document_id: str
    document_name: str | None
    template_id: str
    template_name: str
    version: int = Field(description="書類の版（更新して確定し直すと増える。返すのは最新の版だけ）")
    confirmed_at: str | None = Field(description="確定した日時（日本時間）")


class TableRowValues(BaseModel):
    row_number: int
    cells: dict[str, Evidence] = Field(description="列の名前 → その行のセル（空欄も含む）")


class TableValues(BaseModel):
    table_id: str
    title: str
    columns: list[str]
    rows: list[TableRowValues] = Field(description="値のある行だけ（番号だけの行は除く）")
    truncated: bool = Field(description="行が多く、上限で切ったとき true")


class DocumentValues(BaseModel):
    document: DocumentInfo
    fields: list[Evidence] = Field(description="表の外の欄（帳票の上から順）")
    tables: list[TableValues]
    not_found_fields: list[str] = Field(description="fields で指定した名前のうち、無かったもの")
    summary: str


class TemplateSummary(BaseModel):
    template_id: str
    name: str
    document_count: int = Field(description="確定済みの書類の件数（最新の版だけを数える）")
    latest_confirmed_at: str | None = Field(description="最後に確定した日時（日本時間）")


class TemplateList(BaseModel):
    templates: list[TemplateSummary]
    summary: str


class TemplateRef(BaseModel):
    template_id: str
    name: str


class TableColumns(BaseModel):
    title: str
    columns: list[str]


class TemplateFields(BaseModel):
    template: TemplateRef
    fields: list[str] = Field(description="表の外の欄の名前")
    tables: list[TableColumns] = Field(description="表ごとの列の名前")
    summary: str


class FieldEquals(BaseModel):
    """表の外の欄の値が一致する書類だけに絞る条件（保存値でも画面の値でも一致する）。"""

    field: str = Field(min_length=1, max_length=100, description="欄の名前（例：毛色）")
    value: str = Field(min_length=1, max_length=200, description="値（例：黒）")


class IdentityValue(BaseModel):
    label: str
    value: str | None


class DocumentSummary(BaseModel):
    document_id: str
    document_name: str | None
    template_id: str
    template_name: str
    version: int
    confirmed_at: str | None
    identity: list[IdentityValue] = Field(description="書類を見分ける欄（名号・受付番号など）")


class DocumentList(BaseModel):
    documents: list[DocumentSummary] = Field(description="新しく確定した順")
    total: int = Field(description="条件に合った件数（limit で切る前）")
    truncated: bool = Field(description="読む上限（200件）を超える書類が条件に合ったとき true")
    summary: str


class AggregatedValue(Evidence):
    """集計に使った（又は除いた）値と根拠。"""

    number: str | None = Field(default=None, description="数として読んだ値（文字列）")
    note: str | None = Field(
        default=None,
        description="読み方の注意（「.」を千の区切りとして読んだ・数として読めない等）",
    )


class AggregationValues(BaseModel):
    operation: Literal["sum", "avg", "count", "max", "min"]
    field: str = Field(description="集計した欄（表の列は「表名 列名」）")
    template: TemplateRef
    result: str | None = Field(description="結果（文字列、平均は小数2桁）。計算できなければ null")
    count_used: int = Field(description="計算に使った値の数（count は空欄以外の値の数）")
    blank: int = Field(description="空欄の数")
    documents: int = Field(description="対象にした書類の数")
    used: list[AggregatedValue]
    excluded: list[AggregatedValue] = Field(description="数として読めず、除いた値")
    at: list[AggregatedValue] = Field(description="max・min のとき、その値だった欄")
    truncated: bool = Field(description="書類や値が多く、上限で切ったとき true")
    summary: str
