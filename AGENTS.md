# Repository Agent Instructions

## OCR Safety — Critical

このリポジトリには、複雑なOCRパイプラインがあります。

OCRでは文字列そのものだけでなく、

- 帳票上の物理セル
- crop画像
- mapping
- OpenAIへの送信内容
- OpenAIからの応答
- association
- `mappedCells`
- ReviewPage
- 保存後の補正結果

の対応関係が重要です。

これらの対応関係を意図せず変更・破壊しないでください。

---

## OCR関連変更を行う前の必須手順

OCR入力、OCR出力、mapping、review結果、field identity、template layout、correction保存などに影響する可能性があるコードを変更する場合は、変更前に必ず以下を実施してください。

1. `docs/ocr-safety-contract.md` を最後まで読む
2. 今回の変更がOCRのどの不変条件へ影響する可能性があるか確認する
3. 変更前にOCR contract testsを実行し、基準結果を記録する
4. 必要最小限の変更だけを行う
5. 変更後に同じOCR contract testsを再実行する
6. OCR不変条件に回帰が発生している場合は、作業完了と報告しない

UI変更を容易にするためだけにOCRロジックを変更してはいけません。

ユーザーからOCR変更を明示的に要求されていないUI-onlyの作業では、OCR coreの動作を変更しないでください。

---

## OCR-sensitive areas

以下はOCR-sensitive領域として扱ってください。

- `backend/app/ocr/**`
- OCR provider implementation
- OCR request manifest生成
- OCR structured-output schema
- OCR prompt
- compact ID mapping / expansion
- association token生成・検証
- `ocr_workflow_service`
- `mappedCells`
- `reviewStructure`
- `FieldReading`
- `mappingId`
- `openAiMappingId`
- `targetPath`
- `sourceRegionId`
- `boundingBox`
- crop / `fieldPreview` 解決処理
- correction persistence
- OCR mapping生成に使用するtemplate layout構造

この一覧に含まれないファイルでも、上記データを生成・変換・保存する処理を変更する場合はOCR-sensitiveとして扱ってください。

---

## Critical OCR invariants

以下は単なる現在の実装詳細ではなく、OCRの不変条件です。

### 1. Mapping identity

`mappingId`、`targetPath`、`sourceRegionId`、`boundingBox`の対応関係は決定的でなければなりません。

理由なく別のfieldへ付け替えてはいけません。

---

### 2. Physical cell identity

帳票上の物理セルidentityをOCR値と混同しないでください。

以下は物理セル側のidentityとして扱います。

- `mappingId`
- `openAiMappingId`
- `targetPath`
- `sourceRegionId`
- `boundingBox`
- crop / `fieldPreview`
- template上のcell / region identity

レビュー操作などでOCR値を移動・交換する場合でも、物理位置まで一緒に移動させてはいけません。

---

### 3. Crop correspondence

crop / `fieldPreview` は常に意図した帳票上の物理領域を指さなければなりません。

値を入れ替えた結果としてcropまで別位置へ移動する実装は禁止します。

---

### 4. OpenAI association

OpenAIから返された値は、association検証を通過した場合だけ対象fieldへ受け入れてください。

以下だけを根拠に値を信用してはいけません。

- 配列位置
- 応答順序
- compact IDだけ
- 前後の文字内容
- 「この値ならこの項目だろう」という意味的推測

association evidenceが期待値と一致しない場合は、安全側へ失敗させてください。

誤った可能性がある値を別fieldへ保存してはいけません。

---

### 5. Association mismatch

association mismatchが発生した場合は、誤対応した値を保存しないでください。

既存の安全側の処理を維持してください。

association mismatchを無視したり、warningだけにして値を通過させたりしてはいけません。

---

### 6. 値ずれの帳尻合わせ禁止

OCR mappingのずれを修正するために以下を行ってはいけません。

- 空欄OCR結果を削除する
- 後続値を1つ前へずらす
- 後続値を1つ後ろへずらす
- 前後セルの値をコピーする
- 配列indexだけを使って再割り当てする
- 文字内容から対象fieldを推測する

見た目上正しくなっても、provenanceを破壊するため禁止します。

---

### 7. Review transformation

ReviewPage向けの表示変換やグループ化は、OCR-to-cell identityを変更してはいけません。

表示上まとめることと、内部field identityをまとめることを混同しないでください。

---

### 8. Review row movement

Review上で値を行移動する場合、認識結果に属するデータは一体として移動させます。

例：

- `raw`
- current value
- `normalized`
- `status`
- `alternatives`
- `reviewReason`
- warning / review item情報

一方で物理セルidentityは行位置側へ固定します。

認識文字列だけを移動し、判定やwarningを元位置へ残すことは禁止します。

---

### 9. Diagnostics

既存OCR diagnosticsは、明示的な置き換えを設計・実装・テストしない限り維持してください。

diagnosticsを削除して原因追跡能力を低下させないでください。

---

## Required verification

