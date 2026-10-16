# AIテンプレート作成 フェーズ24：条件付きfactor planの製品外試作

**判定は不合格。意味の成立性5/5と境界83項目は通過したが、T2のローカル処理中央値716.81msが、事前条件の500ms以下を満たしていない。有料のフェーズ25は開始しない。**

2026-10-11。対象は `C:\Users\hanam\Desktop\llm-ocr-deploy`、実装・検証成果物は `D:\llm-ocr-archive\template-ai\20261011-phase24`。開発工程の指定はGPT-6.1 Sol / High。APIモデルの指定は両経路とも`gpt-6-luna`、Responsesは`low`。この工程の実API使用は**0回、366/496・残130回**。モデルによる新しい推論、通常画面・DB保存・音声試験は行っていない。

## 実装した範囲

[Phase23の設計](template-ai-phase23-review-2026-10-11.md)、[v7契約](contracts/template-analysis-v7/README.md)、実装前から保存されていた[Phase24計画](contracts/template-analysis-v7/phase24-plan.json)を基準とした。計画JSONは当時の`design_only_not_executed`を保持し、現在の実行結果は[別の結果JSON](contracts/template-analysis-v7/phase24-result.json)へ記録した。

外部試作を次の4責務に分けた。製品ソースへの転記はしていない。

| 外部ファイル | 責務 |
| --- | --- |
| `factor_plan.py` | geometry・許可済み印字から構造と条件付きfactorを直接生成。gold、帳票ID、旧応答、ファイル・ネットワーク、全直積列挙へ依存しない |
| `evidence_context.py` | factoryで検証した画像bytes・pack・catalogを不変に保持。現在の所有/revision/scope/catalog/同値手動facet/epochと依存実装の内容hashで失効を検出 |
| `selected_adapter.py` | 発行済みID・構造別factor/name集合を厳格に検査。全claimsと宣言元の依存を保持し、選択案だけをv5 DTOへ変換して共通resolver/compiler/manualへ渡す |
| `transport.py` | Decisions/Responsesのwire作成とmock runner。予約→送信、中断・timeout・低信頼・段間binding・最大2送信・再送禁止を検査 |

H2は52完成案を先に作らず、構造5候補＋保留2の7択、列判断最大4候補＋保留2の6択になった。選択前の全候補compileはない。実関数の呼出しを計測し、各範囲の選択後に共通resolver・compiler・manual投影が各1回、`build_pack/check_pack`が0回であることを確認した。packはrunのfactoryで生成する。旧`check_pack`やcompilerの関数・検証を改変／monkeypatchしていない。

catalogは内部JSONとして固定し、利用時には別オブジェクトを返す。応答待ちの間に同値手動facetだけが変わっても失効する。現在状態を供給する境界は外部試作のcallbackであり、製品DB transactionへの接続は未実装。ここを実DBの競合対策まで受入済みとは扱わない。

## 固定5範囲の結果

選択と必要な名称は**評価側が正解参照から供給したoracle**である。新モデルの認識・選択・命名精度を測定した結果ではない。Phase19と同じP-low画像、crop、write refs、provider refsを照合し、contextを追加していない。

| 範囲 | 事前固定した経路 | 完全manual意味投影 | ローカル中央値 |
| --- | --- | --- | ---: |
| T1 | Responses 1回 | 一致 | 452.83ms |
| T2 | Responses 1回 | 一致 | **716.81ms・未達** |
| T3 | Decisions 1回 | 一致 | 332.90ms |
| H1 | Responses 1回 | 一致 | 365.36ms |
| H2 | Responses 1回 | 一致 | 331.28ms |

T1/T2/H1/H2は未選択の構造にもgenerated name slotがあるため、設計どおりResponsesを選んだ。T3だけが有限候補で完結する。実際にAPIへ送っていないため、これらは予定送信数である。Decisions優先でも、固定5範囲の4/5でResponsesが必要になる点は判断材料として残す。

重点はroles **73/73**、bindings **37/37**、rows **7/7**、mark欄 **8/8**。重点以外も含む完全投影では、名前・alias・親・意味の共有・mark・行・実際のrelationが5/5で一致した。既存graphをmanual compilerへ再入力した際の全ID/path込みno-op、physical identityも5/5で一致した。既存graphに対するAI再解析・保存・undoまでを確認した意味ではない。

