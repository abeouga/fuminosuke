# 有効化・本番OCR要求との現状照合

## 最新指示による作業終了

利用者は本作業をWSL environment identity leaseの安全終了に限定した。
[13項目の終了確認](wsl-environment-identity-lease-20260914.md#安全終了確認--complete)はCOMPLETE。
以下の本番化残件は履歴・次Issue候補として保持するだけで、継続実装の指示として扱わない。
正式資格発行、ACTIVE化、provider送信、review/confirm/output/E2E等は今回実施しない。
既に起動済みの追加fullは中断し、成功実績には含めない。製品コードの追加変更なし。

完了監査の途中記録。実装・テストの存在と実テンプレートの有効化成功を区別する。
全体目標は未達。製品ソースを凍結して実行したbackend fullは下記のとおり完走した。
その後に[環境identity lease](wsl-environment-identity-lease-20260914.md)と
[実行時隔離証拠の検査](wsl-execution-evidence-20260914.md)を追加した。
2262件のfull結果はこれらより前のbaseline。後続変更を含む最新fullも完走し、
2348 passed / 1 skipped / 3 warnings、終了コード0を確認した。目標全体の達成とは区別する。
その後に[実効unit設定の解析前検査](wsl-unit-admission-20260914.md)を追加した。
2348件の結果はこの最新変更より前のbaselineであり、最新ソースのfull成功ではない。
続いて環境lease v2にWindows/WSL host更新の前後照合を追加した。
単体159件と対象静的検査は成功。正式な環境資格発行・有効化成功の代用にはならない。

## 直接確認した残件

1. `reference_qualification_service.py` のWSL経路は `environmentQualified=false`、
   `qualified=false` を設定し、環境資格発行器がない。
   `wsl_blockers` は環境認定・geometry toleranceのblockerを解除する条件を持たない。
   校正・glyph・再描画・起動環境の証拠を増やしただけではACTIVEに到達しない。
2. `job_page_service.py::_require_qualified_reference` はstatusを検査した後も常に拒否する。
   有効な資格の発行・失効検査と送信前安全処理を完成させた後、成功経路の実装が必要。
   saved booleanやmockによる資格の偽装を成功経路の代用にしない。
3. `paged_safety_stages.py` は `INK_CLEARANCE` を必須として列挙するが、
   実処理の検査は未接続。`reference_crop_safety.qualify_crop` は未測定欄をUNVERIFIEDとする。
   既存 `CROP_CORRESPONDENCE` の画素照合は実筆画の安全性を証明しない。
   自動判定不能時の利用者による送信前確認工程は未承認であり、暗黙に追加しない。
4. 矩形移動は通常画面では実装済みだが、`LayoutEditor.tsx` は `pageView` があると
   描画追加UIを表示せず、移動も同ページのvirtual見出しに限定する。
   `pageAuthoring.ts::movePageHeading` とbackendのpage authoring検証もこの制限に一致する。
   複数ページXLSXでの後付け多角形の追加・移動は未対応であり、通常画面のテストでは保証できない。
   対応時は元Excelセルを移動せず、ページ表示/OCR領域と出力セルidentityを分けて保持する必要がある。

## 要求別の証拠と限界

| 要求 | 現在の直接証拠 | 判定・不足 |
| --- | --- | --- |
| 保存中の編集・ステップ移動 | LayoutEditor.autosave 10件、TemplatePageのslow-save/failed-saveテスト | mock UI遷移通過。実Chromeの停止時間の再測定は未実施 |
| 項目名の完全一致 | LayoutEditor.authoringの類似名・空白・全半角・大小文字ケース | 通常画面のテスト通過 |
| 手アイコンによる矩形移動 | LayoutEditor、pageAuthoringの分岐・対象テスト | ページ仮想見出し対応。複数ページ多角形は上記残件 |
| 区分変更時に意味づけが残る原因 | setRolesはinputの区分変更時にはrelationを除き、labelから見出しへの変更ではlabelCellIdsだけを除く | targetPath/input側meaningが残る実装。実DB保存往復の再現は未確認。意味を一律削除する変更はしていない |
| 繰り返し群全体と見出しの接続 | meaning/recordsの群接続・nestテスト | UIテスト通過、実テンプレート再開での確認は未実施 |
| hover接続線・曲線 | headingHover 6件、headingConnections 7件 | テスト通過 |
| 複数ページの仮想見出し | LayoutEditor.pages 11件内の追加・移動・復元検証 | 対象テスト通過 |
| XLSXの不要な再検出案内 | TemplatePageのphoto/paged別案内テスト | UIテスト通過。有効化成功の証拠ではない |
| 公開版カードのXLSX/PDF取得 | TemplatePageはactive版をprintVersionへ優先。TemplatePrintFilesはversionId/revisionで取得。TemplatePrintServiceは保存成果物SHAを検査 | frontend/APIの対象証拠あり。実ACTIVE版カードからの実ダウンロードは未確認 |
| 本番OCR→レビュー→補正→確定→出力 | paged用の実装・回帰テストあり | 受付が閉鎖中。実入力による一連の完了は未証明 |

## 今回の検証状態

- frontend: `npm.cmd test`、35 files / **373 passed（15.93秒）**、終了コード0。
- frontend: `npm.cmd run build` 成功。既存の500kB超chunk警告あり。
- backend full初回はunit/integrationのbootstrapテスト2組の同名衝突で収集エラー。
  integration側を `_runtime.py` へ改名し、内容・assertionは保持した。
- backend full再実行:
  `RUN_OPENAI_INTEGRATION_TESTS=false`、
  `.venv/Scripts/python.exe -m pytest -q --tb=short -o addopts='' -p no:cacheprovider --basetemp=.pt-native-bootstrap-full-final-0914`。
  **2262 passed / 1 skipped / 3 warnings（2133.72秒）**、終了コード0。
  既存skip 1件、既存font.copy deprecation警告3件。開始や中間出力でなく終了結果を記録した。
- full実行中は製品ソースを編集せず、別のnative rendererを起動しなかった。
- 環境identity lease・実行時隔離証拠検査を追加した後の最新backend full:
  `RUN_OPENAI_INTEGRATION_TESTS=false`、
  `.venv/Scripts/python.exe -m pytest -q --tb=short -o addopts='' -p no:cacheprovider --basetemp=.pt-environment-execution-full-0914`。
  **2348 passed / 1 skipped / 3 warnings（2315.61秒、38分35秒）**、終了コード0。
  app/testsのPython 460ファイルの開始・終了時SHAは全件一致、追加・削除0件。
  この実行でもソース変更・別native rendererの並行起動なし。
- OpenAI 0/100回、Google 0回。実DB・原本・スキャン画像の変更なし。

## full待機中のhost manager読取確認

WSLの `/proc/1/maps` の実行可能file mapping 24件を、現在のfileのdevice/inodeと照合した。
deleted mapping 0、現在のinodeとの不一致0。manager実体は `/usr/lib/systemd/systemd`。
固定loaderの `--list /usr/lib/systemd/systemd` による起動時依存集合（実体自身を含む）も
24件であり、解決先pathの集合は実mappingと一致した。両方の読取commandは終了コード0。

これは現在の一時点での観測であり、managerの設定読み込み履歴や短命の動的load、
変換中の全依存を保証しない。製品へ前後照合を接続した証拠や環境資格として扱わない。
host設定・service・環境パッケージを変更せず、Office/PDF/native rendererも別起動していない。

## 次の判断

環境資格の発行/失効と実入力安全処理を完成させる必要がある。
既存テスト通過や一部の実変換成功を、未実装の成功経路の代わりにしない。
fullの回帰失敗は0件。次は資格発行・失効と実入力安全処理の不足を実装し、
有効化から本番OCR・レビュー・補正・確定・出力まで実入力で証明する必要がある。

## 対象実DBの読み取り確認

`backend/data/app.db` をSQLite `mode=ro` / `query_only=ON` で開き、
対象版 `6d2187c0-ac7f-4d0c-90c8-a0fc381c2cc8` の状態を再取得した。終了コード0。

- status: `DRAFT`、保存ページ4件。
- renderer: `wsl-print-reference-v1`。
- 保存geometry: `wsl-fods-physical-length-v1`。検証用コピーの新しいCalcモデルとは異なる。
- `printFiles` に `xlsx` / `pdf` / `report` / `renderKey` がある。
  この照会はキーの存在確認であり、成果物のSHA再検証や取得成功の証明ではない。
- `referenceQualification` の記録なし。

実DBは更新していない。新しいモデル・workerによる検証用コピーの結果を、
対象実版での印刷再確定・資格保存・有効化成功と混同しない。
