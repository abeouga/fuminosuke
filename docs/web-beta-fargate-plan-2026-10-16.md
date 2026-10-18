# 文之助ベータのFargate移行設計

2026-10-16更新：[7-Bの実Fargate成立性検証](web-beta-phase7b-fargate-2026-10-16.md)を実施した。
専用のTerraform13資源で固定帳票の3PNG/FODS一致、通信・資格情報分離、OOM/timeout/取消/起動元喪失と回収を確認した。
このprobeは製品未接続であり、以下のAPI/RDS/S3業務保存・夜間制御の配備は未完了。
次は7-Cの保存・再起動と実行境界の接続。旧EC2を再開せず、既存CloudFormation管理を保持する。

2026-10-16。最新の指定はTerraformによる構成管理、APIのFargate実行、画面のS3配信、AWS月1万円以内、毎日23:00〜翌7:00の停止である。時刻は日本時間とする。旧EC2案による公開受入を中止し、この設計でフェーズ7を分割する。対象は `llm-ocr-deploy`、AWS profileは **abe-deploy-user**、account **535562976288**、region **ap-northeast-1**。

## 構成

| 責務 | 採用する構成 | 制約 |
| --- | --- | --- |
| 画面 | 非公開S3＋CloudFront OAC、ACM、herdio-system.com | S3のwebsite endpointを公開しない。静的画面と停止時間表示は夜間も配信する |
| API入口 | CloudFrontの `/api/*` → VPC origin → 内部ALB | APIをキャッシュしない。Cookie、Origin、WebSocket、SSEを維持し、APIのエラーをSPAのindex.htmlへ変換しない |
| API | Fargate Linux x86、0.25 vCPU／1GB、稼働時1 task | このサイズは予算案。3利用者の実負荷とメモリーを測定するまで性能を認定しない |
| DB | RDS PostgreSQL、db.t4g.micro、Single-AZ、暗号化gp3 20GiB | APIからだけ接続。通常ローカルDBを持ち込まない。停止中もstorage料金は発生する |
| 原本と成果物 | 非公開S3。所有者・資源IDを含むkeyと固定SHA | DBの保存完了とS3の確定を明示的に扱い、利用者間の参照を許可しない |
| 描画 | 使い捨ての別Fargate taskを第一候補として成立性検証 | ホストDocker socketの代替として特権実行や共通API内のnative処理へ戻さない |
| 認証 | 作成済みCognitoを継続 | 自己登録なし。既存subを維持する。Terraformへパスワードを保存しない |
| 送信上限 | 既存のS3追記台帳の意味を維持し、共有DBの排他を再設計 | 失敗・再起動・夜間停止・復元による返金なし。新しいinstallation IDを発行しない |
| 夜間制御 | EventBridge Scheduler＋短いLambda | 毎分現在時刻と実状態を照合し、23時停止／7時起動を実現する |
| 状態保存 | Terraform専用S3、versioning、暗号化、lockfile | appのinstance/task roleへTerraform stateの権限を付与しない |

NAT Gatewayは置かない。API taskは外部AIへの送信用にpublic IPv4を持つが、受信は内部ALBのsecurity groupだけに限定する。内部ALBとDBはprivate subnetへ置く。ALBにpublic IPv4を付けず、CloudFrontを入口とする。CloudFront VPC originsは東京の一部AZを除外するため、AZ IDを照合して配置する。[AWSのVPC origin仕様](https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/private-content-vpc-origins.html)

CloudFrontの証明書はus-east-1へ配置する。DNSは現在Cloudflareで管理されており、既存の `app.herdio-system.com` 委任を保持する。AWSのTerraform管理とCloudflare DNSの管理は区別し、DNSまでTerraform管理済みとは報告しない。

## 夜間停止の動作

営業時間は **07:00以上23:00未満**。23:00以降、新しい編集・アップロード・AI処理を受付けない。画面は入力中の内容を保持し、停止時間と再開準備状態を表示する。長い処理は終了前に受付を閉じ、既存の中断・再開規則で保存する。停止を理由としたAI自動再送は追加しない。

制御は次の順に行う。

1. 夜間はAPI serviceのdesiredCountを0にする。
2. APIのrunningCountとpendingCountが両方0になるまでDBを停止しない。
3. 専用clusterの指定renderer familyだけを停止し、残存処理を持ち越さない。
4. DBがavailableならstopを要求する。停止中・起動中のDBへ無効な操作を重ねない。
5. 7:00以降は停止済みDBを起動する。availableを確認してからAPIを1 taskにする。

