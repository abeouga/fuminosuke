# AIテンプレート作成 フェーズ7：実帳票の比較測定・受入不合格

2026-10-11訂正：保存応答を再照合し、全セルignoredは3件から2件へ訂正した。追加診断の最初の分類全数検査での拒否は6件中4件で、1応答は表を3件出力していた。以下の該当記述を修正した。主比較の精度・時間集計と受入不合格は変わらない。後続の設計は[フェーズ8](template-ai-phase8-design-2026-10-11.md)を参照する。

フェーズ3設計のフェーズ7（指定開発モデル：GPT-6.1 Sol / High）を実行した。
比較測定は終了したが、品質条件を満たさず、利用者の手直し・保存を含む受入は未完了である。通常環境への導入を停止する。
製品コードは変更していない。通常DBの移行・通常editing/解析の有効化も行っていない。

## 比較条件

開発対象は `C:/Users/hanam/Desktop/llm-ocr-deploy`。試験成果物は `D:/llm-ocr-archive/template-ai/20261010-phase7`。
開始時に両repositoryのHEAD・ソース・通常DB全表の論理hashを記録し、deploy DBとstorageを専用コピーへ退避した。
コピーだけをmigrationし、実backend 8037 / frontend 5207 / Chromeを使用した。

- 凍結した5帳票、計459物理セル。T1=167、T2=185、T3=12、H1=74、H2=21セル。T1/T2/H1は実帳票、T3/H2は模擬帳票。
- 重点正解は1反復あたり88分類・39結合・7行。2反復の分母は176・78・14。H2は既存manifestの `H2-valid` を使用し、期待値のSHAを照合した。
- 旧全体Responses、新製品graph Responses、製品の種類限定Decisionsを各帳票2反復、計30送信。1巡目は旧→新、2巡目は新→旧、種類限定はその後に測定した。
- APIモデルはすべて `gpt-6-luna`、reasoning=low、画像detail=high。全体Responsesは両方式ともstreaming、上限24576出力token、timeout 60秒、再試行なし。同じ画像・幾何・印字・物理IDを使用した。
- 旧方式のprompt/schemaはフェーズ2、対照の新方式は現製品provider/schema/validatorを使用した。新方式のcompact refは物理ID順。新規検出を模して未確認role=ignored、lock=false、既存意味なしとした。
- ベンチマークの新入力は製品相当だが完全同一ではない。全5帳票に非空verticesはなく、benchのproviderCellsは製品が送る `vertices:[]` を省略している。実画面の1回は製品snapshotと実providerをそのまま使用した。
- 正解は要求に含めず、取得後に採点した。表の明示行列をgold非依存のcanonical形式へ展開してから結合と行順を比較した。

`protocol.json`、`manifest.json`、`offline.json`が固定条件。`benchmark.py`、`runs.json`、`requests/`、`raw/`、`decoded/`、`graphs/`、`proposals/`、`scores/`、`analysis.json`が再確認用の証拠である。
これらは既知帳票の回帰比較であり、未知帳票への一般化や全459セルの精度は主張しない。

## 実測結果

失敗試行も分母と時間に含めた。検証拒否された案は利用不能として重点採点では不正解に数え、手で補完していない。
旧validatorと新validatorの厳密さは異なるため、「構造検証通過」は同一の品質保証ではない。

| 指標 | 旧全体Responses | 新全体Responses | 種類限定Decisions |
| --- | ---: | ---: | ---: |
| 試行数 | 10 | 10 | 10 |
| 構造検証通過 | 8/10 | 4/10 | 10/10 |
| 全試行の終了時間中央値 | 15.513秒 | 7.827秒 | 0.939秒 |
| 終了時間の最小–最大 | 5.824–32.313秒 | 5.007–22.437秒 | 0.688–4.024秒 |
| provider最初のSSE event中央値 | 0.998秒 | 1.187秒 | 非streaming |
| 重点分類の正解 | 148/176（84.1%） | 26/176（14.8%） | 108/176（61.4%） |
| 重点結合の正解 | 45/78（57.7%） | 1/78（1.3%） | 対象外 |
| 重点行順の正解 | 14/14 | 0/14 | 対象外 |

