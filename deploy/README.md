# Webベータのコンテナー実行






通常の `compose.yaml` はフェーズ2のローカル検証用で、公開ポートをホストのloopbackに限定しています。認証構成はcloud overlayで提供します。クラウドの共通送信予算・実AWS公開は後続工程です。通常composeを認証なしでインターネットへ公開しないでください。

AWS配備先は **`abe-deploy-user` / account `<アカウントID>` / `ap-northeast-1`** です。`<アカウントID>` は `aws sts get-caller-identity --profile abe-deploy-user` で確かめます。この工程でAWSリソースは作成していません。ドメインは未定です。

## 構成

`compose.yaml`はproduction buildの画面と単一FastAPIを起動します。SQLiteと原本・成果物は、指定した専用dataディレクトリに保存します。APIイメージにはLibreOffice、Docker socket、AWS資格情報、通常ローカルDB、`.env`を入れません。

ホスト側の専用supervisorは、Unix socketで受けたサイズ・SHA付きの帳票データと固定の処理種別だけを扱います。1件ずつ、固定image IDの描画コンテナーを起動します。入力と実行条件はread-only、出力は64MiBの専用tmpfs、network none、非root、capability除去、no-new-privileges、2GiB/1CPU/64PIDで実行します。LibreOfficeとPDF parse/rasterをAPIから分離します。

workerの独立した75秒alarmとsupervisorの80秒上限、4秒の接続leaseを設けます。失敗・切断・OOMで当該コンテナーとvolumeだけを回収し、supervisorの強制終了後もroot所有markerから回収します。未知の所有者・link・別コンテナーを削除して復旧しません。

API、画面、supervisor、描画コンテナーは `llmocr-beta.slice` の4GiB・swap禁止枠にまとめます。これはアプリ全体の制限であり、OSとDocker daemonの使用量は別です。実EC2での余裕・CPU credit・応答時間は配備時に確認します。

## Linuxホストでの準備

