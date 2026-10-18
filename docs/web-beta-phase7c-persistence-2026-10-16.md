# Webベータ 7-C：PostgreSQL・S3の保存境界

2026-10-17追記：利用者の「Claudeのほうを優先して」に従い、元ファイルのClaude側の変更を優先する。
現在の作業はClaude側の[確定済みデータの構造化取得・質問応答](structured-qa-design-2026-10-16.md)であり、
Webベータ7-C以降の追加実装・配備は一時停止する。本書は停止前に実施した保存・再起動検証の記録である。
この追記以後、製品コードや共通の作業方針文書を変更せず、専用試験資源の整理と証拠保存だけを行う。
現在の作業フォルダー全体を審査済み・公開可能と判定するものではない。

対象は `C:\Users\hanam\Desktop\llm-ocr-deploy`。工程の指定はGPT-6.1 Sol / XHigh。
通常のローカルSQLite、旧checkout、開発用API台帳は移行しない。公開構成はTerraform、
Fargate API、RDS PostgreSQL、非公開S3、CloudFrontとし、月1万円以内と23:00〜07:00 JSTの停止条件を維持する。
本工程では製品のFargate/RDS資源を作成せず、夜間制御も有効化しない。

## 保存の正本

- クラウドの認証・台帳索引・ファイルcatalogはPostgreSQLの `beta_control`、業務データは利用者別schemaに保存する。
- schema名は認証registryが発行したtenant IDから固定し、HTTPの引数から選ばない。接続poolのsearch_pathもschema別に固定する。
- 原本、画像、設定、確定済み出力はS3に保存し、コンテナーのPathは検証済みの作業用コピーとする。
- S3保存は内容SHAと論理パスの対応を固定する。ファイルの公開を確認してから既存の業務DBへ参照をcommitする。
  DB rollbackで残った未参照ファイルは有効な成果物とは扱わず、容量予約は保持する。
- 業務object用bucketは**versioning未使用**に限定する。削除時に旧versionを残す実装ではないため、Enabled/Suspendedを拒否する。
  台帳・バックアップbucketの保持方針とは別である。IAMには専用bucketのGetBucketVersioningと、所有prefixの必要な操作だけを設定する。

`ControlDatabase` が共有トランザクション、`Database` が業務トランザクション、`S3StorageService` がファイルの公開・取得・削除を担当する。
各HTTP handlerへ所有者条件やS3 SDK呼出しを複製しない。既存のローカルstorage経路は保持する。

## 排他・中断・削除

API runtimeはDBのsession advisory lockを所有する。各業務transactionは所有epochとkeeperの実在を検査し、
commitまで共有row lockを保持する。keeper喪失時と別runtimeへの切替後は旧writerを拒否する。
別コンテナー・別作業ディレクトリからの二重起動にも同じ制御を使う。

管理者の利用者登録・停止は稼働中にも実行できる。オフラインbackup/restore/delete-userはAPIと排他にし、
利用者の再承認もその保守処理と同時には行わせない。認証失効時の既存の保存拒否・音声接続終了境界を維持する。

S3公開前にSTAGINGの容量をDB予約する。応答喪失時は予約を残し、同じ内容の再実行だけを検証して復旧する。
利用者500MiB、全体5GiB、単一ファイル64MiBを上限とし、並行writerも同じ排他内で予約する。
論理パスの削除後はDELETEDを保持し、古いwriterによる復活を拒否する。
ローカルコピーの改変や未公開ファイルを含むディレクトリも拒否する。

削除receiptが残っていれば、業務DBの行を既に削除した状態からも所有原本・生成物を回収する。
過去のbackupを戻しても、その後の削除とAPI予約を外部receiptから再適用し、失敗・停止・復元による返金は行わない。
PROCESSINGのまま中断したjobは既存のINTERRUPTED経路へ戻し、有料処理を自動再送しない。

## バックアップと復元

PostgreSQL用のbackupはversion 2。型を保持したテーブルごとのJSONLと、catalogに対応するSHA検証済みobjectを保存する。
ファイルを全て保存した後にmanifestを公開する。SQLite用version 1とは区別し、相互に無言で変換しない。
原本パスの長さに依存しないよう、backup内のobject名は論理パスのSHAで固定する。

復元先は新しい空の専用DBと別の作業ディレクトリに限る。既存schemaや無関係な業務表のあるDBを引き継がない。
復元開始時にDBへRESTORINGを記録するため、作業ディレクトリが消えても不完全なDBを起動できない。
全利用者を停止し、session/loginを破棄して、外部receiptの再照合と削除再適用を完了する。
PostgreSQLのSERIAL採番位置も復元し、次の履歴追加が既存IDと衝突しないようにする。