新方式の全試行中央値は旧より約49.5%短い。しかし、受入の速度目標10.138秒以下を数字だけで満たしても、品質悪化を伴うため採用条件を満たさない。
provider最初のSSE eventは完成した意味案でも、利用者に表示できる最初の正しい分類でもない。
試行数が少なく、p95や保証値は推定していない。

構造通過試行だけの中央値は旧11.516秒（8件）、新6.281秒（4件）だった。
新の通過4件のうちT1-2、T2-2の2件は全セルをignoredにし、意味・表を作らない案である。これらの短時間応答を実用的な完了とは数えられない。
T3-2はlabel 5、input 1、mark 4、ignored 2で、受付番号のfieldを1件出力したが記号4欄は未対応だった。H2-1も選択欄4件が未対応であり、新方式全体の正しい重点結合は1件に留まった。

Decisionsは速いが、confidence>=0.70で採用された重点122分類に14件の誤りがあった。
採用分類だけの正解率は108/122=88.5%。種類だけの判定であり、意味・行列・保存までの完了時間へ置き換えない。高confidenceを自動反映の正しさの保証に使えない。
`effectiveUnresolved`は拒否時の全セルを含めた診断量で旧389、新541。種類限定で結合がないことは製品欠陥として数えず、全体方式と同じ未解決率にはしない。

## 拒否原因と限定診断

新方式の6件は `PROPOSAL_INVALID` で拒否された。主な観測は分類の欠落・重複、参照先の分類と表/見出しの不整合である。
`rejection-analysis.json`には、保存応答の分類coverageと現validatorが最初に拒否する行番号を記録した。
検証通過は意味の正しさを証明しない一方、不正な関連をそのまま採用するために検証を弱めることもしない。

未確認の初期role=ignoredがモデルを誘導している可能性を調べるため、予備枠で単一変数の診断を事前固定した。
T1/T2/H1を2反復し、roleLocked=falseのproviderCellsからroleだけを除去。画像・座標・印字・instructions・schema・model・reasoning・validatorは変更していない。計6送信。

診断では分類やfieldsの出力は増えたが、6件すべてが拒否された。最初の拒否箇所は4件で `graph.py:41` の分類全数検査、diagnostic-T2-1で93行の見出し/分類検査、diagnostic-H1-1で85行の所有関係/分類検査だった。diagnostic-T2-1はfields 7件・表3件を出力したが、値セル重複1件と構造上の分類衝突26件を含んでいた。他5件の表は0件。
これは初期roleによる誘導仮説と整合する観測だが、少数の非決定的な試行から原因を確定したり、roleを消せば解決すると結論することはできない。
診断応答を修理して主比較に混ぜず、製品のpromptや契約も変更していない。
`diagnostic-protocol.json`、`diagnostic-results.json`、`diagnostic-run.log`を保存した。

診断準備の初回は作業directoryに依存した相対DB URLを使い、旧repository直下の既存DBで台帳検索が失敗した。SQLAlchemy OperationalError（code=e3q8）で、予約前・送信前に停止し、API使用0回。
その既存DBを削除・移行せず、診断スクリプトだけ絶対DB URLへ修正した。`diagnostic-preflight-error.json`とlog、台帳IDのないsends entryを保存している。
次の集計ではOperationalErrorの型名とSQLAlchemy codeの取り違えも修正した。いずれも試験側の失敗で、製品修正や有料再試行とは区別する。

## 実画面の1送信と測定の限界

専用コピーのH2を新規未確認templateとして開き、「AIで意味付け」を操作した。実provider・実HTTP・実SQLite/storageを使用した。固定応答への置換はない。

| 区間 | 実測 |
| --- | ---: |
| click→202受付応答 | 275.1ms |
| POST fetch開始→202応答 | 48.4ms |
| provider開始→最初のSSE event | 1,215.9ms |
| provider開始→完了 | 7,999.0ms |
| click→検証済み案の表示 | 8,470.3ms |

画面には16分類単位、未解決4件、保護0件が表示された。意味結合・表構造は作られず、全体テンプレート作成の成功ではない。
案の確認中は音声と自動保存が保留され、「まだ保存していません」と表示された。
反映/保存は操作していない。実コピーDBでもmerge=0、元の21セルはすべてignored、関連・表ともなしを確認した。
人による確認・修正回数・保存完了時間は未測定。分類と構造の品質条件を満たさないため、利用者の受入作業と実マイク試験を開始していない。

