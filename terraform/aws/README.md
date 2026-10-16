# 文之助ベータのTerraform構成

現在の公開方式は **Fargate＋S3/CloudFront、毎日23:00〜7:00 JST停止** へ移行中です。[移行設計](../../docs/web-beta-fargate-plan-2026-10-16.md)の工程と終了条件を参照してください。

AWS profileは **abe-deploy-user**、account **535562976288**、region **ap-northeast-1**。全provider/backendで対象accountを固定します。Terraform 1.15以降、AWS provider 6系をlockfileで固定し、秘密値をtfvars・stateへ直接設定しません。通知先アドレスだけはignoredなterraform.tfvarsで指定し、plan/stateには非公開情報が含まれるものとして管理します。

| ディレクトリ | 状態 |
| --- | --- |
| `bootstrap` | 適用済み。state用S3と暗号化・非公開・versioning・TLS制限の5資源。stateは同bucketの `bootstrap/terraform.tfstate` |
| `beta` | 旧EC2を取り込む33資源の案。**未適用・現行Fargate構成ではない**。誤適用防止の入力検証あり。既存AWSはまだCloudFormation管理 |
| `modules/runtime_hours` | FargateとRDSの夜間停止制御。検証済み定義、未配備、既定無効。実task/service/DBの受入後に接続する |

state bucketは `fuminosuke-tfstate-535562976288-ap-northeast-1`。S3 lockfileを使い、DynamoDBは追加しません。bootstrapは初回だけlocal stateでbucketを作り、その後 `backend.tf` のS3へstate移行済みです。以後backendを削除して新しいlocal stateを作り直さないでください。

`terraform init` → `terraform validate` → 保存planの確認 → **そのplanのapply** の順で実行します。state、plan、`.terraform`、実通知先、資格情報をGitへ追加しません。OpenAIキーとCognitoパスワードはTerraformで作成・取得しません。

既存資源の移管は取込planに置換・削除・意図しない更新がないことを確認し、stateを保全してからCloudFormationのRetain設定と管理解除を行います。Terraform取込だけで二重管理が解消したとは扱いません。今回、ベータ本体の取込・CloudFormation解除・旧資源削除はまだ行っていません。

夜間制御は現在時刻で営業中か判定し、毎分APIとDBの実状態を照合します。7:00は起動開始であり即時利用を保証しません。ECS側のdesiredCountは初期0とし、受入後の変更を時間制御に任せます。手動applyで夜間に1へ戻さないよう、ECS定義でdesiredCountの変更境界を明示します。
