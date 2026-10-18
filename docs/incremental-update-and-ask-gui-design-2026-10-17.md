# 同じ書類の追記分の取り込みと質問画面の改善：設計

2026-10-17。対象は `C:\Users\hanam\Desktop\llm-ocr-deploy`。実行モデルはClaude Opus 5.5。

**第2部（段階1）は§14、第1部の段階2は§15（2026-10-17）、段階3は§16（2026-10-18）で実装した。段階4以降は未実装。**
質問応答の既存設計は [構造化データ質問応答](structured-qa-design-2026-10-16.md) を正本とし、
本文書はその画面改善（第2部）と、新しい「追記分の取り込み」（第1部）を扱う。

## 0. 利用者が決めた要件（2026-10-17）

| 項目 | 決定 |
|---|---|
| 同じ書類かどうか | システムが候補を出し、利用者が「この書類の更新」を選ぶ。自動では結び付けない |
| 読む範囲 | 既定は**前回空欄だった欄・表のセルだけ**をOCRする。前回の値との食い違いの検出（全部読み直して比較）はオプション |
| 保存 | **新しい版**として保存し、前回の確定内容は履歴として残す |
| 質問・出力 | 最新版だけを対象にし、同じ書類を二重に数えない |
| 質問画面 | 対象の絞り込み・会話・根拠の3領域。対象を選んで質問を続けられる |

---

# 第1部 追記分の取り込み

## 1. 現状（読み取り調査の要点）

パスは `backend/app/` 基準。

- **jobの間をつなぐ仕組みはない。** `processing_jobs`・`ocr_results`・`confirmed_results` に別jobを指す列はない。
  重複の検出は `Idempotency-Key` だけで、`image_sha256` は保存されるが検索に使われていない。
- **識別情報で既存の書類を探す処理もない。** `DocumentIdentityConfig.match_role` / `match_group_id`
  （`domain/template_config.py:219-245`）は検証と編集だけで、他のjobとの照合には使われていない。
  唯一の検索は `StructuredDocumentService._matches`（`structured/service.py:98`）の文字列部分一致。
- **選んだ欄だけOCRする利用者向けの経路はない。** `JobService.retry` は全体の再実行。
  ただしprovider内部の高detail再読取（`ocr/openai_provider.py:174-200`、`_request_with_regions`）が
  要求の領域を絞って送れることを示している。要求は通常領域30件ずつ1回、識別欄は1欄1回
  （`_batched_requests`、`:244-260`）。
- **受付**：`POST /ocr-jobs`（`api/v1/routes/ocr.py:75`）は決まったフォーム項目以外を
  `OCR_FORM_UNSUPPORTED` で拒否する。`JobService.create_ocr_job`（`services/job_service.py:126`）が
  受付時snapshot（fieldMappings・fieldMappingHash・reviewStructure・logicalFieldIds・識別設定）を固定する。
- **実行**：`ImageExtractionService.process` が位置合わせ・切り抜きを全領域で行い、
  `OcrRequestPreparer.prepare`（`services/ocr_request_preparation_service.py:33`）が領域ごとの要求を作る。
  `build_mapped_document` は要求に含めた領域だけ欄を埋める。
- **補正**：`correct_in_transaction`（`services/ocr_result_service.py:114`）は欄キーの集合
  （`FIELD_BINDING_CHANGED`）と `source_region_id` / `bounding_box` の変更を拒否する。
- **確定・再編集**：`confirm`（`:192`）は識別欄の読み取り済みを要求し、`confirmed_results` と
  追記専用の `ocr_confirmation_revisions` へ記録する。`reopen`（`:175`）は同じjob内の履歴を残す。
- **表の行**は `reviewStructure.recordSections[].rows[].cells[{columnId, fieldId}]` だけが正本。
  欄キーの `_2`, `_3` は保存先の連番で、行の意味を持たない。
- **paged**：ページ単位の再利用（`services/paged_ocr_execution.py:148` `_previous_success`）はあるが、
  セル単位で絞ると `paged_crop_correspondence.py:84` の `CROP_REGION_SET_CHANGED` に当たる。

## 2. Safety Contractとの関係（意図的な契約変更）

現行契約には次の記述がある。

- §3.3「別の帳票や別jobの値を、番号・ラベル・配列位置だけで統合しない」
- §6.2「新旧jobのID一致だけで値やcropを移さない」
- §7.4「書類ライブラリの検索・自動統合は行わない」

本機能は前のjobの確定値を新しいjobへ引き継ぐため、**意図的なOCR contract変更**になる。
「自動で・IDだけで」統合するのではなく、次の条件をすべて満たす場合に限って引き継ぐ形で新契約を定める。

1. 利用者が前の書類を明示的に選んでいる（§4）。
2. 前のjobと新しいjobの受付snapshotが**同じ `templateVersionId` かつ同じ `fieldMappingHash`** である。
   mappingId・targetPath・sourceRegionId・reviewStructureの対応が版の上で同一であることを根拠にし、
   ラベル・配列位置・値の内容では対応づけない。
