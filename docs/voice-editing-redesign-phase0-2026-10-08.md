# 音声編集・AI向けAPI再設計：フェーズ0基準・差分監査

2026-10-08。対象は `C:\Users\hanam\Desktop\llm-ocr-deploy`。
フェーズ0を完了し、次はフェーズ1（GPT-6-Astra / xhigh）です。
このフェーズは現状監査と後続工程への引き継ぎです。製品コード・DB schema・設定・起動停止は変更していません。
旧音声方式の追加修正、通常切替、実マイク試験は再開していません。

## 1. 今回の要求と変更境界

利用者の直近の要求と合意した再設計計画を優先します。旧フェーズ文書や添付プロンプトは履歴・参考資料です。
破壊的変更は音声編集・AI向け編集APIの再設計に適用し、旧APIとの互換層を残すことを優先しません。
既存業務データ、未コミット差分、受付snapshot、物理セル対応を破棄する意味ではありません。
元の `llm-ocr` リポジトリは実装対象外です。

採用する要求は次のとおりです。

- 応答開始は発話終了から1秒以内を目標とし、保存完了・画面反映とは別に測定する。
- 一つの指示内の変更群は原子的に保存する。一部でも曖昧なら全件未保存のまま不足情報だけを質問する。
- 連続音声はセル未選択で使い、保存後の訂正もボタンを押さず続けられる。定型の承認句を要求しない。
- 長文は最大3分を扱う。文字起こしの途中区切りを個別の編集指示として保存しない。
- 質問中は元の指示全体と値を保持し、対象だけ・値だけの回答で続行する。
- 選択セルへの局所録音、手で線を引く意味付け、手動枠作成を維持する。
- テンプレート意味付けとOCR補正は同じ業務編集基盤へ接続する。行＋列指定は欄名に依存しない。
- 通常の業務用プレビューを中心にし、操作ボタンと通知を必要最小限にする。現行TTS無効状態を維持する。
- AI向けAPIはアプリ内部のFunction Calling接続まで。外部ChatGPT用MCP公開は今回の範囲に含めない。

## 2. 開始時のソース・データ・API予算

正本の開始証拠は `D:\llm-ocr-archive\voice-assistant\20261008-redesign-phase0\before.json` です。

| 対象 | 現在確認した値 | 意味・限界 |
| --- | --- | --- |
| HEAD | `35adae6df378fe288593b113b12615f5c47f2a2a` | main。未コミット差分を含まない |
| 既存status | 通常short表示397件、未追跡を全展開した表示546件 | 同じ差分の表示単位が異なる。今回発生した変更数ではない |
| ソース・文書退避 | 817ファイル | Git管理／非ignore未追跡の指定拡張子を対象にした退避。過去の全ファイル数とは数え方が異なる |
| 通常DB | SQLite、31テーブル、`assistant_voice_v1` | 新編集5テーブルの通常移行は未実施 |
| 試験DB | 36テーブル | phase19の専用業務コピー。共有AI予算の正本ではない |
| 業務ファイル | 855件、各内容SHA保存 | DB・ログ・WAL/SHMを除く |
| 通常編集API | 設定 `editing_api_enabled=false` | 読み込んだ設定値。通常サーバーの動作保証ではない |
| 現行テキスト解釈モデル | 設定 `gpt-6.1-sol` | 開発用モデル切替とは別の実行時設定 |
| 共有AI台帳 | 137予約、最大ordinal137、上限196、残59 | 92 RESPONSE、45 RESERVED。予約後の失敗・未確定も消費する |
| 今回の外部AI使用 | 0回 | OpenAI／Googleへの送信をしていない |

通常DBの `api_call_ledger` をSQLite読取専用接続で確認しました。
phase19の `live_server.py` は通常DB予算を使い、試験DBへ必要な予約参照を複写します。
試験コピーの行数や過去文書の118回を現在の残予算として扱いません。
台帳のcountと最大ordinalは一致しています。送信前予約・払い戻しなしの契約を継承します。

