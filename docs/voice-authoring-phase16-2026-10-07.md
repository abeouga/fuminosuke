# 文之助：会話controllerと共通編集接続（フェーズ16）

2026-10-07 / 対象 `C:\Users\hanam\Desktop\llm-ocr-deploy` / 契約 `editing.v1`

フェーズ16では、文字入力または完成した文字起こしから対象を解決し、手動と同じ保存queueへ渡す経路を実装した。承認操作は設けない。連続マイク、局所録音の新UI、読み上げ、割込み制御はフェーズ17で接続する。通常環境は新API無効・DB未移行のまま。元の`llm-ocr`は変更していない。

## 実装範囲

| 境界 | 主なファイル | 変更 |
| --- | --- | --- |
| 会話の型・実行 | `backend/app/assistant/conversation.py`, `conversation_contracts.py` | 開始時context束縛、有限tool loop、曖昧質問、60秒の一時記憶、中止・再送 |
| モデル通信 | `backend/app/assistant/conversation_provider.py` | Responses strict Function calling、既存送信予算、追加承認なし |
| 対象解決 | `backend/app/assistant/target_resolution.py`, `backend/app/editing/catalog.py` | 名称・alias・印字、位置・見出し・行・role、同名候補の停止 |
| 保存・訂正 | `backend/app/editing/service.py`, `corrections.py`, `contracts.py` | controller命令の固定、直前の一値/一名称の対象訂正、共有履歴 |
| HTTP・生成型 | `backend/app/api/v1/routes/editing.py`, `backend/app/editing/schemas.py`, `frontend/src/editing/generated.ts` | turn開始/解釈/状態/中止の型付きAPI |
| フロント共通保存 | `frontend/src/editing/store.ts`, `client.ts`, `frontend/src/assistant/conversation.ts` | template/OCRの共通queue、解釈後の自動送信、元receipt復旧、手動操作で失効 |

backend変更の理由は、ブラウザー以外のツール利用でも同じ対象・revision・原子的保存・取り消しを保証するためである。OCR provider、prompt、画像処理、mapping生成の精度改善は行っていない。

### 会話から保存まで

1. frontendが描画中・入力中・未解決の保存結果を確認し、発話開始時のresource/context/selection/local edit世代を固定する。
2. controllerは現在の有限catalogと指示をモデルへ渡す。公開は`get_editing_context`、`find_targets`、`apply_edit_batch`、`undo_edit`、`get_edit_receipt`の5ツールだけ。編集操作は既存22種で、resourceとページのcapabilityに限定する。
3. 対象と値が確定したら、controllerがrequest ID・revision・turn・由来を付与し、要求全体のdigestを固定する。モデルにこれらの権限引数を作らせない。
4. frontendは`READY`の命令をタブのjournalへ保存し、同じresource queueへ直ちに送る。`READY`は内部の解釈完了状態であり、利用者の承認待ちではない。
5. 保存receiptと同じrevisionの正本を取得してから「変更しました」を通知する。LLMが自由に生成した成功文は使用しない。変更なし、曖昧、拒否、保存済みで表示失敗を区別する。

`POST /api/v1/editing/sessions/{sessionId}/conversation/turns`で開始し、`POST .../turns/{turnId}/resolve`で文字指示を解釈する。`GET/DELETE .../turns/{turnId}`は状態照会/中止。保存は既存の`batches`/`undo`へ送る。publicな任意の`ready`化APIは設けていない。

### 対象解決・不足情報

catalogは初期50件とページ付き検索で有限に公開する。別名は保存済みの意味・接続から導出し、近くに印字があるという推測で意味を付けない。OCRの位置・見出し・行は受付snapshotと既存の切り出し情報から読む。

同名候補を最高スコアだけで選択しない。選択・直近の単一編集・一意な検索結果で解決する。位置/見出し/行を検索条件へ加える場合は、発話にその条件があることも確認する。ページングで1件だけ返った場合を一意とは見なさない。曖昧な項目が一つでもある操作群は全件未保存とする。

「ここを番号にして」の変更後の名前を対象名と誤認しない。選択/直近対象のない指示語は不足対象を質問する。「はい」「承認する」「それでよろしく」だけから変更を作らない。

質問への回答は元指示と合わせて再解釈する。記憶は所有sessionとcontext世代に束縛した最大60秒のメモリーだけ。別の手動保存・選択変更・失効を跨いで回答を適用しない。音声、全文文字起こし、モデルの継続情報を業務DBへ保存しない。保存要求のjournalには型付き変更内容と所有権・要求IDを残す。

### 訂正・競合・復旧

- 「違う、00124」は直近の一対象へ通常の値補正。「違うよ、今のを戻して」は共通履歴の直前群を取り消す。
- `retarget_previous`は直前の単一`ocr.set_value`または単一`cell.rename`に限定する。誤った欄を元に戻し、同じ値を別の一欄へ一transactionで保存する。別値・複数操作・曖昧な履歴は拒否する。訂正群自身のundoも元の二欄を一括で戻す。
- 対象訂正の履歴は`supersedesOperationId`を持つ。局所録音・ページ・mapping/physical identityの検査は復元側にも適用する。
- モデル待機中はDB transactionやresource queueを占有しない。手動操作開始で古い発話を失効させ、他者revisionへ暗黙にrebaseしない。
- 保存後に応答が失われた場合は元のrequest IDで照会する。元receiptが見つからない場合だけ同一body/keyを再送する。新しい要求として再実行しない。
- 保存後の中止は取り消しを意味しない。`CONSUMED`と元receiptで保存済みを示し、必要なら別のundoを行う。表示失敗と再読込でも保存結果を保持する。

