# 保存ページ上での区分・意味づけ・矩形編集

対象は横1ページ×縦NページのXLSX下書き。新しい境界モデルや自動分類を導入せず、
TemplateVersionの`quality_json.printLayout.pages`をそのまま使用する。

## 操作

1. 「印刷ページを編集」を開く。保存設定がなければ、原本の合法なmanual rowBreaksを初期候補にする。
2. 行境界を目視確認し「印刷ページを下書き保存」を押す。
3. 既存の区分・意味づけ画面の「編集ページ」でページを切り替える。
4. 枠を単一選択・Ctrl+クリック・範囲選択し、区分変更・完全削除・結合・意味づけで手動修正する。

セル一覧・クリック・範囲選択・意味づけ経路の座標は選択ページに限定する。
編集中の状態は全ページ共通の既存LayoutDefinitionで保持し、ページ切り替えで破棄しない。
描画用の矩形は`page.fields[].cellId`で既存矩形へ対応付ける。セルID、canonical座標、
他ページの区分・関係を、表示座標や配列indexから再生成しない。
選択欄の詳細編集は既存canonical画像と既存相対座標のまま扱う。
完全削除は選択ページでも行える。削除した矩形に属するfield・mapping・relationだけを除外し、
残る矩形のセルID・canonical座標、他ページの区分・関係、ページ境界・transformを維持する。
枠追加・任意の位置やサイズの変更はページ表示中に行えない。
結合・結合解除はページ数によらず各ページ内で利用できる。元セルIDとcanonical座標を
cellMerges.partsに保持し、結合親の矩形はpartsの外接矩形とする。
保存前の表示はpartsの明示IDに対応するページ矩形から生成する。保存済みfieldの
sourceParts（cellId・sourceRange）とページ原本セルの矩形により、再読込後の解除も表示できる。
ページ境界をまたぐ結合、欠落・重複のある元Excel範囲、完全非表示の部品、元セル座標改変は拒否する。
意味づけ・繰り返しに使用中のセルは接続を外してから結合・解除する。
XLSXでは既存取込処理の区分を保持し、読込時の隣接位置による見出し候補の自動降格を行わない。
目視編集していないページの区分を保存時に変えない。既存の画像テンプレートの正規化は維持する。

## 非表示・縮小文字の下書き表示

`read_source(..., draft_support=True)`は非表示行列の幅・高さを0とし、完全に非表示の
原本セルを描画しない。既存矩形が完全に非表示なら黙って除外せず拒否する。
原本の結合範囲、手動改ページ、セル値・書式は変更しない。
セル内縮小はPillow参考画像で文字列をセル内へ収める。回転文字は引き続き拒否する。

新しい下書きでこれらを使用する場合、表示モデルを`draft-visible-cells-v1`とする。
取込時の`spreadsheetPreviewGeometry`があれば、その原本SHA・シート・保存済み寸法を検証して使用する。
ない場合は従来の列幅換算による参考寸法を使用する。保存した下書きの寸法方式を後から変更しない。
既存のlegacyモデルと`lo-import-twip-v1`保存版は、保存済みの寸法方式を維持する。
Excelのフォント・組版再現や本番OCRの座標保証を意味しない。

## 保存APIと安全境界

`PUT /api/v1/template-versions/{id}/print-layout/authoring?base_revision=...`

bodyは既存LayoutDefinition。DRAFT、revision、保存planのlayout SHAを検証する。
通常矩形と結合partsへ展開した元セル集合は、重複のない既存集合の部分集合だけを許可する。
残存元セルの座標・形状を維持し、追加IDは検証済み結合親と保存partsの復元だけに限定する。
元XLSXからの範囲解決・出力結合の連続矩形検証・内部値保護と、保存済みページ境界による
page_planの事前検証をDB/config保存前に実行する。任意の構造変更を許可するものではない。
既存LayoutService.saveで手動編集内容を保存し、同じページ境界でfields/bindingsを更新する。
原本セル画像・transformを再利用し、TemplateVersionとその編集用PREPARE snapshotのみ更新する。
受付済みOCR job、公開版、他versionは変更しない。枠追加・任意の位置やサイズの変更・競合は409で拒否する。
不正な結合範囲・ページ横断は409または422で拒否し、保存前のlayout・config・planを保持する。
保存失敗時はフロントの編集状態を保持する。通常layout APIで構造を変えた場合は、従来どおり
printLayoutをstaleとして拒否し、ページの再保存を必要とする。

