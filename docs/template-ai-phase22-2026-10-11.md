# AIテンプレート作成 フェーズ22：候補選択の契約・成立性試作

2026-10-11。**製品外で候補生成と選択境界を実装した。成立性は4/5scopeで不合格。H2の構造仮説は52通り、保留2候補込み54通りとなり、事前固定した32候補上限を超えた。有料比較・製品接続へ進めない。**

4scopeの一致は正解参照から候補を選んだ結果であり、新しいAIの精度・応答速度ではない。API0回、台帳366/496・残130回を保持した。

## 1. 実施範囲

作業対象は `C:\Users\hanam\Desktop\llm-ocr-deploy`。開発役の指定はGPT-6.1 Sol / High。前段の[Phase21審査](template-ai-phase21-review-2026-10-11.md)に従い、製品外 `D:\llm-ocr-archive\template-ai\20261011-phase22` で試作した。

実装前の `plan.json` に、入力・名前導出・候補上限・5scopeすべての意味投影一致・失敗時停止を記載した。generatorは物理snapshot、固定scope、印字evidenceだけを受け取り、gold・case名・保存応答・正解layout・帳票固有辞書を参照しない。goldは評価側だけで使用した。

[v6試作契約](contracts/template-analysis-v6/README.md)と[選択projection schema](contracts/template-analysis-v6/selection-projection.schema.json)を追加した。既存のPhase19 runtime・v5契約・30応答・gold・採点・計測値は変更しない。新しい矩形やOCR経路、製品HTTP/DB/UIは実装していない。

## 2. 実装した責任分担

| 処理 | 今回の実装 |
| --- | --- |
| 候補生成 | 既存gridから列用途・親・行識別・読取の組合せを生成。単体の横隣接欄は独立部品に分割。意味は未確定の仮説として保持 |
| 依存と上限 | 同じ表と交差する所有範囲を一単位にまとめる。32候補込みの固定上限を超えた単位は全体保留し、候補を切り捨てない |
| 事前検査 | printed keyの空／重複を除外し、markの根拠と参照、全claims、親・role・値所有、手動facet、scopeを共通resolver/compilerで検査 |
| 選択境界 | immutableな候補集合とsnapshot/scope/evidence/catalog/手動facet/source/schemaを束縛。未発行選択や古い状態を拒否 |
| 合成 | 独立した回答も最後に一つの案として再検証。競合は先勝ちにせず拒否。unknown・保留のcoverageを保持 |
| 投影 | v5 DTOを明示的に使い、共通semantic commands/compiler/manualへ渡す。選択providerは保存を行わない |

具体案を発行できない単位にも `none_of_these` と `needs_context` を残す。全部保留なら元layoutと空operations、一部だけ解決すれば未解決refs付きpartial draftを返す。

初回の試作実行では、内部hash形式のmark IDが旧v5のcompact参照形式に合わずschema拒否された。共有evidence全体から安定した `m1...` を割り当て、独立欄を合成しても別定義が衝突しないよう修正した。旧schema・期待意味・候補上限は変更していない。

## 3. 候補coverageの結果

候補ごとに受理role・実relation・親・名前/alias/共有・記号・表・行を検査した。正解に一致する候補を各部品から人為的に選び、合成した案がPhase19で送信前に固定したmanual意味投影と一致することを確認した。これをAI選択の成功とは数えない。

| scope | 依存単位数 | 発行した具体案数 | 1単位の最大選択肢数・保留込み | 全重点＋manual意味投影 |
| --- | ---: | ---: | ---: | --- |
| T1 | 1 | 5 | 7 | 一致 |
| T2 | 1 | 2 | 4 | 一致 |
| T3 | 7 | 計13 | 4 | 一致 |
| H1 | 1 | 3 | 5 | 一致 |
| H2 | 1 | 0 | 具体案52＋保留2を要するため保留2のみ発行 | 不合格 |

T1/T2/T3/H1では、共通compile→manual投影が一致し、既存graphを渡したno-opも全ID/path込みで保持した。T2の空行・末尾行を落とさず、position識別と7値列を含む正解案が存在した。行識別を自動採用する処理は作っていない。

H2は、全印字の左右列にvalue/text、value/symbol、row_label、context、printed keyの各仮説があり、中央列にtext/symbolの2仮説がある。列の組合せは5×2×5=50から両側keyの不正2組を除いた48、formの競合4組を加えて52となる。**これは共通compileへ発行する前の列挙数**であり、52案すべての意味正解や共通compile成功を測ったものではない。現在の列挙方式が上限以内に収まらないという失敗である。

geometryで一見もっともらしい用途だけを残す、候補を先頭30件に切る、H2を比較の分母から外す、上限を後から変える方法で救済していない。H2を15refsの未解決単位として保持し、全保留選択ではlayoutを変更しない。

