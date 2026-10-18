# Webベータ 7-B：Fargate描画の成立性

2026-10-16。対象は `C:\Users\hanam\Desktop\llm-ocr-deploy`。計画モデルはGPT-6.1 Sol / XHigh。
AWS profile **abe-deploy-user**、account **535562976288**、region **ap-northeast-1**。

**固定入力を使う描画・隔離・終了回収の成立性を確認した。製品への接続と公開受入は未完了。**
API/RDSの本番Fargate構成、PostgreSQL/S3への業務保存移行、夜間制御の有効化は本工程に含めない。
旧EC2を再開せず、既存CloudFormationの管理とCognito利用者を保持した。

## 原因と変更境界

従来のLinux rendererはホストDockerのnetwork none、tmpfs、cgroup、Unix socket supervisorに依存する。
Fargateへそのまま移せないため、実行方式だけを専用の検証経路で評価した。
`backend/app/rendering`、業務backend/frontend、通常DB、OCR provider/model/prompt/schema、
mapping/association/crop、受付snapshot、補正revision、確定・出力経路は変更していない。
既存OCR contractを維持した。旧証拠のPASS化や既存テンプレートへの新証拠の後付けはない。

上記は本工程の編集範囲である。終了照合では、別途 `templates.py`、`editing/schema_tools.py`、
`job_service.py`、frontendの `App.tsx`、`OcrPage.tsx`、`TemplatePage.tsx` の6件の同時変更を検出した。
上書き・差戻しは行わず、開始時のSHAが固定Phase 7 releaseと一致することを確認して、
終了SHAと差分行数を `concurrent-source-changes.json` に記録した。
この6件を今回のFargate試験で検証済みとは扱わない。次工程は現在のコードでbaselineを取り直す。

変更は `deploy/fargate-probe/`、`terraform/aws/renderer_probe/` と本工程の文書に限定した。
新規ゴール・別エージェント・Git staging/commit/pushはない。

## 構成と実行境界

Terraformで専用cluster、ECR、非公開の暗号化S3、ログ、SG、実行role等12資源を作成し、
digest固定のtask definitionを追加した。計13資源を別state
`probe/renderer/terraform.tfstate`で管理する。既存VPC/subnetは読取参照のみで、管理を取り込んでいない。
専用clusterにserviceや繰返し起動はない。SGは受信なし、送信HTTPSのみ。
AWS資源の作成はTerraformで行い、単発taskの起動・停止・証拠取得は試験runnerから実施した。

1 taskはLinux x86_64、1 vCPU／3GiB、Fargate platform 1.4.0。
搬送とnativeを別コンテナー・別PID namespaceにし、**task IAM roleを付けない**。
image pull/log用のexecution roleをコンテナーへ渡さない。両者はUID/GID 10001、capability ALL drop、
read-only root。搬送側だけが特定の入出力object用の短時間署名URLを受け取る。
URL、AWS資格情報、APIキーはTerraform・共有volume・native環境・証拠ログへ保存しない。

nativeは入力解析前にno-new-privilegesとseccompを設定する。
Office IPC用AF_UNIXだけを許可し、IPv4/IPv6/netlink/packet socket、io_uring、ptrace、
process memoryアクセス、namespace操作などを拒否する。execした子でも通信拒否を確認した。
inputはreadonly、outputと専用の一時領域だけを書込可能にする。
2GiBのcontainer memory limitを実cgroupから読み取った。
RLIMITはcore=1、file=64MiB、processes=64、CPU=45秒/プロセス。
nativeの100秒、搬送の160秒のalarmと、所有taskだけを止める外部runnerの期限を併用する。
搬送の終了がtask全体を終了させる。最後に両コンテナーへinit processを設定した。

これは `fargate-renderer-feasibility-v1` の試験報告で、`productionEvidence=false`。
従来の `linux-container-renderer-v1` のadmissionや資格証として受理させていない。

## 実測結果

固定入力は `docs/reference-renderer-evidence/saved-two-pages.xlsx`、
SHA `a5113de88e235c910e2326b33141cb73b97a5672375ef6bd54e0f6f52b3b86bd`。
名前にかかわらず直接描画は3ページ。既存のworker・supervisorと固定Phase 7 imageによるbaselineを
本工程で再実行し、Fargate出力と比較した。

| 検証 | 結果 |
| --- | --- |
| 基準帳票のPDF/FODS/PNG生成 | 成功。3ページ、各1241×1754px |
| 変更前後の対応 | 入力SHA・ページ数・全3PNGのbytes/画素・FODSのbytesが完全一致 |
| native処理時間 | 基準2.232秒、別入力2.835秒。Office変換＋FODS＋150dpi PNG生成のみ |
| task起動待ち | 上記2件は25.174秒／33.309秒。作成からRUNNINGまで。native時間とは別 |
| 実効隔離 | UID/GID 10001、CapEff=0、NoNewPrivs=1、Seccomp=2、memory=2147483648 bytes |
| 通信・権限 | 4 socket familyでEPERM、AF_UNIX IPC成功、exec子も通信拒否。資格情報環境なし、搬送process不可視、host data/Docker socket非公開 |
| 他job参照 | 署名URLの対象keyを他jobへ変えると403。並行入力のSHAを個別照合。別入力B2による第1ページの画像差を確認 |
| 時間切れ | native exit142。PASS報告を生成せずtask終了 |
| メモリー超過 | native exit137、実ECS reasonはOutOfMemoryError。PASS報告なし、task終了 |
| 破損XLSX | native exit1/RENDER_FAILED、PASS報告なし |
| 取消 | 最終task definitionで両コンテナーexit143。他の帳票の描画は成功 |
| 起動元の強制終了 | 所有launcher PIDを強制終了。外部StopTaskなし、EssentialContainerExitedで約161.97秒後にSTOPPED |
| 障害後の再描画 | 最終定義で成功。基準3ページのPNG/FODSを再照合しbytes一致 |

