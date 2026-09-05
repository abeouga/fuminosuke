# OCR Safety Contract

## 1. 目的と適用範囲

この文書はOCRシステムの技術的契約の正本である。作業の優先順位・許可範囲・Git保護は [AGENTS.md](../AGENTS.md) に置き、詳細仕様を二重管理しない。

OCRはデフォルトで保護するが永久凍結ではない。明示された改善は再現根拠・比較・回帰検証を条件に実施できる（§10）。

正しさは文字列だけでなく、次の対応関係で判定する。

```text
document image → crop / physical region → physical cell identity
→ mapping identity → provider request → provider response
→ association validation → compact ID expansion → mappedCells
→ reviewStructure → ReviewPage → correction persistence → output cell
```

これは検証境界の列でありartifact書込み順ではない。文字列が正しくても別セルへの誤対応はOCR回帰である。文字認識誤りとmapping / association誤りを分けて評価する。

## 2. 現在の実装境界と保護対象

以下は2026-09-06に照合した入口である。表のパスは `backend/app/` 基準（frontendのみリポジトリroot基準）。ファイル名だけでなく、対象データを生成・変換・保存する処理を保護する。

| 境界 | file / class・function | 保護する挙動 |
| --- | --- | --- |
| 受付・snapshot・retry | `services/job_service.py` — `JobService.create_ocr_job` / `retry` | 入力・templateSnapshot・fieldMappings・出力snapshot、冪等性 |
| job実行 | `jobs/runner.py` — `JobRunner`、`jobs/handlers.py`、`services/document_ocr_execution_service.py` — `DocumentOcrExecutionService.execute` | queue、attempt、progress、cancel / retry |
| template・rectangle / region検出 | `services/template_service.py` — `TemplatePreparationService.process`、`services/layout_service.py` — `LayoutService.detect` / `save`、`layout_analysis/detector.py`、`segmentation/`、`template_recovery/` | layout、cell / region、ラベルと入力セルの対応 |
| 画像抽出・crop | `services/extraction_service.py` — `ImageExtractionService.process`、`extraction/region_cropper.py` — `RegionCropper.crop`、`image_processing/`、`extraction/` | alignment、前処理、padding込みcrop.bbox、fieldPreview |
| orchestration | `services/ocr_workflow_service.py` — `OcrWorkflowService.process` / `_create_provider` | provider生成・呼出し、DB・artifact保存、reconciliation |
| request計算 | `services/ocr_request_preparation_service.py` — `OcrRequestPreparer.prepare` | mapping選択、region入力、request、初期mappedCells |
| provider / association | `ocr/openai_provider.py` — `OpenAIOcrProvider`、`_compact_request_ids`、`_build_request_manifest`、`_association_tokens`、`_expand_compact_item` | 送信画像、compact ID、token、応答検証・展開 |
| model / prompt / schema | `core/config.py`、`ocr/prompt_loader.py`、`ocr/transcription_models.py`、`ocr/schema_loader.py`、`backend/resources/templates/cow-breeding-ledger-v1/`（root基準） | primary / fallback設定、prompt、wire / 内部schema。OpenAI転記はload_transcription_promptとcompact modelを使用 |
| response計算 | `ocr/deterministic_mapper.py` — `build_mapped_document`、`services/ocr_response_finalization_service.py` — `OcrResponseFinalizer.finalize` | field ownership、値・判定・警告、mappedCells、diagnostics |
| reviewStructure・表示 | `services/review_display_structure.py` — `build_review_display_structure`、`frontend/src/pages/ReviewPage.tsx` | 構造由来group、保存mapping、行移動 |
| 補正・確定・reopen | `domain/ocr_models.py`、`services/ocr_result_service.py` — `OcrResultService.correct` / `confirm` / `reopen`、`repositories/ocr_result_repository.py` | 原候補・補正・revision履歴・確定snapshot |
| 再読取 | `services/document_reconciliation_service.py` — `DocumentReconciliationService.resolve` / `mark_confirmed` | RESCAN_PAGE、対象page / job、merge・履歴 |
| Spreadsheet / Drive | `services/spreadsheet_template_output_service.py` — `SpreadsheetTemplateOutputService.snapshot` / `render`、`services/google_sheets_sync_service.py`、`services/google_drive_service.py`、`services/output_layout_template_service.py`、`services/excel_export_service.py` | 出力セル対応、snapshot、確定revision、同期・再試行 |