名称にも未解決の設計条件がある。現在の規則は、対象表に書込み可能な表題がなければ列見出しを連結する。H2の手作成参照の表名は「設備点検」であり、現在の規則が作る「点検項目 判定記号 確認内容」とは異なる。これは規則と参照の照合による追加所見で、上限を解除して再測定した結果ではない。**候補数だけを増やせば全意味投影が一致するとは言えない。** 自由な名称をどう扱うかも次の判断に含める。

## 4. 短い境界検証

39条件が通過した。主な内容は以下。

- 回答漏れ・重複・未発行question/候補、別モデル、型不正、NaN/boolのconfidence、重複/不正な確率、余分なoperationの拒否。
- 候補集合・scope・pack・snapshotの古いpin、role保護、同じ値でも新しい手動meaning facetの拒否。
- printed keyの空欄、mark定義の欠落/未使用、異なる親の競合、値所有の重複、範囲外role、手動facetを書き換える案の拒否。
- `none_of_these`、`needs_context`、refusal、低confidence、上限超過では保留を維持。独立部品の一部だけ選んだ場合は未選択roleをcanonicalでunknownとして残す。
- 選択肢の並びを変えても内容IDから同じ意味へ解決し、旧集合pinでは拒否する。生成モジュールにファイル・network・gold読取りがないことも確認した。

confidence 0.90・次点差0.15は未校正の試作設定。実モデルへ送信しておらず、閾値の精度を検証していない。要求ファイルは画像hash・根拠・質問候補を含むオフライン仕様で、実endpointへ送れるwire bodyの確認ではない。

保存30応答も別のPhase22出力先へ再生し、26受理・4拒否、最終採点・全計測値・usage・共通/manual投影・既存graph no-opがPhase20/21と一致した。Phase19の411 hashと30固定request bodyを再照合した。

## 5. 応答時間について確認できる範囲

今回のローカルfreeze単回観測はT1 1.204秒、T2 1.557秒、T3 2.462秒、H1 0.464秒。H2の0.022秒は超過で具体案のcompileを行っていないため、成功の速度として比較できない。freezeには候補生成・候補ごとのpack/guard/compileと固定化が含まれる。

これらは開発用試作のローカル観測であり、モデル待ち、ネットワーク、UI描画、保存を含まない。単回値から中央値・一般的な応答時間・5秒達成を主張しない。現状では事前候補検査自体にも待ち時間があり、候補選択方式に変えるだけで高速化できるとの結論は出せない。

## 6. 合否と次の判断

39境界条件と旧30応答再生は通過したが、事前の成立性条件は5/5である。**Phase22の成立性gateは不合格。製品不採用・通常未移行/無効を維持し、実API比較は開始しない。**

次の区切りは**Phase23：Astra / XHigh、API0回で判断単位と名称の扱いを審査する提案**。表全体の完全な組合せを一問にする方針の費用、独立に質問できる列/読取判断とサーバー側の整合検査、候補外の自由名称をResponses/手動へ分離する境界を判断する。今回の失敗を理由に、確率から未選択案を勝手に採用する処理や、幾何規則での意味確定を追加しない。

これは次の自動実装・比較を開始する指示ではない。現行候補集合・上限・gold・採点を保持し、Phase22を完了した時点で停止する。

## 7. 変更・監査・未検証

repository変更は本書、v6試作契約/schema、AGENTSとSafety Contractの現在地追記だけ。製品外の `hypotheses.py`、`choice_runtime.py`、`acceptance.py`、`legacy_replay.py`、監査スクリプトと結果artifactを新設した。

実行した検証は `acceptance.py`、`legacy_replay.py`、`audit_state.py before/after`、artifact検査、`git diff --check`。終了監査で、両checkoutのHEAD、各37表の全行とschema、設定、旧checkout全ファイル、863通常ファイル、781 artifact、Phase11〜21証拠を保持した。API台帳も366/496のまま。staging/commit/pushは行っていない。

製品OCR/provider/prompt/model/schema、physical identity、mapping/association/crop、受付snapshot、補正revision、確定・出力の実装を変更しておらず、**既存OCR contractを維持した。** 製品ソースを変更しない外部試作のため、backend全件・frontend build・E2E全件は実行していない。

実モデルの選択精度/遅延、未知帳票、全ページplanner、矩形生成、実HTTP・DB保存・UI・音声は未検証。今後の利用者参加の実マイク試験が必要になった時点で呼び出す。

主要証拠は外部Phase22の `plan.json`、`coverage.json`、`boundary-results.json`、`review-results.json`、`choices/`、`candidate-layouts/`、`oracle/`、`requests/`、`preservation.json`、`artifact-validation.json`。今回API **0回、累計366/496、残130回**。
