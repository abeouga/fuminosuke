# 複数ページXLSX本番化 — 作業記録

2026-09-13更新: 利用者から未記入・記入済み各4枚の実印刷スキャンを受領した。
[実スキャン検証](verification/real-print-scans-20260913.md)で8枚の位置合わせと
12通りの誤ページ拒否を確認し、通常位置合わせの変換行列記録を修正した。
修正前後302 cropと画像・領域IDは不変。本番有効化はなお未完了であり、
現在の未実装をスキャン未提供の問題として扱わない。

続けて[送信前crop照合](verification/paged-crop-correspondence-20260913.md)を実装した。
受付snapshot・所有全体画像と実cropの不一致をprovider生成前に拒否し、失敗ページの理由を保持する。
実スキャン302cropの画素/領域は不変。この境界の実装だけで、未完了の環境・参照座標・
実筆画clearance条件や有効化gateを解除していない。

[未描画境界の測定コピー検証](verification/border-probe-20260913.md)では、
実4ページ151欄の全四辺を観測し、測定用の線以外の文字描画・font/width・ページ座標系の
一致を確認した。原本を変更しない診断方式の実証であり、正式な資格発行と本番接続は残る。

2026-09-13 19時台のChrome読み取り専用確認では、実テンプレートの工程5に
151欄設定済み・未割当0・設定不完全0・保存可能が表示されていた。
「再検出は不要です」と明示される一方、承認ボタンは本番未対応として無効である。
別のホームタブはバックエンド未接続・ワーカー停止を表示していた。
画面の保存済み表示を現在のAPI到達性やACTIVE成功の証明には使用しない。
この確認でタブ遷移・編集・再起動・ダウンロードは実行していない。
併行して[WSL起動Pythonの依存制限](verification/wsl-bootstrap-startup-20260913.md)の
修正前回帰テストを追加した。環境認定と本番接続が完了したわけではない。

2026-09-12の明示要求: ゴールはテンプレート有効化、本番OCR、公開版カードからの印刷用XLSX/PDF取得。
印刷操作は利用者が行う。Windows版Officeの制限解除だけで有効化する作業ではない。

## 実OpenAI APIの利用許可

2026-09-13に利用者から、この作業のOpenAI API呼出しを100回まで使用する許可を受けた。
作業全体の上限として扱い、turn・page・retryごとに枠を戻さない。
許可受領時点で本作業が明示的に実行した呼出しは0回。
実呼出し時は送信試行を記録し、失敗・応答不明・retryも計数する。
この利用許可は本番資格、ACTIVE/受付gate、既存の文書全体API予算の解除ではない。
fixtureで足りる検証のために外部送信せず、必要な実OCR検証に使用する。

## 進行中の実装

1. WSLへの専用変換依存導入とネットワーク隔離したPDF生成。
2. 公開版カードのXLSX/PDF取得導線と版固定。
3. 実帳票の保存range・印刷倍率・高度OOXMLを保持する印刷コピー、Office画像とfieldの検証。
4. ページ別結果のレビュー・補正revision・全ページ確定・出力の実装と検証。
5. 上記に基づくqualification・有効化・受付の接続と実OCR検証。

現時点でゴールは未達。3の版保存/UI接続と実WSL統合試験は完了したが、
実帳票のgeometry/crop資格検証は未完了。4のレビュー・補正実装は
[ページ別レビュー](paged-review.md)、[全ページ確定](paged-confirmation.md)、
[全ページXLSX出力](paged-output.md)、[文書識別・名前候補](paged-document-metadata.md)へ進んだ。
全ページ・全attemptの[API予算管理](paged-ocr-budget.md)とDB0034も追加した。
受付時の[prompt本文・応答model schema](paged-transcription-resources.md)も保存する。
実PDFの[OCR領域ベクトル診断](verification/paged-vector-geometry-20260913.md)では
151領域中140領域の四辺を直接測定したが、未描画境界11領域と実手書きcrop等は未認定。
後続の[埋め込み字形照合](verification/paged-embedded-fonts-20260913.md)で、実PDFの4subset・
695 glyph entriesについて通常/太字の輪郭・送り幅・hint一致を確認した。
同PDFの後続read-only分析では、全ページの実使用640 font/code組・延べ3515 glyphについて、
ToUnicodeが示す元font字形とembedded cmapの字形が一致し、誤った対応8件も検出した。
元XLSXの全文配置や実手書きcropの証明とは区別する。
さらに[ページ別文字数](verification/paged-source-character-coverage-20260913.md)は、
元XLSXの表示書式と改行を区別すると全ページで一致した。セル別配置/clipの認定とは扱わない。
ただしWSLの正式な資格発行経路への接続、文字対応・runtime/cropの認定は未完了。
[WSL runtime image](wsl-runtime-image.md)の明示選択経路も追加し、固定userspaceで実4ページの
PNG一致を確認した。host bootstrap・Windows fontsを含む全環境認定とは区別する。
続いて[Windows font集合の前後照合](verification/wsl-font-directory-20260913.md)を追加し、
固定runtimeで使用した全fileの内容identityと変換前後の一致を要求した。正式資格は未完了。
続けて[印刷確定への固定runtime接続](pinned-print-preparation.md)を追加した。
明示設定時はFODS測定・PDF生成に同じruntimeを指定し、両方の環境/font集合一致を保存前に要求する。
実アプリの設定切替と実DRAFTの再確定は、このAPI試験だけで実施済みとは扱わない。
固定runtimeの[worker identity診断](verification/wsl-worker-identity-20260913.md)では、
worker更新がruntime/font情報だけでは識別できないことを実WSLで再現した。
後続で起動時worker snapshotと隔離側の内容SHA、寸法測定/PDF生成間の一致検査を追加し、
実WSLの差し替え拒否も確認した。host bootstrapを含む正式資格はまだ未完了。
続いて[WSL印刷確定版の参照検証](prepared-reference-qualification.md)を追加し、
保存済み版の成果物対応と全ページ画像をWindows版Officeとは別経路で検証する。
成果物の一致は正式な環境/字形/crop資格の代替ではなく、有効化はなお未完了。
さらに[保存XLSXの再描画比較](prepared-reference-replay.md)を接続した。
固定した入力・runtime/font/worker条件で全ページ画像を比較し、証拠の再検査も行う。
再描画一致だけでは環境/字形/crop条件を解除しない。
5は未完了。書類一覧統合も未対応だが、本要求で新たな一覧機能を完成条件に追加しない。
旧単一ページ経路、新printLayoutの有効化・OCR受付gate、
provider/model/prompt/association、原本・既存版・過去jobを変更していない。
新paged requestへ既存の識別形式/桁数ヒントを渡す拡張は上記metadata文書に分離して記録する。

