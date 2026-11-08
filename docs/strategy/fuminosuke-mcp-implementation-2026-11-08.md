# 文之助MCPサーバーの実装設計（2026-11-08）

[文之助MCPサーバーの設計](fuminosuke-mcp-design-2026-11-08.md)（何を作るか・守る約束）を、ファイル・関数・要求と応答の形の単位まで具体化したもの。実装はこの順に進める。

---

## 0. 全体の流れ（もう一度）

```
① Claude Desktop / Agorack
     │  MCP（標準入出力）。道具の名前と引数（名前で指定してよい）
② 文之助MCPサーバー  backend/app/mcp_server/
     │  名前→IDの照合、根拠の形への並べ替え、量の上限、エラーの文章
     │  HTTP（127.0.0.1:8010、ログインなし＝同じPCからの要求）
③ 文之助のサーバー  backend/app/api/v2/structured.py ほか
     │  確定済みだけ・最新の版だけ・ごみ箱を除く・値の照合・数値の集計
     ▼
   PostgreSQL・画像
```

- ②は③の中のPythonの処理を **importしない**。HTTPだけで話す（あとで外向けAPIや別のPCへ切り替えられるように）。
- ③の業務の規則（数値の読み方・値の照合）は、チャットと**同じ処理**を使う。

---

## 1. 変更するファイル

### ③ 文之助のサーバー

| ファイル | 新規/変更 | 中身 |
|---|---|---|
| `backend/app/structured/matching.py` | 新規 | 値の照合の共通処理（`fold`・`compact`・`value_equals`）。今 `qa.py` にある `_fold`・`_compact` を移す |
| `backend/app/structured/aggregation.py` | 新規 | 数値の集計の共通処理。今 `qa.py` の `QuestionEngine._numbers` の中にある「数として読む」「計算する」部分を取り出す |
| `backend/app/structured/query.py` | 新規 | 書類の検索と集計の処理（`StructuredQueryService`）。`StructuredDocumentService` を使って確定済みの書類を読み、上の2つで絞り込み・集計する |
| `backend/app/structured/contracts.py` | 変更（追加のみ） | 新しいAPIの要求・応答の型 |
| `backend/app/api/v2/structured.py` | 変更（追加のみ） | `POST /documents/search`、`POST /aggregations` |
| `backend/app/structured/qa.py` | 変更 | `_fold`・`_compact` を `matching.py` から、`_numbers` の計算を `aggregation.py` から使う。**回答の文・注意の文は変えない** |

### ② MCPサーバー

| ファイル | 中身 |
|---|---|
| `backend/app/mcp_server/__init__.py` | 空 |
| `backend/app/mcp_server/__main__.py` | 起動の入口（`python -m app.mcp_server`） |
| `backend/app/mcp_server/server.py` | FastMCP のサーバーと6つの道具、サーバーの説明文 |
| `backend/app/mcp_server/client.py` | 文之助のサーバーへのHTTPの呼び出し（`FuminosukeClient`） |
| `backend/app/mcp_server/resolve.py` | 名前→IDの照合（テンプレート・書類・欄） |
| `backend/app/mcp_server/evidence.py` | 文之助の応答を根拠の形に並べ替える |
| `backend/app/mcp_server/models.py` | 道具の出力の型（pydantic）。これが structuredContent の形になる |
| `backend/app/mcp_server/errors.py` | AIに返すエラーの文章 |

### そのほか

| ファイル | 中身 |
|---|---|
| `backend/pyproject.toml`・`uv.lock` | 依存に `mcp` を追加（§5） |
| `docs/strategy/fuminosuke-mcp-design-2026-11-08.md` | 実装後に、実際の形と違う点を直す |
| `AGENTS.md` | 先頭に作業記録を1段落 |

---

## 2. ③ 文之助のサーバー側

### 2.1 `structured/matching.py`（値の照合）

`qa.py` から移すだけ。中身は変えない。

