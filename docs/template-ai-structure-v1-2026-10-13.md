# AIテンプレート矩形編集と共通保存の実装記録

修正3として、手描きとAIが共通の物理編集検査を使う構成へ変更した。AIの矩形操作と意味付けを一つの回答・依存単位で検証し、「案を反映」で未保存下書きへ反映、「確認して保存」で既存の共通保存へ渡す。画面での矩形差分表示、独立審査、実Lunaの精度・速度、最終評価は後続工程であり、製品ゴールの完了ではない。

対象は `C:\Users\hanam\Desktop\llm-ocr-deploy`。実行モデルはGPT-6.1 Sol / High、製品APIモデルはGPT-6 Luna。今回の実外部API使用・通常台帳の予約は0回。共有台帳は432/924、残492回を保持する。

## 実装と変更境界

| 対象 | 変更内容 |
| --- | --- |
| `backend/app/editing/geometry_compiler.py`、`manual_compiler.py` | 手動アダプターから物理編集を共通処理へ切り出す。既存ID、由来、結合履歴を検証し、利用者のセル配列順を保持する |
| `geometry_contracts.py`、`geometry_intents.py` | add、resize、split、merge、remove_duplicateの閉じた操作。所有範囲、整数座標、重なり、隣接、分割内包、95%以上の重複被覆を検査する |
| `draft_catalog.py` | 共通手動compilerで検証した未保存枠だけに内部参照を発行する。別ページへの権限拡張を禁止する |
| `authoring_provenance.py`、`facet_contracts.py` | geometryの由来、集合・順序、同値の手修正、削除履歴を照合する。AI追加枠を未保存のまま手動削除できる |
| `backend/app/template_analysis/structure_contracts.py`、`structure_compile.py` | structure.v1の矩形と意味付けを一体で検証する。新しい枠の参照はn、既存枠はc。正規ID・handle・保存先をモデルに発行させない |
| `snapshot.py`、`visual_input.py`、`visual_pack.py`、`visual_grid.py` | 保存前の手描き下書きも解析対象にする。全体画像では原画像全域を1要求で扱い、検出0枠でも開始できる。既存規則表の短縮経路を維持する |
| `visual_compile.py`、`compose.py`、`visual_runner.py`、`visual_provider.py` | 物理編集後に同じBindingResolver・CommandCompiler・共通保存検査を使う。通常欄と表列の接続検査を迂回しない |
| `contracts.py`、`graph.py`、`repository.py`、`runtime_contract.py`、`service.py` | 公開APIをtemplate-analysis.v2へ切り替え、矩形差分を返す。内部geometryPlan・保存操作は公開しない。旧解析・旧反映記録の利用を拒否する |
| `frontend/src/editing/authoringFacets.ts`、`templateAnalysisStore.ts`、同test | 削除した枠の手修正記録も保存へ渡す。未保存の取り消しは矩形と意味付けを一体で復元し、古い構造案の世代を失効させる |
| `TemplateAnalysisPanel.tsx`、生成DTO・JSON schema、生成script | 新しい構造単位の最低限の型接続。矩形差分の描画・画面操作の受入は修正4に残す |

矩形とその依存する意味付けは一体で反映する。依存先が矛盾・保護対象なら構造単位を保留し、依存しない有効な欄は別単位として保持する。変更した端点に古いb候補を流用することは禁止し、derived_linkも編集後に再発行した幾何候補との一致を要求する。

AIが変更できる既存枠は、サーバーが未確認又はAI由来と確認でき、意味未設定・未保護の矩形に限る。過去の由来不明枠、手描き枠、手動分類・意味、既存mapping、記号・複合欄、多角形、表の見出し・行キーは勝手に変更しない。初回検出時だけgeometryをunreviewedとして記録する。

新しい構造編集は単一原画像の全体範囲に限定する。局所録音・選択表は既存の意味付けの所有範囲を維持し、枠を新設する権限を追加しない。XLSX／pagedの物理編集・変換・資格経路も追加開放しない。未保存の手描きから解析する改善は単一原画像に適用する。

## 意図した非互換変更

公開のcreate／result／merge／eventsはtemplate-analysis.v2、構造回答はtemplate-analysis.structure.v1、実行定義はvisual-runner.v5とした。分類のみの旧解析を含め、旧公開定義の反映・保存を失効させる。frontendの復旧情報はv2の別キーを使用し、旧キーを書き換えない。

通常欄はform.v2、規則表は既存grid.v1の検査を内部で利用する。旧v1 schema文書は履歴として保持する。現行のHTTP・event・構造回答・通常欄回答・規則表回答は `docs/contracts/template-analysis-v2` に生成する。

過去のOCR結果、受付snapshot、crop、画像前処理、OCR provider、XLSX出力adapter、通常データ移行は変更対象に含めていない。新規テンプレートの矩形生成契約を意図的に拡張し、既存セルの対応関係・出力先を維持する共通保存検査へ接続した。

## 検証結果

