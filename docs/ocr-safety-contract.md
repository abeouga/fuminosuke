# OCR Safety Contract

## 1. この文書の目的

この文書は、本リポジトリのOCR処理を変更する際に維持しなければならない不変条件、検証方法、Golden fixture、回帰判定基準を定義する。

このシステムでは、OCRの正しさを単純な文字認識精度だけで評価してはいけない。

OCRの正しさには、以下の対応関係が維持されていることも含まれる。

```text
document image
↓
crop / physical region
↓
physical cell identity
↓
mapping identity
↓
provider request
↓
provider response
↓
association validation
↓
compact ID expansion
↓
mappedCells
↓
reviewStructure
↓
ReviewPage
↓
correction persistence
```

文字列が正しくても、この経路上で別セルへ誤対応している場合はOCR回帰である。

逆に、文字列そのものを誤認識していても、正しい物理セルへ正しく対応している場合は「文字認識精度の問題」であり、「mapping / association破壊」とは分離して扱う。

---

## 2. 最重要原則

### 2.1 推測で対応関係を修正しない

OCR mappingの不整合が発生した場合、以下のような方法で帳尻を合わせてはいけない。

- 空欄結果を削除する
- 後続値を前へずらす
- 後続値を後ろへずらす
- 配列indexだけで再割り当てする
- 前後セルの内容から対象fieldを推測する
- 文字列の意味からtargetPathを推測する
- sourceRegionIdを値側へ合わせる
- boundingBoxを値側へ合わせる
- cropを値側へ移動する
- association mismatchを無視して値を保存する

見た目上正しくなっても、provenanceを破壊するため禁止する。

---

### 2.2 不確かな値は安全側へ失敗させる

画像とprovider responseの対応が証明できない場合、その値を別fieldへ割り当ててはいけない。

既存のassociation mismatch処理を維持し、誤対応の可能性がある値は確認対象へ送る。

---

### 2.3 OCR内部のidentityをUI都合で変更しない

以下のようなUI変更のためにOCR coreを変更してはいけない。

- 表示レイアウト
- CSS
- ポップオーバー
- ボタン
- 表示名
- グループ表示
- frontend-only interaction

UI変更でOCR core変更が必要に見える場合は、まず既存OCR contractを維持した設計が可能か確認する。

---

## 3. OCR pipeline boundaries

OCR処理を以下の境界に分離して検証する。

---

### Stage A: Physical region / crop generation

#### Input

- document image
- template layout
- cell / region definition

#### Output

- crop image
- `boundingBox`
- `sourceRegionId`
- その他物理領域identity

#### 不変条件

生成されたcropは、必ず意図した帳票上の物理領域を表すこと。

以下の対応が決定的であること。

```text
template cell
↕
sourceRegionId
↕
boundingBox
↕
crop
```

#### 回帰例

- cropが隣のセルを指す
- boundingBoxだけ別セルの座標になる
- sourceRegionIdとcropが一致しない

---

## 4. Stage B: Provider request manifest

OpenAI等のproviderへ送信する前に、各画像とmapping情報の対応が確定していなければならない。

最低限、以下を追跡可能にする。

- batch number
- request order
- compact ID
- original `mappingId`
- `targetPath`
- `sourceRegionId`
- original image SHA-256
- providerへ実際に渡す画像SHA-256
- association token
- model
- prompt version
- schema version

### 不変条件

以下が1対1で対応すること。

```text
provider input image
↕
association token
↕
compact ID
↕
original mappingId
↕
targetPath
↕
sourceRegionId
```

配列順序だけを対応根拠としてはいけない。

---

## 5. Stage C: Provider response association

provider responseは、そのまま信用してはいけない。

過去に実際に以下の異常が確認されている。

- ある画像の値を次のcompact IDへ返す
- `mappingId:cN`形式の不正ID
- duplicate ID
- 要求件数より少ないresponse items
- structured JSON途中終了
- 文字あり画像を空欄／不明として扱い、その値を後続fieldへ割り当てる

したがって、provider responseではassociation evidenceを検証する。

### 必須検証

- compact ID
- association token
- response item count
- duplicate ID
- unexpected ID
- missing ID
- schema validity

### 不変条件

