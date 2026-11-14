# OCR Safety Contract

## 1. 目的と適用範囲

### 汎用形式（OCR schema 2.0）

deploy版はOCR schema 2.0のみを受け付ける。値は `fields.<key>` に保存し、欄の意味は
受付時のmapping・reviewStructure・テンプレート設定で定義する。旧document/pedigree/
saleCalves/calvings/customFields構造は停止中の一回限りの変換で廃止する。旧構造や
欠落した受付snapshotを実行時に補う互換処理は設けない。画像帳票とXLSXページ帳票の入力経路の区別は残す。

- 変換は旧pathと新pathの対応を記録し、mappingId・region ID・crop・座標・列/行IDを保持する。
- 補正値・候補値・状態・alternatives・reviewReason・reviewItems・revision・確定日時を保持する。
  未設定かつ空の業種モデル初期値だけを除去する。手入力された値はラベル付きの汎用欄へ移す。
- 識別項目はテンプレートが指定する。全帳票共通の個体番号・10桁・一意性を要求しない。
- フィールド名から業種・単位・日付・前行の同上値を推測しない。同上は未確定候補として確認する。
  選択肢、数字桁数、連番は明示的な設定で扱う。
- 補正APIはfieldsとreviewItemsを受け取る。補正で欄集合、sourceRegionId、crop座標を変えない。
  revision競合は409、確定済み補正は拒否し、明示再編集を要求する。
- 確定はローカルtransactionで完結する。未確定XLSXは拒否する。画像帳票の出力は凍結した
  reviewStructure、XLSX帳票は受付時原本・adapter・bindingを使用する。
- Google認証・同期・検索・書類ライブラリ・業種候補辞書を起動、公開API、通常UIから除く。
- 検証は退避DBと保存済み実responseの比較、短いAPI integration、XLSX内容確認、frontend buildを
  優先する。廃止契約のテストは理由を記録して退避し、重複unitを追加しない。

この文書はOCRシステムの技術的契約の正本である。機能ごとの詳細仕様は`docs/`の各文書に置き、この文書と二重管理しない。

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
| template・rectangle / region検出 | `services/template_service.py` — `TemplatePreparationService.process`、`services/layout_service.py` — `LayoutService.detect` / `prepare_layout`、`services/template_authoring_service.py`、`layout_analysis/detector.py`、`segmentation/`、`template_recovery/` | layout、cell / region、ラベルと入力セルの対応 |
| 画像抽出・crop | `services/extraction_service.py` — `ImageExtractionService.process`、`extraction/region_cropper.py` — `RegionCropper.crop`、`image_processing/`、`extraction/` | alignment、前処理、padding込みcrop.bbox、fieldPreview |
| orchestration | `services/ocr_workflow_service.py` — `OcrWorkflowService.process` / `_create_provider` | provider生成・呼出し、DB・artifact保存 |
| request計算 | `services/ocr_request_preparation_service.py` — `OcrRequestPreparer.prepare` | mapping選択、region入力、request、初期mappedCells |
| provider / association | `ocr/openai_provider.py` — `OpenAIOcrProvider`、`_compact_request_ids`、`_build_request_manifest`、`_association_tokens`、`_expand_compact_item` | 送信画像、compact ID、token、応答検証・展開 |
| model / prompt / schema | `core/config.py`、`ocr/prompt_loader.py`、`ocr/transcription_models.py`、`domain/ocr_models.py`、`backend/resources/templates/generic-document-v1/`（root基準） | primary / fallback設定、prompt、wire / 内部schema。OpenAI転記はload_transcription_promptとcompact modelを使用 |
| response計算 | `ocr/deterministic_mapper.py` — `build_mapped_document`、`services/ocr_response_finalization_service.py` — `OcrResponseFinalizer.finalize` | field ownership、値・判定・警告、mappedCells、diagnostics |
| reviewStructure・表示 | `services/review_display_structure.py` — `build_review_display_structure`、`frontend/src/pages/ReviewPage.tsx` | 構造由来group、保存mapping、行移動 |
| 補正・確定・reopen | `domain/ocr_models.py`、`services/ocr_result_service.py` — `OcrResultService.correct` / `confirm` / `reopen`、`repositories/ocr_result_repository.py` | 原候補・補正・revision履歴・確定snapshot |
| XLSX出力 | `services/spreadsheet_template_output_service.py` — `SpreadsheetTemplateOutputService.snapshot` / `render`、`services/excel_export_service.py` | 受付時原本・セルbinding、凍結reviewStructure、確定revision |

`backend/app/ocr/**` と上表のデータ契約はOCR-sensitiveである。Reactやテンプレート編集でも、見た目だけか、保存layout / mapping / cell associationを変えるかで分類する。

現行 `OcrResultService` は取得・補正・再編集・ローカル確定・XLSX取得を所有する。
表示画像は `ocr_result_preview.py`、補正値の型は `ocr_result_types.py` に置く。
旧同期facade、外部同期service、業種別の文書統合は廃止した。

単一画像の主要経路は `JobRunner → PipelineJobHandler → DocumentOcrExecutionService.execute → ImageExtractionService.process → OcrWorkflowService.process`。新しい`jobPages`形式は同じjob lifecycle内で`PagedOcrExecution`へ分岐し、既存の抽出・workflowをページ別に利用する。request/response計算へHTTP・DB・filesystem責務を持ち込まない。依存テストは直接importと特定のfilesystem呼出しの検査であり、推移的依存の完全分離は保証しない。

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

別の帳票や別jobの値を、番号・ラベル・配列位置だけで統合しない。業種別の文書merge経路は廃止した。

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

### 3.6 スマホ撮影写真の明るさ補正と向き（2026-10-18・意図的な変更）

暗い・ゆがんだスマホ写真でも位置合わせできるようにするための意図的な変更。旧契約は「位置合わせ・切り抜きは
撮影した画素をそのまま使い、テンプレートの向きでだけ書類の輪郭を探す」、新契約は次のとおり。

- 単一画像・ページ別の抽出は `image_processing/photo_registration.register_document` を通す。最初の候補は従来と同じ
  向き・同じ輪郭正規化で、紙がテンプレートより暗い（中央値で20以上）か、むらが大きい（5〜95%点の差で20以上）
  ときだけ紙の背景で割って平坦化した画像を使う。その候補が不合格なら撮影したままの画像、さらに縦横を入れ替えた
  輪郭の90°・270°回転を試す。どの候補も既存の `TemplateAligner` の受入条件をそのまま通る必要があり、閾値は変えない。
- 輪郭検出用の縮小画像だけ、95%点が190未満なら明るさを伸ばす。
- 平坦化した候補で位置合わせできた場合に限り、位置合わせ済み画像へ雑音除去と輝度だけの濃度復元をかけ、
  手書き分離・記号判定・切り抜きはその画像から作る。ペンの色は保持する。
- テンプレートと同程度に白い入力は画素・変換・切り抜きとも従来と同一（複製DBの保存済み14ジョブで位置合わせ済み画像の
  SHA-256一致を確認）。採用した回転・平坦化・試行の一覧は品質情報 `photoRegistration`、失敗時は
  `registrationFailure.diagnostics.registrationAttempts` に記録する。
- mappingId・targetPath・sourceRegionId・保存矩形・padding・provider・model・prompt・schema・association・受付snapshotは変更しない。

### 3.7 たわんだ紙の写真の位置合わせ（2026-10-23・意図的な変更）

綴じたまま撮った、たわんだ紙の写真でも位置合わせできるようにするための意図的な変更。旧契約は
「帳票全体を1つの射影変換（＋ECCのアフィン補正）で合わせ、ECCスコア0.75以上を必須とする」、新契約は次のとおり。

- §3.6のすべての候補（向き・平坦化）が不合格のときだけ、同じ候補を同じ順で、続けて元の写真全体（輪郭の切り出しで
  たわんだ紙の端が切れるため）を、`image_processing/curved_registration.align_curved` で試す。従来成功する入力の
  画素・変換・切り抜きは変わらない（通常DBの単一画像15件で位置合わせ済み画像のSHA-256一致を確認）。
- 対応付け：相互一致のORB特徴点から「射影変換＋3次の滑らかな補正」（テンプレート→写真）を当てはめ、続けて
  120px四方の区画ごとに平行移動だけのECCで最大20px（印字の行間約50pxの半分未満）を3回補正し、平滑化して合成する。
  区画の照合はテンプレートの印字の近くの画素だけを使う（紙の端・隣のページのインデックスを除く）。
