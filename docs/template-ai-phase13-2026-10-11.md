# AIテンプレート作成フェーズ13：採用前の修正試作

2026-10-11。担当工程はGPT-6.1 Sol / High。フェーズ12で再現した3つの欠落を製品外コピーで修正し、保存応答と境界条件を検証した。製品採用・速度合格・通常導入の完了ではない。

開発対象は `C:\Users\hanam\Desktop\llm-ocr-deploy`。試作・証拠は `D:\llm-ocr-archive\template-ai\20261011-phase13`。旧 `llm-ocr`、通常製品コード、通常DBの移行・設定変更は対象外。破壊的変更の許可に基づき、試作resolverの旧呼出し形式は廃止した。実APIは0回、共有台帳312/496、残184回。

## 1. 修正内容と境界

### 単体markの意味結合

`prototype/app/editing/manual_compiler.py` のmanual変換がinputだけを関係再構成の対象とし、単体markのrelationを欠落させていた。inputまたはmarkを扱うよう修正した。

markはmarkSetが一意で、relationと同じtargetPathを持ち、compositeSubfieldsを持たない場合だけ再構成する。複数候補や不一致は拒否する。既存relationのID・targetPath、表示名のみの変更、明示解除を維持した。既存CommandCompilerの一意性検証は弱めていない。

### 候補と取得時入力の束縛

新設 `candidate_snapshot.py` の不変snapshotに、入力と候補の正規化JSON、resource/version/page、revision、画像SHA、catalog generation、policyと全体digestを束縛した。期待binding/digestは所有run側で別に固定する。画像内容をcapture時に検証し、復元時にも候補membershipとpolicyを検証する。

入口は `resolve(raw, snapshot, *, expected, ...)` と `compile_candidate(normalized, *, catalog=None)`。payloadと候補を別々に渡す旧形式を残さず、取得後の入力dict変更もsnapshotへ伝播しない。compile時にも参照map、物理geometry、revision、generationを照合する。古い候補と新しいセル配列の組合せを拒否する。

これは実DB所有run・権限・保存revisionの統合実装ではない。テストのresource/version/page/revision/generationは明示的なfixture値であり、画像SHAだけは固定画像の実内容を使用する。既存製品のsnapshot、physicalDigest、facet、receipt、undo等のguardを置き換えたり迂回したりしない。

### context説明列

`expand.py` でcontext bodyをlabelからignoredへ変更した。元の物理IDと表示文字列は保持し、値の所有へ参加させない。表・候補・列・該当セルのcontext情報を案metadataとして保持する。LayoutDefinitionへ新しい永続項目を追加した変更ではない。

context分類は所有表に依存させた。表が保留ならその説明セルだけを独立してignoredにしない。手動role保護で部分保留された場合もretained/heldセルを記録する。必須親と任意の上位見出し接続について、手動parent facet保護を適用した。保護済み見出しの参照は実際の既存catalogを要求し、空graphへ捏造しない。

Phase11のprovider promptにはcontextをlabelとする旧指示がある。今回の再生は既存応答を新resolverへ通したものであり、promptを更新して新API応答を得た結果ではない。Phase14で出力契約とpromptの整合を確定してから次の実測へ進む。

## 2. 検証結果

| 検証 | 結果 | 限界 |
| --- | --- | --- |
| `baseline_regressions.py` | 固定Phase11コードで期待した3不具合を再現 | 修復後も旧コードを参照する原因確認用 |
| `replay.py` | 保存20応答すべてmanual変換後の意味投影を保持 | 保存・再取得や新API品質の受入ではない |
| `boundary_checks.py` | 63項目通過 | 製品外メモリ上の検証 |
| 既存graphのno-op | 20/20で完全一致、上記63項目に含む | 製品の全facet・保存lifecycleを網羅しない |
| 対象manual compilerのRuff | 通過 | 外部試作の対象ファイルのみ |
| `freeze.py` | Python 354ファイルの構文検査・実装hash固定 | 実行時全体回帰ではない |
| `audit_state.py after` | 通常データ・両checkout・過去証拠・差分を照合。結果は `preservation.json` | 動作テストの代わりではない |

