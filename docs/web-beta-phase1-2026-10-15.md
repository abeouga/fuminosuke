# 文之助 Webベータ：フェーズ1の仕様と実装境界

2026-10-15。設計担当はAstra / High。対象は `C:\Users\hanam\Desktop\llm-ocr-deploy`。旧 `llm-ocr` は変更しない。ゴールは作成しない。

フェーズ1は仕様、保持基準、実装順序の固定までとする。クラウド実装・配備は後続工程であり、現在のローカル製品を公開可能と認定したものではない。製品コード・DB・設定・AWSリソースは本工程では変更しない。

## 1. ベータの提供範囲

招待された複数利用者がWebブラウザーで、テンプレート作成、帳票OCR、人間による確認・補正・確定、XLSX取得まで行う。利用者間でデータを共有せず、必要なテンプレートだけ新しいクラウドDBへ取り込む。通常ローカルDBをクラウドDBへ転用・移行しない。

| 項目 | ベータの決定 |
| --- | --- |
| 入力・出力 | 現行の画像／XLSX由来テンプレート、複数ページを含む既存対応範囲を維持する。画像だけの製品へ縮小しない |
| AIテンプレート | 現在の生成モデル・prompt・判断方式を基準とする。案の反映と明示保存を分け、手描き・手修正を保持する |
| 音声編集 | 連続音声、行＋列の指定、直前対象を使う修正、局所録音を残す。現在の応答精度を改善する別実験は行わない |
| 画面 | 業務用の簡潔な画面。AI編集枠、複数セル同時強調、移動・スクロール・表名表示は既存の操作意図を維持する |
| アカウント | Cognitoによる招待制。自己登録を無効化し、各利用者のデータを非公開にする |
| AI費用 | 運営者のAPIキー。利用者別・全体の送信上限をサーバー側で管理する |
| 保持 | 利用者が削除するまで保存。削除後のバックアップ残存期間を表示する |
| 運用 | 単一サーバー停止を許容。AWS費用は月1万円以内を予算条件とし、AI API料金は別枠 |

初期設定値は招待10名、同時ブラウザー利用3名、重い処理1件、保存500MiB/人・全体5GiBとする。これらは初期運用上限であり、性能実測値ではない。3名が同時に重い処理を開始した場合は待機順を表示する。

精度改善と「最初の正しい案が5秒以内」はベータ公開の条件から外す。旧V5実験は停止したまま、[判断配列の修正設計](template-ai-region-decision-repair-design-2026-10-15.md)のV6は保留する。精度が不十分なときに誤案を強制保存したり、対応関係の検査を外したりしない。

## 2. 現在の実装と公開前の不足

| 現行の根拠 | 現在の状態 | 後続の処置 |
| --- | --- | --- |
| `backend/app/main.py`、`api/v1`、`editing`、`template_analysis` | ローカルAPI、編集・音声・AI解析の経路がある。Cognitoの利用者認証は未接続 | 全経路へ同じ認証・所有権境界を接続する |
| `backend/app/db/models.py` | Template、ProcessingJobなどにクラウド利用者の所有者がない | 所有ルートと子資源の関係を固定して移行を実装する |
| `backend/app/core/config.py`、`storage/local_storage.py` | SQLiteとローカルファイルを前提にする | 同じ保存方式を暗号化EBS上で使う。別の保存サービスへ全面変換しない |
| `jobs/runner.py`、`repositories/job_repository.py` | 起動時にQUEUEDを再投入。PROCESSINGはINTERRUPTED、未完成CREATEDはFAILEDへ移す | 無条件の再送を増やさず、利用者・送信予約・受付snapshotを伴う復旧を検証する |
| `services/renderer_process.py`、`renderer_wsl.py` | Windowsプロセス制御／WSL bridgeを含み、reference描画はWindows font sourceを要求する | Linux専用transportと隔離実行を実装する。OS判定の削除だけでは移植にならない |
| [V5実測](template-ai-region-v5-live-2026-10-15.md) | 固定範囲10入力の位置は一致したが、矛盾する保留で案0件、ASGI 11,208.887ms | 既知制約として扱い、手動で続行できるUIを提供する。精度合格と表示しない |