開始時のDBは読取専用接続からSQLite backupで通常／試験を別々に退避しました。
各テーブルの行数、内容SHA、schema SHAと業務ファイルSHAを保存しています。
秘密設定の内容、録音、文字起こし全文を監査結果へ保存していません。

## 3. 現行処理経路と置換すべき責務

| 根拠 | 確認した現行動作 | 再設計で扱う点 |
| --- | --- | --- |
| `frontend/src/assistant/ConversationAudio.ts` | VADのcompleted単位をqueueへ渡し、各itemでbegin→resolveを実行。1発話30秒 | 音声区切りと業務指示を分離し、最大3分の指示へ集約 |
| `backend/app/assistant/stream_service.py` | 接続300秒、累計1200秒、24000Hz×30秒上限 | 新しい発話上限と接続寿命を別々に定義。自動再送による予算消費を防止 |
| `backend/app/assistant/stream_proxy.py` | `gpt-live-transcribe`へ中継 | 文字起こしadapterとして隔離し、編集実行権限を持たせない |
| `backend/app/assistant/conversation.py` | 最大4回のモデル要求、最大6read、60秒のRAM会話記憶。検索と操作の往復 | 1回の構造化プラン生成を基本にし、検証・対象解決・保存をapplication側で所有 |
| `backend/app/assistant/conversation_provider.py` | Responses API、strict schema、parallel tool calls無効、reasoning low、出力上限6000 | プロバイダー設定を業務処理から分離。呼出し回数・実時間を測定 |
| `backend/app/editing/schemas.py` / `registry.py` | 22操作、get context／find targets／apply batch／undo／receiptの5ツール | 編集操作の定義を再利用候補とし、モデルへ公開する契約を小さくする |
| `frontend/src/assistant/conversation.ts` | resolveのREADY応答後、別HTTP要求でstore.commitConversation | 文字指示受付から保存までをサーバーの一つのuse caseへ移す |
| `backend/app/editing/service.py` | context、catalog、権限、turn、保存、undo、履歴、receiptを同serviceが所有 | applicationの処理とdomain検証、DB adapterの境界を明示 |
| `backend/app/editing/catalog.py` / `record_cells.py` | 保存済み構造から対象を投影 | 名前・行番号は検索metadata。保存identityを再生成しない |
| `frontend/src/editing/store.ts` | 手動／音声queue、journal、receipt復旧、表示の再取得 | 保存の二重実行を防ぎつつ、UI下書き・表示世代・結果不明の復旧を維持 |
| `backend/app/services/mutation_receipt_service.py` | 要求キー照合、履歴、補正／authoringとのtransaction保存 | 共通mutation基盤として維持。変更と成功receiptを同時commit |
| `backend/app/api/editing_cutover.py` | 新編集有効時のみ旧writeを410拒否 | 最終切替時に旧入口を撤去し、両方の編集契約を通常運用しない |

現在の会話記憶はプロセスRAMとクライアント質問IDへ分かれています。
再設計では保留指示の所有者、期限、revision、質問への回答、取消、停止、再接続の扱いを一つの契約にします。
「値を保持する」ために全文会話を業務DBへ恒久保存する設計にはしません。

## 4. 遅延の基準と未測定区間

既存 `http-timings.jsonl` の421行をstatus別に集計しました。
期間は2026-10-07 15:46:09〜2026-10-08 18:52:24（ログ表記）です。
コピーAPI、文字指示、マイク等の試験が混在する履歴であり、統一条件の性能試験ではありません。

| HTTP区間（成功200のみ） | n | 平均 | 中央値 | p95（nearest rank） | 最大 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 指示resolve | 33 | 9.067秒 | 8.156秒 | 17.672秒 | 18.203秒 |
| targets:find | 14 | 0.016秒 | 0.016秒 | 0.016秒 | 0.016秒 |
| batches保存 | 27 | 0.051秒 | 0.047秒 | 0.125秒 | 0.140秒 |
| turn作成 | 38 | 0.029秒 | 0.031秒 | 0.047秒 | 0.047秒 |

