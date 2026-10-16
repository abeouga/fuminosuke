# 音声・Decisions再設計 フェーズ4：音声取得・保留回答・連続訂正

2026-10-09。対象は `C:/Users/hanam/Desktop/llm-ocr-deploy`。
フェーズ1 §13の音声セッションを実装し、分離コピーと制御した音声eventで限定検証した。
通常利用への切替、画面への接続、実マイクの受入は完了していない。
次はフェーズ5、開発モデル **GPT-6-Astra / xhigh**。ここでモデル切替を待つ。

## 1. 変更の目的と境界

以前はv2 commandへ音声全文と保留回答を接続していなかった。
音声segment、全文instruction、保存commandを分離し、途中の認識結果を保存に使わない構成とした。
質問への回答では元の全文、回答の順序、判明済みの対象と値を保持する。
フロント側だけでは、provider itemの順序・所有、保存直前の割込みfence、質問の期限、
原子的な対象差替えを保証できないため、application／adapterとcommand metadataを変更した。

OCR読取provider／prompt／mapping／association／crop／受付snapshot／出力bindingは変更していない。
明示補正は既存の補正・revision・receiptを通す。対象差替えは旧対象の復元と新対象への移動を
同transaction・一revisionで保存する。既存OCR contractを維持する構成だが、
この限定検証で全領域のOCR安全性を完全に検証したとは扱わない。

## 2. 実装

### 音声取得

- 所有sessionとloopback／Originを検証し、1 sessionにつき1接続。
- v2 WebSocketは `/api/v2/editing/sessions/{session_id}/voice`。最初にauth envelopeを送る。
- PCMは24kHz・16bit mono。上流は `gpt-live-transcribe`、日本語、delay low、server VADなし。
  所有catalogの欄名・別名・列名をヒントにする。局所録音／選択表では範囲を絞る。
- 最大30秒のsegmentを順番にcommitし、全文は最大180秒／8,640,000 bytes。
  終端manifestは全segmentの順序と一致させる。provider completionの到着順には依存しない。
- 全item完了後の全文digestと所有bindingが一致した指示だけ共通commandへ渡す。
  segment完了やprovisional字幕ではモデル解析・保存を開始しない。
- 通常無音1200ms、列挙・訂正の途中3000ms、局所録音の明示終了は無音待ちなし。
  `speech.stopped`では処理状態を先に通知する。ブラウザーのVAD／画面はフェーズ5で接続する。
- 新たな発声時は同世代の未保存commandをholdし、訂正・追加なら旧planを無効化して全文を再解釈する。
  独立した指示ならholdを解除し、説明可能な先行commandの完了後に実行する。
- 接続300秒、累計1200秒。新発声前のidle境界で必要なら接続を更新し、追加予約する。
  更新失敗や切断で自動再接続・録音再開はしない。不完全な音声による保存も行わない。
- 接続と各segmentは送信前にAPI枠を予約する。失敗のAPI予約は払い戻さない。
  音声・全文は上限付きRAMだけに保持し、完了・取消・終了で解放する。

クライアント制御の正本は `backend/app/api/v2/voice_contracts.py`、生成物は
`docs/contracts/editing-v2/voice.schema.json` と `frontend/src/editing/voice-v2.generated.ts`。
制御は `utterance.begin`、`segment.begin/end`、`speech.stopped`、`utterance.end/cancel`、`capture.stop`。
応答は `ready`、`potentialContinuation`、`transcript.delta/completed`、`processing`、
`command.accepted`、`connection.renewed`、`error`。保存結果は既存の認証付きcommand／SSEから取得する。

