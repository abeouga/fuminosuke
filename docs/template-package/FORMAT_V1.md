# `.llmocr-template` Format v1 交換仕様

## 1. 仕様状態

- `formatVersion`: `1`
- `schemaVersion`: `1`
- 状態: Phase 6受け入れ検証済み
- 対象: package構造、Canonical Model、manifest、compatibility、content digest、Export/Import/有効化mapping、非信頼入力検証、Drive同期境界
- 対象外: 電子署名、publisher信頼昇格、公開配布、マーケットプレイス

## 2. コンテナ

`.llmocr-template`はZIPコンテナである。ZIP entry名はUTF-8、NFC正規化済みの相対POSIX pathを使用する。

```text
manifest.json
template.json
quality.json
assets/clean-template.png
assets/handwriting-mask.png
assets/handwriting-mask-edited.png
assets/preview.png
optional/handwriting-candidates.json
optional/output-layout.json
optional/spreadsheet-source.xlsx
```

v1ではroleごとのpathとmedia typeを固定する。任意名のentry、manifest未記載entry、同一roleの複数entryは認めない。

## 3. package identity

```text
publisherId + packageId
```

がtemplateの外部identityであり、

```text
publisherId + packageId + version
```

が正式releaseのidentityである。

- `publisherId`: reverse-DNS形式。例: `jp.example`
- `packageId`: 小文字英数字とhyphenによるslug。例: `interview-card`
- `version`: SemVer 2.0.0
- 同一release identityはimmutable
- `publisherId`は署名導入前は自己申告値であり、配布者本人の証明ではない
- Format v1のtrust statusは常に`UNVERIFIED`であり、文字列が既知でも自動で信頼しない

ローカル`template_id`、`template_version_id`、DB primary keyは外部identityに使用しない。

## 4. manifest

最小例:

```json
{
  "format": "llm-ocr-template",
  "formatVersion": 1,
  "schemaVersion": 1,
  "publisherId": "jp.example",
  "packageId": "interview-card",
  "name": "面接カード",
  "version": "1.2.0",
  "minimumAppVersion": "0.1.0",
  "requiredFeatures": [
    "field-mappings-v1",
    "template-package-core-v1"
  ],
  "optionalFeatures": [],
  "exportedAt": "2026-09-01T12:00:00Z",
  "contentDigest": "sha256:...",
  "files": []
}
```

`requiredFeatures`、`optionalFeatures`、`files`は昇順で格納する。feature重複、required/optional重複、role重複、正規化後path重複を禁止する。

`files`には`manifest.json`自身を含めない。`manifest.json`を含めるとhash参照が循環するためである。

## 5. Canonical Template Model

`template.json`のrootは`CanonicalTemplate`である。

```text
schemaVersion
name
documentType
mode
canonicalWidth
canonicalHeight
runtime
spreadsheet
layout
approvedDefinition
candidateSets
assets
```

### `runtime`

次を含む。

- registration設定
- OCR region
- field mapping
- composite subfield
- document identity fieldsとmatch group
- document name規則
- mark設定
- export column設定

次を含めない。

- template ID
- template version ID/number
- template表示名の重複コピー
- config file path

### `layout`

現行`LayoutDefinition` schema v3を検証済みの値として格納する。canonical width/heightはrootと一致しなければならない。

### `approvedDefinition`

選択versionで実行対象となる承認済みdefinitionだけを格納する。AI生成・修正・承認の履歴全体は含めない。

次を除外する。

- `templateVersionId`
- ローカルrevision番号
- 作成者/owner情報

import時は新しいローカルversion IDを割り当てて承認済みrevisionを作成する。

### `candidateSets`

candidate setは値渡しとし、package内部ID、名称、候補値、許可suffix、target patternを持つ。

- package内部IDは英数字、dot、underscore、hyphenのみ
- candidate set名は正規化後に一意
- 候補値は正規化後に一意
- 1つのtargetへ複数candidate setを割り当てない
- targetは有効なtext mappingを参照する
- ローカルcandidate set IDを含めない

### `spreadsheet`

`SPREADSHEET_TEMPLATE`だけが持つ。

```json
{
  "sheetName": "面接カード",
  "fileName": "面接カード.xlsx"
}
```

source workbook assetと組み合わせて、元XLSXの対象sheetと出力名を復元する。`fileName`はpathではなくportableな表示ファイル名である。

`sheetName`はExcel worksheet名の完全一致値である。先頭・末尾空白を除去してはならない。Phase 6では末尾空白を持つsheet名について、環境間round-trip、XLSX出力、Google Sheets出力で保持されることを確認した。

### `assets`

asset roleに対応する固定package pathだけを保持する。実ファイルのhash、size、media typeはmanifestが保持する。

`SPREADSHEET_TEMPLATE`では`optional/spreadsheet-source.xlsx`が条件付き必須である。他modeにsource workbookを含めることは禁止する。

## 6. Canonical quality

`quality.json`は現行`quality_json`のdumpではない。次の交換可能な状態だけを持つ。

