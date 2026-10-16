# 画像を使うAI意味付けの解析サービス接続 — 2026-10-13

**新しい画像提案契約を、受付・API予約・検証・案の表示・下書き反映・共通保存へ接続しました。** 専用DBと制御したHTTP応答による48条件が通過しています。実Lunaの認識精度、実応答時間、Chromeでの操作、最終4資料の評価は未検証です。ゴールは継続中です。

対象は `C:/Users/hanam/Desktop/llm-ocr-deploy`。旧 `llm-ocr` は変更していません。成果物は `D:/llm-ocr-archive/template-ai/20261013-visual-runner`。実API使用0・通常台帳への予約0、累計417/496・残79です。従前の18/24/28回と未割当9回の計画配分は変更していません。

## 原因と変更

前工程の `visual.v2` は記号欄・チェックリスト・見出し名を扱えましたが、解析サービスの完全解析は旧 `graph.v1` をページ全体に1回要求していました。新契約への入口がなく、画像単位の案を表示・保存する実利用経路がありませんでした。

今回の完全解析は、受付時に画像と既存セルから処理範囲・必要呼出し数を固定します。範囲ごとにページ全体画像と高解像度の切出しを送り、対象参照と読取り専用の参照を分けます。空の印字OCR文字列でも要求を作れます。画像による文字理解の成功を意味するものではありません。

