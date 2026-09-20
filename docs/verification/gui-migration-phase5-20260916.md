# GUI Migration Phase 5 実Chrome承認（2026-09-16）

## 結論

実Chromeの通常のテンプレート管理画面から「キャリア・プラン作成補助シート001」のV2を開き、
Step 5の正式資格PASS表示と有効な「承認して有効化」ボタンを確認した。ボタン操作は1回だけ行い、
既存の正式approve APIへ現在revisionを送信してHTTP 200となった。

- Template ID: `73aa93bb-2367-4d97-8a96-d8be29fdd0de`
- V1: `6d2187c0-ac7f-4d0c-90c8-a0fc381c2cc8` / `ACTIVE → ARCHIVED`
- V2: `a7b727b0-2316-40c5-bff7-5e686cd9f2ea` / `DRAFT → ACTIVE`
- Template `active_version_id`: V1からV2へ変更
- version数: 2。V3は作成されていない
- approve時の`base_revision`:
  `e4b404d4e7f4dcc4fce4cfe33ed979b2b5c01c310d5120285089e2cfe2936812`
- approve時刻: `2026-09-16 09:33:45 JST`

DB直接更新、approveのAPI代行、重複クリック、自動retryは行っていない。状態変更は実Chromeの
Step 5から開始した既存approve transactionだけによる。

## 承認直前の資格

最初の正式GETでは、Phase 4B後に変化したcurrent host identityにより
`UNVERIFIED / REFERENCE_EVIDENCE_CHANGED`となった。固定SquashFS runtime、source、revision、
prepared graph、mappingは変化していなかったため、再prepareやexpiry書換えは行わず、既存の正式
qualification POSTを同じV2について1回実行した。

- status: `PASS`
- `qualified=true`
- `environmentQualified=true`
- `geometryQualified=true`
- unresolved blockers: `[]`
- issuedAt: `2026-09-16 09:28:39 JST`
- expiresAt: `2026-09-17 09:28:18 JST`
- source SHA:
  `aa1d088cf5ebae9cfca0f42d0ac3cbe1d8b23f7067196407f6ae3ce9c3cc3176`
- prepared graph SHA:
  `aff520952b579718e9875831403b25e470202a6d946736ae15da8213fc1ed409`

承認後の正式GETも同じrevision/graphでPASSを維持した。

## Chrome表示と修正

承認前のStep 5では、旧「有効化未対応」表示はなく、正式資格PASS、期限、blockerなし、
実入力検査が別gateであることを表示した。printLayoutの存在だけではボタンを無効化していなかった。

approve自体は成功したが、承認後GUIが一覧用`GET /templates`のsummaryに`versions`が含まれると仮定し、
V2の所有系列を再検索して失敗した。このため最初の画面にはDRAFT表示が残った。backendの
qualification、Repository、renderer、OCR coreは変更せず、承認前から画面が保持している所有Template IDで
authoritative detailを取得するよう`TemplatePage.tsx`だけを修正し、同じ誤りを拒否するfrontend testを追加した。

修正後の実Chrome再読込では次を確認した。

- 対象カード: `使用中 v2`
- version履歴: `v2 使用中` / `v1 過去の版`
- 承認ボタンは表示されず、ACTIVE版の操作へ切り替わる
- reload後も同じACTIVE表示

既に成功したapproveを再送せず、検証のためのV3も作成していない。

## 不変条件と成果物

承認前後で次を確認した。

- V2のsource SHA、prepared graph、revisionは不変
- mapping 151件と`mappingId / targetPath / sourceRegionId` identity digestは不変
- V1/V2のquality/layout内容はstatus遷移以外で不変
- V1/V2所有artifact 131件のDB metadataと実bytesを含むaggregate digestは不変
- approve後に新規artifact、ProcessingJob、job eventは生成されていない
- 過去job snapshotは変更されていない

ACTIVE版から再prepareせず保存済み成果物を正式download APIで取得した。

- XLSX: 30,901 bytes / SHA
  `a3df374f120a5db6ef0853f2dfb02dd8dd706ceb6afb7495c2bd68015b606d62`
- PDF: 220,658 bytes / SHA
  `b2e6e928683eed12acca62cda6ecda6a902962053c52be5a3bdde96c3c16d941`

両SHAはV2に保存されたcurrent print artifactと一致した。

## OCR gate

ACTIVE切替後もpaged OCR admissionは次の状態を維持した。

- `available=false`
- reason: `PRINT_REFERENCE_NOT_VALIDATED`
- provider送信なし
- OpenAI API 0回
- Google API 0回

ACTIVE化を実入力OCR送信許可として扱っていない。

## 検証

- `TemplatePage.test.tsx`: `42 passed`
- frontend full: `382 passed / 35 files`
- frontend build: PASS
- build warning: 既知の500 KB超chunk warningのみ
- `git diff --check`: PASS
- backend製品コード変更: なし

`GUI_MIGRATION_PHASE_5_COMPLETE`