処理は毎分の照合で進め、Lambdaを待機させない。遅延した起動イベントも**実行時の日本時刻**で判定するため、夜間にAPIを再起動しない。重複実行は現在のdesiredCountとDB状態で吸収する。イベント配送失敗とLambda実行失敗は専用の暗号化SQSへ保存し、CloudWatch alarmで検知する。通知メールの追加送信先は未設定である。

制御モジュールは [runtime_hours](../terraform/aws/modules/runtime_hours/main.tf) と [controller.py](../terraform/aws/modules/runtime_hours/controller.py)。既定は `enabled=false`。受入前のAPI／DBを時刻だけで起動させない。ECS service側は夜間制御と競合しないようdesiredCountの変更をこの制御へ委ね、Terraform applyで1へ戻さない。

Schedulerは秒単位の厳密な停止ではなく60秒精度で起動する。APIの終了・DBの停止には追加時間がかかる。7:00は**起動開始時刻**であり、利用可能時刻の保証ではない。RDSの復旧は数分から数時間かかる場合があるため、「必ず7時ちょうどに使える」と表示しない。[Schedulerの時刻精度](https://docs.aws.amazon.com/scheduler/latest/UserGuide/schedule-types.html)、[RDSの停止と復旧](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/USER_StopInstance.html)

S3、CloudFront、ALB、DB storage、Secrets Managerなどは夜間も存在し、費用が残る。夜間停止を全AWS料金がゼロになる設定とは扱わない。RDSの7日後自動起動に対しても、毎分の夜間照合で停止状態へ戻す。

## 必要な製品変更

現行 `db/session.py` はSQLiteのPRAGMAとBEGIN IMMEDIATEを使い、`cloud/registry.py` と `cloud/journal.py` もsqlite3へ直接依存する。`cloud/tenants.py` は利用者別のSQLite DBを作り、保存処理は実ファイルのPathを要求する。そのままFargateの一時領域へ置けばtask交換時にデータを失う。SQLite WALをEFSへ載せるだけの移植は採用しない。[SQLite公式のネットワークファイルシステムに関する制約](https://www.sqlite.org/wal.html)

PostgreSQLでは共有の認証・運用領域と利用者別の業務schemaを分ける案を優先する。既存の「利用者の全業務runtimeを選ぶ」境界を維持し、個々のHTTP handlerへowner条件を複製しない。schema名をブラウザーから受け取らず、registryの固定tenant IDから決定する。pool再利用時のschema漏れ、job ownership、revision競合、排他予約、停止直前のcommitを同じ入力で検証する。確定前にこの方式を製品仕様とは扱わない。

S3は単なるPath置換では足りない。取得・一時作業・確定保存をstorage adapterに分け、原本SHA、mapping、受付snapshot、revision、確定済みXLSX対応を保持する。夜間再開時に現在のテンプレートから過去jobを再構築しない。削除receiptの再適用と、復元後の全利用者停止・明示再承認も維持する。

描画のホストsupervisorはFargateへ持ち込めない。[Fargateのtask制約](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/fargate-tasks-services.html)を前提に、専用taskの入出力、固定image、時間・メモリー制限、資格情報分離、native処理中の通信遮断を最初に検証する。Taskを分離した事実だけでは従来のnetwork noneと同等ではない。候補は入出力搬送とnative実行のコンテナー分離＋native側のsyscall制限であり、成立前に隔離済みとは認定しない。ここが不成立なら製品移植へ先行せず、実測した制約で方式を再検討する。

## 月額予算

2026-10-16にAWS Price List APIで東京の単価を取得した。31日、稼働16時間/日、起動停止の余裕0.5時間/日を加えた **511.5時間**、1USD=160円、税相当10%で試算する。AI API料金は別枠。

| 項目 | 月額USD |
| --- | ---: |
| Fargate API 0.25 vCPU／1GB | 9.294 |
| PostgreSQL db.t4g.micro | 12.788 |
| 内部ALB 744時間 | 18.079 |
| RDS gp3 20GiB | 2.760 |
| APIのpublic IPv4 | 2.558 |
| 描画task・S3・CloudFront転送・LCU・ログ・ECR・secret・制御の予備費 | 7.000 |
| 合計 | **52.48** |
| 円換算 | **約9,236円** |

余裕は約764円。1GBのAPIで実負荷が成立すること、rendererが必要時だけ起動すること、転送量・LCU・RDS CPU credit等が予備費内であることが条件である。料金のハード上限ではない。2GBへ上げると約498円増えるため、検証なしの上位構成への変更はしない。費用警戒時には新しい重処理の受付を閉じ、既存データを削除して帳尻を合わせない。

旧EC2/EBS/EIPの重複費用はこの定常見積もりに含めない。新構成の継続稼働前に、保持対象の確認を終えて旧計算資源と不要volume/IPを撤去する。停止したEC2にもEBSとEIPの費用は残る。アカウント全体の56USD budget通知はプロジェクト別の請求上限ではない。

単価証拠は `D:\llm-ocr-archive\beta\20261016-phase7\fargate-price-list.json`、計算は `fargate-cost.json`。公表料金は [Fargate](https://aws.amazon.com/fargate/pricing/)、[ALB](https://aws.amazon.com/elasticloadbalancing/pricing/)、[RDS PostgreSQL](https://aws.amazon.com/rds/postgresql/pricing/)、[IPv4](https://aws.amazon.com/vpc/pricing/)を参照する。

## 工程と担当モデル

旧フェーズ7のまま配備を続けず、変更した実行・保存境界を次の工程で検証する。新規ゴールと別エージェントは作成しない。

| 工程 | モデルと強度 | 完了条件 |
| --- | --- | --- |
| 7-A 移行設計と費用 | Astra / High | 完了。本設計、現状保持、停止制御定義、予算試算 |
| 7-B Fargate描画の成立性 | GPT-6.1 Sol / XHigh | 固定入力の実検証を完了。製品未接続のprobeで通信・資格情報・他job参照拒否、timeout/OOM/取消/起動元喪失を確認。正式資格と一般帳票の認定は別 |
| 7-C 保存と再起動 | GPT-6.1 Sol / XHigh | PostgreSQL・S3への移植、利用者分離、revision、台帳、削除・復元・夜間中断の短い実API検証。OCR生成条件は変更しない |
| 7-D Terraformと画面配信 | GPT-6.1 Sol / High | Fargate/RDS/内部ALB/S3/CloudFront/ACM/ECR/夜間制御の構成、既存資源の取込とCloudFormation二重管理の解消、旧資源の撤去計画 |
| 7-E 独立工程審査 | Astra / XHigh | 保存・隔離・予算・停止復旧の審査、関連回帰。審査前に夜間制御やAIを有効にしない |
| 7-F 公開受入 | Astra / High | HTTPS/Cognito、3利用者負荷、実マイク、実停止再開、保存・出力・復元、最終予算を確認 |

## 現在のAWSと検証結果

旧CloudFormationの24資源は作成済み。既存 `app.herdio-system.com` の委任を残してルートAを旧EIPへ設定した。新規Cognito利用者の登録・招待メール送信は明示許可後に実施済みだが、製品ログイン受入は未完了。旧EC2に配布imageを導入した後、業務コンテナー0、registry/operations DBなしを確認して停止した。製品公開済みではない。

Terraformはstate用S3の5資源を作成し、そのstateをS3へ移した。7-Bでは別stateの専用probe13資源を追加し、実Fargateで検証した。全16試験taskは停止済み、専用ENIと試験objectは回収済みであり、製品API/DBの配備ではない。ベータ本体33資源の取込案はFargate指定前に生成したが**未適用**で、CloudFormation管理をまだ解除していない。`terraform/aws/beta` は誤適用防止付きのEC2移行案であり、Fargateの配備rootではない。`deploy/infra.yaml` も旧方式の記録として保持し、新しい環境の作成に使わない。

夜間制御は07:00／23:00境界、DB起動待ち、API終了待ち、renderer終了、重複、対象不存在・不一致、受入前停止の16条件を制御クライアントで検証した。Terraform validateと隔離fixtureのplanは成功し、11追加・変更0・削除0。**このplanは適用せず、実Fargate/RDSの停止再開は未検証**。Windowsの初回検証はtzdata不足で失敗し、日本向け固定UTC+9の標準ライブラリ実装にして16条件を再確認した。

今回、backend/frontendの業務コード、通常DB、OCR生成・mapping/crop・補正・確定・出力経路は変更していない。既存OCR contractを維持した。実AI呼出し0、開発台帳440/924・残484、ベータ追加1000は未初期化。Git staging/commit/pushなし。次工程で実APIを使う場合も既存枠を共有して記録する。
