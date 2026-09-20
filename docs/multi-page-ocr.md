# 複数ページOCR実行基盤の実装報告

2026-09-13追記: [ページ別レビュー](paged-review.md)で専用レビュー・補正と保存表を追加した。
さらに[全ページ確定](paged-confirmation.md)と受付schemaVersion 2を追加した。
以下の未対応記述・migrationなしは初期基盤の記録。[全ページXLSX出力](paged-output.md)と
[ページ別の文書識別・名前候補](paged-document-metadata.md)を追加したが、
書類一覧統合と本番開放は未完了。

2026-09-07。**基盤は実装・fixture検証済み。本番の複数ページOCRは未開放であり、Phase全体は未完了。**
指定XLSXの主要帳票は既存参考rendererの対象外、または印刷画像との位置精度が未検証です。
拒否を解除するための精度証明を省略していません。実OpenAI / Google API呼出しは0回です。

## 受付・実行モデル

既存は `EXTRACT_DOCUMENT`に1 JobInput、1 templateSnapshot、1 OcrResultです。
`ADD_PAGE` / `RESCAN_PAGE`はLedgerDocumentPageの照合・更新操作であり、今回の入力bindingとは別です。
既存単一ページ経路を保持し、新形式のjobだけページorchestratorへ分岐します。

```text
ProcessingJob
  config_snapshot_json.jobPages [immutable, schemaVersion=1]
    templateRevision / printLayout / executionConfig
    pages[]
      pageId / pageNumber / pageRange
      inputId / clientInputId / inputRevision / inputSha256
      templateVersionId / templateSnapshot
      transform / fields [sourceRange, cellId, bindings, rectangle]
      fieldMappings / OCR provider, prompt, schema identifiers
  JobInput × N [existing table, independent UUIDs]
  JobArtifact
    page.<pageId>.* [normalization, alignment, crops, mappedCells, diagnostics]
    pageResult.<pageId> [per-page result / failure]
    documentPages [all page results, completion or partial_failure]
```

1. TemplatePageのIDは版IDと保存済み基準画像artifact IDからUUIDv5で生成します。
   版をまたいで同じIDとみなしません。改ページ再保存で基準画像世代が変わればIDも変わります。
   既存の保存printLayoutに後付け書込みは行いません。
2. InputPageは既存JobInput UUID。clientInputIdは選択入力の照合に使用します。
   inputRevisionはクライアント選択revisionを保存し、アップロード内容SHAとの一致を検査します。
   独立したアップロード編集API／サーバー側入力revision台帳は未実装です。
3. manifestのuploadIdsはmultipart画像との対応を示し、bindingsはTemplatePage IDと入力IDを結びます。
   逆順アップロードでも保存TemplatePage IDの順に処理します。欠落・余剰・重複ID・画像SHA重複を拒否します。
4. templateRevisionはprint-layout revisionとconfigファイルSHAの合成値です。
   mutation lock + BEGIN IMMEDIATE内で版、config、binding、全mapping、画像SHAを確認し、
   commit前にも版状態とrevisionを再照合します。1 jobと全JobInputを同じtransactionでcommitします。
   commit後にenqueueするため、停止後は既存QUEUED recoveryが対象を拾えます。
5. 基準PNGをjob専用template_snapshot以下へコピーします。元artifact・SHA・config本体とSHAも保存します。
   入力原本はJobInputとjob専用storageに保持します。認証秘密値はsnapshotへ保存しません。
6. enabled mappingは全体で過不足なく、一つのページに所属する必要があります。
   page configはそのページのmappingのみを含み、元mapping ID・targetPathを維持します。
   ページをまたぐ文書識別設定、mark、composite field、読み取り項目がないページは現在拒否します。
7. 実行時は現行TemplateVersionを読みません。全入力・全基準画像のSHAとsnapshot全体のdigestを事前検証します。
   OCR・registration・imageProcessing・segmentation設定が受付時と変わった場合は実行を拒否します。
   設定を元へ戻さずに旧設定で再実行する仕組みは未実装です。
8. `logical cell → saved uniform page transform → reference pixels`は既存page_planを使用します。
   mapping矩形をpage-local座標へ変換し、その後のscan normalization / registrationは既存抽出処理を使用します。
   legacyのX/Y画像合わせ補正をuniform scaleへ置き換えていません。
9. provider、prompt、schema、request associationの実装は変更していません。
   既存request preparer／provider／response finalizerを再利用します。
10. 各ページのresultにpage/input identity、reference SHA、snapshot digest、fields、quality、
    validation、mappedCells、diagnostics、provider情報を保持します。field IDからpageと元セルへ追跡できます。
    1枚用OcrResultには保存せず、documentPages artifactを集約結果の保存先とします。
    crop等の物理ファイル名はpage IDと従来filenameのSHA-256をbase64url表記にします。
    DBのartifact名には完全なpage／field IDを残し、Windowsの深い一時パス制約を回避します。
