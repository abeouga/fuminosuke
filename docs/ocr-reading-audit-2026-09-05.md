# OCR読み取りロジック監査（2026-09-05）

調査開始：2026-09-05。最終検証・報告：2026-09-06（JST）。

## Phase A — production変更前の調査

実行モデルはこのタスクのturn_contextで `gpt-6-astra / high` を確認。
AGENTS.md、docs/ocr-safety-contract.md、現在のarchitecture、ocr_contractの6ファイルを読了。
既存fixtureを使ったoffline再現であり、実帳票の誤読頻度を測定したものではない。

### 開始時の基準

- branch: main / HEAD: 19f64392125cc01f7707e5d02241fe8e00e2b008
- 既存tracked差分: 14ファイル、533 insertions / 874 deletions。
- 既存untracked: execution/preparer/finalizer、contract tests、verifier、複数のテスト作業ディレクトリ。
- OCR contract: 167 passed、1 warning、55.95秒。
- backend: 514 passed、1 skipped、4 warnings、103.73秒。
- ReviewPage: 22 passed。
- git diff --check: 問題なし。既存差分は保護し、staging/commit等は行わない。

### 実call graph

```text
api/v1/routes/ocr.py:create_ocr_job
→ JobService.create_ocr_job（受付snapshot / upload保存 / queue投入）
→ JobRunner._worker → _execute → _begin
→ PipelineJobHandler.__call__
→ DocumentOcrExecutionService.execute
  → ImageExtractionService.process
    → _load_template_snapshot（保存済みhash・ID・サイズ検証）
    → ImageLoader.load（EXIF補正 / alpha白背景 / JPEG・PNG検証）
    → DocumentNormalizer.normalize
    → TemplateAligner.align（ORB / ECC、低品質は失敗）
    → HandwritingIsolator.isolate → create_variants
    → MarkDetector.detect_all → RegionCropper.crop
    → _write_artifacts
  → cancel確認
  → OcrWorkflowService.process
    → _create_provider → OcrRequestPreparer.prepare
    → optional PaddleTextPresenceGate.inspect（auditのみ、送信除外しない）
    → mappedCells artifact → cancel確認
    → OpenAIOcrProvider.extract
      → _batched_requests → _call / _call_detail
      → _compact_request_ids → association banner → build_mapped_input_content
      → structured response → association照合 → 元IDへ展開
      → missing/duplicateの安全側処理 → build_mapped_document
    → OcrResponseFinalizer.finalize
      → local ink conflict → fixed key → local/manual mark
      → generic data / mother ID → _apply_record_keys
      → normalize_document_numbers → DeterministicValidator.validate / merge_review_items
      → mappedCells response / diagnostics
    → mappedCells / diagnostics artifact
    → OcrResultRepository.save_provider_result
    → DocumentReconciliationService.resolve（explicit action時）
    → result artifact
→ JobRunner._finish（cancelled または REVIEW_REQUIRED、自動confirmではない）
```

副作用はworkflow/extraction/repository側、pure計算はpreparer/finalizer側に維持する。
DBやrevisionの変更、Google出力の変更は今回の修正対象外。

### Reading Decision Table（修正前）

| 条件 | 決定 | 根拠 / 制約 |
|---|---|---|
| disabled mapping / OCR用に構成されないlabel | 送信対象外 | TemplateConfig.mapped_ocr_regions / enabled |
| 通常のenabled text + crop | provider | そのfield単体のink有無だけでは除外しない |
| fixed calving parityがsnapshotにある | local固定値 | _fixed_calving_record_keys、画像ID/bboxは同じ |
| saleCalves/calvingsで最終ink行より後 | trailing blank扱い・非送信 | _trailing_blank_rows、全行blankなら絞らない |
| 中間blank / 先頭blank | provider | 最終active行以下は保持、row番号は詰めない |
| generic customFields/records | provider | 上記固定collection向けtrailing判定の対象外 |
| manual mark | manual review | MANUAL_CHOICE_REQUIRED、providerへ送らない |
| automatic markでlocal readable/blank | local | singleで複数検出ならuncertain、blankは空欄 |
| automatic markでlocal uncertain + crop | provider | mark_choice候補を付与、local結果で上書きしない |
| crop不足 | providerへ送れない | textはskip、markはlocal uncertain。通常crop生成は一括成功/失敗 |
| printedLabelTextあり | handwriting画像を送る | 印刷文字除外。元のactual crop.bboxを維持 |
| Paddleが文字なしと判定 | 送信を止めない | presence結果は診断専用 |
| explicit full-document flag + OpenAI | 全体画像をcontextとして追加 | batchにも引き継ぐ。field-only回答を要求 |