`backend/app/ocr/**` と上表のデータ契約はOCR-sensitiveである。Reactやテンプレート編集でも、見た目だけか、保存layout / mapping / cell associationを変えるかで分類する。

主要経路は `JobRunner → PipelineJobHandler → DocumentOcrExecutionService.execute → ImageExtractionService.process → OcrWorkflowService.process`。request/response計算へHTTP・DB・Google・filesystem責務を持ち込まない。依存テストは直接importと特定のfilesystem呼出しの検査であり、推移的依存の完全分離は保証しない。

Quest / MRや非HTTP入口でも既存の受付・snapshot・job lifecycleを利用し、revision・cancel・retry管理を迂回しない。

## 3. Physical identity・mapping・crop

### 3.1 物理領域と認識結果の所有権

以下は物理セル側のidentityであり、認識文字列そのものではない。

- `mappingId`、`openAiMappingId`、`targetPath`、`sourceRegionId`
- `boundingBox`、crop / `fieldPreview`
- template上のcell / region、row / cell structural identity

同じ受付snapshot・入力に対する対応は決定的であること。理由なく別fieldへ付け替えず、値を移動・交換しても物理セルidentityは位置側に固定する。

### 3.2 Crop correspondence

`template cell ↔ sourceRegionId ↔ boundingBox ↔ crop` が一致し、意図した帳票領域を指すこと。`RegionCropper.crop` はpadding込みの実領域を `RegionCrops.bbox` と画像群に保持する。crop境界・canonical座標・alignment・画像SHAを対応させ、UIだけのscale / offsetで誤対応を隠さない。

値交換に伴ってcropを別位置へ移す、boundingBoxだけ隣セルへ変更する、sourceRegionIdとpreviewを不一致にする実装は禁止する。

### 3.3 推測補正の禁止

mappingのずれの帳尻合わせとして、空欄OCR結果の削除、array compaction、後続値の前後シフト、隣セルのコピー、配列index・応答順・文字内容だけによる再割当てを行わない。sourceRegionId・boundingBox・cropを値に合わせて変更しない。不確かな対応は安全側へ失敗させる。

reconciliationにも同じprovenance・physical identity契約を適用する。§6の既存mergeを安全性の証拠や禁止の例外とせず、provider associationと混同して推測修正しない。

## 4. Provider request・association・mappedCells

### 4.1 Request manifest

各画像とmappingの対応を送信前に確定し、最低限以下を追跡可能にする。

- batch / call番号、request order、compact ID、original mappingId、targetPath、sourceRegionId
- 元画像、準備後画像、実送信画像のSHA-256と一致関係
- association token、model、prompt version、schema version

`provider input image ↔ association token ↔ compact ID ↔ original mappingId ↔ targetPath ↔ sourceRegionId` の対応を保つ。配列順序だけを証拠にしない。

### 4.2 Response validationとcompact ID展開

受理前にcompact ID、association token、item count、duplicate / unexpected / missing ID、schema validityを検査する。配列位置、応答順、compact IDだけ、前後の文字、意味的なもっともらしさでは値を信用しない。

tokenが期待値と一致しない値は保存しない。`OCR_ASSOCIATION_MISMATCH` では既存の安全側動作を維持し、warningだけにして値を通過させない。現providerは不一致を除外し、対象mappingへ `raw=None` / `uncertain` と理由を付ける。欠落は `OCR_MAPPING_MISSING` として扱い、後続値をずらさない。

