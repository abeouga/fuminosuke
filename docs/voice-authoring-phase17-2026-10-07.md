# 文之助：連続音声と編集画面（フェーズ17）

2026-10-07 / 対象 `C:\Users\hanam\Desktop\llm-ocr-deploy` / 契約 `editing.v1`

連続音声・局所録音・読み上げを共通編集controllerへ接続した。明確な指示は保存し、成功receiptから「変更しました」を通知する。テンプレートの意味付けと単一ページOCRの補正が対象である。通常環境は新API無効・DB未移行のまま。元の`llm-ocr`は編集していない。

## 利用者の操作

連続モードは「音声入力」で開始し、セル選択なしで指示する。対象が一意なら保存、曖昧なら不足情報だけを質問する。承認操作は設けない。保存後のプレビューと対象欄の強調表示を確認し、そのまま次の指示・訂正・取り消しを話せる。終了は「音声入力を停止」。読み上げ中の新発話は再生を中断する。

セル選択後の「選択欄を録音」も残した。「録音を終了して送信」で区切り、同じcontroller・保存queueへ渡す。連続接続を使用できないブラウザーでも、対応録音形式とdecoderがあれば局所録音を使用できる。手動の線接続、セル編集、保存・確定の経路を保持する。確定済みOCRとACTIVEテンプレートで編集音声は停止する。

画面離脱、非表示、ページ範囲変更、停止、切断、上限で録音を終了する。マイクの自動再開・WSの自動再接続は設けない。現行flagが無効な通常環境では旧画面が継続する。新旧の一括切替はフェーズ19で行う。

## 実装境界

| 境界 | 主なファイル | 変更 |
| --- | --- | --- |
| 音声proxy | `backend/app/assistant/stream_proxy.py`, `stream_service.py`, `stream_contracts.py` | 所有session認証、固定送信先、commit ID/順序、字幕と完成指示の分離、時間/保留検査 |
| 永続metadata | `stream_models.py`, `alembic/versions/0006_editing_voice_streams_v1.py` | `editing_voice_streams`/`editing_voice_audio_items`の2テーブル。通常DB未移行 |
| 共有予算/終了 | `app/ai/api_budget.py`, `app/core/lifecycle.py` | 同一transactionで予約、アプリ終了時の接続/取得task解放 |
| HTTP/WS | `app/api/v1/routes/editing_voice.py`, `editing.py`, `router.py` | 状態、lease、WS、局所音声、所有receipt/質問TTS、有限catalogのUI投影 |
| 手動OCR保存 | `app/editing/manual_contracts.py`, `service.py` | 全欄補正を既存correct/revisionと共通receipt/履歴へ接続。物理対応の改変を拒否 |
| 取得/再生 | `frontend/src/assistant/ConversationAudio.ts`, `audioStream.ts` | VAD、16→24k PCM、録音、2発話buffer、割込み、再生中止、track/timer解放 |
| 会話/保存 | `conversation.ts`, `frontend/src/editing/store.ts`, `client.ts` | 発話開始世代、本人の先行receiptによるrevision進行、元journal復旧、後続手動入力保護 |
| 画面 | `ConversationControls.tsx`, `ReviewPage.tsx`, `TemplatePage.tsx`, `LayoutEditor.tsx`, `assistant.css` | 小型の開始/停止/局所録音、対象強調、正本反映、OCR手動操作終了保存/復旧 |
| 固定assets | `frontend/package.json`, `package-lock.json`, `vite.config.ts`, `THIRD_PARTY_NOTICES.txt`, `backend/pyproject.toml` | VAD/ONNX/WSのversion固定、同一originから配信、ライセンス同梱 |

backend変更が必要な理由は、秘密鍵をブラウザーへ渡さず、音声接続/送信前の永続予約、所有context、原子的補正・receipt・undoを保証するためである。手動OCRも同じ共有履歴へ入れるため、フロントだけの録音機能では完結しない。OCR provider、OCR prompt、検出/画像処理、mapping生成の変更はない。

### 音声と保存の分離

発話開始時にresource revision・page scope・selection/local edit世代を固定する。取得中でも手動編集を妨げず、古い発話は保存権限を失う。新しい発話からは最新contextを取得する。外部の保存世代へ旧指示を暗黙にrebaseしない。

