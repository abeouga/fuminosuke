# Webベータ フェーズ5：共通上限・削除・復元・配備定義

2026-10-15開始、2026-10-16終了。計画上の担当はGPT-6.1 Sol / High。対象は `C:\Users\hanam\Desktop\llm-ocr-deploy` のみ。[全体計画](web-beta-phase1-2026-10-15.md)の運用工程を実装した。実AWS配備、有料AIの有効化、公開、ゴール作成、別エージェント、Git書込は行っていない。

## 実装した境界

| 対象 | 変更 |
| --- | --- |
| `backend/app/cloud/{journal,operations,lease,backup,maintenance}.py` | 共通予約、外部追記、削除再適用、runtime/保守の排他、SQLite backup・検証復元、ホスト専用保守入口 |
| `backend/app/cloud/{application,config,registry,tenants}.py` | 共通予算のtenantへの注入、明示AI有効化、容量・要求サイズ、容量超過時の取消、削除済み利用者の失効、registry v2への既知v1移行 |
| `backend/app/ai/{api_budget,budget_contract}.py`、`db/session.py`、`core/lifecycle.py` | 既存の全providerが使うApiBudgetから共有予算へ接続。通常ローカルの924回枠は維持。音声の既存ledger FKだけは参照用mirrorを業務transactionへ追加し、上限は共有側だけで判定 |
| `backend/app/assistant/stream_proxy.py` | 共通予約を伴う旧音声接続・音声frame処理もthreadへ待避し、S3待ちでevent loopを止めない。発話順序・認識方式は維持 |
| `backend/app/core/{config,logging}.py` | cloud専用secret-file読込、cloudログの容量制限・起動時rotation。通常ローカルの設定・ログ方式は維持 |
| `backend/app/services/{job_service,template_management_service}.py` | 検査後・変更前の削除記録。使用済みテンプレートの版IDはarchiveで保持、未使用は版→親の順で削除 |
| `backend/{pyproject.toml,requirements.lock}` | 条件付きS3追記とbackup用のboto3・推移依存を固定 |
| `deploy/infra.yaml`、`compose{.cloud,.ai,.ops}.yaml`、`cloud.env.example`、`frontend.Dockerfile`、`ops.sh`、`fuminosuke-backup.{service,timer}` | 配備定義、通常はAI停止・追加overlayで明示有効化、証明書永続化、専用保守mount、日次backup・同じAI設定への再起動 |
| `frontend/src/auth/AuthBoundary.tsx` | 7日間の復元対象と削除反映の遅延を表示 |

上限は20回/人/JST日、100回/全体/JST日、累計1,000回。S3への条件付き追記→ローカルindexのcommit→送信許可の順にする。失敗・不明・rollbackで払い戻さない。復元では外部台帳から孤立予約も消費済みとして取り込む。音声は300秒を前払いし、利用者ごとの累計1,200秒まで。回数だけで金額の厳密な上限を保証するものではない。

通常DBにowner列・新migration・cloud quotaを追加していない。provider/model/prompt/schema、領域/crop、association/mapping、補正・確定・出力生成は変更せず、既存の業務保存経路を再利用した。

## 削除で再現した既存不具合

同じ保存済み2ページ帳票で、変更前のtemplate削除が `REVISION_CONFLICT` になることを再現した。変更前ソースをphase4 imageから抽出し、開始時SHAと照合した。版と親のflush順を分ける必要があった。

また使用済み版を完全削除するとjobの版IDがNULLとなり、`PAGE_OUTPUT_SNAPSHOT_REQUIRED` でXLSXを取得できなくなることを専用コピーで確認した。使用済みは版データをアーカイブで保持し、新規OCRと版変更を止める。画面の既存の削除説明に動作を一致させる修正である。過去jobの版ID・snapshot・revision4・値・画像・出力対応は維持する。未使用だけを完全削除する。

`deletion-comparison.json` に旧競合→新archived、job/input/artifact/review/confirmation全表の不変、確定XLSX SHA一致を記録した。

## 検証と証拠