現 `_repair_single_compact_id` は単一要求・単一応答に限定され、その後のtoken照合も必須である。複数セルへの推測割当てに一般化しない。

展開は元mappingへの決定的変換とし、original mappingId、targetPath、sourceRegionId、physical cell ownershipを変えない。duplicateの処理も既存の競合検出・安全側判定を維持する。

### 4.3 mappedCellsとdiagnostics

request準備が初期 `mappedCells` payloadを生成し、response finalizerが認識結果を反映し、Workflowがartifactを保存する。件数やfield ownershipを表示都合で変えない。blankの無条件削除・index shift・neighboring value補完は禁止。件数変更には明確な仕様根拠が必要である。

`openAiMappingDiagnostics` は、明示的な置換設計・実装・テストなしに削除・縮小しない。call / batch / request order、compact ID、original mapping ID、targetPath、sourceRegionId、画像SHA、association token、raw response item、expanded item、mappedCells result、request ID、modelを追跡する。

### 4.4 設定も契約の一部

model、fallback、prompt / prompt version、structured-output schema、association schema、request形式、image detail、前処理、batch size戦略、provider SDK利用方式の変更もOCR-sensitiveとする。

provider・model・promptVersion・schemaVersion等、保持している識別子を記録し、コード未変更でも設定差分を追う。`associationVersion` 等を全jobで永続化済みとは仮定しない。

## 5. Review contractとUI / 業務状態

### 5.1 reviewStructureと行移動

表示上のgroup化とfield identity統合を混同しない。独立項目のgroup identityはlabel文字列でなく `parentLabelCellId + labelCellIds` に基づく。現在の `_logical_field_group_id` はlabel cell IDを重複除去・sortし、情報がなければ `field:{mappingId}` にfallbackする。

過去jobの表示は保存済みreviewStructure / fieldMappingsを基準とする。reviewStructureがない旧jobは既存の独立項目fallbackを維持し、現在のTemplateVersionから勝手に対応を再構築しない。

認識結果に属する `raw`、current value、`normalized`、`status`、`alternatives`、`reviewReason`、warning / review item情報と関連metadataは、行移動時に一式として移す。物理identityは行位置側に固定する。行オブジェクト全体のswapや、文字列だけ移して警告を元位置に残す実装は禁止する。

汎用の疎な行では全列の安全な対応が成立しなければ行移動を拒否する。空文字のfieldとfield自体の欠落を区別し、存在する列だけの部分交換、仮field生成、mapping補完を行わない。

### 5.2 データ・業務状態・一時UI状態の区別

| データ / 状態 | 意味と所有権 |
| --- | --- |
| `raw` / current value / `normalized`、`status` / `reviewReason` / `reviewItems` / alternatives | 認識・検証・ユーザー補正に属する内容。OCR初期判定だけに限定されない |
| confidence相当情報、association evidence、diagnostics | 実装が保持する証拠。confidenceをユーザー確認済みの代用にしない。`FieldReading` に汎用confidenceフィールドがあるとは仮定しない |
| boundingBox / crop / field identity | §3の物理的対応。UIのselectedや編集状態とは独立 |
| `result.confirmed` | サーバーが返す、この結果のローカル確定・レビュー作業完了の正本 |
| selected / editing / loading / modal / popover visibility | 一時UI状態。再描画を理由に補正データや確定状態を初期化しない |
| locked / displayed warning | 業務状態と一時状態からの派生値。lockはrevision競合でも生じるため完了と同義ではない |
| Spreadsheet同期状態 | 外部出力の状態。ローカル確定と独立（§7） |

現 `FieldReading` は `readable` / `uncertain` / `blank` を持つ。`blank` はraw・normalizedがnull、`uncertain` はnormalizedがnull、`readable` はrawを必要とする。この意味・正規化をUI都合で変更しない。`reviewReason` は判定理由、`reviewItems` はpath・severity等を持つ確認情報であり、ポップアップ表示フラグではない。