本番rendererの`environmentQualified=false` / `productionQualified=false`は変更しない。
ACTIVE化の`PRINT_LAYOUT_OCR_NOT_AVAILABLE`、paged OCRの`PRINT_REFERENCE_NOT_VALIDATED`、
reference qualificationのモデル照合、Office adapterのhidden行列拒否も維持する。
このため目視編集できる帳票でも、印刷用XLSX/PDFの厳格なadapterでは未対応となる場合がある。
今回の変更で印刷再現性や複数ページOCR全体を完成扱いにしない。

## 検証対象

- `tests/unit/test_draft_page_authoring.py`: hidden/縮小、元manual breaks、保存寸法、結合境界、renderer拒否。
- `tests/integration/test_page_authoring.py`: 区分・意味づけ・矩形削除、残存canonical座標・画像・境界保持、競合、公開版拒否、V1/V2。
- `LayoutEditor.pages.test.tsx`: ページ別の単一・範囲選択、手動分類・削除、意味づけドラッグ、他ページ保持、保存失敗と再試行。
- 既存OCR contract verifier、backend/frontend全体、frontend build、ruff、mypy、diff確認。

## 結合・解除の追加（2026-09-11）

開始HEAD: `b613c11a72b20d4c7cafc7ec3dd7ddf9adf52b55`。開始時は27 trackedファイルの
未コミット変更と既存untrackedファイルがあった。それらを保持し、今回の結合対応を追加した。
実DBとmigration headはともに`0031_template_field_identity`。DB schema変更はない。

変更境界はLayoutEditor/pageAuthoring、print_layout_source/service、追加の
page_authoring_validationと既存XLSX出力の元範囲検証入口。page fieldへ任意のsourcePartsを追加する。
元セル座標・既存snapshotの形式を移行せず、結合親だけに意図した新ID・外接矩形を作る。
provider/model/prompt/schema/association、registration、crop計算、補正/確定処理は変更しない。

回帰ケースは1/2ページの横・縦結合、4ページすべてでの結合/解除、結合親の削除、
保存・再取得・親への意味づけとmapping、元Excel結合範囲、版コピー後のID/範囲保持を含む。
ページ横断、欠落セル、親/parts座標改変、parts重複はlayout/config/plan不変のまま拒否する。
過去OCR job snapshot、ページ境界・transform・参考画像、未編集ページの矩形を比較する。

Chromeでは合成4ページの独立した検証画面を使用し、1ページ目と4ページ目の範囲選択→結合→
ローカル保存、再読込後の結合解除と元セル復元を操作・描画で確認した。
保存APIは別途integration fixtureで検証しており、このChrome確認を本番DBまで通したE2Eとは扱わない。
一時的な検証画面のソースとタブは終了時に除去した。利用中の帳票への結合・削除・保存は行っていない。

OpenAI/Google実APIは0回。認識精度・実手書きのcrop安全性・実responseのmismatch件数は未実測。
ページ手動編集の結合拒否契約を明示的に拡張し、既存OCR contractと本番gateを維持する。
従来の結合パターンCtrl+C/Vはページ表示中には未対応で、該当案内を表示しない。

利用中のChromeでも4ページ目を範囲選択し、結合・一括削除・区分変更ボタンの表示を確認した。
実帳票の変更・保存は行わず、表示を1ページ目へ戻した。backend/frontendを再起動して実装を反映した。

| 追加実装の最終検証 | 結果 |
| --- | --- |
| `scripts/verify_ocr_contract.py`（変更前・変更後） | 各194 passed |
| backend `pytest -q -o addopts='' -p no:cacheprovider --basetemp=.pt-page-merge-full-02` | 824 passed、1 skipped、3 warnings（658.51秒） |
| `test_page_authoring.py` + `test_spreadsheet_template_output_contract.py` | 60 passed |
| frontend `npm.cmd test` | 28ファイル、291 passed |
| 最後の案内文変更後のページ編集テスト / `npm.cmd run build` | 7 passed / 成功（bundleサイズ警告あり） |
| 対象Pythonの `ruff check` | 成功 |
| 対象4サービスの `mypy --follow-imports=silent` | 既存のopenpyxl型定義不足3件が残る。他の3サービス単独は成功 |
| `git diff --check` | 成功 |

