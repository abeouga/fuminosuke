# 牛繁殖台帳 OCR ローカルプロトタイプ

牛繁殖台帳の画像から、帳票位置合わせ、テンプレート復元、手書き分離、領域切り出し、OCR、目視修正、確定JSON保存、個体識別番号による検索、JSON/CSV出力までを行うローカル専用ワークベンチです。

- Backend: FastAPI / Python 3.12 / OpenCV / SQLite / SQLAlchemy / Alembic
- Frontend: React / TypeScript / Vite
- Storage: `backend/data/` のローカルファイルのみ
- Queue: FastAPIプロセス内の有界キュー（既定同時実行数1）
- OCR: `mock`（既定）、`disabled`、設定時のみ`openai`
- Bind: 既定 `127.0.0.1`。クラウドやSpring Bootは不要

## 最短の起動手順（Windows）

必要環境は Python 3.12、Node.js 20.19以上、PowerShell 5.1以上です。

```powershell
cd C:\path\to\llm-ocr
.\scripts\setup.ps1
.\scripts\dev.ps1
```

エクスプローラーまたはコマンドプロンプトから起動する場合は、初回だけ`setup.bat`、以後は`start.bat`を実行できます。

起動後（通常時）:

- UI: <http://127.0.0.1:5173>
- OpenAPI: <http://127.0.0.1:8000/docs>
- Health: <http://127.0.0.1:8000/api/v1/health>

`start.bat` は操作画面を自動的に開きます。8000番または5173番が使用中の場合は空いている次のポートへ切り替えるため、その場合は起動画面に表示されたURLを使用してください。

PowerShellの実行ポリシーで`.ps1`を実行できない場合は、そのターミナルだけ一時的に次を使えます。

```powershell
Set-ExecutionPolicy -Scope Process Bypass
```

`setup.ps1`は固定済みの`backend/requirements.lock`と`frontend/package-lock.json`を使用し、`.env`作成とmigration適用も行います。

## 手動セットアップ