AI実装の保持基準は `region-schema.v1 / region-pack.v3 / visual-runner.v9`。UI接続は既に存在するため、READMEの「バックエンドのみ」という旧説明を現在の状態には使わない。現行画面が存在することと、クラウドで有効・受入済みであることは別である。

## 3. AWS接続先

| 設定 | 固定値・状態 |
| --- | --- |
| 使用CLIプロファイル | **`abe-deploy-user`** |
| 入力名との照合 | 指定の `abe-depoloy-user` は既存プロファイル `abe-deploy-user` の表記違いとして解決。後者でSTSの接続確認に成功 |
| AWS account | `535562976288` |
| 確認した操作主体 | `arn:aws:iam::535562976288:user/abe-deploy-user` |
| 配備リージョン | `ap-northeast-1`（東京） |
| 公開ドメイン | 未指定。フェーズ7のDNS・HTTPS・Cognito callback確定前に必要 |
| 本工程のAWS操作 | STS identityとPrice Listの読取だけ。リソース作成・変更・メール送信なし |

以後の配備コマンドはプロファイルとリージョンを明示し、実行前に上記accountとの一致を確認する。既定プロファイルや `1-test-farmbord` に依存しない。AWSアクセスキーは文書・Git・イメージへ入れず、サーバーは専用instance roleを使う。今回の読取成功はEC2/Cognito作成権限を保証しない。

## 4. 単一サーバーの構成

東京のEC2 x86 `t3a.medium` 1台、CPU creditはStandard、Docker Compose、Caddy、frontendのproduction build、FastAPI 1プロセスとする。データ用EBSを独立させ、コンテナーの再作成でDBと原本を失わない。root20GiB＋data30GiBのgp3、暗号化、基準IOPS/throughputから開始する。

公開入口はHTTPSだけとし、80はHTTPS誘導・証明書取得用途、APIポート・DB・rendererは外部公開しない。Caddyが静的画面とAPIを同一originで配信する。管理接続はSSMを基本とし、広いSSH開放を前提にしない。RDS、Redis、SQS、ALB、NAT Gateway、複数API workerは追加しない。

コードの責務は既存の業務処理、認証・所有権、送信予約、実行環境adapterの4境界に限定する。HTTPルートやReactに保存規則・所有権照合を複製しない。既存共通の保存・revision・確定経路を利用し、クラウド専用の第二の保存経路を作らない。

### 認証・利用者の所有権

2026-10-15フェーズ3で保存境界を具体化した。共有認証registryがCognito subと内部tenant IDを対応付け、業務DB・storage・runtimeを利用者別に分離する。以下のTemplate/Jobを根とする所有関係はそのtenant領域内で成立し、全queryへowner条件を複製する方式は採らない。既存の共通保存経路を再利用する。変更理由・検証・後続のbackup/quota境界は[フェーズ3記録](web-beta-phase3-2026-10-15.md)を参照する。

Cognitoのauthorization code＋PKCEを使い、backendでissuer、署名、audience/client、期限、state、nonceを検証する。Cognitoのsubを利用者identityとし、表示名やemailを所有者IDにしない。ブラウザーにはSecure/HttpOnly/SameSite cookieによる期限付きセッションを渡す。変更APIにはCSRF対策を設け、tokenやAPIキーをlocalStorageへ置かない。アカウント停止はアプリ側の有効状態でも拒否し、既発行セッションの期限だけに依存しない。

TemplateとProcessingJobを所有ルートにする。版・definition・candidate・packageはTemplateへ、input・artifact・result・revision・paged review・confirmation・eventはJobへ帰属する。assistant/editing/analysisのsession・turn・command・run・eventも作成者と対象所有ルートを固定する。session内の対象を変更するときも所有権を再検証する。

