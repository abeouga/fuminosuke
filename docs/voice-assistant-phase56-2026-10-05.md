# 文之助 提案・承認・適用の実装記録

2026年10月5日、[設計書](voice-assistant-design-2026-10-05.md)のブロック4・フェーズ5〜6をdeployコピーへ実装した。操作アシスタントの手入力から変更案を作成し、両画面で明示承認後に保存・再取得する。通常の業務画面の外観を維持する。STT・LLMによる提案・TTS・マイクはまだ接続しない。

対象は `C:\Users\hanam\Desktop\llm-ocr-deploy`。元の `llm-ocr` は編集しない。既存の未コミット変更を保持し、commit・push・差分破棄は行わない。外部AI API使用は今回0回、累計0／100回。

## フェーズ5：型付き操作と表示名

| 操作 | 対象と変更 | 拒否・保持する条件 |
| --- | --- | --- |
| `ocr.set_value` | 未確定の単一OCR結果にあるtext mappingのraw/normalizedを同じ文字列へ。nullは明示空欄 | mappingId/targetPath/sourceRegionIdの受付時対応、他欄・reviewItems・crop・座標を保持。数字・日付の変換をしない |
| `template.rename_cell` | DRAFTのlabel/parent_labelはtext、inputはfieldLabel | printedLabelText、セルID、mapping ID、targetPathを保持。出力表示名への影響を提示 |
| `template.change_category` | 依存関係のない単一セルのrole | relation・見出し階層・繰り返し・結合・marks・読取設定等の整理が必要なら拒否し、手動編集へ戻す |
| `template.rename_choice` | 所有cell/markSet/choiceを確認してlabelを変更 | symbol/checklistはvalueも同名へ。manual/automaticはvalueを保持。追加・削除・並び替え・重複名称は不可 |

fieldLabelは未指定null、1〜100字、前後空白を拒否する。未指定keyはserializationから省略し、既存layoutのJSON内容hashを一括変更しない。入力欄のmapping.labelはfieldLabelを優先する。手動編集にも表示名欄を追加した。

forkの参照置換からfieldLabelを除外し、duplicate・packageのLayoutCellと結合partsへ保持する。結合する入力欄の表示名が1種類なら親へ引き継ぎ、異なる名前を黙ってまとめない。結合解除では保存された各partを復元する。packageの生成schemaを更新した。

## フェーズ6：サーバー提案と承認

session作成後、contextを世代付きで順に同期する。resource revision・view instance・selection/local-edit epochと対象IDを保存し、変更時に前の案を失効させる。クライアントのdraft全体やsnapshotKeyをサーバーへ送らない。

提案は業務正本からbefore/after・影響・diff hashを生成する。期限は120秒、sessionは最終操作から30分。UIが案をrenderした後にpresented APIを呼び、1回の適用に束縛したchallengeを取得する。モデルやクライアントが指定するapproved booleanやbefore/afterは受理しない。

適用ではreceipt所有者を先に確認し、成功済みの同じ要求を期限や現在revisionより先に再生する。未処理要求ではcontext・期限・提示状態・challenge・hash・正本revisionをtransaction内で再検証する。共通補正/authoring、revision、操作履歴と逆操作payload、提案消費、成功receiptを一括commitする。

templateのauthoring prepareは同じwrite lockの下で別の読取Sessionを使い、不変設定を公開してからDB参照を更新する。外部APIやnative rendererは呼ばない。失敗時にはDB参照・履歴・提案消費をrollbackする。公開された未参照ファイルは残し、GCは別作業とする。

UIは未保存編集に保存・破棄・中止を求める。適用中・結果不明中は手動変更を停止する。成功receipt後は正本を取得して表示を更新する。templateでは保存後も所有セルの選択を維持する。応答不明時は同じ要求キーで再送し、保存済みで再取得だけ失敗した場合は再取得を行う。

challengeは信頼するUIとの提示束縛であり、API単独で実際の人間のクリックを証明する機構ではない。session tokenはパネルのメモリに置く。このブロックでは再読み込み後の継続復旧を完成扱いにしない。

## 変更ファイルの境界

- `backend/app/assistant/{contracts,models,operations,service}.py`：型、所有関係、提案、承認と適用。
- `backend/app/api/v1/routes/assistant.py`、router、main：APIと必要なCORS header。
- `backend/alembic/versions/0003_assistant_proposals_v1.py`、env：2テーブルの明示migration。
- LayoutCell、layout_service、template_version_service、生成package schema：fieldLabelの保持・compilerへの接続。
- `frontend/src/assistant`、ReviewPage、TemplatePage、LayoutEditor、types：サーバー接続、編集lock、再取得、表示名編集。
- AGENTS・Safety Contract・Artifact Storage Policy・本記録：変更契約と次ブロック。

