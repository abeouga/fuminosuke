# フェーズ19：実マイク試験・切替準備（進行中）

2026-10-08更新：[追加フェーズ3の最終検証と数字列修正](voice-target-autocorrection-phase3-2026-10-08.md)を参照。
実マイクで数字表記の問題が判明し修正。00789の実保存を確認。聞き返し後に欄名だけを回答すると保存できない回帰を修正し、再起動を依頼中。API93/100、残り7、追加20回は許可待ち。全体フェーズ19・通常切替は未完了。

2026-10-07。通常切替と実音声の受け入れは未完了。全体フェーズ19は継続する。

## 欄名自動補正の追加実装

[追加フェーズ2レビュー](voice-target-autocorrection-phase2-2026-10-07.md)と必要な修正を完了。次は追加フェーズ3の全体検証・利用者参加マイク。

利用者の新しい指示により、近似一候補は自動補正して保存し、読み上げではなく画面で確認する方式へ変更した。
[追加フェーズ1](voice-target-autocorrection-phase1-2026-10-07.md)に実装と検証境界を記録した。
この追加変更だけをGPT-6.1-Sol xhigh実装 → Astra high独立レビュー → GPT-6.1-Sol high最終検証に分ける。
API63/100、残り37、今回追加0。以下の一候補質問・TTS試験は変更前の記録であり、新契約を優先する。
試験backendの再起動、実マイク、通常切替は今回実行していない。

## 19時台の再開・実マイク再試験

利用者が試験backendを再起動し、新PIDとhealth成功を確認した。
再試験では、無選択の連続音声から名号の値 `00123` を保存した成功receipt（revision 2→3）を確認した。
`00456` への連続訂正は未成立。利用者は欄名が英語や「目以後う」になり、連続発話で
session終了とstack traceが表示されると報告した。認識精度・echo・通知を受け入れ済みとは扱わない。

実通信ログは会話のDELETE後にresolveが409で終了したことを示す。
frontend会話controllerのcatchが、旧世代の失敗で停止済み/新会話のmessageを上書きしていた。
2条件を修正前に失敗として再現し、世代違いかつ保存開始前の旧失敗は表示を更新しないよう修正した。
保存開始後は成功receipt・表示復旧を保持する。3条件（停止、新会話、停止中の保存成功）を通過した。
registryによる正常なWebSocket終了後のtask取消しはtracebackへ流さず、無関係な取消しは伝播する。

STTには現在の所有catalogの欄名・aliasesだけを最大100語/計4000文字で送る。
OCRの現在値、帳票画像、全layout、全文会話はヒントに含めない。禁じられた改行/山括弧、長い語、重複を除く。
接続時と各発話開始時に現在の欄名を確認し、変更がある場合だけ同じ接続へ設定を更新する。
公式Realtime transcriptionのprompt/keywords/languages仕様を照合した。
ヒントは認識の補助であり、対象の一意性・保存権限・近似質問を代替しない。
短い思考の間で指示が分断される可能性に対し、無音の終了待ちを700msから1200msへ変更した。
認識の改善効果とこの待ち時間は、次の実マイクで確認する必要がある。

再開時の検証：frontend全97 tests、追加3条件、build、stream既存7条件、
欄名ヒント/所有/上限/正常終了4条件、コピー切替補助7条件が成功した。
streamのruffとmypyも成功した。全backendの旧失敗は今回再実行していない。
切替補助は稼働中port・PENDINGを変更前に拒否し、全DB/.env退避、旧session/challenge失効、
消費済み/再生済み履歴、既存業務table・855ファイル、新schema有効化を隔離コピーで検証した。
Windowsの長いcropパスによる検証補助のコピー失敗は短いDの保存先へ修正した。
正常切替自体は未実行。証拠は `cutover-preparation-results.json` とそのrun参照に保存する。
実試験コピーは保護OCR8ソース、画像/XLSX785ファイル、全74欄の物理対応と受付snapshotが一致した。
指示対象以外の73欄も同一。保存候補の `00123` だけが変更され、`live-preservation.json` に照合結果を保存した。

APIは再試験で追加20、累計63/100、残り37。内訳はWS接続1、音声7、Responses9、TTS3。
最初の失敗試行を含むphase19追加は32。通常31tableと855業務ファイルを保持し、PENDING=0。
実APIの自動再試行・払い戻しは行わない。次の試験は修正後の一指示から始め、発話をまとめて連投しない。
試験backendは再起動待ち。再起動scriptは時刻付きlogへ書き、既存の失敗証拠を上書きしない。

再開時証拠：`conversation-stop-before-2.log`/`conversation-stop-after.log`、
`transcription-hints.log`/`transcription-hints-results.json`、`stream-regression.log`、
`frontend-tests-resume.log`/`frontend-build-resume.log`、`mypy-streams-final.log`、
`live-receipts-progress.json`/`live-error-2.log`/`http-timings.jsonl`。
公式参照：https://developers.openai.com/api/docs/guides/realtime-transcription

## 実マイクで確認した事実

