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

### 1.1 複数ページOCRの製品ゴールと完了条件

製品ゴールは、記入済みの複数ページ帳票から、各ページの明示的なテンプレートページと
読み取り欄へ対応付けたOCR候補を生成し、人間が結果・矩形・必要な補正を確認して
全ページを確定・出力できることに置く。

このゴールにおいて、手書き文字列の完全一致、スキャンと参照画像のピクセル完全一致、
または人間の確認なしに全筆画の正しさを自動証明することは完了条件ではない。
スキャンの回転、拡縮、平行移動、許容された射影歪みは位置合わせで吸収し、
矩形の恒久的な修正は既存DRAFTの明示的なテンプレート編集経路で行う。

ただし「完全一致を要求しない」は、値・ページ・矩形の推測再割当てを許可する意味ではない。
ページidentity、physical cell identity、mappingId、targetPath、sourceRegionId、crop、
受付snapshot、association検証、revision付き補正・確定、出力bindingは維持する。
位置合わせ・crop・associationに根拠が足りない結果は、隣欄へ推測して通すのではなく、
理由を保持して要確認または拒否とする。

§12の資格、入力identity・位置合わせ・crop対応検査、renderer隔離はこの方針だけで削除・緩和しない。
本契約は、未実装の自動保証を「複数ページOCRと人間レビューの操作フロー」全体の
完了条件へ無制限に拡張しないことを定めるが、既存gateを迂回する許可ではない。
候補OCRへ進める条件の変更は§12.9を参照する。それ以外の条件を変更する場合も、§10に従い、ページ別の根拠・
要確認への遷移・補正保存・出力対応を検証して明示的に変更する。

### 1.2 認識精度と通常運用コストの方針

通常運用の目的は、システムだけで手書き文字を完全一致させることではなく、利用者が確認・修正できる
有用なOCR候補を、複数ページの正しい欄との対応を保って提示することである。OCR結果は未確定候補であり、
利用者が全ページの値と切り出し画像を確認し、必要な手動修正を行った後の明示確定を正本とする。
低信頼・判定不能を完全一致に見せる補完、自動確定、確認工程の省略は行わない。

必要な精度の最低条件は、文字列の完全一致率ではなく、少なくとも次を満たすことで判定する。

- 入力ページが正しいテンプレートページへ結び付いていること。
- 位置合わせが既存閾値内で成立し、対象欄のcropとphysical cell / mapping / 出力先の対応が保たれること。
- 認識結果と低信頼・失敗理由がレビュー可能な形で提示され、利用者の補正がrevision付きで保存されること。
- 未確定結果を確定済み出力として扱わないこと。

文字単位の実用精度は、代表的な実記入サンプルで候補正答率と手動修正量を測定して評価する。
現時点で未測定の数値目標を達成済みとは扱わない。一方、完全一致の自動証明を通常利用の開始条件にはしない。

人間レビューを前提とする候補OCRでは、通常の画面表示、OCR受付、OCR実行のたびに、Office再描画、
正式資格の全再発行、全ページ画像の完全一致確認を繰り返さない。これらの重い処理は、初回準備、対象版・原本・renderer環境・証拠の変更、
証拠失効時など、再準備が必要な境界で明示的に実行する。通常利用では保存済み証拠の現在性確認と、
実入力ごとのpage identity・registration・crop correspondence・association検査を残す。
処理時間を短縮する場合も、失効条件を無視するcache、推測mapping、検査失敗時の継続によって実現しない。

## 2. 現在の実装境界と保護対象

以下は実装を照合する入口である。表のパスは `backend/app/` 基準（frontendのみリポジトリroot基準）。ファイル名だけでなく、対象データを生成・変換・保存する処理を保護する。

| 境界 | file / class・function | 保護する挙動 |
| --- | --- | --- |
| 受付・snapshot・retry | `services/job_service.py` — `JobService.create_ocr_job` / `retry` | 入力・templateSnapshot・fieldMappings・出力snapshot、冪等性 |
| job実行 | `jobs/runner.py` — `JobRunner`、`jobs/handlers.py`、`services/document_ocr_execution_service.py` — `DocumentOcrExecutionService.execute` | queue、attempt、progress、cancel / retry |
| XLSXページ定義・geometry | `domain/print_layout.py`、`services/print_layout_service.py`、`services/workbook_geometry.py` | 版所有、保存range、geometry version、page-local transform（§12） |
| paged受付・実行 | `domain/job_pages.py`、`services/job_page_service.py`、`services/paged_ocr_execution.py` | 明示binding、全ページsnapshot、ページ別artifact、部分失敗・retry（§12） |
| reference・環境認定 | `services/reference_qualification_service.py`、`services/renderer_environment.py`、`services/office_reference_renderer.py`、`services/renderer_raster.py`、`services/reference_crop_safety.py` | 原本保持、evidence失効、隔離、font、crop安全性、本番gate（§12） |
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

legacy単一ページの主要経路は `JobRunner → PipelineJobHandler → DocumentOcrExecutionService.execute → ImageExtractionService.process → OcrWorkflowService.process`。新しい`jobPages`形式は同じjob lifecycle内で`PagedOcrExecution`へ分岐し、既存の抽出・workflowをページ別に利用する。request/response計算へHTTP・DB・Google・filesystem責務を持ち込まない。依存テストは直接importと特定のfilesystem呼出しの検査であり、推移的依存の完全分離は保証しない。

以下の既存ReviewPage・単一OcrResult・補正・確定・出力の実装説明はlegacy経路のもので、新paged結果に対応済みという意味ではない。§3〜7のidentity・association・値保護の契約は新経路にも適用し、未対応操作は§12に従い明示拒否する。

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

### 3.4 手動追加した多角形

新しい多角形セルは `shapeType=polygon` とcanonical座標の `vertices` を保持する。
頂点の外接矩形を既存のx/y/width/heightと一致させ、3〜100頂点・重複なし・
面積あり・自己交差なしを検証する。矩形には頂点を要求せず、既存データを移行しない。

多角形の頂点はFieldMappingConfig、RegionConfig、受付snapshotへ引き継ぐ。
RegionCropperは既存のpadding込みbboxで切り出した後、多角形の場合だけ領域外を
白、ink_differenceを0にする。入力画像全体を変更しない。画像端の多角形のpaddingは
canonical領域内に制限する。矩形のpadding・切り出し画素・mapping保存形式は維持する。
空のverticesを矩形のconfig/snapshotへ追加して既存hashを変更しない。
provider・model・prompt・association方式は変更しない。

Canvasのハンド操作による明示的な配置修正では、編集可能なDRAFTの同じcell IDと
意味づけを保持して座標を平行移動できる。多角形の頂点、結合parts、所属行の外接矩形を
対応させ、ドロップ時に既存layout保存経路へ渡す。仮想見出しは移動後もOCR対象外。
元XLSXのセル・出力先を移動する操作ではなく、過去jobのsnapshotを変更しない。
§12のページ投影矩形の固定座標制約は解除しない。

対応範囲・境界テストは[テンプレート手動編集の拡張](template-authoring-extensions.md)を参照する。

### 3.5 単一ページ位置合わせの追加復旧

従来の位置合わせが `LOW_REGISTRATION_CONFIDENCE` で失敗した場合に限り、
双方向のdescriptor比率検査で相互に一致するORB特徴点から別候補を生成できる。
帳票名・項目名に依存する分岐や既存の一致数・inlier数・比率・残差閾値の引下げは行わない。
従来成功経路の画像画素と採用条件は維持する。

追加候補は既存の幾何条件に加え、ECCスコア、補正後の射影形状・向き・残差、
全体の輪郭一致、紙面3×3区画と受付snapshotの読み取り枠付近の印刷輪郭を検証する。
独立した画像輪郭検証には特徴点の対応表を流用しない。32 edge画素未満の領域は
検証根拠とせず、少なくとも4区画と、根拠がある全対象領域が既存の輪郭閾値を満たす必要がある。
空の記入欄だけで追加候補を認定しない。枠数・選択範囲を変換推定へ使わない。
印刷輪郭一致は手書きの認識正解率や、全筆画のcrop clearanceの保証ではない。

追加経路の最終変換はECCを合成したnormalized-image → template-image変換として記録する。
失敗時にもinput / template / config SHAと数値、比較画像をjob / attempt（pagedではpageも）
に分離保存し、切り出し・providerへ進めない。mappingId・targetPath・sourceRegionId・
保存矩形・paddingを再生成せず、過去jobのsnapshotや公開版を変更しない。
新printLayoutの認定gate解除には使用しない。

再現・実装範囲・検証の限界は[位置合わせ失敗調査](registration-investigation-20260911.md)を参照する。

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

