# 帳票 OCR V2 ローカルワークベンチ

## V2で追加した帳票レイアウト定義

V2の帳票処理順は固定されています。

1. 最初に帳票外周を検出し、OpenCVの透視変換で歪みを補正します。画像全体を安易に外周とする従来のフォールバックは、紙面領域の再検出を先に行うよう改善しています。
2. 歪み補正済みの元画像からOpenCVで罫線セルを検出します。内部に2個以上の矩形を含む大枠は記入欄候補から自動除外します。
3. OpenCVのセル候補をCanvas上で「ラベル」「手書き文字欄」「選択マーク欄」「除外」へ分類します。PaddleOCRを任意導入している場合はラベル文字候補も補助します。ラベル文字の修正とJSON保存先の意味づけもこの時点で確定します。矩形は範囲選択に加えてCtrl+クリックで複数選択でき、分類だけの途中状態もレコード紐づけ前に保存できます。
4. 分類と意味づけを保存した後に限り、指定された手書き文字欄の内側だけから手書きマスクを生成し、テンプレート画像から除去します。
   固定ラベルと記入欄が同じ矩形にある場合は、その矩形へ保存先を直接割り当て、矩形内の印刷ラベル名を設定できます。OCR時は固定印刷を除いた手書き文字画像へ自動的に切り替えます。独立したラベルがない欄も保存先を直接割り当てられます。
5. OCR実行時は、ジョブ開始時に意味づけ設定をスナップショットし、意味づけ済みの手書き文字欄を保存座標どおりに切り出してOpenAIへ送信します。ラベルセルや帳票全体は送信せず、PaddleOCRの文字有無判定で送信対象を増減させません。
6. 記入欄からラベルを巻き込むようにドラッグすると、方向に応じて横積み・縦積みと行順を保存します。「縦横ラベルを重複定義」をオンにすると十字方向の二重定義ができます。
7. 確定したセル、ラベル、関係はテンプレート版の `layout_json` としてSQLiteへ保存し、同時にOCR用の固定座標マッピングへ変換します。
8. 「選択マーク欄」では、双・早・流・死・♂・♀のような二次元配置を補正済み画像から自動検出して選択肢の枠を作成できます。複数の選択マーク欄を選んだ状態では、同じ自動検出操作を一括適用します。
9. 読み取り画面はPNG等の画像入力に加え、ガイド枠・静止判定・自動シャッター付きカメラ入力を備えます。

売却子牛は帳票の物理行数に合わせて10行、繁殖履歴は20行を上限とします。価格や体重の意味づけを縦方向へ一括設定するときは、ドラッグ終点側の同一列だけを採用し、隣接列を同時に取り込みません。

配列項目は、セル意味づけに加えて `親セクション → 行レコード → 項目` の構造をテンプレートへ保存します。「レコード作成」で同じ産次の行を端から端まで線でつなぎ、「親ラベル接続」で「売却子牛一覧」などの親見出しへ結びます。OCR応答はmappingIdからこの構造へ確定的に戻され、確定時には種雄牛名・産次・価格を同じ行として検索索引へ保存します。

テンプレートで入力した産次などの `recordKeyValue` はレコードの識別情報として扱います。OpenAIが産次セルを誤読しても、記入のある行ではテンプレートのレコードキーを優先し、空行には値を補いません。補正後も元の `mappingId` とcrop座標は維持されるため、結果確認画面から正しい記入矩形を開けます。

PaddleOCRは必須ではなく、通常のセットアップでは導入しません。OpenCVの文字密度によるラベル候補の編集と、保存済みセルを使ったOpenAI読み取りだけで運用できます。ラベル文字候補の補助が必要な場合だけ、セットアップ前に `LLM_OCR_INSTALL_PADDLE=1` を設定してください。PaddleOCRの文字存在確認は監査用の補助情報で、OpenAI送信の可否判定には使いません。

牛繁殖台帳の画像から、帳票位置合わせ、テンプレート復元、手書き分離、領域切り出し、OCR、Googleスプレッドシートへの直接出力と通常修正、詳細修正画面での矩形・ラベル修正、確定JSON保存、個体識別番号による検索までを行うローカル専用ワークベンチです。

- Backend: FastAPI / Python 3.12 / OpenCV / Google Auth / SQLite / SQLAlchemy / Alembic
- Frontend: React / TypeScript / Vite / dnd-kit
- Storage: `backend/data/` のローカルファイルと、設定したGoogleスプレッドシート
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