### Confirmed Issues

| ID / severity | file / function | 再現入力 → 現在結果 → 期待結果 | root cause | 既存test / リスク |
|---|---|---|---|---|
| R1 / High | ocr/deterministic_mapper.py:_reading | 候補「該当/非該当」、正しいraw「非該当」→「該当・非該当」readable → 非該当のみ | candidate in rawという部分文字列判定 | 正常markのテストでは未検出。ラベル区切り・重複の安全側処理を要検証 |
| R2 / High | domain/numeric_values.py:_normalize_birth_date / _normalize_sale_note | uncertain + MODEL_UNCERTAINの有効日付/♂出荷 → readable、理由消失 → uncertainと理由を保持 | 形式変換成功を認識確度の確定と混同 | readable日付/不正日付fixtureでは未検出。正常変換とalternatives保持を要検証 |
| R3 / High | services/ocr_response_finalization_service.py:finalize | singleのmarkへproviderがYes・Noを返す → readable → 要確認 | localではsingleを検証するがprovider経路では検証しない | Phase5A/Bのsingle fixtureは正常Yesのみ。multiple/manual/localを要検証 |
| R4 / High | services/ocr_request_preparation_service.py:_trailing_blank_rows | record groupにparity fieldと空のrecordKeyValueを明示。row0にink、row2産次のみink → row1/2非送信 → row2と中間row保持 | parityを固定印刷と無条件にみなす | 既存testは固定印刷のみ。手書きkeyを示すsnapshot付きのケースが不足 |

R4は当初、固定印刷という既存testの前提と衝突すると判断し保留した。
追加のread-only確認で、LayoutService._require_verified_record_structureは「固定の産次キーを入力するか、産次セルを意味づけするか」の両方を明示的に許可していると判明。
JobServiceはこのrecord groupをsnapshotへ保存する。したがって新仕様は不要で、明示的な手書きkey情報をpreparerが使っていない問題として認定した。
固定印刷・metadataなしの従来動作を維持し、snapshotが手書き産次を示す場合だけactive-row根拠に含める。
実帳票での発生頻度は未測定。

### A–Lの監査範囲と未実証の懸念

| 範囲 | 確認した処理 / 判定 |
|---|---|
| A preprocessing | normalize/registrationの品質gate、isolationの印刷保護・morphology、mark差分率、crop範囲チェック、PNG encodingを追跡。synthetic pipelineはbaseline成功。薄い線・印刷上の文字除去の実帳票誤読は未実証 |
| B selection | 上表およびR4。4暗画素閾値は経験則。先頭/中間blankを詰めるロジックは見つからない |
| C identity/crop | RegionCropper.bboxをrequest/reading/previewが共有。preparerは画像の種別のみ選択。座標変更の必要性は未実証 |
| D candidates | template_candidates/sire_namesはNFKC・空白・casefoldと許可suffixの一致、未知値はrawを保持してuncertain。候補外を意味推測で別fieldへ移さない。R1はmark候補の過剰一致 |
| E full document | snapshot flagで明示ON時のみ。実modelの改善/悪化は未測定。mappedCellsのwholeDocumentSentToOpenAiは常にfalseで診断上の差異あり。認識害未実証なので変更しない |
| F construction | 通常batchとidentity専用batch、compact IDはbatch内の元ID辞書で展開。contextは各batchへ。retryはbudget/cancelと対象IDを維持。画像hash/manifestは既存contractで固定 |
| G response | missingはuncertain、unknownは対象外、conflicting duplicateは安全側、association mismatchは値を破棄。wireはr/b/uの3状態で独立unreadable/confidence値はない。alternativesはdomainにあるがcompact wireにない |
| H finalizer | local ink + provider blankはLOCAL_INK_OCR_BLANK、固定key/手動mother IDは既存仕様。mappedCellsは最終documentのpath lookup。R2/R3を確認 |
| I normalization | 数値はREADABLEのみ、桁先頭0は文字列に維持。カンマ・金額・小数は別規則。日付は桁数から元号を推定する既存仕様で、明示元号との曖昧さは残る。R2を確認 |
| J validation | configured_pathsを限定、数値syntaxはerror/rangeはwarning、産次重複error/順序warning、低registration error。任意generic fieldへ牛台帳規則は適用しない。閾値false-positiveは実帳票未実証 |
| K repeated rows | dictによるID対応、固定row index、dittoは直上readableに限定しphysical identityは元位置。長文/blank/後続値/同一labelは追加offline検証対象。R4を除き既存fixture上の行詰めなし |
| L marks | confident localならOpenAIへ送らないので「local Yes/OpenAI No」の同一field競合は通常起きない。local uncertainのみproviderへ。manualは常にreview。R1/R3を確認 |

