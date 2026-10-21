# 確定済みデータの構造化取り出しと質問応答：設計（段階A〜C）

2026-10-16。対象は `C:\Users\hanam\Desktop\llm-ocr-deploy`。実行モデルはClaude Fable 5.1。
利用者指示により、Webベータ工程（7-C以降）を一時停止し、本件を現在の作業とする。
AWS操作・Git書込・新規別エージェントの起動はない。

**本文書は段階A〜Cの設計正本である。段階Aの実装は本文書と同時に開始し、B・Cは未実装。**

## 1. 目的と利用者要件

- 確定済みのOCR結果を、XLSX以外に構造化データ（JSON）として取り出せるようにする。
- 確定済みの複数帳票を横断して自然言語で質問し、値や計算結果を取り出す。
  例：繁殖台帳で「母牛こよみの子牛は今何か月？」。
- **特定の帳票フォーマット（繁殖台帳・牛・列名）に依存しない汎用の仕組みとする。**
  語彙・列名・日付規則をコードへ固定せず、テンプレートの見出し・識別情報・表構造から導く。
- 回答はチャットの文章＋表のような表示＋根拠（欄）へのリンク。
- 音声と共通で使えるAPIにする。最初の入口はテキスト入力、音声は後段で接続する。
- AIは GPT-6 Luna ＋ Decisions API を主とし、自由生成は最小限にする。

利用者は2026-10-16に「実際の繁殖台帳テンプレートには子牛の分娩日（生年月日）の欄がある」と回答した。
月齢はその欄から計算し、売却日や日令からの逆算は既定にしない。

## 2. 方針変更の記録

[2026-09-29の判断](review-decisions-2026-09-29.md) §「OCR結果の検索・蓄積機能」は、
過去帳票一覧・全文検索・項目横断検索をv0.1の対象外とし、再検討条件を
「継続して多数の帳票を処理」「過去帳票の再閲覧要求」「項目値による検索・絞り込み要求」
「数十〜数百件の蓄積」と定めた。2026-10-04の汎用化で検索・書類ライブラリは実行経路から廃止した。

利用者が2026-10-16に項目値による横断の質問を明示要求したため、再検討条件のうち
「項目値による検索・絞り込み要求」を満たすと判断し、次の範囲で方針を変更する。

- 対象は**確定済み**結果だけ。修正中の現在値・OCR直後の候補は対象外。
- 範囲は**1利用者のDB内**に閉じる。Webベータの利用者別DB/スキーマの境界を越えない。
- OpenSearch・検索専用RDS・書類ライブラリUIは導入しない。索引は利用者DB内で完結させる。
- 文書識別・自動統合は行わない。同名の書類が複数ある場合は候補を示し、推測で一つに決めない。
- 同文書の再検討前の記述（AGENTS.md、README、Safety Contract §7.4の「検索は対象外」）は、
  本文書の範囲に限り上書きする。

## 3. 現状の確認（読み取り調査の要点）

- 確定値は `confirmed_results.confirmed_json`（単一画像）、`paged_confirmations.pages_json`（複数ページ）。
  `fields.<key>` → `FieldReading{raw, normalized, status, …}` の平らな辞書で、値は文字列。
- 表は配列ではなく列名＋連番のキー（`産次_1, 産次_2, …`）。行・列・見出しの対応は受付時の
  `config_snapshot_json.reviewStructure`（単一）/ `jobPages.documentSnapshot.reviewStructure`（paged）だけが正本。
- 確定の判定：単一は `job.status == CONFIRMED` かつ `confirmed_results.ocr_result_revision == ocr_results.revision`。
  pagedは `job.status == CONFIRMED` かつ `paged_confirmations[(job_id, paged_reviews.revision)]` が一致。
  確定行の存在だけでは判定しない（reopen後も行が残る）。
- 日付・数値は正規化されない文字列（例：`30 8-10`、`1-11-6`、`762.300`、同上記号「：」）。
  方針上、推測した値を保存データへ書き込まない。