正本は `D:\llm-ocr-archive\beta\20261015-phase5`。最終保持比較は `preservation.json`、終了snapshotは `after-final.json`、固定成果は `completion-1/hashes.json`。

- 実cloud ASGI・SQLite・既存保存/出力＋制御S3/HTTPで **39条件成功**。個人/全体/累計上限、20並列予約、JST境界、音声前払い・業務rollback・stream/audio FK、S3応答不明/欠落/障害、削除と復元、容量超過時の取消、registry移行/旧binary拒否、ログ容量/期限、台帳待ち中のevent loop継続と失効後送信拒否を確認。`acceptance-final-9.log`、`acceptance/5ada62ec5a714c5d9f73b308b49e7f3d/result.json`。
- backup後のjob/template/tenant削除を復元先へ再適用し、他利用者の確定XLSXが同じSHAであることを確認。SHA/size・SQLite integrity検査、session無効化、破損・期限切れ・path traversal・未完成restoreの拒否も確認。制御fixtureのbackup約3.74秒、restore約2.25秒は実AWSのRPO/RTO実測ではない。
- 音声の参照mirrorは独立した上限を持たない。mirrorと業務データがrollbackしても外部予約は残る。実マイク・実音声APIは使用していない。
- production入口 `sh /app/start-backend.sh` とCaddyで実HTTPS **8条件**、Chromeの保持期間表示・logout、backend/frontend再起動・証明書保持・非root/read-only/cap除去・API非公開を確認。専用CAと合成招待sessionを使用し、実Cognitoログインではない。この実runtimeでは外部台帳を無効にし、AIを停止している。共通運用境界は前記の制御ASGI/SDKで検証した。
- frontend対象 **8テスト**とbuild成功。対象19 backendファイルのruff/mypy成功。削除serviceは開始時ソースとの比較で追加指摘0。既存ruff3件・mypy5件は残り、全backend静的検査成功とは扱わない。
- `cfn-lint 1.40.4` の東京向け配備定義検査成功。Compose、shell/systemd構文、実行imageとcheckout/distも照合する。実配備、AMI内packageの実導入、IAM/S3 policyの実AWS評価は未実施。

Composeの通常AI停止/明示有効化・origin・証明書volume・保守mount、shell/systemd構文、cloud専用secret-fileと二重設定拒否は11条件で確認した。最終imageのbackend395ファイル・frontend16配布ファイルは現checkout/distと一致。専用5305は検証後に停止し、合成アカウントを無効化、session0・両コンテナーPID0を確認した。通常利用のruntimeは停止していない。

代表的な入口はbackend仮想環境から `python -B acceptance.py`、`python -B deletion-baseline.py`、`python -B http-acceptance.py`、`python -B lifecycle-check.py`。前2つは新しい専用データ、後2つは専用5305 runtimeを使う。全件unit/E2Eは実行せず、広い回帰はフェーズ6へ集約する。

## 保持・残条件・次工程

実AI API **0回**、AWSリソース作成・実S3/Cognito API **0回**。通常開発台帳 **440/924・残484**。beta追加1,000回は実環境で未初期化・未消費。1,000回上限試験は専用仮台帳で、許可枠から差し引かない。最終未使用4帳票は使用していない。通常データ・schema・全業務表・artifact・設定hash・旧checkout・前封印を照合し、既存OCR contractを維持した。

容量500MiB/人・全体5GiBは受付時ガードで、背景artifactの瞬間最大容量のhard quotaではない。実S3/IAM、実Cognito、実マイク、実4GiB・3ブラウザーの継続負荷、全容量backup/restore、公開ドメイン・費用は未受入で、公開前に検証する。復元対象は7日間だがS3物理削除は非同期であり、絶対的な「最大7日で消去」とは表示しない。

運用は [Webベータ運用手順](web-beta-operations.md) を正本とする。registry v2と共有台帳を旧binaryへ単純rollbackしない。次は **フェーズ6：Astra / XHigh** の独立工程審査と関連回帰。別エージェントを自動起動せず、精度V5/V6の研究へ戻らない。合格後にフェーズ7の実AWS・実マイク・公開受入へ進む。
