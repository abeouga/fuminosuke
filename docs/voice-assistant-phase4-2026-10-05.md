# 文之助 共通保存・revision基盤の実装記録

2026年10月5日、[設計書](voice-assistant-design-2026-10-05.md)のブロック3・フェーズ4を `C:\Users\hanam\Desktop\llm-ocr-deploy` に実装しました。OCRの補正・確定・再開・再処理と、テンプレートの手動編集が共通revisionを使用します。テンプレート保存は、設定ファイルの公開後にlayout・mapping・識別設定・品質・履歴を同じDB transactionで更新する方式へ変更しました。

今回の完了範囲は保存基盤です。サーバー側の変更提案・提示・承認・適用、永続undo、音声認識・読み上げは後続フェーズです。操作パネルのAI適用・音声ボタンはまだ有効化しません。前回の[フェーズ2〜3記録](voice-assistant-phase23-2026-10-05.md)にある「templateのresourceRevisionはnull」は当時の状態で、現在は整数revisionを使用します。

## 変更した契約

| 対象 | 旧仕様 | フェーズ4以降 |
| --- | --- | --- |
| OCR revision | 補正時を中心に増加。確定・再開で世代が変わらない | 補正・確定・再開・再処理受付・結果保存が同じrevisionを進める。既存履歴は振り直さない |
| OCR再開 | baseRevision不要 | `POST /api/v1/ocr-jobs/{jobId}/reopen` のbodyにbaseRevision必須。古い世代は409 |
| job再処理 | 既存結果の世代を要求しない | `POST /api/v1/jobs/{jobId}/retry?expectedRevision=...`。既存OCR結果またはテンプレート再処理元がある場合に必須 |
| TemplateVersion | 全編集共通の整数revisionなし | revisionは1以上。SQLAlchemyのversion比較、HTTP guard、業務処理で競合を拒否。各世代のauthoring履歴を保存 |
| authoring保存 | layout・mapping・印刷用authoringを別々に保存 | `GET/PUT /api/v1/template-versions/{versionId}/authoring`。PUTはexpectedRevisionとlayout・documentIdentityFields・documentNameを受ける |
| 旧保存API | `PUT .../layout`、`PUT .../field-mappings`、印刷用の分割authoring保存 | 廃止。読取APIは必要なものを維持し、frontendの保存経路を統合APIへ変更 |
| 版更新の応答 | 保存後の世代を一貫して取得できない | `X-Template-Revision`、authoringのrevision、候補設定・印刷状態のresourceRevisionを返す |
| 確定履歴 | 最新のConfirmedResultが中心 | 最新状態に加えてOcrConfirmationRevisionを保存。再開・再確定しても過去の確定snapshotを保持 |
| 候補設定 | Template系列の変更と版の世代が独立 | 指定版のexpectedRevisionを照合し、系列内の全版のrevisionを進めて古い提案を失効させる |

revisionは単調増加する世代番号です。「HTTP要求1回につき必ず1増加」という契約ではありません。長い検証処理は開始と完了で別々に保存するため、成功した一操作で2増える場合があります。layoutInputRevisionや印刷用hashは入力・描画の識別に残し、編集競合用revisionの代用にしません。

## 保存と競合の実装

TemplateAuthoringServiceは、現在の版・設定・layoutを読み、既存のmapping生成と物理identityの検証を使用して保存内容を準備します。新しい設定YAMLはSHA-256を含む不変パスに完全な内容を公開・検証した後、DB transaction内で旧revision、DRAFT状態、旧設定参照と内容、新設定内容を再確認します。layout、設定参照、識別情報、品質、必要な印刷準備snapshot、revision履歴を一度に保存します。

印刷準備済みの版では、ページ範囲、座標、reference画像との対応を検証し、同じ描画条件ならreferenceImageとprintFilesを引き継ぎます。現在の版から過去の受付snapshotを作り直しません。fork、独立複製、XLSX import、新規テンプレート生成も完全な初期layoutと設定参照を持つ版を作成します。

手編集マスクは新しい不変PNGとartifactを作り、参照・品質・revisionを同じtransactionで変更します。以前のマスクを上書きしません。不変ファイルは複数の版が共有できるため、失敗時に共有ファイルを削除するcleanupは行いません。DBから未参照の完全なファイルが残る可能性は許容し、参照を確認するGCは後続の別作業とします。既存画像artifactすべての保存方式を今回不変化したとは扱いません。