`setup.ps1`は固定済みの`backend/requirements.lock`と`frontend/package-lock.json`を使用し、`.env`作成とmigration適用も行います。初回作成される`.env`は安全な`mock`設定です。外部OCRを使う場合は、起動前に`OCR_PROVIDER`とAPIキーを確認してください。準備済み環境で`.env`が失われた場合、`start.bat`は勝手に`mock`へ切り替えず、設定の復元を案内して停止します。

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
   - `SINGLE_IMAGE_EXPERIMENTAL`: 記入済み帳票1枚から作成
   - `REFERENCE_TEMPLATE`: 白紙帳票1枚から作成
2. DRAFTの比較画像、品質、マスクを確認し、必要ならCanvasでマスクを編集します。
3. レイアウト編集でセルを「ラベル」「手書き文字欄」「選択マーク欄」「除外」に分類し、各入力欄をJSON項目へ意味づけします。一覧にない項目は追加項目として入力できます。複数矩形の範囲選択、Ctrl+クリックによる追加・解除、分類途中での保存に対応しています。
   - 「選択マーク欄」は、双・早・流・死・♂・♀などの選択肢を補正済み画像から自動検出できます。必要ならGUI上で選択肢の枠を調整します。
4. 配列データは、同じ行の手書き文字欄を「レコード作成」で線接続し、「親ラベル接続」で「売却子牛一覧」などの親セクションへ結びます。産次などのレコードキーもここで確認します。
5. レイアウトを検証してからテンプレートを承認し、ACTIVEにします。テンプレート名は重複できません。
6. 「読み取り」でACTIVE版と台帳画像を選んでOCRを開始します。個体識別番号の事前入力は不要です。OCRを切って画像処理だけ行うこともできます。
7. OCR完了時点で、結果はGoogleスプレッドシートの「売却子牛一覧」「繁殖履歴」「帳票一覧」へ直接出力されます。帳票一覧は3番目に配置されます。帳票は1行、配列データは1明細1行で、OCRジョブIDと明細番号をキーに再保存時も同じ行を更新します。
8. 文字、数字、日付、空欄はスプレッドシートのセルを直接修正します。Apps Scriptが非表示の編集キューへ変更を記録し、ローカルアプリが既定5秒間隔でDBへ取り込みます。矩形、認識領域、ラベル対応を変更する場合だけ「修正」リンクからReactの詳細修正画面を開きます。
9. 意味づけ済みセルだけが表形式で表示されます。緑は問題なし、黄色は要確認です。
10. 結果画面はペンアイコンの「結果編集モード」と、マウスカーソルアイコンの「移動モード」を切り替えます。編集モードではセルを選択して対応cropを表示し、ダブルクリックで直接編集できます。選択マーク欄は全画面の選択肢ポップアップから修正します。
11. 移動モードではdnd-kitによるセルのドラッグ&ドロップで、途中からずれた列データを並べ替えられます。列見出しの上下矢印による列全体の1行移動も、このモードでのみ使用できます。データの並びを変えても、各セルのプレビューは元の矩形座標を維持します。
12. 左上の「プレビュー幅」スライダーでは、画像と表の表示比率を30〜55%で変更できます。詳細編集欄では `raw`、`normalized`、`status`、候補、確認理由を確認・修正し、「修正を保存」でrevision付きの修正版を保存します。同時にGoogleスプレッドシートの同じ行も更新されます。
   - 生年月日は数字3組を確認して平成・令和を選ぶと、`2014年9月27日` のような西暦表記へ変換されます。
   - 売却日と繁殖履歴の分娩日は元号年の数字から西暦表記へ変換できます。空白・カンマ・ピリオド・ハイフンが混在していても数字3組として認識し、`30 8-10` は `2018年8月10日`、`5.11.16` は `2023年11月16日` と表示します。先頭の年が2桁なら平成、1桁なら令和として自動判定し、必要なら元号を手動変更できます。
   - 売却子牛一覧の備考は、先頭を `♂` または `♀` の固定候補から選び、後続文字を保持します。
   - 種雄牛名または繁殖履歴の備考で `.` や `ヽ` が読み取られた場合は、直前行と同じ値として補完します。
