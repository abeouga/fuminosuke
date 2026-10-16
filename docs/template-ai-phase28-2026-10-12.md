# AIテンプレート作成 Phase28 修正実装・オフライン検証

2026-10-12。修正実装を製品外に作成しました。**受入条件は不合格です。T2のローカル中央値が625.34msで、設計の500ms上限を超えました。Phase30の実API比較へ進めません。** 実API・製品接続・通常データ移行は行っていません。

対象は `C:/Users/hanam/Desktop/llm-ocr-deploy`、試作は `D:/llm-ocr-archive/template-ai/20261012-phase28`。Phase27の[v8修正設計](contracts/template-analysis-v8/README.md)と[固定計画](contracts/template-analysis-v8/phase27-plan.json)に基づきます。[機械可読結果](contracts/template-analysis-v8/phase28-result.json)を併記します。要求された作業モデルはGPT-6.1 Sol / High、APIモデルはGPT-6 Lunaを維持。実際のチャットモデルはツールで確認していません。

## 変更内容と境界

| 新しい責務 | 試作ファイル | 実装した内容 |
| --- | --- | --- |
| 意味提示 | `semantic_view.py` | 元planから全構造・列用途・親・記号・名称と許可証拠を決定的に表示。内部path/所有digestの重複を送信本文から外し、run内aliasを別manifestへ固定 |
| 条件付き回答 | `branch_schema.py` | structureごとのanyOfに、その構造のfactor/choice/nameだけを発行。全property必須・追加property禁止。直積を生成せず、サイズ超過を保留 |
| 厳密展開 | `answer_adapter.py` | 元plan・証拠・画像・schema・prompt・実装をAnswerBindingへ固定。受信時bindingで元IDへ戻し、従来の選択検査・全claims・共通compile/manualへ渡す |
| 転送画像 | `image_transport_pack.py` | 詳細cropの元PNG bytesを保持。全体画像だけRGB/JPEG quality85・4:4:4で一度符号化し、小さい場合に採用。寸法/detail/許可refと原bytes・送信bytesのSHAを保持 |
| 送信・計時 | `run_transport.py` | 全保留0送信、独立固定判断Decisions一回、条件付き判断Responses low一回。予算予約後の一送信、retryなし、準備開始から60秒deadline、キャンセル/現在性再検査、共有HTTP接続最大2、永続化hookまでの時計 |
| 新規評価入力 | `dataset_factory.py` | 凍結済み入力・画像・scopeだけを読み、既存のverified factoryへ渡す。期待値は読まない |

既存の候補生成、`factor_plan.py`、`evidence_context.py`、`selected_adapter.py`、旧`transport.py`、400個のruntimeファイルは元の凍結実装とbytes一致です。旧rootで変更したのは`run_guard.py`だけで、新しい実装ファイルの照合と宣言済みimportを追加しました。source lockは399ファイルと依存版を固定しています。生成器・新adapter・送信処理はoracle/evaluationをimportしません。

通常製品のprovider、OCR crop、mapping、physical identity、補正・revision、保存・出力経路へは接続していません。AI専用の保存APIを増やさず、既存OCR contractを維持しました。台帳予約と証拠永続化は注入する境界です。今回のmock通過は、実DB transactionや実endpoint受理の確認ではありません。

## 検証結果

