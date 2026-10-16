# AIテンプレート作成フェーズ15：compact v4の製品外実装と固定比較

2026-10-11。実装工程GPT-6.1 Sol / High。APIモデルはGPT-6 Luna / low。対象は `C:\Users\hanam\Desktop\llm-ocr-deploy`、実装・証拠は `D:\llm-ocr-archive\template-ai\20261011-phase15`。

**固定24試行を完了したが、E4は採用条件を満たさなかった。全ページの比較用中央値は10.905秒から8.178秒へ25.01%短縮した一方、重点結合は64/78から62/78へ低下し、行・記号の保持にも欠落がある。製品へ接続しない。**

通常製品コードは変更していない。通常DBの変更は許可されたAPI予約24件だけで、業務データ・schema・通常設定は保持する。追加の比較や自動再設計はここで止め、次はフェーズ16、Astra / XHighで採用範囲を判断する。

## 1. 実装と変更境界

[フェーズ14設計](template-ai-phase14-design-2026-10-11.md)、[v4契約](contracts/template-analysis-v4/README.md)、[固定プロトコル](contracts/template-analysis-v4/phase15-protocol.json)を維持した。

外部 `compact.py` はprovider DTOを共通の意味単位へ展開する。fieldsは値ごとの独立field、formは既存候補の同じ行の明示列から独立field、recordsは繰り返し表へ変換する。名前は取得済みlabelsの文字だけから解決し、欠落・長さ超過を補充しない。m定義は応答内で共有するが、各値は固有の意味・mark bindingを持つ。

不正単位も所有・role・parentのclaimを保持して全体検査する。複数所有は両側保留。保留した意味単位から依存見出しだけを新規反映しない。記号定義の参照完全性・重複選択肢・既存禁則を検査する。取得文字に根拠がない選択肢は使用単位すべてを保留する。証拠照合では空白だけを除いて比較し、保存する選択肢自体の文字・順序は変えない。画像内だけに存在して取得textにない選択肢を自動証明する検証器はないため、この保留は保守的な制約である。

`scope.py` はPhase13のv1物理snapshotを変更せず、新policy `candidate-snapshot.scoped.v2` で対象/参照専用ID、schema/prompt hash、scope digestを束縛する。両方式に同じscope guardを使用する。部分だけ選択された候補の勝手な切り詰め、範囲外の値所有・明示分類・親変更を拒否する。範囲外parentの参照は実在するcatalog見出しだけに限定する。

D13のresolver、候補builder、共通compiler、manual adapterはPhase13修復版のコピー。新API endpoint、通常保存、矩形の生成・分割、OCR provider・prompt・mapping・crop・受付snapshot・補正revision・確定/出力契約は変更していない。**既存OCR contractを維持した。**

## 2. 送信前の検証と固定

| 確認 | 結果 |
| --- | ---: |
| Phase11保存応答のPhase13経路再生 | 20/20意味投影保持 |
| Phase13既存境界（既存graph no-op20件を含む） | 63通過 |
| v4圧縮・form・重複所有・scope検証 | 83通過 |
| 名前・共有mark・空行・同値facet・応答状態・既存graph追加検証 | 28通過 |
| 実測24案の名前/aliases/所有共有/mark/親/表/行のmanual変換 | 24/24保持 |
| 実測24案を既存graphとして再保存するno-op | 24/24完全graph保持 |

保存D案のv4再符号化ではH1-D-1に旧案が発明した記号選択肢を確認し、新v4経路で4つの既存受理bindingが保留になった。旧Phase13再生結果を上書きしていない。H1-D-2には同じ追加欠落がない。これは新モデル品質の測定ではなく、保存宣言に対する根拠検査である。

`execution-manifest.json` にソース、schema、prompt、入力画像/文字、gold、24 requestとscopeのhashを送信前固定し、各送信前と終了時に再確認した。Phase14 artifactとPhase13 implementation manifestのhashも一致した。比較中に実装・prompt・gold・閾値を変更していない。

初回のローカル起動は作業ディレクトリーによるDATA_DIR不一致で送信前に停止した。API0回・run未作成を確認し、同じ固定ソースを実backendディレクトリーから起動した。記録は `initialization-note.json`。APIリトライではない。

測定後の補足projection検査では、空catalogで新規生成されるUUID/pathの文字列差をそのまま比較すると差異が出た。新規割当は意味と物理参照によって比較し、既存graphではID/pathを含む完全一致を別に要求した。`manual-projection-difference.json` は初回の未正規化差分、最終判定は `manual-projection-check.json`。名前・所有の共有・mark/親/行の欠落は認めていない。補足検査の時間は主測定へ加算していない。

## 3. 実API条件

既知5帳票×2反復×D13/E4の20送信と、T2-selected/H2-selected×両方式の4送信。第1反復D13→E4、第2反復E4→D13。最大24回をすべて使用し、HTTP送信失敗・timeout・schema不適合・refusalは0。T3-E4-2はJSON形状が正しくても意味単位が0で、利用可能な案としては失敗である。

