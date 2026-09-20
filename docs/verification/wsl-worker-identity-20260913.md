# WSL worker identity の独立診断

固定runtimeとfont集合の情報だけではworker変更を識別できないことを、実WSLで確認した。
本番資格の成立や攻撃成功を意味する試験ではなく、資格失効に必要な入力の欠落の再現である。

## 方法

全体回帰 `.pt-pinned-print-preparation-full-0913` の実行中に、製品コードを変更せず実施した。
既存 `advanced(prefixed=True)` の人工2ページfixtureと既存 `workbook_copy` を使用した。
ユーザー原本、DB、既存版、保存artifactsにはアクセスしていない。

repo外の一時領域にworkerを複製し、通常版と診断用report項目だけを追加した版を順に起動した。
Windows bridgeのworker pathだけを独立Python process内で差し替え、両方を同じ固定runtimeへ渡した。
隔離設定、資格gate、runtime/font検査は差し替えていない。
診断項目は実行されたコードが異なることを示すためのものであり、PDF処理は変更していない。

- runtime SHA: `1465eef285317a981d1efa98faf9d6dce267e1fe446e29a7f48c65538a45d65d`
- 元worker SHA: `83fa5c087436994829f22d1a0c562f49ed8d86b7bf478ddd5ca6b01fe2e10da1`
- 診断worker SHA: `f224878c7e4a3eda5715507149e93357194a2545ca03b17db21ba83935f31eee`
- font集合 SHA: `47071a85470e130419c56f116d4294978ba536e78f30ac164f860f5a32786571`
  （578 files / 665,977,778 bytes）

## 結果

両方の実行が既存bridgeに受理され、`runtimeImage`全体、`fontDirectoryIdentity`全体、
`resolvedFonts`全体、2ページのPNG bytesは一致した。診断版だけに追加report項目が存在した。
実行前後のrepo worker bytesは完全一致した。

現在の印刷確定はFODSとPDF生成のruntime/fontを比較するが、workerコードを比較しない。
この状態の情報だけを正式資格へ使用すると、worker更新を理由とする失効を判定できない。
単なる画像一致をworker identityの代替にはできない。

修正対象は新しい固定runtime経路のworker内容identity、そのbridge照合、印刷確定の前後一致である。
旧保存report/過去jobへの後付け、資格booleanの変更、失効条件の緩和は行わない。
host bootstrap全体のclosureは別の残条件であり、worker SHAの追加だけで完成とはしない。

実行script（repo外）:
`C:/Users/hanam/.codex/tmp/paged-vector-inspection-20260913/probe_worker_identity.py`

`backend/.venv/Scripts/python.exe <script>` は終了コード0、`DIAGNOSTIC_CONFIRMED`。
実OpenAI/Google呼出し0。製品修正および修正後検証は、この診断時点では未実施。

## 修正前の境界テスト

`tests/unit/test_wsl_worker_identity.py` に9ケースを追加し、製品変更前の失敗を確認した。
同じmetadataの内容変更、空/過大入力、隔離側の専用コピー、起動時snapshotの保持、
worker証拠の欠落・policy/SHA/type不一致を対象とする。

`pytest tests/unit/test_wsl_worker_identity.py -q -o addopts='' -p no:cacheprovider
--basetemp=.pt-worker-identity-before-0913`: **9 failed（0.50秒）**。
失敗理由はidentity関数未実装、元コードpathの直接bind、snapshot欠落、不一致未拒否である。
対象Ruff成功。既存製品コードの全体回帰とは別のTDD baselineであり、通過報告ではない。
この新規fileは稼働中fullの収集後に追加したため、そのfull結果には含まれない。

## 実装と検証

先行fullが1114 passed / 1 skipped / 3 warnings（1671.95秒）で終了した後、
APIのworker変更/片側欠落/両側欠落4ケースも追加した。
新unitとAPI境界の修正前セットは13 failed / 9 deselected（3.88秒）。
APIは本来拒否すべきworker差を200で保存しており、修正対象を再現した。

固定runtimeを明示した場合に限り、次の処理を実装した。

1. Windows bridgeはworkerソースを最大1MiBで読み、実行専用領域にsnapshotを作る。
   起動するのはそのsnapshotであり、読み取った内容のSHAを保持する。
2. Linux brokerはsnapshotを専用private directoryへコピーし、内容一致を確認して0444にする。
   子processへ元repoファイルではなくこのコピーをread-only bindする。
3. 隔離側はworker内容を処理前後にSHA照合し、`workerIdentity`へpolicyとSHAを記録する。
   bridgeは自分が起動前に保持したSHAとの一致を要求し、欠落/不一致を拒否する。
4. 印刷確定はFODS/PDFのworker証拠の存在と完全一致も保存前に要求する。
   不一致では既存の`PRINT_GEOMETRY_CHANGED`と下書き・config・artifact無更新を維持する。

policyは`wsl-worker-source-v1`。size/mtimeだけのSHA再利用はしない。
runtime未指定経路、旧保存report、過去job、ダウンロード済みbytesには後付けしない。
provider/model/prompt、OCR値、mapping/association、canonical geometryの変更はない。

- 新unit・既存runtime bridge・印刷確定API（実WSLを含む）: **47 passed（55.43秒）**。
- 実WSL固定runtime: **4 passed（45.66秒）**。
  既存live経路と固定経路のPNG一致、実worker SHA、元repo不変、欠落imageの拒否、
  owner切断時の終了、および別workerを実際に起動した場合のbridge拒否を確認した。
  差し替えは人工fixture/専用コピーだけを使用し、製品ファイルは書き換えない。
- 対象Ruff成功。bridge/serviceの限定mypyは2ファイル、Linux 3.10指定workerは1ファイル成功。

- 変更後OCR contract: `backend/.venv/Scripts/python.exe scripts/verify_ocr_contract.py`、
  **210 passed / 1 warning（68.98秒）**。変更前は同じ210件で71.51秒だった。
  対応identity/旧OCR値・crop生成を変えない範囲と同一契約検証を根拠に、既存OCR contractを維持した。

後続のWSL参照検証を含むbackend full `.pt-wsl-reference-full-0913` は
1146 passed / 1 skipped / 3 warnings（1698.47秒）で完走した。
先行1114件ではなく、この結果をworker変更後の全体回帰として記録する。
対象/実WSL/契約テストの成功は、全体回帰や実ユーザー版の有効化の代替ではない。
本番資格・ACTIVE・paged OCR受付gateは変更していない。
host bootstrap/worker以外の実装identity、全字形形式と実文字配置、実手書きcrop、
正式資格の発行/失効、実アプリ反映・本番有効化はなお未完了。
