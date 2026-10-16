# AIテンプレート作成フェーズ14：高速化する出力契約と比較条件

2026-10-11。設計工程Astra / XHigh。実行時のAPIモデルは指定どおり **GPT-6 Luna**。対象checkoutは `C:\Users\hanam\Desktop\llm-ocr-deploy`。

**次の比較方式を、圧縮した意味単位を返すv4とする。通常フォームを繰り返し表から明示的に分け、同じ親・読取設定・分類・既知の表示文字の反復出力を減らす。** 製品採用は未確定。今回の成果は設計とオフライン整合確認であり、速度・新しいAI品質の実測ではない。

設計契約は [v4契約](contracts/template-analysis-v4/README.md)、[JSON Schema](contracts/template-analysis-v4/proposal.schema.json)、[provider prompt](contracts/template-analysis-v4/provider-prompt.txt)、[次回の固定プロトコル](contracts/template-analysis-v4/phase15-protocol.json)。外部証拠は `D:\llm-ocr-archive\template-ai\20261011-phase14`。

## 1. 設計の根拠

Phase11のDは準備中央値12.382ms・検証41.504msに対し、モデル応答待ちが約11.312秒。多数の欄を一つずつ返すT1は21.193/24.569秒だった。Phase13の修復は結合・候補束縛・contextを改善したが、この待ち時間を測り直していない。

H1では住所/氏名/電話番号を表と独立fieldの双方へ宣言し、値所有競合が起きた。T1では局所親と直接ラベルの区別が不足する。画像、保存応答、候補builder、normalizer、manual compiler、実製品のsnapshot/merge境界を照合した。