- 既存83件、repair48件、新v8 70件の境界検証を通過。別構造choice、欠落/追加factor・name、子回答付き保留、別run receipt、画像manifest/bytes改変、必須名称null、同値手動変更、owner/revision/epoch変更、実装reload、送信0の上限保留を確認しました。
- 既知5scopeは、評価専用の正解選択を与えた場合に完全manual投影5/5。5経路の一送信orchestrationと証拠書込までをmockで確認しました。これは実モデルの正解率ではありません。
- 元30応答は26受理/4拒否、Phase25の10応答は9受理/1同一拒否。旧分類89/146・結合54/74・行6/14・記号3/16、品質成立4/10・5秒以内0/10・重点誤結合4を再現しました。v8への推測変換や再採点で旧失敗を隠していません。
- crop完全一致、寸法維持、T2だけJPEG採用、codec失敗時の元PNG、元PNGの対照armを確認しました。T2の実v8要求は1,227,195bytesです。旧要求4,447,867bytesとの差には意味提示・schemaの変更も含まれ、画像だけの比較ではありません。
- HTTPX MockTransportで共有clientの対象混線防止、二枠上限、pool待ちdeadline、遅延応答キャンセル時の枠保持と終了後解放を確認。注入台帳の予約競合、予約失敗、予約後キャンセルの払戻し禁止を確認しました。実HTTPの接続再利用・送出時刻・provider時間は未測定です。
- 校正12scopeと評価12scopeを、異なる新規合成帳票familyとして作成。画像・scope・手動記述intent・期待DTO/完全manual投影のhashを固定しました。評価の一意10件はoracle投影一致、保留2件はoperations空。実モデルの未見帳票精度を証明するデータではありません。

校正/評価の期待値は、手動記述intentからDTOを作り、共通compiler/manualで投影しています。質問生成器は期待値を読みません。ただし期待投影と実処理が同じcompilerを使うため、これだけをcompilerの独立検証とは扱いません。

## ローカル時間と未達

別の新規Python processで3反復、各process内で5scopeを固定順に実行しました。アプリの持続cacheはありません。起動/importを除外し、入力準備・plan・転送pack・view/schema・wire構築・受信検査/共通compileを計時しています。評価用oracleの計算時間も含みます。ネットワーク・台帳commit・証拠永続化を含む実`scopeReadyMs`ではありません。

| scope | 中央値ms | 500ms条件 |
| --- | ---: | --- |
| T1 | 285.52 | 合格 |
| T2 | 625.34 | 不合格 |
| T3 | 220.88 | 合格 |
| H1 | 229.19 | 合格 |
| H2 | 204.71 | 合格 |

T2の区間中央値は入力準備約221ms、plan約21ms、新pack/view/schema約122ms、wire約8ms、評価専用oracle約20ms、受信検査/compile約234msです。区間中央値の和と全体中央値は同じ統計量ではありません。

通信bytesを削減できても、準備処理の追加費用が存在します。安全検査・候補・全claimsを省略したり、測定区間から新処理を外して合格にしたりしません。最初の測定からT2未達が継続し、最終source lockでの独立した3反復でも未達でした。途中の測定は別artifactへ保存しています。

## 再現と成果物

全コマンドはdeployのPythonを`-B`付きで使用し、試作ディレクトリで実行しました。製品の全件unit/E2Eは実行していません。今回の対象は製品外であり、旧runtimeのbytes一致、短い境界統合、保存応答比較を用いています。

| スクリプト | 結果 |
| --- | --- |
| `boundary_acceptance.py` / `repair_boundary_acceptance.py` | 83 / 48通過 |
| `v8_boundary_acceptance.py` | 70通過。最終追加の5経路＋元PNG対照も再実行 |
| `legacy_replay.py` / `phase25_replay.py` | 保存30 / 10応答の旧採点を再現 |
| `dataset_acceptance.py` | 合成24scopeの期待投影/保留を確認 |
| `measure_all.py` | 3反復。T2不合格、他4scope合格 |
| `final_checks.py` | 逐次回帰、旧runtime400ファイル・旧root・source lockを照合 |
| `audit_state.py after` | 両checkout/DB/業務データ/旧証拠/台帳を比較。詳細は`preservation.json` |
| `git diff --check` | 文書差分を検証。結果は`diff-check.log` |

試作先の`implementation-lock.json`、`verification.json`、`verification-supplement.json`、`v8-boundary-results.json`、`dataset-results.json`、`datasets/manifest.json`、`local-performance.json`、`verification-logs/`を証拠とします。最後に`result-hashes.json`へ成果物と文書のhashを固定します。

## 次工程

今回API0、累計376/496、残120。Phase29=Astra / XHigh・API0で実装と証拠を独立審査する段階です。T2の500ms未達を解消する修正設計・実装・再検証が必要であり、現状態でPhase30の最大42送信は開始しません。製品採用と通常データ移行も未承認のままです。