association tokenが期待値と一致しない値を対象fieldへ保存してはいけない。

### mismatch時

既存の安全側動作を維持する。

例：

```text
OCR_ASSOCIATION_MISMATCH
```

誤った値を保存するより、unknown / review requiredへ落とすことを優先する。

---

## 6. Stage D: Compact ID expansion

compact IDから元mapping identityへ戻す処理は決定的でなければならない。

### 不変条件

展開処理によって以下が変化してはいけない。

- original `mappingId`
- `targetPath`
- `sourceRegionId`
- physical cell ownership

生responseの誤対応を展開処理側で推測補正してはいけない。

---

## 7. Stage E: mappedCells

`mappedCells`はprovider処理後の結果を保持する重要境界である。

### 不変条件

`mappedCells`生成時に以下を変更してはいけない。

- field ownership
- mapping identity
- physical cell identity

件数変化が発生する場合は、明確な仕様根拠が必要。

以下を無条件に行ってはいけない。

- blank itemの削除
- array compaction
- index shift
- neighboring value補完

---

## 8. Stage F: reviewStructure

ReviewPage向けに表示構造へ変換しても、各OCR fieldのidentityは維持する。

表示上のgroup化と内部identity統合を混同しない。

独立項目のgroup表示では、表示label文字列だけをidentityとして使用しない。

現在のgroup identity契約を維持する。

```text
parentLabelCellId + labelCellIds
```

必要なidentityが存在しない場合の既存fallbackも維持する。

---

## 9. Physical cell identity

以下は物理セル側のidentityとして扱う。

- `mappingId`
- `openAiMappingId`
- `targetPath`
- `sourceRegionId`
- `boundingBox`
- crop / `fieldPreview`
- template上のcell / region identity

これらは認識文字列そのもののidentityではない。

---

## 10. Recognition-result identity

以下は認識結果側に属する。

存在するものだけを対象とする。

- `raw`
- current value
- `normalized`
- `status`
- `alternatives`
- `reviewReason`
- warning
- review item情報
- その他認識結果に属するmetadata

レビュー操作で値を移動する場合は、これらを論理的一式として扱う。

---

## 11. Row movement contract

繰り返し表のレビュー行移動では、

**物理セルを移動するのではなく、認識結果を別の物理行へ割り当て直す。**

したがって、

### 行位置側へ固定

- `mappingId`
- `openAiMappingId`
- `targetPath`
- `sourceRegionId`
- `boundingBox`
- crop / `fieldPreview`
- row / cell structural identity

### 認識結果と一緒に移動

- `raw`
- current value
- `normalized`
- `status`
- `alternatives`
- `reviewReason`
- warning
- その他FieldReading内の認識結果

### 禁止

行オブジェクト全体を単純swapし、物理identityまで移動させること。

---

## 12. Sparse row contract

汎用繰り返し表で、隣接する行同士に安全な全列対応が存在しない場合は、部分交換してはいけない。

例：

```text
列A | 列B | 列C

行1: field | field | field
行2: field | 欠落  | field
```

この場合、行移動はdisabledとする。

値が空文字であるだけのセルと、構造的にfield自体が存在しない状態を区別する。

### 禁止

- 存在する列だけswap
- 欠落列を無視
- 仮の空fieldを生成
- mappingを自動補完

安全な全列交換が成立しない場合は操作を拒否する。

---

## 13. Prompt / Model / SchemaもOCR contractの一部

以下はコード変更でなくてもOCR-sensitive変更として扱う。

- model変更
- prompt変更
- prompt version変更
- structured output schema変更
- association schema変更
- image detail変更
- image preprocessing変更
- batch size戦略変更
- provider SDK利用方式変更

変更時は必ずOCR contract verificationを行う。

---

## 14. Version information

OCRジョブについて、可能な範囲で以下を記録する。

```json
{
  "provider": "openai",
  "model": "...",
  "promptVersion": "...",
  "schemaVersion": "...",
  "associationVersion": "..."
}
```

「コード変更なしで結果が変わった」場合でも、provider / model / prompt / schema差分を追える状態にする。

---

## 15. Diagnostics contract

既存の`openAiMappingDiagnostics`は原因追跡に重要である。

