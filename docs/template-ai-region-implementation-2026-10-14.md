# 修正6-B：領域単位解析・途中反映の実装と隔離検証

2026-10-14 JST。GPT-6.1 Sol / High。**製品実装・隔離検証・制御応答によるChrome受入を完了。改善ゴールは未完了。**

対象は `C:\Users\hanam\Desktop\llm-ocr-deploy`。
[6-A設計](template-ai-input-region-design-2026-10-14.md)と[表外領域の補足](template-ai-region-evaluation-inputs-2026-10-14.md)を実装した。
外部APIは使用していない。Lunaの意味精度、実画面5秒、独立審査、最終未使用4帳票の評価は未検証。

## 実装

- `region_plan.py`：ページ全域の所有領域と既存セルの一意所有、最大16領域、既存relation/結合/record sectionの依存を計画する。規則的なgridは表だけを所有し、周囲の未検出領域を別に残す。
- 繰り返す行配置を依存のヒントとして集約する。意味上の表と断定する処理ではない。不規則な表を小さな別表へ自動分断するのを避けるため、R02 p1は257セル、R03 p1の明細は232セルを一つの範囲として保持する。24セルは目安であり安全・意味上の依存より優先しない。大きな範囲の速度は未検証。
- `region_contracts.py`：`region.v1`の回答でinput、labels、axisを明示する。b候補を回答に使わず、発行した接続候補へ共通compilerで検査する。見出しなしの根拠、独立値、未検出位置のbbox、manual_required/cross_regionを扱う。
- `region_pack.py`：原位置の無加工cropと参照番号付きのコピーを分け、印字を隠さない原画像も送る。接続候補の印字部分をcropへ含める。検出セル外の印字帯を最大6枚の読み取り専用context画像として追加し、その原座標を示す。文字認識や意味推測で凡例を捏造しない。
- `region_compile.py`：領域のgeometryと意味を一括検証・反映する。新規addは所有する未検出領域に限定し、既存枠の端が分割境界へ1〜2pxまたがる場合も座標を変更せず保持する。他の枠との衝突は既存GeometryCompilerで再検査する。
- `visual_runner.py` / repository：完成応答を検証した領域から公開する。実行中にも案を取得・反映できる。途中JSONや空案を操作可能な案に数えない。
- `merge_chain.py` / service：親receiptの直線的な履歴、領域ごとの内容識別子、二重反映、手動facetを照合する。既適用AIの枠変更は履歴から認め、手動枠変更は旧案を失効させる。最終挿入を既存のBEGIN IMMEDIATE transaction内で再検査する。
- 共通authoring保存：反映履歴を照合し、保存したrunを閉じる。正規ID発行、意味・保存先、crop、revisionの共通経路を使用する。別のDB保存系・OCR系は追加していない。
- frontend：解析中の案反映、末尾反映の取り消しと再送、後着応答の破棄、未検出位置の表示、選択又はviewportに基づく優先範囲を接続した。描画後のrendered/actionable、適用・保存のperformance marksを追加した。これらの存在は実Lunaの5秒達成を意味しない。

## 6-Aから具体化したAPI境界

HTTP/SSEの本文は `template-analysis.v3`。URLは既存editing配下を維持する。
生成schemaは `docs/contracts/template-analysis-v3`、frontendの生成型も更新した。
旧解析snapshot・旧復旧journalは新定義として再解釈せず、手動下書きを保持して再解析する。
既存テンプレート・通常OCR jobの保存形式は移行しない。

反映要求はrequestId、binding、draft、selectedUnitIds、unitDigests、parentMergeIdを使う。
6-Aで記載したinputDraftDigestは**サーバーのreceiptへ記録する値**とし、クライアント計算を必須にしない。
全体proposalDigestも反映要求に不要とし、既に公開した領域案を後続領域の公開だけで失効させない。
同じrequestIdの内容変更は既存の要求fingerprintで拒否する。

`POST .../template-analyses/plan`は、予約・送信・解析runを作らず、所有とdraftを照合して領域のbounds/セル一覧を返す。
作成要求の任意`regionScope`には、その計画と完全一致する一領域だけを指定できる。途中の任意矩形を勝手に新しい所有範囲にしない。
限定解析は結果と画面に明示し、全ページ完了とは扱わない。通常GUIはregionScopeなしで全領域を対象にする。
6-Aの先行4送信を、全ページの自動送信へ広げずに実施するための接続である。

理由：`1e-7`と`1e-07`、`1`と`1.0`はJSONの同じ数値を表せるが、独自の文字列ハッシュでは差が生じた。
反例は証拠フォルダーの`cross-sdk-counterexamples.json`。
解析内部のphysical checksumに`physical-values.v1`を導入し、整数と等価な小数表記だけを揃える。
値・座標・IDを丸めたり、通常保存値を書き換えたりする処理ではない。旧snapshotは明示失効する。
これにより、呼出し側にPython固有のJSON表記・ハッシュ実装を要求しない。

## 検証と証拠

証拠：`D:\llm-ocr-archive\template-ai\20261014-region-implementation`。
API / service / provider parser / 予約hook / 共通保存は実コード、DBは専用SQLite。
有料HTTPだけを制御し、外部ソケット接続を拒否した。制御応答はLunaの品質や速度の証拠ではない。