3. 引き継ぐのは前のjobの**確定revisionの確定snapshot**だけ。補正途中の値・OCR候補は使わない。
4. 引き継いだ欄の切り抜き・`bounding_box` は新しい画像のものを使い、前の画像の切り抜きを移さない。
5. 引き継ぎの来歴（どの欄が前の版から来たか、前のjob・revision・確定snapshotのSHA）を新しいjobに記録する。

実装時には、AGENTS §5・Safety Contract §10の手順に従い、Safety Contractの§3.3・§6.2・§7.4へ
本条件を追記し、旧契約と新契約を記録する。本文書の段階では契約文書を変更しない。

## 3. データ構造：書類の系列

前のjobを書き換えず、**新しいjobを作って前のjobへつなぐ**。前の確定内容は前のjobにそのまま残り、
それが履歴になる。

新テーブル `document_series_links`（追加のみのmigration）：

| 列 | 内容 |
|---|---|
| `job_id`（PK） | 新しいjob |
| `predecessor_job_id`（UNIQUE、FK） | 前のjob。UNIQUEにより1つのjobから枝分かれしない |
| `series_id` | 系列ID。最初の書類のjob_idを引き継ぐ |
| `predecessor_revision` | 引き継いだ前のjobの確定revision |
| `predecessor_confirmed_sha256` | 前の確定snapshot（`confirmed_json`）のSHA |
| `mode` | `blank_only`（既定）／`compare_all`（オプション、§6） |
| `created_at` | |

- 系列の「最新版」＝系列内で `CONFIRMED` かつ、確定済みの後継を持たないjob。
- 新しいjobの確定前は、前のjobが最新版のまま（質問・出力の対象は変わらない）。
- 新しいjobが失敗・取消・削除された場合はリンクを外し、前のjobから再び更新できる。
- **後継のあるjobは再編集（reopen）できない**（409 `SERIES_HAS_SUCCESSOR`）。古い版を直すと系列が分岐するため。
  直したい場合は最新版を再編集する。

引き継ぎ計画は新しいjobのartifact `incrementalPlan.json`（不変）に保存する：
`predecessorJobId, predecessorRevision, predecessorConfirmedSha256, mode, carriedKeys[], requestedKeys[], identityKeys[]`。
`FieldReading` のschemaには来歴の項目を足さない（OCRの値の型を変えないため）。

## 4. 画面の流れ

### 4.1 受付（OcrPage）

1. テンプレートを選んで画像を選ぶと、「既存の書類の更新として読み込む」を選べる。
2. 選ぶと、**同じテンプレート版で確定済みの最新版**の一覧を候補として出す（新しい順、書類名・識別値で絞り込み）。
   テンプレート版が違う書類は「テンプレートの版が違うため更新にできません」として選べない形で表示する。
3. 候補を選んで送信すると、更新用のjobとして受け付ける。

受付時点ではまだ画像を読んでいないため、候補は「テンプレートと識別値・書類名の検索」で出す。
識別値の照合は次の4.2で行う。

> 代案：通常どおり全部OCRし、レビュー画面で識別値の一致から候補を出す方式。候補の精度は上がるが、
> API回数は減らず、受付後に引き継ぎ範囲が変わるため実行の流れが複雑になる。既定にはしない。

### 4.2 識別の照合（ReviewPage の識別確認ステップ）

- 更新用jobでも**識別欄は必ず読む**（引き継がない）。
- 既存の識別確認（`ReviewPage.tsx:263-281`）で、読んだ値と前の版の値を並べて表示する。
- 一致しない場合は「前の書類と識別情報が一致しません」と表示し、確定前に利用者が
  「この書類の更新として続ける」か「更新をやめる」を選ぶ。更新をやめた場合は、このjobを取消して
  通常の新規読み込みをやり直す（引き継ぎ値の混ざった結果を通常の書類として確定させない）。

### 4.3 レビュー

- 上部に「追加 N欄 ／ 引き継ぎ M欄」を表示し、既定で**追加された欄だけを強調**する。
  「追加だけ表示」の切り替えを置く。
- 引き継いだ欄は「前の版から引き継ぎ」と表示する。値は変更できる（新しい版の確定内容として扱う）。
- 引き継いだ欄にも新しい画像の切り抜きを表示するので、利用者は紙の内容と見比べられる。
- 表では、前回空欄で今回値が入った行・セルを強調する。

### 4.4 確定

既存の `confirm` をそのまま使い、追加で次を検査する（同じtransaction内）：

- 前のjobがまだ `CONFIRMED` で、確定revisionが `predecessor_revision` と一致し、
  確定snapshotのSHAが `predecessor_confirmed_sha256` と一致する。違えば409 `SERIES_PREDECESSOR_CHANGED`。
- 前のjobに別の確定済み後継がない（UNIQUE制約で担保）。

XLSX出力は新しいjobの確定snapshotから通常どおり作る。全欄（引き継ぎ＋追加）が入った完全な帳票になる。

## 5. 実行：前回空欄だった欄だけ読む（既定 `blank_only`）