## WSL PDF候補経路

利用者の許可を受け、既存Ubuntu-22.04にaptからlibreoffice-calc、bubblewrap、fonts-noto-cjk、
poppler-utils、python3-pilを導入した。Windows側Officeやfontは変更していない。
LibreOffice 7.3.7系とWindows側26.2系は別rendererであり、古い座標・font認定を流用しない。

新しい`renderer_wsl.py`が固定のpage.xlsxだけを渡し、Linux brokerが固有tempへコピーする。
`renderer_wsl_worker.py`はsystemdの固有transient unit内でbubblewrapを起動する。
readonly runtime/fontと専用入力・出力だけをmountし、ホストの/mnt/c、/root、ネットワークを隠す。
uid 65534、capability全削除、no-new-privileges、PID/IPC/user/net等のnamespaceを分離する。
Windows原本path・元ファイル名・API key等をOfficeへ渡さない。マクロ・active contentを無効化する。

メモリ1GiB・swap 0・tasks 64・CPUQuota 100%・unit実行60秒・出力file 64MiBを要求する。
起動したnamespace内からcgroupの実際のmemory/pids/cpu値を読み、指定値との不一致は拒否する。
Office変換とpdfinfoによる全ページ寸法検査は同じ隔離内で実行する。PDF parseをbackendへ戻さない。
所有者のstdin heartbeat消失/EOFでそのunitだけを停止し、tempを回収する。
変換失敗時のAppContainer/non-isolated/旧Page 1/参考画像へのfallbackはない。

新経路は`PRINT_PDF_RENDERER=wsl`、`PRINT_WSL_DISTRIBUTION=Ubuntu-22.04`で明示選択する。
配布defaultは既存appcontainerを保持し、この開発hostの.gitignore対象.env.localだけwslへ設定した。
起動中backendは未再起動で、設定反映はまだ実利用環境で確認していない。
既存workbook adapterのunsupported要素・小数倍率拒否は維持する。
2026-09-13に明示版指定の新adapterを追加した。詳細は次節。

