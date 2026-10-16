# Webベータ フェーズ2：コンテナーとLinux描画

2026-10-15。対象は `C:\Users\hanam\Desktop\llm-ocr-deploy`。新規ゴールは作成していない。[フェーズ1仕様](web-beta-phase1-2026-10-15.md)に基づき、コンテナー実装とローカルの実API・Chrome検証を実施した。AWS配備・Cognito・実マイクは未実施。利用者分離と公開は後続工程である。

## 原因と実装境界

WindowsのJob Object、WSL bridge、Windows字体、FODSの単位を前提にした実装を、そのままLinuxへ配置できなかった。API/画面をコンテナー化し、描画transportと実行条件を `backend/app/rendering` へ分離した。LinuxではAPIからDockerへ直接接続せず、ホストの固定protocol supervisorを利用する。

| 変更箇所 | 変更内容 |
| --- | --- |
| `deploy/`、`.dockerignore` | digest固定のbase image、API・画面・rendererのbuild、loopback限定Compose、専用EBS相当data mount、単一API、systemdの実行・メモリー枠、実行手順 |
| `backend/app/rendering/` | サイズ/SHA付きUnix socket、固定mode、使い捨てrenderer、実効Docker条件の送信前照合、core guard、network/file/resource分離、lease、OOM/切断回収、異常終了後の所有回収 |
| `core/config.py`、print系service/API | `PRINT_PDF_RENDERER=linux`。Linuxで新規準備した寸法・PDF・全ページPNG・所有artifactだけを接続。Windows経路の実行条件をLinux証拠へ読み替えない |
| `spreadsheet_template_import_service.py`、`spreadsheet_preview_geometry.py` | LinuxのExcel→PDF/FODS/rasterを隔離へ移動。cm固定の旧寸法parserへLinuxのinch出力を流さず、別のpreview identityを保存 |
| `prepared_reference_evidence.py`、`candidate_readiness_service.py` | Linux専用のartifact graphとv4候補policy。原本/版/plan/mapping/画像SHA、実行証拠、実装fingerprintを検証し、通常の補正・確定・出力経路へ接続 |
| frontend API、print helper、TemplatePage | 印刷準備のbackend選択をサーバーへ移動。Linuxの準備済み状態を識別。WSL通常GUIでは従来と同じCalcモデルをサーバーが選ぶ |
| `backend/requirements.lock` | pyprojectが要求するjsonschemaを含む依存5件の欠落を補った。既存venvのversionを固定し、fresh imageの起動失敗を解消 |

provider、model、prompt、AI回答schema、association、補正・確定・XLSX出力adapterの製品コードは変更していない。旧 `llm-ocr`、通常DB・設定・原本、既存未コミット差分、V5の停止と封印を保持する。新しいLinuxの描画・候補契約は意図的な追加であり、旧証拠の資格を再解釈したものではない。

## 新しい描画・候補契約

新規Linux previewは `spreadsheet-preview-linux-v1`、印刷寸法は `linux-fods-physical-length-v1`、rendererは `linux-print-reference-v1`、実行は `linux-container-renderer-v1`、準備graphは `linux-prepared-artifact-graph-v1`、候補は `paged-candidate-readiness-v4-linux`。既存の数値投影アルゴリズムを再利用するが、保存する計測identityを分ける。

v4はv3と同じ人間レビューの責務を持ち、`qualified=false`、`geometryQualified=false`、`reviewRequired=true`のままとする。画像・所有原本・版・mapping・実装の変更で失効する。保存PASS boolean、欠落した旧report、現在の環境から成功証拠を補完しない。正式qualificationの実装SHAに依存する旧証拠は、変更時に既存契約どおり失効し得る。

フォントの完全一致だけではLinuxでCalibri帳票を準備できないことを実測した。このため、新規Linux準備だけに `linux-declared-font-substitution-v1` を設け、[実行手順](../deploy/README.md)の5組に限って代替を宣言する。実解決family・SHA・判断を保存し、寸法計測とPDF描画で同じimage/worker/package/font集合を要求する。未知の代替とWindows字体の無断同梱は行わない。旧layout・過去job・元XLSXは変更しない。

