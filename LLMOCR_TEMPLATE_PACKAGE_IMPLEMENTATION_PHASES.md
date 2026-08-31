# `.llmocr-template` 自己完結型テンプレートパッケージ実装計画

## 現在の実装状態

最終更新: 2026-09-01

| Phase | 状態 | 判定 |
|---|---|---|
| Phase 0 | 完了 | 現行依存関係、交換境界、禁止項目、再計算項目を文書化済み |
| Phase 1 | 完了 | Canonical Model、manifest、feature registry、SemVer、Canonical JSON、content digest、JSON Schemaを実装済み |
| Phase 2 | 完了 | 確認付きExport、Canonical変換、asset/XLSX sanitize、ZIP生成・自己検証を実装済み |
| Phase 3 | 完了 | 非信頼入力reader、競合管理、非active import、DB/asset補償付きcommitを実装済み |
| Phase 4 | 完了 | 一時inspection、短寿命token、Export/Import/operation/有効化API、確認UIを実装済み |
| Phase 5 | 完了 | 正式版・作業版のDrive同期、local同期状態、競合制御、確認UIを実装済み |
| Phase 6 | 完了 | live Drive A→B移送、generic/spreadsheet実OCR、再export意味的一致、実出力、security/回帰、文書を検証済み |
| Phase 7 | 対象外 | 公開配布・電子署名は将来対応 |

現在の停止位置はPhase 6完了時点である。Phase 6 acceptance gapはなく、Phase 7には着手していない。詳細は`docs/template-package/PHASE6_ACCEPTANCE_HANDOFF.md`を参照する。

## 1. 目的

1つのテンプレートを、元環境のSQLiteやローカルDB IDに依存せず、単独で書き出し・配布・Google Drive同期・別PCへの取り込みができるようにする。

配布形式は、独自拡張子を持つZIPコンテナとする。

```text
interview-card_1.2.0.llmocr-template
```

本機能は、システム全体を移行・復旧するための「持ち出しバックアップ」「ワークスペース同期」とは別機能として実装する。

## 2. 実装上の確定方針

- SQLiteおよびSQLAlchemyモデルを直接JSON化しない。
- DBモデルと配布モデルの間に、交換用のCanonical Template Modelを置く。
- ローカルDBのprimary key、PC固有パス、Googleアカウント固有情報をパッケージへ含めない。
- パッケージの恒久的な識別には、`publisherId + packageId`を使用する。
- 正式版の識別には、`publisherId + packageId + version`を使用する。
- 正式に書き出した同一バージョンの内容は変更不可とする。
- インポートと有効版への切り替えを分離し、取り込んだ版を自動的に有効化しない。
- パッケージには履歴全体ではなく、選択したバージョンの実行可能なリリーススナップショットを格納する。
- `quality_json`や`layout_json`をそのまま外部へ出さず、交換仕様として必要な項目だけを明示的に変換する。
- ユーザー固有の出力レイアウト、Google Drive ID、Spreadsheet ID、`owner_key`は含めない。
- 再利用可能な既定出力レイアウトは、利用者情報を除去したCanonical Modelとして格納できる。
- 元XLSXは通常templateには含めない。`SPREADSHEET_TEMPLATE`では現在のXLSX出力処理の実行依存物であるため、完全な自己完結packageにはsanitize済み元XLSXと対象sheet情報を必須とする。
- 候補リストと画像は個人情報を含む可能性があるため、書き出し前に内容を確認できるようにする。
- 電子署名を実装するまでは、`publisherId`を本人確認済みの配布者として扱わない。
- ZIP全体のバイト列ではなく、Canonical JSONとasset hashから安定したcontent digestを計算する。
- 外部パッケージは信用せず、読み取り・確認表示・取り込みの全段階で同じ安全制限を適用する。

## 3. 想定パッケージ構造

```text
manifest.json
template.json
quality.json

assets/
├─ clean-template.png
├─ handwriting-mask.png
├─ handwriting-mask-edited.png
└─ preview.png

optional/
├─ output-layout.json
├─ handwriting-candidates.json
└─ spreadsheet-source.xlsx
```

`definition.json`、`layout.json`、`candidate-sets.json`などは、独立更新、独立検証、遅延読み込みの必要がない限り`template.json`へ統合する。ファイル数を増やすこと自体を目的にしない。

`manifest.json`の`files`には、`manifest.json`自身を除く全entryを記録する。各entryには最低でも次を持たせる。

```json
{
  "path": "assets/clean-template.png",
  "role": "clean-template",
  "mediaType": "image/png",
  "size": 12345,
  "sha256": "..."
}
```

## 4. 識別子と状態モデル

### 4.1 正式版

```text
publisherId + packageId + version
```

- `version`はSemVerとして比較する。
- 同じ正式版を異なる内容で再生成しない。
- 同一version・同一content digestは同一物として扱う。
- 同一version・異なるcontent digestは競合として停止する。
- 古いversionの取り込みは許可できるが、既定では非アクティブな履歴版として扱う。

### 4.2 ローカルインストール

ローカルDBでは、外部識別子とローカルの`template_id`、`template_version_id`の対応を別テーブルで管理する。外部識別子をDB primary keyとして流用しない。

最低限保存する情報:

- `publisher_id`
- `package_id`
- `release_version`
- `content_digest`
- `local_template_id`
- `local_template_version_id`
- `installed_at`
- `source_type`
- `trust_status`
- 検証済みmanifestのスナップショット

### 4.3 Drive作業版

作業版は正式版と分離する。

```json
{
  "draftId": "uuid",
  "revision": 12,
  "baseReleaseVersion": "1.2.0",
  "contentDigest": "sha256:...",
  "modifiedAt": "2026-09-01T12:00:00Z"
}
```

Drive側のETagまたはrevisionもローカルに保存し、前回確認値と一致しない場合は自動上書きしない。

---

# Phase 0: 現行データの棚卸しと交換境界の確定（完了）

## 目的

現在のテンプレートが実行時に参照するDBレコード、設定、assetを漏れなく特定し、Canonical Modelへ含める情報と除外する情報を確定する。

## 実装・調査項目

1. 次の現行データについて、作成元、参照先、必須性、利用者依存性を一覧化する。
   - `Template`
   - `TemplateVersion`
   - `TemplateDefinitionRevision`
   - `TemplateCandidateSet`
   - `TemplateCandidateBinding`
   - `OutputLayoutTemplateRevision`
   - `template.yaml`
   - `quality_json`
   - `layout_json`
   - clean template、mask、preview等のartifact
   - spreadsheet sourceと印刷・出力関連asset
2. `quality_json`内の各キーを、次のいずれかへ分類する。
   - Canonical Modelへ移す意味情報
   - import時に再計算する情報
   - ローカル状態として除外する情報
   - PC固有パスまたは一時参照として禁止する情報
3. テンプレート実行に必要な承認済みdefinitionの選択ルールを決める。
4. 出力レイアウトについて、配布可能な既定値と`owner_key`依存状態を分離する。
5. 候補リスト、画像、XLSXについて個人情報・機密情報の分類ルールを決める。
6. アプリ全体で使用するバージョン値と、`requiredFeatures`のfeature registryを定義する。
7. generic templateとspreadsheet templateの双方について、実行時依存関係を確認する。

## 成果物

- 現行モデルからCanonical Modelへの項目対応表
- export禁止項目一覧
- import時再計算項目一覧
- feature registry初版
- アプリバージョン判定仕様
- generic/spreadsheet template別の必須asset一覧

## 完了条件

- 読み取り開始からレビュー、XLSX/Sheets出力までに参照されるテンプレート情報がすべて対応表に載っている。
- PC固有パス、ローカルDB ID、Google固有IDがCanonical Modelへ混入しないことをレビューで確認できる。
- 出力対象となるdefinition、layout、candidate settingsの版選択ルールが一意に決まっている。
- `minimumAppVersion`と`requiredFeatures`をバックエンドで判定できる前提が定義されている。