task definitionの最終revisionは `fuminosuke-renderer-probe:5`。
imageは `sha256:dabd8bfe41a6dc7b881b5ec9aa239ed19541776908513afc106198b094736ad8`。
基準比較・障害・起動元喪失は同imageのrevision 4、最後の取消と再描画はrevision 5で実施した。
両revisionの意味上の差は搬送の `initProcessEnabled=true` だけである。
既定の空配列と省略の差を区別してplanを照合した。

PDFのbytesは生成日時等を含むため一致していない。PNGとFODSの完全一致を、一般帳票の字体・
geometry資格や実手書きのcrop安全性の証明へ拡張しない。
タスク起動を含めれば即時描画ではない。既存の非同期jobと取消へ接続する必要がある。

## 検出して修正した試験環境の不具合

- 搬送用renderer imageにCA bundleがなくHTTPS取得に失敗した。固定backend imageから公開CA bundleだけを追加し、TLS検証を維持した。Office/Poppler/font packageは変更していない。
- SDK既定のS3 endpointによる署名URLは400/403となった。東京endpointを明示すると同じobjectが200となり、その設定で実Fargateの搬送に成功した。
- input/output mountのownerはFargate上で10001であることを確認した。Officeには専用の書込可能な `/tmp` も必要で、root filesystemはreadonlyのまま一時mountを追加した。
- 初期取消では搬送がexit137まで残った。init processを追加し、実取消で両者exit143を再確認した。
- 最終planでAWSが補う空配列の省略差が毎回のtask definition置換を発生させた。environment/portMappings/systemControls/volumesFromとcapabilities.addを実返却と同じ空配列へ明示し、実行条件を変更せず `plan -detailed-exitcode` のexit0／No changesを確認した。container定義をignoreする方式ではない。
- 最初のWSL試験はUID 10001のRLIMIT_NPROC下で子生成に失敗した。実Fargateの10001では成功。補助のWSL試験は別UID 10017を使ったため、Fargate受入の代用にしていない。
- Terraformの初回fmtとplan読取にも構文/cwd/SDK空配列の扱いによる失敗があった。修正後のvalidate、保存planの照合、applyを証拠とし、初回の失敗を成功へ書き換えない。

## 後続工程へ残す条件

製品はまだFargate rendererを使用しない。次の7-CでPostgreSQL/S3への業務保存移植と合わせ、
実際のtask定義のadmission、原本SHA・版・job所有権、取消・再起動・失敗保存を接続する。
新しい実行条件・graph・candidate policyを明示し、古い証拠を読み替えない。
利用者分離、revision、予約の返金禁止、削除receipt、復元後の停止・明示再承認を維持する。

RLIMIT_NPROCは旧cgroup pids上限と同じ契約ではない。
20GiBのtask一時領域は旧64MiB output tmpfsとも異なる。
製品接続時には累積出力・一時領域・ログ・同時起動の上限、競合のないstreaming検証、
font/dependency identity、未知字体の拒否、実運用のnative異常終了の即時検知を実装・検証する。
現試験の搬送はnative異常時にも期限まで待つため、これをそのまま製品のエラー応答時間にはしない。
画像一致・単発試験の通過だけで正式環境資格・本番公開・3利用者負荷の完了としない。

証拠は `D:\llm-ocr-archive\beta\20261016-phase7b`。
実OpenAI呼出し0、開発台帳440/924・残484、ベータ追加1000は未初期化。
AWSの実有料試験は行った。全16試験taskをSTOPPED、RUNNING/PENDINGとも0、専用SGのENI 0を確認。
所有prefixの試験S3 objectを回収し、専用bucketの残存objectも0。旧EC2はstoppedを維持する。
試験用Terraform13資源と固定ECR imageは再現用に保持し、service/繰返し起動は作成しない。
ruff、Terraform fmt/validate、最終planのNo changes、`git diff --check`を確認した。
frontend/backendの全体テストや実マイクは本工程で実行していない。製品コードへの接続もない。
通常DB/schema・原本・設定・旧checkout・API台帳・781 artifact・既存証拠は開始/終了snapshotで一致した。
現在のworktree全体が開始時と同一であるとは主張しない。保持結果と同時変更を分けて記録した。
次は7-C＝GPT-6.1 Sol / XHigh。夜間23:00〜7:00 JSTの製品停止は後続7-D以降の接続・受入対象である。
