# 音声・Decisions再設計 フェーズ2：共通command・editing.v2 API

2026-10-08。対象は `C:/Users/hanam/Desktop/llm-ocr-deploy`。モデルは GPT-6.1-Sol / high。
[フェーズ1設計契約](voice-editing-redesign-phase1-2026-10-08.md)に基づく共通編集基盤を実装し、分離コピー上の短い検証を完了した。
Decisions／Responses、音声取得、画面接続、通常DB移行は今回の実装対象に含めず、各後続工程に残す。

## 1. 実装した境界

`backend/app/editing/domain/command.py`は不変の操作・変更群・binding・状態遷移だけを保持する。
stdlib以外の依存はない。`application/ports.py`と`application/controller.py`は解釈器、command repository、mutationのportを使い、HTTP・ORM・OpenAIには依存しない。
`infrastructure`が既存の補正／authoring／SQLite、永続fence、receiptを扱い、`api/v2`が型検査とローカルHTTP境界を担当する。

22操作のDTOを`operation_contracts.py`、対象投影DTOを`target_contracts.py`、手動draftを`manual_draft.py`へ分離した。
操作compilerはversion固有batchを組み立てず、共通操作群を受け取る。旧経路も同じ型を再公開する。
実保存は`infrastructure/mutation_core.py`へ移し、新APIから旧会話serviceを丸ごと呼び出す構成にはしていない。
旧transportは通常切替まで残し、v2 tokenを旧session経路で拒否する。

## 2. 保存・競合・復旧

- 一指示は最大50操作・1000変更欄。値／graph、revision一回の増分、操作履歴、mutation receipt、command APPLIEDを同じtransactionで確定する。
- 所有session、resource、page、選択表、局所対象、contextRevision、interactionEpoch、catalogRevision、scopeDigestを保存直前に再確認する。
- 永続commandは本文を含まず、識別子、指紋、binding、状態、fence、受付／終端event、receipt参照を保持する。未保存の指示・planはRAMで管理し、処理後に破棄する。
- 同一session＋requestIdの再送は古いrevisionやsession終了より先に既存結果を復旧する。内容が違えば409。同じrequestIdを別sessionで使う場合は別要求になる。
- `afterCommandId`は同session・同bindingの先行成功だけを根拠に世代を引き継ぐ。手動操作の古いhandleを付け替えず、他画面の変更へ自動rebaseしない。
- 一sessionにつき処理中一件と待機二件。取消と保存はtransactionの先後で確定し、保存後の取消は暗黙のundoにならない。
- 取消／scope変更時は解釈待ちを解除する。準備／保存のthreadを途中破棄せず、fenceで旧結果を拒否する。変化のないcontext更新は解釈を中止しない。
- 再起動後の未保存command・保留質問はINTERRUPTEDとし、AIを再実行しない。成功receiptは再取得できる。現在のRAM制御はローカル単一API processを前提とする。

表選択は任意。選択表と局所欄の外への変更を拒否し、外部revision／scope変更で直近表を失効する。
選択解除時は同ページ・同世代の有効な成功receiptだけから直近表を引き継ぐ。複数表の変更群は直近表をnullにする。
明示表／直近表／全表一意一致の判断器はフェーズ3で接続する。

## 3. HTTP・表示情報・型の正本

`backend/app/api/v2/editing.py`にsession作成、context GET／PUT、commands POST／requestIdでのGET、
認証fetch SSE、手動authoring、session終了を追加した。共通HTTP境界は`api/editing_transport.py`。
通常要求262144 bytes、手動authoringは2097152 bytesまで。tokenはheaderで渡し、URLへ載せない。
SSEはLast-Event-IDで保存eventを再開する。通信切断を取消と扱わない。

成功receiptは変更した全欄の安定ID・revisionを含むfocusを返す。表の欄にはtable／row／column IDも含める。
手動選択・DOM focus・OCR判定を変更せず、移動アニメーション、scroll、表名overlayはフェーズ5で接続する。
手動形状変更は既存の物理検査を通し、意味付けと同じ保存へ入る。物理変更のundoは提供しない。