- 受入：一致数・inlier数・比率・残差・分布、全体の輪郭一致0.7、3×3区画と全読み取り枠の印字輪郭0.7は従来と同じ閾値。
  加えて写像の折り返しなし、補正の大きさ（対角の4%以下）、テンプレートの85%以上と各読み取り枠の70%以上が写真内。
  写真外の印字輪郭は根拠がないため輪郭一致の分母から除く。全体のECC相関は記録するが必須にしない（スマホ写真では
  照明・手書きで0.6〜0.7になり、位置のずれを表さない）。
- 写真の端で一部が切れた読み取り枠は `registrationDiagnostics.fieldsPartlyOutOfFrame` と警告
  `FIELDS_PARTLY_OUT_OF_FRAME` に残し、その切り抜きの写っていない部分は白になる（利用者が確認する）。
  採用した方式は `registrationMethod=CURVED_PAGE_ORB_LOCAL_ECC`・警告 `CURVED_PAGE_REGISTRATION`、試行は
  `photoRegistration.registrationAttempts`（`curvedPage`・`wholePhoto`）に記録する。
- 記録する `source_to_template` は射影変換の部分だけ（位置合わせ済み画像は滑らかな補正を含む）。下流の手書き分離・
  記号判定・切り抜きは位置合わせ済み画像（テンプレート座標）から作るため影響しない。
- 従来の平面の位置合わせ（§3.5の追加復旧を含む）にも、合格の前に読み取り枠ごとの印字輪郭0.7を求める（従来は
  追加復旧だけ）。読み取り枠では、縦の罫線と横の罫線（25px以上続く印字）の輪郭も別々に0.7以上を求める。罫線に沿った
  ずれ（横線は重なったまま列が横にずれる）を全体の一致だけでは見逃すため。3×3区画は従来どおり向きを分けない。
  満たさない平面の結果は不合格として次の候補（最後はたわみ方式）へ進む。
- mappingId・targetPath・sourceRegionId・保存矩形・padding・provider・model・prompt・schema・association・
  受付snapshotは変更しない。`register_document` を使う単一画像・ページ別の抽出に適用し（通常DBにページ別の
  jobはなく未確認）、新printLayoutの認定gate解除には使わない。
だけの帳票の幅742pxの欄で約4px）、不合格2件（最も強い弓なり）。

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

### 4.5 手書き抽出画像の同時送信（2026-10-18・意図的な変更）

手書きの時・分を読み、空欄の印字（斜線・括弧・項目語）を値にしないための変更。

- 旧契約：印刷の項目名（`printedLabelText`）を持たない記入欄は、元の色の切り抜き1枚だけを送る。
  受付時に `handwriting_png`（手書き抽出）を作り `openAiImageCount` には数えていたが、実際の送信内容には含めていなかった。
  そのため印刷の斜線・括弧・項目語（日付欄の「／」、区分欄の「( )」、連絡先の「電話」）が値として返り、
  丸で囲んだ選択肢と手書きの数字が同じ枠にある欄では選択肢だけが返った。
- 新契約：上記の欄（`candidate_type` なし、`handwriting_png` あり）に限り、元の画像の直後に
  `handwritingOnly:<compactId>` の文字と補助画像を1枚追加する。補助画像は、手書き抽出で墨のある画素を
  半径4pxだけ広げた範囲に元の色の画素を置き、それ以外を白にしたもの（`_restored_handwriting`）。
  抽出処理で欠けた線を元の画素で戻すため、記入の無い欄は真っ白になる。サイズが合わないときは抽出画像をそのまま使う。
  ASSOCバナーは元の画像だけに付け、補助画像には付けない。
- 指示文（`HANDWRITING_HINT_INSTRUCTIONS`）は補助画像を送る要求にだけ追加する。記入の有無は補助画像で判断し、
  文字の形は元の画像でも確かめる。記入が無ければ印刷があっても `s=b`。記入があれば丸で囲んだ印刷の選択肢と、
  年・月・日・時・分・番・号・斜線のような1文字の単位・区切りだけを値に含め、項目名の語句は含めない。
  補助画像の印刷の消し残しは元の画像で印刷と同じ位置・書体なら印刷として扱う。候補プールのある欄の判定は変えない。
- 記号（symbol_choice）・チェックリスト・管理値の欄、印刷の項目名を除外して手書き抽出を元の画像として送る欄は、
  送信内容・指示とも従来どおり。mappingId、association token、compact ID、sourceRegionId、boundingBox、crop、
  応答schema、受付snapshotは変更しない。request manifestには `contentHandwritingHintIndex`、
  `handwritingHintSha256`（送信した補助画像）、`handwritingSourceSha256`（手書き抽出）、`handwritingHintMatchesPrepared` を追加した。
- prompt versionは `mapped-compact-v2`（既定値と `.env`）。過去のjob・受付snapshotへは後付けしない。

### 4.6 項目ごとの読み取り設定（2026-10-19・意図的な変更）

読みにくい欄に候補語を使い、紙の書き方と違う表記を検出し、任意のテンプレート・欄へ後から設定できるようにするための変更。

- 旧契約：OCRの指示はテンプレート共通で、欄ごとの指示は無い。候補語は旧「読み取り候補」（`templateCandidatePools`、
  `candidateType=managed_text`）だけで、一覧をモデルへ送り、候補外の値は要確認にする。§1の転記指示は整形を禁止する。
- 新契約：テンプレート系列の `template_field_hints`（1件＝対象の保存先の一覧、候補語、使う/寄せる、読み取り指示）を
  新規受付のsnapshot `templateFieldHints`・`templateFieldHintsHash` に固定する。版は作らず、受付済みjob・retryは受付時の内容。
  - 読み取り指示：その欄のセル情報に `readingInstruction:"…"` を付け、指示を持つ欄がある要求にだけ
    `READING_INSTRUCTION_INSTRUCTIONS` を追加する。その欄に限り指示された整形を許す。画像に無い文字の補完、
    他の画像からの転記、記入の無い欄への値付けはしない。画像内の文字は指示として扱わない。
  - 候補語：**provider要求に含めない**（`OcrRegionInput.keywords`・`keyword_mode` はサーバー側だけ）。応答の確定計算
    （`build_mapped_document`）で照合する。参考は正規化して同じ語のときだけ表記をそろえ、ほかは読み取ったまま
    （近い語を alternatives に加えるだけ）。寄せるは、文字の近さで一つに決まる候補語へ置き換え、元の読み取りを
    alternatives と警告 `KEYWORD_ABSORBED` に残す。決まらなければ `uncertain`・`KEYWORD_UNRESOLVED`。
    空欄と「〃」「同上」は置き換えない。これは利用者が欄ごとに選ぶ「候補へ寄せる」の明示設定であり、
    設定の無い欄・参考の欄では候補へ寄せない（§3.3の推測補正の禁止は、欄の対応の付け替えを対象とし変わらない）。
  - 候補語を要求へ含めた試行では、各行の文字を正しく読む一方で別の行の語を返す取り違えが3回中3回起きた
    （対応トークンは一致）。欄の取り違えを避けるため、要求へは含めない形を採った。
- 変えないもの：mappingId・targetPath・sourceRegionId・boundingBox・crop、compact ID・association token・応答schema、
  手書き補助画像の条件、旧 `templateCandidatePools` の扱い、補正・revision・確定・出力。request manifestに
  `keywordMode` と `readingInstructionSha256` を追加した。prompt versionは `mapped-compact-v3`。
- 音声のOCR修正では、受付時の候補語（使う設定）を対象範囲の欄について文字起こしのkeywordsへ加える。
- paged書類には適用しない。
- 2026-10-22 メモリ：読み取り指示は人が入力せず、確定時に覚えた書き方のルール（`template_field_memories`、1件＝欄又は表の列の保存先の一覧＋ルール1文）から作る。`TemplateFieldHintService.snapshot` が欄ごとのルール（使われた回数の多い順、300字まで）を「。」でつなぎ `instruction` にする。候補語の設定がない欄もメモリがあればhintを作る（keywordsなし）。学習は確定の保存後に別処理で行い、確定の成否・応答を変えない。候補語が寄せた値（`KEYWORD_ABSORBED`）・空欄化・空欄からの記入・選択欄・番号欄は対象外。同じ確定（job・revision）は一度だけ学習する。移行時に既存の読み取り指示を各欄のメモリ1件へ移した（`template_field_hints.instruction` 列は残すが読み書きしない）。

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