```powershell
cd backend
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock
.\.venv\Scripts\python.exe -m pip install --no-deps -e .
Copy-Item .env.example .env
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

別ターミナル:

```powershell
cd frontend
npm.cmd ci
npm.cmd run dev -- --host 127.0.0.1
```

## 使い方

1. 「テンプレート」で次のいずれかを選びます。
   - `REFERENCE_TEMPLATE`: 本物の白紙帳票1枚。推奨
   - `MULTI_SCAN_CONSENSUS`: 同一書式の記入済み画像3枚以上
   - `SINGLE_IMAGE_EXPERIMENTAL`: 記入済み画像1枚。実験的
2. DRAFTの比較画像、品質、マスクを確認し、必要ならCanvasでマスクを編集します。
3. 承認してACTIVEにします。自動承認はしません。
4. 「読み取り」でACTIVE版と台帳画像を選びます。OCRを切って画像処理だけ行うこともできます。
5. `raw`、`normalized`、`status`、候補、確認理由、対応cropを見ながら修正します。
6. 修正を保存し、確認ダイアログを経て確定します。確定後の候補は変更できません。
7. 確定結果は10桁の個体識別番号に紐づくJSONとして保存されます。
8. 「個体検索」で番号を入力し、紐づく確定JSON全体を画面またはAPIから取得します。
9. 必要に応じてJSONまたはUTF-8 BOM付きCSVをファイル出力します。

## アーキテクチャ

ReactはAPIキーを持たず、FastAPIだけが画像検証と外部OCR設定を担当します。CPU負荷の高いOpenCV処理はジョブワーカーからスレッドへ渡され、HTTP応答をブロックしません。状態・revision・監査イベントはSQLite、画像とJSONはDB登録済み相対パスで`backend/data/`へ保存します。

ジョブ状態は `CREATED → QUEUED → PROCESSING → REVIEW_REQUIRED → CONFIRMED` です。失敗、取消、中断を別状態にし、起動時に残った`PROCESSING`は`INTERRUPTED`へ回収します。修正は`baseRevision`による楽観ロックで、元OCR候補、現修正版、revision履歴、確定結果を分離します。

## FastAPIエンドポイント

| Method | Path | 用途 |
|---|---|---|
| GET | `/api/v1/health` | DB、worker、OCR providerの安全な状態 |
| GET | `/api/v1/templates` | テンプレート一覧 |
| GET | `/api/v1/templates/{templateId}` | versionと品質を含む詳細 |
| POST | `/api/v1/template-jobs` | 3方式のテンプレート準備 |
| POST | `/api/v1/template-versions/{versionId}/approve` | DRAFTをACTIVE化 |
| POST | `/api/v1/template-versions/{versionId}/reject` | DRAFTを却下 |
| PUT | `/api/v1/template-versions/{versionId}/handwriting-mask` | 編集マスク保存 |
| POST | `/api/v1/template-versions/{versionId}/reprocess` | 新attemptで再生成 |
| POST | `/api/v1/ocr-jobs` | 台帳画像処理・OCR開始 |
| GET | `/api/v1/ocr-jobs/{jobId}/result` | REVIEW_REQUIRED/CONFIRMED結果 |
| PUT | `/api/v1/ocr-jobs/{jobId}/corrections` | revision付き修正保存 |
| POST | `/api/v1/ocr-jobs/{jobId}/confirm` | 最終検証・確定 |
| GET | `/api/v1/ocr-jobs/{jobId}/export?format=json|csv` | 確定結果出力 |
| GET | `/api/v1/individual-records?individualId={10桁番号}` | 個体識別番号に紐づく確定JSON一覧 |
| GET | `/api/v1/jobs/{jobId}` | 進捗と安全なエラー |
| POST | `/api/v1/jobs/{jobId}/retry` | FAILED/INTERRUPTED/REVIEW_REQUIRED再試行 |
| POST | `/api/v1/jobs/{jobId}/cancel` | 取消または取消要求 |
| DELETE | `/api/v1/jobs/{jobId}` | DBと関連ローカルファイル削除 |
| GET | `/api/v1/jobs/{jobId}/artifacts` | 登録済み生成物一覧 |
| GET | `/api/v1/jobs/{jobId}/artifacts/{artifactName}` | 登録済み生成物配信 |

エラーは共通`ProblemDetails`です。クライアントから任意ファイルパスを受け取る配信APIはありません。

## SQLite migration

`0001_initial_schema.py`が次の9テーブルを作成し、`0002_individual_record_index.py`が確定結果へ個体識別番号の検索用インデックスを追加します。

- `templates`, `template_versions`
- `processing_jobs`, `job_inputs`, `job_artifacts`, `job_events`
- `ocr_results`, `ocr_result_revisions`, `confirmed_results`

```powershell
cd backend
.\.venv\Scripts\python.exe -m alembic current
.\.venv\Scripts\python.exe -m alembic upgrade head
```

## OpenCVパイプライン

- Pillowによるmagic/MIME/画素数/辺長検証、EXIF orientation、alpha白背景化
- 外周輪郭検出、四隅順序化、透視変換、canonical化、照明補正
- ORB特徴点、KNN ratio test、RANSAC homography、空間分布・coverage検査
- ECC affine微調整、edge overlap。品質閾値未満は成功扱いしない
- 罫線検出と保護、テンプレート差分、Lab色差、edge residualによる手書き確率
- morphologyとconnected componentsによるmask cleanup、印刷重なりをunknownとして記録
- aligned color、clean reference、handwriting only、enhanced grayの領域crop
- 固定位置の差分によるマーク検出
- 複数画像ではsharpnessで基準選択し、暗部出現頻度、中央値、高輝度percentile、共通罫線を組み合わせる
- 単一画像では色・形状ヒューリスティックを使い、`inpaint`は確認用previewだけに限定
- ONNX segmenterはインターフェース、明示モデルパス、session再利用、タイル合成の境界を用意。モデルは自動取得しない

手書きで隠れた印刷文字を推測して復元しません。

## OCR Provider

既定は`OCR_PROVIDER=mock`で、APIキーなしに全画面と確定フローを確認できます。`disabled`は領域cropを作り、全項目がblankの候補を返します。

外部OCRを使う場合だけ`backend/.env`へ次を設定します。モデル名はコードに固定していません。

```dotenv
OCR_PROVIDER=openai
OPENAI_API_KEY=...
OPENAI_PRIMARY_MODEL=...
OPENAI_FALLBACK_MODEL=
OPENAI_IMAGE_DETAIL=high
```

OpenAI Responses APIへ送るのはBase64化した意味領域cropです。APIキー、画像Base64、生応答、牛名、所有者名などをログへ出しません。request ID、token数、処理時間、エラー種別だけを記録します。通常テストは外部APIを呼ばず、外部統合テストは`RUN_OPENAI_INTEGRATION_TESTS=true`の明示時だけ有効です。

## ローカルCLI

CLIはAPIと同じOpenCV/OCRクラスを再利用します。以下は`backend`から実行します。

```powershell
.\.venv\Scripts\python.exe -m app.cli.main recover-reference --source blank.jpg --template-config resources/templates/cow-breeding-ledger-v1/template.yaml --output .\tmp\reference-output
.\.venv\Scripts\python.exe -m app.cli.main recover-consensus --inputs filled1.jpg filled2.jpg filled3.jpg --template-config resources/templates/cow-breeding-ledger-v1/template.yaml --output .\tmp\consensus-output
.\.venv\Scripts\python.exe -m app.cli.main recover-single --source filled.jpg --template-config resources/templates/cow-breeding-ledger-v1/template.yaml --output .\tmp\single-output
.\.venv\Scripts\python.exe -m app.cli.main extract-document --source filled.jpg --template clean_template.png --template-config resources/templates/cow-breeding-ledger-v1/template.yaml --ocr-provider mock --output .\tmp\ocr-output
.\.venv\Scripts\python.exe -m app.cli.main evaluate --dataset .\evaluation\annotations --report .\evaluation\reports\latest.json
```

設定変更前後を比較するには以前のreportを`--baseline`へ渡します。実画像manifestは開発用、調整用、最終評価用に分離します。

## 評価と現在の実測

Windows 11、Python 3.12.13、OpenCV 4.12.0、620×877の固定合成画像1件で`evaluation/reports/latest.json`を生成しました。

| 指標 | 結果 |
|---|---:|
| registration edge overlap | 0.9341 |
| ECC score | 0.9300 |
| inlier / matches | 359 / 646 |
| median reprojection error | 0.609 px |
| 手書きmask precision | 0.9073 |
| 手書きmask recall | 0.4972 |
| 手書きmask F1 | 0.6424 |
| 印刷を含む全画素に対するfalse positive | 0.000362 |
| registration | 1.150 s |
| mask isolation | 0.366 s |
| 合計 | 1.622 s |
| Python追跡peak memory | 42.1 MiB |

これは退行検知用の合成評価で、実帳票の認識精度ではありません。OpenCVのnative allocationはPython追跡memoryに全て含まれません。実帳票・正解マスク・正解JSONが提供されていないため、実画像OCR精度、ユーザー修正件数、確認時間、外部OCR方式比較は未評価です。重要項目の自動確定は行いません。

## テストと品質チェック

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check app tests
.\.venv\Scripts\python.exe -m mypy app

cd ..\frontend
npm.cmd audit --audit-level=moderate
npm.cmd test
npm.cmd run build
```