Windowsの同時公開では、親ディレクトリ作成中にPythonのパス解決結果が拡張パス表記になり、保存先検査で拒否される競合を再現しました。親ディレクトリを先に確立し、Windowsでは既存ファイルを置換しないrenameを使います。保存先の包含検査は変更していません。POSIXでは置換しないhard link公開を使用します。

Database.transactionがcommitを所有し、呼び出される補正・authoring・履歴保存は渡されたSessionをflushする構成です。transaction内で下位処理がcommitすると例外で拒否し、業務更新をrollbackします。通常の読取Sessionはclose時に読み取ったORM値を失効させず、例外時のrollbackと未確定のrevision scopeの復元を行います。

再処理受付はjob更新と元リソースの世代予約を一度にcommitします。テンプレートの長い再処理は予約した元revisionを受付snapshotに保存し、結果反映時にも比較します。OCR結果保存はprocessing状態とattemptを照合します。古い検証・再処理結果を新しい手編集へ上書きしません。

## receiptと操作履歴の基盤

MutationRequestは要求キー、所有者、リソース、要求種別、payload hash、状態、保存済みの結果またはエラーを保持します。同じキーで所有者・対象・内容が違う要求は拒否します。

1. 要求をPENDINGとして予約する。
2. 書込transactionを取得し、現在revisionの検査より先に既存receiptを確認する。
3. SUCCEEDEDなら保存済み結果、FAILEDなら保存済み業務エラーを返す。業務処理は再実行しない。
4. 未確定の場合だけ業務処理・履歴・成功receiptを同じtransactionでcommitする。

commit前の例外では業務更新をrollbackし、同じキーで再開できます。確定した業務エラーはFAILEDとして保存します。予期しない例外ではPENDINGを維持します。commit後の再送は、現在revisionが進んでいても保存済み結果を返します。

ResourceOperationはbefore/after revision、差分とinverse payload、undo元IDを保持できる基盤です。今回、AssistantSessionやProposalの状態機械、HTTPでのreceipt照会、永続undoの実行APIは追加していません。PENDINGは予約済み・実行中・結果未確定を含む内部状態であり、詳細なユーザー向け復旧表示はフェーズ7で設計します。

## 変更ファイルの範囲

| 主なファイル群 | 役割 |
| --- | --- |
| `backend/app/db/models.py`、`mutation.py`、`mutation_models.py`、`session.py` | 共通revision、世代履歴、receipt、transaction所有 |
| `backend/alembic/env.py`、`versions/0001_generic_schema_v2.py`、`0002_authoring_mutations_v1.py` | 固定した初期DDL、既存・新規DB移行、失敗時のDDL rollback |
| `backend/app/services/template_authoring_service.py`、`layout_service.py`、`template_management_service.py` | 統合保存、mapping準備、長時間検証、マスク保存 |
| `backend/app/services/print_layout_service.py`、`template_candidate_service.py`、`template_definition_service.py` | 印刷・候補・定義更新の同世代制御 |
| `backend/app/services/template_service.py`、`template_version_service.py`、`template_duplicate_service.py`、`spreadsheet_template_import_service.py` | 再処理、fork、複製、importの保存整合性 |
| `backend/app/storage/immutable_artifact.py`、`template_packages`配下 | 不変設定・マスク公開、packageの設定参照・候補更新 |
| `backend/app/services/ocr_result_service.py`、`ocr_workflow_service.py`、`job_service.py`と対応repository | OCR lifecycle revision、共通補正transaction、再処理世代 |
| `backend/app/services/mutation_receipt_service.py` | 冪等要求と操作記録 |
| `backend/app/api/template_revision.py`、`api/v1/routes`配下、`main.py`、`domain/enums.py` | HTTP契約、guard、応答、service接続 |
| `frontend/src/api.ts`、`types.ts`、`printLayout.ts`、各編集画面・保存component・job hook | 読み取ったrevisionを明示送信し、保存後のrevisionを受け取る |
| `backend/scripts/generate_authoring_types.py`、`frontend/src/assistant/generatedAuthoring.ts` | FastAPI OpenAPIからauthoring envelopeの型を生成 |
| frontendの既存境界テスト3ファイル | revision必須化に伴うfixture・呼出引数の更新。検査の削除・弱体化は行わない |
| `AGENTS.md`、`ARTIFACT_STORAGE_POLICY.md`、`docs/ocr-safety-contract.md`、`docs/page-authoring.md`、本書 | 実装契約、成果物の保存先、次フェーズ |

開始時の未コミット変更を含む613ファイルを退避し、HEADとの差分とは別に比較しました。詳細な今回のファイル一覧と差分は、製品外の `phase4-files.json` と `phase4.diff` に保存しています。元の `C:\Users\hanam\Desktop\llm-ocr` は編集していません。staging、commit、pushは行っていません。