```python
_KANA = {code: code - 0x60 for code in range(0x30A1, 0x30F7)}

def fold(value: str) -> str:
    """照合用：全角半角・大文字小文字・カタカナ/ひらがな（「コヨミ」＝「こよみ」）をそろえる。"""
    return unicodedata.normalize("NFKC", value).casefold().translate(_KANA)

def compact(value: str) -> str:
    """照合用：fold した上で空白を除く（「田中 太郎」＝「田中太郎」）。"""
    return re.sub(r"\s+", "", fold(value))

def value_equals(written: str | None, wanted: str) -> bool:
    """欄の値が条件の値と同じか（compact したもの同士の一致）。空欄は一致しない。"""
    return written is not None and compact(written) == compact(wanted)
```

- `qa.py` は `from app.structured.matching import compact as _compact, fold as _fold` とし、中の呼び出しはそのまま。
- `service.py` にある別の `_fold`（カタカナをそろえない）は、一覧の検索 `q` の今の動きを変えないため**そのまま残す**。

### 2.2 `structured/aggregation.py`（数値の集計）

`QuestionEngine._numbers` の前半（数として読む）と計算を取り出す。回答文を作る部分は `qa.py` に残す。

```python
Operation = Literal["sum", "avg", "count", "max", "min"]

_DOTTED_THOUSANDS = re.compile(r"^\d{1,3}\.\d{3}$")   # qa.py から移す
_PLAIN_INTEGER = re.compile(r"^\d{4,}$")              # qa.py から移す

@dataclass(slots=True, frozen=True)
class NumberRead:
    index: int               # 入力の何番目の値か（呼び出し側が欄と結びつける）
    value: str               # 書かれていた値（表示の値）
    number: Decimal
    how: Literal["plain", "dots_as_thousands", "dots_as_decimal"]

@dataclass(slots=True, frozen=True)
class NumberSkipped:
    index: int
    value: str
    reason: Literal["not_a_number"]

@dataclass(slots=True, frozen=True)
class Numbers:
    read: list[NumberRead]
    skipped: list[NumberSkipped]
    blank: int               # 空欄の件数

def read_numbers(values: Sequence[str | None]) -> Numbers:
    """値の一覧を数として読む。規則は今のチャットと同じ：
    - 空欄（None）は数えない（blank に数える）
    - ほかに4桁以上の整数があれば「762.300」の「.」は千の区切り（dots_as_thousands）
    - そうでなければ number_value で読む。「762.300」型は小数として読む（dots_as_decimal、千の区切りかもしれない）
    - number_value が None なら読めない（skipped）。推測しない
    """

def compute(numbers: Numbers, operation: Operation) -> Decimal | int | None:
    """sum・avg（小数2桁）・max・min は読めた数で、count は空欄以外の件数で計算する。
    読めた数が0件なら None（count は 0）。"""

def extremes(numbers: Numbers, result: Decimal) -> list[NumberRead]:
    """max・min のとき、その値だった項目（同じ値が複数あれば全部）。"""
```

`qa.py` の `_numbers` はこう変わる：

```python
numbers = read_numbers([r.value for r in readings])
for skip in numbers.skipped:            # 今と同じ注意の文
    if operation != "count":
        outcome.caveats.append(f"{self._where(readings[skip.index], multi)}の「{skip.value}」は…")
grouped = [readings[n.index] for n in numbers.read if n.how == "dots_as_thousands"]
dotted  = [readings[n.index] for n in numbers.read if n.how == "dots_as_decimal"]
for n in numbers.read:
    readings[n.index].interpreted_as = f"{n.number:f}"
# 以降の回答文・書類ごとの内訳・計算の過程は今のまま（compute / extremes を使う）
```

- 「count」は今どおり、数として読めるかに関係なく**空欄以外の件数**。
- 注意の文・回答の文の文言は一字も変えない（§6.1 で確かめる）。

### 2.3 `structured/query.py`（検索と集計）

```python
class StructuredQueryService:
    def __init__(self, documents: StructuredDocumentService) -> None: ...

    def search(self, request: DocumentSearch) -> dict[str, Any]: ...
    def aggregate(self, request: AggregationRequest) -> dict[str, Any]: ...
```