Known Gap（correction sourceRegionId/boundingBoxをfrozen mappingから再検証しない）は別問題として変更しない。
実API、既存DBへの書込み、実帳票のOCR実行は行わず、fixtureとtest DBを使用する。

## Phase B/C — 実施結果

### 1. 開始時Git状態

上記baselineのとおり。既存の変更ファイルは以下の14件で、今回の監査の変更と混同しない。

```text
backend/app/core/lifecycle.py
backend/app/jobs/handlers.py
backend/app/services/layout_service.py
backend/app/services/ocr_workflow_service.py
backend/app/services/spreadsheet_template_output_service.py
backend/tests/integration/test_ocr_review_api.py
backend/tests/unit/test_mark_detector.py
backend/tests/unit/test_ocr_workflow_optimization.py
backend/tests/unit/test_record_structure.py
backend/tests/unit/test_spreadsheet_template_output_contract.py
docs/ocr-safety-contract.md
frontend/src/components/LayoutEditor.test.tsx
frontend/src/components/LayoutEditor.tsx
frontend/src/pages/ReviewPage.test.tsx
```

これらの既存差分には今回追加変更していない。finalizerは開始時からuntrackedの既存ファイルであり、今回新設したものではない。

### 2. baseline test結果

| 対象 | 変更前実測 |
|---|---:|
| scripts/verify_ocr_contract.py | 167 passed |
| backend全体 | 514 passed / 1 skipped |
| ReviewPage | 22 passed |

添付文書の155/502という過去値はbaselineに使用していない。

### 3. 調査したOCR reading pipeline

Phase Aの実call graph参照。HTTP受付、queue、実行境界、画像前処理、request/response計算、DB/artifact保存を追跡した。
新規テストのDB検証は既存extraction fixtureからworkflowを実行する範囲であり、実カメラ入力からの新規E2E計測ではない。
実画像前処理は既存synthetic pipelineとPhase4 contractによる検証。

### 4. 評価した主要ロジック

Phase AのReading Decision TableとA–L表参照。
追加で4行×2列、400文字の長文、同一表示ラベル、中間blank、後続uncertain、alternatives/reviewReason、逆順response、末尾blankの複合ケースを検証。
「未知の候補」「blank」「形式は正しいuncertain」は別状態として確認した。

### 5. Confirmed Issue一覧

| ID | 判定 | 結果 |
|---|---|---|
| R1 | High：mark候補の部分一致による誤った選択値生成 | 修正済み |
| R2 | High：日付/備考の正規化によるuncertain解除 | 修正済み |
| R3 | High：provider経路でsingle mark制約を無視 | 修正済み |
| R4 | High：snapshotで手書きkeyと定義された産次だけの行の送信欠落 | 修正済み。固定印刷の既存expectedは変更なし |

R4を含め、実帳票での発生頻度は未測定。R1–R3の原因となる処理は開始時HEADのmapper/normalizer/旧workflowにも存在する。
今回の責務分離リファクタリングで初めて生じた問題とする根拠はない。

### 6. 根拠不足で修正しなかった疑わしい箇所