- 書類の識別は `documentIdentityFields`（例：名号＋生年月日）と `document_name`。索引はない。
- `logicalFieldIds` はテンプレート版をまたいで欄の同一性を示す。横断で列をそろえる鍵に使える。
- `genericData` は承認済み汎用定義がある場合だけ作られ、実データでは全件null。入力に使わない。
- 既存の横断読み出し `OcrResultRepository.list_all_confirmed()` は呼び出し元0件。
- AI呼出しは `ApiBudget.client(purpose)` で共有台帳に自動予約される。別台帳は作らない。
- v1会話controller（`app/assistant/conversation.py`）に読み取り専用ツールのループ・聞き返し・
  短時間メモリがあり、書き込み系を外せば骨格として流用できる。既存編集カタログは確定値を読まない。

## 4. 全体構成：3層

```
[層3 質問応答] POST /api/v2/structured/questions   … 段階B（AI、Decisions主）
      │ ツール: find_documents / get_document / date_math（読み取り専用）
[層2 確定済み検索] GET /api/v2/structured/documents  … 段階A（AIなし）
      │ 利用者DB内の確定済みjobを走査し、書類名・識別値・テンプレートで絞り込む
[層1 構造化投影] GET /api/v2/structured/documents/{jobId} … 段階A（AIなし）
      │ 確定値 + 受付snapshot → 意味付きの書類レコード（JSON）
[正本] confirmed_results / paged_confirmations + config_snapshot_json（変更しない）
```

### 4.1 層1：構造化投影（`backend/app/structured/projection.py`）

確定値と受付snapshotから次の形を組み立てる。XLSX出力 `ExcelExportService._append_review_structure`
と同じ検査（mapping/targetPathの一致、全欄の一回使用）を行い、不整合は409 `STRUCTURED_SNAPSHOT_INVALID`。
XLSX出力と異なり、`mappingId` / `targetPath` / `logicalFieldId` / `status` を落とさない。

```json
{
  "schemaVersion": "structured-document.v1",
  "jobId": "...", "kind": "single" | "paged",
  "templateId": "...", "templateVersionId": "...", "templateName": "...",
  "documentName": "UX検証_こよみ", "confirmedAt": "...", "confirmedRevision": 3,
  "identity": [ { "label": "名号", "fieldId": "...", "value": "こよみ", "groupLabel": "名称と生年月日" } ],
  "fields":   [ { "fieldId", "logicalFieldId", "key", "targetPath", "label", "section", "pageId",
                  "raw", "normalized", "value", "status", "reviewReason" } ],
  "tables":   [ { "id", "title", "columns": [ { "id", "label" } ],
                  "rows": [ { "id", "rowNumber", "cells": [ { "columnId", "fieldId" | null } ] } ] } ],
  "extraFields": [ /* mapping外の値。XLSXの「追加項目」と同じ */ ]
}
```

- `value` は `normalized ?? raw`。`status` が `uncertain` / `blank` の欄は回答でも区別する。
- `section` は親見出し（parentSections.title）または「基本項目」。表のセルは `tables` から参照する。
- pagedは全ページの `fields` を結合する（キーは文書内で一意）。各欄に `pageId` を付ける。
- 投影は読み取り専用で、保存データ・revision・確定状態を変更しない。

### 4.2 層2：確定済み書類の一覧（`backend/app/structured/service.py`）

- `GET /api/v2/structured/documents?limit=&templateId=&q=`
- 利用者DBの `processing_jobs`（EXTRACT_DOCUMENT・CONFIRMED）を確定日時の新しい順に走査し、
  §3の確定判定を満たすものだけを返す。満たさないjobは一覧から外す（エラーにしない）。
- 返す要約：`jobId, kind, documentName, templateId, templateName, confirmedAt, identity[]`。
- `q` は書類名と識別値へのNFKC・casefoldの部分一致。数十〜数百件を想定し、新テーブルは作らない。
  件数増で遅くなった時点で、確定時に書く索引テーブルを追加する（段階C、移行不要の設計にする）。
- 利用者分離はDB単位なので横断は自動で1利用者内に閉じる。要求から利用者IDや所有者を受け取らない。

### 4.3 層3：質問応答（段階B、本文書では骨子のみ）

- `POST /api/v2/structured/questions` `{ text, conversationId?, documentIds? }`。
  音声からも同じAPIを呼ぶ（STT済みテキストを渡す）。
