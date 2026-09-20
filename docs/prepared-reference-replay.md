# WSL参照画像の再描画比較 — 修正前テスト

現在のWSL参照検証は保存済み成果物の対応を検査するが、再描画は行わないため
`deterministic=null` / `RENDER_DETERMINISM_UNVERIFIED` を返す。
正式資格に必要な条件の一つとして、保存済みの印刷用XLSXを同じ固定環境で再描画し、
全ページの画像を比較する処理を実装する。元XLSX/PDF/画像/版設定の置換はしない。

## 先行テスト

`tests/unit/test_prepared_reference_replay.py` に15ケースを追加した。

- 同じ入力・runtime/font集合/worker・解決font・DPI・rasterizerの条件で全ページSHAを比較する。
- 入力や環境の変化は同条件の再描画測定ではないため、単純な画像一致で受け入れない。
- 両方に環境情報が無いことを、一致した認定情報として扱わない。
- ページの欠落・順序違い・画像内容の変化を検出する。
- replay reportのSHAと実際の返却画像bytesの不一致を拒否する。
- PDF producer timestamp等の差を含むPDF全bytesの一致を、PNG一致の前提とはしない。
  PDFの字形/文字/構造検証を済ませたという意味ではない。

実行: `pytest tests/unit/test_prepared_reference_replay.py -q --tb=short -o addopts=''
-p no:cacheprovider --basetemp=.pt-reference-replay-before-0913`。
結果は **15 failed（1.07秒）**。比較関数未実装によるAttributeErrorであり、修正前の基準である。
対象Ruff成功。比較処理/実API接続/実WSLによる変更後の検証は未実施。

このtest fileは `.pt-wsl-reference-full-0913` の収集後に追加した。
稼働中fullには含まれず、その製品コードは変更していない。
OpenAI/Google呼出し0。本番有効化、環境/字形/cropの正式資格はなお未完了。

API側にも、保存XLSX/固定runtime/設定されたdistributionの使用、別画像を返した場合の失敗、
画像が一致しても環境が異なる場合の拒否を追加した。
最初の実行は2 failed / 1 setup error（3.19秒）。2件は再描画未実装を検出し、
1件は稼働中fullと変換mutexが競合してRENDERER_BUSYになった。
全体回帰の終了まで変換を伴う追加試験を止め、終了後に直列で再実行する。
排他制御やassertionは弱めない。対象Ruff成功。

## 実装

先行fullの1146 passed / 1 skipped / 3 warnings（1698.47秒）を確認後、
APIの修正前3ケースを直列で再実行し、3 failed（3.40秒）、setup errorなしを確認した。
その後に比較処理とAPI接続を実装した。

- `prepared_reference_evidence.py` は同条件の比較に必要な入力SHA、runtime/font集合/worker、
  解決font、DPI/rasterizerを検査し、全ページの順序・寸法・返却bytes/SHAを照合する。
- `prepared_reference_replay.py` は保存済み印刷XLSX bytesと保存reportのruntime IDで再描画する。
  distributionだけを既存app設定から受け取る。現在のapp runtime IDへの差替えは行わない。
  固定runtime情報のない旧準備は未測定のままとし、live rendererへfallbackしない。
- 明示的なqualification POSTのみでOfficeを実行する。既存mutexを使用し、busy時は409で
  以前のquality/artifactを更新しない。通常のautosave/step移動/状態GETでは再描画しない。
- 比較用PNGを同じsource jobの別JobArtifactとして保存する。元PDF/PNG/printLayoutを置換しない。
- status GETは比較用画像の所有job、ID、名前、path、SHA、寸法、診断上のページ順序を再検査する。
  元reportと比較report/画像から一致を再計算し、保存`deterministic` booleanだけを信用しない。
- 設定/原本/成果物が処理中に変わった場合は、既存の保存前再照合で拒否する。

`deterministic=true` の場合は `RENDER_DETERMINISM_UNVERIFIED` だけを除く。
実測で画像が異なる場合は `deterministic=false` / `RENDER_NOT_DETERMINISTIC` とする。
入力/環境が異なる場合は同条件の比較ではないため、`RENDERER_ENVIRONMENT_CHANGED` として拒否する。
環境/字形/cropの未認定条件、ACTIVE/paged OCR受付gateは解除していない。

## 検証結果

- 最初の変更後unit/APIセット: 18 passed（3.48秒）。
- 画像改変・artifact対応の変更・証拠欠落・config変更・busyを追加した新旧WSLセット:
  **43 passed（96.12秒）**。
  `tests/unit/test_prepared_reference_replay.py`、
  `tests/integration/test_prepared_reference_replay_api.py`、
  `tests/integration/test_prepared_reference_qualification.py`。
- 実WSLは人工2ページで、印刷確定後の再描画・全画像一致・証拠保存・GETでの再検証を確認した。
  元plan不変、DRAFT/未認定状態を維持する。実ユーザー版の認定とは区別する。
- 旧Windows qualificationのAPI/unit回帰: **17 passed（87.79秒）**。
  `tests/integration/test_reference_qualification_api.py` と
  `tests/unit/test_reference_qualification.py` を直列で実行した。
- 変更前OCR contract: 210 passed / 1 warning（73.42秒）。
  変更後: **210 passed / 1 warning（45.33秒）**。
- 対象Ruff成功、限定mypyは4ファイル成功。frontendソースの変更なし。

比較対象の画像/metadataと診断証拠だけを扱い、provider/model/prompt、OCR値・判定、
mapping/association/canonical座標を変更していない。同じ契約検証を根拠に既存OCR contractを維持した。
変更後のbackend fullは`.pt-replay-glyph-deps-full-0913`で実行中であり、
先行1146件の結果や実行開始を変更後の成功として流用しない。
同一processの完走結果は **1178 passed / 1 skipped / 3 warnings、1752.48秒**、終了コード0。
この収集後に追加した新glyph検証module・helperの全体回帰結果ではない。
正式な環境/字形/crop資格と実ユーザー版の有効化、実UI、実OCRは未完了。
OpenAI/Google呼出し0、原本/実DB/実テンプレート変更0。