一覧、直接ID参照、複製、import/export、download、SSE、音声接続、background workerを含め、取得時と書込時の両方で同じ所有ルールを使う。リクエストのowner指定は信用しない。ジョブが参照するTemplateVersionも同一利用者に限定する。非所有資源は存在を漏らさない応答に統一し、署名なしの静的artifact公開を禁止する。

### LinuxのExcel描画

APIコンテナーへDocker socketや任意コンテナー起動権限を与えない。ホストの専用renderer supervisorが、制限したUnix socket経由で固定image digest・固定command・job tokenだけを受け付ける。任意path、image、mount、commandをAPIから指定できない小さい契約とする。

1 jobにつき専用入力・出力だけをmountした使い捨てコンテナーを起動する。network none、非root、read-only root、capability除去、no-new-privileges、PID/CPU/memory/wall-time制限を設定する。他利用者のディレクトリ、APIキー、AWS資格情報、instance metadataへ到達させない。PDF parse/rasterもAPIプロセス外に置く。

最初の資源上限案はrenderer2GiB・1CPU・同時1件。OS/API/画像処理分を含む4GiBでの実測をフェーズ2の終了条件とする。OOM時は処理を失敗状態にし、制限解除や自動的な上位instanceへの変更で隠さない。

Linuxではruntime、font、geometry、adapterの新しいpolicy identityを発行する。旧Windows/WSLの資格をPASSに書き換えない。新規・importテンプレートは新環境で候補準備し、ページ、原本SHA、mapping/crop、入力bindingの検査を維持する。再利用可能なfontを固定し、Windows fontを無断でimageへ同梱しない。代替fontによる座標の不一致は停止理由とし、代表帳票で成立しなければフェーズ2を完了にしない。

## 5. API回数・データ保持・復旧

2026-10-16実装の具体化は[フェーズ5](web-beta-phase5-2026-10-16.md)と[運用手順](web-beta-operations.md)を参照する。新規backupは固有keyの非versioning bucketで上書きを拒否し、7日を過ぎたmanifestを復元対象から除外する。S3物理削除は非同期なので「最大7日以内の消去完了」は保証しない。journalは別bucketで期限なく保持する。保存容量は受付ガードであり、背景artifactのhard quota・実負荷は公開前受入に残る。以下の仕様はこの具体化と併せて読む。

開発台帳は **440/924、残484回**。追加許可の **1,000回はWebベータ用の別枠**とし、この工程で既存台帳を増額・初期化しない。新枠を有効化した時点以降、ベータ準備と公開後の実送信を同じ枠で数える。初期上限は20回/人/JST日、100回/全体/JST日、累計1,000回。日付変更で累計枠は戻さない。

課金対象providerへ送る前にoperation/attempt ID単位で予約する。OCR、音声認識、判断、AIテンプレート、再試行を横断する。失敗や応答不明でも枠を返さず、自動SDK再試行も独立した送信として数える。音声接続が継続課金される経路には接続時間・音声長・出力token上限も設ける。回数制限だけで金額上限を保証しない。

初期構成では同一サーバーの予約を直列化する。SQLiteの予約に加え、個人情報を含まない予約ID・仮名化利用者ID・日時・種別を、復元対象DBとは別のS3 prefixへ送信前に追記する。上書き・削除を実行roleに許可しない。外部記録の成否が不明ならproviderへ送らない。復旧時はこの外部予約一覧とDBを突き合わせ、孤立予約も消費済みとして再構築する。一致を確認するまでAI送信を止める。原音・原文・帳票内容はこの台帳に含めない。

バックアップは1日1回、7日保持。新規書込とjob実行を短時間停止し、SQLite backupと同じ時点の原本・artifact manifestをS3へ保存する。WAL中のDBファイルだけをコピーしない。RPOは最大24時間、RTOは2時間を目標とし、復元試験で測る。7日以内の削除データがバックアップに残ることを表示し、復元時は別管理の削除記録を適用してから公開する。S3 versionと未完了uploadも保持期限・回収対象に含める。