## DB移行とデータ保全

通常アプリを停止してSQLiteのbackup APIで退避し、所有artifact・設定ファイルと、DBが参照する受付snapshot・upload・原本・出力を別途コピーしました。新規DBでは旧初期schemaを固定DDLで作成してから追加migrationを適用し、移行済みDBでは新しい列と4テーブルだけを追加します。初期migrationで現在のmetadata.create_allを呼び、追加migrationと二重作成する構成は廃止しました。

実DBは `authoring_mutations_v1` へ移行済みです。TemplateVersion 10件に初期revision=1とauthoring履歴10件を追加し、既存確定結果3件を確定履歴へ引き継ぎました。移行前から存在する22テーブルの全既存列・行、OCR revision・確定状態・受付snapshotは一致しています。788件のartifact・設定と36件の補助ファイルは全件SHA-256が一致しました。integrity_checkとforeign_key_checkも成功しています。

DDL途中に故障を注入したDBコピーでは、revision列、新規テーブル、alembic revisionがまとめてrollbackすることを確認しました。旧版への自動downgradeは提供しません。戻す場合はアプリ停止後に対応するコード・DB・所有ファイルの退避一式を使用します。移行後の変更を含むDBに古いbackupを上書きする操作は、今回実行していません。

通常起動は `sysover-ray/Start-LlmOcr.ps1 -NoBrowser` で確認し、frontendは `http://127.0.0.1:5180`、backendは `http://127.0.0.1:8010` で稼働しています。backend直接とfrontend proxy経由のhealthでdatabase=ok、jobWorker=runningを確認しました。検証用の5181/8011は停止済みです。SysOverRayのWPFボタンや前面表示を今回検証したとは扱いません。

## 検証結果

| 検証 | 結果・証拠 |
| --- | --- |
| 保存・競合・履歴・再処理の短いintegration | 16件成功。実FastAPI route、SQLiteコピー、serviceを使用。workerと外部providerを起動しない条件 |
| fork・複製・paged保存・失敗receipt・内部commit拒否 | 6件成功。paged保存は配置を編集していない既存XLSX版の検証コピーから準備 |
| 不変ファイルの同時公開 | 8並列×50回、計400公開が成功。既存内容の上書き拒否、tmp残留なし、保存先検査維持も確認 |
| frontendの短い既存テスト | `api.printGeometry.test.ts` 1件と `PrintLayoutEditor.test.tsx` 14件、計15件成功 |
| frontend build | TypeScriptとVite build成功 |
| Python静的検査 | app・alembicのcompileallとRuff重大エラー検査成功。新規の主要7ファイルは設定済みRuff全ルール成功 |
| 新規・既存DBコピー | migration成功。既存列・行一致、integrity、外部キー、head再適用を確認 |
| migration故障注入 | DDL途中の失敗でschemaとmigration revisionをrollback |
| 移行前後のOCR出力 | 同じ保存済み確定結果を旧コード・現在コードで読む応答が一致。XLSXの8構成ファイルが一致。生成時刻を含む `docProps/core.xml` は比較対象外 |
| 実ブラウザーと通常backend | 検証用DBコピーで書類名設定を保存しrevision 1→2。XLSX構造確認で2→4、完了表示、console error 0を確認 |
| 実DBと通常起動 | 上記データ照合と5180/8010 health成功 |
| 差分 | 開始時退避との比較、既存差分保持、`git diff --check`成功 |

16件には、統合保存、無効入力・古いrevision拒否、同世代の同時更新で一方だけ成功、receipt再送・同時送信、途中例外のrollback、候補設定の系列全版失効、古い検証結果の拒否、マスク履歴、OCR補正→確定→再開→再確定、渡したSession内の補正rollback、再処理受付と元世代の拒否、ORM直接更新の比較、DB整合性を含みます。

実ブラウザーでの保存先は `D:\llm-ocr-archive\voice-assistant\20261005-phase4\ui-data` です。通常のFastAPIアプリとworkerを使用し、OCR providerはdisabledです。HTTPをmockに置換していません。実DBに試験用の書類名やテンプレートを保存していません。

OCR provider、prompt、読取モデル、field association、crop座標、targetPath、受付snapshotから出力先への対応規則は変更していません。これらの既存OCR contractを維持する一方、保存・revision・確定履歴の契約は上記のとおり意図的に変更しています。保存済みresponseと一件のXLSX比較はOCR品質全体の保証ではありません。