13. 要確認項目を解消して確認ダイアログから確定します。確定した時点で、台帳専用のスプレッドシートが作成または更新されます。確定後に修正が必要になった場合は、書類ライブラリまたは個体検索から再編集できます。
14. 確定結果は確認済みの個体識別番号に紐づくJSONとして保存されます。
15. 「個体検索」で番号を入力すると、コードではなく項目表として確定JSONを確認できます。「スプレッドシートへ出力」は同じ台帳の行を更新し、「スプレッドシートを開く」は書込みを行わず、その台帳専用ファイルを表示します。検出結果の再編集では確定レコードを残したままレビュー状態へ戻し、再確定時に同じ台帳、検索インデックス、スプレッドシート行を更新します。種雄牛名から検索した売却子牛レコードにも同じ操作があります。

ExcelコピーおよびXLSX保存は画面上の運用対象から外し、Googleスプレッドシートへの直接同期へ一本化しています。Google側が一時的に利用できない場合もローカルのOCR結果と修正内容は保存され、結果画面の「再同期」から再送できます。

## アーキテクチャ

ReactはAPIキーを持たず、FastAPIだけが画像検証と外部OCR設定を担当します。CPU負荷の高いOpenCV処理はジョブワーカーからスレッドへ渡され、HTTP応答をブロックしません。状態・revision・監査イベントはSQLite、画像とJSONはDB登録済み相対パスで`backend/data/`へ保存します。PC間でSQLiteだけを移動してもプレビューを復旧できるよう、OCR元画像と受付時点のテンプレート画像はSQLiteにも復旧用BLOBとして保存します。派生cropはファイル欠損時にこれらから再生成します。

Google Sheets APIは利用者自身のGoogle OAuth `drive.file` 権限で、アプリが作成したファイルまたはGoogle Pickerで利用者が選択したファイルだけを対象に実行します。広範な `spreadsheets` 権限は要求しません。アプリのログイン状態は署名付きJWTをHttpOnly Cookieへ保存し、Googleのrefresh tokenはJWTやフロントエンドへ渡さず、`AUTH_JWT_SECRET`から導出した鍵で暗号化してSQLiteへ保存します。Pickerを開く直前だけ、短時間有効なaccess tokenを認証済み画面へ返します。ログアウトはアプリのセッションだけを終了し、連携解除APIは保存トークンの削除とGoogleへの失効要求も行います。

### Google OAuthとSheets APIの準備

`terraform/google`はGoogle Cloud上でSheets API、Drive API、Picker APIを有効化します。OCR処理、DB、ジョブは引き続きローカルで動き、AWS Lambdaは作成しません。gcloud CLIも不要です。Terraform用のGoogleサービスアカウント鍵がある場合は次のように実行します。

```powershell
cd terraform\google
$env:GOOGLE_APPLICATION_CREDENTIALS = "C:\path\to\terraform-admin.json"
Copy-Item terraform.tfvars.example terraform.tfvars
# terraform.tfvars の project_id を編集
terraform init
terraform plan
terraform apply
```

Googleの通常のOAuthクライアントは公開APIから作成できないため、ここだけはGoogle Cloud Consoleの「Google Auth Platform」で一度設定します。

1. BrandingとAudienceを設定し、Testingの場合は利用者のGoogleアカウントをTest usersへ追加します。
2. Clientsで「Web application」を作成します。
3. Authorized redirect URIへ `http://127.0.0.1:8000/api/v1/auth/google/callback` を完全一致で登録します。
4. 発行されたclient IDとclient secretを`backend/.env`へ設定します。
5. Google Picker API用のAPIキーを作成し、Application restrictionsをHTTP referrer、API restrictionsをGoogle Picker APIに限定します。ローカルでは `http://127.0.0.1:5173/*` を許可します。
6. Google Cloudのプロジェクト番号をPicker App IDとして設定します。プロジェクトIDではありません。

```dotenv
GOOGLE_OAUTH_ENABLED=true
GOOGLE_OAUTH_CLIENT_ID=xxxx.apps.googleusercontent.com
GOOGLE_OAUTH_CLIENT_SECRET=...
GOOGLE_OAUTH_REDIRECT_URI=http://127.0.0.1:8000/api/v1/auth/google/callback
GOOGLE_OAUTH_FRONTEND_URL=http://127.0.0.1:5173/
AUTH_JWT_SECRET=32文字以上の十分にランダムな値
AUTH_SESSION_TTL_SECONDS=604800
AUTH_STATE_TTL_SECONDS=600
AUTH_SECURE_COOKIE=false

GOOGLE_SHEETS_ENABLED=true
GOOGLE_SHEETS_SPREADSHEET_TITLE=繁殖台帳 OCR
GOOGLE_SHEETS_FRONTEND_BASE_URL=http://127.0.0.1:5173/
GOOGLE_SHEETS_EDIT_POLL_INTERVAL_SECONDS=5
```