11. 一部失敗でも他ページを処理し、成功結果を保存します。jobはFAILED、集約結果はpartial_failureです。
    全ページ成功なら既存runnerのREVIEW_REQUIREDになります。これは確定・出力完了ではありません。
    retryは元snapshotのdigestが一致する成功結果を再利用し、失敗ページのみ再実行します。
    cancel時は既存runnerのCANCELLED処理に従います。

## API・画面・未開放の境界

| 入口 | 動作 |
| --- | --- |
| GET /api/v1/template-versions/{id}/ocr-pages | ページID、範囲、基準画像URL、受付revision、利用可否 |
| POST /api/v1/ocr-page-jobs | multipartのmanifestとfiles。明示binding、Idempotency-Key対応 |
| GET /api/v1/ocr-jobs/{id}/page-results | 集約結果をSHA検証後に返す |
| POST /api/v1/ocr-jobs | 旧単一画像APIを維持。新printLayoutを渡せば従来どおり拒否 |
| 既存retry | jobPages形式ではページsnapshotを検査し、元設定で再試行 |
| 既存result / corrections / confirm / reopen | MULTI_PAGE_REVIEW_NOT_AVAILABLE |
| 既存export | MULTI_PAGE_OUTPUT_NOT_AVAILABLE |

認証は既存protected routerを使用します。レビュー・確定・Google同期・XLSX出力の既存1ページ処理へ
新結果を流しません。元XLSXのpageSetupやmanual breaksは書き換えません。
DB表・migrationファイルの追加および実DBへのmigration適用はありません。

OcrPageの新printLayout選択時に、Page N・range・基準thumbnail・個別FilePicker・入力thumbnail・指定状態を
表示するコンポーネントを追加しました。全ページ未指定または基準画像未検証なら送信不可です。
取得失敗時にPage 1だけの旧画面へfallbackしません。新jobの旧レビュー自動遷移も止めています。
現在は新printLayoutのACTIVE化も拒否されるため、この画面を通常運用で利用可能とはしていません。

**本番解除条件は未実装です。** `JobPageService._require_qualified_reference`は常に
PRINT_REFERENCE_NOT_VALIDATEDを返します。適格性を証明できたrendererがまだないためです。
DBのocrReadyフラグだけでこの制約を解除する経路はありません。テストは特定synthetic fixtureについてのみ
この適格性チェックを置換し、その他の受付・snapshot・画像処理・永続化は実装を通しています。

## 指定された実XLSX

`C:\Users\hanam\Desktop\xlsx`の5冊を原本のまま読みました。
セル値は報告書へ転記せず、構造と元ファイルSHAを
[xlsx-page-input-audit.json](xlsx-page-input-audit.json)に保存しました。読取前後で全5冊のSHAが一致します。

| 原本 | 現行描画で確認された主な障害 |
| --- | --- |
| 1 給水装置台帳等複写申込書.xlsx | 20,000セルの描画上限超過 |
| キャリア・プラン作成補助シート（学生用）.xlsx | 主要シートにhidden／回転文字・セル内縮小。Version補助シートを帳票の代わりには使用しない |
| 再検査登録依頼書.xlsx | 回転文字・セル内縮小 |
| 口座振替依頼書.xlsx | 図形・画像・部品、hidden、回転文字・縮小、一部シートの数式 |
| 市・県民税申告書.xlsx | 20,000セルの描画上限超過 |

上限検査は既存print sourceの使用範囲に基づきます。原本の使用範囲・設定を削ったり、
hiddenを解除したり、図形を落として「対応」とはしていません。
実Excel／LibreOfficeのページ画像とのpixel誤差は未測定です。安全な基準画像を作れていません。
2ページfixtureでは、左・右・上・下のfield罫線とpage-local rectangleの辺が0px差で一致し、
実抽出のcrop.bboxもそのrectangleに既存paddingを加えた値と一致しました。
これはPillow内の座標一貫性の証拠であり、Excel組版への忠実性の証拠ではありません。

## 検証とGit

- 開始／終了HEAD: `d33e192cb4483fe3a2a857a2b5f51f046a8f8fbe`、main、同一worktree。
- 開始時は前フェーズの12 tracked変更と12 untracked成果物が存在。それらを保持して追加しました。
- 実DBはread-onlyでrevision `0031_template_field_identity`を確認しました。migration headも0031です。
- 変更前baseline: `backend/.venv/Scripts/python.exe scripts/verify_ocr_contract.py`: 194 passed。
  関連job/import/print-layout testsも起動しましたが、その実行結果は最終根拠に使用していません。
