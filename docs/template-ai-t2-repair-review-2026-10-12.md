# T2修正実装の独立審査

2026-10-12。**Phase24修正revision2を製品外試作として受入とする。修正を差し戻す欠陥は審査範囲で確認されなかった。** 固定比較の500ms条件と意味保持条件を満たしており、フェーズ25の実API比較へ進む前提は成立した。今回の依頼は審査までであり、フェーズ25は未開始・未予約である。製品採用・通常有効化の判断ではない。

対象checkoutは `C:/Users/hanam/Desktop/llm-ocr-deploy`。審査対象は `D:/llm-ocr-archive/template-ai/20261011-phase24-repair` の最終版、証拠は `D:/llm-ocr-archive/template-ai/20261012-t2-repair-review` に保存した。工程上のモデル指定はAstra / XHighであり、チャットの実モデル設定を検証した記録ではない。API使用0回、共有台帳366/496、残130回。

## 審査結果

| 観点 | 確認結果 |
| --- | --- |
| 対象の固定 | 修正実装・結果・文書を含む500成果物hashを開始時に照合。最終lock SHAは `071bbabedb5502f8b7fa1d1b6527a2029fed655859ca612e45cc0dc2571f54e9` |
| 変更境界 | 元runtime399ファイル中398は同一。既存変更は `expand.py` の入力型受付、追加は `verified_snapshot.py`。factor生成器と修正計画はbyte単位で不変 |
| 入力再利用 | 新規生成1回、restoreは所有Expected・画像・policy・digestと再生成候補を比較。runの所有proofと防御コピーを確認 |
| 失効 | ファイル内容・集合変更、別run、現在のrevision/scope/画像/同値手動facet変更を拒否。実装検査失敗後にファイルを戻しても同じleaseとsnapshotは復活しない |
| 処理区間 | 予約前に区間を閉じ、応答後に再開。Responsesは2区間、連続二段Decisionsは3区間。個別first/secondの呼出し元への復帰にも境界を設ける |
| 意味と対応 | 保存oracle5範囲で完全manual意味投影、physical identity、既存graphの全ID/pathを含むno-opを再確認 |
| 共通経路 | 全claims・保留依存閉包・scope外保護・共通compiler/manualを維持。モデルから直接保存する経路を追加していない |

内部Pythonオブジェクトを検証済みとして扱う範囲を確認した。これは任意コード実行を隔離する仕組みではない。製品接続時の所有DB・revision再照合と保存transactionは今回の外部試作に含まれない。

## 速度の判定

保存された旧新各15件、計30計測から区間合計・中央値・短縮率を再計算し、事前固定した実行順と照合した。新たな無計装計測による採点更新や、都合のよい標本への差替えは行っていない。

| 範囲 | 旧版中央値 | 修正版中央値 | 500ms条件 |
| --- | ---: | ---: | --- |
| T1 | 504.26ms | 306.03ms | 合格 |
| T2 | 751.75ms | 472.96ms | 合格 |
| T3 | 364.25ms | 188.51ms | 合格 |
| H1 | 388.76ms | 197.38ms | 合格 |
| H2 | 368.28ms | 191.03ms | 合格 |

T2は37.09%短縮。3標本は463.64 / 472.96 / 482.30msで、中央値の余裕は27.04msに限られる。400ms前後の設計目安には達していないが、合格閾値は500msで固定されている。各3標本でp95・最悪時間・未知帳票を保証しない。

タイマーは入力読込・新proof発行・実装照合・pack・plan・選択検査・共通compile/manualを含む。import済みの条件と、oracle読込/選択・採点・追加no-opが外側であることを確認した。wire組立・通信・実モデル・UI・DB保存は対象外。起動からの時間や音声の応答時間とは扱わない。OSキャッシュを除去した比較でもない。

今回の計装再生でも全5範囲でgeometry生成1回、実装照合2回、現在性4検査、別々のschema検査2回を確認した。計装時間は速度判定に混ぜていない。

## 独立して実行した検証

凍結元を変更せず、393ファイルの内容が同じ隔離コピーを作成した。manifestの絶対パスのみコピー先へ置換し、試験scriptの履歴参照先も明示した。候補・正解・assertion・製品ソースを変更していない。

- 既存83境界：全件再現。元Phase24の83項目名を保持し、修正実装の保存結果と一致した。
- 追加48境界：全件再現。再配置したlock SHA以外は保存結果と完全一致した。
- 旧30応答：26受理・4拒否、採点・時間・usage、受理26件のmanual意味投影と既存graph完全no-opを再現。結果JSONも一致した。
- 保存oracle5範囲：完全manual投影・physical・全ID no-opを別scriptで再確認した。
- 追加の審査用2条件：応答待機中の同値手動facet変更を `CURRENT_BINDING_STALE` で拒否。ソース内容が同じでも待機中のmodule reloadを `IMPLEMENTATION_MODULE_STALE` で拒否し、lease永久失効を確認した。いずれも予約は1件を保持し、応答から下書きを返さない。

実行環境はdeployの `backend/.venv/Scripts/python.exe -B`。主なscriptは審査証拠内の `prepare_review.py`、隔離コピーの `boundary_acceptance.py` / `repair_boundary_acceptance.py` / `legacy_replay.py`、`independent_probes.py`、`verify_scope.py`、`audit_state.py before/after`。最後に `git diff --check` を実行した。

静的差分表示でWindows既定文字コードによる読込エラーが1回発生し、UTF-8を明示して再読込した。実装の試験失敗ではなく、保存資料・実装には変更を加えていない。

## 保持と限界

本審査のrepo変更は本書、[審査結果JSON](contracts/template-analysis-v7/t2-repair-review.json)、AGENTS/Safety Contract/v7索引への現在地追記のみ。前の本文・実装結果の「審査待ち」は当時の記録として保持する。元Phase24の時間不合格、修正revision1の結果、修正設計、最終revision2の全計測を上書きしない。

終了監査で製品ソース、両repoのHEAD、両通常DB各37表/schema、設定、業務863ファイル/781 artifact、旧checkout、Phase11〜24・修正設計・修正実装の証拠を開始時と照合した。**既存OCR contractを維持した。** 根拠は製品不変性と上記意味/physical/no-opの再生であり、製品全体の実機動作を再認定した意味ではない。

全件unit、frontend build、実HTTP/実API、製品DB競合、保存/undo、ブラウザー、実マイクは未実施。今回のモデル品質・実APIレイテンシー・認識誤り件数は未測定であり、0件とは報告しない。

次工程は**フェーズ25：GPT-6.1 Sol / High、APIモデルGPT-6 Luna、固定5範囲×2反復・最大20送信・retry0**。次工程の明示指示で開始する。新しい実装・実測結果はこの凍結版へ束縛し、通常導入はフェーズ26の採用審査と製品接続の検証を経て別に判断する。