## 次フェーズへの移行条件

項目対応表に未分類の必須データが残っていないこと。

## 実施結果

- [Phase 0 現行データ監査](docs/template-package/PHASE_0_AUDIT.md)を作成した。
- `Template`、`TemplateVersion`、設定、layout、definition、candidate、output layout、asset、source workbookの交換境界を確定した。
- `quality_json`を「Canonicalへ移す情報」「import時再計算」「別項目へ移す情報」「export禁止」に分類した。
- legacy global candidate poolは自動移送せず、候補値確認後にtemplate固有データとして値渡しする方針を確定した。
- Google output layoutの`owner_key`、`sourceTemplateVersionId`、内部noteを交換形式から除外する方針を確定した。
- `SPREADSHEET_TEMPLATE`の元XLSXと対象sheet名は、現在のXLSX出力を維持するため条件付き必須と判定した。
- Phase 0の移行条件を満たした。

---

# Phase 1: Canonical Template Modelとパッケージ仕様の確定（完了）

## 目的

SQLite、Google Drive、別PC、将来のPostgreSQLに依存しないテンプレート交換契約を確定する。

## 実装項目

1. Python側にCanonical Template Modelを定義する。
2. Canonical Modelを唯一の定義元としてJSON Schemaを生成する。
3. 次を別バージョンとして定義する。
   - `formatVersion`: ZIPコンテナ構造
   - `schemaVersion`: Canonical Model構造
   - `version`: 個別テンプレートのリリース版
4. パッケージ内部IDの形式を確定する。
   - 意味のある安定IDまたはUUIDを使用する。
   - ローカルDB IDを使用しない。
   - package内参照はpackage内部IDだけで解決できるようにする。
5. `manifest.json`と`template.json`のJSON Schemaを作成する。
6. 未知フィールド、未知feature、将来versionの扱いを決める。
7. Canonical JSONの正規化方式を固定する。
   - UTF-8
   - キー順
   - 数値表現
   - Unicode表現
   - 改行・空白
8. content digestの計算対象と順序を固定する。
9. manifest署名を将来追加できるよう、署名対象範囲を定義する。
10. SemVerのpre-release、古い版、同一版競合の比較規則を実装する。

## 推奨コード構成

```text
backend/app/template_packages/
├─ __init__.py
├─ canonical_models.py
├─ manifest_models.py
├─ feature_registry.py
├─ canonical_json.py
├─ content_digest.py
├─ package_paths.py
├─ semantic_version.py
├─ schema_export.py
└─ schemas/
   ├─ manifest-v1.schema.json
   ├─ template-v1.schema.json
   ├─ quality-v1.schema.json
   └─ output-layout-v1.schema.json
```

## テスト項目

- Canonical Modelから生成したJSON Schemaとの整合
- package内部IDの重複・未解決参照拒否
- 正規化前のキー順や空白が異なっても同じcontent digestになること
- 意味内容が異なればcontent digestが変わること
- SemVerの正式版・pre-release比較
- 未知required featureの拒否
- optional featureの非対応時動作

## 完了条件

- 代表的なgeneric templateとspreadsheet templateをCanonical Modelだけで表現できる。
- Canonical ModelからローカルDB IDとPC固有パスが検出されない。
- JSON SchemaとPythonモデルの二重管理が発生していない。
- content digestを異なる実行環境でも再現できる。

## 次フェーズへの移行条件

Canonical Modelとmanifest仕様のレビューが完了し、実装中にDBモデルを直接JSON化しないことが確認されていること。

## 実施結果

- [Format v1 交換仕様](docs/template-package/FORMAT_V1.md)を作成した。
- `CanonicalTemplate`、`CanonicalTemplateQuality`、`CanonicalOutputLayout`を実装した。
- 現行DB ID、version ID、`owner_key`、PC固有パスを持たない交換モデルを実装した。
- generic templateのdefinition、複合判別、書類名、candidate setを検証できる。
- spreadsheet templateはsanitize済みsource workbookとportableなsheet/file metadataがなければ拒否する。
- manifestのrole、固定path、media type、SHA-256、content digest、timezone付きexport日時を検証できる。
- SemVer 2.0.0のpre-release precedenceとbuild metadata非依存比較を実装した。
- required featureと`minimumAppVersion`の互換判定を実装した。
- Canonical JSON v1としてUTF-8、NFC、key sort、非有限数拒否、`-0.0`正規化を実装した。
- Canonical JSON documentとbinary asset SHA-256からcontent digestを生成できる。
- Pydantic Modelから4種類のJSON Schemaを生成し、生成物の同期検査を追加した。
- Phase 1の移行条件を満たした。

## 検証結果

- Phase 1契約テスト: `19 passed`
- 関連template回帰テスト: `44 passed, 3 warnings`
- backend全体回帰: `375 passed, 1 skipped, 3 warnings`
- Ruff: 合格
- mypy: 合格
- JSON Schema同期検査: 合格

---

# Phase 2: 安全なExport機能（完了）

## 目的

ローカルテンプレートをCanonical Modelへ変換し、ユーザー固有情報を除去した自己完結型`.llmocr-template`を生成する。

## 実装項目

1. `CanonicalTemplateMapper`を実装する。
2. 選択されたテンプレートバージョンについて、承認済みの最新実行状態を収集する。
3. definition履歴全体ではなく、リリース対象の承認済みdefinitionを1つ選択する。
4. candidate setとbindingをpackage内部IDへ変換する。
5. 配布可能な既定出力レイアウトだけをCanonical化する。
6. 次の情報を強制的に除外する。
   - APIキー
   - OAuth token
   - Google/Dropbox固有ID
   - Spreadsheet ID
   - `owner_key`
   - PC固有パス
   - ローカルDB ID
   - OCR済み書類と処理履歴
   - 一時ファイルとキャッシュ
7. assetを一時作業領域へ収集する。
8. PNGの形式、寸法、pixel数を再検証する。
9. 画像メタデータを除去する。
10. 候補値と画像の書き出し確認情報を生成する。
11. `SPREADSHEET_TEMPLATE`の元XLSXを検査・sanitizeし、完全な自己完結packageでは必須assetとして格納する。その他modeでは元XLSXを拒否する。
    - `.xlsx`以外を拒否
    - マクロ、外部リンク、埋め込みオブジェクト
    - 作成者、組織、コメント、document properties
    - 隠しシートと非表示情報
12. 各payload entryのSHA-256、サイズ、media type、roleをmanifestへ記録する。
13. Canonical JSONとasset hashからcontent digestを生成する。
14. `.llmocr-template`を生成し、生成後に同じvalidatorで自己検証する。

## 推奨サービス構成

```text
TemplateExportService
CanonicalTemplateMapper
TemplateAssetCollector
TemplatePackageSerializer
TemplatePackageValidator
SpreadsheetSanitizer
```

## テスト項目

- generic/spreadsheet templateの書き出し
- PC固有パスとローカルIDが含まれないこと
- APIキー、OAuth token、Drive ID、Spreadsheet IDが含まれないこと
- 未承認definitionを書き出さないこと
- owner依存出力レイアウトを除外すること
- asset欠落、hash不一致、寸法不一致時に書き出しを停止すること
- 通常templateにXLSXを含めず、spreadsheet templateではsanitize済みXLSXと対象sheet metadataを必須にすること
- 同一入力から同一content digestを生成すること

## 完了条件

- 任意の利用可能テンプレートを1ファイルへ書き出せる。
- 生成されたpackageをvalidatorへ戻して全検証に合格する。
- 秘密情報、利用者固有情報、ローカルDB IDがpackageに存在しない。
- 書き出し対象となる候補値、画像、XLSXの有無をユーザーへ提示できるデータが用意されている。