### 通信上限

製品の解釈モデルは既存設定`gpt-6.1-sol`、reasoning `low`。開発担当モデルと区別する。一発話のモデル要求は最大4回、読み取りtoolは最大6回、発話全体は60秒。SDKの自動再試行なし。strict schemaと`parallel_tool_calls=false`を使用し、複数のtool callが返った場合は実行前に停止する。continuationではreasoning itemも保持する。[OpenAI公式Function calling](https://developers.openai.com/api/docs/guides/function-calling)

新しいDB schema変更はない。既存turnのJSON metadataにaudio item ID、request ID、命令digestを保存し、会話記憶は永続化しない。streaming接続・音声時間・保留発話の予算はフェーズ17の担当範囲である。

## 確認結果と証拠

保存先は`D:\llm-ocr-archive\voice-assistant\20261007-phase16`。試験DB・ログ・生成型・試験プログラムはこのディレクトリに置き、通常DBへ試験値を戻していない。

| 検証 | 結果・範囲 |
| --- | --- |
| `verify_conversation.py` | 22項目通過。コピー実DB/FastAPI、モデル応答fixture。無選択OCR、先頭ゼロ、対象訂正/undo、同値、digest改変、曖昧質問と回答、群の原子性、競合/中止/失効、局所範囲、再送、権限偽装を確認 |
| `regression_manual.py` | 14項目通過。既存手動保存・線接続・未完成DRAFT・共有undo・receiptの短い回帰 |
| `regression_pages.py` | 7項目通過。保存実XLSXの2ページ、ページ境界、見出し・マーク・手動保存、原本/基準画像保持 |
| `verify_frontend.ts` | 6シナリオ通過。製品TSをbundleしてNodeから実HTTP/コピーDBへ接続。手動/会話queue、応答欠落、journalからの復旧、手動操作による失効、OCR正本revisionを確認 |
| `live_conversation.py` | 実OpenAIで4シナリオ通過。名称変更、対象訂正、自然文undo、意味作成+接続+text読取を保存。最後の意味・接続・読取種別を保存DBでも照合 |
| `ruff check ...` / `mypy ... --follow-imports=silent --no-incremental` | 関連22 Pythonファイルの静的検査通過 |
| `python -m app.editing.schemas --output ...` / `npm run build` | 生成TypeScriptとfrontendの一致、型検査と本番build通過 |
| `verify_preservation.py` / `git diff --check` | 既存差分・正常DB・855業務ファイル・コピー受付snapshot/対応の保持、差分検査 |

各APIスクリプトの件数には同じmigration確認1件を含むため、件数を単純合算しない。`latest-*-run.txt`が最終成功runを指す。途中失敗ログも保持した。初期fixtureの候補を公開50件の外から直接参照していた問題は、製品側の対象検査を弱めず、実際の検索を行う試験へ修正した。指示語の検査漏れは製品側を修正して再確認した。

フロント検証はstorageの最小polyfillを用いたNode実行であり、ブラウザーの新UI E2Eではない。業務API・DBは置換していない。録音、STT、TTS、VAD、音声割込み、実マイク、応答時間の分布は未検証。全件unit/backend/frontend/E2E、OCR contract全体回帰は合意したフェーズ18へ残す。OCR安全性を完全には検証できていない。

実APIは6回追加、開始25回→累計31／100回、残り69回。全6件が`voice.editing`で実DB台帳に予約され応答を受領した。失敗時の払い戻しやコピーDBの空台帳利用は行っていない。実マイク/録音/TTS/OCRの追加呼出しは0回。保存responseは試験用文字指示と有限catalogのものであり、実利用者の録音ではない。

通常DBのschemaは31テーブルのまま、API台帳以外の30テーブルと既存25予約を保持した。855業務ファイルは開始時と一致する。コピーでは明示したOCR補正の値/判定/revisionだけを許容し、受付job、sourceRegionId/対応metadata、確定済みsnapshot、paged結果、原本/cropファイルを比較した。既存OCR contractを維持した範囲と、最終回帰の未実施を区別する。

## 次の境界

フェーズ16で停止する。次は **フェーズ17：GPT-6.1-Sol / xhigh**。

`EditingConversation`を連続音声と局所録音UIへ接続し、completed STTだけを実行へ渡す。VAD、音声item ID/順序、保留最大2発話・60秒、TTS中断、WSの共通予算/時間leaseを実装する。手動操作中も取得を続ける場合の待機と世代失効を区別し、単に新発話が始まっただけで前の指示を捨てない。

本フェーズの`begin`は実行可能な境界を要求するため、取得中の音声buffer/割込み管理を担わない。次フェーズで取得と実行を分離する。reloadや画面離脱後にmicを自動再開しない。局所録音と手で線を引く操作を残す。通常切替と利用者参加の実マイク受け入れはフェーズ19で行う。
