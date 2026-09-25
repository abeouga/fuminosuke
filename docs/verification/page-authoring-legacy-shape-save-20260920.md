# 旧layoutの形状既定値とページ編集保存の互換修正

実施日: 2026-09-20。対象版: `b92f4035-06be-4d09-8cc4-c0a55aba51ba`。

## 原因と修正境界

Step 5は記入欄150件・内容設定済み150件・未割当0件・設定不完全0件・保存可能だったが、
`PUT /print-layout/authoring` が `PRINT_AUTHORING_GEOMETRY_CHANGED` (409) を返していた。
編集開始revisionと保存済みrevisionは一致しており、今回の原因はrevision競合ではない。

旧DBのlayoutでは `shapeType` / `vertices` / `virtual` が省略されていた。
リクエストは既存Pydanticモデルによって `rectangle` / `[]` / `false` に展開される。
`validate_page_edit` が生JSONの省略値を `None` として比較し、同一矩形を形状変更と誤判定した。
既存結合のparts全体比較にも同じ不整合があることを独立した回帰テストで再現した。

修正は `backend/app/services/page_authoring_validation.py` の比較処理のみ。
省略された上記3属性だけを既定値と同値に扱い、既存結合も比較用コピーで同じ補完をする。
明示的null、多角形・頂点・virtualの変更、座標変更、結合の別metadata変更は許可しない。
DBの一括移行、hashの手動補正、原本XLSXの変更、フロントの保存payload変更は行わない。

## Phase 1: 再現と修正

- 旧形式をfixture DBに保存したAPIテストを追加。修正前は無変更・意味づけ・結合・削除の4ケースが409。
- 既存結合を含む旧形式の無変更保存も409となることを追加で確認。
- 省略値の比較を修正。成功ケースは200、保存後GETと応答が一致する。
- 新規のunitテストは省略属性個別・全体、非破壊比較、明示null・形状変更の拒否、結合metadata改変の拒否を固定。
- integrationテストでは拒否時のDB/layout・config・print state保持、成功時の元セル座標・ページ画像・transform保持を確認。

## Phase 2: 回帰検証

pytestの一時領域はすべて `D:/llm-ocr-archive/pytest-work/` 配下の実行別ディレクトリ。
`scripts/verify_ocr_contract.py` 自体は変更していない。同スクリプトの `targets` をASTで読み、
全対象をそのままpytestへ渡してDドライブの `--basetemp` で実行した。

| 検証 | 結果 |
| --- | --- |
| OCR契約全対象・修正前 | 210 passed |
| OCR契約全対象・修正後 | 210 passed |
| page_authoring / page_authoring_validation / page_virtual_headings | 61件通過 |
| frontend `npm.cmd test -- --reporter=dot` | 35ファイル・413 passed |
| frontend `npm.cmd run build` | 成功、既存の500kB超bundle警告あり |
| 対象Python3ファイル `ruff check` | 成功 |
| 変更service `mypy --follow-imports=silent` | 成功 |
| backend full tests | 2506 passed / 1 failed / 1 skipped、2333.18秒 |
| 上記失敗のWSL vector helperファイル再実行 | 2 passed、67.81秒。コード・条件変更なし |
| 最終 `git diff --check` | 成功 |