Pickerの機器固有値はGit管理対象外の`backend/.env.local`へ設定します。

```dotenv
GOOGLE_PICKER_API_KEY=ブラウザー制限済みAPIキー
GOOGLE_PICKER_APP_ID=Google Cloudのプロジェクト番号
```

`GOOGLE_SHEETS_SPREADSHEET_ID`はGoogle OAuthを無効にして既存の固定シートを使う場合だけ設定する互換用項目です。OAuth利用時は未設定にしてください。

JWT秘密鍵はPowerShellで例えば次のように生成できます。

```powershell
[Convert]::ToBase64String([Security.Cryptography.RandomNumberGenerator]::GetBytes(48))
```

バックエンドを再起動して画面の「Googleでログイン」から認証します。以前の連携に広範な `spreadsheets` 権限が含まれる場合、画面に連携更新が表示されます。更新操作は旧refresh tokenを失効・削除してから、`drive.file` 権限だけで再認証します。左メニューの「保存先設定」ではGoogle Pickerを開き、既存フォルダーを選択します。フォルダーの作成、移動、名前変更はGoogle Drive側で行います。

新しい台帳を確定すると、選択したフォルダへ台帳専用のスプレッドシートを1件作成します。同じ台帳へのページ追加とページの読み直しは同じファイルを更新し、「別の台帳」として開始した読取結果は別ファイルへ保存します。自動作成したファイルには3つの業務タブ、非表示の編集キュー、帳票レイアウト、見出しを作り、Google側の空の初期シートは削除します。サービスアカウントの共有設定や既存シートへのメールアドレス追加は不要です。`AUTH_JWT_SECRET`を変更すると保存済みrefresh tokenを復号できなくなるため、その場合もGoogle連携をやり直してください。

### Google Sheets直接修正の初回設定

1. OCRアプリが作成した台帳専用スプレッドシートを開きます。
2. `拡張機能` → `Apps Script` を開きます。
3. `apps-script/Code.gs` の内容をApps Scriptの `Code.gs` へ貼り付けて保存します。
4. 関数一覧から `setupOcrSheetEditing` を1回実行し、権限を許可します。
5. OCR結果を再同期し、通常データセルを編集して数秒後に値が維持されることを確認します。

この設定は台帳専用スプレッドシートごとに必要です。アプリがApps Scriptプロジェクトを自動作成するには、Drive/Sheetsとは別の強いスクリプト管理権限とGoogle Cloud側の追加設定が必要になるため、現実装では自動付与しません。

Apps Scriptは、業務タブの非表示列にある `jobId`、`recordId`、`revision` と列見出しから対象フィールドを決定します。編集イベントは `editId` で重複排除され、同じフィールドが別の画面で変更済みの場合は競合としてキューへエラーを返します。別フィールドの連続編集は、基準revisionの値が変わっていない場合だけ安全に再適用します。

ジョブ状態は `CREATED → QUEUED → PROCESSING → REVIEW_REQUIRED ⇄ CONFIRMED` です。個体検索からの再編集に加え、確定済み結果へ検証エラーを生じさせるSheet編集も`CONFIRMED`を`REVIEW_REQUIRED`へ戻します。確定条件を満たす通常修正は確定状態を維持します。失敗、取消、中断を別状態にし、起動時に残った`PROCESSING`は`INTERRUPTED`へ回収します。修正は`baseRevision`による楽観ロックで、元OCR候補、現修正版、revision履歴、確定結果を分離します。

## FastAPIエンドポイント

