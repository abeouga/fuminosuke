# AIテンプレート作成 フェーズ9：比較試作・採用基準不合格

2026-10-11。開発対象は `C:/Users/hanam/Desktop/llm-ocr-deploy`、指定開発モデルはGPT-6.1 Sol / High。
[フェーズ8設計](template-ai-phase8-design-2026-10-11.md)に従い、製品外で共通APIの試作と3方式の比較を実行した。
**比較試作は終了。B/Cとも採用基準を満たさず、製品組込には進まない。**
実APIは **30回**。共有台帳 **292/496、残204回**。今回上限36回の予備6回は使用しなかった。
製品コード・通常業務DB・通常設定は変更していない。通常DBのmigrationや解析/編集機能の有効化も行っていない。

## 比較条件と成果物

成果物は `D:/llm-ocr-archive/template-ai/20261011-phase9`。両repositoryのHEAD、dirty worktree、通常DB全表を開始時に記録し、deploy DBを退避した。
現行appを `prototype/app` へコピーし、そこでだけ共通DTO/意味結合/表編集/manual compilerを拡張した。実際のcompilerを使うin-memory dry-runであり、HTTPや保存transactionの代替検証ではない。

| 方式 | 内容 |
| --- | --- |
| A | フェーズ2の旧Responses契約・旧validatorを保持 |
| B | v2意味構造＋全セル必須のroleByCell。辞書と構造の矛盾を拒否/保留 |
| C | v2意味構造からセル分類を導出。未出力はunknown（第一候補） |

- 既存の固定5帳票・459物理セル、各2反復、計30送信。重点分母は分類176・結合78・行14。
- モデルは全送信 `gpt-6-luna`、reasoning=low、画像detail=high、streaming、出力上限24576、timeout 60秒。自動再試行・別モデルへの切替なし。
- 1巡目A→B→C、2巡目C→B→A。同時送信なし。B/Cは同じ画像・入力・意味表現で、全数分類要求のみを追加/除去した。
- B/Cの短い参照は `(y,x,physicalId)` 順。新規セルはunreviewedとし、初期ignoredを送らない。正解、保存済み意味、元ファイルパスは送信しない。
- 画像/入力/正解SHA、prompt/schema、比較コードとコピー内appのSHA、順序、採点基準を送信前に固定した。30要求を後から再構成し、固定要求と送信時SHAの一致を照合した。
- 正解は変更していない。T3/H2は模擬帳票、H1/H2も既知の回帰帳票であり、未知帳票や新しいholdoutではない。
- 自然なprompt cacheを使用した。cached input tokenはA 49,720、B 48,274、C 47,889。応答時間の差を単一要因の因果効果とは扱わない。

正本は `protocol.json`、`manifest.json`、`frozen-requests/`、`sends.json`、`raw/`、`decoded/`、`normalized/`、`compiled/`、`proposals/`、`scores/`、`analysis.json`。
`request-audit.json` に要求・モデル・HTTP・usage照合を記録した。30件すべてHTTP200、API側のschema拒否は0件だった。

## 主比較の結果

拒否、保留、欠落も分母に残した。保留した単位を手で修理して主比較へ戻していない。
B/Cは共通compilerまで通した出力から採点する。Aは旧validatorの通過であり、同じ保存品質を保証しない。

| 指標 | A：旧方式 | B：全数辞書＋構造 | C：構造から分類 |
| --- | ---: | ---: | ---: |
| 試行 | 10 | 10 | 10 |
| 検証通過 | 8 | 5 | 9 |
| 意味案が残った試行 | 8（旧検証） | 5 | 6 |
| 全試行の終了時間中央値 | 11.219秒 | 18.449秒 | 15.555秒 |
| 終了時間の最小–最大 | 5.252–34.388秒 | 4.712–28.681秒 | 5.402–27.085秒 |
| 意味案が残った試行だけの中央値 | 8.235秒 | 7.348秒 | 8.648秒 |
| 10.138秒以内に意味案あり | 4/10 | 4/10 | 4/10 |
| 最初のprovider SSE中央値 | 0.980秒 | 0.954秒 | 1.103秒 |
| 重点分類 | 112/176（63.6%） | 57/176（32.4%） | 77/176（43.8%） |
| 重点結合 | 6/78（7.7%） | 14/78（17.9%） | 11/78（14.1%） |
| 重点行順 | 8/14 | 0/14 | 4/14 |
| 記号/チェックの選択肢一致 | 0/16 | 12/16 | 16/16 |
| effectiveUnresolved合計 | 381 | 754 | 660 |