- handwriting isolationの印刷保護・小成分除去による薄い線の消失、printed-label exclusionによる入力情報欠落。
- registrationの閾値、crop paddingの妥当性。実帳票の文字領域との正解比較をしていない。
- full documentが有益か有害か、batch単位のcontext不足、モデルの文字認識精度。
- 数字の桁数による元号推定と明示元号の扱い。既存仕様の期待値を推測で変えていない。
- mark差分面積による文字/チェック誤判定。実帳票の正解付きケースが不足。
- wholeDocumentSentToOpenAi診断値の実送信との違い。認識結果への実害は未実証。

これらを「問題なし」と断定していない。fixture上で通る範囲と実認識品質の未検証範囲を区別する。

### 7. 各Issueの再現方法

repositoryのbackendディレクトリで以下を実行する。修正前の実行結果を先に記録し、同じassertionを維持した。

```powershell
.venv/Scripts/python.exe -m pytest tests/ocr_contract/test_reading_logic_audit.py -k r1
.venv/Scripts/python.exe -m pytest tests/ocr_contract/test_reading_logic_audit.py -k r2
.venv/Scripts/python.exe -m pytest tests/ocr_contract/test_reading_logic_audit.py -k r3
.venv/Scripts/python.exe -m pytest tests/ocr_contract/test_reading_logic_audit.py -k r4
```

| 対象 | 修正前 | 修正後 |
|---|---|---|
| R1：完全ラベル/複数ラベル/未知ラベル/blank | 4 failed / 3 passed | 7 passed |
| R2：3種類の日付と備考 × uncertain/readable | 4 failed / 4 passed | 8 passed |
| R3：single/multiple × 正常/複数/uncertain | 1 failed / 5 passed | 6 passed |
| R4：sale/calving × 手書きkey/固定key | 2 failed / 2 passed | 4 passed |

R4は既存Phase5Aの `_mapping` / `_crop` / `_extraction` を利用。
4行の価格欄（calvingでは日付欄）とrow2のparityを作り、row0の値とrow2産次だけにinkを付与。
recordStructureのrow2にrecordKeyField=parity / recordKeyValue=""を設定しても、修正前はrow0の値しか送らなかった。
修正後はrow0/1/2の値とrow2産次を送り、row3だけ末尾blankとして扱う。
recordKeyValue="3"なら同じinkでも固定キーとして従来どおり除外する対照ケースも成功。
既存test `test_trailing_blank_gate_keeps_leading_blanks_and_skips_only_after_last_ink` のfixture/期待値は改変していない。

### 8. 各Issueのroot cause

- R1：`candidate in raw` は候補ラベル全体の一致ではない。「非該当」に「該当」が含まれるため複数選択を作っていた。
- R2：日付/備考だけがBLANK以外を正規化し、成功時にstatus=READABLE / reviewReason=Noneを設定していた。
- R3：local markはselection_modeを検証する一方、provider markはfinalizerで無条件continueしていた。requestには候補ラベルはあるがsingle/multiple制約は渡っていない。
- R4：target末尾が.parityであれば固定印刷と仮定し、active-rowの根拠から無条件除外していた。

### 9. 追加したregression test

`backend/tests/ocr_contract/test_reading_logic_audit.py`：27ケース。

- R1 7ケース、R2 8ケース、R3 6ケース、R4 4ケース。
- sparse rowと長文・blank・後続uncertain・identity保持：1ケース。
- workflow → test DB → mappedCells/result artifact → revision=1の保持：1ケース。

既存testの削除/skip/assertion弱体化/fixture変更/Goldenハッシュ更新は0件。
追加テストファイルのみRuff formatterで整形し、既存ファイルの一括整形はしていない。

### 10. 修正内容

- R1：まずraw全体と候補を照合。それ以外は「・」「、」「,」「;」「改行」で区切られた完全ラベルだけを認める。未知の要素を含む場合はrawを保持してuncertainとし、既知部分だけを採用しない。正常な複数選択の出力順は既存の候補順を維持。
- R2：READABLE以外の日付/備考は正規化で変更しない。形式の妥当性で認識の不確実性を解除しない。
- R3：provider由来でsingleのREADABLE markが、1個の完全な許可ラベルと一致しなければnormalized=None / uncertain / MARK_CHOICE_UNRESOLVED。raw・alternatives・物理identityは保持。
- R4：recordStructureのsection/row/key情報から手書きparityのpathを導出し、そのcropのinkを最終active行の根拠に加える。固定値がある行とlegacy metadataなしの扱いは維持。