Responses、GPT-6 Luna、low、同じ全ページ画像/high、stream、store=false、最大出力24,576 tokens、60秒timeout、同時実行1、自動retry/有料warmup/追加診断0。モデルとcompleted状態を実応答で検査した。[Luna公式仕様](https://developers.openai.com/api/docs/models/gpt-6-luna)と[Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs?api-mode=responses)を事前照合した。

対象選択は固定fixtureの任意選択でありGUI実装ではない。T2はg1の77セル＋明示表題1セル、H2はg2の15セル。全ページ画像と全c一覧は保持し、候補提示とwrite範囲だけを絞った。選択scopeのgoldは固定goldとの積集合で採点した。

準備→完全応答→形状/意味検査→共通compile→manual投影比較→少なくとも1意味単位成立までをreadyとした。最初のtextはreadyではない。意味案がない試行はraw ready=null、比較には60,000msのペナルティを使用する。これをtimeoutや実処理60秒とは呼ばない。1要求方式なのでfirst verifiedとwhole scope処理結果時刻は同じだが、全欄の意味付け成功を示さない。

## 4. 全ページ主結果

| 指標（各10試行） | D13 | E4 | E4条件 |
| --- | ---: | ---: | --- |
| 重点分類 | 165/176 | 126/176 | 150以上、D13以上：未達 |
| 重点結合 | 64/78 | 62/78 | 72以上、D13以上：未達 |
| 重点行 | 14/14 | 10/14 | 14/14：未達 |
| 記号選択肢 | 16/16 | 8/16 | 16/16：未達 |
| 重点誤結合 | 14 | 0 | 0：達成 |
| 重点結合欠落/保留 | 0 | 16 | 帳票ごとの下限：未達 |
| 意味案が1つ以上 | 10/10 | 9/10 | 旧下限8/10は達成 |
| 5秒以内の意味案 | 3/10 | 0/10 | 8/10：未達 |
| 比較ready中央値 | 10.905秒 | 8.178秒 | 10.138秒以下・D13の80%以下：達成 |
| 比較ready最小/最大 | 4.474/22.771秒 | 7.434/60.000秒 | E4最大は案なしペナルティ |
| 最初のtext中央値（参考） | 5.239秒 | 6.214秒 | readyではない |

E4の実処理総時間の最大は12.501秒で、60秒は案なし試行の比較ペナルティ。全試行を含む中央値比は0.749932。速さの比較条件だけは達成したが、採用条件は不合格である。

E4の重点誤結合0は、欠落16件を無視して成功と解釈できない。D13もT1/H1に重点誤結合が計14件あり、分類165/176だけで採用できない。

### 帳票別の原因

- T1：D13は両反復1/5。E4は0/5、4/5。E4第1反復は直接labelsを指定しながら共通axis=directにした多数項目を保留した。正しい局所親と直接labelsの理解・出力制約が不安定。
- T2：両方式とも各21/21結合、3重点行を保持。E4のpage readyは12.501/11.083秒で5秒に届かない。
- T3：D13は各5/5結合・4/4選択肢。E4第1反復は同条件を満たしたが、第2反復はlabels付きの全fieldをaxis=directとして保留し、意味案0。
- H1：D13は各1/4。E4は4/4、3/4で改善したが、2反復とも3/4以上という個別条件の達成だけでは全体不合格を解消しない。通常formを表と区別する実装は成立したが、既知入力2回だけで一般化を主張しない。
- H2：D13は各4/4結合・4行・4選択肢。E4第1反復はvalue列labels=[]かつname=nullで名前を導けずrecords全体を保留。第2反復は全保持。

保存した主結果では保留理由DIRECT_LABELSが62件、INVALID_DERIVED_NAMEが1件。schema形状が通っても、axisとlabels、null名の前提など意味上の制約は満たされない。これをサーバーの推測補正で通していない。

## 5. 任意選択範囲の結果

| 選択範囲 | D13 ready | E4 ready | 結合・行・記号 |
| --- | ---: | ---: | --- |
| T2-selected | 6.778秒 | 9.167秒 | 両方式21/21結合・3行 |
| H2-selected | 4.165秒 | 5.493秒 | 両方式4/4結合・4行・4選択肢 |

E4は両scopeで品質を保持したが、両方5秒以内の条件に未達。今回は両scopeともD13より遅い。表の任意選択がE4のH2欠落を解消した1試行は観察できるが、速度や未知入力の精度を保証する証拠ではない。各方式・各scope1回の探索的結果に留める。母集団p95を計算しない。

## 6. Tokens・キャッシュ・出力量

| 全ページ合計（各10回） | D13 | E4 |
| --- | ---: | ---: |
| input tokens | 97,128 | 100,198 |
| cached input tokens | 56,325 | 59,088 |
| output tokens（reasoningを含む） | 15,457 | 8,074 |
| reasoning tokens（outputの内数） | 3,075 | 4,437 |
| 応答JSON bytes中央値 | 2,122.5 | 790 |

E4は出力が少ないが、保留・欠落も増えているため、同じ意味を完全に保持した圧縮率とは呼ばない。reasoning tokensと最初のtext待ちは増えており、wire bytesだけから応答速度を説明できない。実行順と各trialのinput/cached/output/reasoning tokensを `results.json` に保存した。キャッシュやモデル変動をこの少数試行で分離していない。

## 7. 全24試行

readyは秒。分類/結合/行/記号は重点正解数で、全対象範囲の完成を意味しない。

| run | ready | 分類 | 結合 | 行 | 記号 |
| --- | ---: | ---: | ---: | ---: | ---: |
| T1-D13-1 | 21.767 | 11 | 1 | 0 | 0 |
| T1-E4-1 | 11.469 | 1 | 0 | 0 | 0 |
| T2-D13-1 | 10.681 | 29 | 21 | 3 | 0 |
| T2-E4-1 | 12.501 | 29 | 21 | 3 | 0 |
| T3-D13-1 | 4.474 | 12 | 5 | 0 | 4 |
| T3-E4-1 | 7.434 | 12 | 5 | 0 | 4 |
| H1-D13-1 | 13.224 | 10 | 1 | 0 | 0 |
| H1-E4-1 | 7.470 | 10 | 4 | 0 | 0 |
| H2-D13-1 | 4.634 | 20 | 4 | 4 | 4 |
| H2-E4-1 | 7.516 | 4 | 0 | 0 | 0 |
| T1-E4-2 | 8.202 | 11 | 4 | 0 | 0 |
| T1-D13-2 | 22.771 | 11 | 1 | 0 | 0 |
| T2-E4-2 | 11.083 | 29 | 21 | 3 | 0 |
| T2-D13-2 | 12.757 | 29 | 21 | 3 | 0 |
| T3-E4-2 | —（案なし） | 2 | 0 | 0 | 0 |
| T3-D13-2 | 5.833 | 12 | 5 | 0 | 4 |
| H1-E4-2 | 8.153 | 9 | 3 | 0 | 0 |
| H1-D13-2 | 11.128 | 11 | 1 | 0 | 0 |
| H2-E4-2 | 7.572 | 19 | 4 | 4 | 4 |
| H2-D13-2 | 4.588 | 20 | 4 | 4 | 4 |
| T2-selected-D13-1 | 6.778 | 29 | 21 | 3 | 0 |
| T2-selected-E4-1 | 9.167 | 29 | 21 | 3 | 0 |
| H2-selected-E4-1 | 5.493 | 15 | 4 | 4 | 4 |
| H2-selected-D13-1 | 4.165 | 15 | 4 | 4 | 4 |

## 8. 証拠・保持・未検証範囲

外部 `execution-manifest.json`、`frozen-requests/`、`sends.json`、`raw/`、`decoded/`、`normalized/`、`compiled/`、`proposals/`、`runs.json`、`results.json` を保存した。raw ready、ペナルティ、first text、全scope処理結果時刻、tokens、JSON bytes、誤結合と欠落、保留理由を分けて残す。

実行した短い検証は `replay.py`、`boundary_checks.py`、`compact_checks.py`、`additional_checks.py`、`benchmark.py --freeze`、`benchmark.py`、`analyze.py`、`manual_projection_check.py`、`audit_state.py before/after`。API送信は最後の比較用benchmarkだけ。重いunit/full regression/E2Eは実行しない製品外工程である。

repository変更は本書、AGENTS、Safety Contractの現在地だけ。終了時の正本 `preservation.json` で両checkout/HEAD、両通常DB全37表（deployのAPI台帳以外）、schema、863通常ファイル、781 artifact SHA、Phase11〜14証拠、固定ソース/入力/goldと `git diff --check` を照合する。通常editing/解析は無効、通常DB未移行を維持する。

**今回API24回、累計336/496、残160回。** 予約前の312件を保持し、追加24件のledger IDとsendsを照合する。保存比較のための許可枠を追加で使っていない。

未知帳票、実HTTPの所有/反映/取消/receipt、実DB保存・再取得、画面、実マイク、OCR実行・出力、frontend build、製品全体回帰は未受入。fixture bindingとin-memory manual検証を製品の保存成功へ読み替えない。

## 9. 次の区切り

フェーズ15は完了、採用判定は不合格。通常製品への統合や追加API比較へ進めない。

次は**フェーズ16：Astra / XHigh**。保存24応答と境界を審査し、全ページ方式を継続するか、範囲補助へ限定するか、契約を変更するかを判断する。APIは原則0回。axis/nameの意味制約、E4のH1改善と他帳票の不安定性、D13のT1/H1誤結合、選択scopeの単反復という限界を検討対象とする。このフェーズで自動的に再設計・再比較は始めない。