明示的なユーザー補正は既存経路で許可する。`ReviewPage.updateReading` はfieldのpatchと該当pathのreviewItems除去を行い、backendが再検証する。確定表示やloading終了のためだけの一括消去・成功判定への置換は禁止する。

### 5.3 レビュー表示と状態遷移

作業完了に応じた表示はデータを壊さない派生値で表す。現ReviewPageの `reviewCompleted` / `displayedWarningPaths` も `result.confirmed` を基準にする。独立したローカルreviewed stateで正本を複製しない。

| 契機 | 維持する状態・UI |
| --- | --- |
| 未確定結果を取得 | 必要な警告・編集UIを表示し、自動確定しない |
| 確定レスポンスで `confirmed=true` | レビュー編集stateを閉じ、判定・mark / choice編集UIと作業としての要確認表示を終了する。修正値・判定根拠は保持 |
| 再取得・再マウント・loading終了 | 最新のサーバー状態に従う。同じ確定結果をOCR初期状態へ戻さない |
| 確定・保存API失敗 / revision競合 | 修正値と復旧手段を保持。lockやエラーを確認完了の代用にしない |
| セル選択・crop閲覧 | 確定後も閲覧を許可し、レビュー編集UIの再表示とは分離する |
| 明示的なreopen成功 | サーバーの `confirmed=false` に従って編集・必要な警告表示を有効化する |
| Google同期失敗 | 確定を取り消さず、既存の外部出力再試行経路へ送る |

## 6. 補正・revision・再読取

### 6.1 補正と確定snapshot

`OcrResultRepository` は `raw_candidate_json`、`current_corrected_json`、`OcrResultRevision`、`ConfirmedResult` を区別する。表示のために原候補から補正済み値を作り直さない。

`correct` はREVIEW_REQUIREDを要求し、candidateを正規化・検証する。repositoryの `save_correction` はbase_revision条件付き更新、revision増分、source付き履歴を記録する。競合を上書きせず、mapping / sourceRegionId / boundingBox / field ownershipを保存・再取得まで照合する。

`confirm` はユーザーが見たbaseRevision、validation、document解決を検査して確定snapshotを保存する。確定や正規化は不確実なOCRを自動的に正読へ昇格させる許可ではない。

`reopen` は確定snapshotの存在を検査し、jobをREVIEW_REQUIREDへ戻してconfirmed_atを解除する。OCR再実行やresult revision増分はなく、補正済み値・既存確定snapshotを残す。

### 6.2 retry / RESCAN_PAGE

現在、独立したre-read service / endpointはない。job retry、別OCRジョブの受付・実行、`document-resolution` の `RESCAN_PAGE`、document reconciliationの組合せを区別する。

- `JobService.retry` は既存の状態制約、受付入力・template snapshot、attempt管理に従う。最新templateへ暗黙に差し替えない。
- `RESCAN_PAGE` は対象document / page / jobを検証し、replaces_page_idとRECONCILIATION revisionを記録する。
- 現 `_merge_documents` は新しい非空のreadingを採用し、空欄等では旧readingを残す。saleCalves / calvingsの対応にはparityまたはindexを使う。これは別文書のmerge仕様であり、provider associationの証明ではない。
- `save_provider_result` は再処理時に現在結果を更新し、REPROCESS履歴を追加する。再OCR・reconciliationが「常に旧補正値を優先する」とは記述しない。

新旧jobの座標・mappingは同一とは限らない。snapshot / pageを含むprovenanceを保ち、ID文字列一致だけで安全としない。旧値を残すmergeのcrop / fieldPreview整合性はケースごとに確認する。UX変更で採用規則を変えず、誤対応は証拠・比較・テストを伴うOCR改善タスクとする。