resolveの409応答5件は成功集計へ含めません。200も保存成功だけでなく質問／no actionを含み得ます。
検索と保存のサンプルはresolveと一対一に対応しないため、上記の平均を足して総所要時間とはしません。
`voice/speech` はTTSであり、STTの測定ではありません。

**合理的な推論**：この履歴ではresolve区間が遅延の主要候補です。
モデル単体、複数要求の往復、入力catalogの量、STTの各寄与はまだ分離していません。
モデル変更だけで応答・精度が改善すること、Lunaで1秒以内に保存できることは未検証です。

後続実装ではrequest／utterance IDを使い、以下の時刻を同じ指示に結び付けます。

1. ブラウザー発話終了（指示全体の終端）
2. サーバー受付とUIの応答開始
3. 全文文字起こし確定
4. プラン要求開始／構造化応答完了
5. 対象・値・権限検証完了
6. transaction commit／receipt確定
7. 対象previewの反映完了

発話終了→応答開始、発話終了→画面反映、モデル要求回数、明確指示の保存率、誤対象率を別々に報告します。
字幕deltaや「処理中」の表示を保存完了と扱いません。p50/p95と失敗件数を併記します。
長文ではVADの短い無音と指示全体の終了判定を区別する必要があります。終端判定の閾値と評価条件はフェーズ3で固定します。

## 5. 現在の実行環境と検証証拠の区分

今回のread-only HTTPでは8019のhealth／editing status、5189の画面配信が200でした。
試験APIは `editing.v1` enabled=true、OCR provider=disabledです。
8019はphase19のPython process、5189はdeployのVite processに属することを確認しました。
8010／5180のlistenerはありません。起動・停止・再起動はしていません。
起動中backendがディスク上の最新ファイルをすべて読み込んでいる保証にはしません。

前工程の[フェーズ3記録](voice-record-cell-phase3-2026-10-08.md)にはfrontend97成功、backend1261成功／23失敗／34収集エラー／外部skip1があります。
これは前工程の証拠であり、今回再実行した結果ではありません。全体テスト成功とは報告しません。
行・列の実マイク成功も、今回の長文・応答速度・名称精度の保証へ転用しません。
最新の利用者回答には名称誤認、価格指示の値欠落、質問後の続行失敗があり、旧音声実装の追加修正は停止されています。
この時点の音声UX全体を受け入れ済みとは扱いません。

## 6. 後続フェーズへの具体的な引き継ぎ

目標構造はUI/HTTP/WS adapter → application use case → domain型・検証 → infrastructure adapterです。
domainからFastAPI/OpenAI SDK/DBへ依存させず、手動とAIは同じapplication mutationへ接続します。
既存transaction、receipt、revision、authoring／OCR correctionの実保存を再利用します。

`editing.v2` の案は `POST /api/v2/editing/sessions/{id}/commands`。
全文指示、scope、request ID、clarification reply IDを受け、サーバー内でプラン生成から原子的保存まで実行します。
イベントはaccepted／applied／clarification_required／no_action／conflictを区別し、成功時はreceiptとrevisionを返します。
モデルには有限の対象catalogとstrictな変更群schemaを渡し、自由なSQL／path／geometryを公開しません。
検索→実行のモデル往復を通常経路から外します。手動のgeometry編集は既存authoring adapterの責務です。

| フェーズ | 使用する開発モデル／強度 | 終了条件 |
| --- | --- | --- |
| 0 基準・差分監査 | GPT-6.1-Sol / high | 本記録、開始退避、予算、遅延集計、保持照合 |
| 1 domain・API契約 | GPT-6-Astra / xhigh | 依存境界、command/event型、22操作対応、保留／receipt／競合／長文契約を固定 |
| 2 サーバー実行 | GPT-6.1-Sol / high | 一つの指示use case、1回を基本とするプラン生成、原子的保存、共通手動mutation、短いコピーAPI確認 |
| 3 音声・保留指示 | GPT-6-Astra / xhigh | 最大3分集約、質問時の指示保持、割込み・停止・遅い応答・世代とrevision管理 |
| 4 UI・意味付け統合 | GPT-6.1-Sol / high | preview中心の連続／局所音声と手動編集、保存通知、必要最小限の操作 |
| 5 独立設計レビュー | GPT-6-Astra / xhigh | 責務重複、API往復、所有／競合、復旧、長文の誤保存を点検し修正事項を固定 |
| 6 最終検証・切替 | GPT-6.1-Sol / high | 必須全体検証、保存response/crop/XLSX比較、実マイク、遅延・精度の評価、通常移行と旧入口撤去 |

