# WSL PDF 描画線による座標検証の準備

2026-09-13。`renderer_pdf_strokes.py` の bounded operator inventory と、
`renderer_vector_pdf.py` / `renderer_vector_worker.py` / `renderer_vector_bridge.py` の
別固定 helper 呼出しを実装した。unit / 合成 PDF に加え、実 WSL と実4ページの線取得まで確認した。
既存の raster verifier、5 anchor 条件、2 px 閾値、qualification / ACTIVE / OCR 受付 gate は変更しない。
既存 glyph helper/API の呼出しを変更せず、qualification API に vector 結果を接続していない。

## 現帳票の再確認

実 DB を SQLite `mode=ro` で開き、版
`6d2187c0-ac7f-4d0c-90c8-a0fc381c2cc8` と保存 PNG を既存 `measure_page` で検査した。
DB、保存画像、レイアウト、意味づけには書き込んでいない。状態は DRAFT。

| page | plan fields | OCR bindings を持つ fields | 測定セル | 測定 fields | 最大誤差 px |
| --- | --- | --- | --- | --- | --- |
| 1 | 122 | 60 | 108 | 102 | 2.0 |
| 2 | 81 | 51 | 56 | 22 | 2.0 |
| 3 | 76 | 37 | 107 | 35 | 2.0 |
| 4 | 6 | 3 | 3 | 3 | 1.5 |

全ページに `REFERENCE_ANCHORS_INSUFFICIENT` がある。
page 2 の anchor 候補では `D60:AI60` が重複し、page 4 はセル3個で5 anchor条件を満たさない。
「測定 fields」は現 verifier が対象にしている全 plan fields で、OCR fields だけの数ではない。
これらを誤対応や大きな座標ずれが証明された件数とは扱わない。

既存の[read-only vector 診断](verification/paged-vector-geometry-20260913.md)では、
OCR fields 151個中140個の全四辺を観測でき、最大差は各ページ2 px未満だった。
その観測だけで現在の raster 判定を PASS に置換しない。
未描画境界11 fields、visibility、crop、registration の問題は残る。

## 新しい部品の境界