明示的な置き換え設計がない限り削除しない。

最低限追跡できること：

- call index
- batch
- request order
- compact ID
- original mapping ID
- targetPath
- sourceRegionId
- image SHA-256
- association token
- raw response item
- expanded item
- mappedCells result
- request ID
- model

diagnosticsの削除・縮小によって原因追跡能力を下げない。

---

## 16. Golden fixtures

### 16.1 目的

Golden fixtureは、現在正常であることが確認済みのOCR処理を固定し、将来の変更で壊れていないか比較するために使用する。

Golden testは、可能な限り実OpenAI APIを呼ばずに再現可能にする。

---

### 16.2 推奨ディレクトリ構造

Golden基盤を実装する場合、例として以下を使用する。

```text
backend/tests/ocr_contract/
├─ fixtures/
│  ├─ independent_fields/
│  │  ├─ layout.json
│  │  ├─ provider_response.json
│  │  ├─ request_manifest.json
│  │  ├─ expected_mapped_cells.json
│  │  └─ expected_review_structure.json
│  │
│  ├─ repeated_section/
│  │  ├─ layout.json
│  │  ├─ provider_response.json
│  │  ├─ request_manifest.json
│  │  ├─ expected_mapped_cells.json
│  │  └─ expected_review_structure.json
│  │
│  └─ association_mismatch/
│     ├─ layout.json
│     ├─ provider_response.json
│     ├─ request_manifest.json
│     └─ expected_result.json
│
├─ test_golden_ocr_replay.py
├─ test_ocr_identity_contract.py
└─ test_association_contract.py
```

これは推奨構造であり、現在存在しない場合は「既存」として扱わない。

---

## 17. Golden fixtureへ含めるケース

最低限、以下を用意する。

### Fixture A: normal independent fields

含める：

- 通常テキスト
- 数値
- 複数値を持つ論理項目
- 空欄

検証：

- mapping identity
- physical identity
- grouping

---

### Fixture B: repeated section

含める：

- 3行以上
- 複数列
- status
- alternatives
- reviewReason
- warning

検証：

- row movement
- physical identity固定
- persistence

---

### Fixture C: association mismatch

意図的に、

```text
expected token != response token
```

となるfixtureを作る。

検証：

- 値を保存しない
- neighboring fieldへshiftしない
- mismatch状態へ落ちる

---

### Fixture D: long text / blank transition

過去に発生した、

```text
文字あり
↓
unknown
↓
後続値が1項目ずれる
↓
空欄で見かけ上復帰
```

のような系列を保存responseで再現できるfixtureとして固定する。

association検証によって誤値を通過させないことを確認する。

---

## 18. Golden expected result

Golden testでは文字列だけを比較してはいけない。

最低限以下を比較する。

```text
mappingId
openAiMappingId
targetPath
sourceRegionId
boundingBox
association result
raw
normalized
status
mappedCells structure
reviewStructure ownership
```

必要な項目のみfixtureごとに比較する。

---

## 19. Golden update rule

Golden expected fileは「現在の実装結果に合わせるため」だけに更新してはいけない。

Goldenを変更してよいのは、

- OCR contractを意図的に変更した
- その変更をユーザーが要求している
- 新しい期待仕様が明確
- regression testで新契約を証明した

場合のみ。

Golden更新時は完了報告に必ず理由を書く。

---

## 20. OCR-sensitive変更前の検証

OCR-sensitive変更前に最低限以下を行う。

```bash
git status
git diff --check
```

その後、現在存在するOCR関連テストを実行する。

### 現在実行可能な検証

リポジトリの実際の構成に合わせて実行する。

バックエンド全体の基準例（`backend/`を作業ディレクトリとして実行）：

```bash
python -m pytest
```

または、このリポジトリで通常使用している専用`--basetemp`付きコマンドを使用する。

Ruff基準：

```bash
python -m ruff check app tests
```

mypy基準：

```bash
python -m mypy app
```

実際のプロジェクト標準コマンドが異なる場合は、リポジトリで定義されたコマンドを優先する。

---

## 21. OCR専用検証script

将来的にOCR contract test群を整備したら、Codexから個別コマンドを記憶させるのではなく、専用scriptへ集約することを推奨する。

