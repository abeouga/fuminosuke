# Webベータの運用手順

2026-10-16最新指示により、公開方式をTerraform＋Fargate＋S3/CloudFrontへ変更し、毎日23:00〜7:00 JSTを停止時間とする。[移行設計と現在の実施状況](web-beta-fargate-plan-2026-10-16.md)を優先する。旧EC2案のホスト操作・日次04:00 backupは新構成の運用手順として流用しない。Fargate版では停止前の保存・バックアップと、DB起動後のAPI再開を実装・検証する。現在は未公開で、以下のEC2手順は旧方式の記録である。

2026-10-16の[修正6-B独立再審査](web-beta-phase6b-review-2026-10-16.md)でR1〜R3の解消を確認した。[修正6-A](web-beta-phase6a-repair-2026-10-16.md)の削除中断回収と、復元時の**全利用者停止・管理者による明示再承認**を現在の仕様とする。次はフェーズ7の実環境受入であり、実AWS/HTTPS/Cognito/S3・マイク・負荷・容量・全容量復旧の受入前に公開済みとは扱わない。

2026-10-16の[初回独立審査](web-beta-phase6-review-2026-10-16.md)は修正前の履歴として保持する。以下は現行の運用手順であり、実環境での障害復旧受入完了を示すものではない。

対象は `llm-ocr-deploy`。AWS CLIは **abe-deploy-user / 535562976288 / ap-northeast-1** を使用します。サーバーは専用instance roleを使用し、アクセスキー・通常DB・開発用.envを配布しません。配備定義は [infra.yaml](../deploy/infra.yaml)、設定のひな型は [cloud.env.example](../deploy/cloud.env.example) です。実AWS配備はフェーズ7で行います。

## 配備前の確認

ドメイン、通知先、Ubuntu Server 24.04 amd64の実AMI、最新料金・為替・税、AWS権限、配布imageのdigestを確認します。`infra.yaml` は東京、t3a.medium/Standard、暗号化root20GiB＋data30GiB、80/443だけの入口、SSM管理、Cognito招待制、非公開S3、条件付き追記、バックアップ期限を定義します。Budgetの56USDはアカウント全体の通知閾値で、請求の上限やプロジェクト別費用保証ではありません。通知先への配信設定は、承認された実アドレスをフェーズ7で指定します。

ホストの `/opt/llmocr` に検証済み配布物を置き、`/etc/llmocr/cloud.env` に非秘密の出力値を設定します。`BETA_DATA_DIR`、`BETA_CADDY_DIR`、`BETA_WORK_DIR` は専用の別ディレクトリで、uid/gid 10001が使用できるようにします。APIにDocker socketや通常のapp.dbを渡しません。rendererは [既存手順](../deploy/README.md) の固定image・socket・隔離を使用します。

以下の手順はホストの `/opt/llmocr` から実行します。`BETA_INSTALLATION_ID` は初回に固定し、復元で変更しません。台帳bucketを交換したり、空の台帳を新規上限として再初期化したりしません。

## 初回起動とAIの有効化

初回だけ、APIを停止した状態で次を実行します。既に初期化された台帳への再実行は拒否されます。

```sh
sh deploy/ops.sh init-journal
docker compose --env-file /etc/llmocr/cloud.env -p fuminosuke-beta \
  -f deploy/compose.yaml -f deploy/compose.cloud.yaml up -d --no-build
```

この段階の有料AIは停止しています。実Cognitoのsubを `app.cloud.admin admit` で登録し、実HTTPS・所有権・手動編集・保存・出力を確認します。コマンドはメールを送りません。Cognito利用者の作成や招待送信は別途の明示指示で行います。

共通台帳・実S3・実マイクの受入後に、Secrets Managerのキーをホスト専用ファイルへ保存します。キーを端末へ表示せず、所有10001、権限0400とし、`BETA_API_KEY_FILE` で指定します。通常.envや配布物へキーをコピーしません。AIを有効にするときだけ `compose.ai.yaml` を追加します。

```sh
docker compose --env-file /etc/llmocr/cloud.env -p fuminosuke-beta \
  -f deploy/compose.yaml -f deploy/compose.cloud.yaml -f deploy/compose.ai.yaml \
  up -d --no-build backend
```

有効化を確認してから管理者が `/etc/llmocr/ai-enabled` を作成します。日次バックアップ後の再起動はこのmarkerに従い、同じAI設定へ戻します。markerを削除し通常cloud構成で再作成するとAIを停止できます。画像・音声のモデルやpromptは今回の運用工程で変更していません。

## 送信上限と障害時

AIは **20回/人/JST日、100回/全体/JST日、累計1,000回**。音声接続は1接続300秒を前払いし、利用者ごと累計1,200秒までとします。切断・失敗・業務transactionのrollbackで返金しません。各HTTP送信と音声再接続を既存のApiBudget経路で予約します。

S3へ条件付き追記→SQLiteへの記録確定→送信許可の順です。失敗・応答不明では送信しません。稼働中の台帳障害では新規AI送信を止め、確定済みデータを消しません。再起動時に外部台帳の全列とローカルindexを照合し、孤立予約も消費済みとして復旧します。外部の欠落・不一致・削除再適用失敗がある場合、公開runtimeを起動しません。

保存容量は受付時に **500MiB/人・全体5GiB** を検査し、1要求は64MiBまでです。容量不足でも既存結果の閲覧と削除を維持します。この検査はfilesystemのhard quotaではなく、背景処理で生成するartifactの瞬間最大容量を保証しません。3ブラウザー・重処理・実4GiBでの容量と負荷は公開前受入で確認します。