過去jobの表示は保存済みreviewStructure / fieldMappingsを基準とする。reviewStructureまたはrecordStructureがない結果は409で拒否する。現行TemplateVersionやラベルから勝手に対応を再構築しない。

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
| XLSX取得失敗 | 修正値・確定状態を保持し、原因を示して再取得できるようにする |

## 6. 補正・revision・再読取

### 6.1 補正と確定snapshot

`OcrResultRepository` は `raw_candidate_json`、`current_corrected_json`、`OcrResultRevision`、`ConfirmedResult` を区別する。表示のために原候補から補正済み値を作り直さない。

`correct` はREVIEW_REQUIREDを要求し、candidateを正規化・検証する。repositoryの `save_correction` はbase_revision条件付き更新、revision増分、source付き履歴を記録する。競合を上書きせず、mapping / sourceRegionId / boundingBox / field ownershipを保存・再取得まで照合する。

`confirm` はユーザーが見たbaseRevision、validation、受付時の識別項目と明示書類名を検査して確定snapshotを保存する。確定や正規化は不確実なOCRを自動的に正読へ昇格させる許可ではない。

`reopen` はbaseRevisionと確定snapshotの世代を検査し、jobをREVIEW_REQUIREDへ戻してconfirmed_atを解除する。OCR再実行はせず、result revisionと履歴を増分し、補正済み値・過去の確定snapshotを保持する。`confirm`も新revisionを作り、現在のConfirmedResultと追記専用のOcrConfirmationRevisionへ同時に記録する。

### 6.2 retry

`JobService.retry` は状態制約、受付入力・template snapshot、attempt管理に従う。
最新templateへ差し替えない。別OCRジョブは別の結果として扱い、旧業種のRESCAN_PAGE・
document-resolution・parity/indexによる文書mergeは公開APIと実行経路から廃止した。
`save_provider_result` の再処理履歴と補正履歴を区別する。新旧jobのID一致だけで値やcropを移さない。

## 7. ローカル確定とXLSXセル

### 7.1 現在の処理順と失敗

単一画像の確定はbaseRevision、validation、受付時の明示識別条件を検査し、XLSX生成を
成功させてから確定snapshotとjob状態を同じローカルtransactionで保存する。
`confirmed=true` の正本はサーバーのjob状態。Google / Drive同期と再同期endpointは廃止した。
XLSX生成失敗は未確定のまま値を保持する。commit後の応答消失は未確定と決め付けず再取得する。
確定済みの取得失敗だけで確認を解除しない。再編集は値・revision・履歴を保持する。

### 7.2 OCR値から出力セルへの対応

Spreadsheet template出力は受付時の `spreadsheetOutput` と `fieldMappings` snapshotを基準に、targetPathの値を `_destinations` と結合anchorへ解決する。UIの配列順や表示名で出力セルを推測しない。元XLSXと生成コピーを区別し、OCRテキストを式として扱わない既存処理を維持する。

`OutputLayoutTemplateService` のローカル保存layoutと `ExcelExportService` の出力も対応境界に含める。UX都合でbinding・sheet・元cell・確定revisionを変更しない。

### 7.3 Excel出力の元結合範囲（2026-09-05修正）

OCR identity契約は変更しない。Excel出力についてのみ、従来の左上座標の
列挙に加え、元XLSXの結合範囲を左上セルで代表する取込形式を許可する。
元XLSXから解決した範囲の和集合が、重複・欠落のない矩形の場合に限る。
空白に見える領域でも未選択セルを推測で追加してはいけない。

範囲展開を利用する新規出力snapshotはschemaVersion 2とし、各結合の
sourceRangesを保存する。元XLSXのSHA-256、sheet、保存したpartsから
出力時に一致を再検証する。現在はschemaVersion 2のみを受け付け、schemaVersion 1を拒否する。
parts、mappingId、targetPath、sourceRegionId、boundingBox、crop、
reviewStructure、補正保存およびprovider associationを変更しない。

出力コピーでは、選択した元結合範囲に完全一致する既存結合だけを解除して
再結合できる。従来拒否していた既存結合の包含を、この根拠がある場合に限って
許可する。部分交差、別結合との重複、数式消失、未承認の内部値消失は拒否する。
元XLSXは書き換えない。重複・欠落・部分交差・式消失の拒否は現在のschemaVersion 2でも維持する。

選択したliteral部品と元セル値の比較では、取込・編集時に除去される前後空白の差を
非空の文字列に限り許容する。比較だけであり、原本・保存partsを正規化しない。
内部空白、文字種・大小文字の差を同一視せず、数式とinput部品の内部値保護も維持する。
テンプレート名・特定ラベル・セル番地による例外は設けない。

ページ結合の版コピーでは、TemplateVersionServiceが付与する20桁のhex suffixを持つ
元セルIDも同じ元Excel座標へ解決する。その他の仮想分割IDへ範囲全体の所有を拡張しない。
ページ保存では取込元のcanonical矩形との一致も検証し、コピー後に元結合範囲を縮小しない。

この変更はExcel出力契約の拡張であり、OCR認識・物理identity契約の変更ではない。

### 7.4 汎用帳票の識別とXLSX出力（2026-10-04）

すべての現行テンプレートは `generic-document`。OCRの正本はschemaVersion `2.0` の
`fields` と `fieldLabels`、保存先は `fields.<key>`。旧階層path、単一documentIdentity、
CSV設定、業種辞書・年号・単位の推測は廃止した。旧補正payloadを無視して受理しない。
現行パッケージはformat/schemaVersion 2、旧package v1は拒否する。

識別情報は `documentIdentityFields` の配列に明示設定する。番号・名称・日付・組合せを
扱い、全帳票共通の番号・10桁・DB主キーの一意性を要求しない。明示されたdigits / exactLengthは守る。
書類ライブラリの検索・自動統合は行わず、読み取り後に設定した値とcropを利用者が確認する。
2026-10-16追記：確定済み結果だけを対象に、1利用者DB内で書類名・識別値による一覧と
構造化投影（`structured-document.v1`、`/api/v2/structured`、既定無効）を読み取り専用で提供する。
自動統合・推測値の保存は引き続き行わない。
2026-10-17追記（書類の系列、`mode=full`）：受付時に**明示的に選んだ**確定済みの単一画像jobを
前の版として、新しいjobを `document_series_links` で結び付けられる（`predecessorJobId`、
alembic `document_series_v1`）。条件は、前のjobが確定済み（CONFIRMED・確定revision一致）、
同じ `templateVersionId` かつ同じ `fieldMappingHash`、paged以外、前のjobに取消以外の後継がないこと。
前の版の確定revisionと確定snapshotのSHA-256を受付snapshot（`documentSeries`）とリンクに記録し、
後継の確定時に一致しなければ409 `SERIES_PREDECESSOR_CHANGED`。後継のある版の再編集・削除は
409 `SERIES_HAS_SUCCESSOR`。前のjobの値・確定snapshot・cropは変更しない。構造化一覧は確定済みの
後継を持つjobを除き、版番号を付ける。`mode=full`の後継は全欄を読み値を引き継がない。

**2026-10-18 意図的なOCR contract変更（書類の系列、`mode=blank_only`、新規の更新の既定）**：
§3.3「別jobの値を統合しない」・§6.2「新旧jobのID一致だけで値を移さない」の例外として、次の条件を
すべて満たす場合に限り、前の版の確定値を新しいjobへ引き継ぐ。旧契約は「別jobの値は一切引き継がない」、
新契約は「利用者が明示的に選んだ確定済みの前の版から、同一mappingの確定値だけを引き継ぐ」。
- 上記の受付条件（明示選択・同じ版とfieldMappingHash・確定revisionとSHAの固定）を満たす。
- 引き継ぐ欄は**受付時に固定**し、受付snapshot `documentSeries.carried`（mappingId→確定時の
  raw/normalized/status/alternatives/reviewReason）に保存する。対象は、前の版の確定値が同じフィールドキーで
  同じ `sourceRegionId`（=mappingId）に結び付き、statusが `readable` / `uncertain` の欄だけ。
  空欄、識別欄（`documentIdentityFields`）、mapping外の値（旧形式の追加項目など）は引き継がない。