- 処理はサーバー側の決定的な手順＋有限選択を主にする（GPT-6 Luna / Decisions）。
  1. 候補の書類を層2で絞る（識別値・書類名の一致）。
  2. 候補の欄・表・列を層1から列挙し、質問に対する「対象の書類」「対象の欄/列」「操作
     （値の取得・日付差・件数・合計など）」を Decisions の有限選択で決める。
  3. 日付・数値の解釈は汎用の候補生成（西暦/和暦/区切り/同上記号）をサーバーが行い、
     複数候補があれば Decisions で選ぶか利用者に聞き返す。解釈した値は保存しない。
  4. 計算（月齢・日数・合計・件数）はサーバーが今日のJST日付で決定的に行う。
  5. 回答は構造化：`{ answer, table?, evidence[{jobId, fieldId, label, raw, interpretedAs}], caveats[], unresolved[] }`。
     根拠は画面でその欄のレビュー（`?job=&field=`）へリンクする。
- 自由生成（Responses）は、有限選択で表現できない言い換えの文章化に限り、低effortで最大1回。
  値・計算結果はResponsesの出力から取らない。
- 共有台帳 `ApiBudget` を通し、用途名は `qa.decisions` / `qa.responses`（20文字以内）。
  質問1件あたりのモデル呼出し上限4回。曖昧なときは推測せず聞き返す。
- 上限・聞き返し・会話メモリは v1 `ConversationController` の型（`MemoryTurn`, `reply_to`）を流用する。

### 4.4 画面（段階B）

- サイドメニューに「質問」ページを追加：テキスト入力、回答、表、根拠リンク、確定済み書類の一覧。
- 確定済みのレビュー画面にも「この書類について質問」を置く（単一書類の範囲）。
- 音声入口は既存 `voiceCapture` を段階Cで接続する。字幕の途中経過を質問として送らない。

## 5. 設定とAPIの慣例

- フラグ `STRUCTURED_DATA_ENABLED`（既定 `false`）。`AppSettings.structured_data_enabled`、
  `RawSettings` の alias、`Settings.from_raw` で対応付け。無効時は503 `STRUCTURED_DATA_DISABLED`。
- router は `/api/v2/structured`、`route_class=EditingRoute`、`dependencies=[local_request, enabled]`。
  `create_business_app` へ追加するだけで、cloudでは gateway の認証・CSRF・利用者別DBが適用される。
- DB/storage は `get_database(request)` / `get_storage(request)` から取る。
- 契約モデルは `Contract`（camelCase alias、`extra="forbid"`）。
- 段階Aでは deploy の compose 設定を変更しない。ベータ構成での有効化は公開受入の際に決める。

## 6. 守る境界

- OCRの値・status・reviewItems・mapping・crop・revision・確定状態を変更しない（AGENTS.md §4）。
- 推測した日付・数値・名寄せ結果を正本へ書き込まない。解釈は回答のたびに行い、根拠と一緒に示す。
- ラベル文字列や配列位置で欄を対応づけない。`mappingId` / `targetPath` / `reviewStructure` だけを使う。
- `uncertain` / `blank` を正しい読みとして扱わない。回答では区別して示す。
- 業種固有の語彙・列名・日付規則をコードへ書かない。

## 7. 段階と検証

| 段階 | 内容 | AI | 検証 |
|---|---|---|---|
| A | 層1・層2、`/status`、一覧、構造化JSON取得 | なし | ローカルDBの実データ「UX検証_こよみ」等3件で、投影の各欄がXLSX出力（`excel-table`）と同じ値・順序になることを突き合わせ。対象ファイルのruff/mypy。短いAPI統合 |
| B | 層3の質問応答API、画面、会話メモリ | あり | 制御応答（実送信0）で境界検証 → 実送信を少数。品質は利用者が実データで判定 |
| C | 音声入口、索引テーブル（必要時）、pagedの実データ確認 | — | — |

単一画像の確定済み実データは3件（pagedは0件）。段階Aのpaged経路は既存の統合テストfixtureで確認し、
実データでの確認は段階Cに残す。

## 8. 段階Aの実装記録（2026-10-16）

**変更ファイル**

- 新規 `backend/app/structured/__init__.py`、`contracts.py`（structured-document.v1 の契約）、
  `projection.py`（確定値→構造の投影）、`service.py`（確定判定・一覧・単一/paged読み出し）
