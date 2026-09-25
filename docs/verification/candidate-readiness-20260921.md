# 候補OCR準備確認 — 2026-09-21

## 目的と変更境界

「複数ページの対応する欄から文字候補を得て、人間が確認する」という製品ゴールを維持する。
正式資格の完全再発行とレビュー前提の候補利用を分離する実装承認に基づき、Safety Contract
§12.10を追加した。旧§12.9の受付経路と正式資格、診断、隔離・OCRのガードは削除しない。

別policy `paged-candidate-readiness-v1`でDRAFTに対して再描画を実行する。
bootstrap native依存inventoryだけの差分を記録し、その他のexecution identityと
全ページPNGのSHA・サイズ・順序を照合する。所有原本・config・print plan・artifact・
geometry/glyph証拠を再検証し、現実装でgeometryを再計算する。
正式資格をPASS化せず、`qualified=false`の`CANDIDATE_READY`を返す。

以下は初期v1の記録である。v1では候補証拠の有効期間を24時間とし、GET/承認/受付で
現在環境を再照合していた。後続のSafety Contract §12.11と本書末尾の追記により、
通常利用は変更まで有効なv2 binding検証へ置き換えた。再描画は引き続き明示操作だけであり、
初回のgeometry/glyph検証や候補準備時の環境検査を省略していない。

layout/cellId/mappingId/targetPath/sourceRegionId/boundingBox/crop/page binding/revision、
原本XLSX、既存print artifactを変更しない。更新は別artifactとquality内のcandidate参照に限定する。
fork時にはcandidate参照を除き、正式資格再発行成功時には正式資格へ戻す。
有効化と受付が同じvalidatorを使い、新規受付snapshotにcandidate証拠bindingを固定する。
既存job/retryを新policyへ移行しない。

## 変更ファイル

- 新規: `backend/app/services/candidate_readiness_service.py`とunit/integrationテスト。
- backend接続: `api/v1/routes/print_layout.py`、`repositories/template_repository.py`、
  `services/job_page_service.py`、`services/template_version_service.py`、
  `services/reference_qualification_service.py`。
- frontend: `src/api.ts`、`src/printLayout.ts`、`components/ReferenceQualification.tsx`、
  `components/OcrPageBindings.tsx`、`pages/TemplatePage.tsx`と対象テスト。
- 文書: `AGENTS.md`、`docs/ocr-safety-contract.md`、`docs/multi-page-ocr.md`、本報告。

開始時から存在する他のdirty差分は保持し、本件の変更と混同しない。
staging/commit/push、既存差分の復元・整理は行わない。

## 検証記録

- 変更前OCR contract: 210 passed / 51.75秒。verifierのtargetsをASTから取り出して同一対象を
  D:の絶対basetempで実行。scriptのC:固定tempは本件では変更していない。
- backend初回対象: 58 passed / 65.75秒。追加の検証中環境変更・証拠不足・
  有効化後受付拒否まで含む新規専用テスト: 25 passed / 27.33秒。
  renderer/native測定は合成証拠fixture。実環境検証や実OCR成功とは区別する。
- frontend対象: 69 passed。初回追加後fullは441 passed。
  実画面で見つけた説明表示修正後の最終frontend full: 443 passed / 36 files（13.82秒）。
  build成功、既存chunkサイズ警告あり。未知policy、不完全証拠、期限切れの表示拒否も固定した。
- 対象Python Ruff成功。mypyの依存まで含む実行は既存5ファイルの51件で失敗
  （openpyxlのstub不足、Linux専用API、既存import/layout型エラー）。
  `--follow-imports=silent`で今回の6ファイルを明示対象にした検査は成功。
- 変更後OCR contract: 同じ210件がpassed / 63.80秒。新しいD:絶対basetempを使用。
- 追加のfork/再発行/検証中revision変更テストを全体試験と並行した回は、
  14 passed / 14 setup errors。共通のWindows renderer mutexを全体試験が使用し、
  fixture準備が`RENDERER_BUSY`で拒否された。guardは変更せず、全体試験後に単独再実行する。
