# AIテンプレート作成：フェーズ1 調査・評価条件の固定

2026-10-09。フェーズ1は文書・評価材料の準備まで。開発対象は利用者の回答により `C:/Users/hanam/Desktop/llm-ocr-deploy` に確定した。APIモデルは `gpt-6-luna` に固定する。製品コード、通常DB、設定、起動中のプロセスは変更しない。次はフェーズ2（GPT-6.1 Sol / High）で、モデル切替後に比較実験を行う。

## 1. 対象と現在の状態

目的は、検出済みの矩形・多角形を入力し、欄の種類、見出しと入力欄の対応、繰り返し表の行・列をAIで提案すること。手動描画・手動意味付けを残す。新しい矩形検出、音声認識の再構築、通常環境への切替はこの工程に含めない。

Chromeの5180画面では「債権者登録（廃止）依頼書」、V1、167セルを確認した。5180のViteはdeployのソースを使用する。通常環境のeditingは無効で、新しい編集用DB移行は未実施。8027の試験backendは別コピーを使用する。画面の存在を新しい編集経路の受入成功とは扱わない。5180の実際のbackendプロキシ先は今回未確定。

開始時点で両リポジトリに多数の既存差分がある。開始時HEADは両方とも `35adae6df378fe288593b113b12615f5c47f2a2a`。差分を破棄・stage・commitしない。`llm-ocr` はH2の画像・layoutを読む用途だけで使用した。

## 2. 調査結果と接続方針

| 実コード | 確認した制約・次工程での使用方針 |
| --- | --- |
| `backend/app/services/template_definition_service.py` | 既存のgenerateは保存済みのroleを先に型へ変換する。多角形・仮想見出し・親見出し・readingModeを含む編集を拒否する経路がある。今回の全体分類器としてそのまま使えない。 |
| `backend/app/services/relationship_analyzer.py` | textとgeometryから関連を推論する既存経路。role分類結果を返さず、画像も送らず、モデルがOCR設定に依存する。テンプレート専用設定・提案境界が必要。 |
| `backend/app/layout_analysis/geometry.py` | 空間隣接等は再利用候補。ただしVALUE型に依存する特徴がある。評価では正解roleを経由した特徴を渡さない。 |
| `backend/app/editing/operation_contracts.py` / `commands.py` / `semantic_commands.py` | 既存の22操作と意味付け・行列・見出し操作を利用する。役割変更にはmark設定・複合欄・readingMode等の依存検証がある。AI都合で依存設定を消さない。 |
| `backend/app/services/template_authoring_service.py` | 共通revision、DRAFT、prepare/persistによる検証と一括保存を利用する。旧TemplateDefinitionを第二の保存正本として追加しない。 |
| `backend/app/editing/infrastructure/mutation_core.py` / `mutation_uow.py` | scope、revision、receipt、undoを含む共通保存境界へ接続する。AI応答からDBへ直接保存しない。 |
| `backend/app/editing/infrastructure/planner_provider.py` / `backend/app/ai/api_budget.py` | 実送信前の共有予算予約を再利用する。音声向けの指示照合promptは画像全体の分類とは分離する。 |
| `frontend/src/pages/TemplatePage.tsx` / `frontend/src/api.ts` | 手動編集・authoring保存・既存のAIフォーカスを利用する。新しい保存状態や強調表示の正本を増やさない。 |

提案する境界は「画像＋既存cell ID＋role非依存の空間情報 → 型付き提案 → 既存操作への変換・依存検証 → 共通authoring保存」。未知・曖昧・候補不足を明示し、解析失敗時に既存の意味付けを空で上書きしない。対象外cell、存在しないID、別ページへの結合、古いrevisionは保存前に拒否する。

deployはschema 2.0の `fields.<key>` を使用する。root側の旧customFieldsや分離保存APIへ戻さない。公開済み版・受付済みsnapshot・mappingId・targetPath・sourceRegionId・crop対応を維持する。`readingMode=number` は自動連番であり、数字文字列の読み取りモードではない。

## 3. 固定した評価材料

保存先：`D:/llm-ocr-archive/template-ai/20261009-phase1`。正本は `cases-manifest.json`。画像、layout、API入力候補、期待値それぞれのSHA256を持つ。元DB・元画像は読み取り専用で取得した。

| ID | 用途 | 帳票 | 入力セル数 | 役割期待値 | 結合期待値 | 行期待値 |
| --- | --- | --- | ---: | ---: | ---: | ---: |
| T1 | 調整用 | 債権者登録依頼書 | 167 | 14 | 5 | 0 |
| T2 | 調整用 | 牛繁殖台帳004 | 185 | 29 | 21 | 3 |
| T3 | 調整用 | 申請受付チェックリスト（既存合成帳票） | 12 | 12 | 5 | 0 |
| H1 | 固定評価用 | 給水装置台帳等複写申込書 | 74 | 12 | 4 | 0 |
| H2 | 固定評価用 | 設備点検票（既存合成帳票） | 21 | 21 | 4 | 4 |
| 合計 | | | 459 | 88 | 39 | 7 |

期待値は画像と座標を照合した重点箇所で、保存済みroleのコピーではない。独立した複数人による正解認定ではなく、全459セルの正解でもない。未採点セルを正解扱いにしない。役割の評価母数は88、結合は39、行は7として別々に報告する。T2は記入済み画像であり、文字OCRの精度試験とは分ける。空欄の先頭行を飛ばさないこと、同名項目の別グループへ結ばないことを含む。