### 11. 修正したproduction files

| file | 今回の変更 |
|---|---|
| backend/app/ocr/deterministic_mapper.py | mark候補判定と小さなpure helper |
| backend/app/domain/numeric_values.py | 日付・備考のstatus guard、説明コメント |
| backend/app/services/ocr_response_finalization_service.py | provider single markの安全側検証 |
| backend/app/services/ocr_request_preparation_service.py | snapshotが示す手書きparityをactive-row判定へ伝える |

provider、prompt、schema、model、crop、DB schema、lifecycle、Google、frontendは今回未変更。
追加ファイルはテスト1件とこの報告書1件。既存refactoringのuntrackedファイルを作り直していない。

### 12. OCR requestへの影響

R4の条件では送信対象が増える。以前欠落した手書きkeyの行と、その手前の中間blank行が通常の順序で送られる。
batch/compact ID/tokenはそのrequestから既存algorithmで生成される。これらの生成規則、candidate metadata、画像detail、full document flagは未変更。
Phase5Aのrequestと画像ハッシュの期待値は変更していない。

### 13. OCR responseへの影響

wire formatとprovider通信処理は変更なし。
R1の誤った選択値とR2/R3の誤ったreadable判定を後処理で修正した。R4は未送信fieldを通常のprovider処理へ戻す。
対象外の値の入替え、空欄削除、位置の詰め直し、意味からのfield推測はしていない。

### 14. mapping / physical identityへの影響

変更なし。mappingId / openAiMappingId / targetPath / sourceRegionId / boundingBox、crop / fieldPreviewの対応を維持。
新規テストは元のsourceRegionId/bbox、mappedCellsのpathとresponseの対応もassertする。
値に関する修正を別の物理セルへ移動させない。

### 15. associationへの影響

algorithm、token生成/検証、compact ID展開は未変更。
既存のassociation mismatch / missing / duplicate / reordered responseの契約テストは成功。
live APIが0回なので実APIのmismatch・誤対応は観測対象0件。意図的不一致fixtureの総件数は未集計であり、実帳票の誤対応率0%を意味しない。

### 16. cropへの影響

変更なし。範囲、padding、actual bbox、aligned/handwriting画像、PNG encodingに変更なし。
synthetic/contractで座標と画像ハッシュを確認した。全実帳票のcropを目視・正解照合したわけではない。

### 17. normalizationへの影響

R2のみ変更。uncertainの値/判定理由/alternativesをそのまま保持。
READABLEの日付変換・備考正規化、数値桁区切り、小数、leading zeroの既存規則は維持。
日付書式が不正なREADABLEをuncertainにする既存動作は残している。

### 18. validationへの影響

DeterministicValidator自体は未変更。configured path、required、syntax/range、severityを緩和していない。
single markの検証不足はfieldの要確認状態へ安全側に落とす。revision、confirmation、review itemのownershipは維持。

### 19. repeating rowsへの影響

row構造の変更なし。追加4行×2列fixtureで、row0長文 / row1blank / row2uncertainの位置・metadataを維持し、row3の末尾blankだけ非送信になることを確認。
R4では既存snapshotに従って手書き産次のある行を保持するよう修正。固定印刷のtrailing gatingは維持した。

### 20. marksへの影響

R1/R3のみ変更。許可ラベルの部分一致を廃止し、single provider出力を検証。
local detectorの閾値、confident localの優先、manualのMANUAL_CHOICE_REQUIRED、正常multiple選択は維持。
同じfieldへlocalとproviderを二重に実行する経路は追加していない。

### 21. live OpenAI API利用

0 calls / 上限30 calls。実providerでしか再現できない問題を今回の修正対象にしていないため未使用。
モデル/プロンプト比較実験なし。全再現がoffline fixture / synthetic input / mock responseで成立した。
したがって実APIのmodel・token usage・文字認識誤りの測定記録は該当なし。
test DB以外のDB書込みや、Google API呼び出しも行っていない。

### 22. Safety Contract