**共通の手順**

1. 対象の書類を決める：`documents.list(limit=MAX_DOCUMENTS+1, template_id=…, query=…)`（確定済み・最新の版・ごみ箱を除く、今の規則のまま）。`documentIds` があればその中だけ。確定日（`confirmedAt` を日本時間の日付にしたもの）で絞る。
2. 欄の値の条件（`fieldEquals`）があれば、各書類を `documents.get(jobId)` で読み、**表の外の欄**で `label`（compactで一致）が同じ欄の値を `value_equals` で比べる。全部の条件に合う書類だけ残す。条件の欄がない書類は合わない。
3. 読む書類は最大 `MAX_DOCUMENTS = 200`。超えたら `truncated: true`。

**集計の手順（`aggregate`）**

1. 上の手順で書類を決め、各書類を `get` する。
2. 欄を決める（`FieldSelector`）：
   - `tableTitle` があれば：その名前の表（compactで一致）の、その名前の列（compactで一致）の全行。値のある行もない行も含め、空欄は blank に数える。
   - なければ：表の外の欄で、名前が一致するもの。なければ表の列で名前が一致するもの。
   - 書類をまたいで、**別の欄・別の表の列が同じ名前で2種類以上**当たったら 409（候補を返す）。同じテンプレートの同じ欄が書類ごとに当たるのは1種類と数える（欄は `templateId`＋表の外なら `label`＋`section`、表なら `tableTitle`＋列の `label` で同じとみなす）。
   - 1つも当たらなければ 404。
3. 当たった欄の値（`value`、表示の値）を順に並べて `read_numbers` → `compute`。
4. 応答に、使った欄・読めなかった欄・読み方を変えた欄を、`jobId`・`fieldId`・`rowNumber` 付きで返す。max・min なら、その値だった欄（`at`）も返す。

### 2.4 新しいAPIの型（`contracts.py` に追加）

既存の型と同じく `Contract`（キャメルケース）を使う。

```python
class FieldCondition(Contract):
    label: str = Field(min_length=1, max_length=100)
    value: str = Field(min_length=1, max_length=200)

class DocumentSearch(Contract):
    template_id: str | None = Field(default=None, max_length=100)
    document_ids: list[str] | None = Field(default=None, max_length=200)
    query: str | None = Field(default=None, max_length=200)
    field_equals: list[FieldCondition] = Field(default_factory=list, max_length=3)
    confirmed_from: date | None = None
    confirmed_to: date | None = None
    limit: int = Field(default=20, ge=1, le=200)

class DocumentSearchResult(Contract):
    contract_version: Literal["structured.v1"] = CONTRACT_VERSION
    items: list[DocumentSummary]       # 今の一覧と同じ要約
    total: int                          # 条件に合った件数（limit で切る前）
    truncated: bool                     # 読む上限（200件）に当たったか

class FieldSelector(Contract):
    label: str = Field(min_length=1, max_length=100)
    table_title: str | None = Field(default=None, max_length=100)

class AggregationRequest(DocumentSearch):
    field: FieldSelector
    operation: Literal["sum", "avg", "count", "max", "min"]

class AggregationItem(Contract):
    job_id: str
    document_name: str | None
    field_id: str | None
    label: str
    table_title: str | None = None
    row_number: int | None = None
    page_id: str | None = None
    value: str | None
    number: str | None = None          # 読めた数（文字列。小数の誤差を出さない）
    note: str | None = None            # 「「.」を千の区切りとして読んだ」「小数として読んだ（千の区切りかもしれない）」「数として読めない」

class AggregationResult(Contract):
    contract_version: Literal["structured.v1"] = CONTRACT_VERSION
    operation: Literal["sum", "avg", "count", "max", "min"]
    field: FieldSelector
    result: str | None                  # 計算できなければ null
    count_used: int
    blank: int
    used: list[AggregationItem]         # 計算に使った欄（count なら空欄以外の欄）
    excluded: list[AggregationItem]     # 数として読めなかった欄
    at: list[AggregationItem] = Field(default_factory=list)   # max・min の値だった欄
    documents: int
    truncated: bool
```

