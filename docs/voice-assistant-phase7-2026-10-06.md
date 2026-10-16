# 文之助 永続undo・操作復旧の実装記録

2026年10月5日〜6日、[設計書](voice-assistant-design-2026-10-05.md)のフェーズ7をdeployコピーへ実装した。対象は `C:\Users\hanam\Desktop\llm-ocr-deploy`。元の `llm-ocr` は編集していない。既存の未コミット変更を保持し、commit・pushは行っていない。外部AI APIは今回0回、累計0／100回。

## 保存と取り消し

直前のアシスタント操作を、元の操作履歴を残して新しいrevisionで取り消す。対象は従来の4操作。undoのundo、複数段の巻き戻し、他の変更をまたぐ取り消しは扱わない。

- `GET /assistant/history` はsessionのresourceに属する直近20件と取り消し可否を返す。履歴はresource所有とし、新しい画面sessionからも取得できる。これはloopbackの単一利用者アプリであり、複数利用者向けACLではない。
- `POST /assistant/operations/{id}/undo-proposal` はサーバーのinverse payload、元のbefore/after、提案履歴から差分を計算する。クライアントから復元snapshotを受理しない。
- undoも既存のpresented・challenge・120秒期限・context世代・apply・Idempotency-Key経路を通す。保存transaction内でも再計算する。設計書の直接undo API案は、この二段階の取り消し案表示・承認へ具体化した。
- 現在revisionと元操作のafterRevision、現在値と保存後snapshot、編集可能状態、元の所有対象を照合する。手動保存・確定・再編集・公開・後続操作が挟まれば、値が同じでも拒否する。
- OCRは対象のFieldReadingを候補・判定・理由まで復元する。他欄・確認メモ・crop・受付snapshotを保持し、既存補正サービスで派生値を計算する。
- templateは対象セルの名称・分類・選択肢を復元する。未指定fieldLabelは未指定へ戻る。共通authoringでmappingや識別表示名を計算し、過去の検証済み状態をそのまま復活させない。ページ所有・geometry検証を維持する。
- 元操作と逆操作は `undo_of_id` の一意制約でも結ぶ。補正、revision、履歴、提案消費、成功receiptは同時commitする。成功済みの同一要求は、期限・現在revisionより先に再生する。

DBの既存 `resource_operations`・`mutation_requests`・`assistant_proposals` を使用する。今回の新規テーブル・migration・既存データ一括変換はない。headは `assistant_proposals_v1` のまま。

## 再読み込みと別タブ

新規提案sessionは画面ごとに分離する。未承認案・challengeを画面に復元しない。承認して送信する要求だけ、送信前にresource別のlocalStorage journalへ保存する。記録内容は元session token・提案・challenge・同じ要求キーで、Web Locksにより同一originのタブ間で送信・回収を直列化する。保存機能が使えない場合は新規要求を送信しない。

復旧では元sessionでreceiptを照会する。成功なら保存済み結果を取得し、確定失敗なら理由を表示する。未受付・PENDINGは未保存の断定に使わず、元の承認済み要求を同じキー・同じpayloadで再送する。期限やrevisionの条件を満たさない未完了要求は失敗する。新しいキーで自動再実行しない。

正本の表示更新が終わるまでjournalを保持する。完了時にはtoken・challenge・要求payloadを除去し、他タブへの通知に必要な成功receiptだけを残す。履歴の正本はDBである。保存済みでも表示取得に失敗した場合、保存を失敗扱いに戻さない。

storage通知または画面開始時に未解決要求を検出すると復旧パネルを開き、既存の保存操作を停止する。別タブ・復元下書きに未保存入力がある場合、receipt照会だけでその入力を上書きしない。「未保存入力を破棄して保存結果を表示」を明示選択した場合だけ更新する。OCRの入力途中の文字列も、lock時のblurで消去しない。templateは保存正本を専用のeditor境界で受理し、選択中セルを維持する。

ブラウザー保存データを消した場合や別origin・別ブラウザープロファイルでは、送信中の要求をブラウザー側から特定できない。DB履歴は残る。Web LocksとlocalStorageの利用を必要とし、制御不能な環境へ無条件に縮退しない。

## 変更境界