### テンプレート文字の自動配置

`LayoutCell.textVerticalAlignment` は表示用の `top` / `middle` / `bottom` / null。
新規XLSXは元セルの縦配置、画像は既存の文字検出領域の上下余白から求める。
文字検出がない場合はnullとし、自動表示では従来の上配置へfallbackする。
既存XLSXは同じ版の `spreadsheetPrintCells` に保存されたidと矩形座標・寸法が
一意に一致する場合だけ不足情報を補う。既存の配置指定は上書きしない。
自動ボタンは各矩形の横・縦配置を使用し、手動表示切替は保存値を変更しない。
これは表示metadataの追加であり、文字列・物理identity・mapping・crop座標・
provider契約を変更せず、過去jobのsnapshotや公開版のDB内容を書き換えない。

### 5.1 reviewStructureと行移動

テンプレートの新しい見出し階層はLayoutCellの `parentLabelCellId` に明示保存する。
見出し区分は既存の `parent_label` を使用し、親専用・仮想専用の区分を増やさない。
新しい接続は既存relation・targetPath・結合・cell IDを再生成せず、単一親と循環拒否を
守る。既存relationの親接続も循環検査に含める。仮想見出しは `virtual=true` の
名前付きmanual見出しで、配置用の座標をOCR領域として使用しない。
仮想見出し自身へのfield mappingは生成せず、ラベル文字候補OCRからも除外する。
囲い込み対象は現在の論理セルに限定し、結合前のpartsから子を再生成しない。
過去jobのreviewStructureや補正・確定値を新しい階層から再構成しない。

繰り返し群への見出し接続はrecordSectionsのparentLabelCellIdへ保存し、
個別列の意味づけへ置き換えない。同じcollectionの複数帯は従来どおり同じ親を持つ。
見出し階層の循環検証には、この群から見出しへの既存接続も含める。

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

テンプレートの自動保存では、保存中の一時状態だけで編集領域全体を無効化しない。
同じエディターの保存要求は直列化し、送信後の追加編集を古い保存応答で上書きしない。
ブラウザー下書きは最新の編集内容が保存された場合だけ消去する。保存後の関連取得失敗も
永続化状態が不明な失敗として扱い、元の値へ戻した編集を含めて下書きを保持する。
同一エディター内の工程切替は未保存の状態と保存キューを保持したまま実行できる。
工程切替を保存完了とは扱わず、移動先の追加編集を古い保存応答で上書きしない。
画面終了・再検出・明示保存・承認の完了境界、既存のAPI検証・revision照合を迂回しない。
実装範囲と検証は[テンプレートの非ブロッキング自動保存](template-background-save.md)を参照する。

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

選択したliteral部品と元セル値の比較では、取込・編集時に除去される前後空白の差を
非空の文字列に限り許容する。比較だけであり、原本・保存partsを正規化しない。
内部空白、文字種・大小文字の差を同一視せず、数式とinput部品の内部値保護も維持する。
テンプレート名・特定ラベル・セル番地による例外は設けない。

ページ結合の版コピーでは、TemplateVersionServiceが付与する20桁のhex suffixを持つ
元セルIDも同じ元Excel座標へ解決する。その他の仮想分割IDへ範囲全体の所有を拡張しない。
ページ保存では取込元のcanonical矩形との一致も検証し、コピー後に元結合範囲を縮小しない。

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

### 記入欄内の記号判定（2026-09-25）

明示された記号読取の要求により、単一ページ経路の `markRecognitionMode` に
`symbol` を追加する。既存の `automatic`（印刷選択肢の差分検出）と
`manual`（利用者が選択）の意味・既定値は維持する。
UI上は記入欄の読み取り方法として文字・記号・印刷選択肢を選ぶ。
保存表現は既存の `role=mark` / `markSets` を使用し、新しい矩形roleを増やさない。

`symbol` は単一選択に限定し、欄全体の既存cropと候補ラベルを
`candidateType=symbol_choice` として既存のOpenAI batchへ渡す。
候補別のgeometryは互換保存用であり、記号の分類やcrop生成には使わない。
文字検出や選択肢別の差分検出結果を記号の認識結果として扱わない。
候補と完全一致した確実な応答だけを採用し、空欄・候補外・複数記号・不確実な
応答を強制的に候補へ補正しない。通常の確認・手動補正・確定経路を維持する。

model、応答schema、association token、mapping/sourceRegion identity、crop、
受付snapshot、revision、出力先決定は変更しない。新モードのrequest指示だけを追加する。
既存jobへの後付け移行は行わない。paged経路のmark拒否も維持し、ページ別編集では
記号モードの新規選択を無効にする。実画像での認識精度はoffline境界テストだけでは保証しない。
検証の範囲・結果は [記号判定の実装記録](verification/symbol-reading-20260925.md) を参照する。

### Template編集の版境界

mapping生成validationの失敗を空mappingへの置換保存に変換しない。
不完全なDRAFT layoutは保存できるが、既存fieldMappings・document identity・logical field
identityのpresent状態を保持し、layoutFinalized=falseとする。再取得は現在layoutの
mapping生成validationを正本にvalidForOcr=falseと対象fieldの警告を返す。
保持したmappingを読み取り確認・ACTIVE化・新規OCR受付で有効化しない。
frontendの未割当（保存先なし）と設定不完全（保存先あり・mark必須設定不足）を区別し、
共通fixtureでbackendとの判定一致を固定する。過去job snapshotは再検証対象にしない。
DBとconfig YAML全体のatomic saveは別課題であり、この保護を全面transactionと称さない。

公開済みTemplateVersionのlayout・field mappings・mask・汎用定義を直接変更しない。
ACTIVEからの編集は同じ原本のDRAFTを1つ作るか再開する。途中保存では版を増やさず、
承認時にactiveを原子的に切り替える。旧版は既存のARCHIVED状態へ移行するが内容は不変。
系列とlogicalFieldIdを継承し、mapping/cell/region/準備job/artifact identityは新版へ分離する。
logical identityのsidecarをprovider associationの代用にしない。
過去jobは受付snapshotを保持し、旧jobへlogical IDを後付けしたり新版から再構築したりしない。
詳細と意図的なlifecycle変更は [Template Versioning](template-versioning.md) を参照する。

### 契約変更の手順

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

## 12. XLSX複数ページOCRの安全契約