証拠・script・隔離DB・制御応答は `D:\llm-ocr-archive\template-ai\20261013-structure-v1` に保存する。API検証は実ASGI、実service、実compiler、専用SQLite、共通の明示保存・別接続の再読込を使う。有料providerのHTTP transportだけ制御し、ネットワーク接続を拒否した。Chromeや実Lunaで成功したという意味ではない。

| 検証 | 結果と証拠 |
| --- | --- |
| 新構造の短い境界検証 | 34項目通過。`check_structure.py`／`structure-checks.json`。追加・縮小・分割・結合・重複除去、古いb、未知n、モデルのsource/ID、越境、保護、別ページhandle、独立単位の保持、旧分類案の拒否を確認 |
| 構造編集の実ASGI | 10事例・112項目通過。`integration_structure.py`／`structure-integration-latest.json`。保存前後、再送ID、出力binding、再読込、AI追加枠の手動削除・サイズ修正、由来・削除digestを確認 |
| 通常欄・表列の境界回帰 | 50項目通過。`check_bindings.py`／`checks.json`。前工程の意味・接続・禁止条件の期待値を維持 |
| 表の実ASGI回帰 | 86項目通過。`integration_tables.py`／`tables-latest.json`。1行表、不規則表、見出しなし列、記号列、旧定義の反映・保存拒否を確認 |
| 通常欄・規則表の実ASGI回帰 | 45項目通過。`integration_ordinary.py`／`integration-latest.json`。22欄・20接続・19記号設定、規則表190欄、手動名称修正、正規mapping・矩形・再読込を照合 |
| frontendの対象検証 | `npx vitest run src/editing/templateAnalysisStore.test.ts`：19件通過。削除・新規枠・取り消し・同値操作・遅延返信・復旧・明示保存の記録を検証 |
| frontend build | `npm run build`通過。`frontend-build-final.log` |
| 対象静的検査 | 対象24 Pythonファイルのruff、mypy通過。mypyは`--follow-imports=silent`。通常のimport追跡では範囲外11ファイルの61エラーが出たため、backend全体の型検査成功とは扱わない |

前工程の検証scriptは新しい証拠フォルダーへ複製した。公開v2・runner.v5と、制御form.v2をgeometry空のstructure.v1へ包むadapterのみ変更し、旧正解・意味・対応・保存の期待値を弱めていない。旧フォルダーは封印したまま保持する。初期失敗ログとfixture修正理由も残す。

手描きR3は、79セルから重複2枠を除き5枠を追加した未保存下書き82セルで確認した。利用者が与えた順序を保持して解析・保存・再読込が成功し、無関係77セルは完全一致した。AIの構造操作を制御したR04例でも同じ77セルの保持を確認した。この5枠は保存経路の構造検証例であり、R04全体の精度正解ではない。

R04全ページの入力一覧は前工程で原画像を確認して122観察項目・5曖昧群を準備済みである。`20261013-r04-evaluation-inventory/inventory.json` は独立審査待ちの暫定資料であり、許容領域・全ページgoldの固定と精度採点は修正5以降とする。既存79矩形や今回の5枠を原本全体の分母へ代用しない。

## 要求数と速度の確認範囲

開発用5ページはすべて各1要求の準備を通過した。`measure_full_page.py`／`full-page-capacity.json`に原画像SHA・要求形式・metadata量を記録した。

| 入力 | 既存セル数 | 要求形式 | ローカル準備時間の観測値 |
| --- | ---: | --- | ---: |
| R01 1ページ目 | 200 | 規則表・none | 99ms |
| R02 1ページ目 | 257 | 構造＋意味・low | 625ms |
| R02 2ページ目 | 54 | 構造＋意味・low | 274ms |
| R03 1ページ目 | 251 | 構造＋意味・low | 678ms |
| R04 1ページ目 | 79 | 構造＋意味・low | 344ms |

これは画像投入、モデル推論、完成回答の検証、画面描画を含む応答速度ではない。各1回のローカル観測であり、5秒目標の達成や優位性は主張しない。既存の共有予約・ページ並行上限・送信後の消費保持を使い、構造のための追加APIラウンドは導入していない。実比較は修正6で実施する。

## 保持と残作業

`before.json`／`after.json`／`preservation.json`で、通常DB全テーブル、通常ファイル863件、artifact781件、設定・台帳、旧llm-ocr checkout、前工程の封印証拠、最終評価4帳票の原本14ファイルを照合する。最終4帳票はhashのみを確認し、画像・内容を調整に使用しない。Gitの既存差分を保持し、staging・commit・pushは行っていない。

今回、既存OCR contractのうち過去結果・受付対応・crop・補正・revision・出力経路を維持した。新テンプレートの矩形契約を拡張したため、OCR安全性全体を完全検証したとの主張はしない。全体unit／E2E、実paged経路、Chrome、実Lunaのschema受入・品質・速度は今回未検証である。

次は修正4をGPT-6.1 Sol / Highで行う。矩形before/after・追加/退役・意味の差分を同じ画面へ接続し、複数セルの同時強調、フォーカス移動、可視領域へのスクロール、古い案の失効を実画面で確認する。続く修正5はAstra / XHighの独立審査、修正6はSol / HighによるGPT-6 Lunaの実API比較、修正7はAstra / XHighの未使用4帳票評価である。