```text
schemaVersion
documentBoundaryVerified
layoutVerified
layoutFinalized
recordStructureVerified
definitionApproved
definitionValid
fieldMappingCount
warnings
```

importerはこれらを信用せず、Canonical Templateとassetを検証して再計算し、宣言値と不一致なら拒否する。

## 7. Canonical Google output layout

`optional/output-layout.json`は利用者IDを除去した`CanonicalOutputLayout`である。

- `owner_key`を含めない
- `sourceTemplateVersionId`を含めない
- `llm-ocr:`で始まる内部noteを含めない
- OCR bindingはcellの`binding`として明示する
- cell座標は宣言row/column内に収める
- mergeとdimension overrideは宣言範囲内に収める

Googleアカウントとの関連付けはimport後のローカル操作で行う。

## 8. Canonical JSON v1

content digestへ使用するJSONは次の規則で正規化する。

1. UTF-8、BOMなし
2. object keyを辞書順に並べる
3. 不要な空白・改行を入れない
4. keyとstring valueをUnicode NFCへ正規化する
5. NFC正規化後に重複するkeyを拒否する
6. `NaN`、正負のInfinityを拒否する
7. `-0.0`を`0.0`として扱う
8. array順は保持する
9. object keyはstringだけを許可する

この規則はFormat v1固有である。異なる正規化方式へ変更する場合はdigest schemaを変更する。

## 9. content digest

ZIPファイル全体のhashはcontent identityに使用しない。圧縮方式、entry timestamp、entry順でZIP byte列が変化するためである。

content digestは次から計算する。

```text
digestSchema = llm-ocr-template-content-v1
canonical JSON documents
binary asset SHA-256
```

JSON documentはpathとCanonical JSON値、binary assetはpathとlowercase SHA-256をpath昇順で並べてhashする。

```text
sha256:<64 lowercase hex characters>
```

package identityとrelease versionはcontent digestへ含めない。将来の電子署名が、identity、version、content digestを含むmanifestへ署名する。

## 10. SemVer比較

- major、minor、patchの順で比較する
- 正式版は同じcore versionのpre-releaseより新しい
- pre-releaseのnumeric identifierはnumeric比較する
- numeric identifierはnon-numeric identifierより低い
- build metadataはprecedenceへ影響しない
- leading zeroを持つnumeric identifierを拒否する

## 11. compatibility

import可能条件:

```text
current app version >= minimumAppVersion
AND
requiredFeaturesの全件を実装済み
```

未知required featureがある場合は、schemaを解析できてもimportしない。

## 12. JSON Schema

PydanticのCanonical Modelを唯一の定義元とし、次を生成する。

```text
backend/app/template_packages/schemas/
├─ manifest-v1.schema.json
├─ template-v1.schema.json
├─ quality-v1.schema.json
└─ output-layout-v1.schema.json
```

生成済みSchemaとコード上のModelが異なる場合、testを失敗させる。Schemaだけを手動修正しない。

## 13. Export mapping

Exportは明示されたローカルtemplate/versionだけを対象にする。DB、runtime config、承認済みdefinition、candidate/output layout、assetから交換可能な値だけをCanonical Modelへ写す。

- `Template.id`、`TemplateVersion.id`、job ID、DB primary key、owner、ローカルpathを除外する。
- 実行対象の承認済みdefinitionだけを含め、編集履歴は含めない。
- candidate setはpackage内部IDと値へ変換し、ローカルcandidate IDを除外する。
- output layoutはownerとsource template version IDを除外し、cell bindingを保持する。
- spreadsheet source workbookは値・数式・書式・mergeを保持し、hidden data、external link、macro、埋め込みobjectを拒否する。
- assetはroleごとの固定pathへ収集し、manifestのhash、size、media typeと一致させる。
- 自己生成ZIPは`GeneratedTemplatePackageValidator`で検査する。これは外部入力readerではない。

inspectionと生成は分離する。inspection結果と生成直前のsource fingerprintが異なる場合はexportしない。

## 14. 非信頼入力とImport mapping

外部packageの唯一の検証入口は`TemplatePackageReader`である。file、Drive正式版、Drive作業版の取得元にかかわらず同じreaderを通す。readerはZIPを展開せず、各entryを上限付きで読み、manifest、Canonical JSON、asset、content digest、compatibility、内部参照を一括検証する。`extractall`は禁止する。

Import mappingは次の規則を使用する。

- 外部identityは`publisherId + packageId`、release identityはこれに`version`を加えた値とする。
- 新しいローカルtemplate/version/job/asset IDを割り当てる。package内のIDをローカルprimary keyとして使用しない。
- runtime config、layout、承認済みdefinition、candidate set、output layout、spreadsheet metadataを新しいローカルversionへ写す。
- candidate setとtarget bindingはpackage内部IDから新しいローカルIDへ写し替える。
- output layoutのowner/source versionはローカル値を割り当て、Canonical cell binding、座標、merge、dimensionを保持する。
- Import完了時はactive versionを変更せず、既存templateの`name`と`document_type`も変更しない。
- 同一release identityかつ同一digestの再Importは冪等に既存installationを返す。
- 同一release identityかつ異なるdigestは`VERSION_CONFLICT`として拒否し、上書きしない。
- 古いreleaseは保存できるが、自動で有効化しない。