## 次フェーズへの移行条件

代表テンプレートのpackage fixtureが作成され、内容レビューと自動検証の双方に合格していること。

## 実施結果

- `CanonicalTemplateMapper`を実装し、現在のactive versionだけを読み取り専用でCanonical Modelへ変換するようにした。
- config内のローカル`template_id`とversion番号は整合検査だけに使用し、packageには含めない。
- `quality_json`が指す承認済みdefinitionを1件だけ選択し、未承認履歴は書き出さない。
- template固有candidate setを決定的なpackage内部IDへ変換した。legacy global候補は自動移送しない。
- output layoutは明示した所有者の最新版だけを任意で選択できるようにし、`owner_key`、ローカルversion参照、内部noteを除去・portable bindingへ変換した。
- clean template、mask、edited mask、preview、手書き候補を収集し、artifactのmedia type、SHA-256、寸法、pixel数を再検証するようにした。
- PNGをdecode後に再encodeし、EXIF・text等の画像メタデータを除去した。
- spreadsheet templateでは元XLSXと対象sheet metadataを必須化し、通常templateに元XLSXが存在する場合は拒否するようにした。
- XLSXのマクロ、外部参照、埋め込みobject、custom XML、外部参照式、非表示sheet・行・列・定義名を拒否した。
- XLSXの作成者、更新者、document properties、コメント、外部hyperlinkを除去し、内部ZIPを決定的な順序・時刻で再生成した。
- candidate値、画像、XLSX、output layoutの有無を示す`TemplateExportInspection`を実装した。
- inspectionのconfirmation digestが一致する場合だけExportできる二段階契約を実装し、確認後に内容が変化した場合は停止するようにした。
- payloadごとのrole、media type、size、SHA-256をmanifestへ記録し、Canonical JSONとbinary asset hashからcontent digestを生成した。
- entry順、timestamp、permissionを固定した`.llmocr-template` ZIPを生成し、生成直後に再読込してmanifest、hash、Canonical Model、content digestを自己検証するようにした。
- `exports/template-packages/`へ任意保存できる処理を追加し、同一identity・versionの既存ファイルを暗黙に上書きしないようにした。
- API、UI、DB migration、Import、Drive同期には変更を加えていない。

## 検証結果

- Phase 1契約 + Phase 2 Exportテスト: `27 passed`
- backend全体回帰: `383 passed, 1 skipped, 3 warnings`
- Phase 2対象Ruff: 合格
- Phase 2対象mypy: 合格
- repository全体Ruff: Phase 2外の既存未コミットコードに`56 errors`が残存
- repository全体mypy: Phase 2外の既存未コミットコードに`38 errors in 7 files`が残存
- frontend: Phase 2でAPI/UIを変更していないため未実施

## Phase 3への引継ぎ

- `GeneratedTemplatePackageValidator`は、自システムが生成したin-memory packageの自己検証専用である。外部ファイルを信用して読ませてはならない。
- 外部packageのsize上限、entry数、展開後容量、圧縮率、Unicode/casefold衝突、symlink、暗号化entry等の防御はPhase 3で実装する。
- Phase 2はDBへpackage identityを保存しない。同一version競合の恒久判定tableとmigrationはPhase 3で追加する。
- Phase 2はlocal templateの状態を一切変更しない。Import、asset commit、rollback、非active版登録はPhase 3の責務とする。
- Phase 4のAPI/UIでは、`inspect`結果を画面に提示し、その`confirmationDigest`をExport実行時に返す契約を使用する。

---

# Phase 3: 安全で原子的なImport機能（完了）

## 目的

外部`.llmocr-template`を厳格に検証し、部分データや孤立ファイルを残さず別環境へ取り込む。

## 実装項目

1. ZIPを直接`extractall`せず、entryごとに検証して読み込む。
2. 次の制限値を設定として定義する。
   - 最大ZIPサイズ
   - 最大entry数
   - 最大展開後総容量
   - 最大圧縮率
   - 最大JSONサイズ
   - 最大画像ファイルサイズ
   - 最大画像幅・高さ・pixel数
   - 最大XLSXサイズ
   - XLSX内部の最大entry数・展開後総容量
3. entry pathについて次を拒否する。
   - 絶対パス
   - `..`
   - Windowsドライブ指定
   - symlink
   - NTFS代替データストリーム
   - Windows予約名
   - 末尾の空白またはピリオド
   - Unicode正規化、casefold後の重複
   - 重複entry
4. 暗号化entry、未対応圧縮方式、manifest未記載entryを拒否する。
5. manifestだけを上限付きで先に読み込む。
6. format、schema、app version、required featuresを検証する。
7. payloadのsize、SHA-256、media type、file signatureを検証する。
8. Canonical Modelとpackage内部参照の整合性を検証する。
9. PNGを安全にdecodeし、実寸法とpixel数を検証する。
10. XLSXをZIPとして再検証し、外部リンク等の禁止要素を検査する。
11. 競合状態を判定する。
12. package内部IDからローカルDB IDへのmappingを生成する。
13. assetを一時領域へ配置する。
14. DBとファイル保存を一体として扱う補償付きcommit手順を実装する。
15. 失敗時はDB rollback、一時ファイル削除、確定位置へ移動済みのasset削除を行う。
16. import済みpackage identityとcontent digestをDBへ記録する。
17. 取り込んだテンプレートバージョンは非アクティブ状態で登録する。

## 競合規則

| 条件 | 動作 |
|---|---|
| 同一identity・同一version・同一digest | 既存を使用し、重複登録しない |
| 同一identity・同一version・異なるdigest | 競合として停止する |
| 同一identity・新しいversion | 新しい非アクティブ版として追加する |
| 同一identity・古いversion | 確認後、非アクティブな履歴版として追加できる |
| 名前だけ同じ・identityが異なる | 別テンプレートとして扱う |
| required feature非対応 | DBを変更せず拒否する |
| schemaが新しすぎる | DBを変更せず拒否する |

## 推奨サービス構成

```text
TemplatePackageReader
TemplatePackageValidator
TemplateImportInspectionService
TemplateImportConflictService
CanonicalTemplateImporter
TemplateAssetCommitter
```

## テスト項目

- Zip Slip
- ZIP bomb
- duplicate entry
- Unicode/casefold衝突
- Windows予約名
- symlink
- 暗号化ZIP
- manifest未記載ファイル
- hash、size、MIME不一致
- 壊れたPNG/XLSX/JSON
- package内部参照切れ
- DB登録途中の例外注入
- asset移動途中の例外注入
- 同一packageの冪等import
- 各競合規則

## 完了条件

- 空の別環境へ、元環境のSQLiteを使わず取り込める。
- import失敗時にDBレコード、確定asset、一時ファイルが残らない。
- 同一packageの再importで重複が発生しない。
- 新規版を取り込んでも既存の有効版が自動的に切り替わらない。

## 次フェーズへの移行条件

悪意あるpackage fixtureと障害注入試験を含むbackend testが合格していること。

## 実施結果