利用者が専用8019/5189を手動起動し、Chromeの実マイクから無選択で発話した。
実OpenAIのstreaming接続・PCM送信・completed文字起こし・Responses解釈は動作した。
「名号」が「名古」に認識され、利用者から近い欄名を具体的に聞き返す要求が追加された。
保存・通知・連続訂正の成功をこの試行で確認できたとは扱わない。

試験補助のSharedBudgetがHTTP予約時に正常DBのtransactionを二重に開き、TTS送信前に
`database is locked`になった。HTTP予約を正常台帳へ一度だけ委譲するよう補助を修正した。
製品ApiBudgetの不具合とは分類しない。失敗ログを保持し、自動再送・枠の払い戻しはしない。
音声取得は停止済み。再起動後の実TTS・echo・自然な訂正・局所録音・意味付けは未確認。

## 製品側の変更

- `assistant/target_resolution.py`：現在の所有catalog内の近い名称を最大5候補として返す。
  2文字名では同じ位置の文字を要求し、「名古 / 牛名」のような位置違いの共有文字を候補根拠にしない。
  名前の類似度は質問用であり、保存の権限や一意性には使用しない。局所録音の候補は選択内に限定する。
- `assistant/conversation.py`：検索が0件なら近い候補を質問する。一つなら「名号の欄ですか」、
  複数なら欄名・位置・見出し・行を質問する。モデルが近い名称を直接選んだ場合にも質問を挟む。
  一候補の直前の質問、同session・同resource/revision/選択/手動世代、60秒内の回答だけを元指示に結び付ける。
  「はい」「うん」「そうです」等はこの対象質問への回答として扱う。質問なし・複数候補の肯定は保存しない。
  明確な変更指示を毎回承認させる方式は導入しない。
- `assistant/conversation_provider.py`：認識された欄名を勝手に置換せず、その文字列で検索する指示を追加。
- `api/editing_cutover.py` と3 routeファイル：新API有効時だけ旧writeを410
  `EDITING_CONTRACT_REPLACED`で拒否。旧assistantの14 write、一括authoring、単一OCR corrections、
  旧template-definitionの生成/一括保存/適用の計19 routeが対象。
  新API無効時の旧挙動、旧GET履歴/receipt、confirm/reopen、現在のlayout検出/印字認識/検証・印刷経路を維持する。

名称の近似は現在の名称・印字・aliasesの文字列に基づく。発音辞書によるあらゆる同音異字の訂正を保証しない。
受付済みOCR、mapping/物理領域、保存先、crop、OCR provider/prompt/schemaを変更していない。

## 検証

専用コピーDBで、旧write19 routeの無変更拒否、新API無効時の旧route、旧読取、確定/再編集の維持、
新command保存・receipt・再取得を確認（6条件）。近似質問・一候補回答・裸の肯定・モデルの直接推測・
無関係名・複数候補の肯定拒否を確認（7条件）。従来の会話22条件も通過。
3 scriptのmigration共通条件を重複除外すると33条件。途中の検証補助の誤りと再実行ログも保持する。
対象ruff（6ファイル）、mypy（4ファイル）、`git diff --check`は通過。
templates.py全体のruffには開始時からのE501が残り、全ファイル静的検査成功とは扱わない。
frontend変更はなく、フェーズ18のbuild/97 testsを今回の再実行と偽らない。
backend全件の旧仕様23失敗・34収集エラーは未整備のまま。今回全件を繰り返していない。

通常DBの全31テーブルはAPI台帳への追加予約以外、開始時内容と一致。
31件の既存予約・ordinalと855業務ファイルを保持。フェーズ12の保護OCR8ソースもSHA一致。
既存OCR contractを維持した。通常DBは`assistant_voice_v1`、新API無効、通常環境への試験書込はない。

APIは追加12枠、累計43/100、残り57。追加分はWS試行2・発話4・Responses HTTP6。
TTSの今回追加送信は0。実際の秒数や性能の受け入れ結果としてこの失敗試行を使用しない。
音声/STT全文を保存せず、HTTP診断はpath・状態・duration・使用回数のみ。

## 起動と残作業

証拠・補助は `D:\llm-ocr-archive\voice-assistant\20261007-phase19`。
開始時は`baseline.json`/`before.db`/`before`/`data-before`、API・保持は`normal-progress.json`、
検証は`cutover-results.json`/`nearby-results.json`/各log、実試験は`live-fixture.json`/`http-timings.jsonl`/`live-error.log`。

agentによる専用serverの起動が自動承認レビューに`blocked by policy`で拒否された。
利用者が`start-runtime.ps1`を実行して起動した。現在の再起動も利用者へ依頼済み。
`restart-backend.ps1`は所有commandを検査して8019の専用backendだけを再起動する。
新しい試験コピーを作らず、既存の変更と音声leaseを保持する。通常8010/5180を停止しない。

`prepare-normal-cutover.py`はDの一回限り移行補助。現時点の実行はread-onlyで`applied=false`。
`--apply`は実マイク受け入れ後だけ使用する。正常port停止・PENDING=0を要求し、DB/.envを退避、
新schemaを移行、旧session/proposal/challengeを失効、業務table不変を検査して新APIを有効化する。
コピーの業務データを通常DBへ戻さない。起動・実画面の手動/音声保存・receipt再読込、通常入口の停止/再起動を検証してから完了とする。