クラウドのファイルログは2MiB×4まで。起動時に前回分をrotateし、14日より古いrotationを除去します。日次バックアップ後の再起動と組み合わせます。Dockerログも10MiB×3に制限します。通常ローカル環境のログ方式は変更しません。

## 日次バックアップ

`fuminosuke-backup.service` と `.timer` をホストのsystemdへ配置し、timerを有効にします。毎日04:00 JSTに実行します。`ops.sh backup` はAPIを停止し、全worker終了後の排他leaseを取得してからSQLite backupとファイルmanifestを作成します。WALファイルの単純コピーはしません。Caddyは継続しますが、その間API操作は待機・接続エラーになるため、入力を保持して復旧後に再取得します。

```sh
sh deploy/ops.sh backup
systemctl status fuminosuke-backup.timer fuminosuke-backup.service
```

manifestは全ファイルの送信後に保存し、その完了したbackup IDを記録します。失敗時はAPIを停止したままにし、原因を調べます。前回成功から24時間を超えた場合は運用上の障害です。RPO24時間・RTO2時間は目標であり、実AWSの全容量では未測定です。

新しいbackupごとに固有keyを使い、上書きしません。新規backup bucketは非versioningとし、7日後のExpirationを設定します。journal bucketのversioning・永久追記とは分けます。復元APIはmanifestの作成から7日を超えたbackupを拒否します。S3の物理削除は非同期で、厳密に7日以内の消去完了を保証しません。[S3 Expirationの公式説明](https://docs.aws.amazon.com/AmazonS3/latest/userguide/lifecycle-expire-general-considerations.html)を参照してください。

## 削除

画面の既存削除は業務側の参照・状態検査を通してから、外部の削除記録を確定します。待機・処理中の読み取りを止めてから削除します。使用済みテンプレートはアーカイブして版IDを保持し、既存の確定結果・XLSXを残します。未使用テンプレートは版→親の順で削除します。

DB削除後にファイル回収が中断した場合も、次の起動・backup・restoreで削除記録を再適用し、同じtenantと資源IDの残存ファイルを回収します。DB行がないことを回収完了とは扱いません。使用済み版のarchiveは維持し、他資源へ通じるlinkは拒否して運用を停止します。

利用者の全業務データを削除する場合は、APIを停止した状態でホスト管理者が実行します。

```sh
docker compose --env-file /etc/llmocr/cloud.env -p fuminosuke-beta \
  -f deploy/compose.yaml -f deploy/compose.cloud.yaml stop -t 120 backend
docker compose --env-file /etc/llmocr/cloud.env -p fuminosuke-beta \
  -f deploy/compose.yaml -f deploy/compose.cloud.yaml run --rm --no-deps \
  backend python -m app.cloud.maintenance delete-user --tenant TENANT_32_HEX
```

削除記録→アプリの失効・session破棄→該当tenant領域の削除の順です。他利用者を削除せず、symlinkを拒否します。削除済み利用者の再有効化は拒否します。Cognitoアカウント自体の削除はこのローカル業務データ削除とは別の管理操作です。通常の `disable` はデータ削除ではありません。

## 復元

APIを止め、空の復元用親ディレクトリを用意し、uid/gid 10001が使用できるようにします。既存rootへ上書きしません。以下の `/var/lib/fuminosuke/restore-work` は新しい専用領域です。

```sh
docker compose --env-file /etc/llmocr/cloud.env -p fuminosuke-beta \
  -f deploy/compose.yaml -f deploy/compose.cloud.yaml run --rm --no-deps \
  -v /var/lib/fuminosuke/restore-work:/restore \
  backend python -m app.cloud.maintenance restore \
  --backup-id BACKUP_UUID --target /restore/new-data
```

全SHA・size・SQLite integrity→session/login破棄・全利用者の停止→外部予約の照合→backup後の削除再適用を行います。失敗した領域には `.restore-incomplete` が残り、公開runtimeが拒否します。成功結果には `usersRequireReadmission=true` が含まれます。tenant ID・sub・業務値は保持し、復元した利用者を新しいtenantへ作り直しません。古いrootは検証が終わるまで保持します。cloud registryはv2で、既知v1からだけ移行します。旧binaryへの単純なrollbackは禁止します。

`BETA_DATA_DIR`を新rootへ切り替えてruntimeを起動しても、復元した利用者は全員停止中です。バックアップ中のactive、古いcookie、Cognitoにアカウントが存在することだけを根拠に自動で有効化しません。管理者が**現在の利用許可を個別に確認したsub**だけ、既存の管理入口で再承認します。

```sh
docker compose --env-file /etc/llmocr/cloud.env -p fuminosuke-beta \
  -f deploy/compose.yaml -f deploy/compose.cloud.yaml exec backend \
  python -m app.cloud.admin enable REVIEWED_COGNITO_SUB_UUID
```

既存subへの`admit`再実行は停止状態を解除しません。未確認の利用者は停止のまま保持し、削除済み利用者のenableは拒否されます。再承認した検証用利用者で対象tenant、確定revision、出力SHAを確認してから利用を再開します。再承認はメール・招待を送りません。

cloud journal、installation ID、削除記録を以前のbackupへ巻き戻しません。Caddyの証明書領域はコンテナー再作成で失われない専用volumeに保持します。EC2障害からの全復旧・実証明書再発行はフェーズ7で確認します。
