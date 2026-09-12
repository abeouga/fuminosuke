# 固定文字の前後空白によるテンプレート保存拒否の修正

## 原因と修正範囲

元XLSXの固定文字に前後空白があると、取込・編集でtrimされたliteralと、
保存前検証の完全一致比較が食い違い、`SPREADSHEET_OUTPUT_MERGE_CONFLICT`になる。
工程移動側は保存失敗を一律に接続障害として案内していた。

`SpreadsheetTemplateOutputService._ensure_merge_contents_safe`で、既存の完全一致に加え、
両方が文字列かつtrim後に非空で、前後空白以外が一致するliteralのみ許容する。
テンプレート名・ラベル名・セル番地による特例はない。
共有判定なのでページ保存・出力snapshot検証・出力コピーの結合で同じ条件を使う。
フロントエンドだけでは、原本を検証するサーバー側の拒否は修正できない。

元XLSX、保存parts、mapping identity、physical identity、provider association、
crop / boundingBox、prompt / model / schemaは変更しない。
内部空白、全角半角、大小文字の違いは一致と扱わず、数式・input内部値の保護を維持する。
renderer認定・ACTIVE化・paged OCR受付の安全ゲートも変更しない。
これは固定文字の表記比較の整合修正であり、既存OCR contractを維持した。

画面は保存失敗時の編集内容保持・画面移動中止を維持し、
「編集欄に表示されたエラー内容」を確認する案内に変更する。

## 変更ファイル

- `backend/app/services/spreadsheet_template_output_service.py`
- `backend/tests/unit/test_spreadsheet_template_output_contract.py`
- `backend/tests/integration/test_page_authoring.py`
- `frontend/src/pages/TemplatePage.tsx`
- `frontend/src/pages/TemplatePage.test.tsx`
- `docs/ocr-safety-contract.md` と本報告

開始時に存在したLayoutEditorと関連3テストの差分は本修正に含めず、そのまま保持する。
AGENTS.mdの変更、staging、commit、pushは行っていない。

## 検証（2026-09-12）

Pythonは`backend/.venv/Scripts/python.exe`を使用。
pytest / ruff / mypyはbackend、npmはfrontendを作業ディレクトリとする。

- 変更前：`python scripts/verify_ocr_contract.py`（ルートから）— 194 passed。
- 新規単体テストの変更前再現：`pytest tests/unit/test_spreadsheet_template_output_contract.py -k 'boundary_whitespace or relax_input' -q -o addopts='' -p no:cacheprovider` — 4 failed / 12 passed。失敗は任意ラベル・2組の座標の前後空白ケース。
- 変更後：`python scripts/verify_ocr_contract.py`（ルートから）— 210 passed。
- 追加APIテスト：`pytest tests/integration/test_page_authoring.py -k boundary_whitespace -q -o addopts='' -p no:cacheprovider --basetemp=.pt-literal-recheck-0912` — 2 passed。異なるページ・文字列で保存成功、再取得一致、ページgeometry維持、実文字不一致時の保存拒否・revision不変を確認。
- `npm.cmd test` — 29 files / 301 passed。保存失敗時の具体的エラー表示・ローカル編集保持・工程維持を含む。
- `npm.cmd run build` — 成功。既存の500 kB超chunk警告あり。
- `python -m ruff check app/services/spreadsheet_template_output_service.py tests/unit/test_spreadsheet_template_output_contract.py tests/integration/test_page_authoring.py` — 成功。
- `python -m mypy app/services/spreadsheet_template_output_service.py` — 成功。
- backend全体回帰：`python -m pytest -q -o addopts='' -p no:cacheprovider --basetemp=.pt-literal-full-0912` — 842 passed / 1 skipped / 3 warnings、585.33秒。今回skipは追加していない。警告は既存テストのopenpyxl非推奨copy呼出し。
- `git diff --check` — 成功。

Golden fixture / 既存期待値は変更していない。実OpenAI / Google API呼出しは0回。
実認識のmismatch・mapping誤対応・schema error・recognition-only errorは今回未計測。
APIテストはテスト用DB・原本と描画stubを用いたもので、実Chrome保存・本番rendererの証明ではない。
実行中サーバーの再起動、Chrome再読み込み、実テンプレートへの保存は未実施。
実環境への反映・保存確認は、未保存編集を保護した上で別途行う必要がある。

テスト一時ディレクトリ5件の削除は実行環境のポリシーで拒否されたため残している。
対象はbackend直下の`.pt-literal-full-0912`、`.pt-literal-recheck-0912`、
`.pt-literal-target-0912`、`.pt-ocr-0e87e34f`、`.pt-ocr-42a7dcf6`。
実テンプレート・実DB・原本の削除は行っていない。