STTの完成順序は発話順序の代用にしない。`input_audio_buffer.committed.item_id`とアプリのaudio IDを対応づけ、完成した2発話を開始順に処理する。早く届いた後続字幕で先行指示を選択しない。字幕deltaは表示だけに使い、IDが未対応なら最大2件・2000文字で一時保留する。audio IDの再利用・一音声からの複数turn開始は拒否する。

新発話の取得/分類中は先行指示の保存を保留する。通常の追加指示は捨てずに後続として実行する。狭い取消し表現は保存前の旧指示を中止する。訂正文は先行指示と合わせて再解釈する。既に保存済みなら、共通履歴のundo/通常補正へ渡す。`CONSUMED`を取消済みと表示しない。

同じ会話の成功operation/turnに連続しているrevisionだけを後続取得から認める。手動保存、選択変更、ページ変更、別writerで失効する。局所録音は選択handleに限定し、不足情報への再録音も同じ選択世代でのみ続ける。初回選択のUI世代0は、session内の単調増加する選択世代へ変換する。

TTSは成功receiptのnotification、または所有sessionに束縛した有効な不足情報質問だけを受け付ける。任意の成功文や自由な読み上げ文字列は受け付けない。新発話では再生・取得中TTSを中止し、録音側を継続する。echoCancellationの有効状態を確認できない場合は連続取得を開始しない。完成文字列の狭い照合も補助的に使うが、実機での反響除去の証明にはならない。

手動OCRの保存結果不明・表示失敗は元receiptから復旧し、後続入力を置換しない。競合では入力を退避し、最新結果の明示確認と同じrevisionの再照合後に共有queueを再開する。復旧/復元だけで後続入力を自動再送せず、次の編集操作または保存へ持ち越す。

### 上限と通信

| 項目 | 制限 |
| --- | --- |
| 1接続 | 最大5分。lease発行時から期限を固定 |
| 累計接続 | DB全体で20分。発行時に全時間を予約し、正常終了時だけ実接続時間へ精算 |
| API | 既存100回台帳と共有。WS接続1予約、発話の最初のframeで1予約、解釈/TTS/局所STTは既存HTTP予約 |
| 1発話 | 最大30秒、PCM 24kHz/16-bit mono。局所録音も30秒/10MiB |
| 保留 | 最大2発話/計60秒。先行認識待ちや取得期限超過で停止 |
| 通信buffer | 開始待ち96KiB、ブラウザーWS送信待ち256KiB、1frame最大24KiB |
| 1接続のaudio metadata | 最大100項目、audio ID再利用禁止 |

WSの一接続内送信も保守的にAPI枠を消費する。これはHTTPリクエスト数や課金額の計測ではない。最初のframeを送る前に予算とaudio metadataを同一transactionで予約する。失敗/クラッシュのcall予約を払い戻さず、正常に閉じていない時間leaseも全枠を消費した状態で残す。空台帳コピーで実APIを送らない。