| 検証 | 結果 |
| --- | --- |
| R02 p2 実ASGI | 実行中の公開・反映、内容識別子の差替拒否、同値手修正、手動geometry、履歴fork、取り消し再送、順次反映・明示保存・別接続再読込を確認 |
| 点検表の保存対応 | 正解選択fixtureで22入力先、54セルのID/位置を保持。モデルによる新しい正解選択は未実測 |
| 2領域のgeometry 実ASGI | 各領域のresize/addを順次反映し、先に追加した枠のID/targetPathと、その後の無関係な手動訂正を保持。48既存セル+2新規枠、4mappingを保存・再読込 |
| JavaScript相当の数値表記 | integral floatをJSON整数へ表すクライアント相当入力でも、正当なAI反映を失効させず保持 |
| 未検出ページ 実ASGI | 検出0セルから開始、bbox付きmanual_requiredを重複せず公開。値の保存なし。旧v2本文を予約前に拒否 |
| 5ページの新規受付計画 | 全セルの単一所有と全ページ面積の被覆。R01/R02 p1/R02 p2/R03/R04は5/5/4/5/6要求。表外領域を含む。送信0 |
| frontend | 全158件と`npm run build`が通過 |
| backend静的検査 | 関係17ファイルのmypyと対象ruffが通過 |

対象の古い実API応答にある意味の誤りは、形式・所有権の検査だけでは全て拒否できない。
今回の正解fixtureの成功を、既知の逆転・混入がLunaで解消した証拠にしない。
R04の145位置/143機能/手動2領域は未変更であり、新しい実API出力の採点はまだ行っていない。

検証中の失敗：geometry検証用scriptの任意fieldLabelへの直接アクセス、計画probeの二重transaction、
新しい型宣言の不足を切り分けて修正した。期待値の削減、検証のskip、goldの変更は行っていない。
全体backend unit/E2Eを実行したという報告ではない。現行Safety §8に従い短い隔離APIと対応比較を使用した。

## Chrome受入（2026-10-14追記）

起動スクリプトの実行をユーザーが明示許可した後、8076/5276を起動した。実行PID・親PID・実行パス、HTTP health、バックエンド読込コードの全ハッシュ一致を確認した。過去の拒否操作の迂回による起動ではない。

実frontend・HTTP・専用DBを使用した。有料HTTPは制御応答であり、外部へのsocket接続は遮断している。通常データでの操作と実API送信は行っていない。

- 最初のセッションで全4案の明示反映と取り消しを確認した。最初の反映操作は全体完了後だったため、解析途中反映の証拠には数えない。
- 次の2セッションでは、画面が「解析中・2/4回」の時点で2案を下書きへ反映でき、後続の案も表示された。
- 印字の文字候補を変更したセッションでは、旧解析案の追加反映を409で拒否した。現行physical checksumは文字候補も解析根拠として保持するため、再解析が必要である。印字変更を意味だけの変更として扱った試験ではない。
- 保存受入セッションでは、先に反映した記号欄の候補を手動で`○◯`へ変更した。その後の別領域2案の反映、末尾反映の取り消し、再反映でも、この手動値を保持した。
- 「確認して保存」から共通保存を実行し、revision 1→2、正しい22入力先・22保存先、54セルのID/位置を確認した。各欄の表示名は保存先catalogと照合し、mappingのtargetPath・矩形位置を照合した。記号mappingの内部labelは既存共通compilerのfield keyであり、表示名の正本とは区別した。
- ブラウザを再読込してテンプレートを再度開き、22/22設定と手動記号を確認した。保存直後と再読込後のHTTP authoring応答は完全一致した。保存済み解析runはstale、active receiptは2件、取り消したreceiptは1件である。
- 受入セッションのConsoleにerror/warnはなかった。

証拠は`ui-acceptance.json`、`ui-receipts.json`、`ui-authoring-saved.json`、`ui_http_reloaded.json`、`ui-reloaded.png`と各画面・通信・計時記録。`verify_ui.py`が、固定入力先と正解fixtureの各名称・保存先・矩形、履歴、再読込、手動保持を検査する。

有料通信を固定した検証であるため、Lunaの意味精度や5秒の達成を主張しない。また、現在の`actionable` markは案のチェック選択後に記録される。人間・試験操作の選択待ちをシステム処理時間へ混入させない計測判定を、6-Cで点検してから6-Dへ進む。検証中の保存先labelに関する一度のassertion失敗は、共通compilerの内部field keyと表示名catalogを混同した試験側の誤りであり、正本を照合する検証へ修正した。受入条件・goldは削減していない。

## 残作業と停止境界

6-BはChromeで途中反映、取り消し、手修正、明示保存・再読込と計時markを確認して完了した。
専用8076/5276は、ユーザーの明示的な実行許可を得て起動済み。
起動用：`D:\llm-ocr-archive\template-ai\20261014-region-implementation\start-runtime.ps1`。
制御providerの専用DBだけを使う。通常データや実マイクは使用しない。

6-Bの完了証拠は`completion-1`へ別途封印し、旧`checkpoint-1`を保持する。次は6-C=Astra/XHighへ切り替えて独立審査する。
新規別エージェントの起動は未許可。6-D=Astra/Highの実Luna・実画面比較へ先行しない。
新実験最大96送信・先行4件停止条件は維持し、旧未送信23試行は使用しない。
ゴールの速度・品質条件が達成されるまで、改善ゴールを完了にしない。