現時点での主な改善対象は、モデルに再生成させる情報量と意味の表現方法である。サーバーの対応検査を省いて速くする根拠はない。[OpenAIの遅延最適化ガイド](https://developers.openai.com/api/docs/guides/latency-optimization)は出力短縮と要求数削減を挙げるが、この帳票での短縮率や5秒達成は実測が必要である。

## 2. 選択肢の評価

以下は3つの専門的観点を模擬した比較であり、別の実在専門家による審査ではない。評価軸は、検証済み案までの時間、意味の保持、実装責任の明確さ、API回数。

専門家A（応答性能）：既知セルの再列挙と設定反復を減らす1要求のv4を優先する。複数要求への分割は最初の案を早める可能性があるが、全範囲の完了・API数・依存管理が増える。現在の24回比較へ同時導入すると原因を切り分けられない。

専門家B（意味構造・対応品質）：行列候補だけでrecordsと判断しない。formを独立field群へ展開し、直接labelsとparentを別項目にする。宣言入口の統一だけでは重複を防げず、全値所有の衝突検査が必要。出力短縮のためのラベル推測や保留解除は認めない。

専門家C（保守・API設計）：provider形式を保存形式へ直接接続せず、既存の意味単位・共通compiler・manual変換へつなぐ。新規保存APIや独自の編集状態を増やすと責任が重なる。v4の非互換性はprovider境界に限定する。

客観的まとめ：共通する判断は、既知構造の展開をサーバーに任せ、AIは意味判断に集中させること。異なる点は分割・推論削減の導入時期。まず同一モデル/推論強度で出力契約を比較し、全ページと任意選択範囲の結果を分ける。速度条件を満たさなければ、不合格を維持して機能範囲を判断する。

## 3. v4で変えること

| 論点 | 新契約 |
| --- | --- |
| 独立欄の反復 | 同じparent/axis/readingを一度宣言し、値と直接ラベルの小さいitemsを返す |
| 通常フォーム | formで行ごとに異なる意味の項目を表現し、独立fieldへ展開。recordSectionを作らない |
| 繰り返し表 | recordsだけが行・列を繰り返す。既存候補と閉区間から展開し、空行を保持 |
| 名前の反復 | 明示されたlabelsのtextが正しい場合だけname=nullで参照。訂正が必要なら明示name |
| 記号設定 | 同じoptions/evidenceをmarksへ一度出し、m参照で共有。値の所有は統合しない |
| 無関係セルの分類 | role/reasonごとにセルをまとめる。未出力をignoredにしない |
| 局所親と直接ラベル | parentとlabelsを別に固定。階層の後付けで誤った直接ラベルを救済しない |
| context | bodyはignored、表示文字・物理参照は案metadata。保留表のcontextだけ反映しない |

宣言はunitsへ集約するが、重複禁止はpromptだけに頼らない。全単位から値・役割・親等の書込集合を集め、競合の両側を保留する。名前・役割・行を幾何だけから自動補完しない。

scopeをsnapshotへ追加して固定する。表選択なしはページ全体、選択ありは明示対象だけ。contextOnlyセルは参照専用。現在の画面選択や新しいセル配列を応答時に取り直して解釈しない。

この設計は既存矩形への意味付けを対象とする。矩形の新規検出・分割、音声認識・直前対象保持、通常のDecisions優先経路は今回変更しない。テンプレートの「案を反映」「確認して保存」、手動編集、局所録音、AI枠/移動/スクロール/表題表示の要件を保持する。

## 4. API設定と応答の扱い

[GPT-6 Lunaの公式仕様](https://developers.openai.com/api/docs/models/gpt-6-luna)でResponses・画像入力・Structured Outputs・streaming、およびreasoning.effortのnone/low等を確認した。次回は両方式とも `gpt-6-luna / low`。noneへの変更を同時に入れず、契約の差を測る。noneが精度を保って速くなるとは仮定しない。

1対象範囲あたりResponses 1要求、stream=true、detail=high、store=false、max_output_tokens=24576、timeout=60秒、直列実行、自動retryなし。Decisionsでの前段選択・モデルによる自己修復・追加OCRを挟まない。上限到達を成功JSONとして扱わない。

schemaはroot object、全property required、additionalProperties=falseとし、nullableで省略可能性を表現した。[Structured Outputsの公式仕様](https://developers.openai.com/api/docs/guides/structured-outputs?api-mode=responses)に沿った形状確認済み。ただし今回APIへschemaを送信しておらず、実受理は未確認。形状適合は意味・所有の正しさを保証しない。

最初の文字は進捗としてだけ計測する。完全応答→snapshot/参照/所有/保護検査→共通compiler→manual意味投影照合の後に編集案を表示する。部分JSONのセルへAI枠を確定表示しない。

今回の1要求方式では、使える案がある試行の「最初の検証済み案」と「要求範囲の処理結果確定」は同時刻になる。保留・未解決があれば残数を示し、全セルの意味付け完了とは呼ばない。タイムアウトや全保留は使える案の時刻なしとして記録する。

## 5. オフラインで確認したこと

保存D応答10件を新しい表現へ機械的に再符号化し、元の宣言へ戻した。旧応答の誤りも保持し、意味宣言の欠落がないことを確認した。通常フォームへの判断変更はこの再符号化に含めていない。

- 同じ内容をminifyしたUTF-8 JSONで **42,176 → 25,188 bytes、40.28%減**。
- schema、例3件、追加path/必須項目欠落/単数checklist指定の不正例3件を確認。
- 保存入力/画像/goldのSHAを照合。v4のruntime、form展開、API受理・品質・速度・保存は未実行。

この40.28%は**保存応答の表現サイズ**であり、出力token数・待ち時間・新しいモデル応答の短縮率ではない。モデルが同じ内容を選ぶ保証もない。Phase13の20応答再生と63境界の過去成功を、v4の成功へ読み替えない。

証拠：外部の `design-checks.json`、`recoded/*.json`、`design-manifest.json`。通常製品のコードを実行する修正試作は今回追加していない。

## 6. フェーズ15の固定比較

**GPT-6.1 Sol / High、製品外実装・測定、最大24回。** 旧計画の予備診断4回は任意選択範囲の対照4回へ具体化した。自動再試行や追加診断の余白は設けない。

| 枠 | 条件 | 回数 |
| --- | --- | ---: |
| 主比較 | T1/T2/T3/H1/H2 ×2反復 ×修復済みD13/v4 E4 | 20 |
| 任意選択範囲 | T2のg1と明示表題、H2のg2 ×各方式1回 | 4 |
| 合計上限 | 送信失敗・timeoutも含む | 24 |

D13はPhase13修復済みdecoder/normalizerとv3 schema。旧promptのcontext→labelだけを正し、両方式に同じ対象範囲制約を伝える。E4は本契約。共通候補builder、入力画像/文字/geometry、共通compiler、manual adapter、HTTP条件を揃える。

主比較は第1反復D13→E4、第2反復E4→D13。プロトコルの順番で直列送信し、モデル要求前にrequest/schema/prompt/source/input/goldのhashを固定する。cold/warm cacheの影響を隠さず、cached tokensと実行順を記録する。課金warmupはしない。

selected試行は全ページと混ぜない。対象IDはプロトコルに固定済み（T2=78セル、H2=15セル）。T2のg1は77セルと明示表題1セル。これは実験用の明示選択であり、製品UIや自動表判定を実装済みとは扱わない。全ページ画像を保持し、対象外の値や意味を変えない。goldは対象との積集合で採点し、旧gold自体は変更しない。

### 記録する時間と品質

- 操作相当の開始→準備完了→API送信→headers/最初のtext→complete→検証/compile/manual→利用可能な案。API送信前の準備時間も総時間へ含める。
- 全試行を残し、失敗試行を中央値から除外しない。ready時刻はnull、比較用のペナルティは60,000msとして別値に記録する。
- min/median/max、5秒以内の使える案数、全scopeの処理結果時刻、保留/未解決件数。n=10やselected各1回から母集団p95を主張しない。
- request/input/cached/output/reasoning tokens、応答bytes、API数、refusal/timeout/schema不適合、誤結合/欠落/保留を別集計する。
- 重点分類/結合/行/記号、帳票ごとの結果、manual変換前後。印字選択肢の順序、直接親、値所有を比較し、名前が似ているだけでは正解にしない。

### 開始条件と判定条件

送信前にPhase13境界とv4のform・参照/名前圧縮・m定義・scope・同値手動意図を短いオフライン検証で通す。既存の誤結合を先勝ちで通したり、未知参照を捨てて送信へ進めたりしない。原本/fixture/プロトコルのdrift、保持すべき意味投影の欠落、guardが不正案を通す不具合では中止する。

旧指標の下限148/176分類、45/78結合、14/14行、意味案8/10、中央値10.138秒は維持して報告する。これだけでH1/T1の欠落を隠せたため、新しい比較開始前に次の追加条件を固定した。

| 条件 | E4の判定 |
| --- | --- |
| 分類・結合 | 分類150/176以上、結合72/78以上、同時対照D13を下回らない |
| 帳票ごとの結合 | 各反復でT1≥3/5、H1≥3/4、T2=21/21、H2=4/4、T3=5/5 |
| 行と記号 | 14/14行、16/16選択肢。T2各3重点行、H2各4行保持 |
| 誤結合 | 重点対象の誤結合0。欠落/保留は上記下限の範囲で別記。評価対象外の新たな誤対応も採用審査へ明示 |
| 変換・保護 | 全利用可能案のmanual意味投影保持、受理された案によるphysical/scope違反0 |
| 比較上の速度 | 失敗を含む中央値≤10.138秒かつ同時D13の80%以下 |
| 「5秒で応答」の主張 | 全ページの使える案が5秒以内≥8/10。満たさなければこの主張をしない |
| 選択範囲 | E4の2試行とも5秒以内、T2重点21結合/H2全4結合と選択肢保持。単反復なので限定的証拠 |

全ページ速度とselected速度のどちらが条件を満たしたかで採用範囲を判断する。速くても品質条件未達なら不合格。選択範囲だけ合格した結果を、全ページの高速化成功にしない。

未知帳票はこの24回へ含めない。既知5帳票をholdoutとは呼ばない。Phase16では新規帳票、手直し時間、実保存・再取得等の残る受入条件を明示し、この測定だけで製品を開放しない。不合格なら自動的な追加再設計ループを続けず、全ページ方式の継続可否と範囲補助への限定を判断する。

## 7. 変更と保持確認

今回のrepository変更は本書、`docs/contracts/template-analysis-v4/`の設計artifact、AGENTS、Safety Contractの現在地。製品コード・通常DB・通常設定を変更しない。外部スクリプトはschema生成、保存応答の表現照合、計測仕様と保持証拠の固定だけ。

外部 `audit_state.py before/after` で両checkout/HEAD、通常DB全37表とschema、312件のAPI台帳、通常863ファイル、781 artifact SHA、Phase11/12/13証拠を照合する。`git diff --check` と参照ファイル/hashを確認する。結果の正本は `preservation.json`。

**今回API0回、312/496使用、残184回。** 通常DB未移行、editing/解析は無効のまま。製品OCRのprovider・prompt・物理identity・mapping/crop・受付snapshot・revision・確定/出力コードを変更しておらず、既存OCR contractを維持した。

frontend build、実HTTP、v4 runtime、実API速度/品質、画面、マイク、保存・取消、全体回帰は未実施。設計契約の確認をそれらの成功へ読み替えない。

フェーズ14はここで区切る。次はフェーズ15、GPT-6.1 Sol / High。採用判定はフェーズ16、Astra / XHighで行う。
