# 複数ページXLSX ACTIVE化 — Phase 2再確定

2026-09-14。判定: **PHASE_2_COMPLETE**。対象版のACTIVE化は未実施。

## 範囲と変更境界

対象TemplateVersion `6d2187c0-ac7f-4d0c-90c8-a0fc381c2cc8` を、既存の明示的な
`PrintPreparationService.prepare` 経路で `wsl-calc-print-grid-v1` へ再確定した。
続いて既存の `ReferenceQualificationService.qualify/status` を実行し、Phase 3のissuerが
評価するprepared graph、再描画、glyph、field geometry、environment leaseを対象実版へ保存した。

資格発行成功経路、`qualified=true`、`environmentQualified=true`、ACTIVE化、OCR受付gate、
provider送信、review/correction/confirmation/outputは変更・実行していない。
製品コード、schema、migration、設定ファイル、provider/model/prompt/schema、mapping/association、
crop方式は変更していない。今回のリポジトリ変更は本記録だけである。

HEADは `c0be81ac174e45c30aa57cc803a049641b16e0be`。開始時から存在したtracked/untracked差分を
stage、commit、reset、revert、整理・削除していない。実OpenAI API 0回、Google API 0回。

## 再確定前

| 項目 | 値 |
| --- | --- |
| status | `DRAFT` |
| revision | `2b026bcecccc5542cfb729614e43b086bd95fae1e11c5a89e2ee243237f33512` |
| geometry | `wsl-fods-physical-length-v1` |
| page ranges | `A1:AK28` / `A29:AK60` / `A61:AK89` / `A90:AK102` |
| pages / fields / OCR mappings | 4 / 285 / 151 |
| source XLSX artifact | `80a0b789-306f-486c-9b53-8d3efb1142c8` |
| source XLSX SHA-256 | `aa1d088cf5ebae9cfca0f42d0ac3cbe1d8b23f7067196407f6ae3ce9c3cc3176` |
| source job artifacts | 14 |
| referenceQualification | なし、`UNVERIFIED` |

処理中の `QUEUED` / `RUNNING` jobは0件で、renderer mutexが空いていることを確認した。
固定runtimeは
`/opt/llmocr-renderer-runtimes/ee8b87dbbffd6b0817e1346aaabeef744c14b9632ea206d7af903cc239ecb03c.squashfs`。
内容SHAはファイル名と一致し、`root:root`、mode `0444`、622,493,696 bytesだった。

## 実行と保存結果

最初に実DBのread-only backupを作る既存 `scripts.dry_run_print_preparation` を、同runtimeと
`--geometry-model wsl-calc-print-grid-v1` で実行した。privateコピーだけで正式prepareが完走し、
page ranges、予測した全fields/bindings、physical identity/meaning、reference画像bytes、scale、
transform、canonical layout、DRAFT、保存後GET、コピー入力の全検査がtrueだった。
privateコピーの結果を実版へ移植・流用していない。

対象実版は同じ製品serviceを直接呼び出して一度だけ再確定した。HTTP routeもこのserviceへ
同じ引数を渡す。実設定ファイルにはruntime IDが未指定だったため、今回の明示実行引数として
最新runtime IDを固定した。生成中にDB mutation lockを保持せず、生成後のrevision、原本SHA、
config SHA再照合を通過してから短いtransactionで保存された。

実DBでは次を変更した。

- `TemplateVersion.quality_json.printLayout` を新Calc planと新prepared artifact参照へ更新。
- source jobの `config_snapshot_json.printLayout` を同じplanへ更新。
- 印刷確定のJobArtifactを7件追加: XLSX、PDF、renderer report、4 reference PNG。
- 既存qualification経路のJobArtifactを15件追加: 4 replay PNG、glyph、environment lease、
  geometry probe XLSX/PDF/report/vectorと4 probe PNG等。
- `TemplateVersion.quality_json.referenceQualification` に診断report参照を保存。

source jobのartifactは14件から36件になった。既存14件のDB rowと実bytes/SHAは全件不変。
他TemplateVersion、他ProcessingJobと過去job snapshotは、prepare前に作成したprivate DB baselineと
全row比較して不変だった。source job自身も `config_snapshot_json.printLayout` 以外は不変。

## 新しいprepared artifact graph

graph policyは `wsl-prepared-artifact-graph-v1`。

- graph SHA-256:
  `8be1a32b32a3d11f6e6381cc415ce35e295b74780f0ea5de504b5685e5b903c6`