各production修正後に同じverifierを実行した。

| 段階 | 結果 |
|---|---:|
| baseline | 167 passed |
| R1修正後 | 174 passed |
| R2修正後 | 182 passed |
| R3修正後 | 188 passed |
| 行対応・永続化検証を追加 | 190 passed / 1 warning / 70.42秒 |
| R4修正後の最終 | 194 passed / 1 warning / 39.44秒 |

Phase5A/5Bの既存request画像・document・validation・mappedCells・diagnostics・result artifactのcanonical SHA256 expectedはすべてそのまま成功。
Golden相当の既存result差分なし。ただしこれは固定された合成fixtureの結果で、網羅的な実OCR Golden corpusではない。
**既存OCR contractを維持した。** 意図した不具合条件の値/確認状態は修正している。

### 23. backend full test

backendで `.venv/Scripts/python.exe -m pytest --basetemp=.pt-reading-audit-final-r4` を実行。
R4前：537 passed / 1 skipped / 4 warnings、111.96秒。
最終：541 passed / 1 skipped / 4 warnings、113.58秒。baselineから27件増加、skipの追加なし。

### 24. frontend test

frontendで `npm test -- --run src/pages/ReviewPage.test.tsx`。
結果：22 passed。baselineから件数・既存期待値の変更なし。
今回はfrontend全体の再実行、build、実ブラウザ操作はしていない。

### 25. Ruff / mypy

- mypy：変更production 4ファイルを通常のimport追跡で検証し成功（追加抑制なし）。
- Ruff：変更Python 5ファイルを検証。numeric_values.pyの既存RUF001が10件残るためコマンド全体はexit 1。
- 同ファイルのHEAD内容をstdinでRuffに渡し、同じ行・同じ10件を確認。今回追加されたlint違反は0件。
- mapper / finalizer / 新規testの3ファイルに限定したRuffは成功。R4のpreparerにも新しいlint違反はない。
- 複数種類のハイフンを扱う既存数値正規化をlint回避目的で書き換えていない。

### 26. git diff --check

最終確認で問題なし。staging / commit / push / reset / restore / checkout / stashは未実行。
tracked差分全体は16ファイル、550 insertions / 877 deletions（既存差分を含む）。
今回のtracked増分はmapper 14追加/1削除、normalizer 3追加/2削除。既存untracked preparer/finalizerへの変更と新規test/報告書は通常のgit diff --statに含まれない。
テストが作成した `.pt-*` ディレクトリは残しており、既存の作業ディレクトリも削除していない。

### 27. 未解決Issue

認定した4件の再現条件に未修正はない。ただし以下は未検証範囲として残る。

- legacy snapshotにrecord groupがない場合は、固定印刷と手書き産次を識別する根拠がない。従来の固定印刷扱いを維持し、推測で書き換えていない。
- 実帳票のcrop品質、薄い筆跡、candidate外の正しい値、full-documentの認識品質比較は未実証。
- これらを含む全入力について、全問題解決済みとするものではない。

### 28. Known Gap

correction payloadのsourceRegionId / boundingBoxをbackendがfrozen mappingから再導出・再検証しない既知課題は未変更。
今回の読取ロジック4件とは分離し、修正範囲に含めていない。

### 29. 今後さらに精度改善するなら次に調査すべき点

1. legacyの保存snapshotでrecord group metadataが欠けるケースを調査。固定印刷と手書きを識別できないものは、テンプレートの再確認を含めて扱いを決める。
2. 正解付き実帳票を用意し、印刷と筆跡が重なる領域・薄い線・セル端をaligned/handwriting/crop画像で比較。文字誤りとidentity誤対応は別に集計。
3. その後に必要な場合だけ、同一production model/promptでfull-document有無や読み取り困難cropの最小live smoke testを行う。推測だけで送信設定を変更しない。

## 最終判定

**実害条件をofflineで再現した問題は4件。そのうち4件を修正。既存testのfixture/expectedは変更していない。**

**既存のOCR identity / association / revision contractを変更したか：No。**

既存contractとbackend回帰に加え、実帳票での認識品質やlegacy snapshotの未測定範囲まで保証するものではない。OCR安全性を完全には検証できていない範囲は上記のとおり。
