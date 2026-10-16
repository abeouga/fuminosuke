# 修正5-A：行番号の対応検査とR04評価基準

2026-10-13。GPT-6.1 Sol / Highで修正5-Aを実装した。前回R1/P1の「中間行を行番号として申告すると6欄が4欄のまま保存できる」反例を、解析と共通保存の両方で拒否する。正常な左右の行番号は3行6欄のまま保存・再読込できる。R04は許容領域・同等表現・固定分母を持つ評価基準候補を作成した。**修正5-Bの独立再審査は未実施、実API比較は開始しない。**

対象は `C:\Users\hanam\Desktop\llm-ocr-deploy`。証拠は `D:\llm-ocr-archive\template-ai\20261013-row-key-repair`。実API・通常台帳予約0回、432/924・残492。新しいエージェントは起動していない。通常データと旧checkoutは変更していない。

## 原因と変更境界

旧 `BindingResolver.table()` は各行の `keyCell` を集合に潰し、行との対応を検査せず途中セル検査から除外していた。AIが中間行の入力セルを行番号と誤指定するだけで、既存の欠落検査を迂回できた。フロントエンドの表示変更では、公開APIから同じ誤った保存を行える問題を解決できないため、解析・共通編集・共通保存のバックエンド境界を修正した。

| ファイル | 変更 |
| --- | --- |
| `backend/app/layout_analysis/record_geometry.py`（新規） | HTTP・provider・DBに依存しない共通の物理行番号検査。行ごとの番号を保持し、一意の同一行、重複所有、入力セルとの重なりを検査 |
| `backend/app/template_analysis/visual_form_answer.py` | `keyCell` を行順の配列のまま渡す |
| `backend/app/template_analysis/binding_resolution.py` | 正しい行番号だけを欠落検査の例外とする。行番号にも編集対象範囲を要求。検査policyをv1→v2へ更新 |
| `backend/app/editing/record_commands.py` | `records.define` / `replace_rows` 等の共通コマンド完了時に、同じ検査を実行 |
| `backend/app/services/template_authoring_service.py` | 共通 `prepare()` で再検査。意味変更のない矩形編集や、解析を通さない手動保存も書込み前に拒否 |
| 文書3ファイル | 本報告、`AGENTS.md`、`docs/ocr-safety-contract.md`の現在地を更新 |

行番号と入力行の縦方向の重なりは、番号セルと各入力セルの小さい方の高さの50%以上を要求する。その条件を満たす行が宣言した一行だけであることを検査し、複数行を覆う番号枠を拒否する。番号は入力セルと別IDで、一つの番号セルを複数の行・表へ共有しない。入力セル内部へX/Yとも2pxを超えて侵入する番号枠も拒否する。番号が左側であるという固定の推測は使わず、右側も許容する。文字列としての印字行番号の読取精度を保証する検査ではない。

`TemplateAuthoringService.prepare()` の検査はconfig生成・公開・DB保存の前に実行する。表示のためのGETには検査を追加せず、既存の不整合を自動再構成・移行しない。通常DBの11版を読取確認し、行番号セルを持つ既存版は0件だった。この事実を全利用環境の互換保証とはしない。

外部の回答schemaと要求prompt、APIモデル、通常のOCR providerは変更していない。公開analysis v2 / structure.v1 / form.v2は維持する。`bindingResolutionPolicy`の更新が既存のruntime contract比較へ反映されるため、旧ポリシーの解析・承認済みmergeは再取得・反映・保存で失効する。旧案の再解析が必要な意図的非互換であり、保存済みの値・ID・mappingを変更する移行は行わない。

## 保存までの検証

専用SQLite、実FastAPIのASGI、共通compiler・共通保存を使った。provider HTTPだけを制御し、偽キー・外向きsocket拒否で検証した。通常DBへテストの編集要求を送っていない。