1. 受付時に前のjobの確定snapshotを読み、欄ごとに分類して `incrementalPlan.json` を作る。
   - `requestedKeys`：前の確定値の `status == blank` の欄＋識別欄。
   - `carriedKeys`：それ以外（`readable` / `uncertain`）。前回 `uncertain` のまま確定した欄も、
     利用者が確定した内容として引き継ぐ。
   - 記号欄（mark）も同じ規則。ローカル検出は従来どおり全欄で動くが、引き継ぐ欄の結果は使わない。
2. 位置合わせと切り抜きは**全領域で従来どおり**行う（位置合わせの精度と切り抜き集合の検査を変えない）。
3. `OcrRequestPreparer` の後段で、providerへ送る領域を `requestedKeys` に絞る。
   - 送る領域の manifest・compact ID・association token の検査は従来と同じ。
   - `mappedCells` は全欄の件数を保ち、引き継いだ欄は「要求せず前の版から引き継ぎ」として
     diagnostics に記録する（件数を表示都合で変えない、§4.3）。
4. 結果の組み立て：
   - 要求した欄：今回のOCR結果。
   - 引き継いだ欄：前の確定snapshotの `raw` / `normalized` / `status` / `alternatives` / `reviewReason`。
     `source_region_id` と `bounding_box` は新しい画像の切り抜きのもの（mappingは同一なので
     `source_region_id` は一致し、`bounding_box` は新しい画像上の位置）。
   - この結果を `raw_candidate_json` として保存する。以後の補正は欄キー集合・bindingの既存検査をそのまま通る。
5. API回数は「識別欄の数＋空欄だった欄÷30（切り上げ）」程度になる。記入が進むほど減る。

## 6. オプション：前回の値との食い違いも検出する（`compare_all`）

受付時に利用者が選ぶ。全欄をOCRし、欄ごとに前の確定値と比べる。

| 前の確定値 | 今回の読み取り | 分類 | 初期値 |
|---|---|---|---|
| 空欄 | 値あり | 追加 | 今回の値 |
| 値あり | 同じ（空白・全半角を除いて一致） | 引き継ぎ | 前の値 |
| 値あり | 違う値 | **差異** | 前の値（今回の値は候補として表示） |
| 値あり | 空欄 | **差異**（消えた／読めない） | 前の値 |
| 空欄 | 空欄 | 空欄のまま | 空欄 |

- 差異はレビュー項目（新しい理由コード `PREDECESSOR_VALUE_DIFFERS`）として表示し、利用者が
  「前の値のまま」「今回の値にする」「手で直す」を選ぶ。確定時に未判断の差異があれば確定できない。
- 一致の判定は表示用の比較だけで、保存値を正規化しない。
- API回数は通常の読み込みと同じ。

## 7. 質問・出力との接続

- `StructuredDocumentService.list` / `_confirmed_jobs` は、確定済みの後継を持つjobを除く（最新版だけ）。
- 一覧・回答に「版 3（2026-10-17更新）」のように系列の版番号を表示する。
- 根拠には、その値が引き継ぎか今回の読み取りかを表示する（`incrementalPlan` から判定）。
- 履歴（古い版）を含めた質問は初期対象外。必要になったら `includeHistory` を追加する。

## 8. 対象外・後回し

- **paged（複数ページ）**：初期は単一画像のみ。ページ単位の再利用は既にあるため、
  第2段階でページ内のセル絞り込み（切り抜き後に要求だけ絞る）を設計する。
- **テンプレート版が違う書類の更新**：`logicalFieldIds` で欄を対応づける拡張は可能だが、
  対応の安全性の検証が別途必要なので初期は扱わない。
- **識別値の先行読み取りによる自動候補**（画像を受けた直後に識別欄だけ読んで候補を絞る）：
  job状態の追加が必要なので後回し。

---

# 第2部 質問画面の改善

## 9. 現状

- `frontend/src/pages/AskPage.tsx` は「会話＋確定済み書類の一覧」の2領域。聞き返しは入力欄の上の別枠、
  根拠は別タブでレビュー画面を開く。
- 書類の特定は質問文に書類名・識別値が含まれるかの部分一致だけ（`structured/qa.py:200`）。
  実送信（台帳441〜445）では4問とも確信度0.70未満で、毎回聞き返しが発生した。
- 既存設計§4.3にある `documentIds` は `QuestionRequest` に未実装。前の回答を参照する会話の継続も未対応。

## 10. 画面構成

```
┌──────────────┬──────────────────────────┬──────────────┐
│ ① 対象の絞り込み │ ② 会話                       │ ③ 根拠の表示   │
│ テンプレート ▼  │  Q 子牛は今何か月？           │ [欄の切り抜き]  │
│ ☑ こよみ (版3) │  A 7か月です                  │ 分娩日          │
│ ☐ はなこ       │    分娩日 2026-03-12 → 今日   │ 「R8.3.12」     │
│ 聞ける欄:      │    [根拠: 分娩日] [書類を開く]  │ → 2026-03-12   │
│ [分娩日][価格]  │ ┌対象: こよみ ×────────────┐ │ 引き継ぎ／今回  │
│              │ │ 質問を入力…        🎤 送信 │ │ [書類を開く]    │
└──────────────┴──────────────────────────┴──────────────┘
```

