# Renderer Sandbox & Production Qualification Closure — 2026-09-08

**本番認定は不成立、ACTIVE・複数ページOCR受付は閉鎖を維持する。** AppContainer起動、通信拒否の実測、Office停止原因のnamed pipe検証、埋込みfontの照合、実行時DLL manifest、PDFium helper、crop clearanceの計算と拒否境界を実装した。現LibreOfficeバイナリはAppContainer内でPDFを生成できず、helperのネットワーク隔離と実帳票のcrop clearanceも未認定である。

正本は[環境artifact](reference-renderer-evidence/closure/environment.json)、[通信・Office試験](reference-renderer-evidence/closure/network.json)、[font試験](reference-renderer-evidence/closure/fonts.json)、[resource試験](reference-renderer-evidence/closure/resources.json)。前Phaseのenvironment/calibration evidenceは保持した。

## 1–3. HEAD、worktree、baseline

開始HEADは`d33e192cb4483fe3a2a857a2b5f51f046a8f8fbe`、main、単一worktree。開始時はtracked変更18ファイル。今回のbaseline実行前からあるuntracked項目43件はdirectory集約を含む。開始時SHA一覧をrepo外の`C:/Users/hanam/.codex/tmp/renderer-closure-start.json`へ保存した。基準テスト生成物も一覧に入るため、今回変更の照合ではコード・文書とtest tempを分ける。

AGENTS.md、OCR Safety Contract全体、print layout文書、Geometry/Environment報告、各evidence、renderer・geometry・qualification・job pages・crop実装を再確認した。実DBをread-onlyで確認し、DB revisionとmigration headは共に`0031_template_field_identity`。migration追加・適用、実データ更新はない。

- 変更前OCR contract: **194 passed、52.27秒**。
- 変更前print layout / geometry / qualification / environment / job pages / process / cleanup: **131 passed、198.15秒**。
- このPhaseの変更前backend fullは実行していない。

## 4–9. Network候補、採用構成、実Office、必要権限

候補としてAppContainerを実装・実行した。`CreateAppContainerProfile`と`SECURITY_CAPABILITIES`でcapability数を0にし、CREATE_SUSPENDEDで作成したprocessをJob Objectへ所属させてから再開する。無制限tokenへのfallbackはない。current userは管理者ではなく、昇格・system firewall変更は行っていない。Office installとWindows fontsの既存read ACLを利用し、追加write ACLは固有request tempのAppContainer SIDに限定した。

Python probeはLibreOffice同梱Pythonを使用。同じlocalhost listenerへの通常Job processの接続は成功、AppContainer内はTimeoutErrorとなり、listener側にも接続がない。integration testでは実tokenの`TokenIsAppContainer=1`を確認した。Internet endpointへの試験は0件。ユーザーcache配下のPythonを最初に使った場合はDLL初期化前に`0xC0000135`で終了したため、既存のProgram Files配下runtimeで再検証した。cacheやOffice installのACLを広く変更していない。

**実OfficeのXLSX→PDFは失敗した。** COM launcher、直接soffice.bin、初期化だけ、事前に空profileを準備したケースでも停止を再現。source/profileへのアクセスは進むがPDFは生成されない。上限process数を増やした診断でも解消せず、約50MB RSS・低いCPU消費のまま待機した。Job timeoutで所有processだけを終了しtempを回収した。

停止の具体的な互換性問題は**named pipe名前空間**である。