## 7. ローカル確定・Google同期・Spreadsheetセル

### 7.1 現在の処理順と失敗

`OcrResultService.confirm` は確定snapshot・document reconciliation・job/document状態をDBにcommitした後、Spreadsheet templateなら `_publish_spreadsheet_template`、それ以外は条件付きDrive名同期と `GoogleSheetsSyncService.sync` を同期的に呼び、最後に結果を返す。UIから別endpointを呼んで初めて全同期が始まる設計ではない。

`confirmed=true` はローカル確定、`spreadsheetSync.status=synced` は外部反映成功、`failed` は外部出力失敗を意味する。同期失敗をレビュー未完了へ変換しない。

Sheets同期とtemplateのDrive作成失敗はfailedを記録する。ただしXLSX生成・出力先確認等のtry外の処理は例外を伝播し得る。commit後のAPIエラー／通信断を未確定と決め付けず、入力を保護してサーバー状態を確認し復旧する。

`POST /ocr-jobs/{job_id}/spreadsheet-sync`（API v1配下）から再試行できる。修正・reopenでも条件によりSheets同期が走るため、「外部同期は確定後だけ」とは扱わない。Driveのaccount / ownership / conflict保護、operation IDによる冪等性、revision照合を迂回しない。Google由来の編集は既存の `apply_spreadsheet_edit` と検証・revision経路を使う。

### 7.2 OCR値から出力セルへの対応

Spreadsheet template出力は受付時の `spreadsheetOutput` と `fieldMappings` snapshotを基準に、targetPathの値を `_destinations` と結合anchorへ解決する。UIの配列順や表示名で出力セルを推測しない。元XLSXと生成コピーを区別し、OCRテキストを式として扱わない既存処理を維持する。

`OutputLayoutTemplateService` の保存layout、通常Sheets同期、`ExcelExportService` の出力も対応境界に含める。UX都合でbinding・sheet・元cell・確定revisionを変更しない。

### 7.3 Excel出力の元結合範囲（2026-09-05修正）

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

## 8. 検証基盤と変更範囲別の検証

### 8.1 実在する基盤と限界

`scripts/verify_ocr_contract.py` と `backend/tests/ocr_contract/` は現在存在する。scriptは実行Pythonのpytestをbackend作業ディレクトリで起動し、固有の `.pt-ocr-*` を生成する。対象はscript内の `targets` が正本。

| 対象 | 現在の検証入口（backendのtestは `backend/tests/` 配下） |
| --- | --- |
| provider / association / mapping | `unit/test_openai_call_budget.py`、`unit/test_openai_mapped_payload.py`、`unit/test_deterministic_mapper.py` |
| request / response計算・workflow | `ocr_contract/test_phase5a_request_preparation_contract.py`、`ocr_contract/test_phase5b_response_finalization_contract.py`、`unit/test_ocr_workflow_optimization.py`、`ocr_contract/test_reading_logic_audit.py` |
| 受付・execution・cancel / retry・永続化 | `ocr_contract/test_phase2_pipeline_contract.py`、`ocr_contract/test_phase3_job_submission_contract.py`、`ocr_contract/test_phase4_document_ocr_execution_contract.py`、`unit/test_job_state.py`、`integration/test_jobs_api.py` |
| preview / reviewStructure / 補正・確定 | `unit/test_ocr_result_previews.py`、`unit/test_review_display_structure.py`、`unit/test_ocr_confirmation_revision.py`、`unit/test_document_reconciliation_service.py`、`integration/test_ocr_review_api.py` |
| Spreadsheet境界 | `unit/test_spreadsheet_template_output_contract.py`、`unit/test_spreadsheet_edit_sync.py` |
| 計算境界の依存 | `ocr_contract/test_phase6_architecture_dependencies.py` |
| frontend review / identity / state | `frontend/src/pages/ReviewPage.test.tsx`（scriptには含まれない） |
| schema fixture | `golden/test_schema_fixture.py`（mock-resultのschema検証。scriptには含まれない） |