- 新規backend tests: `tests/integration/test_job_pages.py` 10 passed。
  2ページ実抽出とmock OCR、明示binding、欠落／重複／未知page／stale版／入力SHA、
  page別config、過去snapshot不変、基準SHA改変、Page 2失敗・既存runner/retry、review/output拒否、
  fixtureの4辺とcrop.bboxを検証しました。
- OCR contract最終: 同じscriptで194 passed。既存testの削除・skip追加・assertion弱体化なし。
- backend full: 初回585 passed, 1 skipped。その後、長いbasetempで新規2テストが失敗しました。
  Windowsでページcropの一時ファイルpathが長すぎることを実tracebackで確認し、上記の物理filenameへ修正。
  テストの削除・短いbasetempへの逃避は行わず、さらに長い`.pt-pages-full-corrected-0907`で
  `python -m pytest -q -o addopts='' -p no:cacheprovider --basetemp=.pt-pages-full-corrected-0907`を実行。
  **最終585 passed, 1 skipped（174.05秒）**。修正後のOCR contractも194 passedです。
- frontend full: `npm test`: 238 passed。新規OcrPageBindings 3件、既存OcrPage 17件も対象。
- frontend `npm run build`: 成功。既存の500kB chunk警告あり。
- 今回変更したPython対象のRuff: 新規エラーなし。全体は既存47件、変更対象外／前フェーズのimporterです。
- mypy: 変更前後とも同じ6ファイルの36件。今回追加・変更した実行基盤に新規エラーなし。
  openpyxl / pypdfium2 stub不足等の全体既存エラーを無効化していません。
- 実ブラウザー: 分離fixture DBと新コンポーネントを使用する一時ハーネスで、
  Page 1 B2:D31 / Page 2 B32:D63、基準画像、未入力表示、実OCR禁止と無効ボタンをDOM・画面で確認。
  最終表示のconsole warning/errorは0。実ファイル選択と本番アプリ全体のE2Eは未実施。
- OpenAI / Google実API: 0 calls。認識品質、実response association mismatch等の実測値は未測定。
- `git diff --check`: 成功。staging・commit・pushなし。検証用9028／5198サーバーとブラウザーtabは終了済み。
  今回生成したテスト用ディレクトリとハーネスは`C:\Users\hanam\.codex\tmp\llm-ocr-job-pages-20260907`へ移動し、
  以前から存在する他の一時ディレクトリには触れていません。
  終了worktreeは18 modified、21 untracked成果物（前フェーズ分を含む）。HEADは開始時と同一です。

## 今回の変更ファイル

新規: `backend/app/domain/job_pages.py`、`backend/app/services/job_page_service.py`、
`backend/app/services/paged_ocr_execution.py`、`backend/app/api/v1/routes/job_pages.py`、
`backend/tests/integration/test_job_pages.py`、`frontend/src/components/OcrPageBindings.tsx`、
同`.test.tsx`、この文書、`docs/xlsx-page-input-audit.json`。

追記・変更: `backend/app/api/v1/router.py`、`backend/app/core/lifecycle.py`、
`backend/app/image_processing/artifacts.py`、`backend/app/services/document_ocr_execution_service.py`、
`extraction_service.py`、`job_service.py`、`ocr_workflow_service.py`、`ocr_result_service.py`、
`frontend/src/api.ts`、`frontend/src/pages/OcrPage.tsx`、`docs/ocr-safety-contract.md`。
前フェーズの他の変更ファイルは今回の変更として集計していません。

## 残課題と次Phase

1. 実XLSXのprint area／使用範囲と高度な組版の扱いを確定し、実ページ画像とfield矩形の誤差許容値を定義・測定する。
   その証拠を保存するrenderer qualificationを実装してから、本番受付／ACTIVE化を開放する。
2. 現在拒否している文書識別・複合field・mark・空fieldページのpage-local configを追加する。
3. 実OpenAIを開放する前に、全ページ合計のcall budget、prompt/schemaリソース本体の固定、
   全ページのrequest/response対応を確認する。現provider生成はページごとなので、既存のcall上限は
   各ページへ適用される。本番開放前にdocument全体の予算契約を決定する必要がある。
4. page別閲覧・補正・revision・確定snapshotを設計し、全ページの値を安全に出力へ結ぶ。
5. filesystem書込み失敗時に未commit jobの孤立ファイルを回収する仕組み、
   source/referenceのDB blobからの復元、ページ結果のbackup/cloud同期まで保証する。
   今回はDBの部分commitを避けていますが、失敗時のfilesystem garbage collectionは未実装です。
6. 設定変更後も受付設定のまま実行するruntime構築、入力revisionの独立編集・照合、
   UIでのページ別進捗・失敗表示と実ファイル選択E2Eを拡張する。

旧単一ページのOCR contractを維持し、新しい受付・ページ実行・結果保存の形式を追加しました。
実帳票からレビュー・出力までのOCR安全性を完全には検証できていません。