例：

```text
scripts/verify_ocr_contract.py
```

理想的には以下を1コマンドで実行する。

```text
OCR unit tests
↓
association tests
↓
Golden replay
↓
mapping identity tests
↓
mappedCells tests
↓
reviewStructure tests
↓
persistence tests
```

将来このscriptを実装した場合、`AGENTS.md`からもこのscriptを必須検証として参照する。

---

## 22. 推奨将来コマンド

OCR contract test基盤を実装した後は、例えば以下のように統一する。

```bash
python scripts/verify_ocr_contract.py
```

または、

```bash
pytest tests/ocr_contract -q
```

ただし、これらは実際にscript／directoryが作成されるまでは「存在するコマンド」として扱わない。

---

## 23. 変更前後比較

OCR-sensitive変更では、可能なら変更前と変更後で同じGolden fixtureを実行し差分を比較する。

理想的な出力例：

```text
mappingId changed: 0
targetPath changed: 0
sourceRegionId changed: 0
boundingBox changed: 0
association mismatch changed: 0

recognized text changed: 2
status changed: 1
```

この場合、文字認識改善による意図的差分なのかを判断できる。

以下のような結果は原則として重大回帰として扱う。

```text
sourceRegionId changed: 7
mappingId changed: 5
targetPath changed: 4
```

ユーザーが明示的にmapping contract変更を要求していない限り、作業を停止して原因を調査する。

---

## 24. 変更タイプ別の必須検証

### UI-only変更

例：

- CSS
- ポップオーバー
- ボタン
- layout

原則：

- OCR coreを変更しない
- OCR-sensitiveファイルへ差分がなければGolden API smoke testまでは不要

必要：

- frontend対象テスト
- 必要なintegration
- build
- `git diff --check`

---

### ReviewPage mapping変更

必要：

- field identity tests
- sourceRegionId tests
- targetPath tests
- crop tests
- persistence tests
- Golden replay

---

### OCR provider変更

必要：

- request manifest tests
- association tests
- schema tests
- Golden replay
- backend regression
- 必要に応じて実API smoke test

---

### Prompt / model変更

必要：

- Golden replay
- schema tests
- association tests
- 実API smoke testを原則検討する

---

### Crop / layout変更

必要：

- boundingBox
- sourceRegionId
- crop SHA / visual correspondence
- Golden fixture
- review display correspondence

---

## 25. Live OpenAI API smoke test

毎回使用する必要はない。

推奨順序：

```text
1. Unit tests
2. Golden replay
3. Integration tests
4. Backend regression
5. Live API smoke test
```

以下の場合にlive APIを検討する。

- provider request形式を変更した
- modelを変更した
- promptを変更した
- schemaを変更した
- association方式を変更した
- deterministic fixtureだけでは実動作を確認できない

---

## 26. Live APIで記録する内容

実APIを使用した場合は最低限、

- 呼び出し目的
- API calls
- image count
- model
- mismatch count
- mapping mismatch count
- schema error count
- recognition-only errors

を記録する。

「文字認識が間違った」と「別セルへ誤対応した」を混同しない。

---

## 27. Test protection

OCR contract testが失敗した場合、実装を通す目的だけで以下を行ってはいけない。

- test削除
- test skip
- assertion弱体化
- fixture削除
- expected JSONを現在結果へ合わせる
- mismatch testを削除
- Golden比較項目を減らす

まず実装がcontractを壊していないか確認する。

---

## 28. Intentional contract change

ユーザーが明示的にOCR contract変更を求めた場合のみ、不変条件を変更してよい。

その場合は必ず、

1. 旧contractを説明
2. 新contractを説明
3. この文書を更新
4. regression test更新
5. Golden fixture更新
6. 影響範囲確認
7. 必要ならmigration
8. 必要ならlive API
9. 完了報告

を行う。

---

## 29. Git protection

OCR-sensitive作業開始前後で以下を確認する。

```bash
git status
git diff --check
```

ユーザーから明示的に指示されていない限り、

- reset
- revert
- checkoutによる差分破棄
- commit
- push
- staging

を行わない。

既存の未コミット変更と今回変更を区別する。

---

## 30. OCR-sensitive変更の完了報告

