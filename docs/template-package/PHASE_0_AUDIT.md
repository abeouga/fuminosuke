# `.llmocr-template` Phase 0 現行データ監査

## 1. 監査結果

実施日: 2026-09-01

現行テンプレートは、SQLite、`template.yaml`、テンプレートversion配下の画像、ジョブartifact、利用者別Google Sheets出力レイアウトに分散している。テンプレート単体の交換形式を作るには、これらをそのまま複製せず、実行に必要な意味情報だけをCanonical Template Modelへ写す必要がある。

Phase 0では実データや秘密情報を読み出さず、DBモデル、service、domain model、migration、既存のportable bootstrap処理から依存関係を確定した。

## 2. 現行データと交換モデルの対応

| 現行データ | 現在の役割 | Canonical Modelでの扱い | Import時の扱い |
|---|---|---|---|
| `Template.name` | 表示名 | `template.json.name` | 新しいローカルtemplateへ設定 |
| `Template.document_type` | 帳票種別 | `template.json.documentType` | 新しいローカルtemplateへ設定 |
| `Template.id` | ローカルDB識別子 | 含めない | 新規採番 |
| `Template.active_version_id` | ローカル有効版 | 含めない | import後の明示的有効化で設定 |
| `Template.archived_at` | ローカル表示状態 | 含めない | import時は非archiveとして作成 |
| `TemplateVersion.id` | ローカルversion識別子 | 含めない | 新規採番 |
| `TemplateVersion.version_number` | ローカル連番 | 含めない | ローカル連番を新規採番 |
| `TemplateVersion.mode` | 実行方式 | `template.json.mode` | 対応するmodeへ復元 |
| canonical width/height | 座標系 | `template.json.canonicalWidth/Height` | asset寸法と照合して復元 |
| `config_relative_path` | PC内設定パス | 含めない | Canonical runtimeから新しい`template.yaml`を生成 |
| `template.yaml` | OCR登録、矩形、mapping、判別、書類名 | `template.json.runtime` | ローカルIDを割り当てた設定へ変換 |
| `layout_json` | セル、関係、レコード構造 | `template.json.layout` | `LayoutDefinition`として検証して保存 |
| `quality_json` | 意味情報、検証状態、パス、revisionの混在 | 限定した`quality.json`へ正規化 | package内容から再計算して照合 |
| `TemplateDefinitionRevision` | AI生成・修正・承認履歴 | 選択versionの承認済み定義1件だけ格納 | 新しい承認済みrevisionとして作成 |
| definitionの`templateVersionId` | ローカル参照 | 含めない | 新しいローカルversion IDへ置換 |
| `TemplateCandidateSet` | template固有候補 | 値渡しで`candidateSets`へ格納 | package内部IDからDB IDへ再割り当て |
| `TemplateCandidateBinding` | 候補とtargetの関連付け | `targetPatterns`へ正規化 | import先mappingへ再接続 |
| legacy `SireName` pool | グローバル候補 | 自動では含めない | export時に確認後、template固有候補として値渡し可能 |
| `OutputLayoutTemplateRevision` | 利用者別Sheets帳票レイアウト | 選択した再利用可能レイアウトだけ別JSON化 | `owner_key`を新環境で新規割り当て |
| output layoutの`owner_key` | Google利用者/ローカル所有者 | 含めない | import利用者に関連付けるまで未設定 |
| output layoutの`sourceTemplateVersionId` | ローカルversion参照 | 含めない | package内の自己参照として扱う |
| output layoutの内部note | bindingまたはローカルversion参照 | bindingを明示フィールド化し、version noteを除外 | 新しいローカルnoteへ再生成 |
| clean template PNG | OCR登録基準 | 必須asset | hash・形式・寸法検証後に保存 |
| handwriting mask | 編集・再生成用asset | 存在する場合に格納 | asset参照を新規作成 |
| edited mask | 利用者修正版mask | 存在する場合に格納 | hashからローカル品質状態を再構成 |
| preview | UI確認用 | 存在する場合に格納 | preview assetとして保存 |
| handwriting candidates | mask候補情報 | 明示確認後に任意格納 | package JSONとして検証して保存 |
| source workbook | spreadsheet出力の実行依存物 | `SPREADSHEET_TEMPLATE`ではsanitize後に条件付き必須 | 新しい`sourceWorkbook` artifactとして保存 |
| source job ID | 作成履歴 | 含めない | import専用provenanceまたは新規処理へ置換 |
| OCR job、結果、書類ライブラリ | 利用データ | 含めない | 復元しない |

## 3. `quality_json`の交換境界

`quality_json`は外部契約ではなく、現在は複数serviceの内部状態格納領域になっている。そのままexportしてはならない。

### Canonical qualityへ移す項目

| 意味 | Canonical項目 | Import時の扱い |
|---|---|---|
| 帳票外形が検証済み | `documentBoundaryVerified` | warningとassetから再確認 |
| layout検証済み | `layoutVerified` | Canonical layoutを再検証 |
| layout確定済み | `layoutFinalized` | mappingとlayout整合性から再計算 |
| レコード構造検証済み | `recordStructureVerified` | layoutの値と一致確認 |
| 承認済みdefinition有無 | `definitionApproved` | package内definitionから再計算 |
| definition妥当性 | `definitionValid` | runtime validatorで再計算 |
| 有効mapping数 | `fieldMappingCount` | enabled mapping数から再計算 |
| 公開可能な警告コード | `warnings` | ローカルIDやパスを含む文言を除外 |

