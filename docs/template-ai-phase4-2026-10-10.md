# AIテンプレート作成・フェーズ4：バックエンド実装

開始2026-10-09、完了記録2026-10-10。開発対象は `llm-ocr-deploy`。
設計は[フェーズ3](template-ai-phase3-2026-10-09.md)。APIモデルは **gpt-6-luna**。
バックエンドの接続・保存境界の限定検証を完了した。画面接続、AIの品質・速度受入、通常環境への導入は未完了。

## 実装したもの

- 全体解析はページごとのResponses、種類だけは8候補のDecisions。自動補完・別モデルへの切替・自動再試行なし。
- 編集session所有の解析run、取得、認証SSE、キャンセル、下書き反映。解析・反映では業務layoutを保存しない。
- 保存版・revision・編集context・scope・画像SHA・物理セル・手元下書き・provider指示/schema digestを固定する。
- 全セルの分類、入力欄の一意所属、表の明示行列、見出しの循環・自己参照・ページ外参照を検査する。
- 意味の欠落は未解決として返す。markの選択肢定義など、この版で扱わない構造を生成しない。
- 手動facetと由来不明の設定を保護する。新規検出の分類だけ既知の未確認状態を記録し、旧データへ遡及推定しない。
- 反映は基準・現在の手元下書き・AI案の三者を比較する。同値の手動再指定も保護し、独立した変更単位だけ反映できる。
- 保存は既存 `manual-authoring → ManualCompiler → MutationCore → TemplateAuthoringService` を使う。
  反映記録を新しいIDへ対応させて由来を保存し、revision・receipt・undoを共通化した。
- 内部ManualDraftは5000操作／参照20000／相異なる物理セル1000。公開指示は50操作／参照1000を維持。
  labelPool等の保存上限も維持し、内部案の超過を `AUTHORING_CAPACITY_EXCEEDED` で拒否する。分割commitはしない。

既存セルを分類変更して同じ保存内で見出しへ接続する場合、旧catalogにheading handleがなく保存できなかった。
既に所有を確認したcell handleを共有commandへ渡す修正をManualCompilerに追加した。
別の保存経路やscope回避を追加するものではない。

## ファイルと責任

| ファイル群 | 役割 |
| --- | --- |
| `backend/app/template_analysis/contracts.py` | 外部提案・HTTP・SSEの閉じた型 |
| `graph.py` / `compose.py` | 対応検査、依存単位、共有compilerへの合成 |
| `snapshot.py` | 版・所有画像・物理対応・手動設定の固定 |
| `provider.py` | Luna固定、Responses/Decisions、有限回数・timeout・計測 |
| `models.py` / `repository.py` / `service.py` | 解析状態、再送・期限・再起動・競合・反映記録 |
| `backend/app/api/v2/template_analysis.py` | ローカル認証・要求サイズ・HTTP/SSEの薄い入口 |
| `backend/app/editing/authoring_provenance.py` / `facet_contracts.py` | 手動/AI/未確認/不明のfacet保護と保存 |
| `backend/app/editing/compilation_limits.py` / `commands.py` / `manual_compiler.py` | 用途別上限と共通変換 |
| `backend/app/editing/infrastructure/mutation_uow.py` | 既存保存transaction内で由来を記録 |
| `backend/app/services/template_service.py` | 試験有効時、新規検出の由来だけを記録 |
| `backend/app/core/config.py` / `lifecycle.py` / `main.py` | 既定無効、起動入口、終了時のtask解放 |
| `backend/alembic/versions/0009_template_analysis_v1.py` / `env.py` | 新しい5表。layoutの変換なし |
| `backend/scripts/generate_template_analysis_contracts.py` | 型を正本にschema/TypeScriptを生成 |
| `docs/contracts/template-analysis-v1/` / `frontend/src/editing/template-analysis.generated.ts` | 次工程が使用する生成契約 |
| 既存editing契約・生成器の出力 | 反映記録と手動facet journalを既存保存へ渡す型 |

`LayoutCell.source`、既存mapping/path、crop・受付snapshot、OCR判定・出力処理を意味編集の由来で置き換えない。
新しい意味のID/pathは既存server割当から生成する。既存OCR contractを維持した。
通常環境の業務DB全テーブル、旧リポジトリのソースとDBの開始・終了比較を行った。
ただし全領域OCR回帰や新規paged保存/出力をこの限定検証だけで完全に検証したものではない。

## APIと導入状態

基点：`/api/v2/editing/sessions/{session_id}/template-analyses`。
POSTで解析受付、`/{analysis_id}` GETで取得、`/events` GETでsequence再開可能なSSE、`/cancel` POSTで取消、`/merge` POSTで下書き反映。
`X-Editing-Session` と既存bindingを使い、クライアントからmodel・画像path・promptを指定できない。
POSTの上限2MiB、解析期限30分、同時provider要求2、1sessionの実行中runは1。
同一要求は再送しても追加送信せず、同一snapshotの実行中要求は同じrunへまとめる。
キャンセル後の遅着結果は採用せず、再起動時の未完了run/pageはINTERRUPTEDで終了する。