## 時間の測定と未達

各範囲を3回、毎回ファイルから入力を読み、snapshot・pack・planを新規構築して選択後の検査/compile/manual投影まで計時した。参照読込・oracle選択・採点・追加のno-op検査は計時外。APIは0回で、wireの組立・実通信・モデル時間・UI表示・保存も含まない。Python import済みの同一process内で、アプリケーションのpack/plan/compile cacheを再利用していない。OSのディスクcacheやprocess起動時間まで除去した測定ではない。

T2の3回は **786.58 / 716.81 / 714.74ms**。中央値に当たるrunの内訳はpack準備329.14ms、plan生成56.32ms、選択検査・compile331.34msだった。別の診断profileではsnapshotのgeometry再検証、依存実装hashの照合、catalog複製・schema検査が残っている。profileの時間と最終無計装測定を直接足し合わせない。

初期のT1にはセルごとのcatalog全複製があり、今回の範囲内で一回のrole索引に修正した。保留した案の宣言元が依存検査から外れる欠落も、境界例に基づいて修正した。初期診断は外部の`initial-acceptance-results.json`、`after-copy-fix-results.json`、profileログへ残した。最終測定の4実装ファイルhashは`acceptance-results.json`に固定している。

全5範囲の中央値500ms以下という条件を4/5へ緩和しない。旧Phase22の4/5不合格も変更しない。モデル時間がない以上、音声編集が速くなった、5秒以内のAI判断が成立した、とも判断しない。

## 境界と旧証拠

83項目で、未発行／別構造factor、欠落・重複、未解決名称、画像・所有・revision・同値facetの変更、scope外書込、親競合・値所有重複・mark欠落・空/重複key、保留範囲への侵入と独立部分の保持を確認した。列順入替、context列追加、同名見出し、複数凡例、競合親、上限超過は変形例で検査した。未見実帳票の精度ではない。

5範囲のmock wireに加え、自由名称を持たない合成planでDecisions二段のreceipt/bindingを確認した。第1段unknownでは下位質問を発行せず、古いreceipt・段間変更・中断・予算不足・timeoutで追加送信を停止する。mock予約は実台帳の使用数に加算しない。画像はinline data URLの復号bytesまで照合した。**実endpointの受理は未確認**。

固定候補と生成JSONを分け、依存するDecisions質問を別requestにする方針は[公式Decisions文書](https://developers.openai.com/api/docs/guides/decisions)に照合した。Responsesは[公式Structured Outputs文書](https://developers.openai.com/api/docs/guides/structured-outputs)に従い、全項目required・追加property禁止とし、条件付き集合はserverで再検査する。schemaのenum数等が送信条件を超える場合は予約前に拒否する。これもmock検証の範囲である。

旧Phase20の30保存応答を変更のないPhase19 runtimeへ再生し、**26受理・4拒否**、旧採点・全計測・usage・manual意味投影・既存graph no-opを再現した。旧runner・応答・fixture・採点は書き換えていない。

## 検証と保持

実行した主な検証は外部の`offline_acceptance.py`、`boundary_acceptance.py`、`legacy_replay.py`、開始/終了`audit_state.py`、`artifact_verify.py`と、対象repoの`git diff --check`。Pythonはdeployのbackend仮想環境を使用し、`-B`で実行した。製品ソース/UIの変更がないため、製品unit全件、frontend build、E2E全件、サーバー再起動、実マイクは実行していない。

repo変更はこの報告、v7試作説明/結果、AGENTS/Safety Contractの状況追記に限定する。両repoのHEAD・通常DB全37表/schema・設定・業務863ファイル/781 artifact・旧checkout・Phase11〜23証拠を開始時と比較した。**既存OCR contractを維持した**。根拠は製品コード・OCR/provider/prompt/model/schema・mapping/association/crop・受付snapshot・補正revision・確定/出力を変更していないことと、終了時の保持監査である。製品全体の現在動作を新しく実機受入した主張ではない。

今回のPhase24はここで終了する。フェーズ25の最大20送信比較は開始条件未成立。次はAstra / XHigh・API0で、T2に残るローカル費用と不変証拠の再利用境界を審査する工程を提案する。新たな再設計・目標変更・実API比較は自動開始しない。