- 位置合わせ・切り抜き・`mappedCells` の件数は全欄で従来どおり。providerへの要求からだけ引き継ぐ欄を除き、
  要求する欄の manifest・compact ID・association token の検査は変えない。引き継いだ欄は `mappedCells` で
  `sentToOpenAi=false`・`localResolution=carried_from_predecessor`、件数を `carriedFromPredecessorCount` に記録し、
  artifact `incrementalPlan` に引き継ぎ・要求のmappingIdを保存する。
- 引き継いだ読み取りの `sourceRegionId` は同じmappingId、`boundingBox` はこのjobの画像の切り抜きを使う
  （前のjobのcropを移さない）。以後の補正・確定・出力は通常のjobと同じ経路・検査を通る。
- 確定時、識別値が前の版の確定値・読み取り文字列のどちらとも一致しなければ、利用者の明示
  （`acceptIdentityMismatch: true`）なしに確定しない（409 `SERIES_IDENTITY_MISMATCH`）。
- 系列のないjob・pagedの結果は変わらない（保存済み画像の同一条件で変更前後を照合済み）。
未移行DBではリンクを作れず（503 `SERIES_NOT_MIGRATED`）、既存の確定・再編集・削除は従来どおり。

画像帳票は受付時の非空fieldMappingsと凍結reviewStructureから独立項目・見出し・明細表を
出力する。列はcolumn ID、値はfield ID / mappingId / targetPathの一致で解決し、ラベルや
配列位置から推測しない。全enabled mappingが一度ずつ対応することを検証する。
同名列、明示空セル、途中・末尾の空行、未mappingの保存済み手動項目を保持する。
構造不足や不一致は409で拒否し、出力時に対応を補充しない。

値はnormalized（nullならraw）を文字列として出力し、先頭ゼロ・改行・式文字列を保持する。
列を編成する際も既存relation.targetPathと欄IDを保持し、保存先を再生成しない。
原XLSXがある帳票は受付時adapterと原本SHAによる経路を優先する。
未確定出力拒否、確定revision、所有原本・paged cell binding・書式保持を維持する。

変換済みの既存候補・補正・確定値は再計算しない。今後の新規OCRでは、例えば `26 9 27` を
業種別の生年月日と見なして西暦へ推測変換しない。既存保存値 `2014年9月27日` は保持する。
対応関係の維持と、この意図した正規化仕様変更を区別して報告する。

## 8. 検証基盤と変更範囲別の検証

### 8.1 現在の検証方法

保存済みの実provider response、停止時の退避DB、同一受付snapshot、crop SHAを比較し、
短いAPI integrationとXLSX内容照合を行う。検証script・fixture・ログは製品外に保存する。
廃止した旧仕様の検証scriptとテストは退役させた。その旧成功件数を現行schema 2.0の回帰成功として使用しない。

### 8.2 選択する検証

| 変更 | 必須検証 |
| --- | --- |
| 文書 | 現行コード・参照パス・文書間整合性、diff確認 |
| frontend表示・操作 | `npm run build` と関係する実画面操作。保存・競合・確定・再取得等の必要な遷移 |
| backend / DB / lifecycle | 短い隔離API integration、対象静的検査。導入・起動・停止・再起動への影響は実入口で確認 |
| OCR / mapping / association | 同じ保存responseからrequest・manifest・compact ID・応答・値・判定・所有権を比較。重複・欠落・不明IDを検査 |
| crop / template / 補正 / 出力 | mappingId・targetPath・sourceRegionId・boundingBox・画像SHA、revisionと保存値、XLSXセル対応を比較 |
| paged / renderer | §12の所有原本・ページbinding・実入力・geometry・隔離gateを変更範囲に応じて実測。単一画像の成功を代用しない |

ユニットテストの追加・全件実行は既定にしない。短い上記検証で未確認の具体的な境界だけ、
最小の既存テストや追加検証を選ぶ。必要時のpytestは、リポジトリ外の未使用の一時ディレクトリをbasetempに指定する。
既存tempやユーザーデータを削除しない。

### 8.3 比較と報告

変更前のworktree・入力・受付snapshotと、変更後の同じ条件を記録する。意図しない値・ID・
crop・revision・確定状態の差分は回帰として調査する。意図した非互換変換は旧新仕様と理由を記録する。
未検証範囲、失敗、中断は成功と分ける。全体unit / E2Eを実行していなければ全件成功と表現しない。
必要な対応関係の回帰が残る場合は完了扱いにしない。

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

保存実response / Golden比較 → 短いAPI・出力integration → 未確認の境界検証 → 必要時のみ実APIの順で検証する。保存responseやmockで足りる条件にOpenAIを使わない。実API呼出しの既存許可・予算を守る。

model・prompt・schema・provider request・association方式変更、または固定fixtureだけでは評価できない精度課題では実API smokeを検討する。実施時は目的、API calls、image count、model、association mismatch、mapping誤対応、schema error、recognition-only errorを分けて記録する。未実測項目を0件と報告しない。

## 10. OCR改善と意図的な契約変更

### 見出しのない番号欄（2026-09-26）

記入欄の「番号を読む」は、自動採番の設定とする。
明示選択されたinputセルだけに `readingMode=number` を保存する。未設定の既存セルには
metadataを追加しない。保存先がない場合だけ、重複しない「番号」の項目と、見出しを
必要としない既存relationを追加する。設定済みtargetPath・cell ID・矩形は維持する。
保存時に番号欄をcanonicalのy、x、cell IDの順に並べ、1開始・1増分の
`sequenceNumber`をfield mappingへ固定する。region・受付snapshotにも保持し、
実行やページ抽出の際には数え直さない。これは文書ごとの欄番号で、文書間通し番号ではない。
番号欄はOpenAI/PaddleOCRの送信・検査対象から外し、画像内容に関係なく保存済み番号を
文字列のFieldReadingとして生成する。sourceRegionId・boundingBox・targetPathはその欄の
既存対応を保持し、mappedCellsに`automatic_sequence_number`と送信対象外を記録する。
番号欄だけならprovider呼出しは0回。番号を含む要求では帳票全体画像もproviderへ送らない。
①や先頭ゼロ等の印刷表記は転記しない。レビュー・手動補正・確定は既存経路を使用する。

2026-10-22：**意図的なOCR contract変更**（番号の数え方）。従来はテンプレート全体の番号欄をy、x順に数えていたため、左右に並ぶ2つの表の行が交互に番号付けされていた（例：左の表が12,14…30、右の表が11,13…29）。繰り返し部分の番号欄は表ごとに行順（recordGroupのorder）で1から数え、同じ見出し（parentLabelCellId）につないだ表は左の表から（高さが重ならなければ上の表から）続けて数える。表の外の番号欄は従来どおりy、x、cell ID順で別に数える（`LayoutService._sequence_numbers`）。番号は保存時にfield mappingへ固定し受付snapshotにも保持するため、受付済みjob・保存し直していない版は従来の番号のまま（「変更を反映する」だけでは数え直さない）。provider送信・crop・mappingId・targetPathは不変。
旧行番号roleへ変換しない。model・応答schema・association・crop・出力先の対応は変更しない。
以前保存したsequenceNumberのない番号OCR版・受付済みjobは従来の転記動作を保持する。
新方式を適用するには編集用の版でレイアウトを保存し有効化する。既存jobへ後付けしない。
別形式のAI汎用定義では番号設定を保持できないため、黙って落とさず既存の未対応拒否へ含める。

### 記入欄内の記号判定（2026-09-25）

単一ページ経路の `markRecognitionMode` に
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

### 単一ページOCRの文字読み取り選択肢（2026-09-29）

単一ページOCR画面の「文字読み取りも実行する」トグルを初期状態では隠し、
文字読み取りを必須とする。backend設定 `OCR_TEXT_RECOGNITION_TOGGLE_ENABLED` が `false` の場合、
frontendはトグルを表示せず、受付値を `ocrEnabled=true` に固定する。
`JobService.create_ocr_job` は同じ設定状態での `ocrEnabled=false` 受付を拒否する。
画像処理のみの経路やprovider処理自体は削除せず、既に受付済みのjobは受付時snapshotどおり実行する。

