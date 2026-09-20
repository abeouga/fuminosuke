# 複数ページXLSX ACTIVE化 — Phase 1調査

2026-09-14。判定: **PHASE_1_COMPLETE**。対象版のACTIVE化は未実施。

## 依頼範囲と証拠の優先順位

直接の依頼は「Phase 1だけ」。添付「複数ページXLSXテンプレートを最短で安全にACTIVE化するプロンプト.md」の
Phase 1の6項目を調査した。文書中の「実装へ進む」、Phase 2〜5、最終ACTIVE化は今回に含めない。
製品コード、実DB、原本、保存qualification、環境設定は変更していない。
今回の変更は本記録の追加だけ。AGENTS.mdとSafety Contractは既存の二段階契約が適用できるため変更しない。

HEAD: `c0be81ac174e45c30aa57cc803a049641b16e0be`。開始時からtracked 55ファイルの変更と多数のuntrackedが存在する。
それらを今回の変更として計上せず、stage/commit/push/整理・破棄は行わない。

根拠は現コード、[Safety Contract §12](../ocr-safety-contract.md)、
[既存の完了監査](production-goal-audit-20260914.md)、[保存プラン照合](calc-plan-validation-20260914.md)、
[Calc整数PDF座標](calc-pdf-coordinate-precision-20260914.md)、[同一読込UNO観測](uno-same-import-20260914.md)、
[環境lease v2](wsl-environment-identity-lease-20260914.md)、
[execution evidence](wsl-execution-evidence-20260914.md)、[unit admission](wsl-unit-admission-20260914.md)。
Windows/AppContainer時代のqualification/geometry/closure記録も照合し、WSLへそのまま適用していない。
過去の成功件数・private版の測定を現在の対象実版の資格として扱わない。

## 1. ACTIVE化に必要な条件

契約上必要な条件と、現在のコードが実際に許可する条件を区別する。
**現在の実装にはprintLayout版をACTIVEにする成功経路がない。**

正式な成功経路で維持すべき条件は次のとおり。

1. 対象TemplateVersionがDRAFTで、検証したrevision/layout/configと承認時の内容が一致すること。
   原本・保存ページrange・canonical layout・元セル・mapping/targetPath/sourceRegionIdの対応を保つこと。
2. 所有原本SHA、印刷XLSX/PDF/report、全ページreference PNGの所有者・SHA・件数・寸法・graphが一致すること。
   新Calcは同一読込の寸法証拠と、原本/layout/configから再計算した全cell/field/bindings/transformの一致も必要。
3. 全OCR領域について参照geometry、描画、明示mapping、crop領域定義とページ境界が成立すること。
   geometryの表現誤差と実スキャンの位置合わせ・実筆画clearanceを別に判定すること。
4. 選択renderer/runtime/font/worker、実行時隔離、bootstrap等の有効な環境証拠があり、
   その証拠を正式な環境資格として評価する処理が成立すること。identity一致だけでは足りない。
5. 資格を版・原本・print revision・geometry model・artifact graph・実装fingerprint・環境へ束縛し、
   GETと承認時にも所有証拠・現在identity・期限・失効条件・未解決の有効化条件を再検証すること。
6. 元準備job、意味づけ/文書identity、公開版切替の既存契約を維持すること。
   旧ACTIVEのARCHIVED化、新ACTIVEと系列active_version_idの更新を原子的に行うこと。

Safety Contract §12.6/§12.8により、実入力のPAGE_INPUT_IDENTITY、SCAN_REGISTRATION、
CROP_CORRESPONDENCE、INK_CLEARANCEはprovider送信前の責務であり、今回の有効化条件へ追加しない。
ただし入力検査を未実装のままOCR受付/送信gateを開けない。ACTIVE化とOCR受付開放は別の変更である。

## 2. 現在falseになる直接原因

