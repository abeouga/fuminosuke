# 文之助：共通編集APIと基本操作の実装

2026-10-06 / フェーズ13 / `editing.v1`

フェーズ13の実装と段階検証を完了した。対象は `C:\Users\hanam\Desktop\llm-ocr-deploy`。元の `llm-ocr` への変更操作は行っていない。新APIは通常利用で無効のままであり、frontend・連続音声・自然言語での対象解決はまだ接続していない。

次は**フェーズ14、Astra / xhigh**。見出し・繰り返し表・マーク・識別・書類名を実装する。本フェーズ末尾で停止し、モデル切替と実行指示を待つ。設計正本は [フェーズ12統合設計](voice-authoring-phase12-design-2026-10-06.md)。

## 1. 変更の目的と境界

手動と音声から同じ意味付けを行い、一つの保存群・revision・履歴として扱うため、backendに有限の型付き編集入口を追加した。従来の選択context・proposal・承認句を自動承認する変更ではない。新しいsession/context/turnと、現resourceに束縛したhandleを使う。

現行OCR provider/model/prompt/schema、画像検出・crop処理、過去jobの受付snapshot、確定・有効化・出力の条件は変更していない。明示された補正とDRAFT authoringは既存の保存serviceを通す。既存OCR contractを維持したことの今回の根拠は、保存境界の短い比較と実DB・業務ファイルの保持確認である。OCR全体の安全性を完全に検証したとは扱わず、必須の全体回帰はフェーズ18に残す。

## 2. 実装済みの操作

| 操作 | 内容・制約 |
| --- | --- |
| `ocr.set_value` | 受付mappingと切り出し対応を確認できる単一OCRの文字欄だけ。値は文字列または明示空欄のnull。先頭ゼロを保持 |
| `cell.set_role` | 既存セルの分類。未解決の接続・読取設定等の依存を拒否 |
| `cell.rename` | 既存セルの表示名override。物理セルID・座標・printedLabelTextを保持。仮想見出し名は次フェーズ |
| `cell.set_reading` | `text`と`sequence`。後者は既存`readingMode=number`の自動採番であり、数字の転記設定ではない |
| `meaning.create` | 名称と別名からserverがID・保存先を発行。batch内だけの`localRef`で後続接続が可能 |
| `meaning.update` | 名称・別名を変更。ID・保存先・既存relation IDを保持。独立したセル名overrideは保持 |
| `meaning.delete` | 依存する接続があれば拒否。同じ群で先に接続を解除することは可能 |
| `meaning.bind` | 既存の通常記入欄と意味を接続。項目名セルの線接続、直接割当を扱う。表所属・複合欄は専用操作待ち |
| `meaning.unbind` | 指定した既存relationを解除。繰り返し表の所属変更は公開しない |

形状生成、分割・結合、座標変更、見出し階層、表、マーク設定、識別設定、書類名、対象の言い直しは未実装capabilityとして公開していない。カタログ上の見出し等は閲覧用である。

## 3. API・型・所有関係

新入口は `/api/v1/editing`。`status`、session作成/終了、context取得/更新、対象検索、batch、undo、receipt照会、resource共有履歴を提供する。session tokenは`X-Editing-Session`で渡し、DBにはhashだけ保存する。localhostのHostと許可Originを検査する。

`contracts.py`が要求・応答の型の正本。追加property、数値として送られたOCR文字値、任意layoutやtargetPathの注入を拒否する。HTTP応答も型を検証し、形式不正は`INVALID_ARGUMENT`とfieldErrorsを返す。

- 一群は最大50操作、展開対象最大1000欄、HTTP body最大256KiB。
- handleはowner・context世代・resource revision・対象identityに束縛したHMAC。別session、古い世代、別pageのhandleは実行できない。
- 検索は名称・別名を照合し、50件単位の署名付きcursorで返す。cursorは検索条件と保存世代に束縛する。検索だけでpageScopeを変更できない。
- `current` pageの外へ影響する暗黙の名称・意味変更は拒否する。全ページへの変更にはcontext側の明示`all`が必要。実paged環境の結合検証は次フェーズ以降に残す。
- 未完成DRAFTのcatalogは現layoutを使用し、過去の保持された派生mappingを現在の編集対象として補充しない。
- sessionは非操作30分で失効。trusted controller用turnは60秒で失効し、READYだけ保存可能。公開HTTPからturnをREADYにする入口は設けていない。
- 局所音声は録音開始時の選択と世代に束縛し、選択外変更を拒否する。これはcontroller境界の確認であり、実録音の試験ではない。

