# Template analysis selection v6 — Phase22の成立性試作

2026-10-11。版は `template-analysis.selection.v6.prototype1`。**製品外の契約・試作であり、成立性は4/5scopeで不合格。製品APIへの接続・有効化・有料比較には使用しない。** [Phase22記録](../../template-ai-phase22-2026-10-11.md)と[Phase21審査](../../template-ai-phase21-review-2026-10-11.md)を参照する。

## 1. 対象と責任

既存の物理セル・行列候補へ意味を付ける。新しい矩形、crop、mapping、OCR provider、過去job、音声取得は扱わない。候補生成はファイル・DB・ネットワーク・goldへアクセスしない純粋な処理とする。

サーバーが完全な操作計画を保持し、モデルは発行された候補IDか保留候補だけを選ぶ。モデルに全layout、自由なpath・座標・操作JSONを書かせない。既存のv5 DTOへ明示変換し、Phase19の共通normalizer/compiler/manual経路を使う。今回の試作はその凍結runtimeを読み取りで参照し、製品側の保存処理を呼ばない。

## 2. 候補と依存単位

| 要素 | 契約 |
| --- | --- |
| component | `name`、所有するwrite refs、由来、候補、状態、保留理由を持つ。write refsは重複なく全scopeを覆う |
| candidate | 安定した内容hash ID、モデルへ見せる意味の差分、サーバー専用の完全なv5 proposal、受理roleと未解決refsを持つ |
| 依存 | 親・列用途・行識別・記号設定を含む表は一つの質問。独立した単体欄は別質問。所有集合が交差する部品はまとめる |
| 上限 | 依存単位ごとに `none_of_these` / `needs_context` 込み32候補。API仕様の上限ではない |
| 超過 | `deferred / CANDIDATE_BOUND_EXCEEDED`。モデルへ具体案を発行せず、保留2候補と全対象refsを保持する。先頭30件を採る処理はない |
| 対応不能 | `NO_SUPPORTED_HYPOTHESIS` または共通検査後の `NO_VALID_HYPOTHESIS`。対象をcoverageから除外しない |

試作の候補生成規則は以下。配置は意味の候補を拾う根拠に限定し、採用の根拠にはしない。

- 完全な行列候補の最初の全印字行をheader仮説とし、後続行を内容に関係なくすべて保持する。列用途のvalue / row_label / context / 成立するprinted keyを列挙する。
- 2列のlabel/value形状ではformとrecordsを競合候補に残す。左側で複数行にまたがる印字セルについて、親へ接続する案と接続しない案を用意する。
- 行列候補に属さない欄は、曖昧でない横隣接の印字label/value対を拾う。独立した印字セルにはheading / ignoredの仮説を用意し、未対応空欄は保留する。
- チェックボックスの選択肢、区切り付き記号一覧を、そのまま印字根拠として読み取り候補へ使う。text読取の競合案も残す。根拠外の選択肢は生成しない。
- 名前は印字を保持する。表題は書込み可能な全幅見出し、なければ列見出しの連結を使う。業種辞書や帳票名、gold由来の別名、接尾語を削る特例は持たない。

列挙作業自体にも4096組の上限があり、超過した単位は保留する。これは計算量の打切りを成功に見せないための保護であり、32候補を達成したという意味ではない。現在のH2はこの作業上限ではなく、具体案52＋保留2が32を超えるため不合格になった。

## 3. 束縛と検証

`FrozenChoices`は候補集合とbindingをJSON文字列で固定し、集合全体のSHAを所有側で保持する。bindingはsnapshot・scope・evidence pack・catalog・手動role/facet・generator/runtime source・選択schema・上限/閾値を含む。

候補を発行する前に、printed keyの空／重複、v5 schema、全claims・親・role・値所有・mark依存・scope・手動facet、共通compileを検査する。問題単位を落として残りだけを候補へ変えるのではなく、その候補全体を拒否して理由を残す。候補間は排他的な仮説であり、採用前から全候補のroleを合成しない。

選択時にも現在snapshot、owner/page/revision、scope/pack、catalog、手動facet、集合SHAを再照合する。古い候補、未発行ID、回答漏れ・重複、別モデル、不正な確率、自由な追加操作を拒否する。候補順を変える場合は新しい集合SHAを発行し、意味IDは保持する。

応答はraw provider JSONをそのまま業務処理へ渡さず、[選択projection schema](selection-projection.schema.json)の `{model, answers}` へ射影して扱う。`choice`は発行集合に限定し、確率分布は発行候補を過不足なく覆い、合計1・有限値・選択が最大であることを要求する。refusalは未解決にする。余分なoperationを渡しても実行しない。

試作閾値はconfidence 0.90以上、次点との差0.15以上。**未校正の試作設定であり、実モデルの正答率や自動採用の安全性を表す値ではない。** 実APIの生応答からprojectionへのadapter、確率表現の実endpoint整合、閾値の校正は未実装・未検証である。

## 4. 選択後の状態

| 状態 | 意味 |
| --- | --- |
| held | 全単位が保留。元layoutを全ID/path込みで保持し、operationsは空 |
| partial_draft | 独立した一部単位だけ解決。未解決単位のrefsと理由をcoverageに残す |
| draft | 発行質問すべてに具体案が選ばれ、合成案が共通検査を通過した。意味正解や製品保存の証明ではない |

選択した単位を合成してから、もう一度全claimsの競合検査と共通compileを実行する。競合時の先勝ち、unknownをignoredへ変更して成功に見せる処理はない。部分案の未選択セルもcanonicalではunknownのまま。保存DTOの中立ignored初期値と区別する。

案の表示後は「案を反映」→「確認して保存」の既存境界を維持する。将来の製品統合でも、手動と同じMutationCore / TemplateAuthoringServiceへ接続し、選択providerに保存権限を与えない。音声指示による既存の直接補正フローはこの契約では変更しない。

## 5. 実装と証拠の所在

製品外 `D:\llm-ocr-archive\template-ai\20261011-phase22` の `hypotheses.py`、`choice_runtime.py`、`selection-projection.schema.json`、`acceptance.py` が試作。repositoryのschemaは実行した外部schemaと同一bytesで保存する。

`public_request`は画像hash・印字根拠・質問/選択肢を含む**オフラインの要求仕様**であり、実Decisionsへ送信できるwire bodyを確認したものではない。API0回。正解参照で選ぶoracleは候補coverageの検査にだけ使い、モデル精度として数えない。

Phase22はT1/T2/T3/H1の共通/manual意味投影を再現したがH2は保留。5/5の成立性条件は満たさない。新たな有料比較・製品接続に進む契約ではなく、次の設計審査の入力として保持する。