参考: [bubblewrapの公式説明](https://github.com/containers/bubblewrap)。
ツール単体を完全sandboxと称さず、実際の構成と試験結果で判定する。

## 現時点の検証

- 変更前OCR contract: 210 passed。
- 変更後OCR contract: 210 passed（40.10秒）。
- 既存Windows AppContainerで人工2ページのPDF生成: RENDER_FAILED、原本不変。
- 新WSL実変換: 1/2/3ページのページ数・A4用紙寸法・SHA・隔離条件が一致。
- 異なるページ数/用紙寸法の拒否、owner切断時のunit終了、実API取得と版/原本不変を検証。
- WSL新規7件と既存印刷API19件: 26 passed。実OpenAI/Google呼び出し0回。
- frontend対象3ファイル: 59 passed。公開版カードからの取得、下書きへの誤配送がないことを固定。
- その後のfrontend全体: 31ファイル344 passed。build成功（既存bundleサイズ警告あり）。
- 変更したPythonのRuffは通過。Windows bridge/印刷service/configの限定mypyと、
  Linux workerの`--platform linux`限定mypyは通過。
- backend全体: 867 passed、既存1 skipped、既存3 warnings、661.00秒。
  `pytest tests -q -o addopts='' -p no:cacheprovider --basetemp=.pt-production-wsl-full-0912`。
  その後、WSL終了待ちtimeoutを明示的なRENDER_TIMEOUTへ変換する限定補完を行った。
  最終WSL7件も再実行して成功（12.33秒）。git diff --check成功。
- Chromeの独立UI fixtureでカード内の「印刷用XLSX」「印刷用PDF」のDOM表示を確認。
  実ユーザーのテンプレート取得・有効化を行った証拠ではない。視覚・実ダウンロード確認は未完了。
- OCR座標、実font一致、手書きcrop clearance、実スキャン位置合わせ、
  production resource/threshold認定はこのPDF生成成功だけでは証明していない。

終了済みテストの自生成5ディレクトリは、削除せず
`C:/Users/hanam/.codex/tmp/llmocr-production-wsl-tests-20260913`へ退避した。
検証UIの一時ソース2ファイルと専用Vite 5197は終了・撤去した。実アプリ5173/8000は停止していない。
staging・commit・push、ユーザー原本・実DBの更新なし。

## 次の必須境界

以下は初期調査時の課題。印刷コピー、専用レビュー/補正/確定/出力、文書識別metadataは
後続節と上記リンクで対応した。mark/composite/空fieldページは明示拒否を維持する。
全ページ予算は後続の専用対応を追加した。実行resource固定と実環境qualificationはなお未完了。

`print_workbook.workbook_copy`の高度OOXML/複数sheet/小数倍率拒否、`page_config`の文書識別・mark・
複合field拒否、`PagedOcrExecution`のdocumentPagesだけの保存、`OcrResultService._require_single_page`
のレビュー/補正/確定/出力拒否を個別に解決する。旧snapshotを新しい型へ暗黙移行しない。
ページ別一意identity・全件数・mappingId/targetPath/sourceRegionId・crop SHAを保存から出力まで比較する。
全ページのprovider call budgetと実行resource snapshotも本番受付の前提とする。

2026-09-13の再調査: `OpenAIOcrProvider.extract`は毎回`call_count=0`から開始し、
`PagedOcrExecution.execute`はページごとにworkflow/providerを呼ぶ。
通信clientを生成しないfixtureで、上限3・61項目×2ページを順次実行すると
予約番号が`1,2,3,1,2,3`、全体6回になった。実API呼出しは0回。
ページ処理後の成功response合算だけでは、失敗・並行batch・retryの予算を制御できない。
文書単位で送信前に予約し、失敗や不明な応答を予算から戻さない方式と、
再試行・再起動時にも計数を失わない保存経路が必要と判明した。
後続の[予算管理](paged-ocr-budget.md)でこの拒否・予約・保存経路を実装した。

また、`JobPageService.execution_config/validate`は設定値の変更を検出するが、
`OpenAIOcrProvider._call_detail`は呼出し時の`load_transcription_prompt()`と
実行中の`CompactTranscriptionBatch`を使用する。版番号だけのsnapshotでは実体変更を検出できない。
新paged受付でprompt内容/SHAと使用schema・runtime identityを固定し、
一致しない実行を最初のprovider呼出し前に拒否する境界が必要である。
legacy requestをこの追加対応で無断変更しない。
後続の[prompt・schema保存](paged-transcription-resources.md)で本文の固定とmodel schema不一致の
拒否を実装した。SDK/native依存等のruntime identity全体は、まだ固定できていない。

予算対応後のoffline境界診断では、実SDK clientを作らずproviderのparse先を固定fixtureへ
差し替え、prompt loaderだけをメモリ上でA/Bへ変更した。設定値・promptVersion・画像と
manifestは不変でも、送信instructionsのSHAが異なることを確認した。
SHAは`ece7856f6833ca6fef959f9ea28c07d402415460709dd85d277dae8889153353`と
`cf5dae9d613d8bd3227e967d40afd56ae433116fba555844225879a81200b584`。
これは再現用の仮promptであり、実promptの変更や外部API呼出しは行っていない。
診断scriptはrepo外`C:/Users/hanam/.codex/tmp/paged-budget-inspection-20260913/check_request_resources.py`。

同日のPDFスキルによるread-only font診断では、実4ページの印刷用PDF
（SHA `56bb73ba362702df0e251de465bffa1bd2cfe79e2c2b53201db85aeb78c35fd4`）の
全ページresource辞書を調べた。4つの共有埋込みTrueType subsetがあり、通常のMeiryo UIは
245/255/2単純字形、Meiryo UI Boldは127単純字形を含む。
通常字は`meiryo.ttc`、太字は`meiryob.ttc`の単純輪郭集合と全件一致した。
太字ファイルSHAは`7d54f5a6a4202dae7b54d5a854770cb4fecd33f8975c0996e89c6ce31f4b0808`。
現`resolvedFonts()`はfamilyごとの通常styleのfc-matchだけを記録しており、
この太字ファイルはrenderer.jsonの候補に含まれていなかった。
resourceの共有参照は各ページで全字形が描画された証明ではなく、TTC face/Unicode対応・
composite/CFF・実fontロードtraceも未証明。PDF/原本/認定artifactは変更していない。
診断scriptはrepo外`C:/Users/hanam/.codex/tmp/paged-metadata-inspection-20260913`に保持した。

実行・結果保存のDB追加が必要な場合は、新表とmigrationで既存result型と分離し、
旧revision/confirmed/reconciliationを流用してページ情報を落とさない。
原本と保存版の印刷条件を変更せずに再現できない倍率は、黙った丸めで通さず、
新しい明示的な印刷/OCR設定の確定操作とrevision比較を設計する。

## 2026-09-13 原本保持・実帳票4ページの診断

`preserving_print_workbook.py`と`print_xml.py`を追加した。
`printAdapter=ooxml-print-preserving-v1`を明示したplanだけが新経路を使う。
このmetadataを既存版へ自動追加する処理、GUIでの選択・確定操作はまだ実装していない。
元のadapterは維持し、不明なadapter値は拒否する。

新経路はZIPのbyte/part数、XMLのUTF-8/DTD/entity/depth/node数を制限し、
数式・外部参照・埋込みオブジェクト・未知の拡張要素を拒否する。
選択した可視sheet以外は既に非表示のsheetに限定する。別の可視sheetを黙って隠さない。
既知の受動的なOffice互換metadata、無効参照/定数/セル参照のdefinedName、
定数による入力規則、正しいcontent-type付きprinterSettingsを保持する。
非表示行列、セル内縮小、prefix付きXMLと印刷設定より後ろのXML要素を保持できる。
追加の数式評価を許可したものではない。未知の拡張や入力規則の式は拒否する。

変更対象は選択sheetの印刷設定・改ページ、sheetPr/pageSetUpPr、当該Print_Areaのみ。
XMLを全体再serializeせず、namespaceを解決したbyte範囲で置換する。
セル・style・結合・その他sheet/partを再生成しない。原本をopenpyxlで保存しない。
小数のcommonScaleは新経路でも拒否し、ダウンロード時に丸めない。

WSL workerに固定FODS出力を追加した。PDFと同じnamespace/cgroup/leaseの中で
Officeによる寸法読取を行う。`wsl_workbook_geometry.py`はsource sheet名、
行/列index、繰返し数を明示して長さを復元し、非表示dimensionを0ptとして扱う。
WSL Officeはinch単位を出力するため、旧Windowsのtwip復元モデルを流用せず、
別model `wsl-fods-physical-length-v1`で未丸めの実測長を返す。
旧plan/legacy座標へ適用する処理は追加していない。誤差・raster校正は未認定。

診断関数の`font_source=windows`では、固定`C:/Windows/Fonts`だけを
`/reference-fonts`へread-only bindする。/mnt/cやユーザーファイルはsandboxへ公開しない。
内部mountinfoのroを検査し、専用fontconfigで参照する。fontのコピー・配布は行わない。
default Linux font経路は変更しない。各指定familyのfontconfig解決先・collection index・
file SHAをreportへ保存する。これはfamilyの解決probeであり、Office実描画中の全bold/italic/
glyph fallback/loaded modulesを完全に追跡するqualification証拠ではない。

### 実帳票のread-only測定

対象DRAFT版: `6d2187c0-ac7f-4d0c-90c8-a0fc381c2cc8`。
原本SHA: `aa1d088cf5ebae9cfca0f42d0ac3cbe1d8b23f7067196407f6ae3ce9c3cc3176`。
SQLiteはmode=roで参照した。元の保存倍率0.6957339287812906と
A1:AK28 / A29:AK60 / A61:AK89 / A90:AK102を変更していない。

- 原本13partの新adapter検査が成立。小数倍率のコピーは明示拒否。
- メモリ内だけで69%にした診断コピー + Linux代替font: 幅878.4pt、PDF8ページ。
- 同じ診断 + Windows指定font: 幅690.0768pt、PDF4ページ、各A4 595.304×841.89pt。
- ページ別行高合計: 1047.132 / 1016.4888 / 1032.9264 / 1055.2608pt。
- 指定familyのMeiryo UI/游ゴシック/ＭＳ ゴシック/ＭＳ Ｐゴシックは
  Windowsのmeiryo.ttc/YuGothR.ttc/msgothic.ttcへ解決し、SHAを取得した。
- 最終診断PDFは220658bytes。原本bytes不変、保存版/DBへの書込なし。
- Linux代替fontで測定した56%でも4ページになったが、これは原因切分け用の診断であり、
  本番設定候補として採用・保存していない。font差を縮小率だけで隠す修正はしていない。

新adapter・寸法parserと旧印刷adapterの対象unit: 50 passed。
WSLの実PDF/FODS、hidden管理sheet、原本保持は8件通過後、Windows fontの実検証を1件追加。
backend全体は905 passed、既存1 skipped、既存3 deprecated warnings、802.21秒。
実行: `.venv/Scripts/python.exe -m pytest tests -q -o addopts='' -p no:cacheprovider
--basetemp=.pt-production-preserving-full-0913`（cwd=backend）。
新WSL9件もこの全体実行で通過した。Ruff、Windows bridge/adapter/geometryの
限定mypy、Linux workerの`--platform linux`限定mypyは成功。
この工程の変更前OCR contractは210 passed（36.90秒）、変更後も210 passed（44.14秒）。
実行はrootから`backend/.venv/Scripts/python.exe scripts/verify_ocr_contract.py`。
`git diff --check`成功。今回frontendコードは追加変更していないため、前段の344件/build結果と区別する。
終了済みの自生成テスト6ディレクトリは削除せず
`C:/Users/hanam/.codex/tmp/llmocr-preserving-tests-20260913`へ退避した。
OpenAI/Google API 0回。原本・実DB・ACTIVE状態・過去jobは変更していない。

次は指定fontと実測geometryを持つ新しい印刷設定の明示確定・revision保存、
PDFと同一条件の基準画像、crop clearance/位置合わせ/依存・資源のqualificationを接続する。
この診断4ページ成功をテンプレート有効化または本番OCR完了として扱わない。

### 次工程で保持する状態遷移

- 新しい印刷設定の確定は、通常の意味づけ/矩形変更の自動保存から分離する。
  Office変換を毎編集で起動しない。変換中にDBのmutation lockを保持しない。
- 候補はDRAFTのbaseRevision・原本SHA・元page範囲に固定して作成し、保存時に再照合する。
  競合した候補を現行版へ上書きしない。既存canonical矩形は変更しない。
- 新model保存後の`_source`/`save_authoring`/`page_plan`にも明示分岐が必要。
  現在のlegacy分岐へ通すとhidden dimensionの拒否や小数倍率の再計算が発生する。
  測定済みsource寸法と整数印刷倍率からページ表示だけを再構築し、意味づけ変更で
  geometry/referenceを勝手に再測定しない。既存旧モデルの動作は維持する。
- 印刷用PDFは確定版のfontSource・adapter・倍率で生成し、基準画像と同じ条件に固定する。
  現在のexportは既定Linux fontを使うため、Windows診断の成功だけでは公開版取得が完成しない。
- 仮想見出しはpageRangeに所属する表示ノードのままとし、OCR対象physical fieldへ混入させない。
  新倍率でページ内位置が無効になる場合の明示的な調整/拒否を用意する。
- `ReferenceQualificationService`は旧Windows環境の検証モデルに固定されている。
  新環境の独立証拠と失効条件を追加し、旧booleanや旧reportを新認定として採用しない。

## 2026-09-13 印刷用ファイルの明示確定・版保存

前節の診断専用段階から、`POST print-layout/prepare?base_revision=...`と
`PrintPreparationService`、GUIの「印刷用ファイルを確定」を実装した。
WSL設定時のみ利用でき、DRAFT・revision・layout SHA・原本SHAを確認する。
元のページ範囲を維持してWSLから寸法を測定し、整数%で収まる新printLayoutを計算する。
元のcanonical layout、意味づけconfig、過去の受理済みOCR jobは変更しない。
printScalePolicy未指定の旧planは従来の小数倍率計算を維持し、暗黙移行しない。

`wsl_workbook_geometry.apply_dimensions`はsourceRangeと元行列indexから新ページ用座標を
構成する。cellId、mappingId、sourceRegionId、targetPath、sourceParts等は保持し、
非表示行列と測定0幅/0高が不一致なら拒否する。保存した寸法は `_source` から復元し、
ページごとの意味づけ保存でOffice再測定を起動しない。

新reference出力は、隔離worker内で一度生成したPDFそのものをpdftoppmで150dpi PNGへ変換する。
1ページ20Mpx、50ページ、aggregate bundle 64MiB未満、既存cgroup/lease/time limitを維持する。
PNGの形式・寸法を同じ隔離内で検査し、固定名のZIPとreportを返す。
backendへnative PDF parse/rasterを戻さない。PDF/PNGの全件数・用紙寸法・SHAを照合し、
別PDFから画像を作ったりPage 1だけを採用したりしない。

生成中はDB mutation lockを保持しない。生成後、原本・config SHA・revisionを再確認してから
短いtransactionでPDF/XLSX/全PNG/reportのartifact参照をTemplateVersionのprintLayoutへ保存する。
PREPARE_TEMPLATE jobのmutable planだけを同期し、過去OCRのsnapshotは更新しない。
以前のreferenceQualificationは失効させる。本番認定・ACTIVE gateはまだ変更していない。

printFiles.renderKeyは原本、geometry/font evidence、用紙/余白/倍率、ページ範囲/transformに固定する。
取得時はartifactの所属job・ID・path・SHAとrenderKeyを確認し、保存したbytesを返す。
Officeを取得ごとに起動しない。意味づけ/仮想見出し変更で印刷条件が同一ならファイルを保持し、
ページ境界/用紙条件を変更した場合は古いファイルを落として再確定を要求する。
新modelで未確定の場合、既定Linux font経路へfallbackしない。

GUIの生成状態はlocal stateであり、通常の意味づけ編集へglobal busyを伝播しない。
ページ条件の操作と重複生成だけを一時的に止め、閲覧・拡大は維持する。
新しい外部authoring stateが届いた後に古い生成responseで画面を戻さない。
既に保存したページを開くだけでは不要なpreview再計算を行わない。
失敗時は保存済み状態を保持して再試行できる。

### 現時点の検証

- 変更前OCR contract: 210 passed（58.27秒）。
- 対象backend（寸法・prepare API・既存取得API）: 42 passed（42.99秒）。
- prepareの実WSL統合: PDF/全PNG保存・SHA一致・再取得まで成功。その他のAPI境界fixtureでは
  競合/変換失敗の無更新、意味づけ保存後のartifact保持、破損artifact拒否を固定した。
- frontend全体: 31ファイル347 passed（14.51秒）。build成功、既存bundleサイズ警告あり。
- 静的検査: 対象Ruff成功。新service/bridge/geometry等の限定mypy成功。
  Linux workerも`--platform linux`で成功。
- backend全体: `pytest tests -q -o addopts='' -p no:cacheprovider
  --basetemp=.pt-print-preparation-full-0913`、915 passed / 1 skipped / 3 warnings（626.69秒）。
- 変更後OCR contract: `backend/.venv/Scripts/python.exe scripts/verify_ocr_contract.py`、
  210 passed / 1 warning（51.46秒）。既存単一ページ契約の回帰は対象試験で検出されなかった。
- Chrome既存4タブはホーム画面で、編集中の画面はなかった。タブ選択によるDOM読取のみ。
  ネイティブUI操作・reload・保存は行っていない。
- 試験時点の8000は自動reloadなし、新prepare endpoint未反映だった。
- 実DRAFTのservice読取: unsupported=[]、4ページ、285 source fields、printFilesなし。
  この285にはmeaning mappingのない見出し等も含まれ、285件のOCR呼び出しを意味しない。

残りは、新モデルのgeometry/crop/font/runtime資格検証、ページ別レビュー・補正・確定・出力、
それらに基づく有効化と実際の読取までの検証。印刷ファイル確定は本番OCR認定ではない。

### 実DRAFTへの適用・全ページ目視

処理中ジョブ0、対象revision一致を確認したうえで、同じserviceをローカルから呼び出し、
版`6d2187c0-ac7f-4d0c-90c8-a0fc381c2cc8`へ印刷成果物を保存した。
これは実DBのDRAFT変更であり、認証済みブラウザAPIの操作試験ではない。
revisionは`2b026bcecccc5542cfb729614e43b086bd95fae1e11c5a89e2ee243237f33512`。
4範囲A1:AK28 / A29:AK60 / A61:AK89 / A90:AK102を維持し、倍率は69%。
canonical layout、意味づけconfig、原本、準備ジョブ以外の全job snapshotの前後digest一致を確認した。
状態はDRAFTのまま。

- artifact generation: `faddf957091e43718320f96a71cc5c69`
- XLSX: 30,901 bytes、SHA `a3df374f120a5db6ef0853f2dfb02dd8dd706ceb6afb7495c2bd68015b606d62`
- PDF: 220,658 bytes、SHA `56bb73ba362702df0e251de465bffa1bd2cfe79e2c2b53201db85aeb78c35fd4`
- 取得serviceが返す両成果物のSHAは保存artifactと一致。再変換なし。
- 同じPDFから生成された全4 PNGを目視し、明白な文字欠け・表のはみ出しを認めなかった。
  これは印刷物・スキャン画像でのcrop検証やfont資格証拠の代替ではない。
- 独立罫線測定: 全ページ`REFERENCE_ANCHORS_INSUFFICIENT`。測定可能な辺の最大誤差は
  page1〜3が2px、page4が1.5px。page4は罫線セル3個で、旧判定の5-anchor条件を満たさない。
  未測定項目を成功扱いにせず、閾値・gateは変更していない。

実プロセスは`dev.ps1`がbackend終了時にfrontendも終了する構成だったため、
処理中ジョブ0と対象PID/親プロセス/portを照合してから同スクリプトを非表示で再起動した。
port8000のhealth=ok、新prepare routeがOpenAPIに存在し、port5173はHTTP200。
Chromeホームでもbackend接続中・worker稼働中を確認した。新prepareボタンの実クリック試験は未実施。
この作業で明示実行したOpenAI/Google API呼び出しは0。
通常起動される既存同期workerのバックグラウンド通信数は計測していない。
本工程のテスト専用9ディレクトリは`C:/Users/hanam/.codex/tmp/llmocr-print-preparation-tests-20260913`
へ移動して保持した。既存の他テスト証拠は変更せず、commit/pushは行っていない。

## 2026-09-13 現在のUI全体回帰の再確認

backendのresource全体回帰と競合しない、APIをmockするfrontend試験を実行した。
最初の`npm.cmd test`は368 passed / 1 failed（15.34秒）。
`TemplatePage.test.tsx`のpaged識別設定保存ケースが、初回印刷設定取得中の無効な工程ボタンを
クリックしていた。工程navigationのDOM表示と、保存ページrevisionの読込完了は別の時点である。

このケースに`waitFor(() => expect(identityStep).toBeEnabled())`を追加した。
初回読込完了後に保存payloadを検証する意図を明示し、保存内容・再検出案内・有効化gateの
assertionを維持した。テスト削除/skip/時間制限拡大は行わず、UIの制約やOCR処理も変更していない。
保存中に工程を即時切替できることは、別の遅延保存試験をそのまま維持して検証する。

- `npm.cmd test -- src/pages/TemplatePage.test.tsx`: 35 passed（4.72秒）。
- 再度の`npm.cmd test`: 34 files / 369 passed（14.87秒）。
- `npm.cmd run build`: 成功。既存のbundle 500kB超警告あり。
- `git diff --check`: 成功。
- 本確認の変更ファイル: `frontend/src/pages/TemplatePage.test.tsx`と本記録のみ。
  backend product、mapping/crop/provider/保存契約、実DB・原本・版を変更していない。

これらは実ブラウザ操作・実際の公開版有効化・実OCRの証明ではない。
backend全体は`.pt-paged-resources-full-0913`の同一processで継続しており、完走結果は別途記録する。

## 2026-09-13 WSL font検索式の追加診断

`renderer_wsl_worker.resolved_fonts`はXLSXのfont nameだけを集め、family単位に重複除去して
そのまま`fc-match`のpattern引数へ渡す。太字/斜体の区別が落ちることに加えて、
family文字列が検索式として再解釈される問題を、実WSLの`fc-pattern`で確認した。

| 意図したfamily | 現行方式で解析された先頭family | escape後 |
| --- | --- | --- |
| Example-Family | Example | Example-Family |
| Example:Family | Example | Example:Family |
| Example,Family | Example | Example,Family |
| Example\Family | ExampleFamily | Example\Family |
| Meiryo UI | Meiryo UI | Meiryo UI |

全ケースでweight=bold、slant=italicを同時に渡した。escape後はfamilyを完全保持し、
weight=200/slant=100を得た。これはinstalled Fontconfigのparserの検証であり、
架空familyの実font存在・Officeの採用font・production qualificationを証明するものではない。

[Fontconfig公式仕様](https://fontconfig.pages.freedesktop.org/fontconfig/fontconfig-user.html)は
family中のbackslash、hyphen、colon、commaのescapeを要求する。
単にCLIへ別引数として渡すだけではFontconfig自身のpattern構文を無効化できない。
workerのfont要求保存ではこの文字列境界とweight/slantの保持が必要である。
`PrintPreparationService.prepare`はFODS側とreference PDF側のresolvedFontsを比較しているため、
両出力で同じ新規record形式を使用し、過去の保存reportを暗黙変換しない。

診断script: `C:/Users/hanam/.codex/tmp/paged-vector-inspection-20260913/probe_font_patterns.py`。
実行はCodex bundled Python。全5ケースのescape後roundtrip assertionが通過。
Office変換0回、OpenAI/Google呼出し0回、font/config/実DB/版/原本の変更なし。
進行中のbackend全体試験の対象コードも変更していない。worker修正・境界試験・実変換比較は次工程。

### 後続の実装・検証

上記診断後、font要求のface保持・pattern escapeと新report policyを実装した。
実テンプレートの同一入力を再変換し、全4ページのPNG byte一致と保存DB/artifact無変更を確認した。
旧記述の「次工程」はこの後続実装前の時点を表す。
検証内訳、候補記録と実描画font資格の区別、未解決のWindows終了確認、
直前backend fullの失敗結果は[WSL font検証](verification/wsl-font-requests-20260913.md)に記録した。
本番有効化は未完了である。

### 字形検証の実装準備

[WSL PDFの字形・Unicode検証](renderer-glyph-validation.md)に、source face identity、
複合字形・hint・全ページ文字命令を照合する新moduleと合成fixture 81件の結果を記録した。
本番worker/APIへの接続、実帳票の隔離実行、正式な資格発行は未完了であり、
既存Windows validatorや保存DRAFT、ACTIVE/受付gateを変更していない。

### 字形 helper の隔離実行と qualification API 接続（後続）

上記81件の単体実装後、固定環境の専用 glyph helper と保存/GET 検査を実装した。
[実行証拠](verification/wsl-glyph-helper-20260913.md)に実4ページのPNG一致、
3,515文字命令・640使用font codeの照合結果を記録した。原本・保存済み成果物は不変。
`prepared_reference_glyphs.py` と qualification API は別artifactに検証reportを保存し、
版・PDF・全印刷graph・所有者・SHA・検証コードの変更で失効させる。
文字照合によって環境全体・位置・visibility・crop safetyを認定せず、本番gateを維持する。
対象164件と変更前後OCR contract各210件が成功。実WSL APIの試験を含む34件も成功。
接続後の backend full は **1,351 passed / 1 skipped / 3 warnings / 1,922.76秒**、終了コード0。
収集後に追加した描画線検証の新部品・テストは含まない。

## 有効化の成功経路と検証段階の監査（2026-09-13、後続）

**診断が成功すれば現在のコードのまま有効化できる、という状態ではない。**
以下を実コードで再確認した。実版のACTIVE化やgate変更は行っていない。

| 境界 | 現在の実装 | 有効化に対する意味 |
| --- | --- | --- |
| 承認HTTP・Repository | `api/v1/routes/templates.py:approve_template_version` と `repositories/template_repository.py:approve` が `require_legacy_ocr` を呼ぶ | `printLayout` があれば、資格結果を調べる前に無条件拒否 |
| WSL参照資格のGET | Phase 3のissuerが保存済み所有証拠を再評価し、限定scopeで `qualified` / `environmentQualified` を発行・失効する | 資格は発行できるが、DRAFT→ACTIVEとOCR受付gateは未接続 |
| ページ別OCR受付 | `job_page_service.py:_require_qualified_reference` が status取得後に無条件fail | ACTIVEをDBで手動設定しても正規の受付はできない |
| 旧単一ページの受付・抽出 | `job_service.py` / `extraction_service.py` にも `require_legacy_ocr` がある | ここを削除してPage 1を旧経路へ流すのは解決ではない |
| crop判定 | `reference_crop_safety.py:qualify_crop` は入力内容に関係なくUNVERIFIEDを返す | 記入済み画像を用意するだけでは現在の関数は成功しない |
| 印刷物取得 | `TemplatePage.tsx` は `active ?? primaryVersion` を `TemplatePrintFiles` へ渡す | カードの取得UI実装と本番OCR有効化は別の完了条件 |

従って残作業を「APIの利用許可」や「記入画像の提供」だけに帰してはいけない。
Phase 3で資格発行・失効検証までは実装した。成功した資格を正規の承認/受付へ渡す実装は残っている。
診断コードの試験成功は、その未実装部分の動作証明ではない。

### 検証段階の分離（後続で承認、実装中）

監査時点のSafety Contract §12は、ACTIVE/本番利用前にgeometry・環境・cropの資格を要求していた。
一方で、空のreferenceから実手書きのclearanceを証明しないことも要求している。
有効化をユーザーの事前記入試験へ常に依存させるか、検証の実施段階を分離するかを明示する必要がある。

提案は、本番安全性の検査を削除せず、次の二段階へ分離することである。

1. **有効化時**: 版・原本・明示field/mapping・全ページの描画/座標・隔離環境の有効な証拠を検証する。
   環境・未描画境界・細かい破線等の未検証条件を保存booleanで通さない。
2. **OCR実行時**: 受付snapshotと実入力のページidentity/SHA、位置合わせ・cropの安全条件を検証し、
   未検証の入力をproviderへ渡さない。失敗時の値・補正・再試行の保護を維持する。

この案は後続のユーザー発言「承認する」により承認された。Safety Contract §12.6/§12.8と
AGENTS §9へ二段階の契約を反映した。現在の本番gateを成功flagで解除したものではない。
実行時検証の仕様・失敗時の復旧方法・独立した実画像による根拠も必要で、
ACTIVEだけを達成してOCRが未対応のまま全体完了とする案ではない。
§10に沿って新旧境界・回帰試験・実行証拠を揃える。

承認前の監査は文書のみ。追加のOCR/Google API呼出し、実DB/原本/設定変更はなかった。

### 承認後の第一段階

`paged_safety_stages.py` と WSL資格APIを接続し、有効化前の阻害条件から
記入画像に依存する `CROP_CLEARANCE_UNVERIFIED` を分離した。
`cropSafety` は `DEFERRED_UNTIL_INPUT`、実行段階は `NOT_EVALUATED` と明示し、
page/field identityを保持する。明示bindingのない見出しを実筆画cropの対象にしない。
GETでは所有印刷graph/実装fingerprint等の検証後にこれらを再生成する。
診断の成功は資格発行ではなく、既存のgeometry・環境・ACTIVE/受付拒否を解除しない。
旧Windows資格/単一ページのcrop判定と過去jobのsnapshotは変更しない。
実行時の具体的な検証・資格発行・正規の承認/受付成功経路は引き続き実装対象である。

画面の段階別表示と、実4ページを固定WSL環境で再確定したコピーの検査を追加した。
全ページPNG・field/binding・canonical layout・原本が不変であることを確認し、
コピーでの資格保存/GETも一致した。ただし参照点不足と環境・閾値の未認定が残る。
詳細と検証範囲は[承認後の検証記録](verification/paged-safety-stages-20260913.md)を参照。
