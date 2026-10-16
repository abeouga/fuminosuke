# 文之助：手動編集と共通編集APIの接続（フェーズ15）

2026-10-07 / 対象 `C:\Users\hanam\Desktop\llm-ocr-deploy` / 次はフェーズ16、Astra / xhigh

手動の意味付けを共通のcommand・revision・receipt・履歴へ接続した。入力中・IME変換中・ドラッグ中は保存せず、操作終了時に保存する。通常利用の新APIはdefault無効、通常DBは未移行のまま。検証はDドライブのコピーDBと専用ブラウザーで行った。連続音声と新会話controllerの接続はフェーズ16以降であり、今回の到達点には含まない。

通常起動中の旧serverは状態照会自体が404となることを確認した。フェーズ19の切替前は、状態照会404またはenabled=falseで従来の手動保存を選ぶ。enabled=trueの新保存失敗から旧writeへfallbackしない。通常serverを再起動・移行して新方式を稼働済みにする作業は今回行っていない。

## 変更の理由と保存境界

従来の手動editorは完成途中のlayoutを組み立ててauthoring snapshotを保存する。新しい意味付けAPIは有限handleと22操作を受け取るため、手動側にも同じ意味上の検証・保存結果・取り消しを適用する変換層が必要だった。frontendだけでは共通commandへの変換、サーバーによる新規ID/path割当、原子的なreceiptを保証できないため、backendに手動専用アダプターを追加した。

`manual-edits`は信頼する手動editorが捕捉した型付き下書きを受け取る。意味付け差分を共通CommandCompilerへ変換し、新しい意味・表・行・マークのID/pathはサーバーで割り当てる。任意graphの置換保存や旧authoringへの自動fallbackは行わない。枠・多角形・結合・印字修正・新しい選択肢領域は手動専用の許可項目として同じprepare/persist/receipt経路へ渡す。物理変更は履歴上のundo境界となり、意味だけの操作を使って以前のgeometryを復元しない。

モデル用の5ツールに手動snapshot・座標・自由なpathは公開していない。手動経路はmanual sessionだけに限定し、body上限、DRAFT、revision、ページ所有、既存authoringのmapping/crop/print検証を維持する。検出品質、既存path、結合前の物理セル履歴などの未対応変更は明示的に拒否する。分類時に旧editorが付けるsource印は保存済みの物理由来を置換せず、行bbox・order等の派生情報は共通compilerが計算する。

## 手動操作と復旧

- テンプレート単位のEditingStoreで書込を直列化する。要求IDとpayloadを送信前にタブのjournalへ保存し、本人の成功receiptによるID/path置換だけを後続下書きへ反映する。
- フォーカス中の文字、IME変換、押下中のpointer、未完了の多角形・仮想見出し入力を保存境界から除く。保存待ち中も編集でき、遅れて届いた応答で後続入力を上書きしない。
- 通信不明・JSON応答欠落・5xxでは元のreceiptを照会する。NOT_FOUND時に再送する場合も元のキー/payloadだけを使う。4xxの明確な拒否や競合でqueueを止め、後続入力を保持する。
- 保存成功後の表示取得失敗は「保存済み・表示更新待ち」。復旧ボタンは保存版を再取得し、保持した入力を次の編集操作まで再送しない。期限切れsessionでも元receiptは取得でき、新しい読取sessionを使う場合も保存revisionを照合する。
- 他の書込への追従は自動化しない。保存版の差分を表示し、明示した編集再開操作に限り未編集部分へ保存版を反映する。入力済みの箇所を保持したまま、次の操作で保存する。
- 取り消しはローカルな線の履歴ではなく、共通resource履歴の直前操作を新revisionとして保存する。取り消し要求もjournalと元receiptで復旧する。印刷編集が返した本人のrevisionはqueue内で照合し、未知の外部revisionを黙って採用しない。

## 既存の手動下書き機能を保つための共通型の拡張

操作数は22のまま、HTTP・strict tool・TypeScriptを同じ型から生成した。手動専用HTTP schemaは別項目で出力し、モデルツールに混ぜない。

- `records.define`と`records.replace_rows`は空の行を明示できる。空の表も有効な未完成DRAFTとして保存する。非空行の列数・所属・物理行IDの検証は保持する。
- `records.define/configure`の`columnLabels`はnullなら従来の列導出、明示配列なら現在の項目名セルを指定する。`records.configure.name=null`は既存UIの「名前を付けない」に対応する。collectionPathと行IDは名称変更で作り直さない。
- 表の意味の接続・解除は`meaning.bind/unbind`を共有する。既存の同じpathへのラベル変更ではrelation IDを保持し、明示した接続解除後も行所属を保持する。未接続になった表を確認済みと偽装しない。
- `mark.configure`のmeaning=null、空optionsは未完成の選択肢群を表す。空pathをOCR可能な意味として作らない。領域が必要な非空選択肢、ページ別checklist制限、所有者、重複path、現行の50選択肢上限は維持する。

## 検証結果