- `used`・`excluded` は最大500件まで返し、超えたら切る（件数は `count_used` で分かる）。

### 2.5 新しいAPI（`api/v2/structured.py` に追加）

今の `router`（`local_request` と `enabled` が付いたもの）に足す。画面・MCP以外からの要求は今と同じく拒否される。

```python
@router.post("/documents/search", dependencies=[Depends(enabled)],
             response_model=DocumentSearchResult, response_model_by_alias=True)
async def search_documents(request: Request, body: DocumentSearch) -> dict[str, Any]:
    return await asyncio.to_thread(StructuredQueryService(runtime(request).service).search, body)

@router.post("/aggregations", dependencies=[Depends(enabled)],
             response_model=AggregationResult, response_model_by_alias=True)
async def aggregate(request: Request, body: AggregationRequest) -> dict[str, Any]:
    return await asyncio.to_thread(StructuredQueryService(runtime(request).service).aggregate, body)
```

エラー（`reject` で返す、既存の形）：

| 場合 | 状態 | コード | 中身 |
|---|---|---|---|
| 欄が2種類以上に当たる | 409 | `STRUCTURED_FIELD_AMBIGUOUS` | `details.candidates`：`[{label, section, tableTitle}]` |
| 欄が見つからない | 404 | `STRUCTURED_FIELD_NOT_FOUND` | 探した名前 |
| テンプレートが見つからない | 404 | 既存のコード | — |
| 機能が無効 | 503 | 既存（`enabled`） | — |

`reject` が `details` を持てない場合は、文に候補を含め、MCP側で文から読まずに済むよう**候補の配列を返せる小さなエラーの型**を足す（実装時に既存の `AppError` を確かめて決める）。

---

## 3. ② MCPサーバー側

### 3.1 起動と設定（`__main__.py`）

```python
from app.mcp_server.server import build_server

def main() -> None:
    logging.basicConfig(stream=sys.stderr, level=logging.INFO)   # 標準出力はMCPの通信に使う
    build_server(base_url=os.environ.get("FUMINOSUKE_API_URL", "http://127.0.0.1:8010")).run()  # stdio

if __name__ == "__main__":
    main()
```

- `app.core.config` などの文之助の設定（`.env`）は**読まない**。AIのキーも持たない。

### 3.2 文之助への呼び出し（`client.py`）

```python
class FuminosukeClient:
    def __init__(self, base_url: str, timeout: float = 15.0) -> None:
        self._http = httpx.Client(base_url=base_url, timeout=timeout)   # Origin は付けない

    def templates(self) -> list[dict]:                       # GET  /api/v1/templates
    def documents(self, *, template_id=None, q=None, limit=1000) -> list[dict]:
                                                              # GET  /api/v2/structured/documents
    def document(self, job_id: str) -> dict:                 # GET  /api/v2/structured/documents/{id}
    def fields(self, template_id: str) -> list[dict]:        # GET  /api/v2/structured/fields
    def search(self, body: dict) -> dict:                    # POST /api/v2/structured/documents/search
    def aggregate(self, body: dict) -> dict:                 # POST /api/v2/structured/aggregations
    def image(self, path: str) -> bytes:                     # GET  /api/v1/jobs/{id}/artifacts/{name}
    def absolute(self, path: str | None) -> str | None:      # 相対のURLに住所を付ける
```

- 接続できない（`httpx.ConnectError`）→ `FuminosukeUnavailable`。
- 4xx・5xx → 文之助のエラー（`code`・`message`・候補）を持った `FuminosukeError`。
- 1回の道具の中で同じ書類を2回読まないよう、道具の呼び出しの間だけ覚える（道具をまたいでは覚えない。確定・再編集をすぐ反映するため）。

### 3.3 名前→IDの照合（`resolve.py`）

照合の規則は設計書 §4.3 のとおり。文字のそろえ方は文之助の `matching.compact` と同じ規則を**ここに書き写す**（②は③をimportしないため。数行なので二重になっても保守できる。外向けAPIで照合をサーバーへ移したら消す）。

