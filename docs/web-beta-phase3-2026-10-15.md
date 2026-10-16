# Webベータ フェーズ3：認証・利用者分離

2026-10-15。対象は `C:\Users\hanam\Desktop\llm-ocr-deploy`。計画上の担当モデルはGPT-6.1 Sol / High。新規ゴール・別エージェント・Git書込・AWSリソース作成は行わない。

## 実装範囲と設計変更

招待制Cognito接続、cookieセッション、CSRF、利用者ごとのAPI・非同期処理・ファイルの分離を実装した。フェーズ1の「共有業務DBに所有者条件を付ける」案を、**共通認証DB＋利用者別SQLite／ファイル／業務runtime**へ変更した。10名規模の単一APIプロセスという前提は維持する。

理由は、既存のサービスがHTTP依存注入以外でも独自にDBへアクセスし、SQLAlchemy ORM、Core、raw SQL、background task、ファイル配信、短期のメモリー状態が併存しているためである。各queryに所有者条件を複製する方式では、子資源・bulk処理・セッションを横断する条件漏れが残りやすい。利用者のDBとruntimeを最初に選び、その中で既存業務処理を実行する方式にした。業務保存処理を複製する方式ではない。

所有関係は `Cognito sub → registryの内部tenant ID → tenantの業務DB／storage／runtime → Template・ProcessingJob → 子資源` になる。業務ID、revision、mapping、受付snapshot、crop、確定・出力の形式は変えない。通常ローカルDBへowner列を追加せず、クラウドへの自動移行もしない。今後のバックアップ／削除／集計はregistryと全tenant領域を一組として扱う。

## 境界

| 項目 | 実装 |
| --- | --- |
| ログイン | Cognito managed domainのauthorization code＋PKCE S256。state・ブラウザーcookie・nonceを一回限りで照合 |
| JWT | 固定した東京issuerのJWKS、RS256限定、署名・issuer・audience・exp・iat・nonce・token_use=idを検証。URLをtokenから組み立てない |
| 招待 | Cognito側の自己登録無効化に加え、host専用のadmitで登録済みsubだけを許可。HTTP signup/admin APIは作らない |
| セッション | 最大1時間、ID token残寿命以下。`__Host-`、Secure、HttpOnly、SameSite=Lax、Path=/、Domainなし。サーバーにはtokenのSHA-256を保存。OIDC tokenはブラウザーやDBへ保存しない |
| 変更要求 | HTTPS origin完全一致＋セッション固有CSRF header。任意のowner header/bodyで利用者を選ばせない |
| SSE／音声 | 同じcookieとtenant runtimeを使用。WebSocketはoriginも必須。既存編集tokenだけでは別アカウントから利用できない |
| 停止・失効 | 各要求でregistryを照合。業務SQL実行・commit・storage解決でも照合。長い接続は1秒間隔でも監視して終了。再有効化は旧セッションを復活させない |
| 背景処理 | runnerはブラウザーの認証contextを継承せず、固定したtenant DB・storageで実行。全runnerで同じ実行semaphoreを使う |
| 終了処理 | 信頼されたlifespan cleanupにだけ、停止済み所有者の実行fenceを保存する限定contextを設ける。HTTPから指定できず、既存の実行taskへ権限を付け直さない |
| ファイル | 公開static artifact経路なし。tenant storageの既存path検査を使う。API応答はprivate/no-store。Caddyにframe拒否・no-referrerを設定 |
| ブラウザー | 認証成立まで業務画面をmountしない。履歴・draft・receiptの保存名をtenant別に分ける。失効時は業務画面を隠し、マイク停止を通知。未保存状態を保持し、別タブ再ログインを案内する |

`ContextVar`はセッション失効と信頼された終了処理の検査だけに使う。DBやファイルの利用者選択は、可変のcontextへ依存せず、認証後に選んだtenant applicationの固定オブジェクトで行う。

認可検査は要求・各SQL／commit・I/Oの検査時点に行う。すでに完了した処理を停止操作で取り消す仕様ではない。終了前のstreamは監視によって切断する。HTTP経路の不存在応答は既存APIに従い、通常404、ページcatalogの欠落409、編集sessionの欠落403を維持し、他利用者のIDと存在しないIDで同じ応答にする。

## 構成・起動

- `backend/app/cloud/`：設定、共通registry、Cognito検証、tenant runtime選択、host管理コマンド。
- `backend/app/main.py`：`APP_ENV=cloud`の場合だけ認証rootを起動。tenant側も既存router／lifespanを再利用。
- `backend/alembic/env.py`：明示された専用connectionでの移行を追加。クラウド起動時にローカル.envのDBを移行しない。
- `deploy/compose.cloud.yaml`／`deploy/Caddyfile.cloud`：HTTPS・認証構成のoverlay。APIポートはpublishしない。proxy headerはこのprivate Compose境界内だけで受ける。
- `frontend/src/auth/`：ログイン境界、CSRF付き通信、利用者別ブラウザー保存。既存API・編集・音声の通信入口へ接続。

