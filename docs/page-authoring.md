# 保存ページ上での区分・意味づけ編集

対象は横1ページ×縦NページのXLSX下書き。新しい境界モデルや自動分類を導入せず、
TemplateVersionの`quality_json.printLayout.pages`をそのまま使用する。

## 操作

1. 「印刷ページを編集」を開く。保存設定がなければ、原本の合法なmanual rowBreaksを初期候補にする。
2. 行境界を目視確認し「印刷ページを下書き保存」を押す。
3. 既存の区分・意味づけ画面の「編集ページ」でページを切り替える。
4. 枠を選び、既存の区分ボタン・保存する情報・意味づけドラッグで手動修正する。

セル一覧・クリック・範囲選択・意味づけ経路の座標は選択ページに限定する。
編集中の状態は全ページ共通の既存LayoutDefinitionで保持し、ページ切り替えで破棄しない。
描画用の矩形は`page.fields[].cellId`で既存矩形へ対応付ける。セルID、canonical座標、
他ページの区分・関係を、表示座標や配列indexから再生成しない。
選択欄の詳細編集は既存canonical画像と既存相対座標のまま扱う。
原本との対応を壊す枠追加・削除・GUI結合はページ表示中に行えない。
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
既存のlegacyモデルと`lo-import-twip-v1`保存版は、従来の処理・拒否条件を維持する。
Excelのフォント・組版再現や本番OCRの座標保証を意味しない。

## 保存APIと安全境界

`PUT /api/v1/template-versions/{id}/print-layout/authoring?base_revision=...`

bodyは既存LayoutDefinition。DRAFT、revision、保存planのlayout SHA、矩形ID・座標・結合を検証する。
既存LayoutService.saveで手動編集内容を保存し、同じページ境界でfields/bindingsを更新する。
原本セル画像・transformを再利用し、TemplateVersionとその編集用PREPARE snapshotのみ更新する。
受付済みOCR job、公開版、他versionは変更しない。geometry変更・競合は409で拒否する。
保存失敗時はフロントの編集状態を保持する。通常layout APIで構造を変えた場合は、従来どおり
printLayoutをstaleとして拒否し、ページの再保存を必要とする。

本番rendererの`environmentQualified=false` / `productionQualified=false`は変更しない。
ACTIVE化の`PRINT_LAYOUT_OCR_NOT_AVAILABLE`、paged OCRの`PRINT_REFERENCE_NOT_VALIDATED`、
reference qualificationのモデル照合、Office adapterのhidden行列拒否も維持する。
このため目視編集できる帳票でも、印刷用XLSX/PDFの厳格なadapterでは未対応となる場合がある。
今回の変更で印刷再現性や複数ページOCR全体を完成扱いにしない。

## 検証対象

- `tests/unit/test_draft_page_authoring.py`: hidden/縮小、元manual breaks、保存寸法、結合境界、renderer拒否。
- `tests/integration/test_page_authoring.py`: 区分・意味づけ、canonical座標・画像・境界保持、競合、公開版拒否、V1/V2。
- `LayoutEditor.pages.test.tsx`: ページ別選択、手動分類、意味づけドラッグ、他ページ保持、保存失敗と再試行。
- 既存OCR contract verifier、backend/frontend全体、frontend build、ruff、mypy、diff確認。

## 今回の実測結果（2026-09-09）

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