記号選択肢の追加指標は既存goldにあるT3/H2各4欄×2反復の16欄。文字列と順序を比較した。
effectiveUnresolvedは未判断に加え、意味結合のないinput/markと拒否試行の全セルを含む診断量。全セルの独立した正解率ではない。
重点結合の内訳は、A=正解6/欠落42/存在するが不一致30、B=14/59/5、C=11/51/16。
欠落と誤った関連を混同せず、OCRの文字認識誤りとしても数えない。

**Cの9件通過を9件の成功とは数えない。** T1-C-1は分類のみ、T2-C-1/2は採用単位なしだった。
意味案ありの6件だけなら8.648秒だが、失敗・空の案を含む中央値は15.555秒。今回のAより遅く、速度改善を主張できない。
過去フェーズ7のA=148分類/45結合から、今回A=112/6へも低下した。同じ既知入力でも非決定性があり、少数試行から性能保証やp95は推定しない。

## 採用条件との照合

| 条件 | B | C |
| --- | --- | --- |
| 分類148/176以上 | 不合格 | 不合格 |
| 結合45/78以上 | 不合格 | 不合格 |
| 行14/14 | 不合格 | 不合格 |
| 意味案8/10以上 | 5/10、不合格 | 6/10、不合格 |
| T2の両反復で3行・21結合 | 不合格 | 不合格 |
| H2の両反復で4行・4結合・記号設定 | 不合格 | 不合格 |
| 意味案ありの中央値10.138秒以下 | 数字上は到達 | 数字上は到達 |

品質を満たした上で速度を評価する条件なので、速度欄だけでは採用できない。
CのH2-1は4結合が正しいが表の行構造なし、H2-2は4行が正しいが結合0/4。正しい断片を別試行から合成して成功にしない。
AもT2両反復で拒否され、記号設定を出せない。旧方式へ無条件に戻す判断も成立しない。

## 原因の切り分け

`diagnostics.json` に保存出力・単位の拒否理由・compilerの原因例外を記録した。

1. **生成された意味構造の重複と衝突。** CのT1初回は91 fieldsを出したが、見出し/役割/親の衝突で多くが保留になった。CのT2初回は3表を出したが、値所有の重複や分類衝突を含み、独立した正常単位が残らなかった。分類生成の重複を減らしても、意味構造自体の整合性は改善が必要である。
2. **B試作の保守的なページ拒否。** T1-B-1、T2-B-1/2は、roleByCellだけに非unknown分類があり、構造/standaloneに対応しないため `KEYED_UNBOUND_CLASSIFICATION` で全ページ拒否した。この実装判断は部分成功の範囲に影響する。Bの成績をすべてモデル能力に帰責せず、次審査でセル単位保留との整合を再検討する。主比較中には変更していない。
3. **設計schemaと現行checklist契約の不一致。** H1-B-1/2、H1-C-2はchecklistをsingleとして出力し、現行LayoutDefinitionの「checklistはmultiple必須」により全体compilerで拒否された。フェーズ8のsingle/multipleを許す設計に不足があった。実装前にprovider schemaと単位validatorの双方へ現行制約を反映する必要がある。
4. **手動保存用adapterの不足。** 欄全体のmarkを先にstageへ仮登録すると、その後のrole変更と衝突した。また第1行の複合見出しを列共通ラベルにすると、複数列で行見出しが重複する。表現可能な共通APIを作るだけでは、既存manual-authoringへの接続は完成しない。

分類の確信度閾値を下げたり、不正な関連を採用する修正は行っていない。
これらにはモデル出力の不備と、設計/試作側の不備が混在するため、どちらか一方だけが原因とは結論しない。

## 保存応答だけの追加検証

実比較の `prototype/` はSHAを固定したまま保持し、追加修正は `secondary/` へ別コピーした。追加APIは0回。

