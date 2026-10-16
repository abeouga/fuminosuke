# AIテンプレート作成 Phase29 独立審査

2026-10-12。**差戻しです。既知のT2時間超過に加え、送信処理に3件の欠落を再現しました。Phase30の実API比較へ進めません。** 今回は審査と再現のみで、修正実装は行っていません。

対象は`D:/llm-ocr-archive/template-ai/20261012-phase28`のPhase28凍結実装です。要求された審査モデルはAstra / XHigh、実際のチャットモデルの確認はしていません。[Phase28報告](template-ai-phase28-2026-10-12.md)、[Phase27設計](contracts/template-analysis-v8/README.md)、[審査JSON](contracts/template-analysis-v8/phase29-review.json)を併用します。source lock SHAは`5006ff34c7b555daf930d109973ce24c223a8ccedcf78f8fa7205d4e0ad20e45`です。

## 差戻し事項

### R1・P1：同じrunの同時実行で二回送信する

[run_transport.py:114](D:/llm-ocr-archive/template-ai/20261012-phase28/run_transport.py:114)は、開始時に`closed`だけを検査します。実行中を示す状態を原子的に取得せず、`closed`は終了時に設定されるため、最初の実行がまだ完了していない間に同じrunを再実行できます。

既存の永続化callbackで一回目を待機させ、別threadから同じ`Run.execute()`を呼びました。**台帳予約2・mock送信2**を再現し、二回目は`status=draft, sends=2`を返しました。一回目は共有guardが閉じられて`SYNCHRONOUS_SEGMENT_REQUIRED`で失敗しました。コードやguardのmonkeypatchは使用していません。

一run最大一送信に違反しています。通常DBへの二重適用は今回実行していません。修正では、実行開始を一度だけ排他的に取得し、重複呼出しをguard/台帳へ触れる前に拒否してください。送信済み回数の防御も一runの状態として保持する必要があります。

### R2・P2：キャンセル後に未送信だったHTTP要求が送られる

[run_transport.py:61](D:/llm-ocr-archive/template-ai/20261012-phase28/run_transport.py:61)のworkerは`cancelled`を受け取らず、外側の待機loopだけがキャンセルを確認します。client初期化後の検査も期限だけです。

client初期化を待機させ、その間にキャンセルしました。呼出し元が`RUN_CANCELLED`で終了した時点のHTTP要求数は0でしたが、初期化を解放すると**キャンセル後にmock HTTP要求が1件発生**しました。HTTPX MockTransportを用い、外部通信は行っていません。

キャンセル済みの不要な呼出しが予算を消費する可能性があります。既に通信中の結果を捨てる処理とは別に、初期化・接続待機後のworkerで、実送信直前にもキャンセルを検査する必要があります。

### R3・P2：予算予約待機中の対象変更後も古い要求を送る

[run_transport.py:136](D:/llm-ocr-archive/template-ai/20261012-phase28/run_transport.py:136)は、現在性を検査してguardを閉じた後で台帳を予約します。予約後の`check()`が確認するのはキャンセルと期限だけです。

`ledger.reserve()`の間にinteraction epochを変更すると、**予約1・mock送信1**が発生しました。その後は`CURRENT_BINDING_STALE`となり、古いdraftの返却は防止されています。したがって、保存破壊の再現ではなく、失効した要求の送信と待ち時間・予算消費の問題です。

台帳予約完了後から実送信までの待機境界でも、owner/scope/revision/epochを検査してください。予約済み枠の払戻しは導入せず、未送信要求を止める必要があります。

## 既知の時間未達

Phase28の保存値から、T2中央値625.3379msを再計算し、報告値が正しいことを確認しました。同一内容のコピーを新規processで3反復した今回のT2中央値は**634.55ms**でした。500ms条件は引き続き不合格です。標本と実行時点が異なるため、約9msの差を性能悪化の証拠とは扱いません。

| scope | 審査時中央値ms | 500ms条件 |
| --- | ---: | --- |
| T1 | 288.00 | 合格 |
| T2 | 634.55 | 不合格 |
| T3 | 217.49 | 合格 |
| H1 | 220.09 | 合格 |
| H2 | 205.11 | 合格 |

この時計は準備・新pack/view/schema・wire・検査/compileのローカル測定です。評価用oracle計算も含み、通信・台帳commit・証拠永続化は除外しています。実際の`scopeReadyMs`やproviderの処理時間ではありません。T2の未達を解消する際も、現行の対象・検査・分母を測定から外して合格にしてはいけません。

## 再確認できた部分

- 開始時にPhase28の634成果物を照合。実装399ファイルを同内容で審査ディレクトリへ複製し、source lockの絶対パスだけを複製先へ変更しました。元の実装・fixture・gold・保存応答は編集していません。
- 元の83＋48＋70＝201境界は同じコピーで通過。独立審査の追加3ケースがその既存集合で未確認だったため、既存集合の通過と今回の差戻しは両立します。
- 旧30応答は26受理/4拒否、Phase25の10応答は9受理/1同一拒否。旧採点・物理対応・manual投影・既存graphのno-opを再現しました。
- 合成24scopeの入力、画像、scope、intent、期待DTO/投影のhashを照合し、保存期待DTOから投影を再生しました。期待値は再生成・差替えしていません。校正12と評価12の宣言familyは重複していません。
- 評価E11は候補が成立せずローカル保留となるため、現在の入力では送信0です。将来の42送信は上限として扱い、回数を埋めるためのAPI呼出しを行わないでください。
- aliasの構造別所属、全claims、必須名称nullによる保留、元crop保持、元PNG対照、codec失敗時の元PNG、応答後の現在性拒否は、再実行した既存境界で確認できました。

合成期待DTOの再生には同じ共通compilerを使用しています。その結果だけを意味の正解やcompiler自体の独立検証とは扱いません。実モデル精度、実endpointのschema受理、実通信の接続再利用、製品UI・保存・マイクは未検証です。

## 公式仕様と検証範囲

OpenAI Docsを2026-10-12に照合しました。独立した有限質問をDecisionsへまとめ、任意のschemaに従う回答をResponsesへ送る設計方針には、この範囲で差戻し指摘はありません。[Decisions公式ガイド](https://developers.openai.com/api/docs/guides/decisions)

root object・必須property・追加property禁止と入れ子のanyOfは公式のStructured Outputs方針に対応します。ただしローカルschema検証は、実endpoint受理を証明しません。[Structured Outputs公式ガイド](https://developers.openai.com/api/docs/guides/structured-outputs?api-mode=responses)

確認はdeployのPythonを`-B`付きで使用し、`run_review_checks.py`、`review_probe.py`、`dataset_review.py`で実施しました。外部socketを禁止し、全件の製品unit/E2Eは行っていません。再現条件・結果は`D:/llm-ocr-archive/template-ai/20261012-phase29`の`independent-probes.json`、`verification.json`、`dataset-review.json`、`verification-logs/`に保存しています。

## 変更・保全・次工程

変更した製品側ファイルはこの審査報告、審査JSON、現在地を示すAGENTS/Safety Contract/v7・v8 READMEの追記です。実装の修正、製品接続、通常データ移行は行っていません。両checkout・DB・設定・旧証拠・台帳の比較は`preservation.json`、差分検証は`diff-check.log`へ保存します。製品のOCR/補正/保存/出力のコードとデータを変更せず、既存OCR contractを維持しました。

今回API0、累計376/496、残120です。次工程はR1/R2/R3とT2時間未達をまとめた修正設計、その設計に基づく実装、独立再審査です。Phase30の実API比較と製品採用は保留します。工程は次の指示まで開始しません。