- 新規 `backend/app/api/v2/structured.py`（`/api/v2/structured/status`、`/documents`、`/documents/{jobId}`）
- `backend/app/core/config.py`：`STRUCTURED_DATA_ENABLED`（既定false）を `AppSettings` / `RawSettings` / `from_raw` へ追加
- `backend/app/main.py`：router登録
- 文書：本文書、`AGENTS.md` 冒頭、`docs/ocr-safety-contract.md` §7.4と§12.12の追記

製品のOCR provider/prompt/schema、mapping/association/crop、補正・確定・出力経路、通常DBの
schema・データは変更していない。新APIは読み取り専用で、既存OCR contractを維持した。
deployのcompose設定は変更していない（ベータでの有効化は公開受入時に決める）。

**検証**

- `ruff check` / `ruff format --check`：対象5ファイル通過。
- `mypy`（strict）：対象ファイルの指摘0。依存先の既知指摘（job_service等、計89件）は変更前と同じ範囲で、本件の対象ではない。
- 通常 `backend/data/app.db` のコピーを専用data dirへ置き、`StructuredDocumentService` と
  `OcrResultService.excel_table` の出力を突き合わせた。確定済み3件（単一画像）すべてで、
  基本項目→見出し→表（行列順）→追加項目の値と順序がXLSX出力と一致。
  「UX検証_こよみ」は72欄、表「売却記録」7列×10行、追加項目2、readable 50 / blank 22、
  logicalFieldId 72/72。識別値は（生年月日=2014年9月27日、名号=こよみ）。
- 同コピーでTestClient（loopback origin）：`/status` enabled、`/documents?q=こよみ` が1件、
  存在しない名前で0件、`/documents/{jobId}` 200、未知IDは404 `STRUCTURED_DOCUMENT_NOT_FOUND`、
  REVIEW_REQUIRED のjobは409 `STRUCTURED_NOT_CONFIRMED`、フラグ無効時は503 `STRUCTURED_DATA_DISABLED`。
- 既存 `tests/integration/test_health.py` 通過。`test_jobs_api.py` は実行環境に依存して結果が変わった。
  sandbox配下の一時ディレクトリでは3件がテンプレート準備jobの `atomic_write_bytes`
  （`NamedTemporaryFile`）の `FileNotFoundError` で失敗、project内 `data/tmp` を一時ディレクトリに
  すると11件通過・1件（`test_template_upload_is_queued_and_processed`）が `POST /template-jobs` 422 で失敗し、
  再実行で失敗箇所が変わった。いずれもv1テンプレート準備経路で、本件のrouter登録・フラグ追加は
  その経路に触れていない。再現が一定しないため本件では切り分けを完了せず、全件成功とは扱わない。
  pytestの既定tmp（`%TEMP%\pytest-of-hanam`）は sandbox で作成できず `--basetemp` を指定した。
  検証用の一時ディレクトリは削除済み。
- 検証スクリプトは製品外（セッションのscratchpad）に置き、製品へ残していない。

**未検証**

- pagedの投影は静的検査のみ。ローカルDBにpaged確定が0件で、既存の paged 統合テストは
  この checkout に `tests/integration/test_print_layout_api.py`（fixture）が無く収集できない
  （変更前からの状態）。段階Cで実データまたはfixture整備後に確認する。
- Webベータ（cloud gateway・利用者別DB）での実HTTPは未確認。慣例に沿って `create_business_app`
  へ登録しただけで、cloud側のコード変更はない。
- frontendの変更はない（段階B）。

## 9. 段階Bの実装記録（2026-10-17）

**変更ファイル**

- 新規 `backend/app/structured/interpretation.py`：日付（西暦・年号・2桁年）と数値の汎用読み取り。
  複数の読み方がある文字列は候補をすべて返し、今日より後の日付は除外する。保存はしない。
- 新規 `backend/app/structured/decisions.py`：GPT-6 Luna / Decisions への1回限りのHTTP呼出し
  （`ApiBudget.client("qa.decisions")`、timeout 10秒、応答のmodel/answers/confidence検証）。
  テストは台本どおりの応答を返す差し替え（制御応答）を使う。