本節は新printLayout / paged OCRの追加契約であり、§3〜10のprovider・model・prompt・schema・association・mapping・補正・確定・出力契約を緩めない。現在の対応状況と作業ルールは[AGENTS §9](../AGENTS.md#9-xlsx複数ページocr--reference-renderer)、実験・測定はそこから参照するPhase報告に置く。

### 12.1 版所有・保存range・geometryの互換性

`printLayout`はTemplateVersionが所有し、Template系列へ共有して公開版の意味を変えない。保存・解除はDRAFT制約とrevision再照合を要求し、検証fingerprintにページ設定を含める。準備jobの定義・画像hashは受付済みOCR jobのsnapshotと区別し、過去jobへ後付けしない。

ユーザー確定済みpage rangesを最優先し、版に保存済みの範囲をmanual breaks・目標ページ数・自動計算だけで再構成しない。再配置は明示的な編集として行う。行・列・元セル範囲を保持し、pixel矩形だけを正本にしない。

論理セル座標と物理ページ座標を区別する。元セル範囲からpage-local rectangleと基準画像への変換は、保存した用紙・方向・余白・全ページ共通のuniform scale・offsetに従う。連続編集ビューのrangeを物理ページそのものと扱わない。

geometry modelの変更を旧保存プラン・legacy canonical rectangle・preview・受付済みjob・OCR結果へ暗黙適用しない。旧X/Y画像合わせ補正も一律にuniform print scaleへ置換しない。旧データの移行が必要なら、§10に従い明示的な版変更・migration設計と比較検証を分離する。

2026-09-14追加の `wsl-calc-print-grid-v1` は、明示印刷確定APIで新モデルを選択した
DRAFTだけに適用する。固定runtime ID、原本/FODS SHAと固定inch出力を要求し、
fit/中央配置用の公称twipと、各寸法を整数化して累積する描画グリッドを分離する。
旧版のGET・通常保存で移行せず、既に新モデルの版は再確定で旧モデルへ戻さない。
canonical layout・元セル範囲・意味づけ・過去jobは保持する。印刷範囲が成立しない場合は
候補を拒否し、座標clampや許容値拡大で救済しない。
FODSだけではUNO寸法exportのoverflow不在を証明できないため、この下書きモデルと
artifact生成成功を本番資格へ昇格させない。詳細は[Calc印刷グリッド](verification/calc-print-grid-20260913.md)。

新Calcモデルの参照検証は、測定開始前・資格レポート保存前・保存済み資格のGETで、
所有原本/canonical layout/configと保存寸法からページを再計算し、全cell/fieldの元範囲・
bindings・座標・transform・ページ所属を保存planと照合する。不一致を再保存して修復せず拒否する。
描画上同じ `shrinkToFit` の省略/falseだけを比較時に揃え、identityや座標を正規化しない。
旧geometryモデルや過去jobへの適用、GETでのOffice起動、資格発行・本番gate解除は含めない。

### 12.2 ページを含むphysical identityと入力binding

新経路のfieldは`TemplateVersion → TemplatePage identity → source cell/range → mapping → page-local rectangle`を一意に追跡できなければならない。同じX/Yでも別ページのfieldは別の物理領域であり、ページ情報なしで保存・処理しない。

現TemplatePage identityは版IDと基準画像artifact IDに由来し、受付入力はJobInput UUIDで識別する。`templatePage → inputPage`は明示bindingで固定し、アップロード順を逆にしても同じ対応を維持する。配列index・ページ番号だけ・OCR文字・ラベルからidentityやfield所有権を推定しない。

### 12.3 受付snapshotと実行・retry

1 logical document/jobの受付transactionで全ページのbindingを検証し、少なくとも次をimmutable snapshotへ固定する。

- TemplateVersion、page identity・順序・ranges、元XLSXのidentity / SHA
- ページ別基準画像・参照元artifact・SHA、transform、source cell/rangeを持つfields、fieldMappings
- OCR設定とprovider / prompt / schema識別子、画像処理・registration等の実行設定
- 新OpenAI paged受付は[prompt本文と応答model schema](paged-transcription-resources.md)も
  内容・SHA付きで保存する。実行時の本文再読込や欠落snapshotの暗黙補充を行わず、
  現在の応答model schemaとの不一致は画像処理/provider呼出し前に拒否する。
  この検査だけでSDK/native依存等のruntime全体を固定・認定したとは扱わない。
- 入力ページidentity、binding、inputRevision、入力SHA

文書識別・書類名設定がある新規受付では、検証済み全体configを維持し、
`documentMetadataAdapter=paged-document-fields-v1`とページ別`documentContext`を固定する。
識別項目のページ所属はmapping IDとtargetPathの一致で決め、全体のgroup/役割/順序を変更しない。
読込時にも全体configからの所有権と各contextの完全一致を検証する。旧受付へ後付けしない。

実行・retryは受付snapshotを正本とし、現行TemplateVersionからranges・fields・mappings・基準画像・設定を差し替えない。全ページの入力・基準画像SHAとsnapshot整合性を最初のprovider呼出し前に検証する。保存画像を再生成して不一致を隠さない。

現実行設定が受付時と異なる場合、最新設定で続行せず明示拒否する。retryで成功ページを再利用できるのは保存結果のSHAとページsnapshotが一致する場合だけであり、同じ受付snapshotで失敗ページを再実行する。過去jobの再現性を新しいgeometryや認定artifactの適用で変更しない。

新規OpenAI paged受付は文書全体の`ocrCallBudget`と専用予算表を同じtransactionで固定する。
2026-09-22以降の新規受付は、各ページの受付mapping数とdocument identity項目数をproviderの
現batch規則で計算した`ocrCallBudgetPlan=paged-ocr-retry-reserve-v1`も固定する。
全ページ最低枠と同数の文書再試行余力を受付時に要求し、設定上限が合計必要数未満なら
catalogを利用不可、acceptを送信前拒否とする。ページ数・帳票名・固定件数では判定しない。
設定上限は引き続きhard ceilingであり、受付後のjobへ増額・再計算・予算追加を行わない。
全page/attemptで上限を共有し、送信前にpage ID・attempt付き予約と使用回数をcommitする。
失敗・応答消失・再起動・再試行で予約を払い戻さない。予算のない旧受付へ後付けしない。
未完了全ページの最低呼出し数が不足する場合は最初の送信前に拒否し、
optional再読取から後続ページの最低枠を保護する。予約済み回数と実送信済み回数を混同しない。
providerがSchema不成立・途中切れとして破棄した応答は、受付snapshot、batch、mapping、associationを
変更せず、設定済みretry回数と残予算の内側で再送できる。破損応答の値は保存・補完しない。
この再送も予約を消費するため、受付時の再試行余力が常に未使用のまま残る保証ではない。
既存の単一ページ予算契約とprovider association方式は維持する。
詳細・migration・失敗復旧は[文書全体API予算](paged-ocr-budget.md)を参照する。

### 12.4 全ページ処理と未対応操作

`jobPages`形式を単一入力経路へ渡さない。欠落・重複・余剰・unsupportedページを黙って除外せず、全fieldのページ所有権を検証する。ページ別artifactを分離し、単一OcrResultへ上書きしない。各ページでも既存のrequest準備・provider association検証・response確定計算の契約を守る。

全結果を`documentPages`へ集約し、一部失敗を文書全体成功と扱わない。現実装では部分失敗はjobをFAILEDとし、成功ページの証拠を保持する。OCR計算の全ページ成功もレビュー・確定・出力完了の代用にはならない。

未対応の操作は明示拒否する。Page 1だけのOCR・レビュー・exportへ縮退して成功扱いにしない。ページ設定を扱えないpackage出力等でもmetadataを黙って落とさない。

2026-09-13の本番化要求に基づき、専用`page-review`取得・補正APIと`PagedReview`を追加した。
全ページ成功と受付snapshot・page/input/reference・field/cropの一致を確認した結果だけを保存する。
legacyの`OcrResult`へ上書きせず、page IDをキーとする全ページの原候補と現在値を分離する。
全ページ共通revisionの条件付き更新と、編集page ID付き履歴を同じtransactionで保存する。
補正では受付mapping IDが当該ページに所属することを要求し、sourceRegionId・boundingBoxを
クライアントから受理しない。他ページの値・物理対応・原候補・認識diagnosticsを変更しない。
画像品質を含む既存の決定的検証を再適用する。現在のTemplateVersionやruntime設定で
レビューを再構成せず、成功ページの再実行・再公開で補正済み値を初期化しない。
保存中の閲覧・追加編集・ページ切替を維持し、遅い応答で新しい下書きを上書きしない。
通信断・revision競合では下書きを保持し、明示的な最新結果取得後に再試行できる。
続けて、受付bundle schemaVersion 2の文書全体snapshotと全ページ確定・reopenを追加した。
受付transactionでlayout全体（見出し・繰り返し・結合を含む）、config、reviewStructure、
logicalFieldIds、承認済み汎用定義、既存XLSX出力snapshot、page ID/source rangeによる出力bindingを固定する。
原本のSHAを確認したjob専用コピーを所有artifactとして保持し、元作成jobや現行版の変更を参照しない。
旧schemaVersion 1を実行・レビュー時に暗黙更新せず、必要な出力snapshotがない場合の全ページ確定は拒否する。
新形式の実行では所有コピーの範囲・SHAも最初のprovider呼出しより前に検証する。

確定は全ページ共通baseRevisionと全page IDの明示確認、既存の値/画像品質検証を要求する。
PagedConfirmationへ全ページの現在値・文書名・受付bundle SHAを一括保存し、jobのCONFIRMEDと同じtransactionでcommitする。
OCR値・statusを自動昇格せず、既存単一ConfirmedResultやLedgerDocumentPageへPage 1を代入しない。
新paged経路では確定・reopenもrevisionを増分する。旧単一reopenのrevision維持は変更しない。
これによりreopen後の古い確定/補正要求を拒否し、過去の確定snapshotと全履歴を保持する。
確定の成否が不明な通信失敗では再取得まで状態変更/編集を止めるが、通常の補正保存では追加編集を維持する。
競合で保持した下書きは確定済み値として表示せず、明示reopen後に編集値として復元する。
書類一覧への統合は未実装。既存単一ページ用APIの拒否と本番受付gateは維持する。
意図的な新paged保存/確定契約の拡張と残る制限は[全ページ確定](paged-confirmation.md)に記録する。
詳細とmigration適用状況は[ページ別レビュー](paged-review.md)を参照する。

識別項目のOCRヒントは当該ページの検証済みcontextだけから既存request準備へ渡す。
書類名候補は受付全体設定の順序で、全構成項目が非空readableの場合だけ生成する。
候補の更新で入力中の書類名・確定済み名を上書きせず、自動確定や書類間結合に使用しない。
詳細と互換性・制限は[ページ別の文書metadata](paged-document-metadata.md)を参照する。

新規受付は`documentSnapshot.outputAdapter=ooxml-paged-values-v1`も固定する。
専用XLSX取得は全ページ確定済みの指定revisionだけを対象とし、受付の所有原本SHA、
page ID・mapping ID・targetPath・sourceRange/sourceParts・anchorの対応を再検証する。
現行版や準備job、画面の配置・項目名から出力先を推定しない。生成はDB書込lockの外で行い、
応答前に同じ確定revisionと受付SHAを再確認する。出力失敗だけで確定解除・値変更を行わない。
新adapterのない旧受付は後付け更新せず出力を拒否する。詳細は[全ページXLSX出力](paged-output.md)。

### 12.5 原本保持とunsupported workbook

原本XLSXを不変とし、可能な箇所でSHAを照合する。図形・画像・controls・高度なOOXMLの削除、hidden行列の可視化、根拠のないused range切捨て、数式の値化、未対応要素を失うopenpyxl再保存、元pageSetupの破壊によって対応済みに見せない。原本を変更して同じ帳票と主張しない。

新renderer用の一時コピーは原本と区別し、検証済みadapterが保存page range・印刷条件を反映するための必要最小限の変更に限定する。未編集のセル・style・ZIPパートを保持し、対応を証明できない形式は拒否する。内部printLayoutの管理を、出力XLSXの印刷設定を変更する根拠にしない。既存出力契約は§7に従う。

新pagedの記入済みXLSXは別adapterによる結果成果物であり、元sheet名・非表示管理sheet・
印刷設定を保持する。全体をopenpyxlで再保存せず、明示所有セルの文字列値と検証済み手動結合だけを
OOXMLのbyte範囲で更新する。空白確定値は当該セルの元placeholderを消す。数式として書き込まず、
先頭ゼロ・改行・エスケープ様文字列を保持する。結合の範囲、内部値、他項目との重複を検査し、
同じ物理セルへ異なる値が要求された場合は拒否する。旧単一ページの出力仕様は変更しない。

離れたprint area、print titles / repeated rows・columns、header/footer、横複数ページ、hidden行列、shapes / controls / formulas、高度な書式・OOXML、未確認font、過大range等を推測処理しない。安全な対応が未検証ならunsupportedまたは未認定として本番への流入を止める。OOXML属性の未指定とライブラリdefaultも区別し、取得できない設定を推測で正規化しない。

明示的に追加した[印刷用帳票取得](template-print-files.md)は、対象TemplateVersionの保存済み
printLayoutを使う別成果物の生成である。横1ページ×縦Nページの保存rangeから成果物の
Print_Area・manual rowBreaks・印刷条件を設定できる。原本、保存版、過去job、既存の
OCR結果出力（§7）は変更しない。小数％倍率等、既存adapterで再現できない条件は拒否する。
この取得成功・PDF生成成功をreference資格や本番OCR利用許可に昇格させない。

### 12.6 Qualificationと本番gate

目視によるページ編集は[ページ単位の目視編集](page-authoring.md)に従う。
`draft-visible-cells-v1`は非表示行列を表示幅・高さから除き、セル内縮小を参考描画する
未認定の表示モデルである。保存済みlegacy/LOモデルを自動更新しない。原本の設定・セルを
書き換えず、元セルIDからページ表示座標を参照する。元セルの表示座標はlayoutへ保存しない。
`PUT print-layout/authoring`はDRAFT/revisionを確認する。通常矩形と結合partsへ展開した元セル集合は
重複のない既存集合の部分集合とし、残存元セルのphysical ID・canonical座標・形状を保持する。
既存LayoutServiceへ人間の区分・意味づけ・明示的な削除・同一ページ内の結合/解除を保存する。
結合親は元partsの外接矩形と一致し、元XLSXから解決したpartsの範囲は欠落・重複のない連続矩形に限る。
物理セルの追加IDは検証済み結合親と保存partsの復元だけを許可する。field.sourcePartsにcellId/sourceRangeを保存し、
表示位置や配列順から部品を推定しない。元範囲・内部値保護・ページ境界の検証をDB/config保存前に行い、
不正要求で保存済みlayout/config/planを変更しない。ページ境界・transform・参考画像を保持する。
物理枠追加・任意の移動/変形・ページをまたぐ結合は拒否する。これは2026-09-11の明示要求による
ページ手動編集契約の拡張であり、既存jobのcrop・mapping・provider association契約は変更しない。
ページfields/bindingsとlayout SHAを更新する。過去のqualificationは新版の証明に流用しない。
この表示モデルでは既存のqualificationモデル照合が不成立となり、本番gateは閉じたままとする。
Office adapterのhidden行列等の拒否条件、renderer isolation条件、OCR受付・実行・レビューの
制限は変更しない。追加の自動区分・自動意味づけは実装しない。

2026-09-12の明示要求により、保存ページ上の仮想見出しだけを追加・移動できる。
`virtual=true` の名前付きmanual見出しに `pageRange` を保存し、そのセルの矩形座標だけを
当該保存ページ内の表示位置として解釈する。元セルのcanonical座標とは混同しない。
ページ所属は版内の保存rangeに一致させ、配列indexや近傍セルから推定しない。
仮想見出し自身は元Excelセル、page.fields、field mappings、ラベルOCRの対象に含めない。
既存の物理IDから仮想への置換、仮想を含む結合、ページ不在・範囲外の配置は保存前に拒否する。
ページ範囲・用紙変更や解除で所属・位置が無効になる場合も拒否し、対象見出しの明示的な削除を要求する。
2026-09-21の明示承認により、改ページ保存要求の `pageHeadingMoves` で既存仮想見出しID・
変更前range・変更先rangeを指定した場合だけ、所属変更を改ページと同一transactionで保存できる。
名称・座標・親子接続・意味づけ・mappingは変更せず、ID重複/不在・旧range不一致・
物理セル・移動先不在・ページ外配置・revision競合・公開版への保存を拒否する。
未指定の見出しを推測移動しない。所属変更指定なしの既存拒否とページ解除の拒否を維持する。
新しい印刷成果物・正式資格は改めて取得し、この操作でOCR受付条件を解除しない。
旧セルのJSONへ未指定pageRangeを追加せず、旧layout hash・geometryの暗黙移行を行わない。
見出しの階層・繰り返し群への接続は既存の明示ID経路を使用する。本番gateは解除しない。
これはページ手動編集契約の拡張であり、既存OCR contractを維持する。

新規XLSX取込のlegacyプレビューでは、同じ一時コピーをOfficeで読んだ行高・列幅を
`quality_json.spreadsheetPreviewGeometry`へ保存し、元セル由来の矩形座標に利用する。
このsnapshotがない既存版の復元は従来計算を維持する。snapshotがある版の復元は保存寸法と
原本SHAを照合し、現在のOfficeで再測定しない。セル数・役割・merged cellsを推測変更しない。
2026-09-25以降の新規取込は`spreadsheet-preview-office-v2`を保存し、Officeのpt寸法を
画像pxへ換算した自然倍率を上限として、印刷可能領域に収まらない場合だけ縮小する。
中央配置設定を反映し、小さい帳票を用紙幅まで拡大しない。保存済みv1版は従来座標のまま
復元し、既存版・jobの座標を暗黙に更新しない。
これはprintLayoutのgeometry evidenceやqualificationを代替しない。
検証と制限は[プレビュー座標・ページ編集検証](spreadsheet-preview-geometry.md)を参照する。

reference imageの生成成功はqualificationではない。元XLSX、TemplateVersion、printLayout revision、geometry model / implementation、ページ別reference SHA、field geometry、renderer evidenceの対応を検証する。版・設定・画像・実装・環境の変更や証拠の欠落で有効性を失った結果を再利用しない。

依存ファイルのサイズ・時刻の一致だけで以前のSHAを再利用しない。
同一metadataでも内容が変わる場合を拒否する。[依存SHAの再検証](renderer-identity-freshness.md)を参照する。

Geometry / Reference QualificationとRenderer Environment Qualificationを区別し、両方の有効な証拠を要求する。network / resource isolation、実選択font、runtime依存、rasterizer、crop安全性・本番閾値の未認定条件を、座標精度のPASSで代替しない。hostにfontが存在することはOfficeの実選択fontの証明ではなく、fallback先を推測して認定しない。

2026-09-13の「承認する」により、新paged経路の検証を二段階へ分離する契約変更を承認した。
有効化時は、版/原本/全ページ参照画像のidentityとSHA、描画・座標・明示field/mapping、
定義されたcrop領域とページ境界、隔離環境の有効な証拠を要求する。
まだ存在しない記入画像のink clearanceは有効化の前提にせず、空のreferenceから成功と推定もしない。
実入力のpage binding/SHA、位置合わせ、crop対応と実筆画の安全条件は、受付snapshotの条件で
provider送信前に検証する。未対応・失効・欠落・不成立なら送信を止め、理由と対応するページを保持する。
有効化は各入力の読取成功を保証せず、失敗・再試行時の補正値とimmutable snapshotを維持する。

新しい資格APIの `safetyStages` は段階別の診断であって資格証そのものではない。
記入画像がない時点の `DEFERRED_UNTIL_INPUT` / `NOT_EVALUATED` は成功を意味しない。
診断codesが空でも `READY_FOR_QUALIFICATION` にとどまり、証拠検証・資格発行を迂回しない。
GETでは保存された段階status/booleanを信用せず、所有証拠を確認してから段階情報を再生成する。
実行時enforcementを未実装のままgateを開放しない。旧単一ページと過去jobをこの契約へ暗黙移行しない。

2026-09-14のPhase 3では、`wsl-calc-print-grid-v1` に限る正式なreference/environment
qualificationを追加した。environment判定はlease v2を資格証とはせず、現在のhost/runtime/font/
worker/bootstrap/native dependency identity、prepared graph、非特権・network/host filesystem隔離、
no-new-privileges、read-only Windows font mount、resource limits、effective unit controls、worker/broker
core guardを合わせて評価する。認定scopeは固定prepared-reference invocationであり、host全体の完全な
dependency closure、全dynamic load trace、実入力scanを含めない。このscopeに必要な証拠が欠落・失効・
不一致なら `environmentQualified=false` とする。

同Calc geometry判定は、原本/layout/configからのplan・全binding再計算、全必要field四辺の独立vector
観測、PDF整数座標一致、所有raster/vector証拠、150dpi・整数scaleの数理的表現誤差境界を同時に要求する。
過去実測に合わせた閾値変更はせず、X方向は `0.5 + 150/72/2000` px以下、Y方向はそれに
page-height換算の厳密な上限を加えた値未満とする。文字可視性、実scan registration/crop対応、実筆画
clearanceはこの資格に含めず、provider送信前検証へ残す。

旧 `REFERENCE_ANCHORS_INSUFFICIENT` は診断から削除しない。上記Calc geometry資格が完全に成立した版だけ
activation判定の非blocking診断へ分離する。legacy/不明geometry、edge・binding・plan・coordinate・
raster/vector証拠の欠落または変更では従来どおりblockingとする。

正式recordはTemplateVersion ID/revision、原本SHA、printLayout SHA、geometry model、prepared graph、
renderer/reference/evidence SHA、environment identity、各issuerと全体のimplementation fingerprint、policy、
issuedAt/expiresAt、未解決blockerへ束縛する。GETは現原本・plan・所有artifact・graph・実装・現在環境・
lease freshness・geometry integrityを読取専用で再評価し、補完・延長・DB修復をしない。変更・欠落・期限切れ
では再qualificationを要求する。この資格発行だけではDRAFTをACTIVEにせず、承認成功経路またはOCR受付gateを
開放しない。

2026-09-14のPhase 4では、既存TemplateVersion承認APIに限り、`printLayout`を持つDRAFTを上記の
正式資格へ接続した。paged版は要求時のprint revisionを必須とし、既存のtemplate mutation lockと
`BEGIN IMMEDIATE`の内側でPhase 3のGETと同じvalidatorを実行する。現在の原本/config/plan/bindings、
prepared graph、所有artifactとSHA、issuer/qualification実装、environment identityとlease期限を再検証し、
正式policy、environment/geometry両component、資格証、activation stageがPASSで未解決blockerがない場合だけ
既存の原子的なACTIVE切替へ進む。保存boolean、古いreport、fixture、期限延長を承認入力にしない。
printLayoutのないlegacy版は従来のmapping/document identity、layout verification/readinessを維持する。
paged版では旧`layoutVerified`/`xlsx-structure` booleanを偽装せず、正式paged資格をその代替承認条件とする。
承認は旧ACTIVEのARCHIVED化、対象版のACTIVE化、系列`active_version_id`、元準備jobの確定を1 transactionで
更新し、失敗時は全更新をrollbackする。ACTIVEになっても実入力stageは`NOT_EVALUATED`のままであり、
`PAGE_INPUT_IDENTITY`、`SCAN_REGISTRATION`、`CROP_CORRESPONDENCE`、`INK_CLEARANCE`とpaged OCR受付/provider
送信gateを開放しない。

2026-09-16の実Chrome検証では、Step 5が表示中TemplateVersion自身の正式資格をGETし、PASSかつ未失効で
blockerなしの場合だけ、取得済みcurrent revisionを`base_revision`として上記approve APIへ送信した。
承認成功後は既知の所有Template IDからauthoritative detailを再取得し、対象ACTIVE、旧版ARCHIVED、
`active_version_id`一致、他ACTIVEなしを確認してからUIを更新する。一覧summaryの省略された`versions`、
保存boolean、approve response単独をACTIVE表示の根拠にしない。対象実版のV2 ACTIVE化後もpaged OCR受付は
`PRINT_REFERENCE_NOT_VALIDATED`で閉鎖され、provider送信は発生していない。実証値とfrontend修正は
[GUI Migration Phase 5](verification/gui-migration-phase5-20260916.md)を参照する。

送信前の `CROP_CORRESPONDENCE` は、受付全ページ検証後、当該page/config/attemptと
所有全体画像・参照画像のSHA/保存範囲を再確認して行う。期待region集合・padding込みbboxと、
全5variantの実crop画素を照合する。抽出結果や保存PASS booleanだけでproviderを生成しない。
不一致は送信前に拒否し、ページ別の理由を保持する。検証成功もscan registrationの局所誤差・
実筆画clearance・本番資格の代替ではない。詳細は[送信前crop照合](verification/paged-crop-correspondence-20260913.md)。

`qualified` / `ocrReady` / `productionReady` / `environmentQualified` / `productionQualified`等の保存booleanだけでACTIVE化・production paged OCRを許可しない。認定対象のidentity・evidence SHA・失効条件と未解決条件を再検証する。fixtureのgate置換やmock通過は本番環境の証明ではない。

認定が不成立なら本番gateをfail-closedに維持する。renderer isolation / container設計は別infraタスクとして保留できるが、保留を理由に拒否を緩めない。再開時は実環境で正式なqualificationを行い、§10の変更手順と回帰検証を満たしてから本番開放する。

### 12.7 Renderer隔離とnative処理

2026-09-12に本番化と印刷用PDF取得の明示要求、およびWSL依存導入の許可を受け、
印刷用PDFに限り`PRINT_PDF_RENDERER=wsl`で選択する別rendererを追加した。
固定入力のOffice変換・PDF構造検査をbubblewrapの非特権・ネットワーク分離namespaceへ収容し、
systemd cgroupの資源上限と所有者leaseによる終了を要求する。非隔離fallbackは許可しない。
Windows版のgeometry/font evidenceをLinux版へ流用せず、PDF生成成功で本番OCRのgateを解除しない。
2026-09-13に同じ隔離境界での固定FODS寸法読取と、明示選択時だけWindows system fontsを
固定readonly mountする診断を追加した。fontconfigの解決probeをOffice実描画中の全font/runtime
証拠と同一視しない。新printAdapterの明示版選択だけが原本保持の拡張コピーを利用できる。
旧planのadapter/小数倍率/geometryを自動更新せず、新geometry測定値は旧座標へ流し込まない。
同日の明示確定APIでは、DRAFT/revision/原本/configを照合し、保存済みページ範囲を維持した
新WSL geometryと整数印刷倍率を版へ保存できる。元canonical layoutと過去OCR snapshotは不変。
PDFと全ページPNGを同一の隔離変換から生成し、SHA・寸法・全件数を確認する。
renderer実行中はDB mutation lockを保持せず、生成後の再照合前に版を更新しない。
印刷条件が同じ意味づけ編集では確定ファイルを再生成せず、条件変更時は再確定を要求する。
このAPIの成功やprintFilesの存在はqualificationの代替ではなく、既存ACTIVE/受付gateを解除しない。
実装・現時点の検証・未完了範囲は[複数ページ本番化](production-paged-ocr.md)を参照する。

同日の追加で、WSL runtime imageのSHAを明示した呼出しに限り、root-ownedの読み取り専用
SquashfsからOffice/Python/Popplerと設定を使用できる。内容を保持したdescriptorで前後SHAを
検証し、private mount namespaceと従来cgroup/bubblewrap条件を維持する。
image欠落・変更・mount失敗からlive runtimeへfallbackしない。未指定の旧経路は維持する。
Windows fonts・host bootstrap・kernel等を含む完全な環境認定とは区別し、既存版/過去jobへ
自動適用しない。詳細と残条件は[WSL runtime image](wsl-runtime-image.md)を参照する。

固定runtimeのWindows fonts経路では、readonly font mountの全regular file集合を
変換前後に内容hashし、追加・削除・変更を拒否する。候補fontだけやsize/mtimeだけで
集合identityを代替しない。既存live経路/保存reportへ後付けしない。
この一致は実描画glyph・文字対応や完全な環境資格の代替ではない。
[font集合の検証](verification/wsl-font-directory-20260913.md)を参照する。

`PRINT_WSL_RUNTIME_ID`を明示した新しい印刷確定では、寸法測定とreference生成へ同じIDを渡し、
runtimeImageとfontDirectoryIdentityの完全一致を保存前に検査する。証拠欠落や不一致で
下書き・config snapshot・成果物を更新せず、現在設定を既存版/過去job/保存済み取得へ後付けしない。
これは[印刷確定の環境固定](pinned-print-preparation.md)であり、本番資格やgate解除ではない。

固定runtimeの新呼出しはworkerソースを実行専用snapshotにし、Linux側のprivateな
read-onlyコピーを実行する。隔離側の前後内容SHAと起動側のSHAを照合し、
寸法測定とPDF生成の`workerIdentity`も保存前に一致させる。
欠落/変更を許容せず、旧reportには後付けしない。host bootstrap全体の認定とは区別する。
実装と失敗注入検証は[worker identity](verification/wsl-worker-identity-20260913.md)を参照する。

WSLのOffice/PDF/font workerと入力を保持するbrokerは、入力の読取・解析より前に
process-localなcore dump抑止を要求する。Linux pipe型core handler向けのsoft/hard limit 1と、
CORE limit変更を拒否する検証済みseccomp filterを併用し、getter・他resource操作は維持する。
変更拒否・実効値・filter/library SHAを両processの証拠として新しい変換応答に記録する。
証拠欠落・不一致・未対応ABIでは変換応答を拒否し、非隔離fallbackや全WSLの設定変更はしない。
旧保存reportへ成功証拠を補完しない。worker実装変更で以前の参照資格を失効させる。
これは[クラッシュダンプ境界](verification/wsl-core-dump-boundary-20260914.md)の追加保護であり、
環境全体の資格発行・本番gate解除・geometryやOCRデータ契約の変更ではない。

固定runtimeを明示する新変換では、root bootstrapのPython検索treeを変換前後で内容hashし、
実行ファイル・検索ZIPの有無・stdlib全entry・file linkの解決先を含む一致を要求する。
isolated/no-site/no-bytecodeの実値、root所有と非書込条件、件数/bytes上限、読取中の変更を検査する。
未知の検索経路、installation外へのlink、未対応directory link、欠落したlink先は拒否する。
Office/glyph/vectorの新応答に `bootstrapPythonIdentity` を保存し、寸法測定・参照画像生成・
再描画の間でも一致を要求する。旧reportには後付けせず、getterで現在の環境を成功として補完しない。
この境界は[Python起動入力の前後照合](verification/wsl-bootstrap-python-enforcement-20260914.md)であり、
さらにv2では、固定のhost起動ツール・Python拡張・libseccompのELF起動時依存を
`nativeDependencies` として同じ前後照合・保存・再描画比較に含める。
固定loaderの `--list` はroot所有のinstallation内ELFだけに使用し、帳票を渡さない。
解決不能/未知の出力、loader環境変数、preload file、所有権/権限違反、集合/内容変更は拒否する。
内容hashと依存graphに結び付く[ELF起動依存の照合](verification/wsl-native-bootstrap-enforcement-20260914.md)であり、
動的dlopenの完全trace・systemd設定・Windows/WSLの完全な環境認定を代替しない。

新しいWSL資格検証では、bootstrap証拠を持つ保存版について、帳票を渡さない独立した
環境照会で選択runtimeの実体SHA・全font集合・worker・Python/ELF起動依存・broker core guardを
再取得し、保存された参照画像の実行条件との完全一致を要求する。
この一致の証拠は版・所有artifact graphに結び付け、24時間の上限付きで別のJobArtifactへ保存する。
GETでは所有者・SHA・版・期限を先に検査し、その後に現在の実体を再読取する。
期限切れ・未来時刻・graph変更・現物不一致を、保存booleanや期限延長で通さない。
旧reportに新証拠を後付けせず、GETでDBを更新しない。
これは [環境identityの期限付き照合](verification/wsl-environment-identity-lease-20260914.md) であり、
scopeは `identity-freshness-only`。完全な環境認定証ではなく、これだけでACTIVE/送信を許可しない。

新規lease v2はWindows build/UBR/DisplayVersion、architecture、WSL launcher実体SHAと
version応答の生bytes SHAも記録し、Linux照会前後・GET再検査で完全一致を要求する。
旧leaseへのhost情報の補完はしない。これは資格記録取得時のhost更新検出であり、
元の帳票変換時のhost状態やOS依存全体を遡って認定しない。

保存済みWSL実行報告の読取では、installation identityの一致だけで隔離条件を認めない。
非特権UID、loopbackのみのinterface、通信拒否、no-new-privileges、host領域非公開、
固定したmemory/swap/pids/CPU上限、Windows font mountのreadonlyを型も含めて照合する。
新しいbootstrap証拠を持つreport、または隔離観測を一部でも持つreportでは全項目を要求する。
寸法測定と参照画像の片側だけから観測を削除して旧形式扱いに戻すことも拒否する。
観測のない旧reportは補完せず、現在の環境leaseを発行しない。
固定runtimeの新変換応答、保存graph、再描画比較、環境lease保存/読取へ同じ検査を適用する。
これは[実行時隔離証拠の検証](verification/wsl-execution-evidence-20260914.md)であり、
環境全体の資格発行・native動作全体の認定・ACTIVE/受付の成功経路ではない。

新しいCalcモデルの明示印刷確定では、同じread-only UNO documentから列幅・行高と
単一セルrange Sizeを観測し、そのdocumentをFODSへ出力する。観測は出力前後で一致させ、
要求したシート/行列prefix・原本SHA・FODS SHA・固定runtime/worker/fontに束縛する。
FODSだけでは判別不能なuint16桁あふれを、独立したwide寸法と累積座標の整数丸めで拒否する。
UNO欠落からCLI再読込へfallbackしない。要求prefixは行列合計20000以内とし、固定I/O・
network/resource/lease隔離を広げない。旧下書きの閲覧に新証拠を後付けせず、Calcの参照資格
検証には独立観測を要求する。これは寸法照合であり、本番許容値・環境資格・crop/inkの認定や
gate解除ではない。[同一読込の寸法観測](verification/uno-same-import-20260914.md)を参照する。

固定runtimeの新しいOffice/glyph/vector変換では、所有unitの時間・終了・file-size等の
実効設定をbrokerで確認するまで、子は入力解析を待つ。成功記録をroot-owned directoryへ
atomicに公開し、子はreadonly mount・file不変・実RLIMIT_FSIZEを検査する。
欠落/不一致/起動失敗/時間切れは解析前に停止し、所有者切断時の既存停止境界も維持する。
保存/bridge/再描画/leaseのunitControlsは完全な検証を要求し、旧reportへ成功を補完しない。
これは[解析前の実効設定検査](verification/wsl-unit-admission-20260914.md)であり、
環境全体の資格発行・geometry認定・ACTIVE/受付の成功経路を代替しない。

WSL印刷確定版の参照検証は、その版に保存した原本・印刷XLSX/PDF・測定report・全ページ画像の
artifact所有者と内容SHAを照合する。Windows版Officeで再生成して代替しない。
検証実装/版/成果物の変更で失効し、保存前の再照合と既存geometry/crop条件を維持する。
これは[保存成果物の参照検証](prepared-reference-qualification.md)としてPhase 2証拠を構成し、
Phase 3の正式issuerはこの所有証拠を現在条件で再評価する。保存booleanや成果物一致だけで
environment資格へ昇格させず、資格が成立してもACTIVE/受付gateを解除しない。

固定環境を記録したWSL版の明示参照検証では、保存印刷XLSXを同じruntime IDで再描画し、
入力/環境条件と全ページの順序・寸法・画像bytes/SHAを照合する。
比較用画像は同じsource jobの別artifactとし、元印刷成果物/意味づけを置換しない。
GETでの証拠再検査にOffice実行を含めず、保存済み一致booleanだけでは通過させない。
詳細は[再描画比較](prepared-reference-replay.md)。これは環境/字形/crop資格の代替ではない。

固定環境を持つ WSL 版の明示参照検証は、所有された保存 PDF を専用 glyph helper に渡せる。
PDF/font parse は固定依存・read-only code/input/font 集合・network/cgroup/lease 境界内に置く。
全使用文字 code の Unicode・source/embedded glyph・metrics/hints を照合する。
結果を版・印刷成果物 graph と同じ source job の別 artifact に束縛し、GET で所有者・内容 SHA・
入力・実装 identity を再検査する。表示 boolean だけで未検証条件を解除しない。
この一致で除けるのは glyph の未検証条件だけで、宣言 face と異なる実 face には fallback の
未認定条件を残す。環境全体・文字位置・可視性・crop・手書き OCR の安全性へ流用しない。
既存版/原本/印刷 PDF の暗黙置換、旧 report の補完、本番 gate の解除は行わない。
詳細は[字形検証](renderer-glyph-validation.md)を参照する。

固定環境を持つWSL版では、各fieldの四辺と最終PNGの観測を版所有artifactとして追加保存できる。
不足する罫線の測定コピーは元印刷ファイルから再生成可能な最小変更に限定し、文字プログラム・
font resource・用紙・環境の一致、測定線近傍以外の画素不変を要求する。原本・印刷成果物・
保存座標やmappingを置換しない。GETでは所有者/内容/実装/入力graphを再検証して計算し直す。
元のanchor診断を削除せず、座標観測 `MEASURED` や暗い画素の存在だけで可視性・本番の許容値・
環境を認定しない。詳細は[項目別四辺の保存証拠](verification/prepared-field-geometry-20260913.md)。

細い破線の観測には、隔離内で純粋なstroke命令1個だけを抑止したPDF測定コピーと、
全ページRGB差分を追加できる。元のdecoded contentは当該1 byte以外保持し、用紙・数値・
文字/font resourceを照合する。元PNGとの画素identityと対象外差分を再検査し、GETでも
保存summaryではなく所有されたhelper証拠から再計算する。既存raster診断は消さない。
subpixelのpaint componentと実線交点候補は別に記録し、交点候補だけで成功としない。
破線片に描画寄与がなく、他の実線の独立したink/contrast観測でも裏付けられない場合は
未検証を維持する。これは[描画寄与の観測](verification/stroke-differential-20260913.md)であり、
資格発行・本番許容値・cropや文字の可視性の認定ではない。

同じ線が複数欄を横断する場合は、明示field/bindingsと辺の範囲に限った
subpixel描画寄与を追加検査できる。欄外componentを当該欄の要件に含めないが、
既存端点guard・画素/contrast閾値は広げず、欄内に未観測componentがあれば拒否を維持する。
実測したink欠落や差分証拠の欠落・不完全・ゼロを救済しない。元の全線診断を保存し、
GETで所有証拠から再計算する。これは[欄別観測](verification/field-raster-scope-20260913.md)であり、
独立したgeometry誤差・可視性・環境・本番資格を証明しない。

独立した単一segmentのsubpixel実線では、同じ固定helperの校正PDF生RGBを再検証した場合に限り、
Splashのstroke adjustmentから予測した画素位置を別の補助観測として使用できる。
校正の入力/code/runtime identityと全256ケースを要求し、元の全線観測・geometry誤差は保持する。
画素探索・端点guard/閾値の拡大・座標の変更はせず、予測画素にもinkがなければ未検証を維持する。
校正なし・不一致、破線・複合path・curve・clip・shear等を推測で救済しない。
保存/GETでは所有helper証拠の生RGBから再計算し、保存summaryを成功根拠にしない。
これは[細線の丸め位置の観測](verification/splash-stroke-quantization-20260913.md)であり、
環境資格・本番許容値・cropや文字可視性・ACTIVE/受付gateの認定を代替しない。

保存済みCalcモデルの参照検証では、原本に束縛された寸法・ページ/field範囲から整数グリッドを
再計算し、固定した整数point PDF出力と観測四辺の数値同一性を追加照合する。
保存rectangleの丸め不一致・min=1拡張・未観測/曖昧な辺・未対応座標系を成功扱いにしない。
保存/GETとも所有vector/rasterから再計算し、計算/照合実装のSHA変更で旧資格を失効させる。
旧geometry・座標・意味づけを移行せず、既存診断の削除や画像fit、許容値拡大を行わない。
これは[整数PDF座標との対応](verification/calc-pdf-coordinate-precision-20260914.md)であり、
数値一致だけで本番許容値・可視性・環境・crop・ACTIVE/受付を認定しない。

AppContainerの通信拒否probeが通っても、実Officeの変換・IPC・filesystem・lifecycleが同じ隔離条件で成立する証明にはならない。Job Objectの資源制限とnetwork isolationも同一視しない。隔離失敗時に無制限processや広い権限へfallbackしない。

Office互換性のためのbinary patch・hook・独自再ビルドは通常修正として行わない。必要性、隔離への影響、配備・再認定方法を別設計として提示し、明示的な変更範囲とする。

未信頼PDFのnative parse / raster失敗をbackend本体へ波及させない。新reference rendererのPDFium helper process境界を維持し、無条件にbackend内native処理へ戻さない。helper自身のnetwork・資源・入出力・依存関係も認定対象とする。timeout・cancel・親終了時は所有processと一時領域だけを回収し、他jobやユーザーのOffice processを終了しない。

### 12.8 Geometry誤差とcrop安全性

§12.6の承認済み二段階契約では、以下の実筆画・scan registrationの検査は実入力を得た
OCR実行時の責務である。テンプレート側のcrop領域定義・対応・参照geometryの検証は有効化前に残る。
検査の時期を分けても、clearance証拠の欠落をPASSに変えてはいけない。
自動的に筆画安全性まで認定する経路では、その証拠なしにproviderへ送信してはいけない。
人間による全ページ確認を前提とする候補OCRの送信条件は、後続の§12.9で明示的に分離する。

geometry PASSはcrop safety PASSではない。field寸法、罫線・筆画、内容と境界・隣接内容とのclearance、paddingを区別して評価する。独立したclearance証拠がなければ未検証とし、空の基準画像や人工fixtureだけから実手書きの安全性を推定しない。padding増加や閾値緩和でgeometry誤差を隠さない。

`logical geometry → reference image`の誤差予算と、`reference image → scan registration`の誤差予算を分離する。前者のずれを既存X/Y画像合わせで吸収して認定せず、後者を未測定のまま実OCR精度が保証されたと扱わない。DPI間は物理単位とraster誤差を区別し、数値の根拠・対象条件はPhase evidenceに保存する。

### 12.9 人間レビューを必須とする候補OCR受付 — 2026-09-21

複数ページから文字候補を生成し人間が確認する製品ゴール、および最短開放案への
「実装開始して」の承認に基づく、§10の意図的な新paged受付契約の変更である。
旧単一ページ・既存受付snapshot・認識器・位置合わせ閾値・矩形・mappingを変更しない。
過去のPhase報告にある「受付は常時閉鎖」は当時の状態として保持する。

- catalogとacceptは同じvalidatorでACTIVE、現layout SHA、全ページの対応項目、
  現在の正式reference/environment/geometry資格を要求する。acceptはmutation lockと
  transaction内で再検証する。保存booleanや旧`layoutVerified`で資格を代用しない。
- 原本・plan・所有artifact・prepared graph・実装・現環境・lease期限は既存正式資格validatorで
  再検証する。失効・不一致・DRAFT・ARCHIVEDは拒否し、資格を自動更新しない。
- 新受付bundleには`paged-reviewed-candidates-v1`、`reviewRequired=true`、
  `inkClearanceVerified=false`と受付時の資格binding/期限/report SHAを保存する。
  過去jobへ新しい資格・policyを後付けしない。retryは既存immutable snapshotを使う。
- `PAGE_INPUT_IDENTITY`、実scan registrationとその失敗拒否、provider生成前の
  `CROP_CORRESPONDENCE`、association、文書全体API予算は引き続き必須である。
  対応不明・位置合わせ失敗・crop改変を人間レビュー予定という理由で通さない。
- **変更する条件は未実装の全筆画clearance自動認定を候補OCRの前提にしない点**である。
  `INK_CLEARANCE`の未検証診断や正式資格の実入力`NOT_EVALUATED`は削除・PASS化しない。
  資格のstage記述は全入力安全性の認定要件として維持し、このcandidate policyは
  その完全認定ではない。空reference・fixture・crop画素一致を筆画完全性の証拠にしない。
- 入力画面と専用レビューに未認定範囲を明示する。結果は未確定候補として公開し、
  各ページの切り出し画像・値を確認/必要な補正後、全ページの明示確認とrevision付き確定を
  要求する。未確定のXLSX出力、自動確定、旧単一結果へのPage 1転用を追加しない。

変更前後の契約テスト、正式資格失効/改変/版状態の拒否、snapshot・crop・review/confirm/outputの
回帰検証を行い、fixture通過と実OCR品質は分けて報告する。

### 12.10 環境更新後の候補OCR準備確認 — 2026-09-21

正式資格の完全再発行と、人間レビュー前提の機能開放を分離する承認に基づく、§10の
意図的な有効化/受付条件の追加である。§12.9の正式資格経路は保持し、以下の別経路を追加する。
過去の正式資格をPASSへ書き換えたり、環境hashの比較を無条件に省略したりしない。

- `paged-candidate-readiness-v1`は、固定runtimeと所有印刷用XLSXの明示的な再描画を要求する。
  旧描画との差分を許すのは`bootstrapPythonIdentity.nativeDependencies`だけである。
  両実行の証拠を検証し、runtime・font・worker・Python tree・隔離/資源制限・core guard等の
  残りの実行identityは完全一致を要求する。無制限実行へのfallbackはない。
- 新しい全ページPNGのSHA・サイズ・順序が保存済み基準画像と一致することを要求する。
  原本/config/layout/print plan/prepared graphと各所有artifactを再検証し、保存済みの
  glyph/geometry測定証拠を現実装で再計算する。過去のPASS booleanを根拠にしない。
  座標誤差・画素差の許容値を広げず、異なる基準画像・mappingを自動採用しない。
- 別の所有artifactに新描画証拠、許容した環境差分、現host、実装fingerprint、版/revision、
  発行/期限を保持する。期限は24時間。GET/有効化transaction/受付transactionでは
  現在の環境・原本・artifact・版・実装・期限を再照合するが、Office再描画は行わない。
  環境観測自体のコストは残る。検証中の変更・期限切れは拒否する。
- 結果は`CANDIDATE_READY`、`qualified=false`、`qualificationIssued=false`、
  `reviewRequired=true`であり、正式認定とは区別する。保存booleanだけでは利用を許可しない。
  有効化/受付は同じvalidatorを使い、受付時にcandidate証拠のbindingをsnapshotへ固定する。
- 準備確認の発行はDRAFT/revision照合下で行う。保存layout、draft内容、原本、print artifact、
  mapping identity、geometry、旧正式資格証を変更しない。版fork時はcandidate参照を引き継がない。
  新しい正式資格の発行に成功した場合のみcandidate参照を外し、正式資格を正本に戻す。
- ACTIVEの期限切れ証拠を自動更新する機能は追加していない。既存の編集用DRAFTからの
  準備/検証/承認手順を用いる。旧ACTIVEの不変性を回避して更新しない。
- 証拠不足、native以外の環境差、異なる描画は引き続き停止する。初回のgeometry/glyph検証を
  省略する経路ではない。正式資格経路・既存診断は削除しない。

実入力のpage identity、registration、crop correspondence、association、API予算、補正revision、
全ページの明示確認と未確定出力拒否は§12.9どおり維持する。文字認識精度や筆画完全性は保証しない。
実装と実環境の検証は[候補OCR準備確認](verification/candidate-readiness-20260921.md)を参照する。

### 12.11 候補準備と通常利用の検証分離 — 2026-09-21

§1.2の通常運用コスト方針と、候補準備後の画面表示・有効化・catalog・OCR受付が
geometry/glyph再計算とWSL環境照会を繰り返していた実装の再発防止に基づく、§10の意図的な
候補利用契約変更である。明示的な候補準備では§12.10の再描画、全ページ画像一致、
geometry/glyph再計算、現在host/runtime確認を引き続きすべて実行する。

- `paged-candidate-readiness-v2`は、準備時に完全検証した版・原本・print plan・実装・
  prepared graph・renderer report・replay・geometry/glyph証拠の参照を、版所有の候補artifactへ固定する。
- 通常の資格状態GET、有効化、catalog、受付では、版/revision、layout・plan、config・実装fingerprint、
  候補artifact本体、原本XLSX、renderer reportの内容SHA、全参照artifactの所有者・登録SHAを照合する。
  geometry/glyph証拠の再計算、replay画像の全再読込、WSL/hostの現在照会、Office再描画は行わない。
- 候補の利用期限は固定24時間ではなく、対象版・原本・layout・print plan・config・対象実装・
  候補artifact・証拠参照のいずれかが変わるまでとする。準備時の24時間値は環境観測の記録として
  保持するが、保存済み基準画像を使う通常OCRの期限には流用しない。
- 既存`paged-candidate-readiness-v1`はDBやartifactを書き換えず、旧発行実装fingerprintと
  保存済みreplay/差分記録を検証してv2利用結果として読む。旧証拠へ新しいPASSを後付けしない。
- OCR受付transactionでは、実際にsnapshotへ複製する各基準ページPNGの内容SHAを引き続き再確認する。
  受付後の実入力identity・registration・crop correspondence・association、全ページレビュー、
  revision付き補正、未確定出力拒否は変更しない。
- 準備後にrenderer/WSL環境だけが変わっても、固定済み基準画像・mapping・crop定義は変更されないため、
  通常利用を再準備へ戻さない。新しい印刷準備や正式資格発行で現環境を使用する場合は、
  その操作の完全検証を改めて要求する。

候補artifact・原本・renderer report・版bindingの改変は通常利用でも拒否する。
軽量化を理由にreference PNGの受付時SHA確認や実入力検査を省略してはならない。

### 12.12 新規テンプレートの通常OCR準備 — 2026-09-22

「通常利用者に資格審査を操作させず、GUIでページ範囲を指定し、OCR結果を確認・修正する」
という要求と実装承認に基づく、§10の意図的な有効化/受付契約の変更である。
§12.9〜§12.11の正式資格・旧候補経路と測定記録は保持するが、通常OCRの初回準備には要求しない。

- `paged-candidate-readiness-v3`を追加する。保存済みの所有原本、config、layout、print plan、
  全ページのreference画像と描画レポート、mappingのページ対応を既存validatorで確認する。
  DRAFT・revision照合・mutation lockを守り、版所有の準備artifactだけを追加する。
  geometry/glyphの測定証拠、過去の正式資格、Office再描画による同一性証明、現WSLの再照会は
  この準備の前提ではない。既存描画レポートの実行条件・隔離証拠の整合性は検査する。
- 戻り値は`CANDIDATE_READY`、`reviewRequired=true`、`pageBindingsVerified=true`、
  `qualified=false`、`qualificationIssued=false`、`geometryQualified=false`。
  対応関係の検証と印刷精度の正式認定を混同しない。未測定値をPASSへ書き換えない。
- 新policyと`prepared-pages-human-review-v1` markerをfingerprintへ固定し、原本、config、
  版/revision、保存planと所有artifact参照、準備artifactの変更で失効する。
  通常利用はv2同様に内容SHA/所有bindingを検査し、固定時間による再審査は要求しない。
  v1/v2の保存artifactをv3へ読み替えない。v3の検証意味を変更する際はpolicy/markerを更新する。
- 各ページに対応するenabled mappingが存在し、全mappingが重複なくページへ所属することを
  有効化前に既存`page_config`でも検証する。未対応の複合/mark設定、項目のないページを
  勝手に変換・削除して通さない。
- 通常UIは「このテンプレートでOCRを使う」の一操作で、必要なら既存の印刷見本生成、
  v3準備、既存approve、サーバーのACTIVE再取得を順番に行う。生成済み見本は再利用する。
  新たな描画には従来の隔離・資源制限を適用する。Officeなしの代用描画へfallbackしない。
  未保存状態では開始しない。失敗は現在画面に表示し、競合時の承認を自動再送しない。
- 印刷範囲は既存の区切り線ドラッグとページプレビューで指定する。mm入力は任意の詳細設定。
  見出しの所属変更はページ候補から明示選択し、IDや接続を推測で付け替えない。
  画像選択画面はcatalogの受付可否を正本にし、独立した正式資格GETで別画面へ戻さない。
- legacy単一ページ、実入力identity/registration/crop correspondence、association、
  API予算、受付snapshot、補正revision、全ページの明示確認、未確定出力拒否は変更しない。
  正式なgeometry/glyph認定を省く結果、印刷と基準矩形の一致や文字・筆画完全性は保証しない。
  利用者が切り出し画像と文字を確認し、必要な補正後に確定する製品ゴールを維持する。

今回の検証は明示要求に従い対象テスト・build・実画面確認に限定する。
全体テスト、実WSL再描画、実OCR品質評価は別であり、fixture通過をその証明にしない。
詳細は[通常OCR準備の検証](verification/reviewed-ocr-entry-20260922.md)を参照する。
