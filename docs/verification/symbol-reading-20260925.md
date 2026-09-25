# 記入欄内の記号判定

## 範囲

- 開始時HEAD: `018326c5ff8a9f9b77bf9202d3cd7821ad24efb9`。既存の多数の未コミット変更を保持。
- ユーザーの「必要ならテストしていいよ／実装を開始して」に基づく実装。
- 単一ページの記入欄に `symbol` 読取モードを追加。初期候補は `○ ◎ × △`。
- 「区分」の選択欄を記入欄の「読み取り方法」へ移動。
- 記号の候補は意味づけ画面の「記号の候補と保存先」で編集する。
- 内部では既存のmark mappingを使い、候補から1記号を読む要求を既存OpenAI batchへ追加。
- 不確実・候補外・複数記号は要確認。空欄を強制的に4択へ分類しない。
- 印刷選択肢のautomatic/manual、過去の受付snapshot、ページ別経路のmark拒否は維持。

## 変更箇所

| 境界 | ファイル |
| --- | --- |
| 設定・API・汎用定義・パッケージ | `backend/app/domain/template_config.py`, `template_definition.py`, `layout_analysis/models.py`, `api/v1/routes/{templates,ocr}.py`, `template_packages/schemas/template-v1.schema.json` |
| OCR要求と応答 | `backend/app/services/ocr_request_preparation_service.py`, `backend/app/ocr/{openai_provider,deterministic_mapper}.py` |
| 作成・確認UI | `frontend/src/components/LayoutEditor.tsx`, `frontend/src/pages/{ReviewPage,TemplatePage}.tsx`, `frontend/src/types.ts`, `frontend/src/styles.css` |
| 追加・拡張テスト | `backend/tests/ocr_contract/test_symbol_choice.py`, `backend/tests/integration/test_mapping_validation_preservation.py`, `frontend/src/components/LayoutEditor{,.authoring,.pages}.test.tsx`, `frontend/src/pages/ReviewPage.test.tsx` |

provider model、応答schema、association、crop生成、物理座標、補正・確定・出力処理は変更なし。
文字と記号の切替はテンプレート編集として扱い、既存の版境界とmapping生成規則に従う。
記号から文字へ切り替える際も現在の保存先を引き継ぎ、既存jobのIDを差し替えない。
OCR requestへの新モード追加は意図的な契約拡張。既存モードの期待値は差し替えていない。

## 検証

pytestの一時成果物は `D:/llm-ocr-archive/pytest-work/` に作成。
`scripts/verify_ocr_contract.py` はCドライブ相対basetempを固定しているため、
その `targets` をASTから取得し、同じ全対象をDドライブのbasetempで実行した。

- 変更前: 専用scriptの全対象が成功（`20260924-symbol-before`）。
- 変更後: 全体検証のJUnitを専用scriptの対象一覧と照合し、全対象222件成功・失敗0・skip0を確認。
- 追加ケースの変更前失敗: `symbol` モードが既存モデルに拒否されることを確認。
- 変更後の境界・保存テスト: 16件成功（`20260925-symbol-persistence`）。
  - 4記号、空欄、不確実、候補外、複数記号。
  - 同一cropの送信、候補の限定、sourceRegionId・boundingBox保持。
  - layout→mapping→汎用定義、API保存・再取得、失敗時の既存mapping保持。
- frontend full: `npm test` 473件成功。
- その後のUI調整・追加ケース: LayoutEditor関連3ファイル150件、ReviewPage29件成功。
  保存先維持、候補編集、単一選択の手動修正、確定後の閲覧を確認。
- 説明文調整後: TemplatePage・LayoutEditor157件とbuild成功。
- frontend build: 成功（500 kB超のbundle警告あり）。
- 変更対象のruff: 成功。
- mypy: 依存先8ファイルに56件のエラー。Windows上のPOSIX属性、openpyxl型定義不足、
  既存serviceの型不一致等。今回変更した8つの検査対象ファイル自体のエラー表示はなし。
  変更前のmypy比較は未実施のため、この実行だけで全件の既存起因を証明したとは扱わない。
- バックエンド全体: 直列実行が長時間化したため約8%時点で中断（失敗なし）。
  pytest-xdistは名前空間があるもののplugin本体がなく、`-n 4` / 明示plugin読込とも実行前に失敗。
  依存を追加せず、全204テストファイルをソートして4組へ均等に振り分け、
  独立したpytestプロセス・basetemp・JUnit・ログで実行。
  `tests/**/test_*.py` と `tests/**/*_test.py` の和集合を使い、対象の削除・skip追加はなし。
  全対象リスト: `D:/llm-ocr-archive/pytest-work/20260925-symbol-shards/files.txt`。
  並列実行で共有Office renderer lockの `RENDERER_BUSY` が発生。
  追加の単体診断でも同じ409を確認した。DB・storage分離だけではrenderer利用テストを
  並列実行できないため、これらのエラーを製品回帰と同一視しない。
  集計: **2436 passed / 38 failed / 121 errors / 1 skipped**。
  並列runnerで `CREATE_NO_WINDOW` を使用した際、PowerShellの日本語stderr文字化けも1件観測。
  renderer関連setupの409・後続KeyErrorが多数あるが、全159件の原因を個別確定したとは扱わない。
- 順次再実行: 候補準備、印刷プラン保存、全ページ出力、バックアップ検証の代表4件がすべて成功。
  `20260925-symbol-sequential-check.xml`、68.93秒。文字化けもこの実行では再現しなかった。
  全件の順次再実行は行っておらず、全体テストの全件成功は未確認。
- 最終 `git diff --check`: 成功。変更対象のpackage JSON Schemaとモデルの一致確認も成功。

主要コマンド（各プロジェクトディレクトリで実行）:

```powershell
# backend
.venv/Scripts/python.exe -m pytest tests/ocr_contract/test_symbol_choice.py tests/integration/test_mapping_validation_preservation.py -x --basetemp=D:/llm-ocr-archive/pytest-work/20260925-symbol-persistence
# 全体: files.txtの行を0始まりで i::4 に分け、i=0..3を同時実行
# .venv/Scripts/python.exe -m pytest <その組の全ファイル> --basetemp=D:/llm-ocr-archive/pytest-work/20260925-symbol-shards/part-N --junitxml=D:/llm-ocr-archive/pytest-work/20260925-symbol-shards/part-N.xml
.venv/Scripts/python.exe -m app.template_packages.schema_export --check
# frontend
npm test
npm test -- src/components/LayoutEditor.authoring.test.tsx src/components/LayoutEditor.test.tsx src/components/LayoutEditor.pages.test.tsx
npm test -- src/pages/ReviewPage.test.tsx
npm run build
```

## 未検証範囲

実OpenAI呼出し0回。実帳票による記号認識精度、実ブラウザE2E、Google出力の実外部APIは未実施。
テスト成功は認識精度の保証ではない。pagedの記号認識も対象外。
機能に関する対象検証は通過したが、全体テスト・mypyの全件成功は未確認であり、
OCR安全性を完全には検証できていない。
