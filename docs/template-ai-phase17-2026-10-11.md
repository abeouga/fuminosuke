# AIテンプレート作成フェーズ17：保留フォームの親競合検査を修復

2026-10-11。実装工程GPT-6.1 Sol / High。対象checkoutは `C:\Users\hanam\Desktop\llm-ocr-deploy`、修復した製品外コピーと証拠は `D:\llm-ocr-archive\template-ai\20261011-phase17`。

**保留したformから親設定の宣言が消え、矛盾する別fieldを受理する不具合を修復した。関係する12条件は通過し、保存24応答の採点・受理/保留・意味投影・既存graph保持に無関係な変化はない。** 製品採用の不合格判定は維持する。今回のAPI使用は0回。

## 1. 原因と修復範囲

[フェーズ16審査](template-ai-phase16-review-2026-10-11.md)のH1再現を使用した。formがラベルc9の親にc8を指定し、別fieldが同じラベルの親にc2を指定すると、通常のformでは競合を検出する。しかしlabelColumnsを `[k1,k1]` にした不正formでは、保留時にparentsを渡さず、別fieldを受理していた。

修復したファイルは外部コピーの `runtime/compact.py` だけ。値の役割・値所有・親設定を集める `field_claims` をfieldとformで共有し、同じ行/列指定から得た値・ラベルを正常/保留の両経路で使う。保留formもparentsを共通normalizerへ渡すため、既存のPARENT_CONFLICTとPROTECTED_FACETの検査が働く。

宣言の順番で採用する、保留宣言を競合検査から除く、親を近接で推測する処理は追加していない。既存normalizer/compiler/manual adapter、schema、prompt、モデル設定、候補builder、snapshot/scope、入力画像・goldは変更していない。Phase15/16のソース・結果を上書きせず、変更前後の差分は外部 `compact.patch` に保存した。

この修復は収集可能な宣言を保留時にも維持するものであり、未知参照や成立しない行指定から意味を推測して復元する機能ではない。新しいprovider契約や性能方式の実装は今回の範囲外。

## 2. 修正前後の短い検証

修復前に `acceptance.py before` で入力と期待結果を固定した。6条件で既知の欠落を検出し、修復後は同じ検証を `acceptance.py after` で実行した。既存fixtureの変更やassertionの弱体化はない。

| 条件 | 修復前 | 修復後 |
| --- | --- | --- |
| 単独fieldを受理 | 通過 | 通過 |
| 正常formの3欄・3結合を保持 | 通過 | 通過 |
| 正常formと異なる親を指定するfieldの競合 | 通過 | 通過 |
| 保留formと異なる親を指定するfieldの競合 | 失敗 | 通過 |
| 上記の宣言順を逆にしても競合を検出 | 失敗 | 通過 |
| 保留formと同じ親を指定する独立fieldは受理 | 通過 | 通過 |
| 列重複だけでなく、値列とラベル列が重なる不正formの親競合 | 失敗 | 通過 |
| ラベル列順が不正なformの親競合 | 失敗 | 通過 |
| 空ラベルを持つ不正formの親競合 | 失敗 | 通過 |
| 保留formでも手動parent facetの保護を検査し、無関係fieldは維持 | 失敗 | 通過 |
| 保留formとの値所有重複を検出 | 通過 | 通過 |
| 保留formとのrole競合を検出 | 通過 | 通過 |

合計は修復前6/12通過・6失敗、修復後12/12通過。すべて物理ID・geometry・取得textの不変も確認した。主再現では、別fieldを保留し、c9の親をc2へ反映しない。正常formは行ごとの独立fieldであり、競合のない行まで一律に取り消す仕様へ変更していない。

結果は `before-acceptance.json` / `after-acceptance.json`。入力、受理結果、保留理由、親宣言、compile後の意味投影を保存した。これは製品外の短い境界検証であり、通常HTTPやDB保存の成功ではない。

コピー準備で `v3_contract.py` の依存漏れにより最初のimportが失敗した。修復・受入検証の開始前にPhase15の同一ファイルをコピーし、初期hashへ記録した。`initialization-note.json` に保存しており、API送信・通常データ変更は発生していない。

## 3. 保存24応答の再生

`replay_saved.py` はPhase15のraw24応答を再decodeし、修復後のresolver・共通compiler・manual変換へ通した。元のrequest/source/input/gold hashとscopeを照合し、実bindingからの別実装採点を維持した。

| 確認対象 | 結果 |
| --- | ---: |
| 保存応答の再decodeと固定request/scope | 24/24一致 |
| 採点・誤結合・欠落 | 24/24一致 |
| 受理/保留・意味単位・保留理由 | 24/24一致 |
| 保存された案と修復後案の名前/共有/mark/親/表/行 | 24/24一致 |
| 共通compileからmanual変換への意味投影・物理参照 | 24/24保持 |
| 既存graphのno-op（全ID/pathを含む） | 24/24完全一致 |
| 外部runtimeの変更範囲 | compact.pyのみ、他381ファイル不変 |

結果は外部 `saved-response-review.json` と `runtime-difference.json`。修復前runtimeのhashは `runtime-before.json`、修復と検証のhashは `implementation-manifest.json`。

保存24応答には今回の診断宣言が含まれないため、採点は変わらない。E4の全ページは分類126/176、結合62/78、行10/14、記号8/16、重点誤結合0・欠落16。Phase15の記録時間を再集計した中央値8.178秒、5秒以内0/10も同じである。**新しいAPI品質や速度を測った結果ではない。**

## 4. 変更境界・保持確認

実行した主要コマンドは `setup_runtime.py`、`acceptance.py before`、`acceptance.py after`、`replay_saved.py`、`audit_state.py before/after`。外部の受入検証と保存応答再生で確認できる修復のため、実OpenAI、実マイク、製品の全件unit/E2Eは実行していない。

repository変更は本書、AGENTS、Safety Contractの現在地だけ。通常製品ソース、OCR provider/prompt、physical identity、mapping/crop、受付snapshot、補正revision、確定・出力実装を変更しておらず、**既存OCR contractを維持した**。通常DBへの移行・機能の有効化はしていない。

終了時の `preservation.json` で両checkout/HEAD、両DB全37表とschema、通常設定hash、863通常ファイル、781 artifact SHA、Phase11〜16の証拠を比較する。旧 `llm-ocr` は変更しない。`git diff --check` と今回の差分範囲・参照も確認する。

**API使用0回、累計336/496、残160回。** 台帳は変更していない。

未検証は、未知帳票・全欄の意味正解、新しい実API速度/精度、実HTTPの所有/反映/receipt/取消、DB保存・再取得、画面・実マイク、矩形生成、OCR実行・XLSX出力、frontend build・製品全体回帰。今回の修復でこれらの受入や採用条件を満たしたとは扱わない。

## 5. 次の区切り

フェーズ17は製品外guard修復として完了。全ページ・任意表選択の製品採用不合格は維持する。追加API比較や製品接続へ自動進行しない。

次は**フェーズ18：Astra / XHigh、性能方式と生成契約の設計審査、API0回**を提案する。最初の検証済み小範囲案と全ページ完了の優先順位、任意選択/自動候補と参照contextの縮小、axis/labels・名前導出の契約整合を判断し、比較する条件と上限を固定する工程とする。実装・実API測定はその後の別工程に分ける。

各フェーズの区切りで指示を待つ。実マイクが必要な工程では利用者を呼ぶ。
