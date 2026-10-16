# 文之助：回帰検証と音声ライフサイクル修正（フェーズ18）

2026-10-07 / 対象 `C:\Users\hanam\Desktop\llm-ocr-deploy` / Astra・xhighの作業ブロック

フェーズ13〜17の保存・会話・音声接続を現行コードで検証した。開始・停止の競合を修正し、重点受け入れ条件を確認した。ただし、backend全件テストは成功していない。旧仕様の残存テストによる23失敗と34収集エラーが残る。全件成功や通常利用への切替完了とは報告しない。

通常DBは未移行、新編集APIは通常設定では無効のまま。実マイク、実OpenAI streaming、音声品質・応答時間、通常環境の切替は実施していない。次のフェーズ19はGPT-6.1-Sol / highとし、利用者参加の実マイク試験前に呼びかける。

## 修正した不具合

変更した製品ファイルは `frontend/src/assistant/ConversationAudio.ts` と `frontend/src/assistant/conversation.ts` の2件。backend、DB schema、依存パッケージ、OCR計算処理は変更していない。

開始処理が非同期の停止処理を待った後で世代番号を読んでいたため、後から押された停止や別の開始を新しい世代と誤認していた。また、古い終了処理・認識応答が現在の録音状態を変更できた。

| 再現した失敗条件 | 修正後の条件 |
| --- | --- |
| 局所録音開始の直後に停止すると、停止後に録音が始まる | 待機前に世代を固定し、古い開始はマイク取得へ進まない |
| 開始処理が重なるとlive trackが2本残る | 最新の開始だけが録音を所有する |
| 録音終了から送信への移行中に停止するとPromiseが未完了になる | stopイベントの完了通知を解除せず、古い処理は送信せず終了する |
| 遅いVAD廃棄を待つ旧エラー処理が新録音を停止表示にする | 終了表示も元の世代に限定する |
| 古い発話終了の失敗が新録音を止める | 古い終了応答を現在の取得へ適用しない |
| 古いVAD誤検出取消の失敗が新録音を止める | 取消応答も元の世代に限定する |
| 旧訂正の解析が終了後に新WSへ制御を送る | 非同期の各境界で世代を再照合する |
| 古いセル選択が新録音へ対象を設定する | 選択解決の各待機後に録音世代を照合し、新しい対象を変更しない |

上記8条件の失敗記録を保存した。さらに連続取得開始直後の停止、遅いVAD廃棄と新session、会話開始途中の停止を合わせ、11条件が修正後に成功した。マイクはChromeの模擬デバイス、制御応答はfixture。実マイク品質の証拠ではない。

## 検証結果

全成果物は `D:\llm-ocr-archive\voice-assistant\20261007-phase18`。使い捨て検証コードを製品内へ追加していない。

| 検証 | コマンド・証拠と結果 |
| --- | --- |
| frontend全件 | frontendで `npm test -- --reporter=default`。17ファイル・97件成功。修正後にも実行 |
| frontend build | frontendで `npm run build`。TypeScriptとVite成功、固定voice assets生成 |
| backend全件 | backendで `.venv/Scripts/python.exe -m pytest --continue-on-collection-errors --basetemp D:/llm-ocr-archive/voice-assistant/20261007-phase18/pytest-full-01 --junitxml=D:/llm-ocr-archive/voice-assistant/20261007-phase18/backend-full.xml`。1,260成功・24失敗・34収集エラー・既存の外部API opt-in skip 1。約299秒 |
| renderer失敗の切分け | freshなDドライブTEMPで同じ `tests/integration/test_renderer_cleanup.py` を単独実行。5件成功。共有TEMPでの1失敗と区別する |
| コピーDB/API | 手動14、会話22、stream 8、意味8、graph inverse 7、guard 4、atomic 18、ページ6、出力6。共通migration確認の重複を含む93件、重複除外85条件成功 |
| 実Chrome・レビュー | `node .../verify_browser.mjs`。11条件成功。局所MediaRecorder、実decoder、実VAD/ONNX初期化、連続指示順序、取消/undo、手動との競合、保存済み表示復旧、TTS中断 |
| 実Chrome・テンプレート | `node .../verify_template_ui.mjs`。無選択から意味作成・接続・通常文字読取を保存し、同じ物理セルを強調。承認ボタンなし |
| ライフサイクル故障 | `node .../verify_audio_lifecycle.mjs`。11条件成功。再現前後のJSONとログを保存 |
| OCR保存応答 | `.venv/Scripts/python.exe .../verify_ocr_replay.py`。72欄、290画像SHA、保存実応答5batch×正常/association不一致/欠落/重複/不明IDの25条件。phase12とrequest・manifest・association・展開結果が一致 |
| 実XLSX | `.venv/Scripts/python.exe .../verify_output.py`。確定済み出力、reopen、共通補正receiptの再送、確定、再取得、再編集を実DB/APIで確認。出力全セルをexcel-tableと照合し00123を文字列で保持 |
| 静的検査 | `.venv/Scripts/python.exe -m ruff check app/editing app/assistant app/api/v1/routes/editing.py app/api/v1/routes/editing_voice.py app/ai/api_budget.py app/core/lifecycle.py` 成功。mypyの同範囲（lifecycleを除く）を `--follow-imports=silent --no-incremental --cache-dir D:/llm-ocr-archive/voice-assistant/20261007-phase18/mypy-cache` で実行、38ファイル成功 |
| 保持照合 | `audit_phase18.py`。通常DB31テーブル・855ファイル一致。11コピーでも受付済み6jobのsnapshot、既存page/input、物理ID/crop、元台帳31件を照合 |
| 差分 | `git diff --check`成功。開始時1,001ファイルとの差分を `phase18-only.diff` に保存。staging/commit/pushなし |

