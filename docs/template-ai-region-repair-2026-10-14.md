# 修正6B 領域依存と失敗位置と計時の修正

2026-10-14 JST。対象checkoutは `C:\Users\hanam\Desktop\llm-ocr-deploy`。
実行モデルはGPT-6.1 Sol / XHigh。計画はHighだったが、モデル切替後の実設定がXHighであることを確認・開示して実装した。

**6-CのR1〜R3を修正し、保存応答・隔離API・Chromeで確認した。Astra / XHighによる独立再審査は未実施であり、6-Dの実API測定へは進めない。**
改善ゴールの実Luna品質と実画面5秒条件は未達成の扱いを維持する。

## 修正内容

| 指摘 | 変更 | 確認した結果 |
| --- | --- | --- |
| R1 初期分割の親子依存切断 | 初期seedに単一所有・全geometry・依存groupの検査を追加。不成立ならページ全体から依存を保つ分割をやり直す。最後にも同じ不変条件を検査する | 同じ公開plan APIで、上の親と左の子が別所有から同一所有へ変わった |
| R2 検出0セルの失敗位置欠落 | writeRefsが空の失敗領域にも、空のcell ID、実際の所有bounds、失敗理由を残す | 上余白の出力上限エラーはfailedのまま、未解決1件と正しい位置を返す |
| R3 選択待ちを含むactionable計時 | 描画後2フレームで有効な選択controlと反映controlのDOM状態を記録。人間がチェックする前に操作開始可能時刻を記録する | 未選択・解析中に記録され、選択を約40秒遅らせても同じ時刻を保持した |

R1は重なる外接矩形を追加する方式ではない。全ページの重複なし被覆、セルの一意所有、元geometryを維持する。
25セル以上の中央反復配置を持つ追加ケースでは、依存を維持しつつ2又は3領域へ分割できた。
依存がないケースは5領域を維持した。局所録音は従来の狭いform経路を保持し、外部の保護された親を読取専用文脈として扱う。

R03では、従来の下端境界が右端の縦長枠を切っていた。元の枠を動かさず安全な分割へ組み直し、要求数は5から4になった。
最終計画はR01 p1=5、R02 p1=5、R02 p2=4、R03 p1=4、R04 p1=6要求である。

R2のプレビュー用識別子は表示のためだけに作る。canonical cellやmappingを新設しない。
既存セル付きの出力上限エラーでも、元の200セルの未解決項目が保持されることを確認した。

R3は、busy、復旧待ち、保存領域エラー、保存中、失効・保留・既反映などを操作開始可能として記録しない。
markのdetailにはanalysis ID、有効な選択control、反映controlの有無・有効性・選択待ちを残す。
選択、反映要求、反映完了、明示保存は別の時刻として記録する。描画だけを速度合格とは判定しない。

## 変更境界と旧案の扱い

製品変更は次の7ファイルである。

- `backend/app/template_analysis/region_plan.py`：領域の被覆・所有・geometry・依存の検査。
- `backend/app/template_analysis/visual_input.py`：領域policyを共通定数から参照。
- `backend/app/template_analysis/visual_provider.py`：同じpolicyの参照と解析契約marker更新。
- `backend/app/template_analysis/visual_runner.py`：未検出の失敗boundsを保持。
- `frontend/src/editing/TemplateAnalysisPanel.tsx`：DOM状態を用いる準備完了計時。
- `frontend/src/editing/templateAnalysisStore.ts`：準備完了guardと段階別mark。
- `frontend/src/editing/TemplateAnalysisPanel.test.tsx`：再現できた選択待ち混入の短い回帰検証を1件追加。

領域policyは `template-analysis.region-plan.v2`、runner markerは `template-analysis.visual-runner.v7`。
公開APIはtemplate-analysis.v3、回答schemaと領域promptは変更しない。製品APIモデルはGPT-6 Luna、region/gridのreasoning=noneを保持する。
古いv1計画の保存snapshotは既存のstale検査で拒否する。古いAI案を新しい意味へ書き換えず、手動下書きを保持して再解析する。DB移行は不要である。

通常OCRのprovider・prompt・schema・association、共通GeometryCompiler/compose/authoring保存、canonical ID・mapping・crop・revision・XLSX出力の実装は変更していない。
変更前後の物理位置・入力先・保存先の照合を通過し、既存OCR contractを維持した。

## 保存と画面の検証

新しい専用8078/5278を起動し、HTTP、PIDと実行path、backendコードhashを確認した。
専用SQLiteと制御した有料HTTP応答を使用し、外部socket通信を拒否した。通常データや旧8076/5276の試験データへ修正試験を書き込んでいない。

Chromeで通常の「AI解析」を押し、API 2/4回の解析中に2領域の案を確認した。
チェックが0件でも、選択controlが有効になった時点で最初の操作開始可能markを記録した。
クリックから2529.5ms、そこから選択まで40526.1msだった。選択によって既存markが動かないことを確認した。
これは制御応答による計時機構の確認であり、実Lunaの5秒達成の証拠には使わない。