型の正本は`backend/app/api/v2/editing_contracts.py`と共有DTO。
`backend/scripts/generate_editing_v2_contracts.py`がoperations／commands／events／context／HTTPのSchemaと
`frontend/src/editing/v2.generated.ts`を生成する。planner／decisions Schemaはフェーズ3の設計契約のまま。
HTTP Schemaは実際のoptional投影項目を表し、model向けの全項目必須Schemaと混同しない。

## 4. 確認した結果

証拠は `D:/llm-ocr-archive/voice-assistant/20261008-redesign-decisions-phase2`。
`acceptance.py`は保存済み試験DBから実行ごとに新しいSQLiteコピーを作り、実alembic migrationと実ASGI HTTPを実行する。
実OCR response・既存service・実storageを使う。解釈の待機／取消を作る部分だけ制御用resolverを差し替えており、実AIの精度試験ではない。

| 検証 | 結果 |
| --- | --- |
| 通常31表／従来試験36表から分離コピーへのalembic upgrade head | 両方37表、editing_commands_v2へ成功 |
| コピー上の実API受入条件 | 54条件成功。原子的二欄変更、二重送信、異payload拒否、競合、取消、scope、順序、undo、手動形状、SSE、復旧 |
| 保存中の故障注入 | 実書込み後の例外で値・revision・履歴・成功receiptをrollback |
| 取消／scope更新中の解釈待ち | provider応答を解放せず、1秒以内に待ちを終了。速度の実AI保証ではない |
| 先行command三件の順序／外部手動変更 | 所有増分だけを引継ぎ、外部変更時は旧planと後続依存を拒否 |
| OCR response保持 | document metadata、sourceRegionId、boundingBoxを保持。値は既存revision補正経路で保存 |
| DRAFT意味作成＋接続／取消 | 一群として保存、物理geometry保持、取消後の保存layoutを照合 |
| 旧transport代表操作 | 共通部への移動後もbatch／undoを実保存できる |
| Schema／型境界 | 7 Schema、20設計例、22操作。旧ToolBatchのstrict Schemaと共有操作型が一致 |
| 静的検査 | Ruff成功。対象Mypy42 source成功 |
| frontend | npm run build成功。画面動作は今回未接続 |
| 差分・保持照合 | git diff --check、通常／従来試験DB全表、855業務ファイル、HEADを終了時に照合 |

ログのtraceには意図した無効target・範囲違反・故障注入を含む。`acceptance-results.json`の成功判定と区別する。
コード構造を言い換えるunitは追加せず、実保存・状態境界の短い検証を先行した。

## 5. 保持した契約と未検証範囲

既存OCR contractを維持した。provider／prompt／認識schema／crop生成／受付snapshot／mapping・association／
出力bindingを変更しておらず、補正値とDRAFT意味付けの明示変更だけを既存の検証・revision保存へ渡す。
通常DB31表と従来試験DB36表にはmigrationも編集も実行しない。通常feature flagは無効のまま。
追加migrationは`backend/alembic/versions/0007_editing_commands_v2.py`であり、通常導入はフェーズ7。

全22操作の実保存網羅、backend／frontend全件、paged、XLSX最終対応、ブラウザー、実マイク、実AI、応答時間の受入は未実施。
`scripts/verify_ocr_contract.py`は開始時から現checkoutに存在しない。短いresponse確認はOCR安全性の完全検証ではない。
全体回帰の既存記録には23失敗／34収集エラーがあり、今回それを解消したとは扱わない。

## 6. 変更ファイルと次工程

新規：`api/v2`、`api/editing_transport.py`、`editing/domain`、`editing/application`、`editing/infrastructure`、
共有operation／target／manual draft／schema tools、v2 migration、生成script、HTTP Schema、生成TypeScript、本記録。
既存変更：旧editing contracts／compiler／service／関連22操作handlerのimport、旧HTTP境界、main、lifecycle、alembic登録、
生成Schema／manifest、AGENTS、Safety Contract、Artifact Storage Policy。
開始前からの広い未commit差分は保持し、stage／commit／pushは行わない。今回だけの全差分は`phase2-only.diff`で確認できる。

今回の実外部AI呼出し0。共有台帳137/196、残59。TTSを有効化しない。
次はフェーズ3、GPT-6-Astra / xhigh。有限候補pool、値抽出、Decisions優先、複雑時だけResponses、予算予約を接続する。
providerの取消でも予約を払い戻さず、失敗状態を記録する。実マイクは利用者参加のフェーズ7。