テストにはunit、API integration、Alembic、合成OpenCV、Golden Schema、revision競合、二重確定防止、JSON/CSV出力が含まれます。

## 保存先と完全初期化

`backend/data/`にはアップロード、attempt別生成物、テンプレート版、SQLite DB、確定export、構造化ログが保存されます。元のアップロード名は保存名に使いません。`data/`と`.env`はGit対象外です。

全データを削除する前にサーバーを停止してください。確認文字列が一致しない場合は何も削除しません。スクリプトは解決済みの`backend/data/`外を対象にしません。

```powershell
.\scripts\reset-local-data.ps1 -Confirm RESET-LOCAL-DATA
cd backend
.\.venv\Scripts\python.exe -m alembic upgrade head
```

Bashでは`./scripts/reset-local-data.sh RESET-LOCAL-DATA`です。単一ジョブはAPIの`DELETE /api/v1/jobs/{jobId}`で削除できます。確定済みだけ`confirm=true`が必要です。

## トラブルシューティング

- `バックエンド未起動`: `backend/data/logs/dev-backend.err.log`を確認し、migrationを適用します。
- `npm.ps1 cannot be loaded`: PowerShellでは`npm.cmd`を使用します。
- port 8000/5173が使用中: `start.bat` は空いている次のポートへ自動切替します。手動起動の場合は既存プロセスを停止します。LAN向けhostへ変更しないでください。
- `DOCUMENT_BOUNDARY_NOT_FOUND`: 帳票四辺が写り、背景とのコントラストが出るよう撮り直します。
- `LOW_REGISTRATION_CONFIDENCE`: 正しいACTIVEテンプレートを選び、ぼけ・切れ・強い傾きを避けます。
- `MODEL_NOT_CONFIGURED`: `openai`選択時だけAPIキーとモデルを設定します。通常は`mock`へ戻せます。
- `REVISION_CONFLICT`: 別編集が先に保存済みです。画面を再読込して最新revisionから修正します。
- `INTERRUPTED`: バックエンド再起動で中断されています。ジョブの再試行を実行します。
- migrationを最初から確認: 別の空`DATA_DIR`を使うか、確認付きリセット後に`alembic upgrade head`を実行します。

## 既知の制限

- 一枚の画像だけでは黒い印刷文字と黒い手書きを完全分離できない
- 手書きで隠れた印刷文字の完全復元はできない
- 強い影、反射、ぼけ、切れ、折れは結果に影響する
- 同じ場所に複数帳票で手書きが存在すると、MULTI_SCAN_CONSENSUSでも残ることがある
- SINGLE_IMAGE_EXPERIMENTALは人間の確認を必要とする
- OpenCV単体で日本語手書き文字認識を完結させない
- OpenAI等の外部OCRを有効にした場合、OCR対象の領域画像が外部へ送信される
- FastAPIプロセス内キューはバックエンド停止中に処理を継続できない
- SQLiteは単一ローカルプロトタイプ向けであり、多人数同時利用を目的としない
- 実帳票30枚以上の匿名化評価セットとGolden OCR正解はまだない
- ONNXモデル本体は同梱せず、ライセンス確認済みモデルの明示設定が必要

## 本番化する場合の境界

本番化では認証・権限、暗号化、秘密管理、個人情報の保存期間、外部OCR同意、永続ジョブキュー、監視、バックアップ、多人数向けDB、実画像30枚以上の固定評価を別設計として追加してください。このプロトタイプにはAWS、ECS、Lambda、S3、SQS、RDS、Cognito、Terraformを含めていません。