4領域を選択して「案を反映」、続けて「確認して保存」を操作した。実反映は1receipt、revisionは1→2。
ブラウザーを再読込して保存済みテンプレートを開き直し、別HTTP取得と別DB接続も照合した。
54セルのID・位置、22入力先・異なる22mapping/targetPath、表示名、読取設定が一致した。
保存後の旧runはstaleである。失敗用の空セルテンプレートはrevision1・セル0を維持した。

失敗用テンプレートでAI解析が出力上限エラーになると、未解決1件を表示した。
「位置を表示」から原画像全体の失敗範囲へ移動し、表示outlineとcanvasの位置・大きさが一致した。
この失敗runにはactionable markが0件である。

最初の自動クリックでは、ブラウザー操作ツールが反映・保存controlを既存の固定工程バーの背後へスクロールし、操作が送信されなかった。
中央へスクロールしてhit-testを確認した後に操作を完了した。反映要求・保存要求が重複していないことはreceiptとrevisionで確認した。

## 実行した検証

- 修正前：親子の別所有、空領域失敗の未解決0件、新規UI回帰の失敗を再現。
- 修正後：同じplan/空領域再現、追加5境界、200セル付き失敗を確認。旧契約拒否も確認。
- 既存の隔離ASGI検証：途中反映・競合・receipt・取り消し・保存再読込13条件、物理編集と手修正保持5条件、空検出4条件、限定範囲6条件を通過。
- R04の住所と同じチェックの正解fixtureを再実行し、独立した2mappingとrevision2で保存した。モデル品質の証明とは分ける。
- 5ページの全セル一意所有、全ページ被覆、領域の非重複を照合。新しい3ページの705 coreと、R04の145 core・15日付部分は、各論理項目が一つの所有領域内に収まった。goldはplanner入力に使っていない。
- `python -m ruff check` と `python -m mypy --follow-imports=silent`：変更したbackend4ファイルが通過。
- `npm test -- src/editing/TemplateAnalysisPanel.test.tsx src/editing/templateAnalysisStore.test.ts`：29件通過。
- 最後の `npm test`：23ファイル159件通過。`npm run build`：通過。
- `git diff --check`、開始時との差分範囲、通常DB/設定/旧checkout/過去封印の保持を確認する。

backend全件unit、全E2E、記入済み帳票の新たなOCR実行は行っていない。今回の短い検証を、OCR安全性の完全検証や全帳票のモデル品質へ拡張しない。
追加probeの誤ったlocal-mode期待、検証用SQL列名の誤り、import順序の検査失敗は `preparation-errors.txt` と該当ログに残した。
製品側の拒否を緩めたり、既存テストを削除・skipして通過させたりしていない。

## 修正した実API測定の配分

新実験は `region-v2-20261014`。元の最大96送信を維持し、配分は88送信へ固定する。

| 群 | 条件 | 最大送信 |
| --- | --- | ---: |
| G1 | 結果欄、その他欄、登録番号、住所複合欄の発行済み範囲を各1回 | 4 |
| G2 | 開発5ページを通常GUIで各1回。要求数は5+5+4+4+6 | 24 |
| G3 | R02 p2を6回×4要求、R04 p1を6回×6要求。通常GUIの全領域解析 | 60 |
| 合計 | 8枠は未配分のまま維持し、自動で他trialへ転用しない | 88 |

G3を「12送信の限定領域解析」と扱わない。12回の画面操作で60送信となる。
viewportは1920×898、工程1、初期scrollは先頭、セル・表の選択なし、既存のviewport優先規則を使用する。
R02 p2の先頭は `(0,0,963,911)`、R04 p1の先頭は `(0,0,1809,264)` の領域と固定し、結果を見て変更しない。

`experiment-allocation.json` に配分、ソース・コード・契約hash、各範囲、reference planとrequest hashを保存した。
これらのfixture IDを実trial IDへ流用しない。6-C通過後、6-Dの外部送信前に新resource/sessionのbinding、実snapshot/plan/region ID、要求本文・設定hashを固定・照合する。
実際のGUI条件・計画件数・先頭が不一致なら送信しない。クリック後の準備は全て5秒に含める。

G1の逆転・混入・保護/対応違反又は速度未達で後続群を止める。G2の重大誤対応でも未送信分を止める。
失敗・並行送信済み・予約は全計上し、無断再試行・lowへのfallback・旧未送信23試行の再利用をしない。
採点基準は[原画像から承認した3ページ](template-ai-region-gold-review-2026-10-14.md)と既存R04固定goldを維持する。
R03 p2の記入例と最終未使用4帳票はモデル入力へ含めない。

## 証拠と次工程

証拠は `D:\llm-ocr-archive\template-ai\20261014-region-repair`。保持結果と再審査用の固定コピーは `preservation.json` と `completion-1` を正本とする。
実API・通常予約は0回、共有台帳436/924、残488。専用制御予約は共有台帳から控除せず別記する。

次は **6-C再審査 / Astra / XHigh**。今回の7ファイルと保存・画面・分割の証拠、88送信の配分を審査する。
合格後に **6-D / Astra / High** の実Luna・実画面測定へ進む。新しい別エージェントは起動していない。
審査・実測・ゴールは未完了である。