- 新規 `backend/app/structured/qa.py`：質問応答エンジン。書類の特定（書類名・識別値の部分一致、
  AIなし）→ 対象の欄/列・処理・行範囲の有限選択（Decisions 1回）→ 年号の読み方（必要時に
  Decisions 1回）→ サーバーでの決定的な計算（値・経過期間・日数・合計・件数・最大・最小）。
  confidence 0.70未満・拒否は推測せず聞き返し、確信のあった選択は保留質問と一緒に保持して
  利用者の回答後に再質問しない。1質問あたりのモデル呼出し上限4回。自由生成（Responses）は使わない。
- `backend/app/structured/contracts.py`：質問要求/回答/聞き返し/根拠の契約。`/status` に `qaConnected`。
- `backend/app/api/v2/structured.py`：`POST /api/v2/structured/questions`。
- frontend 新規 `src/structured/client.ts`、`src/pages/AskPage.tsx`、`src/App.tsx` のメニュー「質問」、
  `src/styles.css` の `.ask-*`。回答は文章＋表＋注意点＋根拠リンク（`?job=&field=` で該当欄のレビューへ）。
  聞き返しは選択肢ボタンで続行する。音声入口は未接続（同じAPIをSTT済みテキストで呼べる）。

製品のOCR・mapping・補正・確定・出力経路、通常DBのschema・業務データは変更していない。
通常DBの変更は共有API台帳の5行だけ。既存OCR contractを維持した。

**検証**

- 制御応答（実送信0）で48条件：日付/数値の読み取り14、エンジン（識別値による書類特定、
  低confidenceの聞き返し→回答の続行、書類の聞き返しはAIなし、年号の2回目呼出し、合計、
  該当欄なし、不正応答の拒否、未接続時503、期限切れ回答409）、HTTP境界（空文はreplyTo付きだけ許可、
  余剰属性422）。対象ファイルの ruff / mypy（strict）指摘0。
- frontend build 通過。vitest 176件は初回1件が間欠失敗、再実行2回は全件通過。
- 実画面（専用 backend 8041 / vite 5241、通常 `.env` の実DB）で「母牛こよみの子牛は今何か月？」を送信。
  **実送信5回**（台帳441〜445、全件RESPONSE、累計445/924・残479）。実モデルは対象の欄・処理・行・
  年号の4問すべてで confidence 0.70 未満を返し、それぞれ画面の選択肢1回で続行、最後は呼出し0回で
  表（7行の経過期間、平成読み）を得た。この帳票には分娩日の列がなく質問と欄が一致しないため、
  低confidenceは妥当な面があるが、分娩日のある実テンプレートでの精度・速度は未測定。
- 実画面で見つけた2件を修正：replyTo付きの空文を契約が拒否していた点、年号の聞き返し中に
  見出し/根拠が付いていた点。回答済みの聞き返しの選択肢が残る表示も修正し再build。

**未検証・残課題**

- 分娩日列のある実テンプレートでの質問品質、confidence閾値の妥当性（4問とも0.70未満だった）。
- paged確定結果での質問、Webベータ（cloud gateway）経由、音声入口（段階C）。
- 複数書類にまたがる集計（現在は1書類を特定してから答える。横断は書類の一覧・絞り込みまで）。
- 会話の継続（「その体重は？」のような前の回答への参照）は未対応。

## 10. 段階Cの実装記録（2026-10-17）：音声入口

利用者指示で実テンプレートでの品質確認は保留し、段階Cの音声入口を先に実装した。索引テーブルは
現在の件数で不要、paged実データは引き続き0件のため、本節は音声入口だけを扱う。

**方式**：音声は文字起こしだけを行い、聞き取った文はブラウザーが `POST /questions` へ送る。
質問の解釈・回答・聞き返し・台帳は、文字入力と完全に同じ経路を通る。字幕や文字起こしが
それ自体で何かを答えたり保存したりすることはない。ブラウザーのWeb Speech APIは、
製品のSTT方針（OpenAI＋共有台帳）と異なるため採用しなかった。

**変更ファイル**