- backend full: 2551 passed / 1 skipped / 3 warnings、2374.46秒。
  `.venv/Scripts/python.exe -m pytest -q -o addopts= -p no:cacheprovider
  --basetemp=D:/llm-ocr-archive/pytest-work/candidate-ready-full-20260921-a`。
  skipとopenpyxl deprecated警告は既存。全体試験の収集後に追加した3ケースは対象テストで別途確認する。
- 追加テスト単独最終再実行: 28 passed / 24.77秒。
  `.venv/Scripts/python.exe -m pytest tests/unit/test_candidate_readiness.py
  tests/integration/test_candidate_readiness_api.py -q -o addopts= -p no:cacheprovider
  --basetemp=D:/llm-ocr-archive/pytest-work/candidate-ready-target-20260921-f`。
- 追加テスト単独初回は27 passed / 1 failed。正式資格への復帰テストでcandidate専用の
  `_prepared`注入がGET側だけに残り、正式発行側と異なる合成reportを読んでいた。
  実製品は両クラスで同じ継承methodを使用する。復帰テストでは専用注入を戻し、
  発行/GETが同じfixture証拠を見るようにした。PASS判定のassertionは維持した。

## 実環境と未検証範囲

既存Chromeタブ1093604667を再利用し、変更前の画面を取得した。
V2は保存済み、記入欄150/設定済み150、未割当0/不完全0、資格UNVERIFIEDで有効化無効だった。
実行中/待機中jobがないことを確認して対象backendのみ再起動し、health正常を確認した。
保存済みの既存タブを再読込して変更を反映した。新規タブ・別ブラウザーは使用していない。

最初の候補準備ボタン操作は全体試験のrenderer mutexと競合して409 `RENDERER_BUSY`で停止。
画面に理由が表示され、操作は再び可能になった。candidate参照は保存されず、DRAFTを維持した。
競合解消後の成功を確認するまでは、候補利用可能/有効化済みとは扱わない。

### 実V2の候補準備成功

全体試験と対象試験の終了後、同じChromeの同じV2で準備を再実行し、
`候補OCR利用可`と承認ボタンの有効化を画面取得で確認した。

- version: `e3286670-4190-436a-a1b6-9f0d7e278b7c`、4ページ。
- candidate artifact: `df6db6fb-7845-439b-ab73-3b4d58f13325`。
- report: `artifacts/ebcb4798-6ae1-4cc6-8ecf-d660fba59ad5/qualification/31ae53259c4d477b8d503f527d863b75/candidate.json`。
- report SHA: `36ce7b88e4121a4c1253a6bb1327586fa34e58978d3a7b6dbf1f98065b204e36`。
- 準備期限: 2026-09-22 19:35:23 JST。正式資格のPASSとは表示していない。
- 実再描画で許容した差はbootstrap native inventory SHAのみ。
  旧`b6d2ce5fd5990e330f280dc2f997147849513060338680068b66b141917ea32d`、
  新`735e4e2b70f53bf16e9998b764587dce76eef6bb44b7d77209858fc360b4adbe`。
  両方ともrootCount 60 / fileCount 111 / totalBytes 42020900 / preloadAbsent true。
  差を生じた個別ファイルまでは、この集約証拠から断定しない。
- 読み取り専用DB比較で、準備前後のlayout、printLayout、candidate以外のquality、
  正式資格参照、configファイルSHA、ProcessingJob件数81が一致した。
  layout SHAは`f3579a0491a4e0f625a1123ea8ca552ca17ffb21a6c047f386d5b43c996ebe90`、
  config SHAは`23e93328bed95400dd9567b44a827ad5e1dfd955d82c3347efe0062e67c159a1`。

その後、既存の「承認して有効化」を押下した。
2026-09-21 19:37:38 JST、正規承認transactionが成功し、画面はV2を選択した読み取り画面へ戻った。
読み取り専用DBで次を確認した。

- V2 `e3286670-4190-436a-a1b6-9f0d7e278b7c`: ACTIVE。
- 系列`9dc80bd9-0070-4754-a271-7ff274729e14`のactive versionはV2。
- V1 `b92f4035-06be-4d09-8cc4-c0a55aba51ba`: ARCHIVED。削除ではなく履歴として保持。
- 承認後もlayout/printLayout/candidate以外のquality/config SHA、job件数81は準備前と一致。