| Method | Path | 用途 |
|---|---|---|
| GET | `/api/v1/health` | DB、worker、OCR providerの安全な状態 |
| GET | `/api/v1/auth/session` | 認証設定・ログイン状態・利用者情報 |
| GET | `/api/v1/auth/google/start` | Google OAuth認可開始 |
| GET | `/api/v1/auth/google/callback` | OAuth code交換・暗号化保存・JWT Cookie発行 |
| POST | `/api/v1/auth/logout` | JWTセッションを終了 |
| POST | `/api/v1/auth/google/disconnect` | Google tokenを失効し、保存済み連携を削除 |
| GET | `/api/v1/templates` | テンプレート一覧 |
| GET | `/api/v1/templates/{templateId}` | versionと品質を含む詳細 |
| POST | `/api/v1/template-jobs` | 3方式のテンプレート準備 |
| POST | `/api/v1/template-versions/{versionId}/approve` | DRAFTをACTIVE化 |
| POST | `/api/v1/template-versions/{versionId}/reject` | DRAFTを却下 |
| PUT | `/api/v1/template-versions/{versionId}/handwriting-mask` | 編集マスク保存 |
| GET/PUT | `/api/v1/template-versions/{versionId}/field-mappings` | 意味づけ済みセルとJSON保存先の取得・保存 |
| GET/PUT | `/api/v1/template-versions/{versionId}/layout` | セル分類・関係・レコード構造の取得・保存 |
| POST | `/api/v1/template-versions/{versionId}/layout/detect` | 実データからセルを再検出 |
| POST | `/api/v1/template-versions/{versionId}/layout/verify` | レイアウトとレコード構造を検証 |
| POST | `/api/v1/template-versions/{versionId}/reprocess` | 新attemptで再生成 |
| POST | `/api/v1/ocr-jobs` | 台帳画像処理・OCR開始 |
| GET | `/api/v1/ocr-jobs/{jobId}/result` | REVIEW_REQUIRED/CONFIRMED結果 |
| PUT | `/api/v1/ocr-jobs/{jobId}/corrections` | revision付き修正保存 |
| POST | `/api/v1/ocr-jobs/{jobId}/reopen` | 確定済み結果を再編集状態へ戻す |
| POST | `/api/v1/ocr-jobs/{jobId}/confirm` | 最終検証・確定 |
| POST | `/api/v1/ocr-jobs/{jobId}/spreadsheet-sync` | Googleスプレッドシートへ再同期 |
| GET | `/api/v1/google-sheets/status?jobId={jobId}` | 書込みを行わず対象台帳のスプレッドシートURLを取得 |
| GET | `/api/v1/document-library` | 個体、台帳、ページ、読み取り履歴をまとめて取得 |
| GET | `/api/v1/google-drive/output-folder` | 現在の保存先とPicker設定状態を取得 |
| POST | `/api/v1/google-drive/picker-session` | Picker用の短時間access tokenと公開設定を取得 |
| PUT | `/api/v1/google-drive/output-folder` | Pickerで選択したフォルダーを検証して保存先に設定 |
| GET | `/api/v1/ocr-jobs/{jobId}/excel-table` | Excelコピー用の共通表データ（確定済みのみ） |
| GET | `/api/v1/ocr-jobs/{jobId}/export?format={json,csv,xlsx}` | 確定結果出力 |
| GET | `/api/v1/individual-records?individualId={10桁番号}` | 個体識別番号に紐づく確定JSON一覧 |
| GET | `/api/v1/sale-calf-records?sireName={種雄牛名}` | 種雄牛名に一致する売却子牛レコード検索 |
| GET | `/api/v1/jobs/{jobId}` | 進捗と安全なエラー |
| POST | `/api/v1/jobs/{jobId}/retry` | FAILED/INTERRUPTED/REVIEW_REQUIRED再試行 |
| POST | `/api/v1/jobs/{jobId}/cancel` | 取消または取消要求 |
| DELETE | `/api/v1/jobs/{jobId}` | DBと関連ローカルファイル削除 |
| GET | `/api/v1/jobs/{jobId}/artifacts` | 登録済み生成物一覧 |
| GET | `/api/v1/jobs/{jobId}/artifacts/{artifactName}` | 登録済み生成物配信 |
| POST | `/api/v1/jobs/{jobId}/artifacts/recover` | 旧データの元画像をSHA-256照合してプレビュー再生成 |

エラーは共通`ProblemDetails`です。クライアントから任意ファイルパスを受け取る配信APIはありません。

## SQLite migration

`0001_initial_schema.py`が基礎テーブルを作成し、以降のmigrationで検索・レイアウト・レコード索引を追加します。

- `0002_individual_record_index.py`: 確定結果へ個体識別番号の検索用インデックスを追加
- `0003_template_layout_v2.py`: テンプレート版へセル分類・関係・レコード構造を保持する `layout_json` を追加
- `0004_confirmed_record_fields.py`: 確定済み配列レコードを項目単位で検索する `confirmed_record_fields` を追加
- `0008_google_oauth_accounts.py`: 暗号化したGoogle OAuth refresh tokenと利用者情報を追加
- `0009_user_spreadsheet_targets.py`: OAuth利用者ごとの自動作成スプレッドシートIDとURLを追加
- `0010_spreadsheet_edit_events.py`: Sheet編集イベントの重複排除、競合結果、適用revisionを保存
- `0011_portable_job_sources.py`: OCR元画像と受付時テンプレートを復旧用BLOBとして保存
- `0014_ledger_document_pages.py`: 個体識別番号とは別に台帳とページ履歴を管理
- `0015_drive_document_library.py`: Drive保存先と台帳専用スプレッドシートの関連付けを追加