- 新規 `backend/app/structured/voice.py`：`QuestionVoice.run`。既存の編集用音声経路
  （`editing/infrastructure/voice_stream.py`）からPCM受信・区間commit・transcript対応付けの
  部分だけを写し、編集session・binding・command・hold・VoiceStream表への依存を外した。
  接続時に `qa.voice.connect`（cloud側では300秒前払いの扱い）、区間ごとに `qa.voice.audio` を
  共有台帳へ予約する。接続300秒・1発話180秒・最終認識待ち15秒・1接続64発話の上限、
  105秒経過後の発話境界での接続更新、Origin/loopback検査は編集用と同じ。
  認識の prompt は質問向けの汎用文、keywords は確定済み書類の書類名・識別値（最大100）。
- `backend/app/api/v2/structured.py`：WebSocket `/api/v2/structured/voice`（フラグ無効・未設定は
  accept前に1008で閉じる）、`/status` に `voiceConnected`。`main.py` でrouter登録。
- 新規 `frontend/src/structured/voiceCapture.ts`：`QuestionVoiceCapture`。既存 `voiceCapture.ts` の
  マイク取得・VAD・24kHz PCM・区間送信を写し、store/bindingを外した。無音1.2秒で発話を閉じ、
  `transcript.completed` の文を呼び出し元へ渡す。
- `frontend/src/pages/AskPage.tsx`：「音声で質問」ボタン（`voiceConnected` のときだけ表示）、
  字幕/状態表示。聞き取った文はそのまま質問として送る。`styles.css` に `.ask-mic` 等。

**検証**

- 制御応答（偽の文字起こし上流、実送信0）で17条件：ready、2区間の発話が manifest 順に結合、
  同一接続での2発話、delta中継、設定に質問向けpromptと書類名keywordsが入る、区間外PCM・
  manifest不一致・余剰属性の拒否とerror通知、取消後の次発話、外部Originの拒否(1008)、
  未設定時の拒否、台帳コピーに connect 5件・audio 5件。
- 質問応答の制御応答48条件、frontend 176件、build、対象ファイルの ruff/mypy 指摘0。
- 実画面（専用8041/5241）で「音声で質問」ボタンの表示を確認。検証用ブラウザーはマイクが
  使えないため、マイク拒否時の表示だけ確認し（英語の生メッセージが出たので日本語へ修正済み）、
  WebSocket接続・実文字起こしは未実施。実台帳への音声予約は0件。

**未検証・残課題**

- 実マイクでの文字起こしと質問送信（利用者の参加が必要）。認識精度、keywordsの効果、
  無音1.2秒の境界が質問文に適切か。
- 聞き返し中に話した文は新しい質問として送られる（選択肢の音声回答は未対応）。
- 連続聞き取り中の質問送信が重なった場合の表示（送信中は次の文を待たせていない）。
- cloud gateway経由のWebSocket、paged、索引テーブルは引き続き未着手。

## 11. 質問画面のUXと聞き返しの見直し（2026-10-18）

利用者指摘「画面遷移で戻る」「聞き返しが多すぎる」「選択肢にない場合にチャットで送れない」「見た目・フロー」による。
段階Bの「確信度0.70未満は聞き返し」を変更した（実モデルは4問とも0.70未満で毎回聞き返していた）。
有限選択・サーバー計算・自由生成なし・保存しない、の境界は変えていない。

**流れ（`backend/app/structured/qa.py`）**

- 質問文（回答文があれば回答文を先に）に書かれた欄名・列名は、そのまま対象にする（最長一致、表名も書かれていればその表、
  書類名・識別値の部分は除いて照合）。AIには欄を選ばせない。
- AIの選択は 0.70以上ならそのまま、0.30以上なら使って「質問を『…』についてと読みました」と示し、
  ほかの欄をワンクリックで選び直せる（`question.optional=true`）。0.30未満・none・拒否のときだけ欄を聞き返す。
- 処理（合計・経過など）が不確かなら「値をそのまま見る」で答えて選び直しを付ける。行は既定で全行。
  年号は聞き返さず、曖昧な日付を最も多く読める年号（同数なら新しい方）で読み、選び直しを付ける。
- 一度回答した後は、AIの最良の選択を使い（表示して選び直し可能）、二度目の聞き返しをしない。
- 聞き返しへの回答は選択肢（`replyTo.value`）か利用者の言葉（`replyTo.text`）。言葉は元の質問に「（補足：…）」として足し、
  欄名・書類名が書かれていればそれを使い、なければAIへ補足付きで渡す。