- Phase 2の`GeneratedTemplatePackageValidator`を呼ばない、外部非信頼入力専用の`TemplatePackageReader`を独立実装した。専用テストでは自己生成用validatorを例外化してもreaderが正常に動作することを確認した。
- ZIPをファイルシステムへ展開せず、manifest先読み、entry単位の上限付き読込、hash・size・media type・signature検証を行うようにした。`extractall`は使用していない。
- ZIP、JSON、PNG、XLSXに個別のsecurity limitを設定し、外側ZIPだけでなくXLSX内部ZIPにもentry数、個別entry、展開後総容量、圧縮率の上限を適用した。
- pathをNFC正規化前後、casefold後、Windows規則を含めて検証し、絶対path、drive指定、backslash、colon/ADS、制御文字、空segment、`.`、`..`、Windows予約名、末尾空白・ピリオド、Unicode/casefold衝突、重複entryを拒否した。
- symlink・特殊file、暗号化entry、未対応圧縮、directory entry、ZIP comment/extra field、manifest未記載entryを拒否した。
- Canonical JSON、schema、format、app version、required feature、package内部参照、content digestをDB変更前に再検証するようにした。
- PNGをdecodeして実寸法とpixel数を照合し、XLSXは独立したbounded readerで再検証してmacro、外部link、埋め込み、OLE、custom XML、comment、外部参照式、非表示情報を拒否した。
- `0029_template_package_imports` migrationを追加し、外部identityとlocal templateの対応、およびreleaseごとのinstallation snapshotを保存するようにした。
- package内部IDから新規local template/version/job/artifact IDへのmappingを生成し、Canonical snapshotとmappingをinstallationへ保存した。
- assetをsystem管理下の一時領域へstageし、DB transaction内でrecordをflushした後に確定位置へ移動する補償付きcommitを実装した。
- DB flush、asset移動、DB commit前の例外ではDB rollback、確定済みasset削除、一時領域削除を行い、DBとassetの部分commitを残さないようにした。
- 同一identity/version/digestの再importは既存installationを検査して返し、重複登録しない。同一identity/versionでdigestが異なる場合は上書きせず停止する。
- 新しいversionは同一local templateの非active versionとして追加する。古いversionは明示許可がある場合だけ非active履歴版として追加する。名前だけ同じでidentityが異なるpackageは別templateとして登録する。
- importはactive versionを変更しない。候補設定とoutput layoutのlocal runtime materializationおよび有効化はPhase 4の明示操作へ残した。
- API、frontend、Drive同期、電子署名には変更を加えていない。

## Security limit

| 対象 | 上限 |
|---|---:|
| package ZIP | 128 MiB |
| package entry数 | 32 |
| package展開後総容量 | 256 MiB |
| package圧縮率 | 100倍 |
| manifest | 1 MiB |
| JSON 1 entry | 16 MiB |
| PNG 1 entry | 50 MiB |
| PNG幅・高さ | 各20,000 px |
| PNG総pixel数 | 100,000,000 |
| XLSX | 64 MiB |
| XLSX内部entry数 | 5,000 |
| XLSX内部1 entry | 32 MiB |
| XLSX内部展開後総容量 | 256 MiB |
| XLSX内部圧縮率 | 100倍 |

## Import mapping規則

- `publisherId + packageId`を1つのlocal templateへ対応させる。外部IDをlocal primary keyとして使用しない。
- package releaseごとに新しい`TemplateVersion`、import用`ProcessingJob`、`JobArtifact`を生成する。
- approved definitionとruntime qualityをlocal設定へ復元し、検証済みCanonical template、quality、任意output layoutをinstallation snapshotへ保存する。
- assetの保存先はpackage pathを使用して決めず、生成したlocal job/version IDからsystem側で決定する。
- package内部field、candidate、asset参照とlocal artifact IDの対応を`local_mapping_json`へ保存する。
- candidate/output layoutはimport時点では有効runtimeへ反映せず、有効化処理まで保留する。

## Commit・rollback手順

1. package全体を非信頼readerで検証し、inspection時のpackage SHA-256と再照合する。
2. template mutation lockとSQLiteの`BEGIN IMMEDIATE`を取得し、transaction内で競合を再判定する。
3. assetをsystem data root内の一時領域へstageする。
4. local DB recordを追加し、foreign key順にflushする。
5. assetを生成済み確定pathへ`os.replace`で移動する。既存pathは上書きしない。
6. 全asset確定後にDBをcommitする。
7. 例外時はDBをrollbackし、移動済みassetを削除し、一時領域をcleanupする。
8. commit結果が不明な例外ではinstallationの永続化を再確認し、DBが確定済みならassetを削除しない。

## 検証結果

- Phase 1契約 + Phase 2 Export + Phase 3 Import + migration: `62 passed in 4.65s`
- backend全体回帰: `411 passed, 1 skipped, 3 warnings in 65.20s`
- Phase 3対象Ruff: 合格
- Phase 3対象mypy: `Success: no issues found in 23 source files`
- repository全体Ruff: Phase 3外の既存未コミットコードに`56 errors`が残存
- repository全体mypy: Phase 3外の既存未コミットコードに`38 errors in 7 files`が残存
- 攻撃fixture: Zip Slip、drive path、ADS、予約名、末尾不正、空segment、Unicode/casefold衝突、duplicate、symlink、暗号化、未申告entry、directory、未対応圧縮、各容量・圧縮率上限、hash/size/MIME不一致、壊れたJSON/PNG/XLSX、XLSX外部link、寸法不一致、参照切れ、将来feature/app version、非Canonical JSON、manifest欠落をすべて拒否
- 障害注入: `after_db_flush`、`after_asset_move`、`before_db_commit`の全地点でDB record、確定asset、一時領域が残らないことを確認
- 冪等import: 同一packageの再importでinstallation、template version、assetが増えず、既存assetの存在とhashを再検査することを確認

## Phase 4への引継ぎ

- Import inspectionとconfirmのAPI、短寿命token、進捗APIは未実装である。
- import済みversionの内容確認と有効化API/UIは未実装である。import service自体はactive versionを変更しない。
- candidate setとoutput layoutのlocal runtime record生成は、有効化時に競合・所有者を確認して実施する必要がある。
- publisherは`UNVERIFIED`として保存する。署名・信頼判定は初期実装対象外である。
- 別環境round-trip、実OCR、実XLSX/Sheets出力はPhase 6の受け入れ試験まで未確認である。

---

# Phase 4: APIとユーザーインターフェース（完了）

## 目的

「読み取り」内のテンプレート管理動線から、安全に書き出し・確認・取り込み・有効化できるようにする。

## API実装項目

1. package export APIを追加する。
2. import用packageを一時保存し、検証結果を返すinspection APIを追加する。
3. inspection結果とpackage hashを結び付けた短寿命tokenを発行する。
4. 確認済みの同一packageだけを取り込むconfirm APIを追加する。
5. token期限切れ、package差し替え、hash変更を拒否する。
6. import進捗と結果を返すoperation APIを追加する。
7. 取り込んだ版を有効化する操作をimportとは別APIにする。

## UI実装項目

1. テンプレートカードへ「配布用に書き出す」を追加する。
2. 新規作成の「＋」付近へ「テンプレートを取り込む」を追加する。
3. 書き出し前確認画面を追加する。
   - テンプレート名とversion
   - 候補値の件数と内容
   - 画像一覧とpreview
   - XLSX有無
   - 除外されるユーザー固有情報
4. 取り込み確認画面を追加する。
   - publisherIdとpackageId
   - publisher確認状態
   - versionとcontent digest
   - format/schema version
   - minimumAppVersion
   - required/optional features
   - 判別条件概要
   - 書類名生成規則
   - asset一覧
   - XLSX有無
   - 競合状態
5. 書き出し・読み取り・検証・取り込みの進捗表示を追加する。
6. セキュリティエラー、互換性エラー、競合エラーを区別して表示する。
7. import完了後に「取り込んだ版を確認」「有効版にする」を別操作として表示する。

## UI上の禁止事項

- 未署名publisherを「確認済み」と表示しない。
- 取り込みと有効化を1つの確認操作にまとめない。
- 同一version競合を上書き選択で回避させない。
- 検証前の画像、XLSX内容を無制限に展開しない。

## テスト項目

- export操作とファイル取得
- import inspectionからconfirmまでの正常系
- inspection後のpackage差し替え拒否
- token期限切れ
- 競合・互換性・セキュリティエラー表示
- 進捗表示
- import後も既存の有効版が維持されること
- キーボード操作とフォーカス制御

