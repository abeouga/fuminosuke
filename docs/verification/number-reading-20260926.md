# 番号を読む: 最小XLSXによる実画面確認（2026-09-26）

## 訂正後の現行仕様: OCRを使わない自動採番

利用者の追加指示により、以下の初回OCR方式を自動採番に変更した。
番号指定セルを上から順（同じyなら左から順）に1開始・1増分で採番し、
レイアウト保存時にmappingの`sequenceNumber`へ固定する。
providerへの番号crop送信とPaddle presence検査は行わず、保存値からFieldReadingを作る。
番号だけの要求はprovider呼出しを省略する。欄のsourceRegionId・boundingBoxと通常レビュー操作を保持する。
旧受付snapshotは変更せず、sequenceNumberがない旧版のOCR動作は維持する。

変更追加箇所: `ocr/sequence_numbers.py`、request preparer、response finalizer、workflow、CLI、
mapping/config/API schema、layout mapping生成、LayoutEditor説明。対応するpackage schemaも再生成。

### 実画面の再確認

- テンプレート「番号欄テスト 20260926」のv2を編集・保存・有効化。
- v2 ID: `96639121-4165-4224-8571-9351f4ef62c7`、ACTIVE。v1はARCHIVED。
- 同じ最小Excelから作ったPNGで実行したjob: `f09eb4ab-8ea9-4ac2-a684-8756af7e7eff`。
- 結果画面: 番号=1、番号2=2、番号3=3、他6欄は空欄。番号2選択時のcropも確認。
- jobの`mapped_cells.json`: 番号3欄はすべて`sentToOpenAi=false`、
  `localResolution=automatic_sequence_number`。各responseの値と物理セル対応を確認。
- 通常の文字欄6つだけがOpenAI送信対象。結果は未確定で保持。
- 有効化APIは200だが画面の系列検索に失敗する既存エラーが発生。DBのACTIVEをread-only確認し、
  再読み込み後の一覧にv2が表示されることを確認して続行。有効化APIは再送していない。

### 検証

短いrequest/finalization既存contract 5件と、新規2ケースを確認した。
新規ケースは番号のみ／文字混在、空白画像、保存番号7・4を逆順に与えた場合を使い、
provider送信除外・実行時の再採番禁止・crop対応を検証する。
初回はpytest親ディレクトリ不足と新規fixtureの引数誤りで失敗し、それぞれ修正後に通過。
最終実行対象は `test_automatic_sequence_numbers.py`、
`test_phase5a_request_preparation_contract.py`、`test_phase5b_response_finalization_contract.py`。
一時成果物は `D:/llm-ocr-archive/pytest/number-sequence-20260926-*`。
frontend build、変更Pythonのruff、git diff --checkは通過。
長時間の全体テストは実施していない。複数ページ・文書間通し番号・任意の開始値は今回の実画面確認対象外。

以下は訂正前の初回実装の検証履歴であり、現在の動作仕様ではない。

## 変更範囲

- `frontend/src/components/LayoutEditor.tsx`: 記入欄に「番号を読む」を追加。保存先未設定の場合のみ「番号」の独立項目を作成し、既存targetPathを維持。
- `frontend/src/types.ts`, `frontend/src/pages/TemplatePage.tsx`: 型と工程説明。
- `backend/app/layout_analysis/models.py`, `backend/app/domain/template_config.py`, `backend/app/api/v1/routes/templates.py`: `readingMode=number`の保存・API往復。
- `backend/app/services/layout_service.py`, `backend/app/services/ocr_request_preparation_service.py`, `backend/app/cli/main.py`: 明示した番号設定をmappingからOCR要求へ引き継ぐ。
- `backend/app/ocr/openai_provider.py`: 指定された番号欄だけ印刷番号も転記する条件付き指示。数値変換・自動採番・隣欄からの補完は禁止。
- `backend/app/services/template_definition_service.py`: 番号設定を保持できない別形式への変換を拒否。
- `backend/app/template_packages/schemas/template-v1.schema.json`: スキーマ再生成。
- `docs/ocr-safety-contract.md`: 明示要求による番号設定の契約を追記。

model、応答schema、物理セル/crop、association、review補正・revision、出力先対応は変更していない。
既存OCR contractの対応関係を維持し、番号を明示した欄の転記対象だけを拡張した。
既存未コミットのテンプレート改名等の変更は保持した。

## 入力と期待結果

`number-field-minimal.xlsx` は1シート「番号欄確認」、A1:C4のみ。
A1は空、B1/C1は「選んだ言葉」「選んだ理由」、A2:A4は印刷番号1,2,3、残り6欄は空。
番号見出しを作らずA2:A4に「番号を読む」を指定する。期待結果は番号=1、番号2=2、番号3=3、他6欄は空欄。
OCR入力は、このExcelをアプリで取り込んだ際の原寸基準PNGのコピー。

## 実画面で行った操作と結果

ChromeでXLSXを取り込み、テンプレート「番号欄テスト 20260926」を作成した。
番号設定、保存先の自動作成、文字→番号の切替、保存、一覧から再開して選択状態の保持を確認。
切替後も9記入欄・9割当・未割当0で、番号項目は重複追加されなかった。
API応答で設定が落ちる不具合をこの確認中に検出し、FieldMappingViewに設定を追加して修正。
最終保存YAMLで3つのmappingすべてにreadingMode=numberが残ることを確認した。
XLSX構造確定→有効化→画像選択→読取開始を画面操作で実行した。

- Template ID: `e5a2f45c-6492-484a-98b6-417ca86cfc54`
- Version: `5814f845-4b56-4ad4-931f-046a585113d2` / v1 / ACTIVE（DB read-only確認）
- 作成job: `abda07b0-5626-4db8-a335-9a79d158dee5`
- OCR job: `c4d9454f-1787-4ee1-b6fc-8ad3e4af5642` / REVIEW_REQUIRED
- 実結果: 番号=1、番号2=2、番号3=3、他6欄は空欄。すべて画面でOK、要確認0。
- 番号2を選択して、対応cropが印刷された2（layout_xlsx_A3）であることをスクリーンショットで確認。
- 読取結果は未確定で残した。Google同期・出力確定は行っていない。

最初の画像選択では操作側のパス指定が不正で受付前に失敗した。サーバーにOCR POSTがないことと実ファイル位置を確認し、正しいファイルを選び直した。成立したOCR jobは1件。

## 短い検査と限界

- `npm run build` (frontend): PASS。既存の500 kB超bundle警告あり。
- `python -m ruff check`（上記変更Pythonファイル）: PASS。
- `python -m app.template_packages.schema_export`: 実行済み。
- `git diff --check`: PASS。

依頼に従い長時間の全体テスト・contract runner・E2Eスイートは実行していない。
この検証は見出しなしの独立番号欄を持つ最小単一ページ帳票に限定される。
①・先頭ゼロ・不鮮明画像・複数ページ・繰り返しグループ・補正確定・XLSX出力は今回の実OCRでは未検証。
全領域のOCR安全性を完全には検証できていない。