- plan SHA-256:
  `4feaec0c8350cd2146b29f6a5e4923fb2d3df9537111e9818c6b3706369cd602`
- import report SHA-256:
  `4982596ace336a11dfc9d4271a8ec441ddd4a2a8829edfdd83925fbb67530d56`
- source artifact / SHA:
  `80a0b789-306f-486c-9b53-8d3efb1142c8` /
  `aa1d088cf5ebae9cfca0f42d0ac3cbe1d8b23f7067196407f6ae3ce9c3cc3176`
- print XLSX:
  `fdec2465-9a67-4a69-bed9-712a1945929f` /
  `a3df374f120a5db6ef0853f2dfb02dd8dd706ceb6afb7495c2bd68015b606d62`
- print PDF:
  `b39b5f60-68a1-459f-b909-a6c9d479cc2d` /
  `7f35829388c2c408135d81149d21619223b60fbc7c758ba59aa24e23ca92ab19`
- renderer report:
  `f9277bf0-105a-448a-baec-80b63d6adec5` /
  `fb9902a9f68e49cfde16787d3d9c3af5fb55c9930f577be5ea9c615fd2595d81`
- page 1〜4 reference SHA:
  `7144e03e…ddeefa` / `56250436…bb874` / `0055b7d2…eccb1` / `cde30781…ad47`。

`ReferenceQualificationService._prepared` は、原本/layout/configと保存寸法から全page planを
再計算し、所有者、artifact SHA、renderer report、全4 PNG、source/config対応を現在graphで
再検証して成功した。保存直後だけでなく、資格GETでも同じgraphを読み戻した。

## 不変条件

再確定前後で次を比較し、差分0を確認した。

- 原本XLSX bytes/SHA、canonical layout、config bytes。
- 4 logical pagesのsheet、ordinal、保存range、開始/終了row/column。
- 全285 fieldsのsourceRange/sourceParts/role/ownership。
- 151 mappingsの `mappingId`、`targetPath`、`sourceRegionId`、mappingType、所有page/cell。
- meaning/relationを含む非geometry field metadata。
- 他TemplateVersion、他job、過去job snapshot、既存artifact。

新しいreference artifact graphを作る正式経路のため、referenceImageのartifact IDと、そこから
導出される**将来の新規受付用**TemplatePage UUIDは更新された。これは古いreference artifactを
新graphのidentityとして残さないための版内証拠rotationである。logical page identity、range、
source cell/range、mapping ownershipは不変で、既存jobの受付snapshot/page identityは変更していない。
新旧4 PNGの内容SHAも全ページ一致した。

## renderer / environment evidence

寸法測定reportとreference生成reportの次の証拠は完全一致した。

- runtime: `ee8b87db…03c`、Squashfs readonly、kernel
  `6.6.87.2-microsoft-standard-WSL2`。
- worker: `wsl-worker-source-v1`、SHA
  `b4279aaafbd87bfb1cacbbb290531972843749f1a7eaac507132577f6ef04a51`。
- Windows font directory: 578 files、665,977,778 bytes、SHA
  `47071a85470e130419c56f116d4294978ba536e78f30ac164f860f5a32786571`。
- bootstrap Python tree v2: Python 3.10.12、1,359 files、SHA
  `3953cf9f116b2027b4cfc15212c8ec2844cf61f8caf3fe66b2c8f22b9eb28a2e`。
- native dependencies: 111 files / 60 roots、SHA
  `daa9da2ed11885dc39e6309f1620047f93e898fc2d7b00f97dd7ff1f47cb48d8`。
- execution: uid 65534、interface `lo`のみ、network denied、no-new-privileges、
  host filesystem hidden、Windows font mount readonly。
- limits: memory 1GiB、swap 0、pids 64、CPU `100000 100000`。
- unit controls: `wsl-effective-unit-controls-v1`。RuntimeMax 1分、TimeoutStop 2秒、
  KillMode control-group、Restart/RemainAfterExitなし、FSIZE 64MiB、CORE 1等を取得。
- worker/brokerのcore guardは `linux-core-limit-seccomp-v1` として一致。

environment artifact `7730307f-b058-499d-8aae-e819635f0d8b` は
`wsl-environment-identity-lease-v2`、scope `identity-freshness-only`。
SHAは `042e1e9e46c144baafb60c719401b5be84a59fae09f3c5478ad9e902972cb38c`。
発行は2026-09-14 18:12:41 JST、失効は2026-09-15 18:12:41 JST。
Windows 25H2 / build 26200 / revision 9445、AMD64、`wsl.exe`実体/生version応答SHAを含む。
GETで所有者、版、graph、期限、保存identity、現在host/runtime/font/worker/bootstrapを再検証した。
このleaseは正式environment certificateではなく、期限切れ後は再発行が必要である。