同じ型からJSON SchemaとTypeScriptを生成する。Function calling用は`get_editing_context`、`find_targets`、`apply_edit_batch`、`undo_edit`、`get_edit_receipt`の5ツール。strict用objectは追加propertyを拒否し、すべてのpropertyをrequiredにする。OpenAIによる実受理・モデル動作はフェーズ16以降で確認する。

保存済みOCRの一件には74欄と72 mappingがあり、2欄には受付mappingが存在しなかった。この2欄は値・sourceRegionId等を保持した閲覧専用対象とし、編集operationを付与しない。一方、mappingが存在するのにsourceRegionId/targetPathの対応が一意でない場合は`FIELD_BINDING_CHANGED`で拒否する。全結果を無条件に拒否する実装から、この区別へ修正した。

## 4. 保存・再送・取り消し

Templateは既存`TemplateAuthoringService.prepare/persist`、OCRは`OcrResultService.correct_in_transaction`を使う。新しい意味付けのcompileと不変config artifactの準備は書込lockの外で行い、transaction内で状態・世代・revisionを再照合する。authoringが暗黙に保存先を変える場合や、識別・書類名の依存を黙って落とす場合は拒否する。

変更群は一回のrevision増分と一つのResourceOperation。業務保存、履歴、receipt成功、turn消費を同じtransactionで確定する。どれかが失敗したら群全体をrollbackする。準備で生成された未参照artifactは成功した業務データとして扱わない。

`MutationReceiptService`には任意のprepare段階を追加した。既存呼出しはprepare無しで利用できる。同じrequest IDとpayloadの再送は、現在のrevisionやsession期限より先に確定receiptを照会する。内容違いの同じIDは拒否する。同時再送で一方が保存した後に他方の準備が失敗しても、既に確定した成功receiptを返す。失敗もowner付きの確定receiptとして再取得できる。

`NO_CHANGE`はrevision・操作履歴を増やさず、operationIdはnull。新形式の直前操作だけをinverseで取り消す。別sessionからの再読込後も履歴を共有する。古い形式の履歴、取り消しの取り消し、現在値やrevisionが変わった履歴は実行しない。undoも新revisionで保存し、過去の品質確認・資格・確定状態を復活させない。

通知は確定した結果からserverが作る。「受理番号を変更しました」等を返し、通常batchに承認案を返さない。TTS再生と画面への反映は後続フェーズ。

## 5. DBと通常利用への影響

新migration `0005_editing_api_v1.py`を追加し、`editing_sessions`、`editing_turns`、`editing_operations`の3テーブルを作る。既存migrationは変更していない。コピーDBで2回の`upgrade head`を実行し、初回に3テーブル追加、2回目は変更なし、既存業務31テーブルの内容保持を確認した。後続履歴を削除するdowngradeは拒否する。

設計時の「ResourceOperationへmetadata列を追加」は、同じoperation IDをPK/FKにする一対一の`editing_operations`テーブルへ具体化した。既存ORMへ列を追加すると、通常DBの移行前でも旧履歴のSELECTがその列を要求するためである。履歴本体・before/after/inverse・旧receiptはそのまま保持する。後続の一斉切替という設計は変えていない。

`EDITING_API_ENABLED`はdefault false。実DBは移行していない。無効時は新session等を拒否し、flagだけを有効にして未移行DBへ書くこともschema検査で拒否する。今回の新schema適用と書込はDドライブのコピーに限定した。通常frontendと現行音声方式はまだ従来の経路で動く。フェーズ19の受け入れ前に有効化しない。

## 6. 変更ファイル

