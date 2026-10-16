# 音声・Decisions再設計 フェーズ7：実マイク受入と修正

対象は `C:/Users/hanam/Desktop/llm-ocr-deploy`。**フェーズ7は進行中で、通常環境へは切り替えていない。**
試験専用コピーを8027／5197で起動し、利用者の実マイクで不具合を確認した。
通常editing無効・通常DB未移行・TTS無効を維持する。開発モデルはGPT-6-Astra / xhigh。

## 実マイクで確認した事実

- 初回：「一行目の体重を500にして」がDecisionsの3秒期限に達した。
  「価格を6万円にして」→行の回答は、指示と編集案の対応検証で拒否された。保存されていない。
- 無音境界でブラウザのタイマーが早く発火し、必要な1200msに対して1199msを送信、音声接続が終了した。
  `voiceCapture.ts`で実経過時間を再確認し、不足分だけ待ち直すよう修正した。
  サーバー側の1200ms検査、言い直し末尾の3000ms、局所録音の終了操作は変更しない。
- 修正後の実マイクでは一行目の体重500を保存できた。利用者から、途中の「101にして」の質問後、
  「501に直して」だけでは対象が解決できなかったと報告された。最終画面は体重501、音声停止を確認したが、
  この最終値を対象省略の成功証拠とは扱わない。

録音・全文transcript・生のモデル応答は新規ログに保存しない。
`live-timings.jsonl`は通信時刻、段階、ID、境界時間、判定の所要時間・確率を記録する。
`ui-timings-first.json`／`ui-timings-second.json`は表示段階・枠の時刻であり、ブラウザとサーバーの
異なる計測時計を直接引き算しない。全体のp95・速度基準達成・音声精度の保証はできていない。

## 対象を省略した訂正

利用者の追加指定を受け、「501に直して」を直前の保存先への訂正として扱う。

- `domain/fast_path.py`で、単独の数値・引用文字・空欄と変更動詞からなる対象省略節を識別する。
  「違う、501に直して」も同じ節として扱う。裸の数値、複数指示、条件文までこの短い経路で解釈しない。
- `domain/planning_catalog.py`で保存receipt、最新revision、focusのcontextRevision／interactionEpoch、
  所有anchorと現catalogの対象が一つであること、現在scopeと操作権限を検証する。
  複数セル保存後、履歴なし、別世代、範囲変更後は対象を質問する。
- `domain/candidates.py`はこの検証済みセル一つと既存の三つの保留候補をDecisionsへ渡す。
  対象省略により全表のセルを再探索しない。既存の完全指定と同じ確率・信頼度基準を使い、閾値は下げない。
- `domain/dialogue.py`の既知情報にも同じ解決先を保持する。省略した単一指示について質問を挟んだ場合、
  「501に直して」で同じ対象の値を訂正できる。別の名前付き欄についての未完了指示を直前セルへ移さない。
- `application/instruction_planner.py`は、元指示と回答がこの単一訂正の場合にもDecisions一回を使う。
  長文・依存操作は既存のResponses経路を維持する。
- `infrastructure/plan_binder.py`と`planner_provider.py`でも同じ省略指定を扱う。
  元の節で「価格」と明示された値に行を補完するとき、別の「体重」へ移す案を拒否する。
  この欠落は実際の所有catalogに誤った列refを入れた読み取り専用検証で再現した。

省略先は画面上の見かけの選択ではなく、サーバーの保存済み対象から解決する。
OCR実行・provider・mappingId・targetPath・sourceRegionId・crop生成、補正transactionとrevision、
receipt／undo、出力bindingは変更しない。解釈の変更を既存の共有commandへ渡し、**既存OCR contractを維持する。**

## 検証

証拠と検証scriptは `D:/llm-ocr-archive/voice-assistant/20261009-redesign-decisions-phase7`。
通常DBからコピーしたマイク試験と、従来試験の停止時コピーから毎回作るAPI試験DBを分けた。

