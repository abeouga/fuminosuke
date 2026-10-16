# 修正6-B：白紙テンプレート向け指示と可逆metadata圧縮

2026-10-14。GPT-6.1 Sol / High、turn `01a138ee-fee8-75f1-9eda-3a2500ef294a`。対象は `C:\Users\hanam\Desktop\llm-ocr-deploy`。[修正設計](template-ai-region-request-repair-design-2026-10-14.md)の初回実装です。今回の実APIは0回、共有台帳437/924・残487回です。Lunaの実精度・実画面5秒と改善ゴールは未完了です。

## 変更した契約と範囲

G1-01では所有11セルの10入力が全件保留されました。画像に入力欄・見出し・凡例があること、正解対照は現compilerで表現できることを確認済みです。モデル内部の保留理由は未確定です。今回の変更はその仮説を実測できる形にするもので、保留の自動成功化はしません。

製品の変更は次の3ファイルです。

- `backend/app/template_analysis/region_pack.py`：白紙のテンプレート作成、readOnlyの印字参照、文字記入と記号設定を指示に明記。セル・接続候補を列定義と値の配列へ圧縮。
- `backend/app/template_analysis/visual_provider.py`：visual-runner.v8 / region-pack.v2を発行。pack作成・要求組立の時間、metadata bytes、画像数を記録。
- `backend/app/template_analysis/visual_runner.py`：slot待ち、現snapshot確認、evidence準備の時間を既存の要求metricsへ保存。予約・決済・公開・保存の順序は変更していません。

cellColumnsの14項目で、ref・元座標・頂点・編集可否・role/lock・親・既存意味・保護facet・textHint/信頼区分を保持します。allowedLabelColumnsはinput/label/axisです。null/defaultの削除や座標の量子化はしていません。未知のセル項目を黙って落とさず拒否します。

snapshotPin・planDigest・unitId・sourceImageSha256はモデル向けの重複記載から除きます。完全な値はサーバーのVisualEvidenceに保持し、catalogDigestも完全なmetadataを含む候補集合へ束縛されています。送信前の既存snapshot・所有・内容SHA確認、応答のcatalogDigest検証、compilerの物理/意味/保護検証を維持しています。

モデルはGPT-6 Luna、領域reasoning=none、回答RegionAnswer schema、出力上限8192、画像内容・順序・detailを維持しました。geometry/table/複合欄の表現力は削減していません。候補の間引き・単純な最近傍の正解化、保留禁止、fallbackは追加していません。キャッシュやUI変更は今回導入していません。

旧v7/v1の解析案は既存runtime contract検査で失効します。過去の業務データ・受付snapshot・保存済みmappingを移行・再保存する変更ではありません。非互換の範囲は未反映/未保存の旧解析案であり、再解析が必要です。

## 検証結果

証拠は `D:\llm-ocr-archive\template-ai\20261014-region-request-fix` へ保存しました。

| 検証 | 結果 |
| --- | --- |
| G1-01 metadataの独立逆変換 | 54セル、61候補、保護・頂点・既存依存が一致 |
| 同metadata文字数 | 16,375→7,019、約57.1%減 |
| 開発5ページ・24領域 | 旧24 reference要求hashを再現し、新旧metadataの情報・画像・schemaを照合 |
| 24領域metadata文字数合計 | 506,616→257,193、約49.2%減。モデルtokensの実測ではない |
| G1-01の実保存応答 | 案0・未解決11を再現。10機能の保留を成功へ変換しない |
| 同snapshotの正解対照 | 反映可能な1領域・10入力。制御fixtureでありLuna精度ではない |
| 専用ASGI integration | 14条件通過、4件の専用予約。実API0 |
| 共通保存・別接続再読込 | revision1→2、54セルID/座標、22入力、22mapping/保存先を保持 |
| 製品cropper | 保存した22mappingのcropが元の白紙画像の対応sliceと完全一致 |
| 旧contract | SNAPSHOT_STALEで拒否 |
| 対象3ファイルruff / mypy | 通過 |
| 差分・データ・封印保持 | `preservation.json` と `completion-1/hashes.json` に保存 |

slot待ち・snapshot確認・evidence準備・model pack・要求組立の8項目を4要求すべてで保存できることを確認しました。これらを実画面のクリック→正しい領域の操作可能時刻の代用にはしません。

ASGIの14条件は、途中反映、digest改変拒否、同値のmanual touch、手動geometryの保護、receipt再送/分岐拒否、tip取り消し、計画順、累積反映、明示保存と別接続取得を含みます。最初の専用実行は検証側がモデルmetadataから除いたunitIdをログ採取に使って失敗しました。検証側の識別をcatalogDigestへ修正し、新しい専用DBで再実行しています。失敗DB/ログと専用予約4件も保持し、成功4件と合わせて専用予約は8件です。製品のguardを緩めて通していません。

frontendコード・生成HTTP契約・回答schemaは今回変更していません。以前の161テスト/buildを今回再実行した結果とは扱わず、同一source hashを照合しました。実画面・全backend/E2Eは今回再実行していません。手書き入力の位置合わせ・認識、実モデルの保存、R04全機能の実精度も未検証です。

通常OCRのidentity・mapping・crop・revision・出力契約を維持しました。ただしAIテンプレート解析のモデル入力prompt/表現は意図的に変更しています。精度・速度の改善をoffline検証だけで保証しません。

## 新しい比較の準備と次工程

`experiment-allocation-proposal.json` は6-C審査前の提案です。新実験 `region-v3-20261014` は診断4＋先行4＋全ページ24＋画面反復60＝最大割当92送信、上限96、未割当4です。旧v2の未送信分を再開・転用しません。trial IDsはすべて新しいV3接頭辞です。

診断A/B/C/Dは、旧/明示指示と旧/圧縮metadataの2×2比較を各1回。4条件のreference本文hash、同一画像・schemaを固定しました。referenceが使う過去fixtureのopaque bindingは実試行に再利用しません。6-C合格後、各実trialの新resource/session・snapshot・plan・region・要求本文を送信前に別途固定します。

重大誤対応は未送信分を停止します。単純保留・遅さも不合格として記録し、診断だけで改善成功としません。製品条件Dが全領域品質とAPI下限5秒を通らなければG1以降へ進みません。D以外の観測結果を見て無断で製品へ昇格させません。G1以降は従前の品質停止と通常GUIの5秒・保存受入を維持します。

次工程は **6-C=Astra / XHigh** です。モデル切替後、可逆性・binding/保護・実画像根拠・新配分・停止条件を独立審査します。6-Dの追加実APIはまだ開始していません。新規別エージェントは起動していません。最終未使用4帳票は未閲覧のまま維持しています。
