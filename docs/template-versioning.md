# Template編集のVersioning

## 現状調査と変更境界

変更前のrevisionは `759ac814`、開始時のworktreeはclean。
`Template.id` が系列、`TemplateVersion.id` が版、`version_number` が系列内の整数版番号。
従来は新しいテンプレート準備処理で版番号が増える一方、既存編集はACTIVE版の
layout・field mappings・maskを直接保存できた。編集開始時のfork処理は存在しなかった。
fieldの識別は版内のmapping ID、矩形ID、generic field/context由来のIDであり、
版をまたぐlogical fieldの正本はなかった。

既に備わっていたものは、DRAFT/ACTIVE/ARCHIVED/REJECTED、active_version_id、
承認時の旧版archiveとactive切替、受付時config・画像・fieldMappings・出力snapshot、
旧jobを同じ受付条件で再実行するretry。これらを利用し、OCR認識経路は変更していない。

## 実装と状態遷移

1. `POST /api/v1/template-versions/{activeId}/edit` は現在のACTIVEを確認する。
   既存DRAFTが1つなら再開し、なければ同じ原本から独立したDRAFTを作る。
   既存DRAFTが複数なら409で停止し、勝手に選択しない。
2. 作成時に `max(version_number)+1` を確定する。途中保存は同じ版を更新する。
   却下された版番号は再利用しない。
3. ACTIVE/ARCHIVED版のlayout、field mappings、mask、汎用定義の保存・検証を409で拒否する。
   frontendの既存編集入口はfork完了後にDRAFTのeditorを開く。失敗時は再試行可能。
4. 既存の「承認して有効化」でDRAFTをACTIVEへ切り替える。旧版は既存状態ARCHIVEDとなるが、
   旧版の設定・layout・原本・mappingは変更しない。親版が現在のactiveと異なれば公開を拒否する。
5. 新規jobは従来のversion選択・受付条件に従いV2を受付snapshotに保存する。
   DRAFTをOCRに使用しない。画面側の「draft編集中はOCRを開始しない」既存ルールも維持する。
6. V1受付済みjobのsnapshotは変更しない。結果表示・補正・確定・出力・retryはV1のまま。
7. 既存の「却下」でDRAFTをREJECTED、その独立した準備jobをCANCELLEDにする。
   旧ACTIVEや過去jobを削除しない。物理削除ではなく履歴保持型の破棄。

forkと承認・却下は既存のプロセス内mutation lockとSQLite `BEGIN IMMEDIATE` を使用する。
承認では旧版status、新版status、active pointer、新準備job確定を1回のcommitで更新する。
commit直前にflush後の例外を注入し、全DB状態が戻ることを検証する。
fork失敗時もDBの半端な版・jobは残らない。ただし、DB rollback以前に複製済みの
新規UUID配下ファイルは孤立ファイルとして残り得る。旧版ファイルを上書きしない。

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
SPLIT/MERGE関係モデルや推測的な継承は今回追加しない。

## Migrationと互換性

`0031_template_field_identity` は2つのsidecarテーブルだけを追加する。
既存version・job snapshot・結果・設定ファイルを一括更新しない。
旧版のsidecar未作成時は `(version.id, mappingId)` から決定的UUIDを解決し、
初回fork/保存で実体化する。同じ版内の厳密なIDから解決し、別版同士の同一性を推測しない。
旧jobにlogicalFieldIdsを後付けしない。

実データDBには本作業中にmigrationを適用していない。
利用前に既存の起動・更新手順で `alembic upgrade head` を適用する必要がある。
テスト用DBで新規構築、0030からのupgradeと既存行・snapshot bytesの完全一致を確認する。

## 変更ファイル

- DB: `backend/app/db/models.py`、`backend/alembic/versions/0031_template_field_identity.py`
- fork/provenance: `backend/app/services/template_version_service.py`、`template_field_identity.py`
- 保存・状態保護: `template_mutation.py`、`template_management_service.py`、`layout_service.py`、
  `template_definition_service.py`、`backend/app/repositories/template_repository.py`
- API/受付: `backend/app/api/v1/routes/templates.py`、`backend/app/services/job_service.py`
- UI: `frontend/src/api.ts`、`frontend/src/pages/TemplatePage.tsx`
- tests: `test_template_versioning.py`、`test_migration.py`、`test_template_pipeline_api.py`、
  `test_ocr_review_api.py`、`test_spreadsheet_template_output_contract.py`、
  `TemplatePage.test.tsx`、`OcrPage.test.tsx`
- 契約: `docs/ocr-safety-contract.md`、本書

## OCR Safetyと対象外

provider / model / prompt / structured-output schema / association照合 / OCR判定は変更なし。
意図的に変えたのは編集lifecycle、新版のmapping/physical identity、受付のlogical sidecar。
crop生成・座標計算・検出アルゴリズムは変更なし。DRAFTのlayout編集で指定した座標のみ新版に反映する。
XLSX出力先番地、旧job output bindingと旧job snapshotは維持する。
既存のGoogle Sheets出力layout revisionと系列単位の候補設定は別の既存機能であり、
それらの設定lifecycleは今回変更しない。

同じ原本の複製までを対象とする。forkしたdraftのprepare再実行は409で拒否し、
既存のlayout確認を使用する。新規テンプレートの初回DRAFTの再処理は従来どおり。
原本差し替え、再検出による原本migration、semantic matching、SPLIT/MERGE GUI、
Correction Telemetry、モデルエスカレーションは未実装。

## 検証

- 変更前: `backend\.venv\Scripts\python.exe scripts\verify_ocr_contract.py` — 194 passed。
- 変更後: 同コマンド — 194 passed（49.39秒）。
- backend全体: `.venv\Scripts\python.exe -m pytest -o addopts='' -q`
  （未使用のrepo-local `--basetemp` 指定）— 551 passed、1 skipped。
  最終の識別子保護追加後も551 passed、1 skipped（149.73秒）。
  skipは実OpenAI opt-inテスト。追加versioningテスト単体は9 passed。
- frontend: `npm test -- --run` — 228 passed。`npm run build` 成功。
  buildに既存の500KB超chunk警告あり。
- 境界テスト: 同時編集、draft再開、移動/名称変更/削除/同名新規追加、旧版write拒否、
  祖先ID混入拒否、失敗rollback、汎用定義fork、旧jobの表示/補正/確定/出力/retry、
  V1/V2のXLSX原本と出力、migrationの既存データ保持を確認する。
- 既存ACTIVE編集テストは新契約に従いDRAFT編集へ変更し、元のmapping/mask/
  結合セル検証は維持。公開版破壊への期待を単に削除してはいない。
- 変更対象Ruff通過。mypy全体は既存の型stub不足・未変更箇所の型エラーで不通過。
  `layout_service.py` の既存nullable文字列処理にもエラーあり。新規versioningモジュールの
  型エラーは解消済み。pytest既存cacheの権限警告は独立したtempとcache無効化で回避した。
- `git diff --check` 通過。
- 実ブラウザー操作、実データDB更新、実OpenAI/Google APIは実施していない。
  API callsは0。実APIのmismatch/認識誤りは未実測であり0件とは扱わない。
- 自動テストはmock/fixtureでの契約検証であり、全帳票のGolden replayや実画像精度保証ではない。
  原本・field対応、補正とsnapshotの既存OCR contractを維持した。
- commit / pushなし。