| 条件 | 結果 |
| --- | --- |
| 前回と同じ中間行省略 | 解析で拒否、revision 1を保持 |
| 前回と同じ中間行の行番号偽装 | `RECORD_KEY_ROW_ALIGNMENT`で拒否、入力mappingを生成・保存しない |
| 左側・右側の正常な行番号 | 幅・高さが異なる3行×2列をready→明示反映→明示保存。別接続でrevision 2、6mapping、行順・行番号ID・元の物理座標を確認 |
| 上下入替・別行の番号・複数行にまたがる番号・入力セルと重なる番号・番号の重複 | 解析で拒否、保存世代を保持 |
| 不正な下書きを解析なしで直接保存 | 共通manual-authoringと `TemplateAuthoringService.prepare()` が拒否。保存世代を保持 |
| 旧不正表へ矩形変更だけを加える | 意味コマンドのない保存も共通prepareで拒否。既存revision 2を保持 |
| 旧resolution policyの案 | GETでstale、承認後の保存も `SNAPSHOT_STALE` / CONFLICT、revision 1を保持 |

新しい検証は計13シナリオ。`row-key-integration-result.json`（12）と `row-key-policy-save-result.json`（1）から各専用runの入力・応答・receipt・保存結果へ辿れる。前回の保存済み誤結果は、前回の封印済みASGI証拠を読み取って直接保存の反例に使った。今回の製品出力へ合わせて旧期待値を置換していない。

既存の接続50条件、矩形34条件、表75＋旧契約11条件、通常欄45条件、矩形の共通保存112条件を今回の保存先へ複製して再実行し、全て通過した。旧検証のassertion・fixtureは変更していない。検証用スクリプト・ログは製品外に置き、unitを追加していない。

対象5製品ファイルのruffとmypy（`--follow-imports=silent`で対象範囲を検査）は通過した。依存先も報告するmypyは11ファイル61件で失敗したが、変更前ソースを `--shadow-file` で照合して61件全て同一、新規エラー0件と確認した。全体型検査の成功とは報告しない。初回検証スクリプトのcompact metadata展開漏れ、専用DBへ旧不正表を投入した後のrevision取得順の誤りは、検証条件を修正して再実行した。失敗ログも保存した。

コマンドは専用Pythonの `python -X utf8 -B <証拠>/integration_row_keys.py`、同 `stale_policy_save`、`check_bindings.py`、`check_structure.py`、`integration_tables.py`（通常と `stale_merge stale_save`）、`integration_ordinary.py`、`integration_structure.py`。静的検査の対象と結果は `ruff.log`、`mypy-targeted.log`、`mypy.log`、`mypy-baseline.log`、`mypy-comparison.json`を参照する。

## R04の固定した評価基準候補

`r04-gold/gold.json`、`freeze-manifest.json`、`assessment-template.json`、`validation.json`、`review.html`を作成した。元の暫定一覧122観察は変更していない。原PNGを変更せず、PDFの印字文字座標を補助にして、全ての位置・親・選択肢・通数の対応を固定した。原画像は開発用R04だけを閲覧し、最終評価用4帳票の内容は開いていない。

6つの印字選択群を29の個別選択肢へ展開した結果、対象は**145位置、143機能項目、型を推測しない2領域**。143は選択肢88＋文字/通数/日付55。2領域は受付と出納印で、幾何評価と手動扱いの集計に必ず残す。検出79矩形、既知10問題、単純な122観察のいずれも便宜的な完成率の分母として使わない。

各対象に必須コアと許容外枠を定義し、必須コア95%以上、許容外枠からの突出2px以内、未割当の別欄へのX/Yとも2pxを超える侵入禁止を定めた。日付は年・月・日と印字がある場合の元号の位置を別に固定し、全日付一欄・完全な成分分割の両方を許容する。必要のない成分間の空白を全てcropする条件にはしない。

同一セル・mark setで複数の選択肢を扱う同等表現は、各選択肢が独立した保存値として識別できる場合に許容する。共用cropの許容範囲は割り当てた選択肢の許容外枠の外接矩形とし、未割当の選択肢や自由記入・通数が混ざれば失敗とする。日付・文字・通数はそれぞれ独立した出力値と原セルへの対応を必要とする。名称・ID・保存先文字列の完全一致は要求しない。