- `backend/app/assistant/undo.py`、contracts、service、assistant route：履歴取得と逆操作の提案・再検証・保存。
- `frontend/src/assistant/recoveryJournal.ts`、serverPreviewProvider、AssistantPanel、contracts：送信前記録、別タブ通知、照会・同一要求再送、履歴・取り消し表示。
- ReviewPage・OcrReviewResults：復旧と未保存入力の保護、入力途中のblur抑止。
- TemplatePage・LayoutEditor：復旧と明示破棄、保存正本の受理、選択維持。
- AGENTS・Safety Contract・Artifact Storage Policy・本記録：現在の契約と次フェーズ。

OCR provider・認識prompt・認識schema・association処理・crop生成・受付snapshot・出力adapterは変更していない。既存OCR contractの対応関係を維持した。保存済み操作を新revisionで取り消す契約とブラウザー復旧契約を追加した。

## 検証

証拠・使い捨てfixtureは `D:\llm-ocr-archive\voice-assistant\20261005-phase7`。開始前631ソース・文書ファイルと差分、DB online backup、855データファイルのコピー・SHAを保存した。

| 検証 | 結果 |
| --- | --- |
| 短いAPI/DB integration | 11シナリオ通過。実service・保存データのSQLiteコピーを使用し、worker/providerは起動しない |
| template逆操作 | input表示名・label名称・checklist名称/値・分類を元に戻し、全セル・mapping・他欄・geometryを比較。新sessionからの履歴取得とundoを確認 |
| OCR逆操作 | 不確実な値・alternatives・reviewReason・他欄・reviewItems・fieldPreviewsが復元前と一致 |
| 二重送信・競合 | 同一キー並行は同じreceipt。別session・別キーで同じundoを競合させ、1回だけcommit。手動同値保存、確定、reopen後は古いundoを拒否 |
| 障害と再送 | 業務flush後の例外で全業務更新rollback、PENDINGから同じキーで復旧。成功後の期限切れ再送も同じreceipt |
| 出力 | 確定後の実XLSX B4が文字列 `00300`。未確定値や別欄を出力先へ付け替えない |
| 実画面 | 8011/5181の隔離通常アプリでOCR保存→reload→履歴→undo、template保存→undo、commit後503の故障注入→別タブ回収→元タブ更新、別タブの未保存書類名保持→明示破棄を確認 |
| 静的検査・build | assistant/routeのruff、mypy 7ファイル、frontend `npm run build` が成功 |

主な成果物は `integration-results.json`、`integration-final.log`、`ui-results.json`、`phase7-result.xlsx`、画面PNG、build/ruff/mypyログ。故障注入のRuntimeErrorは期待したrollback検証。最初の分類fixture選択では既存セルにignoredがなくStopIterationとなったため、依存のない既存label→ignored→undoへ変更した。製品の拒否条件を緩めていない。

結果不明中の `tab.reload()` はブラウザーの離脱保護により実行されなかったため、そのケースの証拠は新しいタブからの復旧である。通常の保存後reloadと履歴取得は実測した。未保存入力を持つtemplate・マスク・印刷の全組合せ、ブラウザー保存容量不足、全件unit/E2E、実マイク、音声API、認識精度は未検証。フェーズ9の重点検証へ残す。

## 通常起動と最終差分

通常入口 `sysover-ray/Start-LlmOcr.ps1 -NoBrowser` から再起動済み。frontend 5180/backend 8010、health、9つのassistant API、既存OCR/authoringのGETを確認した。実データは開始時から29テーブルの全行・全列、855ファイルのSHAが一致し、再起動前後も29テーブルが一致した。SQLite integrityはok、foreign keyエラー0件。実データで検証用操作やOCR送信は行っていない。

証拠は `data-preservation.json`、`runtime-results.json`、`runtime-processes.json`。launcher PID 30068、backend leaf PID 27656、frontend PID 6064。隔離検証の8011/5181と作成タブは終了した。

フェーズ開始時との比較は16ファイル（追加3・変更13・削除0）。`phase7.diff` と `phase7-files.json` に保存し、`git diff --check` と追加行の空白検査を通過した。既存の未コミット差分を今回の変更件数に混ぜていない。

## 次のブロック

フェーズ7の境界で停止する。次は **GPT-6.1-Sol / high、フェーズ8**。STT・提案API・TTS・録音管理・累計100回の予算ledgerを接続する。実マイク試験が必要になった時点で利用者の参加を求める。全体の重点検証はフェーズ9、実マイクを含む調整・最終文書はフェーズ10〜11。
