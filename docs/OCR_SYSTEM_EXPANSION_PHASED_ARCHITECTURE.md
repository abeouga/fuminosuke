# 現行OCRシステムへ追加する要素と段階的なシステム構成

## 1. 結論

現在のシステムは、テンプレートに基づいて帳票を読み取り、利用者が結果を確認・修正し、DBとGoogle Sheetsへ保存する「テンプレート型Document AIワークベンチ」である。

ここへ追加しようとしているものは、単一のOCRモデルや単なる機械学習機能ではない。

追加対象は、以下をまとめた継続的品質改善基盤である。

- テンプレートの習熟状態を管理する仕組み
- OCR結果を業務利用前に止める品質ゲート
- 利用者の確認・修正を追跡可能な正解データとして蓄積する仕組み
- 誤認識リスクに応じて確認範囲を変える意思決定機構
- 安全な帳票だけを自動処理する仕組み
- 精度低下を検出して人手確認へ戻す監査機構
- OCR、画像補正、VLMを使い分ける推論経路制御
- 学習、評価、段階導入、ロールバックを管理するML運用基盤

システムエンジニアリング上は、全体を次のように表現できる。

> **Human-in-the-Loop型の、フィードバック駆動Document AI・継続的品質改善基盤**

英語では、次の表現が近い。

> **Feedback-driven Human-in-the-Loop Document AI Platform with Risk-based Workflow Orchestration and Closed-loop MLOps**

ただし、MLを本番適用する前の段階は「継続学習システム」ではない。正確には、継続学習に必要な正解データ、監査証跡、評価基盤を構築する段階である。

---

## 2. 現在のシステム

現在実装されている主要範囲は次のとおりである。

| 現在の機能 | システムエンジニアリング上の分類 |
|---|---|
| 帳票テンプレートの作成とバージョン管理 | Template Management / Configuration Management |
| OpenCVによる位置合わせ、矩形検出、画像差分処理 | Image Preprocessing Pipeline |
| 意味づけ済み矩形だけをOCRへ渡す処理 | Region-based OCR Pipeline |
| OpenAIによる記入セルの読み取り | External AI Inference Integration |
| `mappingId`等によるOCR結果と項目の対応 | Deterministic Mapping / Referential Integrity |
| Reactによる結果確認と詳細修正 | Human Review Interface |
| revisionを使った更新競合防止 | Optimistic Concurrency Control |
| SQLiteへの結果、履歴、画像情報の保存 | Local Persistence / System of Record |
| Google Sheetsへの出力と通常修正 | Business Application Integration |
| Sheets修正のDBへの取り込み | Bidirectional Data Synchronization |
| 同期失敗時の再送 | Retryable Integration |
| 確定済みデータの検索 | Structured Data Retrieval |

現在のシステムには、人が結果を直す機能はある。しかし、その確認・修正をテンプレートの成熟度評価、リスク判定、推論経路の改善へ体系的に戻す閉ループはまだ完成していない。

---

## 3. 追加しようとしているシステム能力

