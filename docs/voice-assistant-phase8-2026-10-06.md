# 文之助 音声接続・API予算（フェーズ8）

対象は `C:\Users\hanam\Desktop\llm-ocr-deploy`。実装ブロックはGPT-6.1-Sol / high。
旧OCRリポジトリへの書き込み、commit、push、既存差分の破棄は行っていない。

## 実装範囲

- サーバー側STT、Responsesのstrict schemaによる1操作の抽出、変更案と保存成功receiptのTTS。
- 文章による自然言語指示も同じ提案処理へ接続。従来の4操作の手入力とボタン承認を維持。
- MediaRecorderのMIME選択、マイク許可待ち・取消・録音終了・59秒での自動終了・10MiB上限、streamとbufferの解放。
- 録音とTTSの排他。再生停止、object URLの解放、選択・編集・案・challenge変更時の取消。
- サーバーはraw bodyを上限付きメモリで受信する。UploadFileの一時ファイル退避を使わない。
- FFmpegをnetwork protocol禁止のpipe入力で起動し、15秒の処理上限、PCMへの正規化、解読後60秒上限、0.3秒下限と無音検査を行う。実行ファイルがなければ録音を無効にし、手入力へ戻る。
- 提案TTS完了後に新しく開始した録音turnだけを音声承認に使用。「変更を適用」、undoでは「取り消しを適用」の固定句を決定的に照合する。「はい」、同じ指示録音、LLMの承認推測は使用しない。
- 承認turnをproposal、差分hash、challenge hash、session/context世代と期限へ束縛し、適用transactionで一度だけ消費する。音声認識後も従来のrevision・現在値・編集可否検査を通す。
- ブラウザーの承認済み要求journalには承認turn IDも含め、結果不明時に同一キー・同一内容で照会/再送する。旧ボタン要求のdigestに新しいnull項目を加えない。
- Host/Originをloopback画面へ限定。これはローカルUIの誤適用防止であり、本人確認や複数利用者の認証基盤ではない。

`app/assistant`は音声turn・提案の所有と状態を管理し、共通HTTP予算は`app/ai`へ分離した。
OCR側が音声操作domainへ依存する構成にしない。既存補正/authoring/receipt transactionが業務保存の正本である。
外部モデルが選べるのは許可された操作と保存文字列だけで、対象IDや直接の保存権限は与えない。
音声APIへ送る情報は音声・指示・選択項目の表示名/現在値/許可操作に限定し、原本画像や全layoutは送らない。
生音声・会話全文はDBや通常ログへ保存しない。turnには用途・要求digest・最小の結果と結び付けのみを残す。

## 共通API予算

外部AI APIは累計 **13／100回、残り87回**。STT3、提案2、TTS8、OCR0。
同じdeploy DBの`api_call_ledger`を実HTTP送信前にtransactionで予約する。並行要求も最大100回まで。
SDK内retryは0、redirectは無効。失敗・送信可否不明・再起動・response parse失敗で払い戻さない。
`RESPONSE`はHTTP応答headerの受信を示し、音声bodyの読み込みや業務保存の成功を意味しない。
予算境界の100回試験は通信fixtureと別DBで行い、実OpenAI呼び出しに数えない。
ライブ検証の業務DBもコピーだが、API予約だけはdeploy DBの共通ledgerへ記録した。
DBの復元や検証用コピーを使う場合、空の検証ledgerを実消費の正本へ戻してはならない。

追加migrationは`0004_assistant_voice_v1.py`、headは`assistant_voice_v1`。
`assistant_turns`と`api_call_ledger`を追加し、既存28業務テーブルは全行・全列を維持した。
alembic_versionを含むDB全体は31テーブル。既存855データファイルのSHAも不変。

## 設定と公式仕様

`backend/.env.example`にVOICE_PROVIDER、VOICE_STT_MODEL、VOICE_PROPOSAL_MODEL、VOICE_TTS_MODEL、
VOICE_TTS_VOICE、VOICE_MAX_AUDIO_BYTES、VOICE_MAX_RECORD_SECONDS、VOICE_FFMPEG_PATHを記載した。
初期モデルは`gpt-transcribe`、`gpt-6.1-sol`（reasoning low）、`gpt-4o-mini-tts`、声は`marin`。
既存のバックエンドOPENAI_API_KEYを使用し、キー作成・権限追加・frontendへのキー配布は行わない。
SDKは1.97.1を維持した。正常起動環境のPATHにあるFFmpegを使用する。