OCR-sensitive変更を行う場合は、

`docs/ocr-safety-contract.md`

に定義されたOCR contract verificationを必ず実行してください。

### 検証基盤がまだ存在しない場合

`scripts/verify_ocr_contract.py`、`backend/tests/ocr_contract/`、またはGolden replay基盤がまだ存在しない場合、それらを存在するコマンドや実行済み検証として扱ってはいけません。

その場合は、現在存在するOCR関連テストを変更前後で実行し、未実装の検証基盤と未検証範囲を完了報告に明記してください。将来これらが実装された後は、該当する専用検証を必須とします。

最低限、変更内容に応じて以下を確認してください。

- OCR unit tests
- association tests
- Golden replay tests
- mapping identity tests
- `mappedCells` tests
- `reviewStructure` tests
- persistence tests
- backend regression tests
- `git diff --check`

必要な検証を実行できなかった場合は、

「OCR安全性を完全には検証できていない」

ことを明示してください。

---

## Test protection

OCR contract tests、Golden tests、回帰テストを、実装を通す目的だけで以下のように変更してはいけません。

- 削除
- skip
- assertion弱体化
- fixtureを都合のよいデータへ変更
- expected resultを理由なく現在出力へ合わせる

テストが失敗した場合は、まず実装がOCR契約を破壊していないか確認してください。

---

## UI-only tasks

UIだけの変更では、原則としてOCR coreを変更しないでください。

例えば以下の変更は、通常OCR core変更を必要としません。

- CSS
- レイアウト
- ポップオーバー
- ボタン
- 表示名
- visual grouping
- accessibility
- frontend-only interaction

UI変更のためにOCR内部のmapping、provider、association、crop、schema等を変更しようとしている場合は、本当に必要か再確認してください。

OCR契約を維持した設計を優先してください。

---

## Intentional OCR contract changes

ユーザーが明示的にOCR仕様変更を要求し、その結果としてOCR不変条件やデータ契約を意図的に変更する場合は、以下を必須とします。

1. `docs/ocr-safety-contract.md` を更新する
2. 新しい契約を証明する回帰テストを追加または更新する
3. 変更前の契約と変更後の契約を明示する
4. 影響範囲を説明する
5. Golden fixtureへの影響を確認する
6. 必要に応じて実API smoke testを実施する
7. 完了報告でOCR contract変更であることを明記する

暗黙的にOCR contractを変更してはいけません。

---

## Prompt / Model / Schema changes

以下の変更もOCRロジック変更として扱ってください。

- OpenAI model変更
- OCR prompt変更
- prompt version変更
- structured-output schema変更
- association schema変更
- provider request形式変更
- image preprocessing変更

コードロジックを変更していなくてもOCR結果やmapping契約へ影響する可能性があります。

必ずOCR-sensitive変更として検証してください。

---

## Live API usage

OCR contract検証では、可能な限り保存済みprovider responseやGolden fixtureを使用してください。

毎回OpenAI APIを呼び出す必要はありません。

推奨順序：

1. unit tests
2. Golden replay
3. integration tests
4. backend regression tests
5. 必要な場合のみ実OpenAI API smoke test

実APIを使用する場合は、以下を記録してください。

- 呼び出し目的
- API呼び出し回数
- association mismatch件数
- association誤対応件数
- 単純な文字認識誤り

文字認識誤りとmapping / association誤対応を混同しないでください。

---

## Git protection

OCR-sensitive作業では、既存の未コミット変更を保護してください。

明示的な指示がない限り以下を行わないでください。

- `git reset`
- `git revert`
- checkoutによる既存変更破棄
- commit
- push
- staging
- unrelated formatting
- unrelated refactor

変更前後で`git status`と必要なdiffを確認してください。

---

## Completion report requirements

OCR-sensitive変更を行った場合、完了報告には最低限以下を含めてください。

1. 変更したOCR-sensitiveファイル
2. 影響したOCR invariant
3. physical identityを変更したか
4. mapping identityを変更したか
5. provider associationを変更したか
6. crop / boundingBox契約を変更したか
7. 実行したOCR contract tests
8. Golden resultに差分があったか
9. backend regression結果
10. 実OpenAI APIを使用したか
11. API使用回数
12. association mismatch件数
13. 残っている不確実性

OCR動作を変更する意図がなかった場合は、

「既存OCR contractを維持した」

ことを明記してください。

---

## 最重要原則

OCRの正しさを、

「画面上の文字がそれらしく見える」

ことだけで判断してはいけません。

このシステムでは、

`document image`
→ `crop`
→ physical cell
→ mapping identity
→ provider request
→ provider response
→ association
→ expanded result
→ `mappedCells`
→ `ReviewPage`
→ persisted correction

の対応関係が維持されて初めてOCRが正しいと判断します。

文字列が正しくてもidentityが壊れていればOCR回帰です。

不確かな場合は推測で補正せず、安全側へ失敗させてください。