| 検証 | 結果・限界 |
| --- | --- |
| 対象省略の保存・拒否条件 | `verify_recent_edit.py` 42条件成功。修正前の`recent-before.log`で501の拒否を再現。解釈応答は制御、保存は実ASGI・コピーDB |
| 共通command | `phase2_regression.py` 54条件、手動補正`verify_manual.py` 14条件成功 |
| planner／対話 | `verify_planner.py` 65条件、`verify_phase4.py` 19条件、`verify_dialogue_boundaries.py` 22条件成功 |
| 音声wire／数値 | `verify_voice_wire.py` 18条件、`value_boundaries.py` 14境界成功。wireの上流は制御で実マイクとは異なる |
| 共有操作 | `verify_operations.py`で22操作、保存・再送・取り消し・依存拒否を確認 |
| 契約と依存 | 8schemas、20例、22操作集合、domain／applicationの依存境界成功。変更backend7ファイルのruff／mypy成功 |
| OCR再生 | 72読み取り、290画像SHA、実保存response 5回×正常／4故障の25比較一致。過去に廃止した日付推定の差は既存差分として明記 |
| 補正とXLSX | 75値・72crop対応保持、再編集・再確定・再取得、95出力セルを照合。00789と=1+1は文字列として対応セルへ保存 |
| 複数ページ | 実XLSXの2ページで5条件成功。ページ所有・意味付け・逆操作・境界拒否を確認。新しいpaged OCRやrenderer資格認定ではない |
| frontend | 修正後21ファイル124テスト成功、`npm run build`成功。実マイクで500保存を確認。最新backend修正の実マイク受入は未実施 |
| backend全体 | 変更前後とも1261成功／23失敗／34収集エラー／1外部opt-in skip。失敗名・内容、収集エラー名、skipが一致。全体成功ではない |

修正後の関連API再実行は`recent-regression-summary.json`と`*-recent-final.log`。
初期検証scriptのfixture不足、質問の明示取消不足、context DTOのkind指定漏れは失敗ログを残して修正した。
製品側のassertionを弱めて成功させていない。

全体回帰は`python -m pytest --continue-on-collection-errors`、外部API opt-inを無効にし、
前後別のDドライブ絶対basetempとJUnit XMLへ保存した。修正後302.77秒。
`triage_recent.py`／`recent-regression-triage.json`で失敗名・内容と収集エラー名の一致を確認した。
`git diff --check`成功。`audit.py after`と`business-preservation.json`で通常DBの変更はAPI台帳だけ、
元156件の台帳行・業務表・schema、従来試験DB、855業務ファイル、HEADの保持を確認した。
対象省略修正時点の製品差分はbackend7、frontend2、文書4の計13ファイル。
その後の追加300回の許可反映で`backend/app/ai/api_budget.py`を変更し計14ファイル。
既存差分を破棄せず、commit・pushは行っていない。

実APIの対象省略試験は2回、いずれも読み取り専用で保存しない。
最初は2,586ms、確率0.75／信頼度0.67で質問。Decisionsへの継続先説明を追加し、再試験は
1,031ms、確率0.95／信頼度0.93で正しい一行目体重への501を解決した。Responsesへの追加要求なし。
これは各1例の解析区間であり、発話終了から表示までの速度や安定性の評価ではない。
証拠は`recent-live-diagnosis.json`（不成功）と`recent-live-diagnosis-182.json`（成功）。

API開始156/196、対象省略修正の報告時点182/196、フェーズ内26回。対象省略診断はこのうち2回。
その後、利用者が追加300回を明示許可したため累計上限を496へ変更した。現在182/496、残314。
使用済み182件は保持し、許可反映のための実API呼び出しは0。通常環境と共有する予約台帳を使用する。
起動中プロセスの上限は再起動まで196のため、用意済みの再起動で今回修正と上限を反映する。

## 再開と残作業

1. 全体テストの修正前後照合と、通常DB・業務ファイル・既存差分の保持照合は保存済み。
2. 利用者が専用`restart-backend.ps1`を実行する。8027の所有PID・launcher／child・起動元を検証し、
   そのbackendだけ再起動する。5197、試験DB、通常DB、過去ログを保持する。スクリプトは構文確認済み。
   過去の起動・停止は自動承認レビューで`blocked by policy`となったため、エージェントからは実行しない。
3. 再起動後、同じ表で「一行目の体重を500にして」→保存後「501に直して」を実マイクで確認する。
   再録音ボタンを要求せず、同じセルの保存通知と値・枠を確認する。
4. 長文・質問回答・テンプレート意味付けを含む残りの音声受入と、速度・精度を確認する。
   追加許可後の残314回の範囲で必要な試験を行う。さらに追加が必要なら必要数を報告して許可を待つ。
5. 受入不足・不具合を残したまま通常DB移行、旧write撤去、有効化を行わない。

最新backendの起動・実マイク確認が済むまでは、実装済みと実受入済みを区別する。