外部AI APIの使用は累計 **0 / 100回** です。重いbackend/frontend全件unit、E2E全件、実providerの再読取、実マイク、renderer環境の正式qualificationは未実施です。全体回帰はフェーズ9、実マイク操作は参加を求めたうえでフェーズ10〜11で実施します。

## 証拠と再実行

証拠の保存先は `D:\llm-ocr-archive\voice-assistant\20261005-phase4` です。

- `baseline/`、`baseline-hashes.json`、`baseline-status.txt`、`baseline.diff`: 作業開始時のコードと未コミット差分。
- `before.db`、`before-actual-migration.db`、`artifacts-before/`、`support-files-before/`: 移行前のデータ。
- `migration-results.json`、`migration-rollback-results.json`、`actual-migration-results.json`: DB移行。
- `integration-results.json`、`extended-results.json`、`publication-results.json`: 最終の境界検証。
- `frontend-boundary-results.json`、`frontend-build-final.log`: frontend検証。
- `output-comparison-results.json`、`output-before/after.json/xlsx`: 保存応答と出力比較。
- `ui-results.json`、`ui-template-saved.png`、`ui-verified.png`: 検証用DBでの画面操作。
- `runtime-results.json`、`runtime-processes.json`、`startup-final.*.log`: 通常起動と既存データ照合。
- `phase4-files.json`、`phase4.diff`、`final-status.txt`、`diff-check-shell-final.log`: 今回の変更範囲。
- `boundary-failure*.log`、`publication-trace.log`: 修正前の失敗証拠。成功結果で上書きしない。

次のPython検証は新しい検証ディレクトリとDBコピーを作ります。通常データは変更しません。外部に退避したfixtureと既存依存環境が必要です。

```powershell
# 実行場所: C:\Users\hanam\Desktop\llm-ocr-deploy\backend
.\.venv\Scripts\python.exe D:\llm-ocr-archive\voice-assistant\20261005-phase4\verify_migrations.py
.\.venv\Scripts\python.exe D:\llm-ocr-archive\voice-assistant\20261005-phase4\verify_migration_rollback.py
.\.venv\Scripts\python.exe D:\llm-ocr-archive\voice-assistant\20261005-phase4\verify_boundaries.py
.\.venv\Scripts\python.exe D:\llm-ocr-archive\voice-assistant\20261005-phase4\verify_extended.py
.\.venv\Scripts\python.exe D:\llm-ocr-archive\voice-assistant\20261005-phase4\verify_publication.py
.\.venv\Scripts\python.exe scripts\generate_authoring_types.py
.\.venv\Scripts\python.exe -m compileall -q app alembic
.\.venv\Scripts\ruff.exe check --select E9,F63,F7,F82 app alembic
```

```powershell
# 実行場所: C:\Users\hanam\Desktop\llm-ocr-deploy\frontend
.\node_modules\.bin\vitest.cmd run src/api.printGeometry.test.ts src/components/PrintLayoutEditor.test.tsx
npm.cmd run build
```

## 次ブロックへの引き継ぎ

次は **GPT-6.1-Sol / xhigh、フェーズ5〜6** です。この記録でフェーズ4を終了し、モデル切り替え前に次の実装へ進みません。

1. LayoutCell.fieldLabelを新設し、手動編集、mapping表示名、serialization、fork・複製・packageを含めて保持する。ID・targetPath・printedLabelTextを表示名変更のために作り直さない。
2. `ocr.set_value`、`template.rename_cell`、`template.change_category`、`template.rename_choice`の4操作をサーバーで検証する。
3. session・選択context・期限・提案・提示challenge・承認・適用を接続する。ローカルpreviewのbefore値や差分を正本として信頼しない。
4. 適用はMutationReceiptServiceを入口とし、receipt再送判定の後に提案・revisionを検証する。HTTPのtemplate_revision_guardをreceiptより先に置かない。
5. OCRはcorrect_in_transaction、templateはprepare/persistと共通Sessionを使用し、提案消費・操作履歴・receiptと一度にcommitする。下位処理にcommitを追加しない。
6. 成功時に正本を再取得する。409では未保存編集を維持し、GETした最新revisionへ自動で載せ替えて再送しない。保存成功後の再取得失敗と、保存失敗を区別する。

現段階の手動編集では、長い検証が開始後に失敗すると開始時のrevision更新だけが残り、画面の再取得が必要になる場合があります。receipt照会や詳細なoutcome_unknown復旧はフェーズ7で扱います。永続undoも同フェーズです。音声I/O・API予算ledgerはフェーズ8へ引き継ぎます。