成功時は旧DBをRETIREDにし、新DBをREADYへ切り替える。旧DBを別の作業ディレクトリから起動することも拒否する。
途中で停止した場合は両方を閉じたまま保持し、新しい復元先で明示復旧する。旧DBと復元DBを同時に運用しない。
復元後の利用者再承認は既存の管理者CLIで実行する。

運用入口は `app.cloud.admin` と `app.cloud.maintenance`。
PostgreSQL復元では `--target` に加えて `--database-url-file` で新しいDBの接続先を指定する。
接続文字列・AWS資格情報・session tokenをコマンドライン、Git、画面ログへ出さない。
Fargate環境ではPostgreSQL・業務object・台帳・backup設定が欠けていれば起動を拒否し、一時領域のSQLiteへ退避しない。

## 変更境界

変更した製品ファイルはcloud、DB接続、storage、lifecycleと、既存の保存呼出し元に限定する。
クラウドの更新artifactは内容別パスにし、Artifact IDとDB参照経路を維持する。
参照されていない版フォルダーの画像・qualityコピーの更新を廃止し、DBで所有するartifactを正本にする。
単一結果の確定出力はクラウドでは既存の保存ファイルを返し、GETのたびに同じパスを上書きしない。

本工程はOCRのmodel、prompt、response schema、association、mapping ID、physical identity、crop生成条件を変更しない。
受付snapshot、revision付き補正、全ページ確認・確定、出力先セルの契約は維持する。
ただし、同時作業で変更されたOCR関連ファイルは本工程の作成物と区別し、全worktreeの回帰保証には含めない。

## 検証記録

専用の実PostgreSQL 16.15と実S3を使用した。Cognitoログインは未受入で、認証済みsessionを実registryから発行した試験である。
証拠・失敗ログ・固定fixtureは `D:\llm-ocr-archive\beta\20261016-phase7c` に保存する。

| 対象 | 結果 |
| --- | --- |
| PostgreSQL/S3の実ASGI、補正、競合、確定、出力、cache喪失、復元 | 29条件通過 |
| 応答喪失、容量競合、commit失敗、lease喪失、過去backupへの削除・予約の再適用 | 35条件通過。所有39ファイルを実S3から回収 |
| 新しいschema 2個のFK保持、稼働中の管理者操作、保守との排他 | 6条件通過。各schemaのFKは54件 |
| Fargate設定不足拒否、採番位置、専用DB、運用CLI | 6条件通過 |
| 既存の認証・所有権の回帰 | 127条件通過 |
| 修正6-A後の運用・backup/restoreの回帰 | 41条件通過。外部通信は制御応答 |
| Linuxの実入口、実HTTP、1GiB/0.25 vCPU、SIGKILLと再起動 | 12条件通過。稼働中の管理者CLI、保存・session維持、中断処理、正常終了を確認 |
| package assetのS3保存応答喪失と再試行・rollback | 修正前の失敗を再現後、6条件通過 |
| 静的検査・差分・通常データ・過去封印 | 対象ruff、限定mypy16ファイル、diff check成功。既存3 serviceの指摘は凍結baselineと一致。通常データ・schema・設定・API台帳・旧checkout一致、過去30,194ファイルの封印一致 |

最初のLinux試験では確定XLSXのbytes一致、実SIGKILL=137、正常終了=0、中断後の自動再送なしを確認した。
その時点のメモリーは187.5MiB、出力取得0.875秒。単一時点の値であり、peakや3利用者負荷の受入ではない。
Windows/WSLのlocalhost転送では接続timeoutとWSL通信障害があり、その失敗をAPI単独の起動時間とは判定しない。
最終試験はLinuxコンテナー内の実HTTPから実PostgreSQL/S3へ接続し、メモリー213.9MiB、所有artifact取得0.215秒を観測した。
readiness待機と第1段階の操作を含む20.145秒、起動から第1段階終了まで31.922秒は純粋な起動時間ではない。
いずれも1時点・単一試験の観測値で、peakや負荷性能の保証ではない。
最終Linux試験は最後のasset committer修正より前の固定imageで実施し、その修正は別の実S3試験6条件で確認した。