OCR provider比較のphase12実体はphase13開始時の退避ファイルを使い、そのSHAがphase12開始時台帳と一致することを先に確認した。再計算と保存済み値の間にある日付1欄のnormalized差は、10月4日に明示した汎用化仕様変更と同じであり、今回の差ではない。保存済みの値を書き換えていない。

XLSX検証では、コピー内の対象出力キャッシュが既存export処理によって再生成される。コピーの全ファイルがバイト不変だったとは扱わない。対象2コピーの生成キャッシュを個別記録し、元XLSX、アップロード、crop画像、通常環境の855ファイルとは分離して照合した。

## backend全件検証の未完了部分

`backend-full.log` / `backend-full.xml` / `regression-triage.json` に失敗を保持した。テスト削除、skip追加、assertionの弱体化は行っていない。24失敗のテスト本文はすべてphase12開始時と同一だった。

| 分類 | 内容 |
| --- | --- |
| 収集エラー34 | 廃止済みテスト内のhelperを33件が参照。1件は廃止済みExportConfigをimport |
| 旧API・旧保存境界 | reprocessの必須revision欠落、廃止したLayoutService.save、種雄牛辞書API、CSV `_csv_safe` |
| 旧schema fixture | document.cattleName等の旧target、mother_individual_id、旧mark fixture。現行のfields契約へ未更新 |
| 旧migration | 汎用化時に退役した0031〜0033を起点として要求 |
| TEMP依存1 | renderer失敗試験で開始時の共有TEMP集合が変化。独立TEMPでは同じ5件成功。一般環境で常に再現しないことまでは証明していない |

23失敗と34収集エラーの整備は残る。今回の音声修正で発生した回帰ではないが、未実行のテスト領域を成功と数えない。renderer環境再認定・全paged実OCRの代替証拠として、今回の成功件数を使用しない。現在の音声・保存境界は上記の現行契約による専用検証で評価した。

## 受け入れmatrixとの対応

| 条件 | 現在の評価 |
| --- | --- |
| A01/A02/A04/A05/A10/A16 | 制御応答/制御音声で通過。自然な発話、認識表記差、反響・雑音、実機遅延はphase19 |
| A03/A06〜A09/A11〜A15/A17/A18/A20 | コピーDB/API、実UI、保存応答で通過。ページについては実XLSXからの2ページ定義と制約検査。新たな実OCR送信やrenderer資格発行はしない |
| A19 | 通常切替後の旧write拒否はphase19に残る。現在の旧authoring/correction/assistant writeが既に閉鎖されたとは扱わない |

phase19では旧音声session・proposal/challenge・未処理receiptを整理し、旧authoring/correction/assistant writeの拒否を実装・検証してから、手動と音声を同じ契約へ切り替える。confirm/reopen/print編集など必要な既存業務操作まで一括拒否しない。未保存入力、成功履歴、receipt読取、通常の手動操作、再読込を確認する。旧DBの単純な上書き復元で切替後の保存を消さない。

## 実行中に生じた検証用の問題

- phase12退避にprovider実体がないことを確認し、phase13退避実体とphase12 SHAを照合して比較した。比較基準を現行出力へ差し替えていない。
- 出力検証の初回payloadを現行ManualOcrEditのdraft/origin/turnIdへ合わせた。製品のvalidationを緩めていない。
- Viteの更新後、componentが使用するtimestamp付きモジュールと直接importしたモジュールが別になり、試験側の音声callback取得が失敗した。component自身が使うモジュールを捕捉して再検証した。
- 同じコピーを再利用した試験では、以前の成功が残した名称・意味接続を前提にできない。明示した手動APIで試験対象セルを準備し、異なる意味名を使った。最終結果は `browser-final-4.log` と `template-ui-final-5.log`。
- ブラウザー再実行の1回で開始時にFailed to fetchが表示され、音声controlsが表示されなかった。原因は確定できていない。以後の同条件再実行は成功。`browser-final-2.log` と失敗画面を残し、単発成功で一般的な通信耐性を保証しない。

## 実環境・API予算・引継ぎ

実外部AI API追加0回、累計31/100回、残り69回。コピー内の模擬予約を実消費へ加算しない。通常DBの全行、候補/補正/確定snapshot、画像・業務ファイルを保持した。provider/prompt/model/schema、request/association、物理領域と出力対応の主要8ファイルはphase12開始時と同一で、保存応答と実出力も照合した。**既存OCR contractを維持した。**

通常環境の移行・再起動は実行していない。検証用frontendの停止・再起動は自動承認レビューから `blocked by policy` で拒否され、理由の詳細は返されなかった。別手段でプロセスを強制終了していない。最終記録時の検証サーバーはfrontend 5187（PID18588）、backend 8017（PID28116、起動元PID10340）。全検証Chromeは終了し、マイク/WSは停止済み。backendは専用コピーと制御providerであり、通常環境の8010/5180と混同しない。`runtime-processes.json` と最終待受照会を引継ぎに使う。

このブロックで停止する。次はモデル切替後のフェーズ19。上記の全件テスト未整備、原因未特定の単発取得失敗、旧write閉鎖、実マイク/実STT/TTS、応答時間計測、起動停止と通常切替を明示した状態で進める。
