# Activation Phase 4 formal approval（2026-09-15）

## 結論

対象TemplateVersion `6d2187c0-ac7f-4d0c-90c8-a0fc381c2cc8` を、Phase 3の正式資格を
承認transaction内で再検証する既存`POST /api/v1/template-versions/{version_id}/approve`経路から
`DRAFT → ACTIVE`へ切り替えた。

- 最終status: `ACTIVE`
- Template `active_version_id`: `6d2187c0-ac7f-4d0c-90c8-a0fc381c2cc8`
- qualification: `PASS`
- `qualified=true`
- `environmentQualified=true`
- `geometryQualified=true`
- unresolved blockers: `[]`
- OCR受付: `available=false` / `PRINT_REFERENCE_NOT_VALIDATED`

provider生成・送信、実入力OCR、review/correction/confirmation/output、Google同期は実行していない。

## 承認契約

approve APIは、`printLayout`を持たない版では従来のmapping/document identity、layout verification、
generic definition等のlegacy readinessを維持する。`printLayout`を持つpaged版では要求時の
`base_revision`を必須とし、旧`layoutVerified`/`xlsx-structure` booleanを偽装せず、Phase 3の
`ReferenceQualificationService.status`と同じvalidatorを既存mutation lock＋`BEGIN IMMEDIATE`内で
実行する。

正式PASS、qualification/environment/geometryの現行policyとimplementation fingerprint、
各componentのPASS、version/revision/Calc geometry binding、期限、未解決code/blockerなし、
activation certificate発行済みを要求する。実入力stageが`NOT_EVALUATED`であり、送信前4検査が
残っていることも維持する。保存booleanや旧reportを単独では承認入力にしない。

資格成立後の版切替は既存Repository処理を使用し、対象ACTIVE、旧ACTIVEのARCHIVED化、
`active_version_id`、元準備jobのCONFIRMEDを1 commitで更新する。commit失敗注入で対象DRAFT、
旧ACTIVE、pointer、元jobがすべてrollbackすることを確認した。

## fail-closed確認

承認境界の対象試験で次を拒否した。

- 正式recordなしでtop-levelのqualified/environmentQualified/geometryQualifiedだけがtrue
- qualification/lease expiry
- environment evidence変更
- reference graph/artifact bytes変更
- source workbook bytes変更
- qualification implementation変更
- geometry qualification不成立
- unresolved activation blocker
- print plan変更
- 別TemplateVersion binding
- revision競合
- formal validatorを渡さない旧callerによるpaged承認

Phase 3の既存試験で、artifact ownership/SHA、plan/binding再計算、environment identity、lease、
renderer/geometry issuer、graphの各失効もGET時にfail-closedとなることを維持した。

## 対象実版

Phase 4コード変更でqualification実装fingerprintが変わったため、旧Phase 3 recordの最初のGETは
`UNVERIFIED / PRINT_LAYOUT_CHANGED`となった。GETで補完・期限延長はしていない。既存の正式
qualification POSTを同revisionで実行し、保存済みprepared graphと現在環境を再評価して再発行した。

再発行結果:

- policy: `wsl-prepared-reference-v2`
- evidence source: `SAVED_PREPARED_GRAPH`
- issuedAt: `2026-09-15T00:47:53+09:00`
- expiresAt / environment lease expiresAt: `2026-09-15T18:12:41+09:00`
- qualified / environmentQualified / geometryQualified: `true / true / true`
- unresolved blockers: `[]`

承認直前:

- status: `DRAFT`
- revision: `38e8e4205c4bdb470b0e5ea8666333253868b580d95a4007ad63fd2cd735b7f4`
- active_version_id: `null`
- source SHA: `aa1d088cf5ebae9cfca0f42d0ac3cbe1d8b23f7067196407f6ae3ce9c3cc3176`
- prepared graph SHA: `8be1a32b32a3d11f6e6381cc415ce35e295b74780f0ea5de504b5685e5b903c6`
- source job: `REVIEW_REQUIRED`

正式approve APIは2026-09-15 00:50:42 JSTにHTTP 200となった。承認後GETでもrevision、source SHA、
prepared graph SHAとformal qualificationは同一だった。Template系列に旧ACTIVEは存在しなかったため、
今回ARCHIVEDへ変更した版はない。旧ACTIVEありのarchive処理はprivate integrationで確認した。

承認前後でTemplateVersionのquality/layout、元準備jobのconfig snapshot、元jobの全artifact ID/bytes/SHA、
元準備job以外の全ProcessingJob rowが不変だった。変更した既存DB状態は対象版status/approved_at、
Template.active_version_id/updated_at、元準備jobstatus/confirmed_atだけである。

## 検証

- 変更前baseline: `4 passed`
- paged承認boundary: `13 passed / 16 deselected`
- Phase 3 qualification integration全体: `29 passed`
- legacy/versioning回帰: `12 passed`
- 関連回帰: `131 passed / 4 deselected`（645.12秒）
- 最終focused: `2 passed`
- Ruff（変更対象）: PASS
- TemplateRepository対象mypy: PASS
- 横断mypy: 既存のAny/stub由来を含む63件。今回検出されたRepositoryのnullable 2件は修正済み
- `git diff --check`: PASS
- OCR contract: `210 passed / 1 warning`（55.38秒）
- backend full: `2470 passed / 1 skipped / 3 warnings`（2487.03秒）

backend fullのskip 1件とopenpyxl deprecation warning 3件は既存範囲。実OpenAI API 0回、
Google API 0回。provider/model/prompt/schema、mappingId/targetPath/sourceRegionId、physical identity、
association、crop、correction、output mappingは変更せず、既存OCR contractを維持した。

## Phase 5以降へ残す範囲

- paged OCR production admissionの実装
- provider送信前の`PAGE_INPUT_IDENTITY`、`SCAN_REGISTRATION`、`CROP_CORRESPONDENCE`、
  `INK_CLEARANCE`の実入力enforcement
- ACTIVE後もqualification/environmentが失効・変更していないことの受付時再検証
- provider生成・送信、paged review/correction/confirmation/outputの本番経路確認

Phase 4ではこれらへ進んでいない。

`PHASE_4_COMPLETE`