### ① 対象の絞り込み
- テンプレートの選択と、確定済み書類（最新版だけ）のチェック。選んだ書類は入力欄の上にチップで表示し、
  次の質問へ引き継ぐ。チップの×で外す。
- 選んだテンプレートの**欄名チップ**を並べる（テンプレート定義から出すだけでAIは使わない）。押すと質問文に入る。

### ② 会話
- 回答は「一文 → 表 → 計算の過程 → 注意点 → 根拠チップ」の順。
- 聞き返しの選択肢は、その回答の吹き出しの中にボタンで出す。
- 表の回答に「CSVでコピー」を付ける（ブラウザー内で作る）。
- `status` が `uncertain` / `blank` の値は印を付けて区別する。

### ③ 根拠の表示
- 根拠チップを押すと、欄の切り抜き画像、元の文字列、解釈した値、引き継ぎ／今回の読み取りを右側に表示する。
- 修正が必要なときだけ「書類を開く」でレビュー画面へ移る。

### 狭い画面
①は引き出し、③は下から出るシート、②は全幅。

## 11. APIの変更（読み取り専用、既存の保存データは変更しない）

| 変更 | 内容 |
|---|---|
| `QuestionRequest.documentIds?: string[]`（最大20） | 指定時は書類特定をこの中に限る。1件なら書類の聞き返しをしない |
| `QuestionResult.steps?: {label, value}[]` | 計算の過程（サーバーが決定的に作る。例：起点の日付、基準日、差） |
| `GET /api/v2/structured/fields?templateId=` | 欄名チップ用。確定済み書類の受付snapshotから `logicalFieldId` 単位で欄名を集める |
| 根拠に `cropUrl` | 根拠用の切り抜き画像。新しいAPIは作らず、レビュー画面と同じ既存のartifact取得経路（`/api/v1/jobs/{id}/artifacts/fieldCrop.{mappingId}.alignedColor`、pagedは`page.{pageId}.`付き）のURLを返す（§14で設計を変更） |
| 一覧・投影に `seriesId` / `seriesVersion` | 第1部の系列表示 |

- 「その体重は？」のような前の回答を参照する会話の継続は、まず `documentIds` の引き継ぎ（画面側）で
  大半を解決し、サーバー側の会話メモリは後回しにする。
- AIの呼出し方（Luna / Decisions、1質問最大4回、確信度0.70未満は聞き返し）は変えない。

## 12. 進め方と検証

| 段階 | 内容 | OCR契約 | 検証 |
|---|---|---|---|
| 1 | 質問画面（§10）と読み取り専用API（§11、系列表示を除く） | 変更なし | 制御応答で `documentIds` の境界、frontend build、実画面。実送信は少数 |
| 2 | 系列テーブル、受付の候補選択、reopenの制限、最新版だけの一覧 | 受付・確定に検査を追加 | 通常DBのコピーで短いAPI統合、migrationの適用・再起動 |
| 3 | `blank_only` の実行・結果の組み立て・レビュー表示・確定時の検査 | **意図的な変更**（§2）。Safety Contract更新 | 同じ帳票の空欄あり／追記ありの2枚で、要求した欄・引き継いだ欄・mappedCells・XLSX内容を照合。前のjobの確定snapshotが不変であること。実送信は少数 |
| 4 | `compare_all` オプション | 同上 | 値を書き換えた画像で差異の検出と確定拒否 |
| 5 | paged対応 | 同上 | 別途設計 |

- 各段階の前に、変更前の保存response・受付snapshot・XLSX出力を基準として採取する。
- ユニットテストは増やさず、保存済み実データ・短いAPI統合・出力内容の照合・実画面で確認する（AGENTS §1.1、§11）。
- 実OpenAIを使う場合はAIテンプレート作成とは別に、使用回数を利用者へ確認してから送る。

## 13. 利用者に確認したい点

1. 識別値が一致しないとき（§4.2）、「更新として続ける」を許すか、更新をやめて新規読み込みに限るか。
2. 引き継いだ欄をレビュー画面で編集可能にしてよいか（本設計は可能とする）。
3. 前回 `uncertain` のまま確定した欄を、`blank_only` でも読み直す対象に含めるか（本設計は含めない）。
4. 質問画面の段階1を先に実装してよいか（第1部とは独立に進められる）。

## 14. 段階1の実装記録（2026-10-17）

利用者は§13の確認点を「設計どおりでよい」と回答し、段階1（質問画面と読み取り専用API）の実装を指示した。
第1部（追記分の取り込み）は未着手。

**変更ファイル**

- `backend/app/structured/contracts.py`：`QuestionRequest.documentIds`（1〜20件）、`QuestionResult.steps`、
  `Evidence.cropUrl`、欄名一覧の `FieldName` / `FieldNameList`。