```python
@dataclass(frozen=True)
class Candidate:
    id: str
    name: str
    detail: str        # テンプレート名・識別値・確定日など、AIが見分けるための説明

class Ambiguous(Exception):      # 候補が2つ以上
    def __init__(self, kind: str, wanted: str, candidates: list[Candidate]): ...

class NotFound(Exception):
    def __init__(self, kind: str, wanted: str, hint: str | None = None): ...

def template(client, wanted: str) -> Candidate:
    """ID一致 → 名前の完全一致 → 名前に含まれる（compact）。"""

def document(client, wanted: str, *, template_id: str | None) -> Candidate:
    """ID一致 → 書類名・識別値の完全一致 → 一覧の検索 q（文之助の今の規則）。
    同じ名前が複数あれば Ambiguous（detail にテンプレート名・識別値・確定日）。"""
```

欄の照合は③（集計API）に任せる。`get_document` の欄の絞り込み（`fields`）だけは②で行う（表示の量を絞るだけで、業務の判断ではないため）。

### 3.4 根拠の形（`evidence.py`・`models.py`）

```python
class Box(BaseModel):
    x: float; y: float; width: float; height: float

class DocumentLocation(BaseModel):
    template_id: str
    template_name: str
    document_name: str | None
    field_id: str | None
    field_label: str
    section: str | None = None
    table_id: str | None = None
    table_title: str | None = None
    row_number: int | None = None
    column_id: str | None = None
    page_id: str | None = None
    box: Box | None = None
    crop_url: str | None = None      # 絶対URL
    image_url: str | None = None     # そのページの画像の絶対URL

class Evidence(BaseModel):
    kind: Literal["document"] = "document"
    source_id: str                   # jobId
    label: str                       # 「繁殖台帳1021「はじめ」の毛色」
    location: DocumentLocation
    value: str | None                # 画面に出す値
    stored_value: str | None = None  # 保存値が違うときだけ（「黒2」）
    status: Literal["readable", "uncertain", "blank"]
```

- `value`：選択欄で、末尾の数字を外した名前が同じ欄の別の選択肢（`choiceLabels`）にあるときだけ数字を外す（ライブラリの表示と同じ規則）。それ以外は保存値のまま。
- `image_url`：書類の `pages` から `pageId` が同じページ（なければ1ページ目）の `imageUrl`。
- 文之助の応答の項目からだけ作る。足りない項目は `null` にし、作り出さない。

### 3.5 道具（`server.py`）

FastMCP で、型付きの関数として書く。戻り値の pydantic の型が structuredContent になり、同じ中身がJSONの文章としても返る。各出力に、人が読める1〜3行の `summary` を入れる。

```python
mcp = FastMCP("fuminosuke", instructions=INSTRUCTIONS)

@mcp.tool()
def list_templates() -> TemplateList: ...

@mcp.tool()
def get_template_fields(template: str) -> TemplateFields: ...

@mcp.tool()
def search_documents(
    template: str | None = None,
    text: str | None = None,
    field_equals: list[FieldCondition] | None = None,
    confirmed_from: str | None = None,     # YYYY-MM-DD
    confirmed_to: str | None = None,
    limit: int = 20,                        # 最大50
) -> DocumentList: ...

@mcp.tool()
def get_document(
    document: str,
    template: str | None = None,
    fields: list[str] | None = None,
) -> DocumentValues: ...

@mcp.tool()
def aggregate(
    template: str,
    field: str,                             # 「価格」又は「売却子牛一覧 価格」
    operation: Literal["sum", "avg", "count", "max", "min"],
    document: str | None = None,
    field_equals: list[FieldCondition] | None = None,
    confirmed_from: str | None = None,
    confirmed_to: str | None = None,
) -> AggregationValues: ...

@mcp.tool()
def get_field_image(document_id: str, field_id: str) -> Image: ...
```