## 完了条件

- 「読み取り」からテンプレート単体の書き出しと取り込みを完了できる。
- import前に内容、互換性、競合、個人情報リスクを確認できる。
- ユーザーの明示操作なしに有効版が切り替わらない。

## 次フェーズへの移行条件

backend、frontendの自動テストに加え、実ブラウザで書き出し・取り込み・有効化まで確認できていること。

## 実施結果

- Import inspection用packageを`tmp/template-package-inspections/`へoperation ID由来の名前で一時保存し、30分でoperationと一時ファイルを破棄するようにした。
- Export/Import確認tokenは256 bit相当の乱数から生成し、平文ではなくSHA-256だけをserver memoryへ保存するようにした。有効期限は10分、用途はExportまたはImportへ固定し、確認後は再利用できない。
- Import inspection、preview、confirmの各段階で、Phase 3の`TemplatePackageReader`を唯一の外部package readerとして使用した。Phase 2の`GeneratedTemplatePackageValidator`は外部入力に使用していない。
- inspection時のpackage SHA-256をtokenとoperationへ結び付け、confirm直前に一時ファイルを再読込してconstant-time比較するようにした。差し替え時は一時ファイルを削除し、DB import前に停止する。
- Export inspection/confirm、Import inspection/confirm、operation取得・削除、検証済みpreview取得、installation有効化のAPIを追加した。
- Import confirmは202を返し、単一workerで進捗と結果をoperationへ反映する。Import完了時点ではlocal versionを非activeのまま保持する。
- runtime調査により、candidate set/bindingは`Template`単位、output layoutは`owner_key + template_version_id + revision`単位、active切替は`Template.active_version_id`と`TemplateVersion.status`で管理されていることを確認した。
- 有効化では、現在のactive versionの楽観的一致を確認した後、candidate set/bindingの置換、owner別output layout revisionのmaterialization、旧activeのarchive、新versionのactive化、jobのconfirmed化を1つのDB transactionで実行するようにした。
- 有効化途中の例外ではtransaction全体をrollbackする。既に有効な同一installationへの再実行はruntime candidateの一致を確認して冪等に処理し、変更済みcandidateまたはoutput layoutは自動上書きせず競合停止する。
- 通常のtemplate承認APIからimport済みversionを直接active化する経路を拒否し、package有効化APIを経由させるようにした。
- 「読み取り」の新規作成ボタン付近へ「テンプレートを取り込む」、templateカードへ「配布用に書き出す」を追加した。Template管理画面にも同じ操作を追加した。
- Export確認画面でidentity/version、candidate値、asset、XLSX/output layout、除外情報を表示する。Import確認画面でpublisher未確認表示、format/schema/app互換性、feature、判別条件、書類名規則、asset、競合を表示する。
- upload/import/exportの進捗を表示し、security、compatibility、conflict、validation、system errorを区別して表示するようにした。
- Import完了後は「取り込んだ版を確認」と「有効版にする」を別操作として表示し、自動有効化しない。
- Phase 4ではDB migrationを追加していない。Phase 3の`0029_template_package_imports`を継続利用する。

## 検証結果

- Phase 1から4のtargeted backend test: `66 passed`
- backend全体回帰: `415 passed, 1 skipped`
- Phase 4対象frontend test: `36 passed`
- frontend全体回帰: `199 passed`
- frontend production build: 合格。既存の500 kB超chunk warningは残存
- Phase 4対象Ruff: 合格
- Phase 4対象mypy: `Success: no issues found in 28 source files`
- repository全体Ruff: Phase 4対象外を含む既存dirty worktreeに`61 errors`が残存
- repository全体mypy: Phase 4対象外の7ファイルに`38 errors`が残存
- 実ブラウザ: Import inspection、検証済みpreview、未有効化Import、独立した有効化、Export inspection、保存完了表示、Escapeによる閉鎖とfocus復帰を確認。ブラウザerror logは0件

## Phase 5以降への引継ぎ

- Google Drive単体テンプレート同期には着手していない。
- browser fixtureはPhase 4動線確認用の合成templateであり、実書類OCR、XLSX/Google Sheets出力、別環境round-tripはPhase 6の受け入れ試験へ残す。
- operation/tokenは単一process内の短寿命状態であり、server再起動後の再開は行わない。再起動時はinspectionからやり直す仕様とする。
- publisher署名が未実装のため、UIとDBのtrust statusは引き続き`UNVERIFIED`である。

---

# Phase 5: Google Drive単体テンプレート同期（完了）

## 目的

システム全体バックアップを復元せず、複数環境間で正式版テンプレートと作業版を安全に共有する。

## Drive保存構造

```text
LLM OCR/
├─ システムバックアップ/
│  └─ workspace-20260901.zip
└─ テンプレート/
   └─ jp.example/
      └─ interview-card/
         ├─ releases/
         │  ├─ 1.1.0.llmocr-template
         │  └─ 1.2.0.llmocr-template
         └─ drafts/
            └─ {draftId}.llmocr-template
```

## 実装項目

1. システムバックアップ用フォルダーとテンプレート用フォルダーを分離する。
2. 正式版packageをimmutableなDrive fileとしてアップロードする。
3. 同一identity/version/digestがある場合は再アップロードしない。
4. 同一identity/versionで異なるdigestがある場合は競合として停止する。
5. Drive file ID、folder ID、ETag、revision等はローカル同期テーブルだけに保存する。
6. これらのGoogle固有値を`.llmocr-template`へ含めない。
7. Drive上のpackageを一覧表示し、選択したものだけinspection/importできるようにする。
8. 作業版に`draftId`、`revision`、`baseReleaseVersion`、`contentDigest`を持たせる。
9. 前回取得したETag/revisionと一致する場合だけ作業版を更新する。
10. 別PC側の更新を検出した場合は自動上書きせず、競合として表示する。
11. 作業版から正式版を発行する操作を明示的に分離する。
12. Driveから取得したpackageにもローカルファイルと同じvalidatorを適用する。

## 同期時の禁止事項

- template編集のたびに正式versionを自動採番しない。
- Drive上の正式版を上書きしない。
- 作業版の競合を最終更新時刻だけで自動解決しない。
- Driveから取得したpackageを検証なしでimportしない。
- package取得と同時に有効版へ切り替えない。

## テスト項目

- PC Aで正式版をアップロードし、PC Bで取得・importできること
- 同一正式版の重複アップロード抑止
- 同一version・異なるdigestの競合
- PC A/PC Bの作業版同時更新競合
- ETag/revision不一致時の上書き停止
- Drive切断・タイムアウトからの再試行
- Drive固有IDがpackageに含まれないこと
- システムバックアップ同期と混同されないこと

## 完了条件

- 別PCが元PCのSQLiteを持たずにDrive上のテンプレートを利用できる。
- 正式版のimmutabilityがDrive上でも維持される。
- 作業版競合でデータが無言で失われない。
- システムバックアップとテンプレート同期の保存先、UI、処理が分離されている。

## 次フェーズへの移行条件

2台相当の独立データディレクトリを用いた同期試験と競合試験が合格していること。

## 実施結果