| 境界 | 現コードの動作 | 意味 |
| --- | --- | --- |
| 資格発行/GET | `reference_qualification_service.py::qualify/status`がWSLの`qualified`と`environmentQualified`をfalseにする | 測定を増やしてもtrueにはならない |
| 環境/誤差blocker | 同ファイルの`wsl_blockers`は再描画とglyphだけを条件付き解除する | 環境資格とgeometry toleranceには成立判定がない |
| anchor診断 | `reference_geometry.py::measure_page`のcodesを資格codesへ合流する | 欄別geometryがMEASUREDでもanchor不足によるFAILが残る |
| ACTIVE API/Repository | `templates.py::_approve_template_version`と`TemplateRepository._approve_locked`が`require_legacy_ocr`を呼ぶ | printLayoutが存在するだけで409 `PRINT_LAYOUT_OCR_NOT_AVAILABLE` |
| 段階表示 | `paged_safety_stages.stage_assessment`がcodesとblockersをactivationへ合流する | 診断表示でありissuerではない。空でもcertificateIssued=false |

承認APIには現状baseRevision入力がなく、Repositoryはqualificationを評価しない。
無条件拒否を削除するだけでは、版/graph/環境/期限を検証する承認処理にならない。

さらに対象実版は`layoutVerified=false`、`layoutVerificationDetector`未設定である。
汎用定義承認のmetadataもなく、既存RepositoryのXLSX構造条件
（`layoutVerified=true`かつdetector=`xlsx-structure`等）を満たさない。
印刷再確定処理はこのlegacy readinessを更新しない。
正式なpaged qualificationに基づく承認条件を既存条件へ接続する必要があり、
旧booleanの書換えだけで救済してはいけない。
元準備jobの`REVIEW_REQUIRED`とfieldMappingCount=151は実DBで確認した。
文書identityの全validation成功までは今回実行していない。

## 3. 3 blockerの分類と最小変更

| blocker | 現在のACTIVE判定 | 既存証拠で解決できる範囲 | 後続で必要な最小変更 |
| --- | --- | --- | --- |
| `WSL_ENVIRONMENT_QUALIFICATION_UNVERIFIED` | 隔離環境の正式資格は必須 | lease v2、execution検査、unit admissionの仕組みは再利用可能。ただし対象版にその証拠がない | 現workerで対象版を再確定し、完全な実行証拠・leaseを束縛。既存検査を合成する環境issuerと失効/現在照合を実装する。未証明のhost/manager境界を単なるidentity一致で認定しない |
| `GEOMETRY_TOLERANCE_UNQUALIFIED` | 参照geometryの正式判定は必須 | 新Calcのsource-plan再計算、全四辺、整数PDF座標MATCH、raster観測を判定根拠に利用できる。実測最大値へのfitは不要 | 支持された座標経路/倍率/DPIに限定し、表現誤差・全領域/四辺・描画証拠・ページ/crop定義から合否を出す。未知/曖昧/欠落は拒否。数値一致だけで可視性や実入力clearanceをPASSにしない |
| `REFERENCE_ANCHORS_INSUFFICIENT` | 現コードでは有効化blocker | 旧5点raster診断の不成立。新Calc自体の誤対応を実測した意味ではない。全OCR領域の独立四辺証拠が代替候補 | 新Calcに限り、全必要領域のgeometry/描画検証が正式成立したときだけ旧anchor結果を診断として保持し、activationの必須条件から分離する。旧モデル・未測定/失敗時は拒否を維持 |

この表は後続実装の提案であり、blockerを解除済みとするものではない。
Safety Contractは旧anchor診断の保持と、MEASUREDだけでの拒否解除禁止を要求する。
したがって今回は削除/格下げしない。新Calcの代替判定を実装する段階で、条件と回帰試験を
契約へ明記する。保存boolean、空codes、fixtureの成功を正式資格の代わりにしない。

## 4. environment identity lease v2だけで正式判定できるか

**できない。** `wsl_environment_evidence.py`のscopeは`identity-freshness-only`である。
版/graph/所有artifact、24時間の期限、Windows/WSL hostの前後一致、runtime/font/worker/
bootstrapの現物照合を提供する。`validate_execution`は非特権・通信拒否・固定資源上限・
unitControlsを検査するが、これらの一致を正式な環境資格へ昇格するissuerはない。