- `backend/app/structured/qa.py`：対象の書類の指定（指定した中だけで書類を特定し、1件ならAIを使わず確定、
  複数なら質問文の書類名で絞り込み、決まらなければその中だけで聞き返す。聞き返しへの回答は
  元の質問の対象を引き継ぐ）。計算の過程（経過期間：起点・読み方・基準日・経過、合計・最大・最小：
  対象件数・式・結果）。根拠の切り抜きURL。AIの呼出し方・確信度0.70・上限4回は変更なし。
- `backend/app/structured/service.py`：`field_names(templateId)`。そのテンプレートの新しい確定済み書類
  （最大20件）の受付時の構造から、欄名と表の列名を重複なく並べる。推測はしない。
- `backend/app/api/v2/structured.py`：`GET /api/v2/structured/fields?templateId=`、`documentIds` の受け渡し。
- `frontend/src/structured/client.ts`：型の追加、`fields()`、CSV変換 `tableCsv`。
- `frontend/src/pages/AskPage.tsx`：3領域（対象の書類／会話／根拠）に作り替え。テンプレートの選択と
  書類のチェック、入力欄の上の対象チップ（次の質問へ引き継ぐ）、聞ける欄のチップ、
  吹き出し内の聞き返しボタン（最新の質問だけ）、計算の過程、CSVでコピー、根拠チップ
  （未確認・空欄は印付き）、根拠パネル（切り抜き画像・読み取り・確定値・解釈・書類を開く）。
  900px以下では対象の選択を全画面の引き出し、1100px以下では根拠を下から出るシートにする。
- `frontend/src/styles.css`：質問画面の追加スタイル（末尾に追記、既存の行は変更なし）。

OCRの値・mapping・補正・確定・出力経路、通常DBのschema・業務データは変更していない。
新しいAPIはすべて読み取り専用。既存OCR contractを維持した。

**検証**（実送信0回。共有台帳は不変）

- 既存の制御応答48条件（前回セッションの検証スクリプト）を変更後のコードで再実行し、全件通過。
- 追加の制御応答28条件：対象1件でAI呼出し1回・書類の聞き返しなし、計算の過程（経過・合計の式と結果の一致）、
  根拠のcropUrlが同じjobの切り抜きartifactを指すこと、対象2件での書類の聞き返しが対象内だけ・AI呼出し0回、
  聞き返しへの回答が元の対象を引き継ぐこと、対象内の書類名による特定、対象外の書類名を使わないこと、
  未知IDは回答不能、対象なしの従来動作、値の質問に過程なし、欄名一覧（欄・表の列・重複なし・未知テンプレート空）、
  HTTP境界（`documentIds` 空配列・21件は422、`/fields` の取得とtemplateId欠落の422）。
- 対象ファイルの ruff check / format、mypy（strict）は対象ファイルの指摘0（依存先の既知指摘は対象外）。
- frontend build、vitest 177件通過。
- 実画面：通常DBと保存ファイルのコピー（製品外のscratchpad）で専用 backend 8052 / vite 5252 を起動し、
  Decisionsは台本どおりに選ぶ差し替え（APIキーは偽値で送信不可）。1440px幅で、対象チップ→
  「生年月日から何年？」（過程・表・根拠）、根拠パネルの切り抜き画像（「26年9月27日生」）と確定値、
  欄名チップ→「売却記録の価格の合計は？」（7件の式・千区切りの注意）、対象2件での吹き出し内の聞き返し→
  CHK-002の回答、を確認。375px幅で根拠シートと対象の引き出し、横スクロールなし、コンソールエラーなしを確認。
  対象チップの表示名が識別値になっていた点を書類名優先へ修正した。

**未検証・残課題**

- 実モデル（GPT-6 Luna / Decisions）での品質。対象を指定すると書類の聞き返しは減るが、欄・処理の確信度は未測定。
- 「CSVでコピー」は検証用ブラウザーでクリップボードの書き込みを確認していない（失敗時は「コピーできませんでした」を表示）。
- paged確定結果での根拠画像（URLの形式はレビュー画面と同じだが、paged確定データが0件のため未確認）。
- 既存の数値読み取りで「826-100」が826として合計に入り、注意が出ない。本段階の変更ではないが、
  質問応答の品質課題として残す。
- 系列（版）の表示は第1部の実装後。

## 15. 段階2の実装記録（2026-10-17）：書類の系列

利用者指示「段階2（系列テーブル、受付の候補選択、reopenの制限、最新版だけの一覧）を実装して」による。

**段階2で決めたこと（§3〜§5からの具体化）**

- 段階2の後継jobは**全欄を通常どおり読む**（`mode=full`）。前の版の値は引き継がない。
  前回空欄だけ読む `blank_only` と値の引き継ぎは段階3で実装する（Safety Contract §3.3・§6.2の意図的な変更）。
- 後継を「持っている」とみなすのは、後継jobが取消（CANCELLED）以外の状態のとき。失敗したjobは
  再実行できるため前の版を保持し続ける。不要なら削除すればリンクも消える（FKのCASCADE）。
- 前の版を削除できるのは、後継がない場合か、取消済みの後継だけの場合（そのリンクを外して削除）。
- 同じIdempotency-Keyの再送は、前に作ったjobの系列snapshotをそのまま使い、同じjobを返す
  （検証中に、再送が自分自身を「更新中」とみなして拒否する不具合を見つけて修正した）。
