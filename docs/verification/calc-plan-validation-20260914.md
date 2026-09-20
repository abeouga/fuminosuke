# Calc保存プランと元セル対応の再検証

## 原因・変更境界

`ReferenceQualificationService` の参照検証は所有artifact/SHAを検査する一方、
保存 `page.fields` / `page.cells` / transformを原本とconfigから再計算していなかった。
新Calcモデルに限定し、同じ所有原本・canonical layout・config mapping・保存寸法を
既存 `_source` / `page_plan` に渡す。測定前、保存前、GETでプラン一致を要求する。
座標・元セル範囲・意味づけを推測修正せず、不整合な版は `PRINT_GEOMETRY_CHANGED` で拒否する。

新規integrationの変更前は **7 failed / 1 passed（8.80秒）**。
矩形、元セル範囲、binding、field欠落、transform、cell、page順の不整合が、
座標対応違反として拒否されず測定へ進んだ。既存のanchor不足・本番gateは拒否したままであり、
本番OCRへ流出していた証拠ではない。今後の資格発行前に必要な不変条件の不足である。

変更ファイルは `backend/app/services/reference_qualification_service.py`、
`backend/tests/integration/test_calc_qualification_plan.py`、Safety Contractと本記録。
新Calcのfingerprintへ元セル復元・source reader・page authoring検証の実装SHAを追加した。
旧モデルの再計算・移行、provider/model/prompt/schema、OCR値・補正・出力、原本変更は行わない。

初回修正後は不整合7件を拒否できたが正常例1件が失敗した。
原因は旧source readerが `shrinkToFit=false` を省略し、draft readerは明示する差だった。
既存rendererは両方を同じ表示として扱うため、比較時に省略をfalseに補う。
保存値へ追加せず、trueはfalseと区別し拒否する。物理identity/座標の比較項目は削除しない。
次の実行は **8 passed（12.81秒）**。GETと測定中変化の拒否ケースを追加した。
追加時のtest配置誤り（別test内の変数参照）は元のassertionを正しいtestへ戻して修正した。

## 実帳票の読み取り専用確認

新Calcのprivate copy `%TEMP%/llmocr-prepare-copy-l7um7_0d` をSQLite `mode=ro` /
`query_only=ON`で読み、同じ `_prepared` を実行した。原本対応からの再計算・artifact graph検査が
成功し、status/layout/qualityの前後一致を確認した。4ページ、未割当を含む全285fieldsが対象。
参照geometryの機械測定対象151はこのうちbindingsを持つOCR領域であり、総field数とは区別する。
Office/PDF helper/OpenAI/Google起動0、DB書込み0。

保存された旧qualification fingerprintは新実装と不一致になり、以前のレポートを現在の資格と
扱わない。数値観測の履歴は保持するが、新fingerprintでの正式な資格再実行とは区別する。
元の実prepare/scan比較は [Calc印刷グリッド](calc-print-grid-20260913.md) を参照する。

変更前OCR contractは **210 passed / 1 warning（42.40秒）**。
対象Ruffとqualification service限定mypyは成功（未使用mypy設定の通知あり）。
最終の新規境界テストは **11 passed（11.36秒）**。
正常例の保存/GET/無書換え、8種類の不整合の測定前拒否、GET時のsource再検証、
測定中のsource変化による資格保存拒否を含む。最終Ruffも成功。
途中の関連33件実行は **1 failed / 32 passed（94.45秒）**。失敗は上述のtest変数配置誤りで、
元のassertionを戻した後の11件は成功した。32件のうち旧WSLの実native試験も通過したが、
この実行全体を成功とは扱わず、全体回帰でも再実行する。
変更後OCR contractは **210 passed / 1 warning（65.35秒）**。
前後ともrootで `backend/.venv/Scripts/python.exe scripts/verify_ocr_contract.py` を実行した。
全体回帰をbackendで `RUN_OPENAI_INTEGRATION_TESTS=false`、
`pytest -q --tb=short -o addopts='' -p no:cacheprovider --basetemp=.pt-calc-plan-full-0914`
として実行し、同じsessionの終了コード0を取得した。
**2044 passed / 1 skipped / 3 warnings（2055.14秒）**。
skipは既存のOpenAI opt-in試験、warningは既存のopenpyxl `Font.copy` 非推奨通知3件。
今回追加した11件と、先行全体試験の収集後に追加したprivate-copy CLIの11件を含む。
先行2022件の成功で今回の全体成功を代替したものではない。
frontendは今回変更していない。先行frontend373件/build成功は新しい実UI確認の証拠ではない。
本番有効化、環境資格、geometry/可視性判定、送信前ink clearance、実OCR E2Eは未完了。
OpenAIは作業全体0/100回、Google 0回。認識誤り/association/schema errorは未測定。

## 変更後の実参照検証

全体試験の終了後、同じprivate copyで製品 `ReferenceQualificationService.qualify` を実行し、
終了コード0を取得した。保存直後のGETと結果が一致し、DRAFT、layout、printLayout、revision、
元XLSX/config/印刷XLSX/PDF/report/全ページPNGのSHAは前後不変だった。
新しい資格診断artifactだけを検証用コピーに保存した。実DB・ユーザー原本は変更していない。

- fingerprint: `88baf214cf942c90561614f23a426cbb118962b24cf0154ea589ca0f9adc9983`
- report: `artifacts/7b90d415-98d5-44e4-971a-e477ded0fdab/qualification/c23d6496932740b9b82f78899daf1cc1/report.json`
- report SHA: `05aa419114fcda0923dd1ad0148ec86c75d554ec074d315cd98364833d32949c`
- 全4ページのfield geometryは `MEASURED`、codesなし、required ink observed。
- 字形: 3515文字命令・640使用font code、宣言faceとの一致。
- `qualified=false`。`REFERENCE_ANCHORS_INSUFFICIENT` と
  `WSL_ENVIRONMENT_QUALIFICATION_UNVERIFIED` / `GEOMETRY_TOLERANCE_UNQUALIFIED` が残る。

更新後のfingerprintでの実行証拠を得たが、本番資格の発行成功ではない。
文字可視性・実筆跡clearanceの認定や実OCRをこの検証結果で代替しない。