送信先は `wss://api.openai.com/v1/realtime?intent=transcription` に固定。API keyをURL/ブラウザーへ公開せず、session tokenは接続直後のauth frameで照合する。local hostと許可originを検査してから外部予約へ進む。PCMのappend/commitだけを送り、providerのVADは無効、client側で区切る。日本語・`gpt-live-transcribe`・`delay=low`の設定は公式資料と照合した。実OpenAI WSとの疎通は未実施である。[Realtime transcription](https://developers.openai.com/api/docs/guides/realtime-transcription), [公式WebSocket例](https://developers.openai.com/cookbook/examples/speech_transcription_methods)

録音音声と全文字幕はDBへ保存しない。永続化するのはstream/audio ID、開始世代、時刻、sample数、予約ID、処理状態である。録音bufferと会話記憶は画面/所有sessionの一時メモリーに限定する。業務journalには型付き保存要求・request ID・所有tokenだけをタブ内に保持する。

### 依存とassets

`@ricky0123/vad-web=0.0.31`、`onnxruntime-web=1.24.3`、`websockets=16.1.1`を固定した。Silero v5、worklet、ONNX WASM/mjsとnoticeをbuildへ同梱する。client VADは16kHzのframeを受け、PCM送信用に状態を保持した24kHz変換を行う。無音700ms、先頭250ms、最短250msである。[VAD API](https://docs.vad.ricky0123.com/user-guide/api/)

VAD約2.33MB、WASM約12.36MBで、初回開始時に読み込む。同一originだけを使い、実行時にCDNから取得しない。`voice-assets.json`にbuild出力のSHA/サイズを保存した。noticeは配布元のISC/MIT本文を同梱した。

依存監査は追加前後とも同じ6パッケージ/6件（moderate2/high2/critical2）。新しい音声依存による追加指摘はない。既存依存の解消は本フェーズでは行っていない。開始時/終了時の監査JSONを保存した。

## 確認結果と限界

証拠は `D:\llm-ocr-archive\voice-assistant\20261007-phase17`。外部AIはすべて制御応答へ置換し、コピー実DB/APIで業務保存する。通常DBに試験値を戻さない。

| 確認 | 結果 |
| --- | --- |
| `verify_streams.py` | 8項目。migration、origin/所有拒否、固定PCM設定、完成順逆転、2件上限、30秒/hold、選択失効、局所実decoder、手動OCR receipt/undo、20分・100回停止 |
| `verify_browser.mjs` | Chromeで11シナリオ。実レビュー画面、実ONNX/VAD初期化と模擬mic解放、実MediaRecorder/WebAudio音源、局所保存/通知、保存応答故障/後続入力保護、競合と明示再開、順序逆転、保存前中止、保存後undo、手動介入後の新指示、再生中断/終了清掃、resample/echo補助 |
| `verify_template_ui.mjs` | 1シナリオ。実TemplatePage componentをChromeへmountし、無選択指示から意味作成/接続/読取設定を保存、正本反映と物理欄強調を確認 |
| `verify_conversation.py` / `regression_manual.py` | 22/14項目通過。前フェーズの短い保存・会話境界回帰 |
| `npm run build` | TypeScriptと本番build通過。音声assets/notice同梱 |
| `ruff check` / 対象9ファイルの`mypy --follow-imports=silent --no-incremental` | 関連静的検査通過。全体の型検査成功を意味しない |
| `verify_preservation.py` / `git diff --check` | 通常31テーブル・31API予約・855業務ファイル、既存差分とコピー受付snapshot/対応を保持 |

API各スクリプトの件数は共通migration確認を含むため単純合算しない。途中失敗の記録も保持した。実ブラウザー確認で初回選択世代0の拒否と、後続発話だけが先に完了した場合の処理再入を発見し、製品を修正して同じ操作を再確認した。fixtureのURL/React import/局所参照の誤りは試験を修正し、製品の所有/対応検査を弱めていない。

VAD初期化とtrack解放はChromeの模擬マイク、録音decoderは生成したWebAudio信号で確認した。順序/訂正/通知ではVAD callbackとSTT/モデル/TTS応答を制御した。利用者の実マイク、発話の認識精度、実OpenAI WS接続、日本語TTSの聞き取り、反響・割込みの実環境精度、応答時間分布は未検証。ブラウザーの模擬確認を実音声受け入れと報告しない。

重いunit/integration/backend/frontend全体回帰、OCR contract開始時/変更後比較、実XLSX全体対応はフェーズ18へ残す。依存を含む広いmypyでは変更外の指摘も観測しており、開始時との差分切り分けを18へ引き継ぐ。OCR安全性を完全には検証できていない。

通常の31テーブル内容、31API予約、855業務ファイルは開始時と一致する。コピーで明示した値/判定/revision以外の受付job、sourceRegionId/boundingBox、確定snapshot、paged結果、原本/cropを比較した。この確認範囲で既存OCR contractを維持した。外部AI追加0回、累計31/100回、残り69回。通常DBへのschema移行・新API有効化・commit/pushは行っていない。

## 次の境界

**フェーズ17で停止する。次はフェーズ18、Astra / xhigh。**

フェーズ12の受け入れmatrixと実装差分に従い、短い境界テストから必要なbackend/frontend full testsとOCR契約比較へ進む。結果不明の保存/再読込/他writer/マイク停止/質問失効、複合意味付け・undo・ページ/XLSX対応を確認する。依存型検査と依存監査の既存指摘を切り分ける。失敗のskip・assertion弱体化で完了扱いにしない。

フェーズ19はGPT-6.1-Sol / high。利用者を呼んで実マイク・実STT/TTS・応答時間・スピーカー反響を検証し、調整後に新hand/voiceを同時切替する。通常切替の実施はこのフェーズ17の完了には含まれない。
