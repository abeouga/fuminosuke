# v7製品外試作の実装境界

2026-10-11。[Phase24結果](../../template-ai-phase24-2026-10-11.md)。試作は実装済み、総合gateはT2のローカル時間未達で不合格。製品API/DB/UI/音声は未接続。Phase23設計と旧応答の意味を変更しない。

正本実装は `D:/llm-ocr-archive/template-ai/20261011-phase24/` の`factor_plan.py`、`evidence_context.py`、`selected_adapter.py`、`transport.py`。`factor-plan.v7.prototype1`と`factor-answer.v7`を新しい境界とし、既存v5 DTOへ選択後に明示変換する。生成器は旧候補列挙器をimportしない。

構造はform/records、固定行区間・header・position又は適格printed keyを発行する。列判断は`value/text`、`value/mark`、`row_label`、`context`を一つの条件付きfactorで選ぶ。formの独立したparent/readingも固定slotで発行する。自由名称はgenerated slot、固定名・保護名はliteral/preserve値として扱い、非generated名をモデル回答で置換しない。固定5範囲で選択されたgenerated slotはH2の表題で、名称はoracle供給に限る。

全componentは重複のないwrite ownershipを持つ。重なるgeometryを独立に適用せず保留する。未選択・未解決・unsupportedはcoverageへ残す。意味競合では物理所有と宣言元を依存closureへ残し、関係するcomponentを丸ごと保留する。`coverageSummary`はrequested/resolved/unknown/held refsを分ける。

1質問32択（保留2込み）、有効質問64、plan定義512質問/8192候補、2段/2送信を維持する。構造や全体上限を超えた場合は切り捨てず保留する。Responses schema自体のenum/文字数境界も送信前に検査し、超過は自動分割・方式変更・予約/送信を行わない。

factory発行の不変evidenceには内部所有bytes/JSONを使用する。現在状態callbackの所有/revision/scope/画像内容/catalog/手動facet/epochと、凍結runtime全Python/JSON/text契約・4試作実装・ライブラリ版のfingerprintを照合する。pack再cropを省くが、画像内容hash・現在性・共通semantic/scope/physical検証は省略しない。callbackは製品DBの正本へ未接続である。

wireはgpt-6-luna、Responses reasoning=low。自由名称がどの候補にも不要ならDecisions、一つでも必要ならfocused request全体をResponses一回へ固定する。二段Decisionsの下位質問は選択構造と第1段の生応答/manifest/選択hashに束縛する。閾値0.90/差0.15は未校正の試作値。正規化前に型・有限確率・全分布・別モデル・未発行IDを拒否する。

予約順序・中断・timeout・最大送信はmock runnerで確認した。実DB台帳、ネットワークclient、実endpoint受理は本工程の実装範囲に含まない。製品接続時の保存正本は引き続き`manual-authoring → MutationCore → TemplateAuthoringService`と「案を反映」→「確認して保存」。新しいAI専用の業務保存経路は追加していない。

合否・全3回の時間・source hash・mock request/response・旧30応答の再生・保持監査は外部artifactへ保存する。正解選択5/5はモデル精度ではなく、4/5のローカル時間達成はPhase25開始条件を満たさない。
