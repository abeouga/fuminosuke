# 仮想見出しを保持した改ページ保存（2026-09-21）

## 目的・契約変更

利用者の明示承認に基づき、仮想見出しの削除を要求していた改ページ保存へ、
明示ID/旧range/新rangeによる所属変更を追加した。自動移動ではない。
`pageHeadingMoves` 未指定時の旧拒否、ページ設定解除時の拒否は維持する。

各指定のID一意性、既存のmanual virtual parent_label、変更前range、
移動先ページの存在、既存位置がページ内に収まることを検証する。
プレビューは読取のみ。保存はDRAFT/revisionを描画前とcommit前に検証し、
所属変更とplan/編集用PREPARE snapshotを同じDB transactionで保存する。
保存失敗後の新規描画artifactの孤立ファイル回収は既存実装と同様、別課題。

## 変更ファイル

- backend: `app/domain/print_layout.py`、`app/services/print_layout_service.py`
- backend test: `tests/integration/test_page_virtual_headings.py`
- frontend: `src/printLayout.ts`、`src/components/PrintLayoutEditor.tsx`、
  `src/pages/TemplatePage.tsx`、`src/components/PrintLayoutEditor.test.tsx`
- docs: `ocr-safety-contract.md`、`page-authoring.md`、本記録

## 保護した内容

対象layoutの変更は指定された仮想見出しのpageRangeのみ。
名称・ID・親子接続・relation・物理セル・canonical座標・意味づけ・mappingを再生成しない。
config YAMLを保存せず、provider/model/prompt/schema、OCR crop計算、過去job、
補正/確定/出力経路を変更しない。改ページに伴う新ページgeometryは既存plannerを使用する。
旧印刷成果物や資格を新planの証拠として補完せず、再準備・再資格を要求する。
意図的な編集保存契約の拡張であり、既存OCR identity contractと本番受付gateを維持した。

## 実データでの適用

既存ChromeタブからV2 `e3286670-4190-436a-a1b6-9f0d7e278b7c` に以下を保存した。

- 改ページ: 28/60/89。保存range: A1:AK28 / A29:AK60 / A61:AK89 / A90:AK102。
- 仮想見出し「作成日」「名前」の既存IDを指定し、A1:AK29からA1:AK28へ変更。
- 保存前後で、仮想見出しのpageRangeを除いたlayout全体のSHAが完全一致。
- config SHAは `23e93328bed95400dd9567b44a827ad5e1dfd955d82c3347efe0062e67c159a1` のまま。
- V1 `b92f4035-06be-4d09-8cc4-c0a55aba51ba` はACTIVEのままで、layout/quality/configのSHAが不変。
- 新規タブなし。既存タブの更新・下書き再表示・保存済み所属を画面取得して確認。
- backendの処理中ジョブがないことを確認して再起動した。DB直書き・検証迂回はしていない。

## 検証

- OCR contract対象: 変更前210 passed (51.36s)、変更後210 passed (53.89s)。
  `verify_ocr_contract.py` のtargetsをASTで読み、同一対象をDドライブの固有basetempで実行。
  script本体はCドライブ固定のため実行・変更していない。
- backend対象: 24 passed (35.98s)。明示移動、従来拒否、未知/重複/物理ID、
  旧range不一致、行先不在、範囲外、公開版、revision競合、commit失敗rollbackを含む。
- frontend対象14 passed、frontend全35 files / 426 passed (15.14s)、build成功（既存chunk警告あり）。
  保存成功時の正本layout通知、保存後の所属表示とdirty解除も検証した。
- backend対象sourceのmypy・Ruff成功。`git diff --check` 成功。
- backend全体: 2519 passed / 1 skipped / 3 warnings (2207.77s)。
  `python -m pytest tests -q -o addopts= -p no:cacheprovider
  --basetemp=D:/llm-ocr-archive/pytest-work/heading-repage-full-02
  --junitxml=D:/llm-ocr-archive/pytest-work/heading-repage-full-02.xml`。
  外部API integrationは無効、既存openpyxl非推奨警告3件。
  初回全体実行は実画面の資格処理と描画mutexが競合し、`RENDERER_BUSY`による
  fixture失敗が発生したため中断。資格処理終了後の上記単独再実行は失敗なし。

実OCR送信・有効化は今回行っていない。
画像認識正解率・provider association mismatch等は今回未測定。

## 正式印刷準備と指定スキャンの診断

既存Chromeで「印刷用ファイルを確定」を実行し、4ページ・69%の生成成功を確認。
続いて正式資格確認はPASS、qualified=true、qualificationIssued=true、
unresolvedBlockers=[]。画面取得でも確認した。期限表示は2026-09-22 14:03:10 JST。
資格reportは `backend/data/artifacts/ebcb4798-6ae1-4cc6-8ecf-d660fba59ad5/qualification/45a8e53d021b492f9203853dc0c50c3f/report.json`。
reference geometry資格は150fields / 600edges、全4ページPASS。
最大representation誤差0.5015912073500317pxは資格の解析上限内。
旧raster anchor診断のREFERENCE_ANCHORS_INSUFFICIENT表示は残るが、正式geometry資格とは
別の診断結果であり、警告を削除したり検査を弱めたりしていない。

指定された `Desktop/実印刷記入済み/1.png`〜`4.png` を明示ページbindingで
V2参照画像に対してローカル診断した（`scripts.inspect_print_scans`）。
全4枚ALIGNED_DIAGNOSTIC_ONLY、領域数59/51/37/3（合計150）。
originalUnchanged=true、externalApiCalls=0、productionQualified=false。
全ページinkClearanceVerified=false。これは正式印刷資格PASSとは別の、実入力の未認定条件。
report: `D:/llm-ocr-archive/llmocr-scan-baseline-hgaauuwj/report.json`。
新第4ページにD-1/D-2/D-3があることも生成画像で確認した。
V2はDRAFTのまま。ACTIVE切替、OCR job受付、OpenAI送信は行っていない。