検証中にschema fingerprintの作成順依存、Windowsの長いbackupパス、復元先の初期行重複、SERIAL採番位置を修正した。
失敗ログは保持する。旧回帰harnessによる過去reportの一時上書きは、封印済みSHAと完全一致する内容へ復元し、
今回のfixtureを専用領域へ移した。途中版へ戻った本工程の制御moduleは、最新の優先指示より前に退避・復元した。
最新のClaude優先指示以後は元ファイルを復元・上書きしない。構造化QAの同時変更（main、structured API・service・contracts・projection、設計文書、作業方針）を保持し、
その機能の回帰保証は本書に含めない。
package assetの応答喪失後、STAGING状態のファイルをPathへ解決できず再試行が失敗する欠落も再現した。
保存adapterのexistsと冪等削除を使うことで、同じ内容の再試行とrollbackを確認した。

実OpenAI呼出しは0。通常台帳は440/924、残484を維持し、ベータ追加1000は未初期化。
実S3と短期間・専用prefixに制限した検証用STS資格情報を使用した。AWSプロファイルは **abe-deploy-user**、
account535562976288、ap-northeast-1。資格情報をimageへ組み込まず、試験後に一時envファイルを除去する。
専用7 prefixの890 object（約9.4MiB）をlocal archiveへSHA付きで退避して削除し、残存0を確認した。
試験APIコンテナーを除去し、専用PostgreSQLは停止・volumeを保持した。一時AWS資格情報ファイルは除去済み。
旧EC2は停止、probeのrunning taskは0、既存CloudFormationの24資源を保持している。

## 次工程と未受入

再開指示後の計画上の次工程は7-D＝GPT-6.1 Sol / High。自動開始しない。TerraformによるFargate API/RDS/内部ALB/S3/CloudFront/ACM/ECRの配備、
既存CloudFormation資源の二重管理解消、renderer probeの製品実行境界への接続、夜間制御を具体化する。
旧EC2は停止したまま保持し、新構成の継続稼働前に重複費用を解消する。

Fargate/RDSの実停止再開、公開HTTPS/Cognito、実マイク、3利用者負荷、全容量復旧、最終費用は未受入。
rendererのprobeを製品接続済み・正式資格済みとは扱わない。
backend full tests、OCR contract検証一式、frontend回帰を今回の保存検証だけで代替したとは扱わない。重い回帰と独立工程審査は未実施である。
通常データの移行・本番台帳初期化・公開・Git書込・
新規ゴール・別エージェント起動は行わない。独立工程審査と重い回帰は7-E、公開受入は7-Fとする。

参照： [S3の整合性](https://docs.aws.amazon.com/AmazonS3/latest/userguide/Welcome.html)、
[条件付き書込](https://docs.aws.amazon.com/AmazonS3/latest/userguide/conditional-writes.html)、
[ECSの実行環境識別](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/ecs-environment-variables.html)、
[検証用STS session policy](https://docs.aws.amazon.com/STS/latest/APIReference/API_GetFederationToken.html)。

## 変更ファイルと検証入口

本工程の製品変更は以下に限定する。Claude側の同時変更はこの一覧の外に記録し、上書きしない。

- `backend/app/cloud/` の `admin.py`、`application.py`、`backup.py`、`config.py`、`journal.py`、`maintenance.py`、`operations.py`、`registry.py`、`tenants.py`。
- 同ディレクトリに追加した `relational.py`、`runtime.py`、`postgres.py`、`pg_backup.py`。
- `backend/app/core/config.py`、`backend/app/core/lifecycle.py`、`backend/app/db/session.py`、追加した `backend/app/db/metadata.py`。
- `backend/app/storage/local_storage.py`、`immutable_artifact.py`、追加した `s3_storage.py`、`backend/app/image_processing/artifacts.py`。
- `backend/app/services/template_mutation.py`、`ocr_result_service.py`、`template_service.py`、`layout_service.py`、`backend/app/template_packages/asset_committer.py`。
- `backend/pyproject.toml`、`backend/requirements.lock` と本書。

検証入口は専用archive内の `acceptance.py`、`faults.py`、`control-boundaries.py`、`guards-cli.py`、
`package-assets.py`、`linux-isolated-runtime.py`、`finish-audit.py`。
Linuxの実入口は `deploy/start-backend.sh`、静的検査は `ruff check`、限定 `mypy --follow-imports=silent --ignore-missing-imports`、
差分確認は `git diff --check` を使用した。各実行結果のlogとJSONはarchive内に保持する。
`acceptance.py` 等は専用DB/S3を操作する検証用入口であり、通常運用の開始コマンドではない。