- `templates`, `template_versions`
- `processing_jobs`, `job_inputs`, `job_artifacts`, `job_events`
- `ocr_results`, `ocr_result_revisions`, `confirmed_results`, `confirmed_record_fields`, `spreadsheet_edit_events`

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
OPENAI_INITIAL_IMAGE_DETAIL=high
OPENAI_RETRY_UNCERTAIN_HIGH_DETAIL=false
OPENAI_MAX_CONCURRENCY=2
```

OpenAI Responses APIへ送るのはBase64化した意味づけ済み記入セルのcropと、バッチ内だけで有効な短いIDです。30矩形単位、high画像、同時実行数2で処理します。応答は短い4項目Schemaを使い、元の `mappingId`、候補補正、同上処理、レビュー理由はバックエンドで復元します。混在矩形に「読み取りから除外する印刷ラベル」を設定した場合だけ、その固定文字も矩形内の除外指示として送ります。JSON保存先、テンプレート名、帳票全体、別矩形のラベル文字は送信しません。APIキー、画像Base64、生応答、牛名、所有者名などをログへ出さず、request ID、token数、処理時間、エラー種別だけを記録します。通常テストは外部APIを呼ばず、外部統合テストは`RUN_OPENAI_INTEGRATION_TESTS=true`の明示時だけ有効です。

読み取り結果画面の項目数は固定Schemaの総項目数ではなく、ジョブ開始時にスナップショットした意味づけ済みJSONパスの件数です。意味づけていない基本情報や血統項目は、空欄判定として表示・要確認集計しません。

結果とプレビューの対応は配列番号だけではなく、OCRへ送信したセルの `sourceRegionId`（テンプレート上の `mappingId`）を基準に解決します。レコード位置やJSONパスが変わった場合でも、クリックしたセル自身のcropを優先します。結果画面は画面全体へ追従する可変レイアウトで、デスクトップではプレビュー幅を調整でき、狭い画面では画像と表を縦に並べます。

## ローカルCLI

CLIはAPIと同じOpenCV/OCRクラスを再利用します。以下は`backend`から実行します。

```powershell
.\.venv\Scripts\python.exe -m app.cli.main recover-reference --source blank.jpg --template-config resources/templates/cow-breeding-ledger-v1/template.yaml --output .\tmp\reference-output
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

テストにはunit、API integration、Alembic、合成OpenCV、Golden Schema、revision競合、二重確定防止、JSON/CSV/XLSX出力、Excel向けリッチクリップボード生成が含まれます。

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
- Excelへ貼り付けても罫線が付かない: Chromeでクリップボード利用を許可し、「Excelへコピー」の後にExcel側で通常の貼り付けを使用します。ブラウザがリッチクリップボードを拒否した場合は、値だけのTSVコピーへ安全にフォールバックします。
- migrationを最初から確認: 別の空`DATA_DIR`を使うか、確認付きリセット後に`alembic upgrade head`を実行します。

## 既知の制限

- 一枚の画像だけでは黒い印刷文字と黒い手書きを完全分離できない
- 手書きで隠れた印刷文字の完全復元はできない
- 強い影、反射、ぼけ、切れ、折れは結果に影響する
- 記入済み帳票から作成したテンプレートは人間の確認を必要とする
- OpenCV単体で日本語手書き文字認識を完結させない
- OpenAI等の外部OCRを有効にした場合、OCR対象の領域画像が外部へ送信される
- FastAPIプロセス内キューはバックエンド停止中に処理を継続できない
- SQLiteは単一ローカルプロトタイプ向けであり、多人数同時利用を目的としない
- 実帳票30枚以上の匿名化評価セットとGolden OCR正解はまだない
- ONNXモデル本体は同梱せず、ライセンス確認済みモデルの明示設定が必要

## 本番化する場合の境界

現在のOAuthアカウント保存は単一利用者のローカル運用を前提としています。本番化ではユーザー別データ分離、秘密管理、個人情報の保存期間、外部OCR同意、永続ジョブキュー、監視、バックアップ、多人数向けDB、実画像30枚以上の固定評価を別設計として追加してください。現段階のTerraformはGoogle API有効化だけを対象とし、AWS、ECS、Lambda、S3、SQS、RDS、Cognitoはまだ作成しません。