トグルを復活させる場合は `OCR_TEXT_RECOGNITION_TOGGLE_ENABLED=true` としてbackendを再起動する。
`/health` からその状態をfrontendへ渡し、従来の画像処理のみの選択を利用可能にする。
この設定はトグルを持つ単一ページOCR受付だけに適用し、paged OCR、mapping、crop、association、
補正・確定・出力、既存job snapshotの意味は変更しない。

### Template編集の版境界

mapping生成validationの失敗を空mappingへの置換保存に変換しない。
不完全なDRAFT layoutは保存できるが、既存fieldMappings・document identity・logical field
identityのpresent状態を保持し、layoutFinalized=falseとする。再取得は現在layoutの
mapping生成validationを正本にvalidForOcr=falseと対象fieldの警告を返す。
保持したmappingを読み取り確認・ACTIVE化・新規OCR受付で有効化しない。
frontendの未割当（保存先なし）と設定不完全（保存先あり・mark必須設定不足）を区別し、
共通fixtureでbackendとの判定一致を固定する。過去job snapshotは再検証対象にしない。
2026-10-05以降は統合authoring保存を使用する。設定ファイルの不変公開後に、DB内のlayout・config参照・品質・sidecar・準備jobのページ設定を同時にcommitする。失敗で残った未参照ファイルを有効な設定とは扱わず、旧設定ファイルを上書きしない。

公開済みTemplateVersionのlayout・field mappings・mask・汎用定義を直接変更しない。
ACTIVEからの編集は同じ原本のDRAFTを1つ作るか再開する。途中保存では版を増やさず、
承認時にactiveを原子的に切り替える。旧版は既存のARCHIVED状態へ移行するが内容は不変。
系列とlogicalFieldIdを継承し、mapping/cell/region/準備job/artifact identityは新版へ分離する。
logical identityのsidecarをprovider associationの代用にしない。
過去jobは受付snapshotを保持し、旧jobへlogical IDを後付けしたり新版から再構築したりしない。
詳細と意図的なlifecycle変更は [Template Versioning](template-versioning.md) を参照する。

### 契約変更の手順

汎用化・最適化に必要な破壊的変更も、次の条件で行う。変更を必要とする具体的な問題、旧契約と新契約、既存データへの影響を示し、
この文書と関係する仕様文書を同時に更新する。ユニットテスト追加は極力避け、保存response・実API・
短いintegration・出力比較で契約を確認する。変更の許可と検証結果は区別し、
未検証の変更を検証済みと報告しない。

OCRコア変更には、明示されたタスク範囲、再現可能な問題または測定可能な精度課題、変更前後比較、§8の検証が必要である。コード整理・抽象化・美観・一般化・speculative optimization・将来用途だけでは変更しない。

既存契約に戻す不具合修正は、不変条件の書換えを必要としない。発見した新しい境界違反は再現・原因を示して修正する。UXタスクで別のOCR不具合を発見しただけでは、作業範囲を広げる理由にはならない。

契約自体を変える場合は、以下をすべて行う。

1. 旧契約と新契約、必要性、影響範囲を提示する。
2. この文書を更新し、新契約を証明する回帰テストを追加・更新する。
3. Golden fixtureへの影響を確認し、必要な期待値変更は§9.2に従う。
4. 必要に応じてmigration・実API smokeを実施する。
5. 検証結果と残る不確実性を報告し、意図的なOCR contract変更と明記する。

不変条件を暗黙に緩めない。不確かな対応を推測で通過させない。

## 11. Baselineと完了報告

過去の実Chrome E2Eやassociation mismatch 0件の記録は、現在のrevision / worktreeへの保証ではない。

baselineは作業開始時のrevision・worktree・テスト・fixtureで記録する。

OCR-sensitive変更の完了報告には、次を含める。

- 変更ファイル、pipeline stage、影響するinvariant、原因と意図
- mapping identity、physical identity、provider association、crop / boundingBox、prompt / model / schemaの変更有無
- 変更前後の検証コマンド・結果、Golden / 保存response比較の結果と差分、backend regression、frontend検証
- 実OpenAI API使用有無・回数、実測したmismatch / mapping誤対応 / schema error / recognition-only error
- `git diff --check`、未検証範囲、既存失敗・環境制約

OCR動作を変更していない場合は「既存OCR contractを維持した」と明記する。同じ画像から意図したセル・mapping・検証済みresponse・レビュー・補正保存・出力までの対応を証明できることが完了の基準である。

## 12. XLSX複数ページOCRの安全契約

本節は新printLayout / paged OCRの追加契約であり、§3〜10のprovider・model・prompt・schema・association・mapping・補正・確定・出力契約を緩めない。

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
artifact生成成功を本番資格へ昇格させない。

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
現在は受付bundle schemaVersion 2のみを実行・レビュー可能とし、schemaVersion 1を拒否する。必要な出力snapshotがない全ページ確定も拒否する。
新形式の実行では所有コピーの範囲・SHAも最初のprovider呼出しより前に検証する。

確定は全ページ共通baseRevisionと全page IDの明示確認、既存の値/画像品質検証を要求する。
PagedConfirmationへ全ページの現在値・文書名・受付bundle SHAを一括保存し、jobのCONFIRMEDと同じtransactionでcommitする。
OCR値・statusを自動昇格せず、単一ConfirmedResultへPage 1を代入しない。
paged経路では確定・reopenもrevisionを増分する。単一OCRにも2026-10-05から同じ増分原則を適用する。pagedと単一の保存モデル・API・受付identityは引き続き分離する。
これによりreopen後の古い確定/補正要求を拒否し、過去の確定snapshotと全履歴を保持する。
確定の成否が不明な通信失敗では再取得まで状態変更/編集を止めるが、通常の補正保存では追加編集を維持する。
競合で保持した下書きは確定済み値として表示せず、明示reopen後に編集値として復元する。
書類ライブラリは製品対象外。既存単一ページ用APIの拒否と本番受付gateは維持する。
確定済みpaged結果の読み取り専用投影（2026-10-16、`/api/v2/structured`）は同じ確定判定
（status・revision・snapshot・documentNameの一致）を使い、確定行の存在だけでは投影しない。
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

#### ページ単位の手動編集

目視によるページ編集は[ページ単位の目視編集](page-authoring.md)に従う。
`draft-visible-cells-v1`は非表示行列を表示幅・高さから除き、セル内縮小を参考描画する
未認定の表示モデルである。保存済みlegacy/LOモデルを自動更新しない。原本の設定・セルを
書き換えず、元セルIDからページ表示座標を参照する。元セルの表示座標はlayoutへ保存しない。
保存は`PUT /template-versions/{id}/authoring?expectedRevision=...`でDRAFT/revisionを確認する。

- 通常矩形と結合partsへ展開した元セル集合は重複のない既存集合の部分集合とし、
  残存元セルのphysical ID・canonical座標・形状を保持する。
- 人間の区分・意味づけ・明示的な削除・同一ページ内の結合/解除を保存する。結合親は元partsの外接矩形と一致し、
  元XLSXから解決したpartsの範囲は欠落・重複のない連続矩形に限る。
- 物理セルの追加IDは検証済み結合親と保存partsの復元だけを許可する。field.sourcePartsにcellId/sourceRangeを保存し、
  表示位置や配列順から部品を推定しない。元範囲・内部値保護・ページ境界の検証をDB/config保存前に行い、
  不正要求で保存済みlayout/config/planを変更しない。ページ境界・transform・参考画像を保持する。
- 物理枠追加・任意の移動/変形・ページをまたぐ結合は拒否する。既存jobのcrop・mapping・provider association契約は変えない。
- ページfields/bindingsとlayout SHAを更新する。過去のqualificationは新版の証明に流用しない。
  Office adapterのhidden行列等の拒否条件、renderer isolation条件は変えない。自動区分・自動意味づけは行わない。

保存ページ上の仮想見出しは、`virtual=true` の名前付きmanual見出しに `pageRange` を保存し、そのセルの矩形座標だけを
当該保存ページ内の表示位置として解釈する。元セルのcanonical座標とは混同しない。

- ページ所属は版内の保存rangeに一致させ、配列indexや近傍セルから推定しない。
- 仮想見出し自身は元Excelセル、page.fields、field mappings、ラベルOCRの対象に含めない。
- 既存の物理IDから仮想への置換、仮想を含む結合、ページ不在・範囲外の配置は保存前に拒否する。
  ページ範囲・用紙変更や解除で所属・位置が無効になる場合も拒否し、対象見出しの明示的な削除を要求する。