scriptにはfrontend、backend全体、すべての検出・cropテストは含まれない。汎用の保存実response Golden replayと旧文書の推奨fixtureディレクトリは未整備。現contract testsの固定入力・stub比較を全Golden replay成功と扱わない。

### 8.2 実行コマンド

Windowsの現リポジトリでは、pytestの入った `backend/.venv` を使う。別環境では依存を備えたPythonへ読み替える。

| コマンド | 作業ディレクトリ |
| --- | --- |
| `git status --short`、`git diff --check`、対象の `git diff` | リポジトリroot |
| `npm test -- ReviewPage.test.tsx`（変更対象に合わせて選択） | `frontend/` |
| `npm test` | `frontend/` |
| `npm run build` | `frontend/` |
| `backend\.venv\Scripts\python.exe scripts\verify_ocr_contract.py` | リポジトリroot |
| `.\.venv\Scripts\python.exe -m pytest tests/ocr_contract -q`（これだけではscript全対象を代替しない） | `backend/` |
| `.\.venv\Scripts\python.exe -m pytest` | `backend/` |
| `.\.venv\Scripts\python.exe -m ruff check app tests` | `backend/` |
| `.\.venv\Scripts\python.exe -m mypy app` | `backend/` |

pytestのtemp権限に問題がある場合は、未使用のrepo-localパスを `--basetemp` に指定する。既存のtempやユーザーデータを再利用・削除しない。静的検査は変更対象と全体の既存エラーを区別する。

### 8.3 検証の適用

| 変更 | 必須検証と追加条件 |
| --- | --- |
| 文書だけ | 実コード・パス・文書間整合性、diff確認。記載した動作に未確認点があれば該当テストを実行 |
| 表示・操作・一時UI stateだけ | frontend対象テストとbuild。状態変更は関係する成功・失敗・再取得・再マウント・reopen・同期失敗を確認。共通UI / 複数画面への影響時はfrontend full tests |
| backend（OCR-sensitive以外） | 関連unit / integration、該当静的検査。共有service / DB / lifecycle変更時はbackend full tests |
| OCR core（prompt / model / schema含む） | 変更前後の専用script、同入力の保存response / fixture比較、関連境界テスト、backend full tests。provider変更はrequest・association・schemaを検証 |
| ReviewPage mapping / 補正payload / revision / 出力対応 | OCR-sensitiveとして上記を実行。field identity、sourceRegionId、targetPath、crop、保存・再取得、frontend対象テスト・buildを含める |
| Crop / rectangle / region / template layoutデータ変更 | OCR-sensitiveとして上記を実行。検出・boundingBox・sourceRegionId・実crop SHA / 見た目とreview対応を追加確認 |

変更前にrevision・worktree・条件を記録し、変更後と同じ対象で比較する。意図しないmappingId / targetPath / sourceRegionId / boundingBox差分は重大回帰として停止し、原因を調査する。認識文字列やstatusの差分は、意図した精度改善か別途評価する。

必須検証が実行不能なら理由と代替の既存テスト・未検証範囲を報告し、「OCR安全性を完全には検証できていない」と明記する。未整備のGolden基盤を実行済みとしない。必要な対応関係を証明できない変更や契約回帰は完了扱いにしない。

## 9. Golden・テスト保護・実API

### 9.1 比較データの要件

Golden replayでは同一画像・layout・manifest・provider responseからidentityも比較する。未整備部分に最低限揃えるケースは以下。

- independent fields：通常テキスト、数値、複数値の論理項目、空欄、grouping
- repeated section：3行以上・複数列、status / alternatives / reviewReason / warning、行移動と保存
- association mismatch：期待tokenと応答token不一致、値を保存せず隣接fieldへshiftしないこと
- long text / blank transition：文字あり→unknown→後続ずれ→空欄で見かけ上復帰する系列を検出すること

