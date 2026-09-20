# WSL実行時隔離証拠の検証

## 再現した不足

`prepared_reference_evidence._replay_identity` はruntime/font/worker/bootstrapを照合していたが、
報告されたUID、通信拒否、資源上限などを比較対象・成立条件へ含めていなかった。
`wsl_environment_evidence.new_lease` もその読取経路を使っており、identityが一致すれば
不正な実行条件でも現在環境の照会へ進んでいた。

新規の故障注入7件で、UID 0、外部interface、通信許可、host領域公開、privilege制約欠落、
font mountの書込許可、資源上限欠落をそれぞれ再現した。変更前は全7件が拒否されず失敗。
実workerの起動時検査を突破してrootで帳票を変換した実測ではない。
この時点でも本番gateは閉鎖されており、不正な環境で実OCRを行ったという報告ではない。

## 変更

- `backend/app/services/wsl_execution_evidence.py` に観測条件の厳密な検証を追加した。
  UIDは真の整数65534、interfaceは `["lo"]`、通信拒否等は真のbooleanを要求する。
  resource上限はmemory 1GiB、swap 0、pids 64、CPU `100000 100000` の一致を要求する。
  保存statusやpolicy名だけでは認めない。Windows fonts使用時はreadonly mountを要求する。
- 固定runtimeのWindows bridge応答、保存prepared graph、再描画identity、
  環境leaseの保存/読取で検証する。入力SHA・artifact所有権・worker/runtimeの照合は別に維持する。
- 新bootstrap報告と部分的な隔離報告では欠落を拒否する。観測のない旧reportは補完せず、
  historical replayの比較と本番資格を区別する。
- 寸法測定/参照画像の片側だけから全観測を削除する故障も追加で再現した。
  変更途中の検査では拒否できなかったため、両側を調べて完全性を要求するよう修正した。
- 新moduleのSHAを資格fingerprintへ含め、以前の検証実装による報告を現在資格として再利用しない。

変更するのは保存された証拠の受理条件。workerの変換処理・隔離設定そのもの、
原本、矩形座標、meaning、mapping ID、targetPath、sourceRegionId、crop、provider/model/prompt/schema、
補正/出力対応は変更していない。既存OCR contractを維持した。

## 検証と限界

- 新規故障注入baseline: **7 failed**（拒否不足を再現）。
- 単体・bridge・再描画・lease: **189 passed（1.25秒）**。
- bridgeの欠落拒否7件と保存graphの故障注入3件を追加し、実WSLの変換・再描画・
  owner切断・資格APIを含む実行: **221 passed（138.02秒）**。
- 片側の全観測欠落は追加baselineで **1 failed**。上記の両側検査へ修正した。
- 修正後の保存/再取得/再描画/glyph/geometry合同テスト:
  **235 passed / 1 deselected（97.10秒）**。除外は前項で実行済みの実WSL資格1件で、
  235件を全backend回帰や実変換全件の成功とは扱わない。
- 変更後のOCR契約専用script: **210 passed / 1 warning（69.31秒）**。
  変更前の210件と同じ対象。実OpenAIによる認識品質の測定は含まない。
- 対象Ruff成功。対象4moduleのMypyは `--follow-imports=silent` で成功。
  プロジェクト全体の型検査成功とは扱わない。
- 本変更後のbackend full: **2348 passed / 1 skipped / 3 warnings（2315.61秒）**、
  終了コード0。環境identity leaseと本変更を含む。既存font.copy警告3件。
  `RUN_OPENAI_INTEGRATION_TESTS=false`、basetempは `.pt-environment-execution-full-0914`。
  app/testsのPython 460ファイルを開始・終了時にSHA照合し、追加・削除・変更0件。
  実行中のソース変更・別native rendererの並行起動なし。
- frontendは本変更で編集しておらず、新しい実画面検証もしていない。
- OpenAI **0/100回**、Google **0回**。実DB、原本XLSX、提供スキャンへの書込なし。

環境認定証の発行器、geometry/可視性の認定、送信前INK_CLEARANCEは依然として未完了。
今回の証拠検証通過だけで `environmentQualified`、ACTIVE、OCR受付を許可しない。
実帳票のOCR→レビュー→補正→確定→出力を通した実測は未実施であり、
OCR安全性を完全には検証できていない。

再現コマンド（backend cwd、Pythonは `.venv/Scripts/python.exe`）:

```text
python -m pytest -q --tb=short -o addopts='' -p no:cacheprovider tests/unit/test_wsl_environment_evidence.py tests/unit/test_wsl_execution_evidence.py tests/unit/test_prepared_reference_replay.py tests/unit/test_wsl_worker_identity.py tests/unit/test_wsl_runtime_selection.py tests/unit/test_wsl_dimension_observation.py tests/unit/test_wsl_native_bootstrap.py tests/integration/test_prepared_reference_qualification.py tests/integration/test_wsl_frozen_runtime.py
python -m pytest -q --tb=short -o addopts='' -p no:cacheprovider tests/unit/test_prepared_reference_glyphs.py tests/unit/test_prepared_reference_geometry.py tests/unit/test_prepared_reference_replay.py tests/unit/test_wsl_execution_evidence.py tests/unit/test_wsl_environment_evidence.py tests/integration/test_prepared_reference_replay_api.py tests/integration/test_prepared_reference_glyph_api.py tests/integration/test_prepared_reference_geometry_api.py tests/integration/test_prepared_reference_qualification.py -k "not real_frozen"
python -m pytest -q --tb=short -o addopts='' -p no:cacheprovider --basetemp=.pt-environment-execution-full-0914
```