| 追加する要素 | 役割 | SE用語としてのパッケージング |
|---|---|---|
| `CALIBRATING`、`ASSISTED`、`AUTOMATIC`、`DRIFTED` | テンプレートの習熟度に応じて処理を変える | Lifecycle Management / Finite State Machine |
| OCR完了直後のSheets出力を止める | 未確認データの業務利用を防ぐ | Quality Gate / Release Gate |
| 確認完了後にSheetsへ出力する | 業務処理の順序と条件を管理する | Workflow Orchestration |
| セル・セクション・帳票単位の確認記録 | 人による確認の強さを区別する | Human-in-the-Loop Validation |
| 修正前後を追記保存する | 判断履歴と教師データを失わない | Append-only Event Log / Event Sourcing |
| 未変更だが明示確認された値を記録する | 正解事例を収集する | Ground-truth Data Collection |
| 修正種別を分類する | エラー原因を分析可能にする | Error Taxonomy / Data Labeling |
| 矩形単位の修正率や誤空欄率を集計する | 問題箇所を定量評価する | Quality Analytics / Observability |
| ルールで高・中・低リスクを判定する | 確認対象を絞る | Rules Engine / Risk Scoring |
| 要確認セルだけを表示する | 人手確認の負荷を下げる | Exception-based Processing |
| 低リスク帳票を自動出力する | 人手を介さず処理する | Straight-through Processing |
| 低リスク帳票の一部を無作為確認する | 見逃しを観測する | Statistical Quality Control / Control Sampling |
| 精度低下時に全体確認へ戻す | 運用品質の劣化を止める | Drift Detection / Automated Fallback |
| 修正イベントから学習用データを作る | ML評価可能な形式へ変換する | Training Data Pipeline / Feature Engineering |
| MLを本番へ影響させず予測だけ行う | 安全に性能を検証する | Shadow Deployment |
| ルール方式とML方式を比較する | 改善効果を定量検証する | A/B Testing / Champion-Challenger Evaluation |
| OCR、画像補正、超解像、VLMを選択する | コストと精度を最適化する | Model Routing / Cascaded Inference |
| モデル、重み、ライセンス、版を管理する | 商用利用と再現性を確保する | Model Governance / Software Bill of Materials |

---

## 4. フェーズ別実装計画

# Phase 0：現状基準と統制条件の確定

## 目的

現在の処理、品質、費用、変更範囲を固定し、以後の改善効果を比較可能にする。

## 追加するもの

- 現行OCRフローの基準記録
- 実帳票に対する評価指標
- 既存テンプレートとSheets運用の回帰基準
- 個人情報、外部送信、保存期間に関する運用条件
- 利用モデルと依存物のライセンス台帳
- モデル名、バージョン、重み、配布元、ハッシュの管理方針
- OpenAI出力をML学習へ利用するかどうかの契約上の境界

## SE用語

- As-Is Analysis
- Baseline Management
- Non-functional Requirements
- Model Governance
- Compliance Control

## 完了状態

現行方式と将来方式を、精度、修正時間、処理時間、API費用、見逃し率で比較できる。

---

# Phase 1：確認履歴・修正履歴のデータ基盤

## 目的

利用者の確認と修正を、後から分析できる信頼可能なイベントとして保存する。

## 追加するもの

- テンプレートバージョン単位のライフサイクル状態
- セル、セクション、帳票単位の確認イベント
- OCR値、確定値、修正種別、確認方法の履歴
- 矩形、JSONパス、crop、ジョブ、revisionの追跡関係
- 状態変更履歴
- 重複登録防止と競合検出

## SE用語

- Event Sourcing
- Audit Trail
- Data Lineage
- Lifecycle State Machine
- Optimistic Concurrency Control
- Idempotency

## 完了状態

任意の確定値について、元のOCR値、対象画像、修正内容、確認者、確認方法、テンプレート版を追跡できる。

---

# Phase 2：新規テンプレートのキャリブレーション

## 目的

新しいテンプレートの最初の数帳票から、信頼できる正解事例を収集する。

## 追加するもの

- 新規テンプレートを`CALIBRATING`として開始する運用
- OCR結果をDBへ下書き保存する処理区分
- 確認前のSheets同期を停止する品質ゲート
- OCR値と矩形画像を対応表示する全体確認画面
- セルまたはセクション単位の確認
- 未確認セクションが残る場合の確定禁止
- 確認完了とSheets同期状態の分離
- Sheets同期失敗後の再試行

## SE用語

- Human-in-the-Loop Workflow
- Calibration Workflow
- Quality Gate
- Workflow Orchestration
- Eventual Consistency
- Retryable Integration

## 完了状態

未確認の新規テンプレート帳票が業務用Sheetsへ流れず、確認結果を失わずに同期を再試行できる。

---

# Phase 3：品質集計とテンプレート成熟度管理