「画像の準備へ」からページ別入力画面へ進み、4ページと画像選択欄を確認した。
この実画面で、catalogは利用可能なのに説明が正式資格だけを判定して誤警告を出す取り残しを発見。
`OcrPageBindings`にcandidateの説明分岐を追加した。受付の正本は引き続きcatalogとサーバーacceptであり、
表示文を根拠に受付を許可する変更はしていない。candidate表示とcatalog true/falseの両方をテストした。
ファイル未選択時の実行ボタン無効、catalog拒否時の無効化を維持した。

今回の分離でOffice再描画をGETから実行することはないが、glyph/geometry証拠の再計算と
現環境の照合は残り、実画面で読込待ちが発生した。利用時の待ち時間を解消した、
あるいは常時即時表示になったとは扱わない。証拠cache/重複GETの性能改善は本件に含めていない。

修正後、ファイル未選択であることを確認して同じChromeタブを再読込し、学生用V2を選び直した。
「この版は人間レビューを前提とした候補OCRを利用できます。正式資格の認定とは異なります。」と
4ページ（A1:AK28 / A29:AK60 / A61:AK89 / A90:AK102）の画像選択欄を確認し、画面を取得した。
正式資格の誤警告と受付停止の表示は出ていない。入力画像未指定のため実行ボタンは無効のままである。
computer-useスキルを用い、既存Chromeタブだけで確認した。新規タブ・別ブラウザーは使用しなかった。

## 未検証範囲・残る制約

本件の実OpenAI呼出しは0回。実入力ファイルは選択/送信していない。
今回の候補policyによる実OCRの認識精度測定、人間による全ページレビュー/確定/出力は未実施。
自動テスト通過や有効化成功を実文字の正しさ・筆画完全性の証明とはしない。
OCR安全性を完全には検証できていない。この時点では候補証拠の24時間期限、
ACTIVE証拠の自動更新未実装、利用時の証拠照合に要する待ち時間が残っていた。
後続v2で固定期限と重い通常時再検証は除いたが、実OCR品質の未測定は解消していない。

終了時の`git diff --check`成功。既存dirty差分は保持し、staging/commit/pushなし。

## 追記: 通常利用時の重い再検証防止

初期実装では、候補準備後の資格状態GET・有効化・catalog・OCR受付も`status_for_use()`から
保存済みgeometry/glyph証拠の全再計算とWSL/hostの現在照会へ入っていた。
Office再描画ではないが、通常画面の待ち時間と同じ検証の反復を発生させる原因だった。

`paged-candidate-readiness-v2`で、明示候補準備の完全検証と通常利用のbinding検証を分離した。
通常利用では版/revision・原本・plan・config・実装・候補artifact・renderer report・
参照artifact metadataを確認し、geometry/glyph/native inspectorを起動しない。
候補は固定24時間ではなく関連bindingの変更まで有効とし、旧v1はartifactを変更せず互換読取する。
OCR受付時の各reference PNG内容SHA、実入力の位置合わせ・crop・association、全ページレビューは維持する。

実DBを読み取り専用で確認し、既存V2 `e3286670-4190-436a-a1b6-9f0d7e278b7c`の旧v1証拠は、
DB/artifact更新なしで`paged-candidate-readiness-v2`、`validUntilChange=true`、`expiresAt=null`として
利用可能だった。同一processで候補状態を5回取得した参考値は92.09〜99.73ms、4ページcatalogは
273.45msだった。端末・filesystem cacheに依存する値であり性能保証ではないが、通常経路で
WSL/geometry/glyph再検証を起動していないことは専用回帰テストでも固定した。

### v2最終検証

- 候補利用・受付関連の最終対象テスト: `78 passed in 397.25s`。
- OCR contract対象テスト: `210 passed in 54.29s`。
- frontend full test: `36 files / 447 passed`。
- frontend build: 成功。既存の500 kB超chunk warningのみ。
- backend full test: `2553 passed, 1 skipped in 2362.86s`。警告3件は既存test内の
  openpyxl `copy` deprecated warningであり、今回の回帰ではない。
- Ruffとmypyは成功。pytestの`--basetemp`はすべて固有のDドライブ絶対パスを使用した。
- 実OpenAI呼び出し、実入力アップロード、実OCR、新規の実ブラウザ表示確認は実施していない。