| 検証 | 現在の証拠 |
| --- | --- |
| コピーDBと実API | `verify_manual.py` 14項目、既存の意味付け境界8項目、ページ境界7項目。共通のmigration保持項目を重複除外し、27項目が通過 |
| 手動とAPIの同値 | 同じ物理セル・意味・pathを手動の線接続と公開meaning.bindで保存。新規relationの割当ID以外の業務graphが一致。undo後の元graphも一致 |
| 未完成DRAFTと行identity | 未割当マーク、空表、名前なし、表の線解除を保存。3行の入替でセル所属・既存path・relation IDを保持。解除後も行を保持しundoで復元 |
| ページ | 保存実XLSXを2ページ化。全ページを含む手動下書きの現在ページ編集で、他ページのセルを変更しない。越境・マークmode・metadataの既存拒否も通過 |
| 実ブラウザー | Chrome、frontend 5185、実backend 8015、専用コピーDB。入力中の未保存、保存待ちの後続入力、成功応答欠落、保存後5xx、表示取得503、他の保存との競合と明示再開、氏名からの線接続、共有undo、再読込を確認 |
| 入力/ID照合 | 実reconcileモジュールの短い3ケース。別欄で同じ選択肢のローカルIDを使う場合、新セルのID割当、本文を置換しないこと、後続入力保持を確認 |
| frontend | `npm run build`成功（TypeScript + Vite、92 modules） |
| backend | `ruff check app/editing app/api/v1/routes/editing.py`成功。`mypy`同対象、`--follow-imports=silent --no-incremental`で17 source成功 |
| 保持照合 | 通常DB全31テーブル、855業務ファイル、API台帳25件を開始時と照合。4つのコピーでも受付済み6 job、OCR/補正revision/確定snapshot/page review/input binding、既存855ファイルを保持 |
| 差分 | `git diff --check`、976ファイルの開始時SHAとの差分範囲を確認。予定外の変更・削除、staging/commit/pushなし |

保存後の5xx試験ではrevision 14→15の一回だけ進み、元receiptのGETで成功を取得した。表示取得失敗の試験ではrevision 9→10で保存済み、復旧後も10のまま。競合では他の書込で10→11、古いcontextのPUTが409となり、入力を保持した。明示再開後の次の保存で11→12となり、他の画面が変更した別欄も保持された。線接続12→13、undo13→14、再読込後も接続解除と表示名が一致した。

試験middlewareの初回の再使用時には、使用済みflagのrename衝突で保存前500が出た。製品の回帰と区別し、fixtureを一意な保存先へ修正して再実行した。途中の失敗は成果物に残した。大きな依存範囲まで追跡するmypyは既存Windows/import型の問題を含むため、現行フェーズの指定範囲による検査と区別した。

## 変更ファイルと保護範囲

追加：`backend/app/editing/manual_contracts.py`、`manual_compiler.py`、`frontend/src/editing/client.ts`、`generated.ts`、`reconcile.ts`、`store.ts`。

変更：`backend/app/editing/contracts.py`、`commands.py`、`service.py`、`schemas.py`、`semantic_commands.py`、`record_commands.py`、`mark_commands.py`、`backend/app/api/v1/routes/editing.py`、`frontend/src/components/LayoutEditor.tsx`、`frontend/src/pages/TemplatePage.tsx`。文書は本書、AGENTS、Artifact Storage Policy、Safety Contract、フェーズ12設計。

既存OCR contractを維持した。OCR provider/prompt/schema/model、画像前処理、読取・crop生成、受付snapshot、補正・確定・出力の実装は変更していない。通常DBへ新schemaを適用していない。手動編集の対象版だけを既存authoring serviceで保存し、受付済みjobを現行テンプレートから再構築しない。コピーの保存済みOCR・snapshot・入力とファイルの照合を実施した。

全ユニットテスト、全体回帰、全形状の手動編集、連続音声と共通queueの結合、実マイク、正常環境の切替は今回の検証範囲に含めない。重い回帰はフェーズ18、実マイクと通常切替はフェーズ19。従来の局所録音・手動描画は維持し、新会話controllerの履歴接続はフェーズ16で行う。

## 成果物と次フェーズ

証拠は `D:\llm-ocr-archive\voice-assistant\20261006-phase15`。開始日は10月6日、完了記録は10月7日。`baseline.json`、`before/`、`before.db`、`data-before/`、各runと最終log、生成schema、`ui-requests.jsonl`、`ui-*.json`、`manual-wire-saved.png`、`reconciliation-result.json`、`copied-ocr-preservation.json`、`preservation.json`、`phase15-only.diff`を保管する。試験スクリプトは製品sourceへ追加していない。専用serverと検証タブは終了する。

実外部AI API追加 **0回**、累計 **25/100回、残り75回**。

フェーズ15で停止する。次は **フェーズ16、Astra / xhigh**。Function calling、無選択の対象解決、曖昧な指示への質問、訂正、手動と音声の共通履歴を接続する。モデル切替と次の実行指示を待つ。
