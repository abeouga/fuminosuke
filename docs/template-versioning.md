# Template編集のVersioning

使用中（ACTIVE）のテンプレートを編集するとき、元の版を変えずに新しいDRAFT版を作り、
承認で切り替える仕組みの仕様。版をまたぐ項目の同一性（logical field）の持ち方も扱う。

## 用語

`Template.id` が系列、`TemplateVersion.id` が版、`version_number` が系列内の整数版番号。
版の状態はDRAFT/ACTIVE/ARCHIVED/REJECTEDで、系列は`active_version_id`で使用中の版を指す。
承認時は旧版をARCHIVEDにしてactiveを切り替える。OCRの受付時にはconfig・画像・fieldMappings・出力snapshotを
保存し、旧jobを同じ受付条件で再実行するretryができる。OCR認識経路は版管理で変わらない。

## 状態遷移

1. `POST /api/v1/template-versions/{activeId}/edit` は現在のACTIVEを確認する。
   既存DRAFTが1つなら再開し、なければ同じ原本から独立したDRAFTを作る。
   既存DRAFTが複数なら409で停止し、勝手に選択しない。
   `printLayout`版では更新元の現在revisionを`base_revision`として必須とし、stale要求を
   409で拒否する。既存DRAFTの再開前に、更新元ID、系列、mode、DRAFT status、
   version/job snapshotのlineage、所有するsource workbookの実bytes SHAを再検証する。
   不一致を別DRAFTの新規作成で回避しない。
2. 作成時に `max(version_number)+1` を確定する。途中保存は同じ版を更新する。
   取り消した版の版番号は再利用しない。
3. ACTIVE/ARCHIVED版のlayout、field mappings、mask、汎用定義の保存・検証を409で拒否する。
   frontendの既存編集入口はfork完了後にDRAFTのeditorを開く。失敗時は再試行可能。
4. 編集画面の「変更を反映する」（新規作成時は「テンプレートを作成する」）でDRAFTをACTIVEへ切り替える。旧版は既存状態ARCHIVEDとなるが、
   旧版の設定・layout・原本・mappingは変更しない。親版が現在のactiveと異なれば公開を拒否する。
5. 新規jobは従来のversion選択・受付条件に従いV2を受付snapshotに保存する。
   DRAFTをOCRに使用しない。画面側の「draft編集中はOCRを開始しない」既存ルールも維持する。
6. V1受付済みjobのsnapshotは変更しない。結果表示・補正・確定・出力・retryはV1のまま。
7. 「変更を取り消す」でDRAFTをREJECTED、その独立した準備jobをCANCELLEDにする。
   使用中のACTIVE版がある系列のDRAFTだけが対象で、まだ作成していないテンプレートは取り消せない。
   旧ACTIVEや過去jobを削除しない。物理削除ではなく履歴保持型の破棄。

forkと承認・取り消しはプロセス内mutation lockと書込みtransaction（`begin_immediate`）を使用する。
承認では旧版status、新版status、active pointer、新準備job確定を1回のcommitで更新する。
fork失敗時もDBの半端な版・jobは残らない。artifactは
`jobs/{newJob}/1/{newArtifactId}.{sourceExtension}`へ複製し、元filenameをidentityに
使用しない。複製元はjob/attempt所有関係と保存SHAを確認し、複製後に実bytes SHAを再読取する。
保存先衝突、SHA不一致、copy途中失敗ではcommitせず、当該要求が新規作成したpathだけを削除する。
旧版および過去jobのファイルは上書き・cleanupしない。

新DRAFTへは編集継続に必要なlayout、page ranges、source関係、logical identityを継承するが、
`referenceQualification`、資格boolean/certificate、`printFiles`、prepared graphや
renderer/environmentの正式証拠は現在資格として継承しない。安全に複製したpage imageと
保存済みgeometry/layout入力は編集継続用に保持できるが、それだけではprepared/qualifiedに
ならず、新DRAFT自身の明示prepareとformal qualificationが必要である。