ISO PDF の CTM と path/stroke 操作を明示的に扱う。
仕様の参照先は [ISO 32000-1:2008 の §8.3–8.5](https://opensource.adobe.com/dc-acrobat-sdk-docs/pdfstandards/PDF32000_2008.pdf)。
新部品はファイルや font を解析せず、先行 PDF validator が resource / text を検証した
operation 配列を受け取るための純粋処理である。

- path を構成した時の CTM で点を default page points に変換する。fit/shift はしない。
- `q/Q` の描画状態を保存・復元し、開始済み path の途中で状態を勝手に変更しない。
- 実際に stroke した直線と矩形の辺だけを記録する。fill/clip/discard の path は線にしない。
- 曲線を端点間の直線に置換しない。同じ path の straight 部分にも curve 共存を明示する。
- width/cap/join/miter/dash/color/stroke CTM と paint ordinal を保持する。
- clip が作用する可能性は未検証として残す。dash/hairline を solid line とみなさない。
- page rotation/crop/単位の変換、可視性、overpaint、cell identity 対応の証明は含めない。
- 戻り値の `visibilityVerified` / `productionQualified` は常に false。

上限は operation 500,000、straight segments 100,000、graphics stack 256、
数値の絶対値1,000,000。破棄 path も segment 上限に含め、非有限数・bool・
退化変換・未対応命令・不正状態を部分成功にしない。
このアプリ内上限は外部 helper の network/cgroup/timeout を代替しない。

## 検証と次工程

48 unit tests を実行。座標変換の順序と復元、回転・せん断、負の矩形幅、
stroke/fill/clip の区別、curve、dash、色、後続 paint、数値/stack/segment 上限を検査する。
これは合成 operator 配列の試験であり、実 PDF の隔離解析や本番閾値の認定ではない。
対象 Ruff、Linux/Python 3.10 指定 mypy が成功。

```powershell
.venv/Scripts/python.exe -m pytest tests/unit/test_renderer_pdf_strokes.py -q --tb=short -o addopts='' -p no:cacheprovider --basetemp=.pt-vector-strokes-final-0913
```

glyph qualification 接続後の backend full は同じ process で継続中。
full が収集した後に追加したこの部品と48件の試験を、その full の検証範囲には数えない。
既存製品 module を import/変更せず、進行中 full の対象コードを差し替えていない。

次は隔離 helper で全 page の box/rotation/単位と描画線を取得し、同じ保存 PDF/code identity
に束縛する。小さなセルも含む各明示 OCR field の四辺を調べ、最終 raster の可視性と
未描画境界の独立証拠を組み合わせる。文字や位置の近さから cell/mapping identity を推測しない。
手書き crop の安全性や環境資格をこの線 inventory の成功から導かない。
実 OpenAI / Google 呼出し0回。ユーザーの OpenAI 許可上限100回は維持する。

## 固定 helper の接続実装（後続）

vector の zipapp は元の glyph broker / sandbox ABI を使用し、`__main__` だけを
所有された vector entrypoint に固定する。任意 command/module を受け取る入口を作らない。
既存 glyph module の global 変数・dispatch・code bundle を変更せず、先行 full の対象コードを維持する。
unit で glyph bundle の前後不変と vector bundle の決定的な内容を確認した。

非特権 inner では同じ read-only PDF の全 glyph/resource/text 検証が実際に成功した後、
page boxes、UserUnit、Rotate、content SHA / operation count、stroke inventory を取得する。
再解析の前後で PDF/input/code と font集合、runtime identity を照合する。
broker は従来の固定 unit/cgroup/network/所有者 lease を維持する。
後半の失敗で前半の glyph report が一時的に存在しても、process 非成功時は broker が発行しない。
成功扱いには vector report の存在と全検査も必須である。

Windows側は PDF/font を解析せず、glyph validation の結果、全 page 順序、content identity、
page boxes の整合性、回転後の150dpi寸法と reference raster の一致、線の数値/style/資源上限を
検査する。path や glyph 名の近さで OCR field との対応を作らない。
clip/overpaint による最終可視性は未証明のままで、本番判定には使わない。

### この段階の検証結果

- operator / 合成 PDF / bridge schema: **88 passed / 0.48 秒**。
  全ページ、原点のずれ、回転、UserUnit、glyph/content linkage、寸法差、未対応 graphics、
  文書上限、report 改変、退化行列と不正色型、固定 bundle を検査した。
- OCR contract: **210 passed / 1 warning / 44.39 秒**。
- 対象 Ruff: 成功。Linux/Python3.10 指定の PDF/worker mypy と Windows bridge mypy: 成功。
- `git diff --check`: 成功。

初回 mypy で再exportされていない関数への参照と colorSpace の型不足を検出した。
元定義からの明示 import と文字列の型検査を追加して修正した。

実 WSL 用 `tests/integration/test_wsl_vector_helper.py` を追加した。
先行 `--basetemp=.pt-glyph-qualification-full-0913` は同じ process で完走した。
**1,351 passed / 1 skipped / 3 warnings / 1,922.76秒**、終了コード0。
実変換を重複させず、terminal 確認後に新 vector integration を開始した。
先行 full は新しい vector code/test を収集しておらず、その成功だけでは vector 全体回帰を主張しない。

現時点の成果は実装と合成 fixture 検証であり、実環境の動作・本番 geometry 認定・有効化は未達。

## 実 WSL と保存済み4ページの後続確認

先行 full の終了コード0を確認した後、以下を実行した。

```powershell
.venv/Scripts/python.exe -m pytest tests/integration/test_wsl_vector_helper.py -q --tb=short -o addopts='' -p no:cacheprovider --basetemp=.pt-vector-helper-real-0913
```

**2 passed / 61.56秒**。両ページの実描画線取得、同じPDFのglyph証拠との一致、
隔離・code/runtime identity、reference raster寸法改変の拒否を確認した。
これは合成 workbook を実固定環境で実行した試験であり、本番有効化ではない。

さらに、版/固定runtimeを引数に取る read-only 診断
`backend/scripts/inspect_prepared_vectors.py` を実行した。
DBは `mode=ro` と `query_only=ON`、artifactはdata root配下と保存SHAを検査する。
保存済み print.xlsx の bytes を同じ固定runtimeで再描画し、全4PNGの保存SHA一致を確認してから
vector helperを呼ぶ。最後に元quality_json/statusと参照した全artifact bytesの不変を確認する。
保存済みprint filesや実版への資格情報の書込みは行わない。

```powershell
.venv/Scripts/python.exe -X utf8 -m scripts.inspect_prepared_vectors 6d2187c0-ac7f-4d0c-90c8-a0fc381c2cc8 9b513e12512fc7dfe9cb71eee69649d002b02bdd78153c8669d54299466a1e01
```

| page | stroke segments | dashed segments | curved stroke paths | clipping unverified segments |
| --- | --- | --- | --- | --- |
| 1 | 44 | 3 | 0 | 0 |
| 2 | 40 | 11 | 0 | 0 |
| 3 | 45 | 2 | 0 | 0 |
| 4 | 13 | 0 | 0 | 0 |

合計142線分、3,515文字命令。全ページrotation 0、UserUnit 1、
MediaBox/CropBoxは `[0, 0, 595.303937007874, 841.889763779528]`。
新描画PDF SHAは `05412cd7e2771f7afcd64fbf490cc479010cb1b399c4fee21c3d21ef5cee35ed`、
vector code bundle SHAは `5a764e351fc54040dae9934683b6c3802ae4f5367ecae17fcd7c253b29548007`。
runtimeは `9b513e12512fc7dfe9cb71eee69649d002b02bdd78153c8669d54299466a1e01`。
保存原本を新しいPDFへ置換した結果ではなく、PDF producer metadata差を画像ずれと扱ってもいない。

現時点で実環境の線取得まで確認したが、各field四辺の照合・最終rasterの可視性・
未描画境界・手書きcrop・本番環境資格は未完了。
`productionQualified=false` / `visibilityVerified=false` / DRAFTを維持する。
破線16線分を実線の連続coverageとして計測しないことが次工程の具体的な条件である。

診断scriptの対象Ruff/mypyも確認し、型注釈と明示的なread-only接続closeを追加した。
frontend全体は同ターンに34 files / 369 passed、build成功。既存645.86 kB bundle警告あり。
Chrome実操作を検証した意味ではない。OpenAI API 0/100、Google API 0。

## 記入欄の四辺照合（後続実装）

`reference_vector_measurement.py` を追加した。固定 bridge が検証した線群と、保存された
page/field/明示 mapping binding、同一 SHA の参照画像を入力とする数値診断である。
PDF/font を backend で再解析せず、診断 CLI のみから使用する。qualification API、
ACTIVE/受付 gate、旧 raster verifier、元座標・意味づけ・出力先は変更しない。

- PDF の page 原点・回転・UserUnit を DPI へ直接変換する。座標の fit/shift を行わない。
- 明示 bindings が空の見出し等は OCR 対象から除外する。不明な bindings は拒否する。
- 四辺ごとに線分の重複を除いた **path coverage** を測る。これは描画された画素の
  coverage ではない。破線を含む候補を明示し、visibility の証明には使わない。
- 複数候補から近い線を選ばず AMBIGUOUS、欠けた辺は UNOBSERVED とする。
  clip/curve 共存/hairline は対象外。最大探索距離8px、path coverage 0.85 は診断条件であり、
  本番資格として承認された閾値ではない。線分照合にはページ内2,000,000回の上限を設ける。
- 座標を丸めて2px超過を隠さない。すべての資格フラグを false のまま返す。
- `renderer_pdf_strokes.py` の protocol を v2 にし、破線の位相を後続で評価するため
  subpath 内距離を元 user units で保存する。新 subpath でリセットし、曲線長を推測しない。
  bridge は新項目を検証し、旧形式を暗黙に補完しない。

### この段階の検証

```powershell
# backend
.venv/Scripts/python.exe -m pytest tests/unit/test_reference_vector_measurement.py tests/unit/test_renderer_pdf_strokes.py tests/unit/test_renderer_vector_pdf.py tests/unit/test_renderer_vector_bridge.py -q --tb=short -o addopts='' -p no:cacheprovider --basetemp=.pt-vector-direct-reviewed-0913
.venv/Scripts/python.exe -m pytest tests/integration/test_wsl_vector_helper.py -q --tb=short -o addopts='' -p no:cacheprovider --basetemp=.pt-vector-v2-real-0913
# repository root
backend/.venv/Scripts/python.exe scripts/verify_ocr_contract.py
```

- 最終対象 unit: **125 passed / 0.55秒**。任意の field/mapping 名、小さい欄、四辺、
  回転/原点/UserUnit、3px/20px変位、2px直上の誤差、曖昧候補、重複線分、破線、
  欠けた罫線、ID重複、SHA/寸法不一致、数値/処理上限を検証した。
- v2 の実 WSL helper: **2 passed / 64.42秒**。旧 glyph の全検証と隔離条件を維持する。
- 変更前 OCR contract: **210 passed / 1 warning / 41.35秒**。
  変更後: **210 passed / 1 warning / 53.11秒**。
- 対象 Ruff、4 source files の mypy は成功。先行 backend full **1,351 passed** は
  この新 module/test を含まない。今回の実装を full で再検証したとは扱わない。

同じ read-only CLI で実4ページを測定した。全保存PNGのSHAが再描画と一致し、
元 DB status/quality_json と印刷成果物 bytes が不変であることを診断終了時にも確認した。
丸めを除いた最終診断の PDF SHA は
`97a603fcb6a587eaec4ccb7fe9b183064b220139294c93ed35f6646b69439fe9`、
固定 helper code bundle SHA は
`b3ec8440e01f8b3cb0902861b3532519e9fce4860ed7e70177a011b287e8aee7`。

| page | OCR記入欄 | 全四辺観測 | 未検証欄 | 観測した辺の最大差（px、概数） |
| --- | --- | --- | --- | --- |
| 1 | 60 | 53 | 7 | 1.5875 |
| 2 | 51 | 49 | 2 | 1.4484 |
| 3 | 37 | 35 | 2 | 1.3901 |
| 4 | 3 | 3 | 0 | 0.7958 |

151欄中140欄は幾何的な四辺を観測できた。未検証11欄は以前の独立診断と同じ元セル範囲で、
不在の境界を近傍セルから補っていない。これは最終 raster の可視性や手書き crop の
検証成功ではない。DRAFT と本番拒否を維持し、テンプレート有効化は未完了である。
OpenAI API の作業全体の許可上限は100回、失敗・再試行も含める。実呼出しは **0/100**。
実 OCR 未実施のため、認識誤り・association mismatch の実測件数は未測定である。

## 最終 PNG の ink/contrast 観測（後続実装）

`reference_vector_raster.py` を追加し、read-only 診断 CLI へ接続した。
入力は同じ隔離 renderer から生成され、版の参照 SHA と一致する opaque PNG と検証済み
vector report に限定する。既存 `reference_geometry.py` と同様に所有 PNG の画素を読むが、
PDF/font の解析を backend へ移していない。qualification API/DB/版に新しい資格を保存しない。

PDF スキルの最終描画確認に従い、保存4ページの PNG も目視で確認した。再生成した4画像は
すべて保存 SHA と一致し、今回の変更は帳票の描画・改ページ・文字・原本を変更していない。

### 手法と限界

- 四辺照合に使った明示 segment index だけを検査する。暗い画素を別位置で探して
  座標を補正せず、失敗した線を別の線へ置き換えない。
- stroke width は CTM による法線方向の投影を用い、せん断した法線ベクトルの長さで
  過大評価しない。線の両端、clip/curve、hairline、未対応の幅・dash cap は未検証にする。
- dash/phase/subpath distance は元 user units で評価する。奇数長配列の繰り返しや
  長さ0の要素を保持し、半画素以内の dash transition は塗り区間/空白区間から除外する。
  根拠は [ISO 32000-1 §8.4.3.6](https://opensource.adobe.com/dc-acrobat-sdk-docs/pdfstandards/PDF32000_2008.pdf)。
- 塗り区間の暗い画素と両側との contrast、空白区間の ink を別々に数える。
  全面黒塗りは ink があっても contrast がなく、白塗りは ink がないことを検出する。
- 画素の存在だけで、それを描いた object が元の罫線であるとは証明できない。
  `objectIdentityVerified` / `visibilityVerified` / `thresholdQualified` /
  `productionQualified` はすべて false。描画順・交差・手書き crop への保証はしない。
- 診断の ink=128、contrast=64、transition guard=0.5px は本番閾値の認定ではない。
  ページ内2,000,000 sample、stroke width16px、PNG20,000,000 pixels/64MiBの上限を守る。
  数値や画像の不一致・処理上限超過で途中までの成功結果を返さない。

### 実4ページの観測

| page | 照合対象線分 | paint 区間を sample できた線分 | 未検証線分 | 検査位置数 |
| --- | --- | --- | --- | --- |
| 1 | 37 | 36 | 1 | 18,619 |
| 2 | 34 | 25 | 9 | 25,502 |
| 3 | 31 | 31 | 0 | 23,766 |
| 4 | 13 | 13 | 0 | 8,802 |

対象115線分中105線分では、検査した paint 区間のすべてに暗い画素があった。
contrast は一部位置で不足し、これを未検証のまま捨てて完全可視性の証拠にはしない。
10線分は dash 長0.3325pt（150dpiで約0.693px）のため、両端のtransitionを除くと
内部 paint sample が得られない。これは描画欠落の証明ではなく、この測定法の解像度限界である。
元の151欄中11欄にある未描画境界の問題とも別である。

この実行の新 PDF SHA は
`ada4b30ad7ddd0e8401e1ef1721a5d6b528e080b828b2968da09b0b9cb1e8f4c`。
runtime/image/code identity は直前の四辺照合と同じ。保存成果物と DB の不変を終了時も検証した。
上の線分集計は描画命令142線分すべてではなく、明示 OCR field の四辺候補に使われた集合である。

### 検証結果と残作業

```powershell
# backend
.venv/Scripts/python.exe -m pytest tests/unit/test_reference_vector_raster.py tests/unit/test_reference_vector_measurement.py tests/unit/test_renderer_pdf_strokes.py tests/unit/test_renderer_vector_pdf.py tests/unit/test_renderer_vector_bridge.py -q --tb=short -o addopts='' -p no:cacheprovider --basetemp=.pt-vector-raster-all-0913
.venv/Scripts/python.exe -m ruff check app/services/reference_vector_raster.py scripts/inspect_prepared_vectors.py tests/unit/test_reference_vector_raster.py
.venv/Scripts/python.exe -m mypy app/services/reference_vector_raster.py scripts/inspect_prepared_vectors.py
# repository root
backend/.venv/Scripts/python.exe scripts/verify_ocr_contract.py
```

- 新しい raster unit **24 passed**、vector 関連全体 **149 passed / 0.61秒**。
- OCR contract は変更前 **210 passed / 39.76秒**、変更後 **210 passed / 40.15秒**。
  それぞれ既存 warning 1件。Ruff/mypy は対象ファイルだけの検査である。
- 今回 frontend、OCR provider/model/prompt/schema、mapping/crop/補正保存、
  WSL worker、資格発行・ACTIVE/受付コードは変更していない。既存 OCR contract を維持した。
- 先行 full backend/ frontend の成功を、この新しい診断の full regression と称さない。
  実 OpenAI **0/100**、Google **0**。実手書きの正解率・誤対応件数は未測定。

本番資格発行・ACTIVE/受付の成功経路は依然未実装/未開放であり、DRAFTのままである。
残る実装・証拠不足は、未描画境界と細かい破線の独立検証、環境認定の残条件、
実入力に対する位置合わせ/crop、実OCR→レビュー→補正→確定→出力の通し検証である。
ユーザーの記入済み4ページが必要な作業と、まだ実装されていない資格発行を混同しない。