各フェーズ境界で停止し、モデル切替後に次のフェーズを実行します。
実行時のテキストplannerは合意したGPT-6 Lunaを候補とし、開発モデルとは分離します。
音声adapterとplannerのmodel設定はフェーズ1以降で公式仕様と照合し、実呼出しの適合性を別途検証します。
重い全件unit/E2Eは後半へ寄せ、各工程では新しい境界の短い検証だけを先に行います。
実マイク試験は利用者を呼んでから実施します。API追加枠が必要なら送信前に報告します。

フェーズ1では、最低限次の受け入れ／失敗条件を型と状態遷移へ落とします。

- 表の行＋列名／列番号で汎用セルを指定し、先頭ゼロを保持する。数量と引用文字列を区別する。
- 複数変更のうち一つだけ対象が不明なら全件未保存。対象だけの返答後に元の全変更を一度だけ保存する。
- 明確な変更後の「違うよ…」を直前receiptへ束縛し、手動／外部revisionを越えて勝手にundoしない。
- 長文途中の「500、いや501」は終端前に500を保存しない。
- 同じrequest IDの再送は同じreceipt。応答消失で新しい変更を二重実行しない。
- 解析中に手動編集・page/scope変更・停止があれば保存権限を再照合。保存済みreceiptは停止で取り消さない。
- DRAFTの意味付けを共通mutationで保存し、未完成DRAFTを許す。ACTIVE・確定済み・所有外・未対応pagedは拒否する。
- 欄名の近似は一意な現所有候補に限定し、最高スコアだけで選ばない。値を近似名称の根拠や推測置換に使わない。

## 7. 証拠と今回の検証

成果物は `D:\llm-ocr-archive\voice-assistant\20261008-redesign-phase0`。
`audit.py` はconfig／型定義だけを読み、アプリ初期化、migration、予算予約、API送信を行いません。

- `before.json`／`source-before`：開始status、ソース内容SHAと退避、通常／試験DBのテーブル内容SHA、855業務ファイルSHA。
- `normal-before.db`／`trial-before.db`：読取専用接続から取得したDB退避。
- `status-before.txt`／`diff-stat-before.txt`／`tracked-before.diff`：既存差分の記録。秘密設定と業務dataはdiff対象外。
- `historical-timings.json`：過去ログのSHA、status別集計、測定の限界。
- `runtime-readonly.json`：listenerの所有、HTTP health/status、画面配信の確認。
- `after.json`／`preservation.json`：終了時の保持照合。
- `diff-check-after.txt`／`documentation-check.json`：既存差分を含むdiff checkと、今回分の文書確認。
- `phase0-only.diff`：開始退避との比較による今回の3文書だけの差分。新規の本記録も含む。

実行条件：backend仮想環境のPythonで `audit.py before`／`audit.py after`、`PYTHONDONTWRITEBYTECODE=1`。
終了照合は通常DB全31テーブル、試験DB全36テーブル、855業務ファイル、HEADが一致しました。
変更されたソース／文書はAGENTS.md、ARTIFACT_STORAGE_POLICY.md、本記録の3ファイルだけです。
`git diff --check` は既存差分を含めexit 0。今回の文書の参照先と行末空白も確認しました。
今回の動作確認はread-only HTTPまでです。build、全件テスト、編集API保存、実OpenAI、実マイク、通常切替は実行していません。
既存OCR contractを維持したという報告は、今回製品コードと業務データを変更しない範囲の保持照合に限ります。
新しい音声編集機能やOCR全体の安全性を実証したという意味ではありません。