`printLayout`を持つpaged DRAFTの承認は、要求時のprint revisionと
`wsl-prepared-reference-v2`正式資格（[WSL印刷確定版の参照検証](prepared-reference-qualification.md)）を必須とする。承認transaction内で資格のGETと同じvalidatorを使い、
version/source/plan/graph/artifact/environment/implementation/expiryを現在条件で再検証する。
有効なenvironment資格とgeometry資格、未解決blockerなしを満たす場合だけ上記の既存切替を行う。
printLayoutのない版は従来のlegacy readinessを維持する。paged版のOCR受付の条件は
[OCR Safety Contract §12](ocr-safety-contract.md#12-xlsx複数ページocrの安全契約)に従い、
実入力のidentity/registration/crop/ink検査は送信前gateで行う。

## Identity

| 対象 | V1→DRAFT/V2 |
| --- | --- |
| Template.id | 維持 |
| TemplateVersion.id / 版番号 | 新規 |
| logicalFieldId | fork時の明示的対応を継承 |
| mappingId / generic mapping key | 新規 |
| rectangle / cell / region / mark / option / composite subfield ID | 新規 |
| 原本・mask・準備job・artifact IDと保存先 | 内容を複製し、保存先とIDを分離 |
| 座標・サイズ・印字・semantic targetPath | fork時は維持。draft編集後の変更はV2だけ |
| XLSXセル番地 | 出力アドレスとして維持。矩形IDは `xlsx_A1_<unique>` 等で分離 |

`template_logical_fields` が系列内logical identity、`template_field_identities` が
`(template_version_id, mapping_id) → logical_field_id` のsidecar。
composite subfieldは `mappingId:subfieldId` でも記録する。
`GET /api/v1/template-versions/{id}/field-identities` で対応を参照できる。
新規OCR受付snapshotには `logicalFieldIds` を追加するが、providerのmapping識別には使用しない。

同じdraft内の同一IDの移動・名称変更はlogical IDを保持する。新しいIDの項目は新規logical ID。
項目削除は当該版のbindingを非presentにし、旧版のbindingは保持する。
意味づけを一時解除して同じセルIDに再設定した場合は同一項目として復帰する。
名称・座標・targetPathによる推測マッチングは行わない。
祖先IDをdraftへ戻す保存は拒否する。

汎用定義はfield/section/recordの明示的ID対応からgeneric mapping keyを再計算する。
この形式だけはmapping key由来の `customFields.generic_*` 出力キーも新版に合わせる。
既存の意味的なtargetPathは変更しない。
lineageは `quality.forkParentVersionId` とlogical bindingで表現する。
SPLIT/MERGE関係モデルや推測的な継承は持たない。

## 保存と互換性

`template_logical_fields` と `template_field_identities` の2つのsidecarテーブルで持つ
（`backend/alembic/versions/0001_generic_schema_v2.py`で作る）。
既存version・job snapshot・結果・設定ファイルを一括更新しない。
旧版のsidecar未作成時は `(version.id, mappingId)` から決定的UUIDを解決し、
初回fork/保存で実体化する。同じ版内の厳密なIDから解決し、別版同士の同一性を推測しない。
旧jobにlogicalFieldIdsを後付けしない。

## OCR Safetyと対象外

provider / model / prompt / structured-output schema / association照合 / OCR判定は版管理で変わらない。
版管理が扱うのは編集lifecycle、新版のmapping/physical identity、受付のlogical sidecar。
crop生成・座標計算・検出アルゴリズムは変わらない。DRAFTのlayout編集で指定した座標のみ新版に反映する。
XLSX出力先番地、旧job output bindingと旧job snapshotは維持する。
系列単位の候補設定は別の機能であり、その設定lifecycleは版管理で変わらない。

同じ原本の複製までを対象とする。forkしたpaged DRAFTは版自身の明示prepare経路で
version-bound artifact graphを再生成できるが、fork操作からprepareを自動連鎖しない。
新規テンプレートの初回DRAFTの再処理は従来どおり。
原本差し替え、再検出による原本migration、semantic matching、SPLIT/MERGE GUIは扱わない。

## 実装の場所

- DB: `backend/app/db/models.py`
- fork/provenance: `backend/app/services/template_version_service.py`、`template_field_identity.py`
- 保存・状態保護: `template_mutation.py`、`template_management_service.py`、`layout_service.py`、
  `template_definition_service.py`、`backend/app/repositories/template_repository.py`
- API/受付: `backend/app/api/v1/routes/templates.py`、`backend/app/services/job_service.py`
- UI: `frontend/src/api.ts`、`frontend/src/pages/TemplatePage.tsx`