最新の記録でもhost v2は資格記録時の更新検出であり、元の帳票変換時のhost状態や
Windows/WSL全依存を遡及的に証明しない。unit admissionも実効設定の検査であり、
manager全設定や動的依存全体を認定した結果ではない。
対象/環境を限定したissuerの成立条件を明示し、証明できない条件を残す必要がある。
「issuerを追加するだけで必ず成立する」とは結論しない。一方、新しいclosure機構の
無制限追加を先に始める理由にもせず、必要条件と既存の観測を対応させて実装する。

今回読んだ実版と最終Calcコピーは新証拠を欠いているため、現在環境を照会しても
それだけで旧変換を認定できない。Phase 1ではWSL/Office/helperを起動していない。

## 5. Calc geometryに新しい測定が必要か

**新しい測定方式や無制限な追加校正は、現時点の最小作業には含めない。**
既存記録は実帳票604辺と独立holdout2304辺について、整数グリッドからのPDF座標予測に
不一致0を報告している。対応計算は現在の`calc_reference_precision.py`にも接続済み。
150dpiの表現誤差上限X=0.5010417px以下、Y=0.503125px未満は単位変換の根拠であり、
OCR許容値を0.5px近辺の実測最大値から決めたものではない。

ただし、**対象版を現worker・新Calcで正式再確定し、既存方式を再実行する必要はある。**
後述の保存証拠は現在の検査で失効/拒否される。
過去の座標観測を設計根拠として参照することと、古いgraphを現在資格として受理することは異なる。
既存の`thresholdQualified=false`/`visibilityVerified=false`をそのままtrueへ置換せず、
原本/保存plan/全四辺/PDF/rasterから毎回合否を導く条件付き判定が未実装である。
実筆画の新測定はACTIVE化のためには要求しない。

## 6. 新Calcでもanchor不足を必須blockerにすべきか

現コード上は必須blockerだが、**旧5点anchor方式そのものを新Calcへ永久必須にする根拠はない。**
目的はページ全体とfieldの物理対応の検証であり、同等以上の独立した全領域検証で満たす設計が可能である。
今回の保存reportでは、ページ4は測定可能な罫線セルが3個しかなく5点条件を満たせない。
ページ2は下側の2 anchorが同じrangeとなり、5個の異なるanchorにならない。
また旧処理はbindingsなしを含む全285 fieldsを検査し、罫線なし/測定不能でも同codeを出す。
新しい欄別geometryの151 OCR領域とは検査母集団も異なる。

したがって「anchor不足=Calc座標が誤っている」とは言えない。
一方、151欄のMEASUREDだけで全285 fieldsのsource/layout対応、文字可視性、crop領域定義まで
検証済みと拡張してはいけない。代替判定が成立するまでは現在の拒否を維持する。

## 今回の読み取り専用確認

SQLiteは`mode=ro`、`PRAGMA query_only=ON`。SQLAlchemyもこの接続を使用し、通常Database初期化の
journal変更処理を使わずに`_prepared`と`status`を呼んだ。対象recordのstatus/layout/qualityは前後一致。

| 項目 | 対象実版 | 後続UNO記録の最終Calc privateコピー |
| --- | --- | --- |
| 版ID | `6d2187c0-ac7f-4d0c-90c8-a0fc381c2cc8` | 同IDだが別DB。実版への流用不可 |
| DB | `backend/data/app.db` | `%TEMP%/llmocr-prepare-copy-vnxpvohr/app.db` |
| status / ページ / 全fields / OCR領域 | DRAFT / 4 / 285 / 151 | DRAFT / 4 / 285 / 151 |
| geometry | `wsl-fods-physical-length-v1` | `wsl-calc-print-grid-v1` |
| qualification | 未保存、status()はUNVERIFIED | 保存済みFAIL。現在status()はUNVERIFIED / PRINT_LAYOUT_CHANGED |
| 現在の_prepared | `Prepared renderer linkage changed` | `WSL execution isolation evidence is missing or invalid` |
| 拒否の具体的根拠 | fontRequestPolicyなし。後続のruntime/font/worker/bootstrap/core/unit証拠もない | unitControls欠落。bootstrapPythonIdentity/coreDumpGuard/brokerCoreDumpGuardもない |
| 印刷XLSX/PDF/reportと全4 PNG | ファイル存在・保存SHA一致 | ファイル存在・保存SHA一致 |