曖昧群U01〜U05は前回の審査判断を継承する。「組」は戸籍関係の中立名と独立した数値、個/全・昭和/平成・親族種類・本人確認書類は全候補を保持し、印字されていない単一選択の業務規則を足さない。受付・出納印を文字認識成功として採点せず、領域保持と未解決/手動扱いを別に記録する。申請日は生年月日と別の欄とする。

評価表には145件全ての実cell/mark set/option ID、targetPath、capture SHA、親・通数の対応判定、幾何被覆率、理由を記録する。未解決・未対応・欠落を分母から落とさず、余分なfield/optionも別集計する。受付・出納印の扱いが未解決のまま全ページの自動完成を主張しない。新しいモデル出力を見た後の無言の基準変更を禁止し、変更時は原本由来の理由と新バージョンを必要とする。

全必須コアの重複なし、原本hash、印字29選択肢の位置、145件の評価行、元から欠けている3位置を検査した。Chromeの独立した原画像重ね表示で145群を表示し、元画像読み込みと146表行を確認した。これは採点用資料の表示であり製品E2Eではない。日付成分の表示更新後の追加スクリーンショット取得は終了せず、待機を打ち切った。最終HTML・JSONの対応を確認し、追加キャプチャ成功とは数えていない。

これは白紙帳票の機能・位置評価用の固定候補であり、記入済み筆跡のcrop clearance資格やOCR文字精度の証明ではない。`acceptedGold=false`、`independentlyReviewed=false`を維持し、修正5-Bで全領域と同等表現を再審査する。実Luna品質は未測定。

## 証拠への操作ミスと保持確認

今回、前回の `probe_row_keys.py` をimportしてモジュール直下の実行処理を動かし、前回のコンパイラ再現出力2ファイルを上書きした。要約 `row-key-probe.json` は会話ログに残る値から元のhashと完全一致する形で復元したが、詳細 `omitted_row_as_keys.json` の元のbytesは復元できない。元の封印manifestを変更せず、この1ファイルを封印不一致として明示する。上書き後の出力も今回の `overwritten-review-output` に保存した。

元の実ASGIの制御応答・receipt・saved結果・専用DB、元の要約と審査報告はhash照合でき、旧不具合の保存到達証拠は保持している。`evidence-incident.json` に旧新hash・原因・復元できた範囲を記録した。「前回証拠を全て保持した」「前回封印が全件通過した」とは報告しない。以降の実行スクリプトは今回専用フォルダーへコピーし、出力先を確認した。

通常863ファイル、781artifact、全通常DBテーブル・schema・設定・共有台帳・旧checkout、最終4原本14ファイルを終了時に照合する。既存差分を保持し、今回の差分は製品5ファイルと文書3ファイルに限定する。`git diff --check`、前工程の封印照合結果と上記1例外は `phase-result.json` に固定する。staging/commit/pushなし。

## 未検証と次工程

既存のOCR値・mapping identity・physical identity・provider association・crop・revision・確定snapshot・出力形式は変更していない。正常表の保存対応を維持する既存OCR contractの不具合修正であり、不正な行番号対応を拒否する。geometry・意味付け・共通保存の合流点以外へOCR経路を追加していない。

frontend実装・build・製品Chrome・実マイク・実Luna・全体unit/E2Eは今回実行していない。稼働中の旧専用サーバーの起動・停止・再起動も行っていない。新規13シナリオは新しい隔離ASGIで実装を読み込んで検証した。実Lunaのschema受入、意味精度、初回5秒、最終4帳票、手作業時間比較は残る。

修正5-Aの実装・候補基準整備を終了。次は **修正5-B：Astra / XHighの独立再審査**。製品5ファイル、13シナリオの旧新証拠、R04の145位置と採点規則、証拠事故の例外を審査する。合格するまで修正6（実Luna最大27回）を開始しない。ゴールは未完了。