必須設定は `BETA_ROOT`（専用の新しいDATA_DIRと同じ）、`BETA_PUBLIC_ORIGIN`、`BETA_SITE`、東京の `BETA_COGNITO_POOL_ID`／`BETA_COGNITO_CLIENT_ID`／`BETA_COGNITO_DOMAIN`。root直下に既存のapp.dbがある場合は拒否する。registry schemaは独立したversion 1、業務DBの現行revisionはtemplate_analysis_v1のまま。専用ディレクトリーを0700、registry DBを0600にする。

有料AIはフェーズ5の共通quota接続まで閉じる。cloud起動はOCR openai、voice openai、API keyの存在を拒否する。利用者別DBの既存ApiBudgetを、ベータ全体の利用上限として使ってはならない。追加1,000回を各tenantへ複製しない。

AWS CLIプロファイルは引き続き **abe-deploy-user**、account **535562976288**、東京 **ap-northeast-1**。このフェーズではAWSへ接続していない。ドメイン／user pool／app clientは未作成・未指定で、検証用の値を本番IDとして扱わない。

## 検証

証拠は `D:\llm-ocr-archive\beta\20261015-phase3`。通常DB・過去の封印・旧checkoutと混ぜない。

| 層 | 結果・範囲 |
| --- | --- |
| 署名＋実ASGI／SQLite | `acceptance.py`、127条件PASS。制御したOIDC HTTP応答と実RSA検証。二利用者、PKCE、state再利用拒否、issuer/audience/token_use/nonce/期限/署名/algorithm拒否、未招待拒否、CSRF、他利用者の参照／変更／削除／retry／編集token／SSE／音声拒否、停止／再有効化／logout／再起動 |
| 保存・出力 | フェーズ2の専用2ページfixtureをコピー。`00789`／`60000`と確定状態、layout JSONを保持。出力XLSXのSHAが変更前と完全一致。通常DBは使用しない |
| 失効・非同期 | 失効した認証contextでcommitを拒否しrollback。二利用者の実runner＋DB lifecycleを制御遅延付きの既存handlerで確認し同時実行最大1、ブラウザーcontext継承0。入力欠落の2jobは意図どおりFAILED。成功OCR・native描画の再評価ではない |
| 実HTTPS／コンテナー | `http-acceptance.py`、18条件PASS。新規WSL専用data・5299、実Caddy/FastAPI/SQLite。二利用者の一覧／変更／ファイル・XLSX取得／編集session作成・context／CSRF／拒否、既存出力SHA、PKCE redirectを確認。認証cookieは試験fixtureをhost側で発行し、実Cognitoログインと区別 |
| frontend | 対象5ファイル47テストPASS、TypeScript＋Vite build PASS。未ログイン非表示、失効時のdraft保持、logout失敗、CSRF、通信先制限、利用者別保存、既存編集／解析／音声取得の対象条件 |
| 静的検査 | backend対象ruff、17 source filesのmypy。全体回帰はフェーズ6で実施 |
| Chrome | 新しい隔離contextでHTTPSのログイン画面を確認。ローカルCAの証明書例外はその試験contextだけ。OS証明書storeへ登録していない |

検証中の失敗も保存した。`acceptance-final-2.log`では、Docker buildと並行したWindows ASGIの既存cv2.cvtColorでUnknown C++ exceptionが1回発生した。原因は未確定で、同じ入力・最終コードの直列再実行は127条件PASS。実Linux HTTPSでも同じ編集session作成・context取得を確認した。OpenCV処理・期待値を変更して隠していない。再起動直後のHTTP検証開始では一度502に遭遇し、startup完了後に18条件が通過した。公開前の負荷・起動待ちの受入はフェーズ5／6／7に残す。

実AI API **0**、実AWS API **0**。既存開発台帳 **440/924・残484**、ベータ追加1,000回は未設定・未使用。Cognito token交換・JWKSの制御応答は実AWS呼出しではない。

## 未検証・次工程

実AWSのCognito設定、招待アカウントによる実ログイン、実ドメイン／ACME、AWS4GiB負荷、実マイクは未検証。現地の設定・実受入はフェーズ5／7で行う。Caddy証明書領域の永続化、共有quota、バックアップ、削除、運用手順はフェーズ5で仕上げる。この段階のcompose overlayだけで公開完了としない。

次はフェーズ4＝GPT-6.1 Sol / High。通常画面のベータ化、失敗・復旧表示、音声導線、フェーズ2で記録したpaged jobが単一結果画面へ進む既存不具合を修正する。精度V6や5秒応答改善へ戻らない。