変更対象は `LayoutEditor.tsx`、`pageAuthoring.ts`、`printLayout.ts`、
`page_authoring_validation.py`（新規）、`print_layout_service.py`、`print_layout_source.py`、
`spreadsheet_template_output_service.py`、対応するfrontend/backendテストと本節・Safety Contract・XLSX Print Layout文書。
既存の未コミット差分は保持した。AGENTS.mdへの追加変更、staging、commit、pushは行っていない。

## 初回の実測結果（2026-09-09）

開始・終了HEADはともに `b880d76484e3c687b9efc3032fcc5d6cae1c0ff9`。
staging・commit・pushは実施していない。開始時の未コミット変更を保持し、
今回の差分は開始時コピーとも照合した。既存の印刷成果物出力・元画像の寸法補正実装は今回の新規実装件数に含めない。

| 検証 | 結果 |
| --- | --- |
| `backend/.venv/Scripts/python.exe scripts/verify_ocr_contract.py`（変更前） | 194 passed |
| backendで `.venv/Scripts/python.exe -m pytest tests --basetemp=<検証用外部ディレクトリ>` | 775 passed、1 skipped、3 warnings |
| 同Pythonによる今回追加のunit/integration 2ファイル | 12 passed |
| `scripts/verify_ocr_contract.py`（変更後の最終再実行） | 194 passed、1 warning |
| frontendで `npm test` | 25ファイル、260 passed |
| frontendで `npm run build` | 成功。既存の大きいbundleに関する警告あり |
| 変更対象backendの `ruff check`、`mypy --follow-imports=silent --ignore-missing-imports` | 成功 |
| `git diff --check`、変更範囲・開始時差分との照合 | 成功 |

新規テストはbackend 12件、frontend 6件の計18件。
変更後の契約テストは初回に既存のXLSX出力テストでWindowsの一時ファイル置換がアクセス拒否された。
該当テスト単独と契約テスト全体の再実行は通過した。出力コードやテスト条件を変更して解消したものではない。

キャリア・プラン作成補助シート（学生用）の実ファイルを、独立した検証用DB・保存領域で確認した。
元の手動改ページ28・60・89行を使用し、A1:AK28、A29:AK60、A61:AK89、A90:AK102の4ページを保存した。
Chrome上でページ切替、矩形選択、区分の手動変更、意味づけ、保存、一覧からの再表示を確認した。
367矩形のID・canonical座標はすべて不変。区分変更は手動操作したI46の1件だけで、
意味づけも操作したI47の1件のみ。原本と保存済み原本のSHA一致を確認した。
詳細は [実測データ](page-authoring-evidence.json) を参照。

4枚のPillow参考画像（各1241×1754）の生成・配信・画面表示を確認した。
LibreOfficeによる実印刷/PDF変換は今回実施していない。PDFページ数・印刷位置の一致は未検証。
この帳票に対する厳格な印刷用XLSX/PDF adapterの拒否条件を解除していない。
rendererは引き続き未認定で、参考画像の表示成功をqualificationの根拠にはしていない。

OCR認識・provider・prompt・rectangle detection・出力処理は今回変更していない。
変更したのはテンプレート下書きの表示と手動編集保存経路であり、mapping生成は既存実装を利用する。
物理対応・version独立性・保存競合・拒否条件の検証とOCR契約テストにより、既存OCR contractを維持した。
横方向分割、新しいページ分割アルゴリズム、自動意味づけ・追加の自動分類、
review・correction・confirmed・Google Sheets・過去jobの変更、本番paged OCRの開放は対象外。

## 今回の変更ファイル

- backend: `app/api/v1/routes/print_layout.py`、`app/services/draft_page_geometry.py`、
  `app/services/print_layout_source.py`、`app/services/print_layout_renderer.py`、
  `app/services/print_layout_service.py`
- backend tests: `tests/unit/test_draft_page_authoring.py`、`tests/integration/test_page_authoring.py`
- frontend: `src/api.ts`、`src/pageAuthoring.ts`、`src/components/LayoutEditor.tsx`、
  `src/components/PrintLayoutEditor.tsx`、`src/pages/TemplatePage.tsx`
- frontend tests: `src/components/LayoutEditor.pages.test.tsx`、`src/pages/TemplatePage.test.tsx`
- docs: `ocr-safety-contract.md`、`xlsx-print-layout.md`、`page-authoring.md`、`page-authoring-evidence.json`

既存の利用中アプリには変更を強制反映していない。バックエンドが非reload起動の場合は再起動し、
ブラウザを再読込する必要がある。検証操作は独立したアプリ環境で行い、利用中のテンプレートを編集していない。
