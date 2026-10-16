# AIテンプレート接続検査の修正2実装

2026-10-13。対象は `C:/Users/hanam/Desktop/llm-ocr-deploy`、実行モデルはGPT-6.1 Sol / High。
[統合設計](template-ai-structure-repair-design-2026-10-13.md)の修正2を実装した。
通常欄と表列へ共通の接続検査を適用し、独立審査R1の迂回を案の公開前に拒否する。
矩形AI編集・画面接続・実Luna品質・5秒・最終4帳票評価は後続工程に残る。

## 原因と修正範囲

旧form.v1では通常欄だけが発行済み接続又は見出しなしの根拠を要求し、表列は自由な`labels`を受理した。
R02のc53を1行1列表にした根拠欠落／遠いc1への接続と、c53/c54を2行にした同じ2反例を、変更前に共通compilerで再現した。
新form.v2では通常欄・表列とも`binding`を必須とし、同じBindingResolverで検査する。
旧form.v1は明示拒否する。内部の規則表展開・保存済み応答検証のvisual形式を、新しいモデル回答の互換入口として公開しない。

| ファイル | 最終変更 |
| --- | --- |
| `backend/app/template_analysis/binding_resolution.py` | 発行候補・同一入力/方向・根拠・書込範囲の共通解決、表本体の幾何検査 |
| `backend/app/template_analysis/visual_form_answer.py` | form.v2、通常欄/表列の共通binding、根拠を全行へ展開 |
| `backend/app/template_analysis/visual_compile.py` | 新回答の受入と旧form.v1の明示拒否 |
| `backend/app/template_analysis/visual_isolation.py` | 表列の根拠も競合分離の依存関係に含める |
| `backend/app/template_analysis/visual_graph.py` | 文字列の表にも根拠説明とreadSetを付ける |
| `backend/app/template_analysis/visual_provider.py` | 新schema/指示、runner.v4と共通解決policyを固定 |
| `backend/app/template_analysis/runtime_contract.py` | 旧visual定義の案・反映記録を共通検査する |
| `backend/app/template_analysis/repository.py` | 取得・反映時にも定義の現在性を検査する |
| `backend/app/editing/authoring_provenance.py` | AI反映記録付きの共通保存前に定義を再照合する |

表列は最初の行の対象セルへ接続する。vertical/directだけを受理し、水平見出しの通常欄を表列として読み替えない。
全行の列数・一意所有・上から下/左から右の順序・行内Y投影・列のX投影50%以上・2pxを超える内部交差を検査する。
各列の全行を覆う領域内に未所属セルを残せず、`ignored`宣言でも除外できない。
等間隔・同じ高さ/幅は要求しない。見出しなしの根拠は全行のプレビューとreadSetへ伝える。

追加確認で、旧定義のready案が反映され、旧反映記録も保存される欠落を専用DBで再現した。
送信/公開時だけだった定義照合を取得・反映・共通保存にも追加した。
変更前は反映HTTP200・保存APPLIED、変更後は反映409/SNAPSHOT_STALE・取得stale・保存CONFLICT/SNAPSHOT_STALEとなり、revision1と未設定の欄を保持した。
これは現在性検査の追加であり、通常データ・過去の保存済みテンプレートを移行するものではない。
既に完了した同一要求のreceiptを再取得する冪等経路は、新規編集とは区別して維持する。

## 検証結果

証拠は `D:/llm-ocr-archive/template-ai/20261013-form-binding-v2`。
検証用script・専用DB・応答・失敗ログは製品外へ保存した。

| 実行 | 結果と証拠水準 |
| --- | --- |
| `check_bindings.py` | 50条件通過。変更前4反例、旧形式/自由labels/未知・他入力のb参照/根拠欠落/幅・行列・途中セル・readonlyの拒否、正例とclosed/required schemaを確認 |
| `integration_tables.py bad0 bad1 bad2 bad3 bad4 bad5 bad6 bad7 one irregular unlabelled marked stale_merge stale_save` | 実ASGI・専用DB・制御HTTPで86条件通過。不正8応答を公開せず、1行1列表・不等幅/不等高の3行表・見出しなし列・記号列を明示反映/共通保存/別接続再読込。根拠への同値手修正は表全体の反映を拒否。旧定義の反映/保存も拒否 |
| `integration_ordinary.py` | 45条件通過。旧fixtureの意味を保ったform.v2再符号化でR02の22保存先/20接続/19記号を保持。R01の実保存回答は規則表190欄のまま共通保存。予算/候補上限は送信前拒否 |
| 対象9ファイルの `ruff check` / `mypy --follow-imports=silent` | 通過。`ruff-final.log`、`mypy-final.log` |
| `seal_phase.py` | 開始時との差分、通常DB/ファイル/台帳、旧checkout・過去証拠・最終4原本のhash、`git diff --check`を照合 |

最終の表検証runは`integration/4a7db126-1748-4311-ad69-68c518d915a3`、通常欄/規則表は
`integration/341aead6-d9ff-4d3d-94d4-7d1db8f7f8c3`。
前者14件・後者2件のHTTPは全て制御用で、通常API予約・実OpenAI送信は0。
途中の失敗/修正前再現は別runと別ログに保持し、成功runの証拠と混在させない。

新規の合成例で初期座標が規則表の条件を満たし、formでの本体直接参照が正しく拒否されたため、規則表経路の検査は維持して別の不規則座標例を作った。
その他の途中失敗にはfixtureのID/保存構造/compact metadataの読み違いとimport順序があった。
`validation-notes.json`で、製品欠落の再現と検証側の訂正を区別する。既存正解・過去の原本・応答・失敗ログは変更していない。

## 維持した契約と未検証範囲

新しい案は既存のCommandCompiler、MutationCore、TemplateAuthoringServiceへ渡し、AI専用保存は追加していない。
共通保存で生成する新しいmappingは明示反映した内容に限る。
物理cell ID・座標・印字、保存先の一意性、行別記号の意味、明示保存のrevisionを上記専用環境で確認した。
通常OCRのprovider/model/prompt/schema、association、crop/入力画像、受付snapshot、補正・確定・出力実装は変更していない。
既存OCR contractを維持した。通常の設定・データ・既存台帳は保持し、実API0、432/924・残492。

幾何的な候補と根拠形式の検査は、原画像に対する意味の正しさを自動証明しない。
実Lunaによる新schemaの受入・品質・応答速度、新実装の独立審査、Chrome、矩形AI、R04の正解基準確定、未使用4帳票/手作業比較は未検証。
frontend・DB schema・公開解析APIのv2化はこの修正2に含めていない。全件unit/E2Eは実行していない。
現在の連結成分単位の解析計画も維持し、矩形のない領域を扱う全ページ要求は修正3で実装する。

次は修正3、GPT-6.1 Sol / Highで共通GeometryCompiler、未保存手描きの順序保持、矩形と意味付けの一体化を実装する。
その後の修正4で画面へ接続し、修正5のAstra / XHigh審査を経て修正6の実API測定へ進む。
修正2の受入はゴール全体の完了を意味しない。