利用者の削除では所有ルートと従属資源の参照を検査し、実行中jobを停止してから削除する。自動再生成や別ユーザーの原本削除を防ぐ。原音は処理後に破棄し、本文をaccess logに記録しない。障害記録はIDと安全なエラー分類を中心とする。

## 6. AWS費用の見積もり

2026-10-15に `abe-deploy-user` でAWS Price Listを読取。Tokyo Linux/Shared/NA/Usedのt3a.mediumは **0.049 USD/h**（SKU `7622ZVSJPAVXJEF9`）、Tokyo gp3は **0.096 USD/GB-month**（SKU `C8Y3GJZBQTH8T5JV`）。回答JSONは工程証拠へ保存した。

| 項目 | 条件 | 月額USD |
| --- | --- | ---: |
| EC2 | 0.049 × 730時間、Standard CPU credit | 35.77 |
| EBS | gp3合計50GiB、追加IOPSなし | 4.80 |
| Public IPv4 | 1個 × 0.005 × 730時間 | 3.65 |
| 周辺サービス予備費 | S3最大約35GiBの7世代、台帳、小規模ログ、DNS、image保管、secret、転送・要求 | 7.00 |
| 合計 | 周辺サービスは実測前の予算配分 | **51.22** |

為替を **1USD=160円と仮定**し税相当10％を加えると約 **9,015円/月**、744時間月では約9,148円。市場為替を確認した値ではない。新規ドメイン取得費は未算入で、既存サブドメイン利用を想定する。Cognito Liteは10名程度なら無料枠内を想定するが、account/organization共有枠の消費とメール費用を配備前に確認する。AI API料金は含まない。

為替180円の場合、730時間でも約10,142円となり予算を超える。したがって配備前に実為替・税・選択ドメイン・転送量を入れて再計算し、1万円を超える案をそのまま配備しない。CPU Standard、保存上限、ログ14日・容量制限、ダウンロード転送上限を設定し、7,000/8,500/10,000円の運用警戒値を用意する。予算通知には遅延があり、請求の厳密な上限ではない。