最初の設備点検票候補は画像と保存矩形の位置が一致しなかったため除外した。証拠は `excluded-candidates.json` と元の `cases/H2` に保持。採用版はmanifestの `caseDirectory=cases/H2-valid` を使う。画像SHA一致だけでは画像とgeometryの対応を保証できないことを確認した。元データの修復はしていない。

APIへ渡せるのは各caseの `reference.png` と `input.json` に限定する。後者はcanonical寸法とcellのID・座標・text・vertices・shapeTypeのみ。`source-layout.json`、`expected.json`、manifest内の保存role集計・名前・元パスは解析入力に含めない。採点データは応答取得後だけ読み込む。

## 4. フェーズ2の比較・予算

同じ入力で次の二方式を比較する。採点可能な共通提案形式へ変換し、保存は行わない。

1. Responsesのみ：GPT-6 Lunaの画像入力と構造化出力で役割・関連を提案する。
2. Decisions優先：同モデルの複数質問でrole・候補をまとめて選び、自由な意味名や解決できない関連だけResponsesへ渡す。候補は座標とtextだけから作り、unknownを含める。候補に正解がない問題をモデル誤選択と分けて記録する。

Decisionsは複数質問と画像入力を扱える。choiceは質問ごとに2〜255候補。質問ごとの拒否を欠落と混同しない。最大質問数を未確認の値で決めつけない。入力上限に当たった場合は黙って切り捨てず、不完全として記録して分割設計を見直す。

**比較枠は実HTTP送信40回以内**。従前の「5帳票×2方式×3回=30」は論理試行数であり、ハイブリッド経路の実送信数ではなかった。これを以下へ修正する。

- まず全5帳票・2方式・2反復：20論理試行、最大30送信（Responses方式1送信、ハイブリッド方式最大2送信）。
- 残10送信は固定順T1→T2→H1の両方式の第3反復に最大9送信、残りを予備とする。失敗送信も消費し、自動再試行しない。
- この上限で完結できない分割が必要なら実験を停止し、結果と必要送信数を報告する。共有予算内でも無断で40回を越えない。
- 最初の送信前にinput/schema/candidate生成をオフライン検証する。H1/H2を見たprompt再調整はしない。調整を行う場合は別実験として記録する。
- 同一画像・detail・出力制約を固定する。Responsesの初期reasoningはlowとし、適用可能な設定を送信前に確認する。Decisionsに未対応の設定を渡さない。順序は反復ごとに二方式を交互にする。

計測は入力準備、各送信、応答検証、総所要時間、token/usage、実送信数、候補数、未解決率、役割・結合・行の成績を分離する。2〜3回という少数試行の中央値・最小最大と個々の値を示し、安定したp95や一般的な高速性は主張しない。手修正量・誤結合・拒否を速度と併記し、速さだけで方式を採用しない。採用前には未採点箇所を含む人間の確認と別帳票での検証が必要。

共有台帳は開始時 **182/496、残314**。今回の実API使用は **0回**。フェーズ2でも通常deployの共有台帳で送信前に予約し、コピー台帳や別SDK経由で迂回しない。OCR全体のモデル設定は変更しない。

公式仕様確認先：[GPT-6 Luna](https://developers.openai.com/api/docs/models/gpt-6-luna)、[Decisions guide](https://developers.openai.com/api/docs/guides/decisions)、[Decisions create](https://developers.openai.com/api/reference/resources/decisions/methods/create)。公開betaのため次工程の送信前にも必要パラメーターを照合する。

## 5. 検証と変更境界

deployの現行 [Safety Contract](ocr-safety-contract.md)、[Artifact Storage Policy](../ARTIFACT_STORAGE_POLICY.md)を優先する。旧 `scripts/verify_ocr_contract.py` はdeployでは廃止済み。旧root向けの検証計画をそのまま実行しない。

今回は文書と外部評価材料のみ。画像・入力・期待値のSHA、ID参照、許可した入力キー、評価数、文書参照、`git diff --check`の確認に成功した。開始終了時の照合では、rootの既存ファイル変更0、deployは本記録の追加とAGENTS・Artifact Storage Policyの追記だけ。両DB各37テーブルの行数・論理hashは一致し、API台帳も保持した。既存OCR contractを維持した。検証結果は `fixture-validation.json`、`annotation-validation.json`、`preservation.json` に保存。動作テスト・実音声・実AI性能評価は未実施。

検証コマンド（deployから）：`backend/.venv/Scripts/python.exe D:/llm-ocr-archive/template-ai/20261009-phase1/validate.py`、同Pythonで`survey.py finish`、`git diff --check`。検証スクリプトには任意項目tableColumnOrderの扱いと文書中のコマンド引数をパスとして読む不備があり、修正後に全5caseが成功した。製品不具合やfixture正解の変更ではない。

次に製品コードへ入る前に、保存実応答の再生、snapshot/ID/cropの対応、専用コピーのAPI保存と競合拒否をbaselineとして固定する。実装中は関係する短い境界検証、重い回帰は計画の最終検証工程へまとめる。通常DBの移行・有効化、既存OCR経路の変更を評価実験へ混在させない。

フェーズ2の開始手順：この文書と最新差分を確認 → manifestのSHAを検証 → 純粋な空間特徴と候補生成の漏洩防止を確認 → オフラインのpayload検証 → 上記40送信以内の比較 → 結果と実API残数を保存してモデル切替で停止する。