比較対象は存在するmappingId、openAiMappingId、targetPath、sourceRegionId、boundingBox、association result、raw、normalized、status、mappedCells構造、reviewStructure ownershipを含む。文字列だけの比較は禁止。

### 9.2 テストと期待値の保護

実装を通すためのtest削除・skip、assertion弱体化、都合のよいfixture変更、mismatchケース削除、Golden比較項目の削減は禁止する。失敗時はまず実装の契約破壊を調査する。

Golden expectedを現在出力へ合わせるだけの更新は禁止する。変更できるのは、ユーザーが要求した意図的なOCR契約変更で、新しい期待仕様が明確であり、回帰テストで証明する場合のみ。旧新契約・理由・影響を報告する。新しい再現ケースの追加と既存期待値の差替えを区別する。

### 9.3 実API利用

unit → 保存response / Golden比較 → integration・必要な回帰 → 必要時のみ実API smoke testの順で検証する。mock / stubで足りるテストにOpenAI / Google APIを使わない。実API呼出しの既存許可・予算を守る。

model・prompt・schema・provider request・association方式変更、または固定fixtureだけでは評価できない精度課題では実API smokeを検討する。実施時は目的、API calls、image count、model、association mismatch、mapping誤対応、schema error、recognition-only errorを分けて記録する。未実測項目を0件と報告しない。

## 10. OCR改善と意図的な契約変更

OCRコア変更には、AGENTS §5の明示されたタスク範囲、再現可能な問題または測定可能な精度課題、変更前後比較、§8の検証が必要である。コード整理・抽象化・美観・一般化・speculative optimization・将来用途だけでは変更しない。

既存契約に戻す不具合修正は、不変条件の書換えを必要としない。発見した新しい境界違反は再現・原因を示して修正する。UXタスクで別のOCR不具合を発見しただけでは、依頼範囲を拡張する権限にはならない。

契約自体を変える場合は、ユーザーの明示要求に基づき、以下をすべて行う。

1. 旧契約と新契約、必要性、影響範囲を提示する。
2. この文書を更新し、新契約を証明する回帰テストを追加・更新する。
3. Golden fixtureへの影響を確認し、必要な期待値変更は§9.2に従う。
4. 必要に応じてmigration・実API smokeを実施する。
5. 検証結果と残る不確実性を報告し、意図的なOCR contract変更と明記する。

不変条件を暗黙に緩めない。不確かな対応を推測で通過させない。

## 11. Baselineと完了報告

旧文書には、Phase 0〜9で実Chrome E2Eまで確認したという履歴がある。記録内容は、manifest / raw / expanded / mappedCells対応（当時association mismatch・誤対応0件）、行移動時の物理identity固定と認識metadata移動、補正保存・再読込、displayName・独立項目group・同名別項目分離、セル直接編集・判定popover、疎な行の操作拒否とcorrection非発行である。

この履歴は現在のrevision / worktreeへの保証でも、今回の実UI再検証でもない。baselineは作業開始時のrevision・worktree・テスト・fixtureで記録する。

OCR-sensitive変更の完了報告には、次を含める。

- 変更ファイル、pipeline stage、影響するinvariant、原因と意図
- mapping identity、physical identity、provider association、crop / boundingBox、prompt / model / schemaの変更有無
- 変更前後の検証コマンド・結果、Golden / 保存response比較の結果と差分、backend regression、frontend検証
- 実OpenAI / Google API使用有無・回数、実測したmismatch / mapping誤対応 / schema error / recognition-only error
- `git diff --check`、未検証範囲、既存失敗・環境制約

OCR動作を変更していない場合は「既存OCR contractを維持した」と明記する。同じ画像から意図したセル・mapping・検証済みresponse・レビュー・補正保存・出力までの対応を証明できることが完了の基準である。