文字起こしwireの設定・接続だけを `app/ai/realtime_transcription.py` に共有化した。
旧assistantの同関数は再exportし、旧音声の設定／接続挙動を変更しない。
上流の仕様確認は [OpenAI Realtime transcription](https://developers.openai.com/api/docs/guides/realtime-transcription) に基づく。

### 保留回答と訂正

- 元の全文と32回答以内・合計20,000文字以内の順序付きturnをRAMで保持する。
- 元のinstruction期限900秒、質問期限300秒。GETや意味のない承認で延長しない。
  GETを挟まず新POSTを送った場合も、期限切れの旧質問をEXPIREDにする。
- questionId／version、所有、revision、scope、interactionEpochを照合する。
  値不足への「それでよろしく」を値として保存しない。
- 発行済み候補で一意に補完できる回答はモデルなしで実行する。API枠が尽きてもこの処理は利用可能。
  自由回答は全文・既知の対象と値を付けてResponsesへ渡し、既知の変更を落としたplanを拒否する。
- 対象範囲の質問への明示回答は `/scope-answer` で受け付ける。
  元の値・instruction ID・期限を保持して再束縛し、旧対象候補と旧planを捨てる。
  一意な範囲提案への音声の「はい」も接続を維持して同じ処理へ渡す。
- 通常の手動scope変更は旧planを無効化する。手動interactionEpochを越えて指示を移さない。
- 質問中の「それは中止」「キャンセルして」等は保留を終了する。
  「それは中止、次は…」「キャンセル、代わりに…」では新instructionを開始する。
  対応する明示制御句だけを分類し、曖昧な自由文を取消と推測しない。
- 保存済み単一変更の対象差替えは元の対象を復元して同じ値／名称を新対象へ移す。
  receipt・revision・直近群を再検証し、複数対象や説明できない移動を拒否する。
- 再送は永続receiptを先に返す。再起動では未保存指示と質問をINTERRUPTEDとし、旧回答を拒否する。

### 主な変更ファイル

| 責務 | ファイル |
| --- | --- |
| 音声・保留の純粋モデル | `backend/app/editing/domain/utterance.py`、`dialogue.py` |
| 指示受付・割込み・回答 | `backend/app/editing/application/controller.py`、`voice_acquisition.py`、`instruction_planner.py`、`ports.py` |
| 音声接続・共通wire | `backend/app/editing/infrastructure/voice_stream.py`、`backend/app/ai/realtime_transcription.py`、`backend/app/assistant/stream_proxy.py` |
| 永続fence・期限・対象移動 | `backend/app/editing/infrastructure/command_repository.py`、`command_models.py`、`mutation_uow.py` |
| Evidence・scope・接続 | `backend/app/editing/infrastructure/plan_binder.py`、`planner_provider.py`、`sessions.py`、`runtime.py`、`domain/planning_catalog.py` |
| HTTP／WS・schema生成 | `backend/app/api/v2/editing.py`、`editing_contracts.py`、`voice_contracts.py`、`backend/app/main.py`、`backend/scripts/generate_editing_v2_contracts.py` |
| migration | `backend/alembic/versions/0008_editing_voice_dialogue_v2.py`：commandのheld列。コピーだけに適用 |
| Luna比較の経路指定 | `backend/app/editing/domain/planning.py`：実験用responses_first。既定decisions_firstを保持 |

## 3. Lunaファーストの実API測定

「Lunaファースト」はDecisionsを省略して `gpt-6-luna` のResponsesへ直接渡す経路として比較した。
Decisions優先側もアプリ内モデルは `gpt-6-luna`。開発に使用するCodexモデルとは別である。
同じテキストと業務状態で各経路1回ずつ測定し、途中はコピー上のundoで同じ値へ戻した。
時間はcommand POST開始から保存結果取得まで。音声認識・無音待ち・ブラウザー描画時間を含まない。

| 指示 | Decisions優先 | Luna直接 | 実際の経路 |
| --- | ---: | ---: | --- |
| 一行目の体重を507に変更 | 2.448秒 | 7.179秒 | Decisions／Responses |
| 名号を00789に変更 | 0.770秒 | 3.109秒 | Decisions／Responses |
| 長文中の訂正を含む2セル変更 | 5.051秒 | 4.700秒 | 両方Responsesへ直接 |

6件すべてAPPLIEDで、期待する対象と値を保存した。preview GETまでの時間は順に
2.462／7.191秒、0.783／3.122秒、5.064／4.713秒。
単純指示では今回のLuna直接経路が遅かったため、既定のDecisions優先を維持する。
長文は両方同じ経路であり、0.351秒差を方式の優劣とは判定しない。
各条件n=1、経路の実行順固定で、warm/coldやネットワーク変動を分離していない。
p50／p95、実音声の速度・精度、設計目標の達成を示す測定ではない。

実APIは **6回（Decisions 2、Responses 4）**。開始150、終了 **156/196、残40**。
通常DBの共有台帳で予約した。その他の制御fixture検証は外部APIを使っていない。

## 4. 検証と証拠

証拠保存先：`D:/llm-ocr-archive/voice-assistant/20261009-redesign-decisions-phase4`。

| 検証 | 結果と範囲 |
| --- | --- |
| `verify_phase4.py` | 19条件成功。質問時の部分保存拒否、候補回答、既知値保持、対象差替え、hold、順序・digest・manifest等 |
| `verify_dialogue_boundaries.py` | 22条件成功。300／900秒期限、scope回答、実PlanBinderの行と値保持、取消、再起動、180秒分PCM上限等 |
| `verify_voice_wire.py` | 18条件成功。認証、同時接続、上流設定、PCM予約順、局所終了、音声scope回答、更新・障害・RAM解放等 |
| `phase2_regression.py` | 共通API54条件成功。原子的保存、競合、再送、取消、undo、scope、SSE、旧共通保存等 |
| `validate_contracts.py` | 8 schemas、20 examples、22操作の既存意味を維持。domain／application依存境界を確認 |
| Ruff／Mypy | 対象の静的検査成功。Mypy30 source files |
| 生成TypeScript | `v2.generated.ts`／`voice-v2.generated.ts` のstrict検査成功 |
| 差分・保持監査 | `git diff --check`成功。通常業務表・従来試験DB・855業務ファイル・HEADを保持 |

HTTP側は移行した実SQLiteコピー上の実ASGI APIと実保存を使用した。
音声側は制御WebSocket／上流応答とPCM fixtureを使用した。実OpenAI文字起こしやマイクの精度試験ではない。
180秒はPCM容量と制御eventの境界検証であり、実時間180秒の録音受入ではない。
旧試験DBは音声時間を使い切っていることを検証し、正のwire試験には別コピーの未使用時間metadata fixtureを
明示的に準備した。元DBの時間台帳や共有API枠はリセットしていない。
初回に検出したbinderのturn参照不整合、取消receiptの検証期待値、fixture設定不足のログも保持した。

再実行はbackendを作業ディレクトリにして `.venv/Scripts/python.exe` と証拠保存先の各scriptを使用する。
静的検査は `ruff check` と `mypy --follow-imports silent` で domain、application、infrastructure、v2 APIを対象とした。
TypeScriptは `tsc --noEmit --strict --skipLibCheck --target ES2022 --module ESNext` で生成2ファイルを対象とした。
benchmarkの再実行は共有API枠を消費するため、必要性を確認してから行う。

通常DB31表の内容差分はAPI台帳6予約だけ。通常のmigrationは旧版のまま、新held列は未導入。
従来試験DB36表とmigrationは保持。通常のediting機能は無効のまま。
開始時の既存未コミット差分を保持し、staging／commit／push／既存server再起動を行っていない。

## 5. 残作業と終了境界

- フェーズ5：画面への連続音声／局所録音／意味付け接続、任意表scope、全変更セル同時枠、
  移動animation、画面外scroll、表名overlay、手動編集との共存、対象build／実画面。
- フェーズ6：統合の責務と失効、余分なモデル往復、queue／表示競合、計測に基づく性能調整。
- フェーズ7：重い全体回帰、全22操作、保存response／paged／crop／XLSX、実マイク、速度・精度、移行・切替。

既存の `scripts/verify_ocr_contract.py` は開始時点で削除されており、今回実行していない。
backend full tests、frontend full tests／build、ブラウザー操作、実マイク、実音声provider、
自由回答の実モデル精度を今回検証していない。TTSは無効を維持した。
これらの未検証を残した状態を、通常音声編集の本番受入完了とは報告しない。