実版revision: `2b026bcecccc5542cfb729614e43b086bd95fae1e11c5a89e2ee243237f33512`。
Calcコピーrevision: `a57aaa41151f27e94c2185e843aa43f7ed47f57c79172f857f3d6b7b02707b91`。
Calcコピーの保存report SHA: `8e397cb8d1b26cd8da0603179b3d7fb3d84ffc9f442de45e89d588d4aa4a4f59`、内容一致。
保存fingerprintは`2672e90af69076951238a6b8b4d1fdab4928033ebbecf23f322eb254baf62d36`、
現在計算値は`bcebf9368114726a2f5fb3c72863bd2256bda397d99c0ee95e4f546e15d1b841`で不一致。
保存reportはdeterministic=true、glyph 3515文字命令/640 font-code一致と前掲3 blockerを持つ。
これらは保存履歴の内容確認であり、新fingerprintでの正式再認定ではない。
`_prepared`が失敗したため、現資格経路によるgeometry再計算成功とは報告しない。
XLSX/PDFのHTTP取得・ACTIVE版カード操作は実施していない。

## 後続作業の最小順序と終了範囲

1. Phase 2: 対象版を既存の明示prepare経路で新Calc/現workerへ再確定する。
   保存range、元セル/canonical layout、mapping/targetPath/sourceRegionIdを前後比較し、最新環境証拠を取得する。
2. Phase 3: 既存の証拠検査を使う正式issuer、geometryの成立判定、条件付きanchor診断分離、失効検査を実装する。
   本文書で不足とした環境条件が成立しなければ、未証明のまま発行しない。
3. Phase 4: APIとRepositoryの両方を資格検査へ接続する。旧XLSX readiness、revision照合、版切替も扱う。
   共通`require_legacy_ocr`の全呼出元を一律解除せず、OCR受付gateは維持する。
4. Phase 5: 正式資格保存/GET/ACTIVE/保存XLSX・PDF取得を対象実版で確認する。

以上は次Phaseの作業範囲であり、今回実行していない。新しいschema/測定機構の追加を先行課題として増やさない。
ACTIVE化までの全体目標は未達だが、Phase 1の6項目の結論が出たため調査を終了する。

## 検証と変更境界

backend cwdで次を実行し、**139 passed（3.82秒）、終了コード0**。

```text
.venv/Scripts/python.exe -B -m pytest tests/unit/test_wsl_environment_evidence.py tests/unit/test_wsl_host_identity.py tests/unit/test_wsl_execution_evidence.py tests/unit/test_paged_safety_stages.py tests/unit/test_calc_pdf_coordinates.py tests/unit/test_reference_qualification.py::test_measurement_rejects_field_shift_missing_edges_and_page_size -q --tb=short -o addopts='' -p no:cacheprovider --basetemp=.pt-phase1-activation-audit-20260914
```

leaseの拒否・期限/host条件、execution証拠、二段階診断、整数座標、旧測定の拒否境界を確認した。
これは単体試験であり、実WSLの資格成立・本番認識品質の証明ではない。
文書内のローカル参照パスと実コードを照合し、`git diff --check`は成功。
製品変更がないためOCR contractスクリプト、backend full、frontend test/buildは今回再実行していない。
過去の2348件fullはunit admission/host v2後の全体成功として流用しない。

製品コード・データ・provider/model/prompt/schema・mapping/association/crop・補正/出力を変更せず、
**既存OCR contractを維持した**。実入力の認識誤り/association mismatch/schema errorは未測定。
OpenAI API **0/100回**、Google API **0回**。本番OCR、review、correction、confirmation、XLSX結果出力は未実施。