| 道具 | 処理の手順 |
|---|---|
| `list_templates` | `templates()` と `documents(limit=1000)` → テンプレートごとに件数と最新の確定日 |
| `get_template_fields` | `resolve.template` → `fields(id)` → 表の外の欄名と、表ごとの列名に分ける |
| `search_documents` | `resolve.template`（あれば）→ 条件が `text` だけなら `documents(q=…)`、欄の値・確定日があれば `search(...)` → 要約に並べ替え、`limit` で切る |
| `get_document` | `resolve.document` → `document(id)` → `evidence` で欄と表を根拠の形に。`fields` があればその名前（compactで一致）の欄・列だけ。表は値のある行だけ（全部の列が空・番号だけの行は外す）、100行まで |
| `aggregate` | `resolve.template`・`resolve.document`（あれば）→ `field` を「表名 列名」に分けられるか試す（最初の空白で分け、前半が表名として当たれば表の列、当たらなければ全体を欄名として）→ `aggregate(...)` → `used`・`excluded`・`at` を根拠の形に（各書類を `document()` で読み、欄IDから位置を取る） |
| `get_field_image` | `document(id)` で欄の `cropUrl` を探す → `image()` → Pillow で長い辺1024pxに縮め PNG で返す |

**サーバーの説明（INSTRUCTIONS）**

```
このサーバーは、文之助で人が確認・確定した帳票の値だけを返します。
- 返した値をそのまま使い、推測で埋めたり言い換えたりしないでください。
- 数の計算（合計・平均・件数・最大・最小）は aggregate を使い、自分で計算しないでください。
- 答えには、書類名と欄名を根拠として添えてください。
- 値が違いそうなときは直そうとせず、文之助の確認画面で確かめるよう利用者に勧めてください。
- 名前が1つに決まらないと返されたら、候補から選び直すか、利用者に聞いてください。
- 最初に list_templates、欄名が分からなければ get_template_fields を呼んでください。
```

### 3.6 エラー（`errors.py`）

道具の中の例外は、FastMCP の道具のエラー（`isError: true`）として、次の文章で返す。

| 例外 | 文章 |
|---|---|
| `FuminosukeUnavailable` | 文之助のサーバー（{url}）に接続できません。起動しているか確かめてください。 |
| 503（機能が無効） | 文之助の確定済みデータの機能が無効です。 |
| `Ambiguous` | 「{wanted}」に当てはまる{kind}が{n}件あります。次のどれかを指定してください：<br>・{name}（{detail}） … |
| `NotFound` | 「{wanted}」という{kind}は見つかりませんでした。{hint} |
| 409（欄が2種類以上） | 「{field}」に当てはまる欄が複数あります：・{tableTitle} {label} … 「表名 列名」の形で指定してください。 |
| そのほかの4xx・5xx | 文之助のサーバーがエラーを返しました：{message} |

---

## 4. 量の上限（まとめ）

| 場所 | 上限 |
|---|---|
| ③ 検索・集計で読む書類 | 200件（超えたら `truncated`） |
| ③ 集計の `used`・`excluded` | 各500件 |
| ② `search_documents` | 既定20・最大50 |
| ② `get_document` の表 | 1表100行 |
| ② `get_field_image` | 1枚・長い辺1024px |

---

## 5. 依存の追加

1. 通常の 8010/5180 を止める（稼働中に仮想環境を入れ替えると壊れる）。
2. `backend` で `uv add mcp`（その時点の安定版を `pyproject.toml` と `uv.lock` に固定）。
3. `sysover-ray/Start-LlmOcr.ps1` で 8010/5180 を起動し直す。
4. 開発用の道具（ruff・mypy・pytest）が消えていないか確かめる（以前 `uv sync` で消えたことがある）。

---

## 6. 確かめ方

ユニットテストは増やさない。使い捨ての確認の台本は製品の外（作業用のフォルダ）に置き、結果だけを記録に残す。

### 6.1 チャットの計算が変わらないこと（最初に行う）