| 区分 | ファイル |
| --- | --- |
| 共通編集 | `backend/app/editing/{__init__,contracts,models,errors,registry,catalog,commands,service,schemas}.py` |
| HTTP | `backend/app/api/v1/routes/editing.py`、`backend/app/api/v1/router.py` |
| DB | `backend/alembic/versions/0005_editing_api_v1.py`、`backend/alembic/env.py` |
| 有効化・CORS | `backend/app/core/config.py`、`backend/app/main.py`、`backend/.env.example` |
| 共有receipt | `backend/app/services/mutation_receipt_service.py` |
| 文書 | 本書、`AGENTS.md`、`ARTIFACT_STORAGE_POLICY.md`、Safety Contract冒頭、フェーズ12設計の実装状況追記 |

既存未コミット差分を退避して保持。staging/commit/push/変更破棄は実行していない。今回分だけのdiffは製品外に保存する。

## 7. 段階検証と成果物

保存先は `D:\llm-ocr-archive\voice-assistant\20261006-phase13`。開始時958 Git対象/未追跡ファイル、DB backup、855業務ファイルを退避した。ログとapp.db本体等は業務ファイルSHA比較から除き、DBはテーブル内容をread-onlyで比較する。

| 検証 | 実行・結果 |
| --- | --- |
| コピーDB/API結合 | `backend/.venv/Scripts/python.exe .../verify_editing.py`。23項目成功。actual FastAPI route、SQLite、authoring/補正serviceを使用。migration、9基本操作、原子的保存、先頭ゼロ、再読込undo、NO_CHANGE、失敗receipt、他owner拒否、競合、同時再送、保存flush後故障、局所turn、終了後照会、body上限、型生成等 |
| pageの境界 | 上記の1項目は既存実セルIDを使ったdomain validation。renderer・実paged templateの受け入れとは区別 |
| 実起動・HTTP | `backend/.venv/Scripts/python.exe .../verify_live.py`。2構成成功。未移行コピー/無効flagでhealthと旧OCR GET、新schemaコピー/有効flagで保存・再送・receipt。各serverの正常終了と専用port消滅も確認 |
| Python静的検査 | `ruff check`で変更source全部、mypy `--follow-imports=silent --no-incremental`で新編集・HTTP・共有receiptの11 source。双方成功 |
| 生成TypeScript | 既存typescriptの`tsc --strict --noEmit --skipLibCheck --target ES2020 --module ESNext --moduleResolution bundler`で生成された型を検査。成功 |
| 実データ保持 | `.../verify_preservation.py`。実DB31テーブルの内容/schema、855業務ファイルのSHA、API台帳25件を開始時と照合。削除0、予定外source変更0 |
| 差分 | `git diff --check`成功。開始時差分と今回分を区別して保存 |

最終結合結果は `runs/20261006-222233-675859/results.json`、実起動結果は同runの`live-results.json`。`latest-run.txt`がrun参照。失敗した試行も各integration logとrunへ残し、原因を隠していない。最初の失敗は追加OCR欄のカタログ分類、別の停止は試験対象templateに未接続セルがなかったためで、実在する別DRAFTを追加の意味付け試験へ使った。同時再送の故障試験ではHTTP workerと呼出しthreadが別であることを踏まえ、待機条件をrequest IDへ結び付けた。

`before/`、`before.db`、`data-before/`、`baseline.json`、`phase13-only.diff`、`preservation.json`、`ruff-final.log`、`mypy-final.log`、`typescript-final.log`、`contracts`生成物、専用起動logを保存する。使い捨て試験scriptは製品に追加していない。実録音・会話全文は扱っていない。

実外部AI追加0回、実DB台帳累計**25/100回、残り75回**。コピー台帳も25件を保持している。新APIのHTTP要求数やschema生成回数を、許可された外部AI送信回数として計上しない。

## 8. 未検証・次フェーズ

全件unit/backend full tests、`verify_ocr_contract.py`の全領域比較、保存応答の全Golden replay、frontend build/実UI、実paged template結合、Google同期・XLSX出力、OpenAI tool受理、マイク・VAD・音声遅延は本フェーズの確認範囲に含めない。重い回帰はフェーズ18、実マイクは利用者参加のフェーズ19。現在の23項目をそれらの代替証拠として扱わない。

フェーズ14では基本handlerと同じprepare/persist、有限handle、batch/receipt/historyを拡張し、heading/records/marks/identity/nameと依存関係、current/all page制約、graph inverseを完成させる。新APIは無効のまま維持する。フェーズ15以降で手動UI、会話、音声取得へ順に接続する。