1. 同版[Windows OSL pipe実装](https://github.com/LibreOffice/core/blob/libreoffice-26.2.3.2/sal/osl/w32/pipe.cxx)は`\\.\pipe\OSL_PIPE_...`を構成する。
2. 同版[Office IPC初期化](https://github.com/LibreOffice/core/blob/libreoffice-26.2.3.2/desktop/source/app/officeipcthread.cxx)はcreate/open失敗時に再試行する。[Desktop初期化](https://github.com/LibreOffice/core/blob/libreoffice-26.2.3.2/desktop/source/app/app.cxx)はこのIPCを有効にする。
3. 実AppContainer内の[Win32 probe](reference-renderer-evidence/closure/appcontainer-pipes.json)では通常名のCreateNamedPipeがWinError **5**、`LOCAL`名は成功した。[Microsoft仕様](https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-createnamedpipea)とも一致する。
4. 実インストールのsal3.dllの`osl_createPipe`を同じ境界で呼ぶ[直接probe](reference-renderer-evidence/closure/appcontainer-osl-pipe.json)もWinError **5**で失敗した。
5. Office threadの[stack採取](reference-renderer-evidence/closure/appcontainer-stack-samples.json)では`SleepEx`/`ZwDelayExecution`と`InitVCL`経路に整合するaddressを確認した。stack上の候補address全てを正式なunwind済みcall stackとは扱わない。

これらからIPC互換性が初期化を妨げていると判断した。他の全初期化要素が適合することまで証明したわけではない。network capabilityやunrestricted tokenを付けて回避しない。Office DLLのpatch、hook、再ビルドは行わない。必要なのは単なるfont/temp read権限の追加ではなく、AppContainerに適合するIPC実装または別の隔離環境である。

AppContainer失敗後の代替評価: program別Firewallは管理者管理と全child binaryへの適用設計が必要。helper serviceもservice account/ACL/network policyの配備が必要。Windows Sandbox/containerは現ホストで認定済みruntimeがなく、feature導入・権限・再現試験が必要。いずれもこのPhaseでは導入せず、本番gateを維持する。

## 10–13. Actual fontとloaded dependency

人工XLSXのNormal styleと実cell fontをCalibri/Arial/Meiryo/Yu Gothic/Aptosへ明示設定。PDFはOfficeが生成し、helper内のpypdfでFont resource・FontDescriptor・埋込みTrueTypeを取得した。埋込みsimple glyphの輪郭bytesを、Windowsの実font/TTC faceの輪郭と比較した。fontバイナリはrepoへ保存していない。

| requested / Normal | PDFで観測したfont | 対応するhost輪郭 | 判定範囲 |
| --- | --- | --- | --- |
| Calibri | Calibri | 21 simple glyph一致 | 人工文字列で実選択を確認 |
| Arial | ArialMT | 21一致 | 同上 |
| Meiryo | Meiryo | 21一致 | 同上 |
| Yu Gothic | YuGothic-Regular | 22一致 | 同上 |
| Aptos | DejaVuSans | 許可候補のhost fileと一致なし | fallbackを観測、拒否を維持 |

各host path/SHA、Normal解決、PDF resource名、埋込みsubset SHA、候補ごとの一致数をfonts.jsonへ記録した。UNO descriptor/MDWだけを実選択の証拠としていない。PDFはOffice出力の観測点であり、OSのfont file open全イベントをtraceしたものではない。composite/CFF・全Unicode字形・theme/混在styleは未認定。Aptosの代替fontはPDF名まで判明したがロード元file identityは証明できず、`FONT_FALLBACK_UNVERIFIED`を維持する。

同版の[PhysicalFontCollection](https://github.com/LibreOffice/core/blob/libreoffice-26.2.3.2/vcl/source/font/PhysicalFontCollection.cxx)と[Windows font実装](https://github.com/LibreOffice/core/blob/libreoffice-26.2.3.2/vcl/win/gdi/salfont.cxx)も確認した。family照合とglyph単位のfallbackは別経路を持つため、人工ASCII列の成功を全字形の保証へ広げない。

Job内の実PIDを列挙し、PSAPIでロード済みimageを観測する。OfficeとPDFiumそれぞれにapplication / Windows OSのpath・SHA一覧、manifest SHA、観測失敗codeを保存する。runtime manifestも現fileと照合し、変化したreference evidenceを失効させる。短命moduleを取りこぼし得るpolling方式であり、`completeTrace=false`。DLL以外のconfig/registry/動的fontアクセスを含む全installation closureではない。

file hashはpath・size・mtime・ctimeでcacheし、初回または変化時に再計算する。hash中の変更も拒否する。Office program全体を毎request読み直す処理を避けた。同じmtime等を保つ敵対的改ざんの検出器としてこのcacheを認定していない。

## 14–19. PDFium helper、resource、lifecycle、temp、concurrency

PDFiumのparse/rasterとPDF fontのparseは専用processへ移動した。backendの環境identity取得もPDFiumをimportせずmodule位置だけを取得する。helperは固定の`page.pdf → page.png/raster.json`、1ページ、DPI 96/150/300のみ。任意code/module/output pathのRPCを設けていない。64MiB PDF、20,000,000 pixels、64MiB PNG、1MiB reportを上限とする。

Office policy: Job commit **1GiB**、同時4 process、CPU hard cap 20%、45秒。helper: **512MiB**、同時2 process（Windows venv launcherを含む）、20%、30秒。kill-on-job-close、CPU時間制限、wall timeoutを適用する。起動失敗時に無制限実行へ移らない。child native fail-fast後もbrokerが継続し、invalid PDF・複数PDFページ・過大pageを拒否するintegration testを追加した。helperのnetworkはまだ隔離されていないため「完全sandbox」としない。

simple・2ページ・3ページ・多数列・多数行・20,000セルを実Officeと新helperで実行した。全件生成成功。Office peak Job commitは約556–561MB、helperは約43MB（罫線fixture）。font fixtureのpeakもresourcesではなくfonts.jsonのexecutionに保存する。単位はRSSと混同しない。これらは**Officeがuncontainedな診断経路**の測定であり、AppContainer成功時のresource認定ではない。

既存のtimeout/cancel/parent death/他process保護/global mutexの試験を維持。新AppContainer profileに所有markerを設け、異常終了後のstale回収対象にする。profile名は固有temp path由来で、別pathのprofile名への置換は拒否する。temp回収はreparseを拒否し、readonly属性の自生成directoryも扱う。検証途中にUSERPROFILEをtempへ向ける試みでDocuments directoryのreadonly回収に失敗したため、互換性に必要なprofile pathを保持するallowlistへ修正した。provider key/proxy等の任意環境変数は新renderer/helperに渡さない。

[AppContainer親process強制終了の実測](reference-renderer-evidence/closure/appcontainer-parent-death.json)でも、子の終了、所有markerの残存、lease解放、tempとAppContainer profileの回収を確認した。失敗試験で残った自生成readonly temp 4件も、所有markerとleaseを確認して回収した。

複数backend processは既存global mutexで1件に制限する。独立Job間のprocessを一括killしない。HTTP切断から同期renderのcancel接続、別user/sessionの運用認定は残る。

## 20–23. Clearance・padding・threshold・DPI

candidate policyは`clearance-bounded-physical-v1`。各fieldについて、物理mmでwidth/height、想定inkのborderからの距離、隣接inkまでの距離、罫線幅、筆画幅、paddingを区別する。

`halfStroke = (borderStroke + inkStroke) / 2`

`clearance = min(contentInset - halfStroke, neighborGap - padding - halfStroke, min(width,height)/2 - halfStroke)`

`toleranceMm = min(0.1mm + 1px × 25.4/dpi, 既存2px × 25.4/dpi)`

通過には`clearance > toleranceMm`と実測errorがtolerance以下の両方が必要。paddingを増やしてgeometry誤差を正当化せず、隣接領域に対するclearanceはむしろ減る。registrationの誤差予算は混ぜない。既存geometry detectorの2px、探索窓、X/Y補正は変更していない。

96/150/300dpiで同じ物理寸法・policyを使用し、wide-clearance PASS / narrow-clearance FAILを確認する。20px field移動は既存geometry verifierでもFAIL。これは独立に与えた人工ink-envelope条件の検証であり、実手書き内容の分布を認定したものではない。現templateには独立測定済みclearanceがないため、`qualify_crop`は各fieldをUNVERIFIEDとし、本番閾値は未認定。

新helperを通したexplicit843の[3 DPI実測](reference-renderer-evidence/closure/dpi-verification.json)では、各DPIの再生成PNGが一致し、前Phaseの同DPI PNGとも**bytes単位で全件一致**した。[crop/geometry独立検証](reference-renderer-evidence/closure/crop-geometry-verification.json)ではgeometry PASSの同じ入力に対してwide PASS・narrow FAIL、fieldのみ20px移動でgeometry FAILを確認した。

## 24–32. Artifact、失効、reference合成、gate

環境artifactはschemaVersion **2**。Office/OS/font/geometry identityにsandbox候補実装・helper・PDF parser・clearance policyのversionとコードSHAを追加した。manifest・実測network/Office/font/resource/crop evidenceをSHAで参照する。前Phaseのschema 1 artifactは現identity検証で失効する。既存TemplateVersionや受付済みjobを更新する処理は追加していない。

Reference Qualificationは従来のsource/plan/revision/reference PNG SHA、renderer/environment identityに、ページ別crop safetyとOffice/helperのexecution manifestを追加した。status取得時にmoduleの現SHAも照合する。保存されたqualified booleanだけで解除しない。

- ACTIVE: `PRINT_LAYOUT_OCR_NOT_AVAILABLE`の拒否を維持。
- 複数ページOCR受付: `PRINT_REFERENCE_NOT_VALIDATED`の拒否を維持。
- 全条件を満たす実環境がないため、DRAFT→ACTIVE→mock OCR E2Eは未実施。認定をmockで偽装していない。
- `environmentQualified=false`、`productionQualified=false`。
- 残る閉鎖条件: AppContainer内Office変換、helper network、全runtime/config/font closure、独立測定済みfield clearance、production resource/threshold認定。

## 33–44. 原本、変更境界、検証、未解決、Git

実ユーザーXLSX5冊は全SHA一致、全件renderer preflightでUNSUPPORTED_XLSX_FEATURE。内容値は報告しない。shapes/controls/formulas/hidden/advanced OOXML/20,000セル上限を解除していない。

変更は新reference rendererと診断・検証経路に限定した。新規: renderer_appcontainer / renderer_dependencies / renderer_font_evidence / renderer_raster / renderer_raster_worker / reference_crop_safety、closureのunit/integration tests、再現scripts、本報告・evidence。更新: renderer_process / renderer_temp / renderer_environment / office_reference_renderer / reference_qualification_service、cleanupとreference APIのmock接続。PDF font解析用に`pypdf==6.10.0`をpyprojectとrequirements.lockへ固定しbackend venvへ導入した。テストのassertion削除・skip追加・期待値弱体化はない。

legacy preview / OCR、provider/model/prompt/schema/association、registration、既存rectangle/mapping、補正/confirm、XLSX/Sheets出力、Drive/Pickerは変更していない。前後contractと同じ入力の回帰を根拠に**既存OCR contractを維持した**。実APIでの認識精度再測定や実帳票Golden replay全体の保証ではない。

OpenAI **0 calls**、Google **0 calls**。frontendソース変更なし。このPhaseのfrontend full/buildは実行対象外。最終backend full・静的検査・差分照合は末尾の確定結果を参照する。

次PhaseはAppContainerに適合するOffice IPC版または別の実network隔離環境の選定、helperの同等隔離、実field clearanceとfont条件の認定である。現Officeへのbinary patchやnetwork capability追加で検証を通していない。productionQualified=trueをこのPhaseの実績として報告しない。

staging・commit・push・reset・revertなし。最終HEAD/statusと今回の変更一覧はchange-audit.jsonへ保存する。

## 確定検証結果

| 検証 | 結果 |
| --- | --- |
| 変更後OCR contract | **194 passed**、59.91秒 |
| backend full | **696 passed, 1 skipped**、538.54秒。warning 3件 |
| reference qualification API再検証 | **4 passed**、77.28秒。最終fullにも含む |
| 最終security / clearance対象 | **12 passed**、3.09秒。native終了codeを`STATUS_FAIL_FAST_EXCEPTION`まで厳密化して再実行 |
| 実Office/helper resource matrix | 6条件、合計9ページ、生成成功 |
| 実Office/helper DPI | 96/150/300すべて再生成一致・前Phase PNG一致 |
| AppContainer native IPC | 通常pipeと実OSL pipeはWinError 5、LOCAL pipe成功 |
| parent death | 子終了・profile/temp回収成功 |
| Ruff | 今回の19 Pythonファイルで違反0 |
| mypy app | 36 errors / 6 files、今回の変更ファイルに新規0。既存6ファイルは開始時から変更なし |
| git diff --check / 追加コードwhitespace | 成功 |
| 原本5冊 | SHA全件一致・unsupportedを維持 |

途中の失敗を最終成功へ隠していない。最初の対象セットはmockが追加のexecution_metrics引数を受け取れず3件失敗した。引数を受け取れるようにし、元の拒否・保存・失効assertionを保持した。次の試験で上記USERPROFILE由来のreadonly cleanup不具合が発生し、原因修正後のAPI対象とfullで失敗0を確認した。

再現コマンド（backendでは`.venv/Scripts/python.exe`）:

- root: `backend/.venv/Scripts/python.exe scripts/verify_ocr_contract.py`
- backend: `python -m pytest -q -o addopts='' -p no:cacheprovider --basetemp=<未使用path>`
- backend: `python scripts/qualify_renderer_closure.py`
- backend: `python scripts/probe_appcontainer_ipc.py`
- backend: `python scripts/verify_closure_dpi.py`
- backend: `python scripts/finalize_renderer_closure.py`
- backend: `python -m mypy app --no-incremental`、今回変更Pythonファイルへの`python -m ruff check ...`

終了HEADも`d33e192cb4483fe3a2a857a2b5f51f046a8f8fbe`。環境fingerprintは`4a8711c12634d2a79fe8d56c6135c49704249f16e3a0848f2ce429adda9c07ba`で現identityとの一致を再確認した。既存コード・文書の欠落0、今回変更した既存ファイルは9件。今回生成したtest temp 8個はrepo外の`C:/Users/hanam/.codex/tmp/llm-ocr-closure-20260908`へ移動し、以前からあるtest tempは保持した。終了時のsoffice残留processとrenderer temp残存は0件。

最終worktreeはtracked変更20ファイル・untracked52項目（directory集約）。[status](reference-renderer-evidence/closure/git-status.txt)と[変更照合](reference-renderer-evidence/closure/change-audit.json)を参照。既存差分のstage/commit/push、整理・破棄はしていない。