- 未移行のDB（`document_series_links` がない）では、系列のリンクを作れない（503 `SERIES_NOT_MIGRATED`）が、
  確定・再編集・削除・構造化一覧は従来どおり動く。通常の起動スクリプト（`scripts/dev.ps1`）は
  `alembic upgrade head` を実行するため、次回の通常起動で移行される。

**変更ファイル**

- `backend/app/db/models.py`：`DocumentSeriesLink`（`document_series_links`）。
- `backend/alembic/versions/0010_document_series_v1.py`：テーブル追加のみの移行。
- 新規 `backend/app/services/document_series.py`：系列の読み取り（後継・最新版・版番号）、
  受付時の検査（確定済み・同じ版とfieldMappingHash・paged以外・後継なし）、job作成と同じtransactionでの
  リンク作成と再検査、確定時の前の版の不変検査、再編集・削除の制限、候補一覧 `series_candidates`。
- `backend/app/repositories/job_repository.py`：`create(before_commit=...)`。jobと同じtransactionで行を足すため。
- `backend/app/services/job_service.py`：`create_ocr_job(predecessor_job_id=...)`、受付snapshot `documentSeries`、
  削除時の制限。
- `backend/app/services/ocr_result_service.py`：`confirm` で前の版の不変検査、`reopen` で後継の有無検査。
- `backend/app/api/v1/routes/ocr.py`：フォーム項目 `predecessorJobId`、`GET /api/v1/document-series/candidates`。
- `backend/app/structured/service.py`・`contracts.py`：一覧は確定済みの後継を持つjobを除外、`seriesId`/`seriesVersion`。
- frontend：新規 `src/components/DocumentSeriesPicker.tsx`、`src/api.ts`（`createOcr` の更新元、`seriesCandidates`）、
  `src/pages/OcrPage.tsx`（画像の準備で「確定済みの書類の更新として読み込む」→候補から選択、確認画面に
  「〇〇の更新（版nとして保存）」、未選択では次へ進めない）、`src/pages/AskPage.tsx`（書類一覧に版番号）、
  `src/structured/client.ts`、`src/styles.css`（末尾に追記）。
- 文書：本節、Safety Contract §7.4の追記、AGENTS.md冒頭。

OCRのprovider・prompt・schema・mapping・crop・補正値・出力経路は変更していない。確定と再編集には
系列の検査を追加したが、系列のないjobの動作は変わらない。

**検証**（実送信0回。共有台帳は不変。通常DBは未移行・未変更）

- 移行済みの複製（`D:\llm-ocr-archive\series-20261017\work`、通常 `alembic upgrade head` で適用）で
  API統合44条件：移行の適用、候補一覧（選択可・識別値・絞り込み・他テンプレートは出ない・未知の版404）、
  受付の拒否（未知404・未確定409・版違い409・未知のフォーム項目422・拒否でjob/リンクを作らない）、
  更新の受付（リンクと確定revision、Idempotency再送で同じjob・リンク追加なし、2件目は409、候補は「更新中」）、
  模擬OCRで確認待ちまで処理、前の版の再編集・削除は409、取消で再び選択可・古いリンクの置換、
  補正と確定（前の版の確定snapshotを書き換えると409、戻すと確定成功）、最新版だけの一覧（前の版は非表示、
  後継は版2、IDを指定すれば前の版も読める、追記した値が最新版に入る、前の版の確定snapshotは不変）、
  最新版の再編集は可能でその間は前の版が一覧に戻る、後継の削除でリンクが消え前の版を再編集できる。
- 未移行の複製で7条件：一覧・候補は動く、更新の受付だけ503、既存の再編集・確定は成功。
- 質問応答の制御応答48＋28条件（未移行の複製）を再実行し全件通過。
- 対象ファイルのruff check/format、mypyで新規の指摘なし（依存先の既知の指摘は対象外）。
- frontend build、vitest 177件通過。
- 実画面（移行済みの複製、模擬OCR、専用 8052/5252）：テンプレート選択→画像→スイッチ→候補「UX検証_こよみ」を選択
  →確認画面に「版2として保存」→読み取り開始→確認待ちのjob、候補は「更新中の読み取りがあります」、
  前の版の再編集APIは409を確認。
- 既存 `tests/integration/test_jobs_api.py` は1件失敗（`test_template_upload_is_queued_and_processed`、
  `POST /template-jobs` 422）。今回変更していないテンプレート作成経路で、§8の段階A記録にも同じ失敗がある。

**検証で分かったこと（段階3への入力）**

- 前の版の確定データ（UX検証_こよみ）には、現在のテンプレートの欄にない旧形式の項目が2つある
  （sourceRegionId `pedigree`、`ear_tag`）。新しいjobの欄はすべて前の版にあるが、逆は成り立たない。
  段階3の引き継ぎは受付snapshotのmapping（mappingId / targetPath）だけを対象にし、mapping外の値は引き継がない。

**未検証・残課題**