- 改ページ保存要求の `pageHeadingMoves` で既存仮想見出しID・変更前range・変更先rangeを指定した場合だけ、
  所属変更を改ページと同一transactionで保存できる。名称・座標・親子接続・意味づけ・mappingは変えず、
  ID重複/不在・旧range不一致・物理セル・移動先不在・ページ外配置・revision競合・公開版への保存を拒否する。
  未指定の見出しを推測移動しない。新しい印刷成果物・正式資格は改めて取得する。
- 旧セルのJSONへ未指定pageRangeを追加せず、旧layout hash・geometryの暗黙移行を行わない。

#### XLSX取込のプレビュー寸法

新規XLSX取込のlegacyプレビューでは、同じ一時コピーをOfficeで読んだ行高・列幅を
`quality_json.spreadsheetPreviewGeometry`へ保存し、元セル由来の矩形座標に利用する。
このsnapshotがない既存版の復元は従来計算を維持する。snapshotがある版の復元は保存寸法と
原本SHAを照合し、現在のOfficeで再測定しない。セル数・役割・merged cellsを推測変更しない。
`spreadsheet-preview-office-v2`は、Officeのpt寸法を画像pxへ換算した自然倍率を上限として、
印刷可能領域に収まらない場合だけ縮小する。中央配置設定を反映し、小さい帳票を用紙幅まで拡大しない。
保存済みv1版は従来座標のまま復元し、既存版・jobの座標を暗黙に更新しない。
これはprintLayoutのgeometry evidenceやqualificationを代替しない。

#### 資格と有効化

reference imageの生成成功はqualificationではない。元XLSX、TemplateVersion、printLayout revision、geometry model / implementation、ページ別reference SHA、field geometry、renderer evidenceの対応を検証する。版・設定・画像・実装・環境の変更や証拠の欠落で有効性を失った結果を再利用しない。
依存ファイルのサイズ・時刻の一致だけで以前のSHAを再利用せず、同一metadataでも内容が変わる場合を拒否する。

Geometry / Reference QualificationとRenderer Environment Qualificationを区別し、両方の有効な証拠を要求する。network / resource isolation、実選択font、runtime依存、rasterizer、crop安全性・本番閾値の未認定条件を、座標精度のPASSで代替しない。hostにfontが存在することはOfficeの実選択fontの証明ではなく、fallback先を推測して認定しない。

paged経路の検証は二段階に分ける。

- 有効化時は、版/原本/全ページ参照画像のidentityとSHA、描画・座標・明示field/mapping、
  定義されたcrop領域とページ境界、隔離環境の有効な証拠を要求する。
  まだ存在しない記入画像のink clearanceは有効化の前提にせず、空のreferenceから成功と推定もしない。
- 実入力のpage binding/SHA、位置合わせ、crop対応と実筆画の安全条件は、受付snapshotの条件で
  provider送信前に検証する。未対応・失効・欠落・不成立なら送信を止め、理由と対応するページを保持する。
  有効化は各入力の読取成功を保証せず、失敗・再試行時の補正値とimmutable snapshotを維持する。

資格APIの `safetyStages` は段階別の診断であって資格証そのものではない。
記入画像がない時点の `DEFERRED_UNTIL_INPUT` / `NOT_EVALUATED` は成功を意味しない。
診断codesが空でも `READY_FOR_QUALIFICATION` にとどまり、証拠検証・資格発行を迂回しない。
GETでは保存された段階status/booleanを信用せず、所有証拠を確認してから段階情報を再生成する。

正式なreference/environment qualification（`wsl-calc-print-grid-v1`に限る）は次を満たす。

- environment判定は、lease v2を資格証とはせず、現在のhost/runtime/font/worker/bootstrap/native dependency identity、
  prepared graph、非特権・network/host filesystem隔離、no-new-privileges、read-only Windows font mount、
  resource limits、effective unit controls、worker/broker core guardを合わせて評価する。認定scopeは固定
  prepared-reference invocationであり、host全体の完全なdependency closure、全dynamic load trace、実入力scanを含めない。
  必要な証拠が欠落・失効・不一致なら `environmentQualified=false` とする。
- Calc geometry判定は、原本/layout/configからのplan・全binding再計算、全必要field四辺の独立vector観測、
  PDF整数座標一致、所有raster/vector証拠、150dpi・整数scaleの数理的表現誤差境界を同時に要求する。
  X方向は `0.5 + 150/72/2000` px以下、Y方向はそれにpage-height換算の厳密な上限を加えた値未満とする。
  過去実測に合わせた閾値変更はしない。文字可視性、実scan registration/crop対応、実筆画clearanceは含めない。
- 旧 `REFERENCE_ANCHORS_INSUFFICIENT` は診断から削除しない。Calc geometry資格が完全に成立した版だけ
  activation判定の非blocking診断へ分離する。legacy/不明geometryや証拠の欠落・変更では従来どおりblockingとする。
- 正式recordはTemplateVersion ID/revision、原本SHA、printLayout SHA、geometry model、prepared graph、
  renderer/reference/evidence SHA、environment identity、各issuerと全体のimplementation fingerprint、policy、
  issuedAt/expiresAt、未解決blockerへ束縛する。GETは読取専用で再評価し、補完・延長・DB修復をしない。
  変更・欠落・期限切れでは再qualificationを要求する。資格発行だけではDRAFTをACTIVEにしない。

`printLayout`を持つDRAFTの承認は、要求時のprint revisionを必須とし、template mutation lockと書込みtransactionの
内側で資格のGETと同じvalidatorを実行する。正式policy、environment/geometry両component、資格証、
activation stageがPASSで未解決blockerがない場合だけ、旧ACTIVEのARCHIVED化、対象版のACTIVE化、
系列`active_version_id`、元準備jobの確定を1 transactionで更新する。保存boolean、古いreport、fixture、
期限延長を承認入力にしない。printLayoutのないlegacy版は従来のreadinessを維持する。
ACTIVEになっても実入力stage（`PAGE_INPUT_IDENTITY`、`SCAN_REGISTRATION`、`CROP_CORRESPONDENCE`、`INK_CLEARANCE`）は
`NOT_EVALUATED`のままであり、OCR受付の条件は§12.9〜§12.12に従う。
画面は承認成功後に所有Template IDからdetailを再取得し、対象ACTIVE・旧版ARCHIVED・`active_version_id`一致を
確認してから表示を更新する。approve response単独をACTIVE表示の根拠にしない。

送信前の `CROP_CORRESPONDENCE` は、受付全ページ検証後、当該page/config/attemptと
所有全体画像・参照画像のSHA/保存範囲を再確認して行う。期待region集合・padding込みbboxと、
全5variantの実crop画素を照合する。抽出結果や保存PASS booleanだけでproviderを生成しない。
不一致は送信前に拒否し、ページ別の理由を保持する。検証成功もscan registrationの局所誤差・
実筆画clearance・本番資格の代替ではない。

`qualified` / `ocrReady` / `productionReady` / `environmentQualified` / `productionQualified`等の保存booleanだけでACTIVE化・production paged OCRを許可しない。認定対象のidentity・evidence SHA・失効条件と未解決条件を再検証する。fixtureのgate置換やmock通過は本番環境の証明ではない。

認定が不成立なら本番gateをfail-closedに維持する。renderer isolation / container設計を保留しても、保留を理由に拒否を緩めない。

### 12.7 Renderer隔離とnative処理

- 印刷用PDFは`PRINT_PDF_RENDERER=wsl`で別rendererを選べる。固定入力のOffice変換・PDF構造検査をbubblewrapの
  非特権・ネットワーク分離namespaceへ収容し、systemd cgroupの資源上限と所有者leaseによる終了を要求する。
  非隔離fallbackは許可しない。Windows版のgeometry/font evidenceをLinux版へ流用せず、PDF生成成功でOCRのgateを解除しない。
- 同じ隔離境界で固定FODS寸法を読み、明示選択時だけWindows system fontsを固定readonly mountする。
  fontconfigの解決probeをOffice実描画中の全font/runtime証拠と同一視しない。
  旧planのadapter/小数倍率/geometryを自動更新せず、新geometry測定値は旧座標へ流し込まない。
