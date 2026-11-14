# 文之助ベータのTerraform構成

AWS profileは **abe-deploy-user**、account **`<アカウントID>`**、region **ap-northeast-1**。全provider/backendで対象accountを固定します。Terraform 1.15以降、AWS provider 6系をlockfileで固定し、秘密値をtfvars・stateへ直接設定しません。通知先アドレスだけはignoredなterraform.tfvarsで指定し、plan/stateには非公開情報が含まれるものとして管理します。

## 動かす形と定義の場所

クラウドの文之助（`APP_ENV=cloud`）は、次の2つの形で動きます。

- 単一ホスト（EC2＋Docker Compose）：AWS資源の定義は [deploy/infra.yaml](../../deploy/infra.yaml)（CloudFormation）、ホストでの手順は [deploy/README.md](../../deploy/README.md) です。
- ECS Fargate：PostgreSQL（RDS）とS3に業務データを置きます。アプリ側の条件は `backend/app/cloud/config.py` にあります。ECSのserviceとRDSの定義はこのリポジトリにありません。

## ディレクトリ

| ディレクトリ | 中身 |
| --- | --- |
| `bootstrap` | state用S3 bucket（暗号化・非公開・versioning・TLS制限）。stateは同bucketの `bootstrap/terraform.tfstate` |
| `beta` | `deploy/infra.yaml` の単一ホストの資源をTerraformへ取り込むための定義。入力検証（`migration-gate.tf`）で、そのままではapplyできないようにしています |
| `modules/runtime_hours` | FargateのserviceとRDSを、JSTの7:00〜23:00だけ動かす制御（Lambda・EventBridge Scheduler）。既定は無効 |
| `renderer_probe` | 帳票の描画をFargateの単発taskで試すための、独立したstateの構成（cluster・ECR・S3・SG・task definition）。serviceと繰り返しの起動はなく、製品のAPI・DBからは使いません |

`renderer_probe` のstate keyは `probe/renderer/terraform.tfstate` です。
taskの起動は短い試験runnerからのみ行い、終了後に所有task・ENIと試験objectを照合します。
保存planは実資源の作成/変更を管理し、実行時の署名URLはstateへ保存しません。

## stateと実行の順

state bucketは `fuminosuke-tfstate-<アカウントID>-ap-northeast-1`。S3 lockfileを使い、DynamoDBは追加しません。bootstrapは初回だけlocal stateでbucketを作り、その後 `backend.tf` のS3へstateを移します。以後backendを削除して新しいlocal stateを作り直さないでください。

`terraform init` → `terraform validate` → 保存planの確認 → **そのplanのapply** の順で実行します。state、plan、`.terraform`、実通知先、資格情報をGitへ追加しません。OpenAIキーとCognitoパスワードはTerraformで作成・取得しません。

CloudFormationで作った資源をTerraformへ移すときは、取込planに置換・削除・意図しない更新がないことを確認し、stateを保全してからCloudFormationのRetain設定と管理解除を行います。Terraform取込だけで二重管理が解消したとは扱いません。

## 夜間の停止

`runtime_hours` は毎分、現在時刻で営業中か判定し、APIとDBの実状態を照合します。7:00は起動開始であり即時利用を保証しません。ECS側のdesiredCountは初期0とし、変更を時間制御に任せます。手動applyで夜間に1へ戻さないよう、ECS定義でdesiredCountの変更境界を明示します。