## 目的

テンプレートが全面確認を卒業できるか、感覚ではなく実績で判断する。

## 追加するもの

- テンプレート単位の確認済み帳票数
- 矩形単位の観測数、修正率、誤空欄率
- 構造修正、意味づけ修正、矩形修正の集計
- OCR経路別の正解率と確認時間
- `ASSISTED`へ移行可能かを示す判定
- 管理者による手動昇格・降格

## SE用語

- Quality Metrics
- Operational Analytics
- Data Quality Monitoring
- Human Approval Workflow
- Configuration Governance

## 完了状態

テンプレートの状態変更について、判断根拠となる数値と履歴を説明できる。

---

# Phase 4：ルールベースの要確認判定

## 目的

全面確認から、問題が起きやすい箇所だけを確認する方式へ移行する。

## 追加するもの

- OCRステータス、画像品質、形式不正、過去修正率等によるリスク判定
- 高・中・低リスクの区分
- 判定理由コード
- 高リスク帳票の全体またはセクション確認
- 中リスクセルだけの選択確認
- 低リスク項目の確認省略

## SE用語

- Rules Engine
- Risk-based Decisioning
- Exception-based Processing
- Explainable Decision Logic
- Selective Human Review

## 完了状態

MLを使用せず、なぜ確認対象になったかを説明できる状態で確認範囲を削減できる。

---

# Phase 5：Sheets修正のフィードバック統合

## 目的

日常業務でSheets上に発生した修正を、同じ品質改善データへ統合する。

## 追加するもの

- Sheets修正とジョブ、矩形、JSONパスの安定した対応
- Sheets修正イベントの追記保存
- revision競合の検出
- 元OCR値、確定値、cropへの追跡
- React修正とSheets修正の統合集計

## SE用語

- Bidirectional Synchronization
- Change Data Capture
- Integration Event
- Eventual Consistency
- Conflict Detection

## 完了状態

Sheetsの行番号や表示順に依存せず、どのOCR結果がどのように修正されたか追跡できる。

---

# Phase 6：低リスク帳票の自動処理と監査

## 目的

十分に安定したテンプレートについて、人手確認なしで処理できる範囲を限定的に増やす。

## 追加するもの

- `AUTOMATIC`状態
- 低リスク帳票の下書き自動出力
- 中・高リスク帳票の確認継続
- 低リスク帳票からのランダム監査抽出
- 修正率、誤空欄率、レイアウト変化の監視
- 精度低下時の`DRIFTED`移行
- 全体確認への自動フォールバック

## SE用語

- Straight-through Processing
- Statistical Quality Control
- Control Sampling
- Drift Detection
- Circuit Breaker / Automated Fallback

## 完了状態

自動化率を上げても、見逃しを測定し、異常時には自動処理を停止できる。

---

# Phase 7：ML学習基盤とShadow Mode

## 目的

利用者が修正する可能性を予測するモデルを、本番処理へ影響させずに評価する。

## 追加するもの

- 明示確認済みデータからの学習データ生成
- 画像品質、OCR confidence、フィールド属性、過去実績の特徴量化
- 学習、検証、テストデータの分離
- モデルと特徴量定義のバージョン管理
- 本番裏側でのML予測
- 予測確率と実際の修正結果の比較
- ルール方式との性能比較

## SE用語

- MLOps
- Training Data Pipeline
- Feature Engineering
- Model Registry
- Shadow Deployment
- Offline Evaluation

## 完了状態

MLを本番判断に使わず、どの条件でルール方式より優れるかを実測で説明できる。

## 契約上の境界

OpenAIの出力を学習データとして利用する設計は、契約確認が完了するまで有効化しない。初期の学習対象は、画像特徴量、ローカルOCR結果、利用者が明示確認した正解データを中心とする。

---

# Phase 8：A/BテストとMLによるリスク判定

## 目的

ML方式が既存のルール方式より安全かつ効率的であることを確認し、限定的に本番適用する。