料金根拠：[EC2 pricing](https://aws.amazon.com/ec2/pricing/on-demand/)、[EBS pricing](https://aws.amazon.com/ebs/pricing/)、[Public IPv4 pricing](https://aws.amazon.com/vpc/pricing/)、[Cognito pricing](https://aws.amazon.com/cognito/pricing/)、[AWS Budgetsの通知遅延](https://docs.aws.amazon.com/cost-management/latest/userguide/budgets-managing-costs.html)。

## 7. 固定した入力と受入条件

本工程の証拠は `D:\llm-ocr-archive\beta\20261015-phase1`。`before.json`は両checkoutの全対象ファイル、通常DBの全テーブル内容、schema、設定hash、保存artifact、従前証拠を固定する。秘密の設定値を複写しない。

`baseline-inputs.json`には次を絶対pathとSHA256で固定した。

- V5-G1-01のrequest、snapshot、実provider response、analysis result、assessment、timing。矛盾応答が案0として止まる既知失敗を保存する。
- 同試行の `frozen-correct-control.json`。正解制御入力であり、モデルの実成功として数えない。
- `docs/reference-renderer-evidence/saved-two-pages.xlsx` と2枚のPNG。旧描画の比較入力であり、Linux描画の認定結果ではない。
- V5停止記録と封印manifest。旧実験は再送しない。最終未使用4帳票は開かず、ベータ調整へ使わない。

通常OCR、レビュー、出力の保存データは `before.json` の全テーブル／artifact hashで保持する。後続工程の実行用コピーはこの基準から作り、個別caseで使ったjob/version/input IDを追記してから実行する。現段階で全業務シナリオの再実行成功を主張しない。

| 受入ID | 必須操作と期待結果 |
| --- | --- |
| B01 | A/B二利用者でTemplate、Job、artifact、編集session、analysis、SSEのIDを相互指定。存在・画像・値が漏れず書込0 |
| B02 | XLSXの複数ページを取り込み、準備・有効化・OCR・全ページ補正・確定・XLSX取得。原本/ページ/欄/出力対応が一致 |
| B03 | AI案なし・不正応答・timeoutでも手描きと保存を継続。正解制御入力で案反映→手修正→競合→再読込を確認 |
| B04 | 連続音声の行＋列編集、直前対象への訂正、局所録音、停止・再接続を実マイクで確認。ここではユーザーを呼ぶ |
| B05 | 同じ操作の再送、複数tab、日次/全体上限、provider timeout、再起動で二重適用・予約漏れ・枠復活がない |
| B06 | rendererのネットワーク、他jobファイル、秘密、任意commandを拒否。timeout/OOM/親停止後もAPI継続・所有子だけ回収 |
| B07 | ユーザー削除後の再取得拒否、バックアップ復元後の削除維持、台帳巻戻し拒否、確定revision保持 |
| B08 | 3ブラウザー＋重処理待機で通常編集可能。実4GiB環境で起動・停止・再起動・restore・ログ回収を確認 |

B02の具体的な通常OCR実行fixtureとB04の発話一覧は、対応工程で固定した入力IDから作る。未整備のfixtureを「通過」と扱わない。対象短時間integrationと実画面を先に行い、広い回帰はフェーズ6へ集約する。旧checkoutの退役した `verify_ocr_contract.py` や過去全件成功数を流用しない。

## 8. 工程と終了条件

| フェーズ | 担当モデル・強度 | 成果・終了条件 |
| --- | --- | --- |
| 1 仕様とbaseline | Astra / High | 本書、AWS profile/account、入力hash、保持比較、費用前提、変更境界を固定。ドメイン選定は配備前依存として残す |
| 2 コンテナー・renderer | GPT-6.1 Sol / High | 同じ画像から起動できる構成、Linux専用adapter、B02の描画/対応とB06、4GiB資源制限。ライセンス/画像/対応の差を説明 |
| 3 認証・所有権 | GPT-6.1 Sol / High | Cognito接続、全APIと非同期経路の所有権、B01。新規クラウドDBだけへschema導入 |
| 4 製品UI | GPT-6.1 Sol / High | 通常導線へAI/音声を接続、ベータ表示、失敗・待機・上限・手動継続、B03。B04は実マイク前まで準備 |
| 5 運用・配備定義 | GPT-6.1 Sol / High | 送信予約、削除、backup/restore、infra定義、手順、B05/B07/B08の隔離検証 |
| 6 審査・回帰 | Astra / XHigh | 所有権、renderer、復旧、保存の独立工程審査。関連回帰とfrontend build。公開阻害事項0 |
| 7 AWS受入・公開 | Astra / High | domain/費用再確認、AWS配備、実HTTPS/Cognito/browser、B02/B04/B08、配布可能なベータと運用手順 |

工程ごとにモデルを切り替える。別エージェントを自動起動しない。今はフェーズ1だけを実行し、2以降へ先行しない。認証／保存／対応関係／隔離の重大不具合は公開阻害事項とし、精度不足や5秒超過だけを理由に再び精度研究へ戻らない。

## 9. フェーズ1の検証記録

製品コード、通常データ、旧checkout、設定、前証拠の保持は `preservation.json`、終了時snapshotは `after.json`、本工程成果のSHA256は `completion-1/hashes.json` を正本とする。現在の実装・参照pathとの照合、文書間の現行優先順位、`git diff --check` を検証する。

API実送信0、通常台帳の予約0。AWSはidentity/priceの読取だけ。動作test、build、実マイク、Linux renderer、AWS配備は本工程では実施しない。文書だけの変更として既存OCR contractを維持し、ベータの実装済み・公開済みとは報告しない。