`qa.py` を直す**前**に、通常データの確定済みの全書類について、計算の対象になりうる全部の欄（表の外の欄・表の全列）× 5つの計算で、今の `_numbers` の結果（回答の文・注意の文・計算の過程）を保存する。直した**後**に同じことをして、全件が一字一句同じことを確かめる。外部のAIは呼ばない（`_numbers` を直接呼ぶ）。

### 6.2 新しいAPI

`http://127.0.0.1:8010` に直接要求して確かめる。

- 集計：繁殖台帳1021「こよみ」の「売却子牛一覧 価格」の合計が、チャットの答えと同じ。平均・最大（どの行か）・件数も同じ。
- 読めない値の除外と、「.」を千の区切りとして読む例が `excluded`・`note` に出る（通常データにない場合は、確定済みの書類を変えずに、照合の関数だけで確かめる）。
- 欄の名前が2つの表にある場合に 409 と候補が返る。
- 欄の値の検索：「毛色」が「黒」の書類だけが返る。確定日の範囲で絞れる。
- ごみ箱・確定前の書類が出てこない（確定待ちの書類が通常データにあれば、それが含まれないこと）。

### 6.3 MCPサーバー

小さな呼び出しの台本（MCP の Python SDK のクライアントで標準入出力につなぐ）又は MCP Inspector で、6つの道具を呼ぶ。

| 確かめること | 期待 |
|---|---|
| `list_templates` | 通常データのテンプレートと件数が、ライブラリの表示と同じ |
| `get_document("はじめ", fields=["毛色"])` | 値が「黒」（保存値「黒2」）、位置・切り抜きのURLが付く |
| `get_document("こよみ")` | 「こよみ」と「UX検証_こよみ」の候補が返る（完全一致の「こよみ」があれば、それが選ばれる） |
| `aggregate("繁殖台帳1021", "売却子牛一覧 価格", "sum", document="こよみ")` | ③の集計APIと同じ結果に、根拠が付く |
| `get_field_image` | 欄の切り抜きの画像が返る |
| 文之助のサーバーを止めて呼ぶ | 接続できない旨の文章 |

### 6.4 Claude Desktop

`claude_desktop_config.json` に設計書 §7 の設定を入れてつなぎ、次の質問に根拠付きで答えられることを確かめる（ここだけ実際のAIを使う。回数を記録する）。

- 「はじめの毛色は？」
- 「こよみの売却子牛の価格の合計は？」
- 「毛色が黒の牛は何頭？」
- 「一番高く売れた子牛は？」

### 6.5 そのほか

- 対象のファイルの ruff・mypy。
- `git diff --check`。

---

## 7. 作業の順番

ブランチ `feat/fuminosuke-mcp` を切り、最後にPRを作る（いつもの「ブランチ＋PR」の運用、リポジトリは abeouga/fuminosuke）。デプロイはしない。

| 順 | 作業 | 目安 |
|---|---|---|
| 1 | §6.1 の「直す前」の結果を保存 | 30分 |
| 2 | `matching.py`・`aggregation.py` を作り、`qa.py` をそれを使う形に直す → §6.1 で一致を確認 | 1〜2時間 |
| 3 | `query.py`・型・2つのAPI → §6.2 | 1〜2時間 |
| 4 | 依存の追加（§5） | 15分 |
| 5 | MCPサーバー（client → resolve → evidence → 道具） → §6.3 | 2〜3時間 |
| 6 | Claude Desktop でつなぐ → §6.4 | 30分 |
| 7 | 設計書と AGENTS.md の更新、PR | 30分 |

1〜3（文之助のサーバー側）と5（MCP）は分けてコミットし、3が終わった時点で文之助単体でも新しいAPIが使える状態にする。

---

## 8. 実装のときに確かめて決めること

1. 既存の `reject`／`AppError` が候補の配列（`details`）を返せるか。返せなければ、409 の応答に候補を入れる方法を決める（§2.5）。
2. FastMCP の structuredContent の出し方（戻り値の型の注釈で出るか）と、画像の返し方を、入れた `mcp` の版で確かめる。
3. `confirmedAt` の日付の区切り（日本時間で日付にする）を、ライブラリの表示の日付と合わせる。