追加設定は `TEMPLATE_ANALYSIS_ENABLED=false`。通常`.env`は変更していない。
通常DBは未移行、通常editingとAIテンプレート解析は無効のまま。
移行・実サーバー起動はDドライブの専用コピーだけで実施した。従来の音声試験サーバーは操作していない。
検証用8034はこの工程の終了時に停止する。画面や本番用の起動手順ではない。

## 検証と結果

成果物：`D:/llm-ocr-archive/template-ai/20261009-phase4`。
製品外のscriptをdeployの `.venv/Scripts/python.exe` から実行した。全件unit/E2Eは実施していない。

| 検証 | 結果と限界 |
| --- | --- |
| `prepare_copy.py` | 開始時DB退避から専用コピーを作成しAlembic headへ移行。通常DBへ移行なし |
| `verify_http.py` | 実HTTP・実SQLite・実storage・共通保存24条件。外部providerだけ固定応答 |
| `verify_more_http.py` | 範囲/画像変更、要求内容衝突、旧分類保護、明示解除、期限、保存実応答再生等12条件 |
| `verify_boundaries.py` | 完全性・重複・行幅・循環・unknown・26項目/52セル・公開51操作拒否・保存上限等24条件 |
| `verify_lifecycle.py` / `runtime.log` | 実lifespan終了、再起動時の未完了run/page終了、自動再送なし |
| 対象ruff / mypy | 新機能13ファイルと共有変更の対象検査。新規・今回変更行にエラーなし |
| `npm run build` | TypeScriptとfrontend build成功。AI解析画面はまだ実装していない |
| `finish_audit.py` | 両repoのHEAD・既存差分・全テーブル比較、通常業務863ファイルの開始時コピー比較、deploy所有artifact781件のSHA照合 |
| `git diff --check` | 成功。staging/commit/pushは実施なし |

保存検証では一度だけrevisionが増え、再送で再更新せず、取り消しで元layoutへ戻った。
AI由来の記録も共通compilerによる新IDの置換後に保持された。
開始時から存在する `template_service.py` のruff長行4件、mypyの既存arg-type1件は今回の検証でも残る。
開始時退避ソースとの照合で既存と確認し、無関係な全体整形・型修正は行っていない。

旧 `llm-ocr` のDBには現在の配置に存在しない過去artifactへの参照が多数ある。
この工程でその存在を開始時から比較していないため、旧repo全artifactの健全性は保証しない。
旧repoのソース・全テーブルは開始時と一致しており、旧artifactの修復・移動・削除は行っていない。

## 実API：3回、品質受入は未完了

共有台帳は **222 → 225 / 496、残271**。上限8送信以内。業務保存は行っていない。
固定T3画像を使い、新しいstrict schemaとDecisionsの実接続を確認した。

| 要求 | 結果 |
| --- | --- |
| 全体Responses 1回目 | completed JSON。labelを持つfieldをdirectと指定し、graph検証で拒否 |
| 種類Decisions | 応答・種類graphの検証成功。12セル中4セルはunknownとして保持 |
| 全体Responses 2回目 | completed JSON。markをfieldへ含め、parent_labelをlabelにも使用したため拒否 |

最初の拒否後にaxis条件を指示・schema説明へ追加し、全体だけ一度再確認した。
2回目も不整合があったため、品質改善の反復はこの工程で続けていない。
最後にinput/mark・label/parentと自己参照の禁止も指示へ明記した。この最終指示の品質は未測定。
保存済みの不正な2応答は期待結果を変更せず拒否する再生検証へ固定した。
APIがJSONを返せることと、正しい意味付けを返すことは別である。高速化・実用精度の達成は主張しない。

## 次の工程

**フェーズ5：GPT-6.1 Sol / High**。既存editorに解析・案確認・部分反映・保存・手動facet journalを接続する。
複数セル枠、ページごとの検証済み案、保護理由、未解決、競合、失敗・再取得・遅着を表示する。
種類だけの機能は補助として扱い、品質を確認せず通常の自動保存へ昇格させない。

フェーズ6はAstra / Highで独立監査と重い回帰。budget競合、複数ページ、既存mark/composite/identity保持、
保存response・crop・XLSX対応と関係する全体回帰を確認する。
フェーズ7はGPT-6.1 Sol / Highで同条件のAI品質・速度・修正完了時間と実画面受入。
全体Responsesの不正案が残る状態では通常開放しない。実マイクが必要になった時点で利用者を呼ぶ。