### 別のCanonical項目へ移すもの

| 現行項目 | 移動先 |
|---|---|
| `sourceSheetName` | `template.json.spreadsheet.sheetName` |
| `sourceFileName` | `template.json.spreadsheet.fileName` |
| edited maskの存在 | `template.json.assets.handwritingMaskEdited` |
| asset SHA-256 | `manifest.json.files[].sha256` |

### Import時に再計算するもの

- `fieldMappingCount`
- `layoutVerified`
- `layoutFinalized`
- `recordStructureVerified`
- definitionのvalid/approved状態
- mask SHA-256
- clean template SHA-256と画像寸法
- printable PDF、preview、print cell metadata
- ローカルlayout input revision

### 絶対にexportしないもの

- `spreadsheetSourcePath`
- `spreadsheetPrintablePdfPath`
- `editedMaskArtifactId`
- ローカルartifact ID
- `templateDefinitionRevision`
- `layoutInputRevision`
- `templateDefinitionLayoutInputRevision`
- source job ID
- PC固有パス
- Google/Drive/Spreadsheet ID
- OAuth subject、access token、refresh token

## 4. テンプレート種別ごとの必須asset

| Asset | `REFERENCE_TEMPLATE` | `SINGLE_IMAGE_EXPERIMENTAL` | `SPREADSHEET_TEMPLATE` |
|---|---:|---:|---:|
| clean template PNG | 必須 | 必須 | 必須 |
| `template.json` | 必須 | 必須 | 必須 |
| `quality.json` | 必須 | 必須 | 必須 |
| layout | 必須 | 必須 | 必須 |
| approved definition | 使用している場合必須 | 使用している場合必須 | 使用している場合必須 |
| handwriting mask | 存在する場合 | 存在する場合 | 任意 |
| edited mask | 存在する場合 | 存在する場合 | 任意 |
| preview | 推奨 | 推奨 | 推奨 |
| sanitized source XLSX | 不可 | 不可 | XLSX出力を維持するため必須 |
| default Google output layout | 任意 | 任意 | 任意 |

現行の`SpreadsheetTemplateOutputService`は元XLSXを直接開き、選択sheetへOCR値を書き込む。このため、spreadsheet templateを完全に自己完結させる場合、元XLSXとsheet名は必須である。元XLSXを含められない場合は完全版packageとしてexportせず、OCR専用の別仕様を将来定義する。

## 5. 個人情報・機密情報の分類

### 自動除外

- OCR済み書類
- OCR結果と修正履歴
- 書類ライブラリ
- APIキーとOAuth token
- Google/Dropbox/Spreadsheet固有ID
- PC固有パス
- source jobと処理イベント

### export前に内容確認が必要

- candidate setの値
- legacy `SireName`から移す候補値
- clean template、preview、mask
- handwriting candidates
- 元XLSXのセル値、コメント、隠しsheet、document properties
- Google output layout内のnote、固定値、リンク、formula

「テンプレートに属する」という理由だけでは非個人情報とは判定しない。

## 6. feature registry初版

| Feature ID | 意味 |
|---|---|
| `template-package-core-v1` | package基本契約 |
| `field-mappings-v1` | 読み取りmapping |
| `layout-v3` | 現行layout schema v3 |
| `composite-document-detection-v2` | 複数矩形・groupによる書類判別 |
| `document-name-v1` | ライブラリ/XLSX/Sheets共通書類名規則 |
| `template-definition-v1` | 承認済み汎用definition |
| `candidate-sets-v1` | template固有候補設定 |
| `mark-recognition-v1` | mark/checkbox認識 |
| `spreadsheet-template-v1` | 元XLSXを利用するtemplate |
| `xlsx-layout-v1` | XLSXセル構造と出力mapping |
| `google-output-layout-v1` | 利用者IDを除去したSheets出力layout |

未知のrequired featureが1つでもある場合はimport不可とする。未知のoptional featureは、当該機能を無効化してもtemplateの必須動作が維持できる場合だけ無視できる。

## 7. アプリ互換version

初版の互換versionは`0.1.0`とする。SemVer 2.0.0のprecedenceで比較し、build metadataは優先順位へ影響させない。

`minimumAppVersion`はbackend package versionおよびfrontend package versionと同じリリースversionを使用する。version更新時はfeature registry、manifest互換判定、両package metadataを同一変更で更新する。

## 8. Phase 0で確定した禁止事項

- DB rowをそのまま`model_dump`してpackageへ入れない。
- `quality_json`をそのまま`quality.json`として保存しない。
- source workbookなしでspreadsheet templateを完全復元可能と表現しない。
- legacy global candidate poolを無確認でpackageへ取り込まない。
- `owner_key`付きoutput layoutを配布既定値として扱わない。
- imported templateを自動的に有効版へ切り替えない。

## 9. Phase 1への移行判定

現行の実行依存物はCanonical Model、条件付きasset、import時再計算項目のいずれかへ分類済みである。Phase 1の交換契約実装へ進行可能と判定した。