Linuxがpipe型core handlerを持つ場合、単純なCORE=0ではdumpを抑止できない。既存WSLの境界と同様にCORE=1とseccompによるsetter拒否を使用する。Linux kernelの[該当処理](https://github.com/torvalds/linux/blob/v6.6/fs/coredump.c)を照合し、各workerで限界値と変更拒否を検証した。OS全体のcore設定を変更していない。

## 実行環境と基準入力

証拠は `D:\llm-ocr-archive\beta\20261015-phase2`。通常dataをmountせず、WSL Ubuntu 22.04の専用 `/var/lib/llmocr-beta-phase2/data` へ新規schemaを導入した。Docker Engine 29.9.1、Compose 5.6.0を公式repositoryから導入。5package新規導入、既存packageの更新・削除はしていない。WindowsのDocker Desktop実体は見つからず、WSL内の実行基盤を使用した。

API/画面はCompose、描画supervisorは専用 `llmocr-beta-phase2.service`、HTTP入口は `127.0.0.1:5298`。実行中のimage・全backend Python sourceとcheckoutの内容一致を照合した。完成imageのID、worker SHA、package集合、実効条件を保存している。

固定入力はフェーズ1の `docs/reference-renderer-evidence/saved-two-pages.xlsx`。ファイル名を2ページ成功の根拠にはしない。原本のmanual breaksによる直接描画は3ページだった。実受入ではAPIで明示した `B2:D16`、`B17:D30` の2ページへ印刷用コピーを生成し、元原本のSHAを保持した。

最終caseの対応は次のとおり。

| ページ | 元セル | mapping / 保存先 | 最終保存値 |
| --- | --- | --- | --- |
| 1 | B2 | `layout_xlsx_B2` / `fields.number` | `00789` |
| 2 | B20 | `layout_xlsx_B20` / `fields.value` | `60000` |

87物理セルのID・canonical rectangleを保持し、2欄を手動で意味付けした。入力のupload順を逆にしてもpage IDで対応した。認識部分だけ既存mock providerの空欄制御応答を使用し、実HTTP/API、SQLite、画像処理、所有artifact、レビュー、補正、確定、出力を実行した。文字認識品質や実OpenAIの受入成功を示す試験ではない。

## 検証結果

| 検証 | 結果・範囲 |
| --- | --- |
| Compose build・新規起動 | backend/frontend/rendererを実build。新規専用DBのmigration、health、静的画面、voice assetsのproduction buildを確認 |
| 2ページ準備と有効化 | 実APIで取込→手動意味付け→ページ保存→PDF/2 PNG生成→v4準備→ACTIVE→catalog受付可能→再取得を通過 |
| 補正・出力 | 2ページともレビューへ到達、値の補正→明示全ページ確認→確定→XLSX取得。B2/B20の文字列値と先頭ゼロを確認 |
| 隔離 | 実コンテナーでloopback/metadata宛接続拒否、API data/Docker socket/AWS資格情報への非到達、入力read-only、CapEff=0、NoNewPrivs=1、memory.max=2GiB、pids.max=64を確認 |
| 故障・回収 | 任意command付きheader拒否、lease期限切れ、切断、不正XLSX、注入OOM、supervisor SIGKILLと再起動、回復後の再描画の8条件を通過 |
| OOMの条件 | 正常な2GiB admission後、運用側だけが32MiBへ一時縮小して発火。Docker OOM event、子/volume回収、API継続を確認。2GiB超を実割当てする試験ではない |
| frontend | 対象2ファイルの既存15条件と `npm run build` を通過 |
| backend静的検査 | 関係する新規/変更ファイルのruffとLinux向けmypy 17ファイルを通過。mypyは依存先をsilent、既存openpyxl stub欠落を除外。全backend検査ではない |
| Chrome | 新規独立contextで接続、通常画面、確定結果導線を確認。初回ホームのerror/warnは0件。paged結果導線では404を確認し、下記の既知課題へ記録 |

最終APIの2ページ印刷準備は単発4.063秒。AI推論や音声応答の時間ではなく、5秒達成の証拠に流用しない。WSLの総RAMは約1.86GiBであり、4GiB EC2の実測と称さない。アプリは4GiB上限/swap禁止のsliceへ収容して実行した。実EC2でのOS/daemon込み余裕、CPU Standardでの継続負荷はフェーズ7の確認対象に残す。

既存の大きいXLSX取込serviceを単独で全検査するとruff27件・mypy18件が残る。追加部分を除いた開始時ソースを証拠内だけに再構成し、開始時SHAとの完全一致を確認して比較した。検査message集合・件数は同一で、新しい検査エラーは0件。既存エラーを削除・抑止して全体成功と報告していない。比較は `static-baseline-comparison.json`。

`final-case-2/correspondence-proof.json`で87物理ID/canonical矩形、page ID・mapping・targetPath・sourceRegionId・boundingBox・crop URLの保持を照合した。XLSXのZIP part集合は同じで、変更partは値を書いた `xl/worksheets/sheet1.xml` だけ。API再起動後も確定状態と出力bytes/SHAが一致した。最後に既定の認識無効へ戻し、healthを確認した。

初回の認識無効設定ではfieldsがないため、paged review初期化が既存 `PAGE_RESULT_CHANGED` で停止した。同じ入力をmock providerへ切り替えるとレビュー・出力へ進んだ。照合条件を緩めず、認識無効の実行を成功として扱わない。APIの既定はdisabledへ戻し、実送信を誤起動しない。

## 既知課題と後続工程

Chromeで `?job=<paged job ID>` を開く通常導線は単一結果画面へ進み、「指定された読み取り結果が見つかりません。」を表示した。APIでは同じjobの全ページレビュー・確定・出力が成立している。App/Homeのpaged結果導線をフェーズ4で接続する。今回のコンテナー化で導入した修正として隠さず、公開前の必須UI修正とする。

日本語Windows fontの未知の代替、全帳票の実印刷・字形/crop精度、実マイク、Cognito、利用者間分離、API予算・backup復元、AWS環境は未検証。精度実験やV6へは戻らない。フェーズ2の実装・ローカル検証を終了し、次は **フェーズ3：GPT-6.1 Sol / Highによる認証・所有権**。公開可能なベータの完成ではない。

## 証拠と保持

`runtime-checks.json`、`oom-events.jsonl`、`final-case-2/`、各build/static log、image/source照合、Chrome snapshotを参照する。harnessの失敗・中断を保存し、再実行は別名で記録した。初期試験のimport response要約は後の成功結果で上書きされたため、失敗の再現内容はこの記録と実行履歴を根拠とし、最終caseを別directoryで保持した。

通常DB・旧checkout・設定・原本・前証拠の終了照合は `preservation.json`、本工程の封印は `completion-1/hashes.json`。`git diff --check`と変更範囲を確認する。OCRの値・mapping・crop・revision・確定・出力の既存契約を維持し、Linux環境の新しい生成・候補契約を追加した。

実AI API使用0回、通常の予約0回、開発台帳 **440/924・残484回**。ベータ追加1,000回は未設定・未使用。AWS API操作・resource変更0、Git staging/commit/pushなし。重い全件回帰と独立工程審査はフェーズ6へ残す。