- `template_package_drive_release_syncs`と`template_package_drive_draft_syncs`を追加し、Google subject、Drive file/folder ID、Drive version、head revision ID、ETag、更新日時をpackage外のlocal同期状態へ保存した。
- システムバックアップの`LLM OCR ワークスペース`とtemplate単体同期の`LLM OCR テンプレート`を、別root marker・別API・別UIとして分離した。
- 正式版は`publisherId + packageId + version`で検索し、同一digestは既存fileを再利用し、異なるdigestは`DRIVE_TEMPLATE_RELEASE_CONFLICT`として停止するようにした。正式版を更新するPATCH経路は設けていない。
- Drive上のidentity folder名は表示用途に限定し、照合metadataにはidentityのSHA-256を使用した。正確なidentityはダウンロード後に`TemplatePackageReader`で検証したpackageからのみ取得する。
- Driveから一覧取得、アップロード後確認、inspection取得を行うすべての経路でpackage bytesを`TemplatePackageReader`へ通し、Drive metadataとmanifest/content digest/package SHA-256の一致も検証した。
- Drive取得packageのImport元を`DRIVE`として記録したが、publisher trust statusは`UNVERIFIED`のままとし、Import完了後もactive versionを変更しない。
- 作業版はlocal同期状態のdraft ID、revision、Drive version、head revision ID、ETagを前提条件として更新し、multipart PATCHへ`If-Match`を付与した。HTTP 412、またはいずれかの前回確認値不一致を競合として停止する。
- 作業版の内容が既に同一の場合は冪等成功とし、別環境で更新された場合は自動上書きせず、再取得を要求する。
- UIにDrive上の正式版・作業版一覧、同期状態、未確認publisher表示、選択inspection、正式版発行、作業版の新規作成・更新を追加した。Drive取得後もImport確認と有効化は別操作のままである。
- 2つの独立data rootを共有Drive transportへ接続する試験で、正式版共有、別環境Import、非active、重複抑止、異digest競合、作業版同時更新競合を確認した。
- package内部にDrive file ID、folder ID、Drive version、head revision ID、ETag、draft revisionが混入しないことを検査した。
- migration headは`0030_template_package_drive_sync`である。

## 検証結果

- Phase 1から5のtargeted backend test: `73 passed`
- 2 data root・Drive同期対象test: `6 passed`
- backend全体回帰: `422 passed, 1 skipped`
- Phase 5対象frontend test: `38 passed`
- frontend全体回帰: `201 passed`
- frontend production build: 合格。既存の500 kB超chunk warningは継続
- Phase 5対象Ruff: 合格
- Phase 5対象mypy: 合格
- repository全体Ruff: Phase 5外の既存箇所に`56 errors`が残存
- repository全体mypy: Phase 5外の既存箇所に`38 errors in 7 files`が残存

## Phase 6への引継ぎ

- 実Google Driveアカウントを用いたlive E2Eは未実施である。Phase 5ではDrive transportを用いた決定的な2環境同期・競合試験までを完了した。
- 空環境でのexport/import/re-export意味的一致、実書類OCR、XLSX/Google Sheets出力はPhase 6の対象として残す。
- Google Drive APIの`version`、`headRevisionId`、HTTP ETagをlocal同期状態として扱い、package仕様には追加しない。
- 電子署名がないためpublisherは引き続き`UNVERIFIED`である。

---

# Phase 6: 総合検証、移行、運用文書（完了）

## 目的

単にDBへ登録できるだけでなく、別環境で実際に読み取り・レビュー・出力まで再現できることを証明する。

## 実装・検証項目

1. DB migrationを追加し、既存テンプレートへ外部package identityを安全に付与できるようにする。
2. identity未設定の既存テンプレートについて、明示的な初回発行フローを用意する。
3. 次のround-trip試験を実装する。

```text
環境Aでexport
→ 空の環境Bでimport
→ 環境Bで再export
→ Canonical Modelとasset hashの意味的一致を確認
```

4. imported templateを使用して、実際に次まで実行する。
   - 書類アップロード
   - テンプレート選択
   - 読み取り
   - 判別条件評価
   - レビュー画面表示
   - 書類名生成
   - XLSX出力
   - 対応する場合はGoogle Sheets出力
5. generic templateとspreadsheet templateの双方で検証する。
6. 旧version、pre-release、未知feature、将来schemaの互換性試験を行う。
7. 大容量packageと上限境界値を試験する。
8. import各段階へ障害を注入し、rollbackとasset cleanupを確認する。
9. APIキー、OAuth token、個人情報、PC固有パスの漏えい検査を行う。
10. 利用者向け文書と開発者向けpackage仕様書を作成する。

## 必須受け入れ試験

| 対象 | 必須結果 |
|---|---|
| 別環境import | 元SQLiteなしで復元できる |
| 読み取り矩形 | 元環境と意味的に一致する |
| 判別条件 | 複合条件を同じ規則で評価できる |
| 書類名 | 同じ入力から同じ規則で生成される |
| 候補設定 | package内IDから正しく再割り当てされる |
| 出力レイアウト | 利用者固有IDなしで復元される |
| 失敗時rollback | DB・asset・一時ファイルが残らない |
| 同一package再import | 重複登録されない |
| 競合package | 自動上書きされない |
| セキュリティfixture | すべて拒否される |
| 実OCRスモーク | レビュー画面まで到達する |

## 完了条件

- Phase 0から5までの完了条件をすべて満たす。
- backend、frontend、migration、security、round-trip、実ブラウザ試験が合格する。
- 対象テンプレートで実OCRスモークが成功する。
- 既存の持ち出しバックアップとDriveワークスペース同期に回帰がない。
- 利用者がexport、import、競合、有効化、Drive同期の違いを文書から判断できる。

## 完了証跡

- 実Google Driveで環境Aから空の環境Bへ正式版を移送し、inspection、非active Import、明示activationを実施した。
- generic/spreadsheetの実OpenAI OCR、人手review、confirm、document library、XLSX、Google Sheets出力を完走した。
- 環境Bの再Exportは、環境A正式版とCanonical Model、quality、output layout、asset role→SHA-256、content digestが一致した。
- security fixture、compatibility、障害注入、秘密情報漏えい、workspace/backup回帰、backend/frontend/build/migrationを再検証した。
- 実ブラウザでDrive一覧、inspection、Import/有効化、OCR、出力、Export、競合、focus復帰を確認し、console errorは0件だった。
- 利用者向け`docs/template-package/USER_GUIDE.md`と開発者向け`docs/template-package/FORMAT_V1.md`を確定した。

---

# Phase 7: 公開配布対応（初期実装の対象外・未着手）

## 目的

組織外や不特定多数への配布で、配布者と内容の真正性を検証できるようにする。

## 将来実装項目

- Ed25519によるmanifest/content digest署名
- publisher keyと`keyId`の管理
- trusted/untrusted/revoked publisher表示
- 署名鍵のローテーション
- ライセンス、作者、配布元、利用条件のmetadata
- 署名検証失敗時の取り込み禁止または明示的隔離
- package catalogまたはマーケットプレイス
- 自動更新通知

## 初期実装で行わないもの

- publisher認証局
- 公開マーケットプレイス
- 差分package
- package間依存関係
- 未確認publisherの自動信頼
- Dropbox同期

Phase 7は、Phase 0から6の交換形式を壊さず追加できることだけを初期設計で保証する。

---

# フェーズ共通運用規則

1. 各Phaseは、前Phaseの完了条件を満たしてから開始する。
2. Phase 0と1の仕様確定前にZIP生成処理を先行実装しない。
3. 各Phaseを独立したレビュー単位とし、仕様・実装・検証結果を分離して報告する。
4. targeted testの成功と全体回帰試験の成功を区別する。
5. DB migration、秘密情報、Driveデータ、既存のdirty worktreeを個別に確認する。
6. 既存テンプレートの履歴・OCR結果・書類ライブラリをpackage exportへ混入させない。
7. acceptance gapが残る場合は、機能完成と表現しない。

# 初期実装の最終完了条件

Phase 0から6を完了し、次の条件をすべて満たした時点で初期実装完了とする。