DB flush、asset確定移動、DB commitを一つの補償付き処理として扱う。失敗時はDBをrollbackし、確定assetとstageを除去し、active versionを変更しない。Phase 6では`after_db_flush`、`after_asset_move`、`before_db_commit`を障害注入で検証した。

## 15. 有効化mapping

Importと有効化は別操作である。有効化はimport済みversionを明示的に選択し、expected active versionを用いる楽観的競合検査の後に行う。

- candidate setとcandidate bindingをruntimeが参照するlocal recordへmaterializeする。
- Canonical output layoutを対象owner/versionのruntime layoutへmaterializeする。
- active versionを切り替え、旧activeをarchive状態へ遷移させる。
- 有効化するCanonical Modelの`name`と`documentType`をtemplate metadataへ反映する。
- すでにactiveなversionとtemplate metadataが矛盾する場合は`TEMPLATE_PACKAGE_METADATA_CONFLICT`で停止する。
- 同じversionへの再有効化は冪等である。
- 競合または途中失敗時はactive version、metadata、candidate/output layoutを一体でrollbackする。

Phase 6の環境B再Exportが環境Aの正式版と意味的一致したため、上記materializationから再びCanonical Modelを再構成できることを実証した。

## 16. security limitsとpath規則

既定の非信頼入力上限は次のとおりである。

| 対象 | 上限 |
|---|---:|
| package byte | 128 MiB |
| ZIP entry数 | 32 |
| ZIP展開後合計 | 256 MiB |
| ZIP圧縮比 | 100 |
| manifest | 1 MiB |
| JSON entry | 16 MiB |
| image entry | 50 MiB |
| image幅・高さ | 各20,000 px |
| image総pixel | 100,000,000 |
| XLSX byte | 64 MiB |
| XLSX内部entry数 | 5,000 |
| XLSX内部1 entry | 32 MiB |
| XLSX内部展開後合計 | 256 MiB |
| XLSX内部圧縮比 | 100 |

package pathは1〜240文字、NFC正規化済み相対POSIX pathに限定する。absolute path、空segment、`.`、`..`、backslash、colon、制御文字、segment末尾のspace/dot、Windows予約名を拒否する。NFC後またはcasefold後に衝突するentryも拒否する。

readerはさらにZip Slip、symlink、duplicate、encrypted/unsupported compression、undeclared entry、hash/size/MIME/signature不一致、壊れたJSON/PNG/XLSX、XLSX external link/hidden data、package内部参照切れを拒否する。

## 17. Drive release、draft、metadata境界

正式版releaseは`publisherId + packageId + version`ごとにimmutableである。同一identity/version・同一digestは同一releaseとして扱い、異なるdigestは上書きしない。

作業版draftは`draftId`と増加するrevisionを持つ更新可能な同期対象である。更新時はlocalに保存したDrive `headRevisionId`とETagを条件として使用し、local revisionまたはremote revisionが基準から変化していれば競合として再取得を要求する。

次の値はlocal/user sync stateだけに保存し、package内部へ含めない。

- Drive file ID、folder ID、Drive version、head revision ID、ETag
- Spreadsheet ID、Google account identifier、owner key
- PC固有path、local template/version ID

workspace backup同期とtemplate単体同期は、Drive folder root、API、UI、local stateを共有しない。

## 18. round-trip semantic equivalence

round-tripの成功条件はZIP byte列やpackage SHA-256の一致ではない。`TemplatePackageReader`で双方を読み、次を項目別に比較する。

1. Canonical Template Model
2. Canonical quality
3. Canonical output layout
4. asset roleからSHA-256へのmapping
5. content digest

`exportedAt`、ZIP timestamp、圧縮結果によるbyte差は正常である。上記の意味要素に差がある場合は実装不備または仕様不足として扱い、理由なく無視しない。

Phase 6ではgeneric/spreadsheet 1.1.0について、環境AのDrive正式版と空環境BでImport・有効化後に再Exportしたpackageが、上記5項目すべてで一致した。

## 19. compatibilityとtrust

future format/schema、未知required feature、minimum app version不一致はImport前に拒否する。pre-releaseと正式版はSemVer規則で比較し、古いrelease、同一release、newer release、version conflictを区別する。有効化は別途optimistic conflictを検査する。

Format v1は電子署名を持たない。`publisherId`は自己申告であり、file/Driveのどちらから取得してもtrust statusは`UNVERIFIED`である。UIは「配布者未確認」と表示し、利用者の確認なしに信頼昇格しない。

電子署名、publisher verification、公開配布はPhase 7以降の対象であり、Format v1 Phase 6の実装には含めない。