実行コマンド（backendディレクトリ）:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/integration/test_page_authoring.py tests/unit/test_page_authoring_validation.py tests/integration/test_page_virtual_headings.py -q --basetemp=D:/llm-ocr-archive/pytest-work/20260920-shape-target-02
.\.venv\Scripts\python.exe -m pytest tests --basetemp=D:/llm-ocr-archive/pytest-work/20260920-shape-full -o addopts='--strict-markers --disable-warnings' -q
.\.venv\Scripts\python.exe -m ruff check app/services/page_authoring_validation.py tests/unit/test_page_authoring_validation.py tests/integration/test_page_authoring.py
.\.venv\Scripts\python.exe -m mypy app/services/page_authoring_validation.py --follow-imports=silent
```

frontendディレクトリでは `npm.cmd test -- --reporter=dot` と `npm.cmd run build` を実行した。

全体実行の失敗は
`tests/integration/test_wsl_vector_helper.py::test_real_vector_report_cannot_claim_different_reference_raster_dimensions`。
期待するraster寸法不一致の検証へ到達する前に、WSL glyph brokerが
`Glyph bootstrap Python changed during validation` を検出して停止した。
このテスト経路は今回変更した `validate_page_edit` を呼ばない。
同ファイルを `--basetemp=D:/llm-ocr-archive/pytest-work/20260920-shape-wsl-recheck` で
単独再実行すると2件とも通過した。bootstrap識別情報の変化原因は今回未確定であり、
全体テストが一度で全件通過したとは扱わない。全体の再々実行は未実施。

最初の契約テストはD側親ディレクトリ未作成によるsetup errorで中断した。
親を作成して別の実行ディレクトリで再実行した結果が上表。テストのskipや検証条件の弱体化はしていない。
追加した意味づけテストの期待値に不足していた既存schemaの `parentLabelCellId: null` を補い、
保存結果の完全一致比較は維持した。

## Phase 3: 既存Chromeでの通常保存

開始時の画面取得済み。既存タブは再読込せず、新規タブも作成していない。
下書きと保存済みlayout・print state・mappingは読取専用で比較用に保持した。

バックエンド反映前に実行中jobがないことを確認し、該当uvicornプロセスだけを再起動した。
実保存前のGET確認時、5173番のフロントエンドサーバーが停止していることを確認した。
通常のVite復旧では既存クライアントの再接続pingが `location.reload()` を呼ぶため、
今回の起動だけ `server.hmr=false` / `server.ws=false` として接続を復旧した。
プロジェクトのVite設定は変更していない。既存画面の `performance.timeOrigin`、URL、
localStorage下書き、DB revisionが復旧前後で一致したことを確認してから保存した。

一時起動用ファイルとログは `D:/llm-ocr-archive/runtime-logs/20260920-shape-save/` に置いた。
`start-frontend-preserve-draft.mjs` で起動したフロントエンドは、この実行中だけ自動更新なし。
現在のタブを再読込しない条件を守るため、通常のHMR起動へ戻す操作は行っていない。

「テンプレート設定をDBへ保存」を1回だけ押し、Chrome通信記録で以下を確認した。

- `PUT /print-layout/authoring?base_revision=...`: 200。
- `GET /field-mappings`: 200、続く `PUT /field-mappings`: 200。
- 後続の `GET /print-layout`、保存後確認のlayout / print-layout / field-mappingsもすべて200。
- 開始revision: `610b08c0235b18eb235de09c684873b1004fc01808d028215d78677f81e517ce`。
- 最終revision: `cee09d83898feeecc641548691739959996f5cdd9513465a527466c1c1739813`。
- 保存結果: 287セル、54結合、150関係、13繰り返し群、2繰り返し区分、150 mappings。
- 下書きとの比較: ID順序・既存値は一致。cells / relationsの差は既存schemaによる省略値の補完だけ。
- 残存物理セル345件のID・canonical座標、ページrange / transform / 寸法 / referenceImage / 原本cellsは不変。
- 原本SHA `aa1d088cf5ebae9cfca0f42d0ac3cbe1d8b23f7067196407f6ae3ce9c3cc3176` は不変。
- `validForOcr=true`、`warnings=[]`、`fieldErrors=[]`。照合設定と書類名のfield pathをGETで確認。
- localStorageの作業用下書きはアプリの通常保存処理で消去。手動での消去・書き換えなし。
- 他23版の全行、対象版の編集用PREPAREを除く79件のjob snapshotは保存前後のhash一致。
- 版はDRAFT、正式資格はUNVERIFIEDのまま。承認・資格再確認・OCRのボタンは押していない。

保存後の実画面も取得し、「テンプレート設定保存済み」「レイアウトは保存済み」と
「レイアウト287セル、意味づけ150件、書類の見分け方を下書きとして保存しました」を確認した。
保存前に表示されていた形状変更エラー・未保存案内は消えた。最後まで同一documentのままである。

## OCR契約と未検証範囲

revision / layoutSha256、DRAFT限定、ページ境界、結合parts、原本内部値保護の各検証は維持する。
provider・prompt・mapping生成・physical identity・crop・OCR受付snapshot・review/補正・出力処理の変更はない。
今回の互換修正は既存OCR contractを維持する。正常保存はACTIVE化や本番paged OCRの資格を意味しない。
実OpenAI/Google APIによるOCR・外部同期、実手書き認識、renderer資格の再認定は今回実施しない。

既存dirty差分は保持し、stage・commit・pushは行わない。

## 今回の変更ファイル

- `backend/app/services/page_authoring_validation.py`
- `backend/tests/integration/test_page_authoring.py`
- `backend/tests/unit/test_page_authoring_validation.py`（新規）
- `docs/page-authoring.md`
- 本検証記録（新規）

開始時から存在するfrontend 5ファイル・Markdown 2ファイルの差分には手を加えていない。
実ブラウザの保存確認は実施済み。ただし画面の再読込・一覧からの再オープンは指示に従い未実施。