- 段階3（前回空欄だけ読む・値の引き継ぎ・追加分の強調・識別値の照合表示）。
- 通常DBへの移行の適用（次回の通常起動で行われる。別セッションの検証サーバーは移行を実行しないため、
  その間は更新として読み込めない）。
- 更新用jobのレビュー画面に「どの書類の更新か」を表示していない（段階3でまとめて実装）。
- Webベータ（PostgreSQL・利用者別スキーマ）での移行と動作。

## 16. 段階3の実装記録（2026-10-18）：前回空欄だけ読む・値の引き継ぎ・追加分の強調・識別値の照合

利用者指示「段階3（前回空欄だった欄だけ読む、前の版の値の引き継ぎ、追加分の強調、識別値の照合表示）を実装して」による。
これは**意図的なOCR contract変更**で、Safety Contract §7.4に新契約（§3.3・§6.2の例外）を追記した。

**仕組み**

- 受付時（`DocumentSeries.snapshot_for`）に、前の版の確定snapshotから引き継ぐ欄を決め、受付snapshot
  `documentSeries.carried`（mappingId→raw/normalized/status/alternatives/reviewReason）に固定する。
  対象は、同じフィールドキーで同じmappingIdに結び付いた `readable` / `uncertain` の欄。空欄・識別欄・mapping外の値は対象外。
  識別値は `predecessorIdentity`（確定値と読み取り文字列）として保存する。新規の更新の既定は `mode=blank_only`。
- 実行時（`OcrWorkflowService` → 新規 `app/services/series_carry.py`）：位置合わせと切り抜きは全欄で行い、
  providerへの要求から引き継ぐ欄だけを除く。`OcrResponseFinalizer.finalize(carried=...)` が、項目名・検証・
  mappedCellsの前に引き継いだ読み取りを入れる（`sourceRegionId` は同じmappingId、`boundingBox` はこの画像の切り抜き）。
  引き継いだ記号欄はローカル検出の結果で上書きしない。artifact `incrementalPlan` に引き継ぎ・要求のmappingIdを残す。
- 確定時：識別値が前の版の確定値・読み取り文字列のどちらとも一致しなければ、`acceptIdentityMismatch: true`
  （厳密なboolean）がない限り409 `SERIES_IDENTITY_MISMATCH`。
- `GET /ocr-jobs/{id}/result` に `series`（前の版の名前・版番号・モード・引き継いだ欄・前の版の識別値）を追加。

**画面**

- 識別確認ページ：各識別欄に前の版の値（読み取り文字列が違えば併記）と一致/不一致、「前の版の値にする」。
  不一致のときは「この書類の更新として続ける」「更新をやめる（この読み取りを取り消す）」を出し、選ぶまで先へ進めない。
- レビュー画面：「〇〇の更新（版n）・追加 N欄・前の版から引き継ぎ M欄」、「追加した欄を目立たせる」
  （他の欄を薄くする）、「次の追加欄へ」。追加した欄は緑の強調と「追加」、引き継いだ欄は「前の版」と表示。

**変更ファイル**

- backend：`app/services/document_series.py`（引き継ぎ計画・識別値・review_info・identity_mismatches）、
  新規 `app/services/series_carry.py`、`app/services/ocr_workflow_service.py`（+23行）、
  `app/services/ocr_response_finalization_service.py`（+11行）、`app/services/job_service.py`（計画に必要なmappingと識別欄を渡す）、
  `app/services/ocr_result_service.py`（`series` の応答と識別値の検査）、`app/api/v1/routes/ocr.py`（`acceptIdentityMismatch`、応答の `series`）。
- frontend：`src/types.ts`（`OcrSeriesInfo`）、`src/api.ts`（`confirm` の引数）、`src/components/OcrReviewResults.tsx`
  （`ReviewSeriesMarks` context、セルの「追加」「前の版」表示）、`src/pages/ReviewPage.tsx`、`src/styles.css`（末尾に追記）。
- 文書：本節、Safety Contract §7.4、AGENTS.md冒頭。

provider・prompt・schema・compact ID・association検査・crop生成・補正/出力経路は変更していない。

**検証**（実送信0回。共有台帳は不変。検証はすべて複製データ）

- 変更前の基準：通常の（系列なし）jobを、保存済み画像・台本provider（全欄に決まった値を返す、外部送信なし）で
  変更前に読み、結果・artifactを保存。変更後に同じ条件で読み直して比較し、違いは処理時間の記録
  （`quality.durationMsBeforeOcr`）だけだった。
- 段階3の31条件（OpenAI経路を台本providerで通す）：通常jobは全欄を送り `series` なし。更新jobは
  `blank_only`、引き継ぎ48欄＝前の版の記入済みの対応欄（識別欄除く）、providerへは空欄と識別欄の24欄だけ
  （引き継ぎ欄は1つも送らない、識別欄は全部送る）、欄キーの集合は通常jobと同じ、引き継いだ値は前の版の確定値と一致、
  切り抜き位置は通常jobと同じ、追記した値（備考_1）が入りほかの空欄は空欄のまま、mappedCellsの件数は通常jobと同じで
  引き継ぎ件数を記録、`incrementalPlan`、`series` の内容。確定（識別一致）→最新版とXLSX表に追記値、前の版の値を保持、
  前の版の確定snapshotは不変、一覧は最新版だけ。版2からの再度の更新では、前回追記した欄が引き継ぎになり要求されない、
  版3の表示、識別不一致で409・明示確認で確定、`acceptIdentityMismatch` は文字列"yes"を422で拒否。
