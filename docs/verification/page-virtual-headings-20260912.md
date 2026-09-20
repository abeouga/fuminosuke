# 複数ページXLSXの仮想見出し — 2026-09-12

## 原因・変更範囲

開始HEADは `c0be81ac1`、worktreeはclean。
AGENTS.md、OCR Safety Contractとページ編集・renderer関連契約を確認した。
DB migrationは追加していない。

従来はLayoutEditorがpageView中の仮想見出しボタンを隠し、保存APIも元Excelセルにない
追加IDを拒否していた。表示側もpage.fieldsの物理セルIDだけを扱い、仮想見出しの所属ページを
保存・再表示できなかった。ボタンだけの変更では解決しない。

名前付きmanual仮想見出しに限って `pageRange` とページ内表示矩形を保存する。
従来セルのcanonical座標・IDを変換せず、未指定pageRangeをJSONへ追加しない。
仮想見出しを元セル集合・page.fields・field mappings・ラベルOCRへ追加しない。
表示ページの矩形で囲い込み、明示IDで既存の見出し階層・繰り返し群へ接続する。
ハンドを選択した場合だけページ内の仮想見出しを移動でき、通常セルは移動不可のまま。
モード切替のチュートリアルは追加していない。

保存時は所属range、ページ内の寸法、元セルIDの流用、既存物理セルの仮想化、結合混入を検証する。
見出しを孤立させる改ページ・用紙変更・設定解除も保存前に拒否する。
通常layout APIにも検証を接続し、printLayoutを持てないパッケージv1への混入を拒否する。
旧版のパッケージ・ページ所属なしの仮想見出しは維持する。

## 変更ファイル

- frontend: `src/components/LayoutEditor.tsx`、`src/pageAuthoring.ts`、`src/types.ts`、
  `src/components/LayoutEditor.pages.test.tsx`
- backend: `app/layout_analysis/models.py`、`app/services/page_authoring_validation.py`、
  `app/services/print_layout_source.py`、`app/services/print_layout_service.py`、
  `app/services/layout_service.py`、`app/template_packages/canonical_models.py`、
  `app/template_packages/schemas/template-v1.schema.json`
- backend tests: `tests/integration/test_page_virtual_headings.py`、
  `tests/unit/test_template_package_contracts.py`
- docs: `ocr-safety-contract.md`、`page-authoring.md`、`template-authoring-extensions.md`、本書

## 検証

コマンドはroot以外、各backend/frontendディレクトリから実行。

| 対象 | コマンド・結果 |
| --- | --- |
| 変更前OCR契約 | root: `backend/.venv/Scripts/python.exe scripts/verify_ocr_contract.py` — 210 passed |
| 変更後OCR契約 | 同コマンド — 210 passed |
| 新機能の再現 | 追加したAPIテストが変更前にpageRangeの422で失敗することを確認 |
| ページ保存・既存拡張・package契約 | `pytest tests/integration/test_page_virtual_headings.py tests/unit/test_layout_authoring_extensions.py tests/unit/test_template_package_contracts.py -q -o addopts='' -p no:cacheprovider --basetemp=.pt-virtual-focus2-0912` — 46 passed |
| backend全体 | `.venv/Scripts/python.exe -m pytest -q -o addopts='' -p no:cacheprovider --basetemp=.pt-virtual-full-20260912` — 856 passed、既存1 skipped、3 warnings、591.31秒 |
| 追加境界 | 最終追加後のページ仮想見出し13テスト＋package契約20テスト — 33 passed |
| 最終package回帰 | `test_template_package_import.py` / `export.py` / `drive_sync.py` / `contracts.py`（unit）、`test_template_package_api.py`（integration）、`test_page_virtual_headings.py`（integration）をまとめてpytest — 82 passed |
| frontend全体 | `npm.cmd test` — 327 passed / 29 files |
| frontend build | `npm.cmd run build` — 成功。既存500kB超chunk警告あり |
| Ruff | 上記変更Python 8ファイルへの `.venv/Scripts/python.exe -m ruff check` — 通過 |
| package schema | `.venv/Scripts/python.exe -m app.template_packages.schema_export --check` — 通過 |
| mypy | `.venv/Scripts/python.exe -m mypy --follow-imports=silent` 変更app 6ファイル — 既存4件で未通過 |
| 差分 | `git diff --check` — 通過 |

全体テスト開始後にページ境界3テストとpackage混入拒否・1テストを追加したため、
最終の追加境界テストは全体856件とは別に検証している。
mypyの既存指摘はopenpyxl型stub不足3件とLayoutServiceの
`custom_field_key(parent_target_path).strip()` のNone判定1件。HEADの同じ処理と照合した。
既存テストの削除、期待値弱体化、skip追加はない。

保存response・再取得を比較し、通常セルのID/canonical座標、mappingId/targetPath/矩形、
ページのrange/transform/referenceImage/fieldsとsource SHAを照合した。
見出し自身のOCR field非生成、A3ページ内でcanonical画像より外側への配置、階層・群の保存、
版コピーのID分離・親接続保持、保存失敗後の再試行、移動・削除を検証した。

## 未検証範囲・運用

既存OCR contractを維持した。provider/model/prompt/association、物理セルのcrop・mapping、
補正・確定・出力、過去job snapshotは変更していない。
ページ手動編集の追加許可は明示要求に基づく拡張で、本番qualification gateは維持する。
OpenAI API 0回、Google API 0回。実手書き認識率・実APIのassociation mismatch、mapping誤対応、
schema error、recognition-only error件数は未実測。未整備のGolden replay全体の完了とは扱わない。

UIはcomponentテスト、保存は分離DBのAPI integration fixtureで確認した。
今回、実Chromeで利用中の帳票を変更する確認は行っていない。
稼働中のポート8000バックエンドは `--reload` なしで、停止・再起動していない。
利用環境への反映には、編集中データの保存と実行中処理の終了を確認した上でバックエンド再起動が必要。

staging/commit/pushなし。今回生成した `.pt-*` のテスト一時成果物は、リポジトリ内のgit対象外
`backend/.pytest-full-mapping-virtual-artifacts-20260912/` へ移動して保持した。削除していない。