## qualificationの現在状態

qualification record:

- fingerprint:
  `74b25fa340ab07079ebfcd24bd23eb989a3f6c86bc5b0b4b0fbb616e6cbd78f8`
- report SHA:
  `d2f196b8e40795d185c9ed30af32b85bd4649d83b81110573f0d4d29e605ca50`
- status: `FAIL`
- `qualified=false`
- `environmentQualified=false`
- codes: `REFERENCE_ANCHORS_INSUFFICIENT`
- production blockers:
  `WSL_ENVIRONMENT_QUALIFICATION_UNVERIFIED`、`GEOMETRY_TOLERANCE_UNQUALIFIED`

保存reportとGET再計算結果は完全一致した。再描画は `deterministic=true`。
glyphは3,515 text instructions / 640 distinct font codes、宣言face一致でPASS。
field geometryは4ページ・151領域すべて `MEASURED`、coordinate precisionは全ページ `MATCH`。
activation safety stageは上記3条件を持つ `BLOCKED`、実入力段階は
`PAGE_INPUT_IDENTITY` / `SCAN_REGISTRATION` / `CROP_CORRESPONDENCE` / `INK_CLEARANCE` の
`NOT_EVALUATED` を維持した。

これはPhase 2の期待結果であり、資格issuerの成功を意味しない。Phase 3には、既存証拠からの
正式environment判定、geometry tolerance判定、Calc全領域証拠成立時の旧anchor診断分離を残した。

## 保存XLSX/PDF取得

`TemplatePrintService.export` で対象版・最終revisionを指定して再取得した。

- XLSX: 30,901 bytes、SHA
  `a3df374f120a5db6ef0853f2dfb02dd8dd706ceb6afb7495c2bd68015b606d62`。
- PDF: 220,658 bytes、SHA
  `7f35829388c2c408135d81149d21619223b60fbc7c758ba59aa24e23ca92ab19`。

両方とも保存artifactと一致した。取得時の再変換や原本再保存はない。

## 検証

- private実WSL prepare preflight: 終了コード0。不変条件は全項目true。
- privateの既存qualification実行/GET: 終了コード0。4ページ/151領域、glyph、geometry、
  environment leaseを保存・再取得。
- 実版のprepare/qualification/GET/不変条件監査: 製品処理は成功。
- 実版の対象job限定事後監査: 終了コード0。全14不変条件、全36 owned artifact SHA、
  `_prepared`、qualification GET、XLSX/PDF取得を確認。
- 関連unit:
  `test_calc_*`、prepared replay/glyph/geometry、WSL execution/environment/host/unit admission。
  **361 passed（5.51秒）**、終了コード0。
- 関連integration:
  Calc preparation/private copy、pinned runtime、prepared qualification、実environment inspection。
  **51 passed（311.72秒）**、終了コード0。
- 一時監査スクリプトのRuff: PASS。検証後に両一時ファイルを削除し、製品へ残していない。

最初の実版監査起動は監査コードが存在しないGET `status` keyを参照し、製品service呼出し前に
停止した。DB/model/artifactは旧状態のままだったことをread-onlyで確認してから再実行した。
二回目は製品prepare、qualification、保存GET一致まで完了後、監査コードが対象jobでなく
DB全artifactを読んで別jobの既知欠落fileに当たり停止した。対象jobだけへ限定した事後監査で
上記の全検査が成功した。製品処理のエラーや証拠欠落ではない。

製品コード変更がなく、Phase 2指示が既存正式経路の実行と関連テストに限定しているため、
backend full、OCR contract script、frontend test/buildは再実行していない。
既存OCR contractを維持した。実入力OCRの認識誤り、association mismatch、schema error、
INK_CLEARANCE等は今回未測定であり、本番OCR安全性を完全に検証した結果ではない。

## 最終状態

- revision:
  `38e8e4205c4bdb470b0e5ea8666333253868b580d95a4007ad63fd2cd735b7f4`
- status: `DRAFT`
- geometry: `wsl-calc-print-grid-v1`
- qualification: `FAIL` / `qualified=false` / `environmentQualified=false`
- ACTIVE化: 未実施
- OCR受付gate: 閉鎖維持
- OpenAI API: 0回
- Google API: 0回

`PHASE_2_COMPLETE`