- 書類：質問の書類名・識別値で絞り、書かれた欄名を持つ書類だけに絞る。複数残れば書類ごとに同じ欄を答える
  （表に「書類」列、根拠は書類名付き）。同じテンプレートの書類だけが残った場合も書類ごと。テンプレートが異なり欄名もないときだけ書類を聞き返す。
  他の書類での欄は説明文の一致、なければ同じ名前が1つだけの欄で対応付ける（ない書類は対象外と表示）。
- 日付・数値の計算で対象欄に読める値がないときは、日付（数値）が入った欄を選択肢にして聞き返す。
- AI未接続・時間切れ・予算切れ・不正応答では失敗にせず、欄名が書かれていれば答え、なければ欄を選んでもらう（注記付き）。
- 数値：「826-100」のように数字が複数ある値は数値として読まず除外して注意を出す（従来は826として合計に入っていた）。
  同じ列に4桁以上の整数があるときの「762.300」は千の区切りとして読み、注意を1行にまとめる。
- 日付：日の後ろの短い文字（「26年9月27日生」の「生」）は日付の一部として扱わない。
- 聞き返しの保持時間を600秒→3600秒。

**契約**：`QuestionReply.value` は任意になり `text` を追加（どちらか一方）。`Clarification.optional`、`QuestionResult.notes` を追加。

**画面（`frontend/src/pages/AskPage.tsx`、`structured/askStore.ts`、`ask.css`）**

- 会話・選んだ書類・入力中の文・根拠の表示を画面外のストアに置き、sessionStorageにも保存。書類を開いて戻る・ホームへ移る・
  再読み込みでも消えない。回答待ちのまま画面を離れても回答は会話に入る（再読み込み時に送信中だったものは再送ボタン付きで表示）。
- 書類を開いたときの戻るボタンは「質問へ戻る」、根拠から開くとその欄を選んだ状態で開く。質問画面はURL（`?view=ask`）に残す。
- 聞き返し中の入力は「〜への回答として送ります」と表示して回答として送る（「新しい質問にする」で切替）。
- 期限切れの聞き返しへの回答は、元の質問に回答を書き足して自動で送り直す。
- Enterで送信（変換中は送らない）・Shift+Enterで改行、回答待ちでも入力できる。例文は利用者の書類名と欄名から作る（固定の業種語なし）。
- 見た目：大きな見出し帯をやめ、会話を画面の高さに収めて内側だけスクロール。吹き出し、読み方の注記、確認事項（4件以上は折りたたみ）、
  計算の過程は折りたたみ、選び直しは折りたたみ。長い回答は質問の位置から表示。「AI呼出しn回」の表示を削除。

**検証**（実送信0回、共有台帳は不変）

- 制御応答48条件（複製DB、台本のDecisions）：確信時の一回回答、0.4で回答＋選び直し、0.2で聞き返し→言葉で回答、
  言葉が欄名でない場合の補足付き再選択、欄名での書類特定、2書類の横断回答、書類の聞き返し→言葉で書類指定、対象内の書類横断と聞き返し、
  none・不正応答の聞き返し、年号の既定と選び直し、AIなしでの回答と処理の選び直し、千区切りと「826-100」の除外、日付の接尾字、
  日付・数値のない欄で該当欄の提示、HTTP境界（text返信・value/text同時/両方なしの422）。
- 前回の48条件スクリプトは、0.4で聞き返す旧仕様の箇所だけが意図どおり不一致（解釈部分は全件一致）。
- ruff・mypy（対象ファイルの指摘0）、frontend build、vitest 177件。
- 実画面（複製データ `D:\llm-ocr-archive\ask-ux-20261018\data`、台本のDecisions、APIキーは偽値）：1280px・375pxで、
  横断回答、書類の聞き返し→自由入力、期限切れからの自動回復、根拠→書類（欄選択済み）→「質問へ戻る」で会話・根拠が残ること、
  送信直後にホームへ移って戻っても回答が入ること、再読み込みで会話が残ること、ページ・横方向のスクロールが出ないこと。
- 未検証：実モデル（GPT-6 Luna / Decisions）での確信度の分布と回答品質、実マイク。