Docker Engine、Compose plugin、systemd、Python 3を使います。Docker Engineの導入は[公式手順](https://docs.docker.com/engine/install/ubuntu/)に従います。専用の新規dataディレクトリを使い、通常 `backend/data` を指定しません。

リポジトリrootでイメージを作ります。

```sh
docker build -t fuminosuke-renderer:beta -f deploy/renderer/Dockerfile .
```

Composeのbuildにも下記3変数が必要です。`RENDERER_IMAGE_ID`は描画imageをbuildした後で設定します。backend/frontendの環境へ秘密の値を埋め込まないでください。

```sh
export RENDERER_IMAGE_ID="$(docker image inspect fuminosuke-renderer:beta --format '{{.Id}}')"
export BETA_DATA_DIR=/var/lib/fuminosuke-beta/data
export RENDERER_SOCKET_DIR=/run/llmocr-renderer
export BETA_HTTP_PORT=5288
```

root権限の運用担当が専用ディレクトリを作り、dataをUID/GID10001へ割り当てます。既存の別用途ディレクトリへ再帰的な所有者変更を行いません。

```sh
install -d -o 10001 -g 10001 -m 0750 /var/lib/fuminosuke-beta/data
install -d -o 0 -g 0 -m 0700 /var/lib/llmocr-renderer
install -d -o 0 -g 0 -m 0755 /etc/llmocr
install -d -o 0 -g 0 -m 0755 /opt/llmocr/backend/app
cp -R backend/app/rendering /opt/llmocr/backend/app/
touch /opt/llmocr/backend/app/__init__.py
```

`/etc/llmocr/renderer.env`へ以下を記録します。rootだけが書込可能にし、値は現在のimageとworkerから取得します。`RENDERER_WORKER_SHA256`はimage内 `/opt/renderer/worker.py` と一致させます。タグが同名でも異なるimageを黙って採用しません。

```text
RENDERER_IMAGE_ID=sha256:<検証したimageの64桁ID>
RENDERER_WORKER_SHA256=<worker.pyの64桁SHA256>
RENDERER_SOCKET=/run/llmocr-renderer/renderer.sock
RENDERER_SOCKET_GID=10001
RENDERER_SPOOL=/var/lib/llmocr-renderer
```

```sh
install -m 0644 deploy/renderer/llmocr-beta.slice /etc/systemd/system/
install -m 0644 deploy/renderer/llmocr-renderer.service /etc/systemd/system/
chmod 0600 /etc/llmocr/renderer.env
systemctl daemon-reload
systemctl start llmocr-beta.slice llmocr-renderer.service
docker compose -f deploy/compose.yaml build
docker compose -f deploy/compose.yaml up -d
```

`http://127.0.0.1:5288/api/v1/health`と画面の接続表示を確認します。新規専用DBへの既存migrationはbackendのentrypointで実行します。APIは1プロセスで、通常DBを取り込む処理はありません。現在の既定は `OCR_PROVIDER=disabled`、AI/音声の公開機能も無効です。実APIの有効化は利用者分離・予算実装後に行います。

停止ではComposeのデータvolume削除を指定しません。

```sh
docker compose -f deploy/compose.yaml down
systemctl stop llmocr-renderer.service
```

再起動は同じimage ID・専用data指定で `systemctl start` と `compose up -d` を実行します。新しいrenderer imageを導入するときは新しい準備を明示実行し、旧証拠を書き換えて通しません。

## フォントと旧データ

Linux新規準備は `linux-fods-physical-length-v1`、`linux-print-reference-v1`、`paged-candidate-readiness-v4-linux`を保存します。旧Windows/WSL版・過去jobへ後付けしません。文字や筆画、正式geometry/glyph資格を認定する経路ではなく、人間が全ページを確認する候補経路です。

利用可能なLinux fontへ正確に解決するものに加え、Calibri→Carlito、Cambria→Caladea、Arial→Liberation Sans、Times New Roman→Liberation Serif、Courier New→Liberation Monoの限定代替を宣言しています。実際の解決結果・font SHA・判断を保存します。Windows fontは同梱しません。MeiryoやMS Gothic等を未知の代替へ自動承認せず、対応しないfontは印刷準備を停止します。Noto CJKはimageに含みますが、全帳票の字形・位置の同等性を保証しません。

ベースimageはdigest固定、Python/Node依存は既存lockを使用します。Debianのnative packageはbuild時の集合SHAを記録し、配布した完成imageのIDを実行正本にします。将来の再buildがbit単位で同一になるとは保証しません。


## 招待制クラウドの認証構成（フェーズ3）

`compose.yaml` に `compose.cloud.yaml` を重ねます。専用の新しい `BETA_DATA_DIR` を使い、既存のapp.dbを置きません。APIには公開portを設定せず、Caddyだけを入口にします。秘密値をイメージやGitへ入れません。

| 設定 | 内容 |
| --- | --- |
| BETA_PUBLIC_ORIGIN | 正確なHTTPS origin。末尾スラッシュやpathを付けない |
| BETA_SITE | 公開hostname。CaddyのHTTPSとHTTP redirectに使う |
| BETA_COGNITO_POOL_ID | ap-northeast-1の実user pool ID |
| BETA_COGNITO_CLIENT_ID | client secretなしのauthorization code／PKCE用app client |
| BETA_COGNITO_DOMAIN | 東京のCognito managed domainのHTTPS URL |
| BETA_DATA_DIR | registry.dbとtenantsを格納する専用ホスト領域 |

Cognitoの自己登録を無効にし、callbackを `<BETA_PUBLIC_ORIGIN>/api/auth/callback`、scopeをopenid、grantをauthorization codeに限定します。実Cognitoの設定は後続AWS工程で検証します。WSL試験に使った架空のpool/client/domainを配備へ転用しません。

既存の招待アカウントのsubをhost側で登録します。このコマンドはメールを送らず、Cognitoアカウントを新規作成しません。実際の招待送信は別途の明示指示で行います。

```sh
docker compose -f deploy/compose.yaml -f deploy/compose.cloud.yaml exec backend python -m app.cloud.admin admit COGNITO_SUB_UUID
docker compose -f deploy/compose.yaml -f deploy/compose.cloud.yaml exec backend python -m app.cloud.admin disable COGNITO_SUB_UUID
docker compose -f deploy/compose.yaml -f deploy/compose.cloud.yaml exec backend python -m app.cloud.admin enable COGNITO_SUB_UUID
```

`disable`／`enable`はいずれも既存cookie sessionを破棄します。enable後は再ログインが必要です。管理HTTP APIや匿名signupはありません。使用する実行profileは **abe-deploy-user**、account **`<アカウントID>`**、東京 **ap-northeast-1**です。

有料AIの共通quotaをフェーズ5で接続するまで、cloudは `OCR_PROVIDER=disabled`、`VOICE_PROVIDER=disabled`、OpenAI API keyなしでのみ起動します。開発台帳の924回や追加1,000回をtenantごとに発行しません。バックアップ・削除・復旧はregistryと全tenant領域の整合性を維持する必要があります。