RuffではPhase11から継承した長さ超過2行も確認し、外部コピーだけを改行整理した。規則の無効化・skip・gold変更は行っていない。

境界検証には、所有resource/version/page/revision/画像/generation変更、digest/policy/membership不一致、参照改変、未知ref、重複値所有、逆順行、階層cycle、単数checklist拒否、同値の手動meaning/parent意図、context role保護、既存見出しのread-only参照、mark結合の保持・解除・不一致拒否を含む。

保存20応答の再生結果は次のとおり。分母・gold・応答本体・画像はPhase11のまま固定した。関係やラベルの集合順は正規化し、表の行順と記号選択肢順は保持して比較する。

| 方式 | 分類 /176 | 結合 /78 | 重点行 /14 | 記号選択肢 /16 |
| --- | ---: | ---: | ---: | ---: |
| B′ 保存応答再生 | 146 | 7 | 10 | 12 |
| D 保存応答再生 | 150 | 63 | 14 | 16 |

Dの150はresolver方針変更後のオフライン再生値であり、Phase11の実API主結果142を置き換えない。H2の説明セルがignoredとなる一方、T2の保留表に属する説明セルはlabelからunknownとなる。この差も記録し、unknownを成功へ数えない。結合・表・選択肢・意味構造・競合判断は旧保存結果と照合した。

H1の値所有競合、T1の直接ラベルと親の混同は解決していない。応答速度は今回測定していない。Phase11のD中央値11.356秒を今回の測定値として使用しない。採用不合格は維持する。

## 3. 成果物と保持確認

外部成果物は `candidate_snapshot.py`、`expand.py`、`prototype.py`、`prototype/app/editing/manual_compiler.py`、再生・境界・保持確認スクリプトとJSON。`implementation-manifest.json` に実装hash、`baseline-regressions.json` に修正前の再現、`replay.json` に20試行、`boundary-checks.json` に63項目を記録した。

Phase11のappコピーからの追加変更はmanual compilerだけ。geometry候補builderとv3 schemaは同一hash。Phase11までに導入済みの共通command/record関連差分を製品へ持ち込んでいない。repository変更は本書、AGENTS、Safety Contractの現在地のみ。

保持確認の正本は外部 `before.json`、`after.json`、`preservation.json`。両通常DBの全37表・schema、HEAD、API台帳、通常863ファイルと781 artifact SHA、Phase11の536ファイルとPhase12証拠を開始時と照合する。通常editing/解析は無効のまま、通常DB未移行。`git diff --check` を実行する。

OCR provider/prompt・mapping/crop・物理identity・受付snapshot・補正revision・確定/出力の製品実行コードを変更しておらず、**既存OCR contractを維持した**。実HTTP・DB保存・再取得・undo・実画面・マイク・未知帳票・frontend build・重い全体回帰は今回未実施。過去の全件テスト失敗の解消も主張しない。

## 4. 次工程

フェーズ13はここで終了し、モデル切替後の指示を待つ。次は **フェーズ14：Astra / XHigh、API0回**。出力の重複宣言、通常フォームと表、直接ラベルと親、context方針を整理し、高速化する出力契約と測定条件を確定する。

表選択は任意を維持し、選択範囲と未選択ページ全体を別条件で評価する。部分JSONを直接反映しない。最初の検証済み案・全範囲完了・品質・API数を分けて測る。フェーズ15の限定比較は計画上限24回、16は採用判定。製品接続は採用後に判断する。

連続音声訂正、直前対象の保持、局所録音、手動枠作成、複数セルAI枠と移動/スクロール/表題overlayの要件は変更していない。テンプレートの「案を反映」「確認して保存」と、音声による値編集の即時保存を混同しない。