指定モデルは `gpt-6-luna`。Responsesの厳密なJSON Schemaを使用し、応答のmodel・完了・拒否・形式を検査します。分類だけの操作は既存のDecisions経路を維持します。モデルと画像入力・構造化出力の仕様は[公式モデル資料](https://developers.openai.com/api/docs/models/gpt-6-luna)と[Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs)で確認しました。アカウントでの実利用可能性は今回未確認です。

既存の共有API台帳とHTTP送信処理を利用し、同時処理数は2です。取消、編集session、版、対象範囲、基準画像を予約後・HTTP送信直前にも再検査します。そこで拒否した予約は返却せず、HTTP送信を止めます。実行権をDB transactionで一度だけ取得し、同じ解析の重複実行で呼出しを増やしません。自動再試行は追加していません。

完全な応答を共通編集compilerで検証した範囲から `page_validated` を発行し、既存画面へ案を表示します。生の途中JSONは公開しません。同一ページ内の一部失敗を `partial` として表現し、成功した案と失敗した対象欄の両方を保持します。案の反映は解析終了後、保存は従来の「案を反映」→手修正→「確認して保存」です。AI専用の保存経路は増やしていません。

## 変更ファイル

| ファイル | 役割 |
| --- | --- |
| `backend/app/template_analysis/visual_provider.py` | 画像2枚・固定モデル・visual.v2契約による要求と完了応答の検査 |
| `backend/app/template_analysis/visual_runner.py` | 処理範囲の実行、送信後の検証、部分成功、順次公開 |
| `backend/app/template_analysis/service.py` | 完全解析の接続、重複実行防止、部分結果の集約、旧分類経路の送信直前検査 |
| `backend/app/template_analysis/snapshot.py` | 未保存の意味IDが再割当される下書きを、有料送信前に拒否 |
| `backend/app/template_analysis/visual_input.py` | 既存の意味設定と保護項目を画像要求へ追加、型の明確化 |
| `backend/app/template_analysis/visual_compile.py` | 生存状態の検査責任について文書を更新。今回の挙動変更なし |
| `backend/app/template_analysis/contracts.py` | ページ内の部分成功をHTTP契約に追加 |
| `backend/scripts/generate_template_analysis_contracts.py` | visual.v2の公開schemaを生成 |
| `docs/contracts/template-analysis-v1/http.schema.json`、`visual-proposal.schema.json` | 生成済みHTTP・画像提案契約 |
| `frontend/src/editing/template-analysis.generated.ts` | 部分成功を含む生成型 |
| `frontend/src/editing/TemplateAnalysisPanel.tsx` | 部分成功の表示、未解決の技術コードを利用者向け文言へ置換 |
| `AGENTS.md`、`docs/ocr-safety-contract.md`、本報告 | 現在地と検証限界 |

## 検証証拠

通常データと分離したSQLiteを実製品のschemaから作成し、実FastAPI/ASGI、編集session、AnalysisService、VisualAnalysisProvider、ApiBudget、共通反映・保存を実行しました。外部通信は禁止し、HTTP transportだけを制御しました。最新実行の参照は `integration-latest.json`。48条件、制御したHTTP11回・専用台帳14予約で、実外部APIと通常台帳は0です。

主な条件は以下です。

- 記号3欄の分類・意味・選択肢を提示し、下書き反映後に見出しを手修正、明示保存・再読込する。実体の座標とセルIDを保持し、3つの異なる出力先を保存する。
- 第2範囲の応答待機中に第1範囲の検証済み案を取得できる。第2範囲が不正でも第1範囲だけを保存でき、失敗欄は未解決として残る。
- 不正参照、model不一致、拒否、未完了を適用しない。二重受付・同時実行で二重送信しない。
- 送信枠待機中の取消は予約0。予約直後の取消・版変更・画像変更は予約を残して送信0。通信中の取消・版変更は届いた案を公開しない。
- 同値の手動指示も反映時に保護する。案取得後に画像が変われば反映しない。新規の未保存意味IDは予約前に拒否する。

既存の証拠は上書きせず、新しいコピーで再実行しました。

| 検証 | 結果 |
| --- | --- |
| 画像準備・保護・範囲境界 | 74条件通過 |
| 記号・チェックリスト・意味compiler | 36条件通過 |
| 共通保存の実ASGI・専用DB・再接続 | 46条件通過 |
| 共通手動compilerの保護 | 13条件通過 |
| 新しい解析から保存までの実ASGI | 48条件通過 |
| 既存frontend解析storeテスト | 17件通過 |
| 対象8ファイルのruff / mypy | 通過 |
| 生成契約、frontend build、diff確認 | 通過 |

実行コマンド、作業ディレクトリ、終了コードは `checks.json` と各ログに固定します。今回の再現入口はbackendの既存venvで実行する `integration.py` と `run_checks.py`。封印後の同一フォルダーで再実行せず、新しい検証フォルダーへコピーして記録してください。`run_checks.py` は同一反復中の成功済み項目を再実行しないため、新しい検証では `checks.json` を引き継ぎません。

## 保持した境界と未完了

記入済み帳票のOCR provider、prompt、model、mapping/association、crop、受付snapshot、補正・確定・出力は変更していません。既存OCR contractを維持した範囲は、この変更差分と通常データ保全によって確認しています。OCR全件の再評価を行ったという意味ではありません。新しいテンプレートの案は共通保存で意図的に意味と読取設定を生成します。DB schemaと通常環境の機能有効化設定は変更していません。

以下は残っています。

1. 実Lunaでの意味精度、モデル誤対応率、実応答5秒条件。今回の制御応答の速度は代用しません。
2. Chromeでの順次表示・操作・修正・保存・再読込。ASGIやstoreテストは実画面の代用ではありません。起動・移行の検証も未実施です。
3. 未保存の新規意味IDを保持したまま解析へ渡す機能。共通compilerがIDを再割当する場合は、有料送信前に保存を求めます。従来の送信後拒否を前倒ししたもので、機能完成とは扱いません。
4. 新しい矩形生成・選択肢ごとの矩形分割、paged記号定義など、前工程から未対応の構造。320セルを超える依存範囲は保留し、上限を緩めて通しません。
5. 旧保護条件の全移植と独立審査。今回の同一agentによる検証を独立審査とは呼びません。旧34-F受入を偽造せず、35〜37比較の予算・条件を今回へ転用しません。
6. 選定済みの公開実帳票4種類の独立した正解作成と最終評価。原本と評価ページは固定済みで、今回の開発・検証には利用していません。

通常データ・台帳・旧checkout・既存成果物・最終4資料の保全証拠は `preservation.json`、この工程の結果と封印は `result.json` / `result-hashes.json` に保存します。