画面計測コードにはSSEもJSONとしてclone読取する誤りがあり、非同期JSON parse errorを出した。追加の確認GETも誤ったURLで404を出した。証拠取得の誤りであり、製品の正常ログとは扱わない。
同じ画面で `ERR_NO_BUFFER_SPACE` も記録された。この原因は計測のstream cloneを含め未確定で、製品SSEに問題がないとは断定できない。
上記UI時間は計測器付き1試行の観測値で、200ms受付目標の達成やブラウザE2E全成功を主張しない。主比較30件はこのブラウザ計測器を使用していない。
再読込後の画面にはconsole error/warnはなかったが、ホーム画面での観測なので編集画面/SSEの再受入ではない。
`browser-evidence.json`、`browser-snapshot.json`、`browser-console.json`、`browser-screenshot.jpg`、`ui-analysis-db.json`、`ui-result.json`、`ui-responses/`を保存した。

## 検証と保全

| 実行・確認 | 結果 |
| --- | --- |
| `benchmark.py --offline` | 凍結画像/入力/正解SHA、5帳票の参照対応、schemaを確認。外部APIなし |
| `benchmark.py`、`score.py` | 実30送信、固定重点採点。主比較の集計は診断を混ぜず保持 |
| `diagnostic.py` | 実6送信。全拒否、製品への採用なし |
| 専用Chrome | 実1送信、案表示・自動保存保留・DB未反映を確認。計測器のエラーと限界は上記のとおり |
| `final_analysis.py` | 主比較30＋診断6＋画面1の台帳IDが相異なること、通常台帳の追加37行との一致を確認。送信前失敗1件を除外 |
| `finish_audit.py` | 両HEAD・旧ソース・通常業務DB不変、deploy DB schema不変、既存台帳225行不変。通常863ファイル・781 artifactのSHA一致 |
| `git diff --check`、開始/終了status | 差分検査成功。製品コード変更なし、記録3ファイルのみ。既存の未コミット変更を保持 |
| 終了処理 | 専用backendを正常shutdown、frontendと専用Chrome tabを終了。8037/5207のlistenerなし、backend PID消失を確認 |

通常DBに加えた変更は共有API台帳の37予約だけ。通常DBのmigration、業務layout/OCR結果/補正値/出力の変更はない。
今回の実測のためにユニットテストや全体回帰を再実行していない。製品コードは同一で、フェーズ6の143 frontend成功/build、backend既存23失敗/34収集エラーを今回の新しい成功証拠へ読み替えない。
既存OCR contractを維持した。provider/prompt/model/schema、物理identity、mapping/crop、受付snapshot、revisionと出力bindingは今回変更していない。
OCR安全性を完全には検証できていない。全体回帰の既存失敗、実利用の手直し・保存、未知帳票の品質、実マイクは未受入である。

API使用は **37回（主比較30、診断6、実画面1）**。共有台帳 **262/496、残234**。フェーズ初回上限40以内で終了した。
製品用とOCR用のAPIモデル設定は変更していない。APIキーや画像をrepositoryへ追加していない。

## 判定と次工程

**受入不合格。通常導入は停止。** 旧方式も完全ではなく、旧方式へ無条件に戻せば解決するとは言えない。
現在のボトルネックは輸送の遅さだけでなく、Lunaに全数分類・意味結合・表構造を一括生成させる出力契約の成立率と、無内容の案を実用完了と区別する品質評価である。

次は **Astra / XHighで修正設計**を行う段階とする。全数分類の表現、初期値の伝え方、分類と関連推論の責任分担、部分成功の扱いを、速度・品質・API回数の制約付きで比較する。
検証の弱体化、自動再送追加、無断のAPIモデル変更、通常DB開放は行わない。
ページ全拒否からセル単位救済への変更や出力契約の再編は大きな仕様変更に該当し得るため、この工程では実装を開始しない。具体的な設計と変更境界を次のモデルで確定してから進める。

公式仕様の確認先（2026-10-10）：[GPT-6 Luna](https://developers.openai.com/api/docs/models/gpt-6-luna)、[Decisions](https://developers.openai.com/api/docs/guides/decisions)。採用判断は上記の実測に基づく。