OCR provider・認識prompt・認識schema・association処理・crop生成・受付snapshot・出力adapterは変更していない。既存OCR contractの対応関係を維持し、名称と明示補正の変更だけを追加した。

## 検証と証拠

退避・使い捨て検証は `D:\llm-ocr-archive\voice-assistant\20261005-phase56`。開始前647ファイルの内容hashと差分、DBのonline backup、855データファイルのコピー/SHAを保存した。

| 検証 | 結果と限界 |
| --- | --- |
| 新規DB・既存DB migration | head=`assistant_proposals_v1`、29テーブル。既存26業務テーブルの全行・全列を維持、2つの新テーブルは空、integrity/FK一致、再実行はno-op |
| 短いAPI/DB integration | 16シナリオ通過。実serviceとSQLiteコピーを使用、worker/providerは起動しない |
| 所有・承認・失効 | 未提示適用、別session、hash/challenge違い、期限、取消、A→B→A、手動revision変更、確定→再開を拒否 |
| transaction/再送 | 同一キー並行は同じ結果、異なるキー同じ案は1回だけ。業務flush後の故障で全更新rollback、同じキーで復旧 |
| OCR/XLSX | `00123`、null、`0`、日付文字列を保持。他欄・確認メモ・crop・snapshot対応一致。確定後XLSXのB4へ`00100`を文字列出力 |
| fieldLabel・印刷ページ | 未指定serialization一致、fork/実duplicateで保持。保存済みページの所有を照合し、geometry・reference・版の対応を維持。native描画/資格認定は実行しない |
| 実UI | 分離した通常backend 8011/frontend 5181で7シナリオ。承認前無変更、適用中lock、選択維持、未保存編集の破棄、OCR再取得、サーバー停止から同じキーで復旧 |
| 静的検査/build | 追加assistant/route/migrationのruff、assistant/routeのmypy、frontend `npm run build`を通過 |

integrationは通常のHTTP経路を使用する。duplicateの名前保持試験だけはコピーDB内にACTIVE状態をseedした。実際の有効化・qualificationの合格を証明する試験ではない。ページ試験には元geometryが一致する別の未編集XLSXを使い、gateを解除していない。

故障注入のRuntimeErrorは期待したrollback試験のログ。初期のduplicate試験はDRAFTから複製しようとして拒否され、fixtureの状態を正した。初期の印刷試験では既存の手動変更geometryが拒否され、元セルと一致する別fixtureへ変更した。製品側の拒否条件やassertionを緩めていない。

主要成果物は `integration-results.json`、`integration-with-pages.log`、`ui-results.json`、`ui-template-after.json`、`ui-ocr-after.json`、各画面PNG、各runの`assistant-result.xlsx`。全件unit/E2E、実マイク、STT/LLM/TTS、認識精度は未検証。

## データ移行・稼働状態

実データ移行と通常launcherの再起動を完了した。同退避先の`actual-migration-results.json`・`runtime-results.json`・`runtime-processes.json`に結果を保存した。既存26業務テーブルの全行・全列、855ファイルのSHA、OCR/templateの保存値・ID・revision・確定状態が一致し、assistantのsession/proposal表だけを追加した。旧layoutにfieldLabelを書き込む一括移行は行っていない。

通常入口`sysover-ray/Start-LlmOcr.ps1 -NoBrowser`から再起動し、frontend 5180/backend 8010、health、7つのassistant API、既存OCR/authoring GETを確認した。launcher PID 27920、backend leaf PID 21516、frontend PID 23800。分離検証の5181/8011と作成したブラウザーtabは終了した。今回の25ファイルの差分を開始時コピーと照合し、追加9・変更16・削除0、`git diff --check`を通過した。

## 次のブロック

ここで停止する。次は**Astra / xhigh、フェーズ7**。

1. 現在のresource operationと逆操作payloadを使い、元操作/逆操作をそれぞれ1回だけ適用する永続undoを実装する。
2. session/要求キー/保存receiptの復旧を、別タブ・再読み込み・保存後の応答消失まで広げる。失敗・未処理・成功済みを区別する。
3. confirm/reopen・手動編集・後続変更と競合したundoや古い要求を拒否し、修正draftを保護する。

フェーズ8はGPT-6.1-Sol / highでSTT・提案API・TTS・予算ledger。マイク試験が必要な時点で利用者を呼ぶ。重点的な全体検証はフェーズ9へ残す。