## 追加するもの

- ルール方式とML方式の比較運用
- テンプレート単位または組織単位の段階導入
- 利用者修正率、見逃し率、費用、処理時間の比較
- 重大項目に対する別閾値
- ML停止とルール方式への即時復帰

## SE用語

- A/B Testing
- Champion-Challenger Model
- Progressive Delivery
- Feature Flag
- Model Risk Management
- Rollback Strategy

## 完了状態

ML方式が重大な見逃しを増やさず、修正時間または処理費用を改善すると確認できる。

---

# Phase 9：OCR経路の自動最適化

## 目的

帳票と矩形の状態に応じて、最適なOCR処理経路を選択する。

## 追加するもの

- PaddleOCRで完結する低コスト経路
- OpenCV補正後に再OCRする救済経路
- 条件付き超解像経路
- 軽量VLM経路
- 高性能VLMへの最終フェイルオーバー
- 経路別の精度、時間、費用の評価
- モデル変更時の再キャリブレーション

## SE用語

- Model Routing
- Cascaded Inference
- Adaptive Inference
- Policy-based Orchestration
- Cost-aware Decisioning
- Multi-model Serving

## 完了状態

すべての帳票を高性能モデルへ送らず、必要な箇所だけ高コスト経路へ送ることで、精度を維持しながら費用と処理時間を削減できる。

---

## 5. フェーズ全体の意味

フェーズごとの位置付けは次のようになる。

| フェーズ | システムとして獲得する能力 |
|---|---|
| Phase 0 | 改善前後を比較できる |
| Phase 1 | 確認と修正を追跡できる |
| Phase 2 | 未確認データを業務利用前に止められる |
| Phase 3 | テンプレートの成熟度を数値で判断できる |
| Phase 4 | 確認対象をルールで絞り込める |
| Phase 5 | 日常業務の修正を改善データへ戻せる |
| Phase 6 | 安全な範囲だけ自動化できる |
| Phase 7 | MLを本番へ影響させず評価できる |
| Phase 8 | MLを限定的かつ可逆的に本番導入できる |
| Phase 9 | OCR経路を精度、時間、費用に応じて最適化できる |

---

## 6. この取り組みを一言で表す場合

### 技術設計書向け

> テンプレート型OCRへ、Human-in-the-Loop確認、追記型監査イベント、リスクベース処理、ドリフト監視、段階的モデルルーティングを追加する、フィードバック駆動型Document AI基盤。

### システムエンジニア向け

> OCR業務フローに、ライフサイクル管理、品質ゲート、イベント履歴、例外処理型レビュー、段階的自動化、MLOpsを統合するシステム拡張。

### 事業説明向け

> 利用者が確認・修正するほど、確認対象と高コストAIの利用を減らせる帳票処理SaaS。

### 就職活動・成果説明向け

> 利用者の修正履歴を品質改善データとして蓄積し、ルール判定、監査、Shadow Mode、A/Bテストを経て、OCR精度、確認工数、API費用を継続改善するDocument AIシステムを設計した。

---

## 7. 誤解を避けるための用語上の注意

- Phase 1からPhase 6までは「教師データ収集・品質制御基盤」であり、MLシステム本体ではない。
- 自動再学習を実装していない段階で「自己学習AI」と表現しない。
- 利用者の修正を無条件に正解とせず、明示確認、revision、確認方法を含めて信頼度を管理する。
- リスクの高いデータを人へ回す仕組みはHuman-in-the-Loopである。
- 不確実なデータを選択的に人へ回し、その結果を将来の学習へ使う段階はActive Learningに近いが、単なる修正履歴保存だけをActive Learningとは呼ばない。
- 複数OCRを選択する仕組みはMixture of Expertsではなく、Model RoutingまたはCascaded Inferenceと表現する。
- 継続改善は行うが、検証なしに本番モデルを自動更新するOnline Learningとは区別する。