- 任意の利用可能テンプレートを`.llmocr-template`へ書き出せる。
- 元PCのSQLiteを使わず、別環境へ取り込める。
- ローカルDB IDをpackageへ含めない。
- 読み取り矩形、フィールド、セル、関連付けが復元される。
- 複合書類判別条件と書類名生成規則が復元される。
- 承認済みテンプレート定義、候補設定、画像、既定出力レイアウトが復元される。
- 新しいversionを既存版と並存させられる。
- 同一version・同一digestを重複登録しない。
- 同一version・異なるdigestを競合として停止する。
- importと有効化が分離されている。
- Zip Slip、ZIP bomb、hash不一致、schema不正、壊れたassetを拒否する。
- import失敗時にDB、確定asset、一時ファイルへ部分データを残さない。
- APIキー、OAuth token、ユーザー固有ID、PC固有パスをexportしない。
- Drive上で正式版を上書きせず、作業版競合を検出できる。
- システムバックアップとテンプレート単体同期が別機能として動作する。
- 空の別環境でround-trip試験と実OCRスモークが成功する。

# 次回実装開始時の作業

Phase 6は完了している。次の実装は自動では開始しない。Phase 7の電子署名、publisher信頼昇格、公開配布は、別途明示された場合だけ着手する。

将来変更でも、外部packageは`TemplatePackageReader`だけを入口とし、Importと有効化、正式版と作業版、template単体同期とシステムバックアップ同期を分離する。Phase 6の受け入れ証跡は`docs/template-package/PHASE6_ACCEPTANCE_HANDOFF.md`を正とする。

# 実装履歴

## 2026-09-01: Phase 0・Phase 1

### 実装範囲

- 現行依存関係監査
- Canonical Template Model
- Canonical quality/output layout
- manifest v1
- package path契約
- feature registryとアプリ互換version
- SemVer比較
- Canonical JSONとcontent digest
- JSON Schema生成・同期検査
- 契約・回帰テスト

### 重要な判断

- 交換形式はDB dumpではなく実行可能なrelease snapshotとする。
- definition履歴は含めず、選択versionの承認済みdefinitionだけを含める。
- qualityは内部JSONを複製せず、限定した意味情報へ変換し、import時に再計算する。
- output layoutの所有者情報とローカルversion noteは含めない。
- candidate値は個人情報を含む可能性があるため、Phase 2のexport確認対象とする。
- spreadsheet templateの元XLSXは現在の出力処理に必要なため、sanitizeできない場合は完全packageのexportを失敗させる。
- package importと有効化は将来Phaseでも分離する。

### 残作業への引継ぎ

- Phase 1は交換契約だけであり、`.llmocr-template`ファイルはまだ生成できない。
- DBからCanonical Modelへのmapperは未実装である。
- `quality_json`再計算ロジックは監査済みだが、export mapperへの接続は未実装である。
- XLSX sanitizerは未実装である。
- ZIP security limitとreaderはPhase 3で実装する。
- package identity保存tableとmigrationはPhase 3まで追加しない。
- API/UIはPhase 4まで追加しない。
- Google Drive単体同期はPhase 5まで追加しない。
- 電子署名は初期実装対象外である。

### 既存作業ツリーとの境界

Phase 0・1で追加したのは`backend/app/template_packages/`、契約テスト、`docs/template-package/`、本計画書である。既存のworkspace cloud sync関連の未コミット変更は編集対象に含めず、保持した。

## 2026-09-01: Phase 2

### 実装範囲

- active template versionからCanonical release snapshotへのmapper
- approved definition、candidate set、任意output layoutの選択・変換
- PNG asset収集、hash・寸法・pixel検証、metadata除去
- spreadsheet source XLSXの検査・sanitize・決定的再生成
- Export内容のinspectionとconfirmation digest
- manifest、content digest、決定的ZIP生成
- 生成packageの自己検証
- system storageへの任意出力
- Phase 2専用テスト

### 重要な判断

- Exportを`inspect`と`export`の二段階に分け、candidate値・画像・XLSX・output layoutの確認結果が変化した場合は生成を停止する。
- output layoutは既定で含めず、所有者を明示した場合だけportableな内容へ変換して含める。
- spreadsheet sourceの非表示情報は自動削除で意味を変えず、利用者による確認が必要なためExportを拒否する。
- コメント、外部hyperlink、作成者情報等は実行依存ではないためsanitize時に除去する。
- 同一release pathの既存ファイルを上書きしない。恒久的なidentity競合管理はPhase 3へ送る。
- 自己検証用validatorと非信頼入力用readerを分離する。後者はPhase 3より前に実装しない。

### 残作業への引継ぎ

- Import、競合判定table、migration、原子的asset commitは未実装である。
- 外部ZIPに対するbomb、Zip Slip、symlink、暗号化entry等の防御は未実装である。
- API endpointとExport確認UIは未実装である。
- Google Driveへのtemplate単体同期は未実装である。
- 別PC round-tripと実OCR smokeは未実施である。
- 電子署名は初期実装対象外である。

### 既存作業ツリーとの境界

Phase 2で追加・変更したのは`backend/app/template_packages/`、`backend/tests/unit/test_template_package_export.py`、本計画書である。既存のworkspace cloud sync関連の未コミット変更、DB model、API、frontendは編集していない。

## 2026-09-01: Phase 3

### 実装範囲

- 非信頼入力専用のbounded package reader
- ZIP、JSON、PNG、XLSXのsecurity limit
- path、entry属性、圧縮、manifest、hash、signature、Canonical Modelの検証
- package identityとrelease installationを保存するDB migration
- identity/version/digestによる競合判定
- Canonical packageからlocal template/version/job/artifactへのmapping
- asset stagingとDB transactionを組み合わせた補償付きcommit・rollback
- 非active import、冪等import、既存installation整合検査
- 攻撃fixture、競合test、障害注入test、migration test

### 重要な判断

- Phase 2の自己生成用validatorは外部入力へ流用せず、外部入力の検証責務を独立readerへ閉じ込めた。
- ZIPは展開せず、すべてのentryをmemory上で上限付きに読み取る。XLSX内部ZIPにも同じ防御原則を適用する。
- package pathからlocal保存先を導出しない。保存先はlocal IDからsystem側が生成する。
- DB transactionを先に確定させず、asset確定後にDB commitする。途中失敗では確定assetも補償削除する。
- 同一identity/version/digestは既存利用、異なるdigestは無条件停止とし、上書きoptionを設けない。
- importと有効化を分離し、imported versionはDRAFTかつ非activeで登録する。
- candidate/output layoutはCanonical snapshotを失わず保存するが、local runtimeへの反映は明示的有効化まで保留する。
- 署名未実装のため、import元にかかわらずtrust statusは`UNVERIFIED`とする。

### 残作業への引継ぎ

- API、frontend、一時upload token、進捗表示、有効化操作はPhase 4まで実装しない。
- Google Drive単体template同期はPhase 5まで実装しない。
- 別環境round-trip、import templateを使った実OCR、XLSX/Sheets出力はPhase 6で検証する。
- publisher署名と公開配布はPhase 7の将来対象である。
- repository全体のRuff 56件、mypy 38件はPhase 3外の既存未コミット箇所に残っている。Phase 3対象検査とbackend testは合格している。

### 既存作業ツリーとの境界

Phase 3で追加・変更したのは`backend/app/template_packages/`、`backend/alembic/versions/0029_template_package_imports.py`、`backend/app/db/models.py`、`backend/tests/unit/test_template_package_import.py`、`backend/tests/integration/test_migration.py`、Phase 2の決定的XLSX再生成に必要なcore timestamp正規化、および本計画書である。既存のworkspace cloud sync、backup、Google Drive、frontend関連の未コミット変更は保持した。

## 2026-09-01: Phase 4

### 実装範囲