- 欄全体のsymbol/checklistは既存領域の仮登録をせず、共通mark.configureに生成を任せた。
- manual compilerは各行のcellLabelCellsを明示し、列共通ラベルへ行見出しを混ぜない。
- 通常表、1列記号表のmanual compiler経由変換を確認。さらに2行×2列・共有行見出し・記号4欄の例で、ラベルと物理geometryの保持を確認した。
- checklist singleをその欄/表の保留理由にする局所guardを、保存済み20応答へreplayした。H1-B-1/2、H1-C-2の3件は残る独立単位をcompilerへ渡せた。
- 主比較の成績は差し替えていない。追加replayは新しいモデル比較、速度改善、実保存受入ではない。T2の表の不足は残る。

`manual-audit.json` が修正前の失敗、`secondary/manual-audit.json` と `extended-audit.json` が追加コピーの確認結果。
`secondary/checklist-replay.json` と `replay-normalizer.py.txt` に局所guardと再検証を残した。
これは今後のschema修正の根拠であり、現行製品のchecklist契約をsingleへ拡張する提案ではない。

旧Aの8通過案も、保存応答を決定的に共通操作へ変換する追加検証では1件だけがcompilerを通過した。他7件はmarkの選択肢設定を旧契約で表現できなかった。
この追加adapterの成立率を旧Aの本来の精度へ読み替えず、旧validator通過8件が保存成功8件を意味しない根拠として扱う。

## 検証と変更境界

| 確認 | 結果・限界 |
| --- | --- |
| baseline_probe.py | 元のDTOは行別matrixを拒否、元のMeaningBindは一意markでも拒否することを再現 |
| offline.py | 27境界確認。未知ref、重複所有、独立欄、表幅、親の伝播、保護値、記号、行別ラベル、既存relation ID/targetPath保持 |
| benchmark.py --freeze / benchmark.py | 固定30送信。予備・自動再試行なし。全要求のAPI schema受理を確認 |
| analyze.py / diagnose.py | 凍結goldと保存応答を採点、原因例外を再現。gold更新なし |
| request_audit.py | 30要求のSHA・固定body・model・HTTP200・usage照合 |
| secondaryのmanual/局所guard検証 | 上記の限定変換と保存応答replay。実保存やHTTP受入ではない |
| 対象Python構文 | 7ファイルを構文検査。全体型検査や全件回帰ではない |
| finish_audit.py / git diff --check | 両HEAD、製品ソース、通常業務全表、既存台帳262行、schema、通常data/artifact、通常機能無効を照合 |

製品repositoryで変更したのはこの報告、AGENTS、Safety Contractの現在地だけ。試作コードとテスト用scriptはDドライブの製品外に保存した。
通常DBへの書込みは共有API台帳の30予約のみ。正常/失敗で払い戻さず、送信IDと追加30行が一対一に対応することを確認した。
通常data 863ファイル、781 artifactのSHAも照合した。両HEAD・製品ソース・業務テーブル・schema・既存台帳262行を保持し、通常機能は無効のままである。
物理セル/geometryの保持は、B/Cの検証通過14試行と限定例で確認した。14試行のうち案なし2件は無操作で、実際に共通compilerへ操作を渡したのは12試行。意味の関連が正しいこととは別で、上記の結合誤りを隠さない。
既存OCR contractを維持した。OCR provider/prompt/model、mapping/crop、受付snapshot、補正revision、確定・XLSX出力対応は変更していない。
今回はOCR値の読取を実行しておらず、文字認識のみの誤りや実OCRのassociation精度は未測定である。
実HTTP、frontend build/画面、保存transaction、再読込、undo、実マイク、人の修正時間は今回未検証。重い全件回帰も実行していない。
以前のbackend23失敗/34収集エラーを今回解消したとは扱わない。通常利用受入は未完了である。

## 次工程

次は **フェーズ10：Astra / XHighによる採用審査・設計の再評価**。原則API0回で保存応答と試作を点検する。
現時点の判定は「採用方式なし」。フェーズ11の製品実装へ直行しない。
審査では、役割辞書のみのセルの保留範囲、checklistのschema制約、親/値所有の局所検証、手動保存adapterを確認する。
その上で、全ページ一括推論を維持できるか、既存の空間構造で表候補を先に固定してAIへ渡す必要があるかを判断する。
これは次審査の論点であり、新たな方式や追加API実験を今回の結果だけで確定しない。

公式仕様の確認先（2026-10-11）：[GPT-6 Luna](https://developers.openai.com/api/docs/models/gpt-6-luna)、[Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs)。
APIによるschema受理と、編集の意味的な正しさを区別する。