- 回帰：段階2の44条件（受付時のモードの期待値を `blank_only` に更新）、未移行7条件、質問応答48＋28条件。
- frontend build、vitest 177件。
- 実画面（複製データ、台本provider、専用 8052/5252）：識別確認で前の版の値と「一致」、名号を変えると不一致の警告と
  2つの操作・先へ進めない、「前の版の値にする」で戻る。レビュー画面で「追加 1欄・引き継ぎ 48欄」、
  追加欄の強調と「目立たせる」で他の欄が薄くなる、「次の追加欄へ」で備考を選択。

**検証で分かったこと**

- 同じ紙を読み直しても、前の版で利用者が直した識別値（例：生年月日「26 9 27」→「2014年9月27日」）とは一致しない。
  照合は確定値と読み取り文字列のどちらかと一致すれば一致とした。

**未検証・残課題**

- 実OpenAIでの品質（引き継ぎにより送る欄は減るが、空欄の判定精度は通常どおり）。
- 「同上」記号が引き継いだ前の行を参照する場合の扱い（同上の解決は要求した欄の中だけで行われる）。
- `compare_all`（前回の値との食い違い検出、段階4）、paged（段階5）。
- 通常DBは2026-10-18 00:21の通常起動（起動スクリプトの `alembic upgrade head`）で `document_series_v1` へ移行された
  （系列テーブルは0行、業務データは不変）。この起動は段階3の実装途中のコードを読み込んだ可能性があるため、
  通常サーバーを再起動してから使う。通常環境での更新の読み込みは未確認。

## 17. 通常環境での実送信確認（2026-10-18）

利用者指示「通常サーバーを再起動したうえで、実際の追記済みの紙で更新の読み込みを少数（実送信は数回まで）試して」による。

**準備**

- 通常サーバーは00:21にSysOverRay（`sysover-ray/Start-LlmOcr.ps1` → `scripts/dev.ps1`）で起動されていた。同じランチャーで
  00:31に再起動し、段階3の最終コードを読み込んだ（8010/5180、OCRは実OpenAI、移行は最新で変更なし）。
  この再起動はClaudeのプレビュー管理下で行ったため、セッション終了時に止まる場合はSysOverRayから起動し直す。
- 追記済みの実物の紙の画像はなかった（この書類の画像は確定済みの元画像1枚だけ）。利用者の選択により、
  前の版の位置合わせ済み画像の9行目（前回空欄）に、HG教科書体で7欄を書き込んだ**合成画像**を使った
  （`D:\llm-ocr-archive\series-20261017\real\koyomi_row9_synthetic.png`）。実物の手書きではない。

**結果**（job `ddc4d1a3-a587-4603-9867-56c56a52d02f`、実送信3回＝台帳550〜552、15.8秒、入力3,431/出力895 token、約0.0008ドル）

- providerへ送った欄は24（前回空欄22＋識別2）、引き継ぎ48。mappedCellsは72件を維持し、引き継ぎ48を記録。
- 引き継いだ48欄はすべて前の版の確定値（raw・normalized・status）と一致。
- 追記した7欄（9行目の産次・日令・体重・価格・種雄牛名・売却日・備考）は7/7とも期待どおり読めた（`9`、`281`、`315`、`612000`、`安福久`、`8-10-5`、`♀`）。
- 前回空欄で今回も空欄のはずの15欄のうち、14欄は空欄、`産次_10` の1欄だけモデルが `uncertain`（MODEL_UNCERTAIN、値なし）を返し「要確認」になった。
  誤った値の追加はない。
- 識別値：名号は「こよみ」で一致。生年月日は今回「26年9月27日生」と読み、前の版（読み取り「26 9 27」、確定「2014年9月27日」）の
  どちらとも一致しないため、識別確認ページで不一致の警告と「前の版の値にする」「この書類の更新として続ける／更新をやめる」が表示された。
- 通常画面（5180）で上記の表示を確認した。通常DBの業務データを変えないよう、識別の操作・補正・確定はしていない。

**分かったこと・課題**

- 同じ紙でも、識別欄の読み方（印字の「年」「日生」を含めるか、区切り）が読み取りごとに変わる。
  現在の照合（全角半角・空白を無視した完全一致）では不一致になりやすい。数字の並びだけで比べる、
  識別欄の読み取り指示を前回の読み取り形式に合わせる、などの改善が考えられる（未対応）。
- 合成画像は印字体のため、実物の手書きの追記での精度は未確認。

**通常DBに残っているもの**

- 確認待ち（REVIEW_REQUIRED）のテスト用job（合成画像）と、その系列リンク。これがある間は「UX検証_こよみ」を
  別の画像で更新できず、前の版の再編集・削除もできない。不要なら、このjobを削除すればリンクも消える。