- Import inspection用一時保存、短寿命token、package SHA-256差し替え防止
- Export inspection/confirm APIとpackage download
- Import inspection/confirm APIと非同期operation/result API
- 検証済みpreview asset APIとoperation破棄
- candidate/output layout materializationを伴う独立有効化API
- 通常承認経路によるpackage activationの迂回防止
- 「読み取り」とTemplate管理画面のExport/Import確認dialog
- 進捗、publisher未確認、security/compatibility/conflict error表示
- runtime有効化、rollback、冪等性、競合、token、差し替え、UIの自動テスト
- OAuth無効の隔離data rootを用いた実ブラウザ確認

### 重要な判断

- 外部packageは、upload inspection、preview、confirm importのすべてでPhase 3の`TemplatePackageReader`を通す。Phase 2の生成物自己検証器を外部入力へ流用しない。
- tokenは短寿命・用途固定・一回限りとし、packageの平文tokenを永続化しない。
- UIから`owner_key`を指定させず、認証済みsubjectまたはlocal runtimeが決定した値を使用する。
- Importは非active状態で完了させ、有効化は現在のactive versionを条件にした別操作とする。
- candidate set/bindingは現行runtimeがTemplate単位で参照するため、有効化時にTemplate全体をCanonical snapshotへ原子的に置換する。
- output layoutは現行runtimeの`owner_key + template_version_id`へmaterializeし、既存内容が異なる場合は上書きしない。
- active切替、candidate、output layout、job状態を同一DB transactionへ置き、途中失敗時は全変更をrollbackする。
- 既に有効な同一installationはruntime内容が一致する場合だけ冪等成功とし、利用者変更がある場合は競合として停止する。

### 残作業への引継ぎ

- Google Drive単体テンプレート同期は未着手である。
- 別環境round-trip、実書類OCR、XLSX/Google Sheets出力はPhase 6へ残している。
- server restartを跨ぐoperation再開は実装しない。短寿命inspectionは再実行する。
- 電子署名がないためpublisherは`UNVERIFIED`のままである。
- frontend buildの500 kB超chunk warning、repository全体Ruff 61件、mypy 38件はPhase 4対象外の既存課題として残る。

### 既存作業ツリーとの境界

Phase 4で追加・変更したのはtemplate package operation/activation/API、imported versionの承認迂回防止、lifecycle/router接続、Phase 4 API test、frontendのtemplate package dialogと読み取り・Template管理画面への接続、および本計画書である。既存のworkspace cloud sync、backup、Google Drive同期関連の未コミット変更は保持し、Phase 5のDrive単体テンプレート同期は追加していない。

## 2026-09-01: Phase 5

### 実装範囲

- template package専用Drive rootとidentity/release/draft folder
- immutable release upload、重複抑止、digest競合停止
- draft ID・revision・Drive version・head revision・ETagによる楽観的競合制御
- release/draftのlocal同期状態とDB migration 0030
- Drive一覧、package取得、Reader検証、inspection接続
- Drive正式版発行、作業版新規作成・更新API
- Drive同期状態、未確認publisher、競合を表示する確認UI
- 2つの独立data rootを使った同期・競合試験
- `If-Match`とHTTP 412のtransport試験

### 重要な判断

- workspace backupとtemplate packageは、folder marker、保存root、API、UI、local stateを共有しない。
- Drive metadataは索引・競合制御だけに使い、packageのidentityと内容は必ず`TemplatePackageReader`の検証結果を正とする。
- 正式版はcreateのみとし、同一identity/versionの異digestを更新する操作を実装しない。
- 作業版は更新日時だけで勝者を決めず、local保存済みのrevision、Drive version、head revision ID、ETagをすべて確認する。
- HTTP ETagはDriveのfile resourceをGETしたresponse headerから取得し、更新時は`If-Match`へ戻す。
- Google固有状態はlocal DBとDrive appPropertiesへ限定し、`.llmocr-template`のmanifestやpayloadには追加しない。
- DriveからのImportも非activeで完了し、有効化を独立操作として維持する。

### 残作業への引継ぎ

- live Google Drive E2E、空環境round-trip、実OCR、XLSX/Google Sheets出力はPhase 6で実施する。
- Phase 5の自動試験はDrive transportを共有remoteとして用いた2環境試験であり、Google API実通信の受け入れ試験ではない。
- publisher署名とtrust昇格はPhase 7の対象であり、現時点では`UNVERIFIED`を維持する。
- frontend buildの500 kB超chunk warning、repository全体Ruff 56件、mypy 38件はPhase 5対象外の既存課題として残る。

### 既存作業ツリーとの境界

Phase 5で追加・変更したのはtemplate package Drive transport/sync service、local同期モデルとmigration、Phase 4 operationへの`DRIVE` source接続、Drive API/UI、同期・競合test、および本計画書である。既存のworkspace cloud sync、portable backup、document library/Sheets同期の未コミット変更は保持した。Phase 6以降の受け入れ試験・運用文書・署名実装は追加していない。

## 2026-09-01: Phase 6（途中停止）

### 実施範囲

- 実Google Driveアカウントを用いた環境Aから空の環境Bへのrelease取得、inspection、import、activation
- generic/spreadsheetの正式release 1.1.0発行
- generic templateの実OpenAI OCR、candidate判定、書類名生成、人手レビュー
- spreadsheetのsource sheet名を完全一致で保持するexport修正と回帰test
- 新version有効化時にCanonicalのtemplate名・document typeを反映する修正
- Import中はmetadataを変更せず、有効化時だけ反映する境界test

### 停止判定

利用量上限の指示により受け入れ検証を途中で停止した。環境Bからの再export、意味的一致、spreadsheet実OCR、XLSX/Google Sheets出力、security/boundary、workspace backup回帰、全体回帰、利用者・開発者文書が未完了である。このためPhase 6完了とは判定しない。

### 再開記録

実施証跡、digest、独立data root、14項目の進捗、次回の手順は`docs/template-package/PHASE6_ACCEPTANCE_HANDOFF.md`へ記録した。Phase 7の署名・公開配布には着手していない。

## 2026-09-01: Phase 6（再開・完了）

### 実施範囲

- generic 1.1.0のconfirm、document library、XLSX/Google Sheets出力
- spreadsheet 1.1.0の実OpenAI OCR、review、confirm、XLSX/Google Sheets出力
- 未割当て/空OCR値によるsource workbook固定値消失の修正と回帰test
- local名が未変更でもremote baselineが古い場合のDrive名再調整と回帰test
- 環境Bからのgeneric/spreadsheet再Exportと意味的一致比較
- runtimeのfield/cell/candidate/identity/document name/output layout/source sheet metadata再現確認
- compatibility、Phase 3 security fixture、failure injection、秘密情報・環境依存情報漏えい検査
- portable/workspace backup、Drive workspace sync、document library、Sheets、template管理の回帰
- backend全体、frontend全体、production build、migration、schema、Ruff、mypy、diff whitespace検査
- 実ブラウザでDrive一覧、inspection、非active Import、明示activation、OCR結果、出力、Export、競合、focus、console error 0件を確認
- 利用者向け運用文書とFormat v1開発者仕様書を確定

### 完了判定

Phase 6の全acceptance gapを解消した。外部入力reader、非active Import、`UNVERIFIED`、immutable release、同一identity/version異digest拒否、Drive metadata境界、workspace backup分離を維持している。Phase 7には着手していない。

### 検証結果

- package targeted backend: 72 passed
- 既存機能回帰: 117 passed
- backend全体: 426 passed, 1 skipped
- frontend全体: 201 passed
- production build、migration 7件、migration head、schema同期、Phase 6対象Ruff/mypy、diff whitespace: 合格
- repository全体Ruff 52件、mypy 36件/6 files、frontend chunk warningはPhase 6外の既存課題として残る

詳細な実データ、比較項目、security limits、実ブラウザ証跡、未解決事項は`docs/template-package/PHASE6_ACCEPTANCE_HANDOFF.md`に記録した。