確認した公式仕様は [File transcription](https://developers.openai.com/api/docs/guides/speech-to-text)、
[Text to speech](https://developers.openai.com/api/docs/guides/text-to-speech)、
[Structured outputs](https://developers.openai.com/api/docs/guides/structured-outputs?api-mode=responses)、
[GPT-6.1 Sol](https://developers.openai.com/api/docs/models/gpt-6.1-sol)。
UIにはOpenAIへの送信目的とAI音声である旨を表示する。

## 検証証拠

成果物は `D:\llm-ocr-archive\voice-assistant\20261006-phase8`。

| 確認 | 結果と証拠 |
| --- | --- |
| 短いAPI/DB確認 | `verify_voice.py`、`integration-results.json`、`integration-final.log`。10シナリオ通過。外部API0回 |
| 送信前停止 | 無音・60秒超・MIMEと実内容の不一致・10MiB超・所有者違い・外部Origin・余分なschema propertyを拒否 |
| 案と承認 | STT→案では保存ゼロ、同じturn/同じ音声再送で追加provider呼び出しゼロ、別内容は拒否。再生完了前の承認・曖昧な固定句・古いcontextを拒否。承認turnは一度だけ消費 |
| 取消 | STT処理中の取消後にLLMへ進まず、保存済み正本を維持 |
| 保存境界 | OCRの他欄・確認メモ・fieldPreviewsを維持。同じ承認済み要求から一度だけrevision更新 |
| 予算 | 送信より前の予約、HTTP失敗・予約のみの消費、残2回へ4並行要求で成功2、再起動後100回を保持 |
| 音声形式 | 保存した実TTS応答の仮WAV長を実frame数へ再構成。WebM/OpusをPCMへ解読。外部API0回 |
| migration | `migration-copy.log`、`migration-actual-results.json`、`migration-fresh.log`。既存コピー・実DB・空DBでheadとFKを確認 |
| ライブAPI | `live-runs`に制御音声・案・receipt・各試行の結果。先頭ゼロ付き00123、STT、strict提案、提案TTS、固定句STT、receipt TTSを確認 |
| 限定UI | OCRで文章指示→案→読み上げ完了→ボタン適用→正本再取得→成功receipt読み上げ。テンプレートで名称案→TTS途中停止→音声承認無効→ボタン適用→fieldLabel再取得 |
| UIデータ照合 | `preservation-results.json`。OCR1項目とtemplate1セルのfieldLabelだけを変更、各revision 1→2、操作履歴各1件。提案/receiptの読み上げ完了と途中停止をDB状態でも確認。UIは制御provider、外部API0回 |
| 静的確認 | `mypy.log`の18ファイル通過。共通AI/assistant/route/configのruff、frontend build、git diff --checkを実行 |
| 通常起動 | `runtime-results.json`。backend 8010/health、frontend 5180が200、worker稼働、音声設定とdecoderが利用可能、assistantの17 API pathを確認。再起動前後31テーブル一致、API予算13回を保持。検証用8011/5181と検証タブは終了 |

今回の差分は27ファイル（追加10、変更17、削除0）。`phase8-files.json`と`phase8.diff`で開始時からの差分を確認できる。
主な変更は`backend/app/ai`、`backend/app/assistant`、assistant route、設定とmigration、OCR/CLIの送信予算接続、
`frontend/src/assistant`、本記録とAGENTS/Safety Contract/保存方針である。既存の未コミット差分は保持した。

ライブTTSで仮のRIFF/dataサイズを通常の長さと解釈した不具合が発生した。
2回の試行はローカル音声処理で失敗し、実frame長からheaderを再構成して修正した。
成功済みのSTT/提案を繰り返さず、保存した実提案の再生を使用してTTS以降を確認した。
receipt TTSは1回body受信でタイムアウトしたが、保存receiptを維持し、追加予約した1回の再試行が成功した。
これらの失敗も13回に含める。モデル変更・自動retry・回数払い戻しは行っていない。

OCR providerの変更は共通送信予算の接続のみ。OCR prompt/schema/model設定、画像、association、mapping、crop、出力adapterは変更していない。
既存OCR contractを維持した。今回の証拠は保存/対応関係と音声接続の境界であり、新たなOCR精度保証ではない。

## 未検証と次ブロック

実マイク、実環境ノイズ、話者差、実際の発話に対する誤認識率は未検証。
ブラウザーの録音権限とMediaRecorderの実入力は利用者参加の段階で確認する。
今回のUI音声は制御した再生fixture、ライブAPI音声は架空の指示をTTS生成した制御入力である。
バックエンド/フロントエンド全件unit・全件E2Eは実行していない。

次は **フェーズ9、Astra / xhigh**。保存response比較、共通OCR送信予算、復旧/取消/expiry、
出力、必要な短いintegration、build、限定UIの受け入れ証拠をまとめる。
フェーズ10〜11はGPT-6.1-Sol / highで利用者の実マイク操作・調整・手順・最終報告。
実マイクが必要な時点で利用者を呼ぶ。このフェーズ8境界で停止する。