最低限以下を報告する。

1. 変更したOCR-sensitiveファイル
2. 影響したpipeline stage
3. 影響したOCR invariant
4. mapping identity変更有無
5. physical identity変更有無
6. provider association変更有無
7. crop / boundingBox変更有無
8. prompt / model / schema変更有無
9. 変更前テスト結果
10. 変更後テスト結果
11. Golden replay結果
12. Golden差分
13. backend regression結果
14. live OpenAI API使用有無
15. API使用回数
16. mismatch件数
17. mapping誤対応件数
18. recognition-only error
19. `git diff --check`
20. 残る不確実性

OCR動作変更を意図していない場合は、

> 既存OCR contractを維持した。

と明記する。

---

## 31. 現在のknown-good baseline

Phase 0〜9で、以下の主要契約が実Chrome E2Eまで確認されている。

この節は文書作成時点の検証記録であり、将来の任意のrevisionや未コミット差分へ自動的に適用される保証ではない。OCR-sensitive変更の基準状態は、作業開始時のrevision、worktree、実行したテスト結果とともに別途記録する。

### OpenAI association

実OCRで、

- manifest
- raw items
- expanded items
- mappedCells

の対応を確認済み。

確認時にはassociation mismatch 0件、association誤対応0件だった。

---

### Physical identity

行移動前後で、

- `mappingId`
- `openAiMappingId`
- `targetPath`
- `sourceRegionId`
- `boundingBox`
- crop / `fieldPreview`

が物理セル側へ固定されることを確認済み。

---

### Recognition result movement

行移動では、

- raw
- normalized
- status
- alternatives
- reviewReason
- warning

が認識結果と一緒に移動することを確認済み。

---

### Persistence

ライブ保存と再読込後にも、

- 認識結果の移動
- physical identity固定
- displayName
- independent field edits
- status

が維持されることを確認済み。

---

### Sparse row safety

構造的にfieldが欠落している疎な汎用行では、行移動buttonがdisabledとなり、correction requestも発生せず、部分交換されないことを実Chromeで確認済み。

---

### Review UI

以下も実Chromeで確認済み。

- `displayName`
- 独立項目group表示
- 同名別項目分離
- セル直接編集
- 判定ポップオーバー
- 行移動
- 保存・再読込

これらの確認済みcontractを、今後の変更で理由なく弱めてはいけない。

---

## 32. 最終判断基準

### Excel出力の元結合範囲（2026-09-05修正）

OCR identity契約は変更しない。Excel出力についてのみ、従来の左上座標の
列挙に加え、元XLSXの結合範囲を左上セルで代表する取込形式を許可する。
元XLSXから解決した範囲の和集合が、重複・欠落のない矩形の場合に限る。
空白に見える領域でも未選択セルを推測で追加してはいけない。

範囲展開を利用する新規出力snapshotはschemaVersion 2とし、各結合の
sourceRangesを保存する。元XLSXのSHA-256、sheet、保存したpartsから
出力時に一致を再検証する。schemaVersion 1の既存jobは従来どおり解釈する。
parts、mappingId、targetPath、sourceRegionId、boundingBox、crop、
reviewStructure、補正保存およびprovider associationを変更しない。

出力コピーでは、選択した元結合範囲に完全一致する既存結合だけを解除して
再結合できる。従来拒否していた既存結合の包含を、この根拠がある場合に限って
許可する。部分交差、別結合との重複、数式消失、未承認の内部値消失は拒否する。
元XLSXは書き換えない。旧形式の重複拒否テストは維持し、新形式を別ケースで検証する。

この変更はExcel出力契約の拡張であり、OCR認識・物理identity契約の変更ではない。

OCR変更後に問うべきことは、

> 「文字が読めたか」

だけではない。

最低限、

```text
同じ画像が
↓
同じ物理セルへ対応し
↓
正しいmapping identityを維持し
↓
provider responseとのassociationが検証され
↓
正しいfieldへ保存され
↓
ReviewPageでも同じidentityを維持し
↓
保存・再読込後にも維持されているか
```

を確認する。

この対応関係を証明できない変更は、OCR安全性を完全に確認できたとは扱わない。

不明な場合は推測で補正せず、安全側へ失敗させる。
