# 文之助 重点受け入れ検証（フェーズ9）

対象は `C:\Users\hanam\Desktop\llm-ocr-deploy`。担当ブロックはAstra / xhigh。
フェーズ9を完了し、GPT-6.1-Sol / highのフェーズ10〜11へ引き継ぐ。
元の `llm-ocr` は編集していない。既存未コミット差分を保持し、stage・commit・pushは行っていない。

## 修正した不具合

共通API予算が100回に達した際、送信前hookのAppErrorをOpenAI SDK 1.97.1が
APIConnectionErrorへ変換していた。実送信は止まるが、OCRは接続障害として再試行し、
文書別の追加予約を消費した。音声でも競合による上限到達が一般的な接続失敗になった。

- `app/ai/api_budget.py` に、予算超過と送信先拒否だけを元の業務エラーへ戻す共通処理を追加。
- `app/ocr/openai_provider.py` の送信と `app/assistant/voice_provider.py` のクライアント境界へ適用。
- 予算超過は429で終了し、OCRの接続失敗retryへ進まない。通常の通信エラーは従来処理を維持。
- 予約の払い戻し、SDK retry、予算増額、認識model/prompt/schemaの変更は行っていない。

残り1回で500応答を注入した再現では、実transport到達は変更前後とも1回。
変更前はOCR_PROVIDER_ERROR/502で文書予約3回、変更後はAI_API_BUDGET_EXHAUSTED/429で
文書予約2回（実送信失敗1回と、共通予算で拒否された予約1回）。追加retryの予約を防止した。
音声の事前確認と送信の間で別処理が最後の枠を消費する競合も、予算超過として停止した。

## 現在コードでの検証

成果物は `D:\llm-ocr-archive\voice-assistant\20261006-phase9`。
開始時の781ソース/文書・DB・855データファイルと、今回の差分を保存した。
外部providerだけを制御し、業務保存には実API処理と隔離SQLiteコピーを使用した。

| 検証 | 結果・証拠 |
| --- | --- |
| 提案・承認・4操作 | `verify_assistant.py` / 同名log、16シナリオ。所有者・ID・challenge・revision・依存分類の拒否、fieldLabelとpackage/fork、保存文字列・空欄・crop・他欄保持、同一要求再送、rollbackとXLSX |
| 永続undo | `verify_undo.py` / 同名log、11シナリオ。4操作の正確な復元、別sessionからの履歴、並行要求、手動保存・confirm/reopen後の拒否、失敗後同一キーの復旧 |
| 音声の基本境界 | `verify_voice.py` / 同名log、10シナリオ。提案のみでは保存ゼロ、重複音声、別内容、無音/長さ/形式/容量、読み上げ完了、別turnの固定句承認、取消・context失効、保存実WAVとWebM、予算100回境界 |
| 音声の故障境界 | `verify_voice_faults.py` / `voice-faults.log`、8シナリオ。別owner/指示turn/取消済み/期限切れの承認拒否、challenge更新、適用中の承認消費を含むrollback、音声undo専用句、上流403/429、receipt TTS timeout、録音/案の失効、遅延TTSの取消 |
| SDKと共通予算 | `verify_budget_boundary.py`、`budget-before.log`、`budget-after.log`。上記不具合の修正前失敗・修正後成功。模擬transport以外へ送信しない |
| SDK正常経路 | `verify_sdk_success.py` / `sdk-success-results.json`。実SDKによるSTT multipart、strict Responses parse、保存実WAVのTTS応答処理、各送信前の予約。3回の模擬要求、実API0回 |
| 保存OCR応答の比較 | `verify_ocr_replay.py` / `pipeline-replay.json`。72欄・290画像SHA、保存済み実5 callを正常/association不一致/欠落/重複/不明IDで再生。計25ケースの送信引数・manifest・展開後の値/判定/対応が今回の変更前後で一致 |
| XLSX | 提案/undoの検証内で、確定済み出力B4が先頭ゼロ付き文字列であることを確認。未確定・旧revision・confirm/reopenの境界を維持 |
| ブラウザー | `ui_probe_app.py`、`recovered-after-reload.png`、`undo-after-reload.png`。マイク許可拒否、中止後の遅延stream停止、文章指示→提案、commit後503、再読込→元要求照会→復旧→永続undo |
| 実データ保持 | `verify_preservation.py` / `preservation-results.json`。実DB全31テーブルの全行・全列一致、855データファイルSHA一致、FK違反0。UIコピーはrevision 1→2→3、適用とundoだけの2履歴で原候補・補正文書を完全復元 |
| 静的確認 | 変更3ファイルのmypy成功。共通AI/音声providerのruff成功。OCR providerには開始前から同じE501が3件あり、追加指摘0。`ruff-baseline-comparison.json` |
| build・差分 | frontend `npm run build`（tsc/Vite）成功。`audit-results.json` / `phase9.diff`で今回範囲と `git diff --check` を確認 |
| 通常起動 | 通常の停止/起動入口から再起動。8010 health、5180、17 assistant path、音声設定、API予算13回、全31テーブル保持を確認。`runtime-results.json`。検証用8012/5182と作成タブは終了 |

ブラウザーの通常再読込はbeforeunloadによって保護されることを観測した。
検証環境がそのダイアログを自動取消するため、検証タブの離脱確認listenerだけを一時除去して
強制再読込を再現した。保存journalやreceiptは変更していない。再読込で本来のlistenerが再登録され、
タブ終了後に障害注入は残らない。実マイクは取得せず、許可拒否/遅延streamはブラウザーfixtureを使用した。

保存候補と再計算結果を比較する72欄のうち、1欄には既知の汎用変換時の日付正規化差がある。
過去の保存normalized値を保持し、新規計算は原文の日付をそのまま使うという既存仕様である。
今回の変更前後のprovider比較25ケースには差がない。この1欄を今回の認識改善・回帰とは扱わない。

再実行はdeploy rootから `backend/.venv/Scripts/python.exe` に上記Dドライブの各scriptを渡す。
各scriptは新規検証領域を作り、実業務DBへ補正を書かない。模擬予算用コピーを実DBへ戻さない。
frontend buildはdeployのfrontend、mypy/ruffはbackendで実行した。全件unit・全件E2Eは実行していない。

## 変更境界と残る確認

製品コード変更は共通予算のエラー伝達3ファイル。フロントエンド、DB schema/migration、
補正・undo transaction、template authoring、paged/rendererのgateは変更していない。
mappingId・targetPath・sourceRegionId・boundingBox・crop、受付snapshot、provider association、
OCR prompt/model/schema、出力セル対応を維持し、保存応答比較とXLSX照合で確認した。
既存OCR contractを維持した。実画像の認識精度や、全paged/rendererの実環境認定を新たに証明したものではない。

実APIの追加利用は **0回**。累計 **13／100回、残り87回**。
実DB ledgerを正本とし、SDK fixtureの予約やコピー内の100回試験は実消費へ加算しない。

実マイク、話者差、雑音、実際の発話の認識品質は未検証。
次の **フェーズ10〜11、GPT-6.1-Sol / high** で利用者に参加を求め、OCRとテンプレートの
「選択→発話→案の確認→承認→保存→取消」を確認する。そこで見つかった問題を調整し、
利用手順と最終報告をまとめる。このフェーズ9のモデル境界で停止する。