- 印刷ファイルの明示確定APIは、DRAFT/revision/原本/configを照合し、保存済みページ範囲を維持した新WSL geometryと
  整数印刷倍率を版へ保存する。元canonical layoutと過去OCR snapshotは不変。PDFと全ページPNGを同一の隔離変換から生成し、
  SHA・寸法・全件数を確認する。renderer実行中はDB mutation lockを保持せず、生成後の再照合前に版を更新しない。
  印刷条件が同じ意味づけ編集では確定ファイルを再生成せず、条件変更時は再確定を要求する。
  このAPIの成功やprintFilesの存在はqualificationの代替ではない。
- WSL runtime imageのSHAを明示した呼出しに限り、root-ownedの読み取り専用Squashfsから
  Office/Python/Popplerと設定を使用できる（[WSL変換用runtime image](wsl-runtime-image.md)）。
  内容を保持したdescriptorで前後SHAを検証し、private mount namespaceと従来cgroup/bubblewrap条件を維持する。
- 固定runtimeを明示したWindows font経路では、font directoryの全regular fileを変換前後に内容hashし、
  追加・削除・変更を拒否する。候補fontだけやsize/mtimeだけで集合一致を判定しない。
  印刷確定では寸法測定と参照生成の`runtimeImage`・`fontDirectoryIdentity`・`workerIdentity`の完全一致を
  保存前に検査する（[固定WSL環境を使う印刷確定](pinned-print-preparation.md)）。
- WSLのOffice/PDF/font workerと入力を保持するbrokerは、入力の読取・解析より前に
  CORE limit変更を拒否する検証済みseccomp filterを併用する。証拠欠落・不一致・未対応ABIでは変換応答を拒否する。
- Python起動入力（実行ファイル・stdlib全entry・link解決先）とhost起動ツールのELF起動時依存は、
  変換の前後で一致を要求する。未知の検索経路、installation外へのlink、欠落したlink先は拒否する。
- 環境照会で得た選択runtime・全font集合・worker・起動依存・core guardの一致証拠は、版・所有artifact graphに
  結び付け、24時間の上限付きで別のJobArtifactへ保存する。期限切れ・未来時刻・graph変更・現物不一致を、
  保存booleanや期限延長で通さない。
- 保存済みWSL実行報告の読取では、installation identityの一致だけで隔離条件を認めない。固定したmemory/swap/pids/CPU上限、
  Windows font mountのreadonlyを型も含めて照合する。実効設定をbrokerで確認するまで、子は入力解析を待つ。
- Calcモデルの明示印刷確定では、同じread-only UNO documentから列幅・行高を読み、シート/行列prefix・原本SHA・
  FODS SHA・固定runtime/worker/fontに束縛する。UNO欠落からCLI再読込へfallbackしない。
- WSL印刷確定版の参照検証は、保存成果物の所有者と内容SHAを照合する（[WSL印刷確定版の参照検証](prepared-reference-qualification.md)）。
  Windows版Officeで再生成して代替しない。固定環境を記録した版では、保存印刷XLSXを同じruntimeで再描画した全ページの一致、
  保存PDFの全使用文字のglyph照合、各fieldの四辺と最終PNGの観測を、版所有artifactとして追加できる。
  これらは保存summaryではなく所有されたhelper証拠から再計算し、環境資格・本番許容値・cropや文字の可視性・
  ACTIVE/受付gateの認定を代替しない。画素探索・端点guard・閾値を広げて観測を通さない。

AppContainerの通信拒否probeが通っても、実Officeの変換・IPC・filesystem・lifecycleが同じ隔離条件で成立する証明にはならない。Job Objectの資源制限とnetwork isolationも同一視しない。隔離失敗時に無制限processや広い権限へfallbackしない。

Office互換性のためのbinary patch・hook・独自再ビルドは通常修正として行わない。必要性、隔離への影響、配備・再認定方法を別設計として提示し、明示的な変更範囲とする。

未信頼PDFのnative parse / raster失敗をbackend本体へ波及させない。新reference rendererのPDFium helper process境界を維持し、無条件にbackend内native処理へ戻さない。helper自身のnetwork・資源・入出力・依存関係も認定対象とする。timeout・cancel・親終了時は所有processと一時領域だけを回収し、他jobやユーザーのOffice processを終了しない。

### 12.8 Geometry誤差とcrop安全性

§12.6の二段階の検証では、実筆画・scan registrationの検査は実入力を得た
OCR実行時の責務である。テンプレート側のcrop領域定義・対応・参照geometryの検証は有効化前に残る。
検査の時期を分けても、clearance証拠の欠落をPASSに変えてはいけない。
自動的に筆画安全性まで認定する経路では、その証拠なしにproviderへ送信してはいけない。
人間による全ページ確認を前提とする候補OCRの送信条件は§12.9で分ける。

geometry PASSはcrop safety PASSではない。field寸法、罫線・筆画、内容と境界・隣接内容とのclearance、paddingを区別して評価する。独立したclearance証拠がなければ未検証とし、空の基準画像や人工fixtureだけから実手書きの安全性を推定しない。padding増加や閾値緩和でgeometry誤差を隠さない。

`logical geometry → reference image`の誤差予算と、`reference image → scan registration`の誤差予算を分離する。前者のずれを既存X/Y画像合わせで吸収して認定せず、後者を未測定のまま実OCR精度が保証されたと扱わない。DPI間は物理単位とraster誤差を区別し、数値の根拠・対象条件は資格の証拠に保存する。

### 12.9 人間レビューを必須とする候補OCR受付（2026-09-21）

複数ページから文字候補を生成し人間が確認する製品ゴールに基づく、
§10の意図的なpaged受付契約の変更である。
旧単一ページ・既存受付snapshot・認識器・位置合わせ閾値・矩形・mappingを変更しない。

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

### 12.10 環境更新後の候補OCR準備確認（2026-09-21）

正式資格の完全再発行と、人間レビュー前提の機能開放を分離するための、§10の
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

### 12.11 候補準備と通常利用の検証分離（2026-09-21）

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

### 12.12 新規テンプレートの通常OCR準備（2026-09-22）

通常利用者に資格審査を操作させず、GUIでページ範囲を指定し、OCR結果を確認・修正できるようにするための、
§10の意図的な有効化/受付契約の変更である。
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

## 13. チェックリスト（2026-10-04）

`markRecognitionMode=checklist` は、紙のペン記入、印刷/セル文字のチェック記号、Excelセル内チェックボックスを対象とする。従来の automatic/manual/symbol の判定方法は変更しない。

- checklist は `mappingType=mark` / `markSelectionMode=multiple`。候補は1〜50件、同名不可。候補名に選択値の区切り文字 `・、,;` / 改行を含めない。
- 設定した欄全体のalignedColor cropを送信し、チェックされた候補ラベルを候補順に `・` で連結する。未選択は `blank` / null、曖昧・候補外・対応不明は `uncertain`。空の読取可能応答を未選択へ変換しない。
- プロンプトは checklist の画像だけ、手書きと印刷のチェック印を対象とし、選択値を `v` に返す。ASSOC、compact ID、返却対応検査は従来どおり。
- 補正は既存revision経路で保存する。確定前に受付snapshotの候補と照合し、判別不能・未知の候補・重複選択を拒否する。checkboxをfalseへ落として確定しない。
- ページ別実行は、従来のtextに加えchecklistだけを許可する。page field bindingから欄全体のrectangleを取得する。選択肢ID/値を保持し、選択肢rectangleは欄全体へ設定する。これは画像差分検出用の範囲ではない。その他のmark・複合項目の未対応制限は維持する。
- XLSX直接入力はACTIVEな単一ページSPREADSHEET_TEMPLATEに限定する。同じsheet/行列寸法/結合/記入欄以外の値を検査し、受付対象のセルだけを描画用コピーへ転記する。原入力xlsxとSHA、セル内checkbox数を記録する。原本を書き換えない。
- Excelのセル内checkboxは、OOXMLのFeaturePropertyBag→XFComplement→XFControls→Checkboxとboolean cellの対応を検証する。候補が1つのchecklist欄だけに割り当てる。書式のないTRUE/FALSEをcheckboxと推定しない。
- セル内checkbox原本の出力では、確定値をbooleanへ変換し、所有するsheetのcell XMLだけを変更する。他のZIP partと原sheet名を保持する。手動結合との併用は拒否する。通常の文字チェックリストは既存XLSX出力を使用する。
- 旧フォーム/ActiveX、外部リンク、数式を含む直接入力、paged XLSX直接入力は未対応として停止する。画像にしたチェック欄は通常の画像OCRで確認できる。paged rendererの資格・入力検査を緩和しない。

検証の範囲と残る制約は [チェックリストOCR](checklist-ocr.md) を参照する。実筆記・実撮影画像の精度は、合成検証画像やExcel描画画像の結果では証明しない。

### 13.1 選択肢の記入欄（2026-10-19・意図的な契約変更）

- チェックリストの選択肢に記入欄を1つ結び付けられる（`markOptions[].writeInTargetPath`、テンプレートの文字の記入欄の保存先。checklistだけ、1つの記入欄は1つの選択肢だけ）。未設定の選択肢は保存形式・hashとも従来どおり（キーを出力しない）。
- 規則（`domain/checklist.apply_write_ins`）：記入欄に文字があれば、その選択肢を選択済みとして「・」区切りの値に加える（選択肢の順序）。全未選択なら記入した選択肢だけの値になる。判別不能（uncertain）のチェックリストは変更せず人の確認に残す。記入欄自体の値・crop・mapping・状態は変更しない。
- 適用箇所：OCR直後（`ocr_response_finalization_service`）と、単一画像・ページ別の補正保存のたび。確定・XLSX・構造化データは保存済みの値をそのまま使う。

#### 13.1.1 記号の欄の記入欄（2026-10-20・意図的な契約変更）

- 「○ その他（　）」のような記号と記入欄を1つの項目として扱うため、記号の欄（symbol、○◎×△から1つ）にも記入欄を1つ結び付けられる。保存は同じ `markOptions[].writeInTargetPath` で、記号の欄では1つの選択肢（既定は「○」）だけが持つ（2つ以上は拒否）。未設定の欄は保存形式・hashとも従来どおり。
- 規則（`domain/checklist.flag_symbol_write_ins`）：OCR直後だけ、記入欄に文字があり記号が空欄（blank）なら、記号の欄を不確実（`WRITE_IN_WITHOUT_MARK`）にして確認メモを付ける。どの記号かは推測せず値は入れない。記号が読めている・記入欄が空のときは変更しない。補正保存では適用しない（人が空欄にした値を保持）。記入欄自体の値・crop・mapping・状態は変更しない。
- 確認画面では、記号・チェック欄と記入欄が同じ項目名につながっていれば（保存した結び付け、又は旧テンプレートでは同じ項目名セルにつながる記号欄1つと文字の欄1つ）同じ行に「記号｜記入」として並べる。表示だけの変更で、保存値・確定・XLSX出力の位置は不変。
- あわせて欄の出力名（mappingの `label`、OCR要求の `displayLabel` にも使う）は、つないだ項目名セルの文字→印字（結合した項目名）→表示名の順に取り、記号だけの名前とセルID等の内部IDを使わない（なければ「名称未設定の欄」）。出力名は下書きの保存時に作るため、受付済みjobと保存済みの有効版の設定は変わらない。確認画面のグループは、接続に見出しがなければ項目名セルの見出し（逆向きに引いた見出し線を含む）を使う（受付時の `reviewStructure` を作るときだけ、受付済みjobは不変）。

### 13.2 印刷された選択肢の読み取り（2026-10-21・意図的な契約変更）

選択肢の設定を簡単にし、選択肢に丸以外の記入（チェック・線・塗りつぶし）がある場合も扱うための変更。

- 旧契約：`automatic` は選択肢ごとの矩形の画像差分（`mark_detector`）で判定し、判定できない欄だけ欄全体を `mark_choice` として送る。`manual` は送らず `MANUAL_CHOICE_REQUIRED` で人が選ぶ。
- 新契約：`automatic`/`manual` とも、チェックリストと同じく欄全体の `alignedColor` crop を `candidateType=mark_choice`・候補＝選択肢の文字で必ず送る（`OcrRequestPreparer._mark_state` が UNCERTAIN を返し送信対象にする）。選択肢ごとの矩形は設定画面の表示とページ別の互換保存用で、画像差分の結果は `extraction.marks` の診断に残るだけで値に使わない。
  - 指示（pool文と要求全体の追加文）：印刷された選択肢に丸・チェック・線・塗りつぶしのどれで印が付いていても選択とし、選ばれた文字を候補順に「・」で連結して `v` に返す。印がなければ `s=b`。印が曖昧、または選択肢ではない文字が書かれているときはその文字を `v` に入れて `s=u`。印刷文字だけから選ばない。
  - 応答の扱いは従来の `mark_choice` と同じ（`deterministic_mapper._reading`）：候補に全部一致すれば候補順の「・」連結を値にし、一致しなければ `uncertain`・`MARK_CHOICE_UNRESOLVED` で `raw` に読んだ文字を残す。単一選択で2つ以上返れば `uncertain`（`ocr_response_finalization_service`）。
  - ページ別（`job_page_service.page_config`）でも `automatic`/`manual` を受け付け、選択肢の矩形は切り抜き全体にする（従来は `PRINT_PAGE_CONFIG_UNSUPPORTED`）。
- 変えないもの：mappingId・targetPath・sourceRegionId・boundingBox・crop、compact ID・association token・応答schema、記号（symbol）・チェックリストの扱い、補正・revision・確定・出力。prompt versionは `mapped-compact-v4`。受付済みjobは受付時のsnapshotで従来どおり。
- テンプレート作成側（契約外・保存しない）：`POST /api/v1/template-versions/{id}/layout/mark-options/suggest`（`mark_option_suggestion_service`）が、選択欄の切り抜き（テンプレート画像のみ、記入済み書類は送らない）を GPT-6 Luna の Responses（json schema、reasoning low、`template.mark_options` で台帳予約）で読み、印字の選択肢の文字と位置を返す。見た目が同じ欄（内側の濃淡32×16のコサイン類似度0.85以上・寸法差25%以内）は1回の要求にまとめ、他の同じ欄へ同じ選択肢を提案する。画面がその結果を下書きに入れ、通常の保存経路で保存する。

## 14. テンプレートの線接続操作（2026-10-05）

項目名と記入欄を線で結ぶ操作の仕様。

- 項目名から記入欄へドラッグし、離したときに反映する。複数欄の選び方は従来の `meaningLinkLaneCells` を使用する。選択一覧や追加の接続確認ボタンを要求しない。
- 描画時と確定時は同じ `meaningDragSelection` を使う。終点セルの中心への補正、役割による絞り込み、方向・経路の対象判定を共有し、強調表示と実際の対象が食い違わないようにする。
- 接続線は丸い端と白い縁取り、視認できる一定の表示太さにする。ドラッグ中は接続先の枠と件数を表示する。これらは一時UI状態であり、セル座標・crop・値・保存先を変更しない。
- 2026-10-05追加最適化: 項目名のhover時に接続点とgrabカーソルを表示する。接続中は原画像の文字を見やすくするため認識文字の重ね描きを抑え、仮想見出しは表示する。保存した文字は変更しない。
- セル内のヒットを優先する。セル外では項目名/記入欄の矩形までの距離を表示pxで測り、8px以内を吸着候補にする。最短2候補の距離差が2px未満なら対象を決めず、利用者に欄の中へ移動するよう示す。多角形の外側への吸着は行わない。吸着はポインターの解釈だけに適用し、セルの座標・形状や既存の接続を変更しない。
- ドラッグ中の表示更新と端から32px以内の自動スクロールはrequestAnimationFrameで処理する。スクロール後のcanvas座標から候補を再計算する。マウスを離す・Esc・pointer cancel・ウィンドウ非アクティブ化で停止し、4px未満のクリック移動は接続変更を行わない。
- 保存先の推定・重複回避、relation作成・並び順、既存保存APIは従来の経路を使用する。利用者の接続操作によるrelation ID更新は従来の仕様であり、OCRのmappingIdと混同しない。
- Esc、pointer cancel、工程・ページ・ツール変更で未確定の線を破棄する。取り消しは直前のrelationと親関連に加え、recordStructureVerified / allowCrossAxisLabelsを元の値へ戻す。確認状態を再計算して変更しない。
- 既存テンプレートや受付snapshotの一括変換は不要。provider・prompt・OCR実行・補正revision・XLSX出力は変更しない。
