# XLSX Reference Renderer Environment Qualification — 2026-09-08

**環境認定は不成立、production gateは閉鎖を維持する。** Windows Job Objectによるプロセス収容、host identity、環境artifactの失効検査、96/150/300dpi測定まで実装した。ネットワーク遮断、Officeの実選択font、完全なinstallation closure、crop安全性に基づく本番閾値は未認定。geometryの改善を環境認定の代わりにしていない。

正本: [環境artifact](reference-renderer-evidence/environment/environment.json)、[DPI dataset](reference-renderer-evidence/environment/dpi-dataset.json)、[resource probes](reference-renderer-evidence/environment/resource-probes.json)。前Phaseの校正evidenceと元ユーザー帳票は書き換えていない。

## 1–3. HEAD・作業開始・baseline

開始/終了HEADは`d33e192cb4483fe3a2a857a2b5f51f046a8f8fbe`、main、単一worktree。開始時tracked変更18ファイル、untracked36項目（directory集約を含む）。開始時ファイルSHA一覧をrepo外へ記録し、今回変更した範囲と既存差分を区別した。AGENTS.md、Safety Contract、print layout、最新2報告、校正dataset/analysis、現サービス・テストを再確認した。

変更前のOCR contractは194 passed。print layout / reference qualification / geometry / job pagesの関連セットは112 passed（167.11秒）。今回の変更前backend全体は未実施。実DB変更、migration追加・適用なし。

## 4–9. identityとfont

環境identityはschemaVersion 1。時刻・PID・temp path・測定peakをfingerprintへ入れない。以下を記録してhashを算出する。

- LibreOffice version/build、起動COM実体SHA、program直下170個のexe/com/bin/dll/rdbの個別SHAと集合SHA、adapter version。
- geometryModelVersion=`lo-import-twip-v1`、source geometryとpage transformの実装ファイルSHA。geometry計算自体は今回変更していない。
- pypdfium2 **5.12.1**、pdfium.dll SHA、Pillow/NumPy/OpenCV versionと直下native module SHA、Python runtime identity。
- Windows version/build/UBR/DisplayVersion、architecture、Job Object実装・network/resource/temp policy version、閾値policy versionと実装SHA。
- requested Normal family、Normal styleの解決結果、expected host family、fontファイルpath・SHA・availability。

LibreOfficeは**26.2.3.2**、build `70e089b17412e4cb7773e41413306b17a2328c34`。
soffice.com SHAは`6dc82122e9caf7e4c297885ee35e69bfe698b28684fe6eda3d016dd21370776c`。
pdfium.dll SHAは`cc2058add54b9da299a4496d526b79c375c6f0eb3a9000552a45c60f83765d55`。
version問合せもJob Object内で実行する。ただしshare/config tree、実行時に選ばれる全moduleの同定までは完了しておらず、`completeInstallationVerified=false`。

Calibri/Arial/Meiryo/Yu Gothicの既知Windowsファイルを参照でき、Aptosは前Phaseと同様に未確認。font本体はコピーしていない。**ファイル存在・SHA一致とOfficeの実選択fontは別**なので、既知fontでも`actualOfficeFontVerified=false`、`FONT_FALLBACK_UNVERIFIED`を保持する。欠落fontは`FONT_IDENTITY_UNAVAILABLE`も記録する。誤ったfont artifact、SHA変更、認定booleanの書換えを通さないテストを追加した。

Normal fontのbold/italicはLibreOfficeのFontDescriptorへ反映され、scheme/themeもfont model解決に関係することを同版[StylesBuffer](https://github.com/LibreOffice/core/blob/libreoffice-26.2.3.2/sc/source/filter/oox/stylesbuffer.cxx)と[UnitConverter](https://github.com/LibreOffice/core/blob/libreoffice-26.2.3.2/sc/source/filter/oox/unitconverter.cxx)で確認。新artifactではNormalのscheme/bold/italic、検出したrich text等を未解決semanticsとして記録する。条件付き書式等は既存preflightでも拒否される。theme・rich text・全cell fontの完全な解決器やfallback実選択の証明は未実装であり、推測で認定しない。

## 10–12. Network方式と権限

| 候補 | 現状・制約 | 採用判断 |
| --- | --- | --- |
| Windows Firewallのprogram別outbound block | system policy管理権限と全子binaryへの適用範囲が必要。ユーザーが手動利用するOfficeにも影響し得る | 管理者ではない現tokenで暗黙の昇格やsystem rule変更を行わない |
| AppContainer/Win32 app isolation | capabilityによるnetwork境界を提供する候補。Office起動、必要な読取権限、temp ACL、代替経路を含む検証が必要 | このPhaseでは未実装・未認定。Windows上で不可能だと断定しない |
| Windows Sandbox/container | feature/runtimeの用意と無network設定の認定が必要。WindowsSandbox/Docker executableは現PATHで見つからない | 未導入、未検証 |
| Job Object＋profile/preflight | process/resource境界を改善するがnetwork能力を制限しない | resource用途のみ採用 |

現在のprocessは**administrator=false**。OS全体のfirewall・network設定を変更していない。AppContainerがnetwork境界を持つことは[Microsoft一次資料](https://learn.microsoft.com/en-us/windows/win32/secauthz/appcontainer-isolation)で確認したが、現Officeでの再現可能な運用方式を実装・認定するまでには至っていない。

同じJob Object runnerで起動した安全なPython probeが`127.0.0.1`のlisten socketへ接続し、**成功した**。Job Object方式のnetwork遮断不成立を実測で確認した結果であり、outbound block成功の証拠ではない。Internet宛の試験通信は0。Office自体に外部URLを開かせていない。全候補方式の不可能性を立証したわけではなく、network isolationは明確な残課題である。

## 13–21. Resource・process・temporary filesystem

[Windows Job Objects](https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects)を採用。CREATE_SUSPENDEDで生成し、Jobへ所属させてからresumeする。所属失敗時は停止状態の自processだけを終了し、制限なしで実行しない。breakawayを許可せず、kill-on-job-close、job aggregate commit limit、同時active process上限、CPU hard cap、job CPU timeとwall timeoutを設定する。

診断policyは**1GiB commit、同時4 process、system CPUの20% hard cap、45秒wall/CPU time**。version問合せは10秒。初回の単純帳票で約530MiBのpeak commitが観測されたため512MiBを本番値とせず、約2倍の診断上限を設定した。本番resource policyは未認定。peak値はRSSではなく[Job Objectのcommit accounting](https://learn.microsoft.com/en-us/windows/win32/api/winnt/ns-winnt-jobobject_extended_limit_information)である。

- 代表8帳票×3dpi: peak約530–535MiB。
- 3ページ人工帳票: 個々の変換で約485–534MiB、全3ページ成功。
- 20,000セル人工帳票: 約530MiB、1ページ成功。セル上限を変更していない。
- 64MiBに限定した安全probeで128MiB割当がMemoryErrorになり、backendは継続。同時process=1のprobeでは追加spawnを拒否。

timeout/cancel/例外時は所属Jobのみ終了し、root handleと所属active数を待ってからtempを回収する。全sofficeの一括killなし。親service interpreterの強制終了でもOSによるhandle closeでchildが終了することを別process試験で確認した。Job Objectは別service経由の起動・悪意あるsandbox escape全般の防御を保証するものではない。PDFium rasterizationはbackend内に残り、native crash・メモリについて同じJob containmentを受けない。この点も環境認定の残課題。

global named mutexを使い、Python内の既存lockに加えて複数backend processのOffice変換を1件に制限する。競合はRENDERER_BUSY。mutex作成/取得失敗を無視しない。他processの子をkillしないことを試験した。別user/sessionでのACL運用や悪意ある同名mutex占有は本番検証未了。

tempはcurrent user/SYSTEMへACLを限定したrootにランダムUUID directoryを作り、source名は固定の短い内部名、profile/outputはrequest/pageごとに分離。owner markerとOS file leaseを保持する。normal/error/PDF/PNG失敗/例外後の回収をテスト。markerのowner/nonce、24時間経過、lease非使用を確認したstaleだけを次回render開始時に回収する。起動直後専用hookは追加していない。active directory・非所有directory・reparse pointは収集しない。HTTP切断から同期rendererへのcancel通知の接続は未実装で、runnerのcancelとPython例外処理を確認した範囲である。

## 22–28. DPI・物理誤差・threshold案

default、explicit843、width-matrix、height-matrix、margins、centering、origin、holdout-manyの8件を96/150/300dpiで測定。同じ保存source寸法と同じ共通印刷倍率を使い、DPIごとにfitしていない。各境界のraw誤差、平均/median/p95/max、pt/mm換算、終端driftをdatasetへ保存。explicit843は各DPIで再renderのpage数・寸法・PNG bytes/hashが一致。新Job Object経路の150dpi画像は前Phase画像とも一致した。

| DPI | X最大px | Y最大px | X最大mm | Y最大mm | X終端drift最大px | Y終端drift最大px |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 96 | 0.7953 | 0.7953 | 0.2104 | 0.2104 | 0.1295 | 0.2460 |
| 150 | 0.7218 | 1.1343 | 0.1222 | 0.1921 | 0.4082 | 0.7411 |
| 300 | 1.3374 | 1.7418 | 0.1132 | 0.1475 | 0.3429 | 1.1279 |

DPIを増やすと最大pixel誤差は増える一方、mm誤差は減る。raster量子化成分が存在する証拠だが、physical residualがゼロという証明ではない。残差のX/Y倍率補正は追加しない。

**本番閾値は未決定。** 診断の2pxは変更しない。検討案は`physical allowance 0.1mm + raster allowance 1px`を上限予算とし、別に検証したfield clearanceより小さいことを必須にする方式。0.1mmは本番誤差上限として証明した値ではなく、実帳票で検証すべき設計上の配分案。96/150/300dpiなら1.378/1.591/2.181pxに相当するが、採用してPASSを発行していない。今回の全fixtureは既存2pxにも収まるため、PASSさせるための閾値拡張は不要。

既存RegionCropperはregion.paddingを加え、canonical境界を検査する。paddingはreference geometryの不正確さを無条件に吸収できる保証ではない。隣接fieldとの距離、罫線太さ、stroke欠落、crop paddingの組合せの検証が必要。scan registrationの誤差は別のbudgetとして扱い、このreference閾値へ混ぜない。threshold案とdataset/analysis SHA・renderer fingerprint・geometry modelをartifactで結び付ける。

## 29–36. artifactとgate

host identity（Normal fontを含む利用条件ごと）に対するartifactを`renderer-environments/{fingerprint}/report.json`へ保存する。TemplateVersionのreference reportはartifact path/SHA/fingerprintを参照する。実測報告はdocs配下にも保存する。時間を入れず再現可能なidentityを作り、binary/font/geometry/rasterizer/OS/実装/policy変更で失効する。

ReferenceQualificationService.statusは既存のtemplate revision/config/source SHA/保存plan SHA/reference SHA/診断画像SHA検査に環境artifact SHA・identity linkage・current fingerprintの照合を追加した。保存environmentQualifiedやqualified booleanを信用せず、未解決policyを現実装から再計算する。geometry PASSとenvironment PASSとtemplate整合を分けて扱う。

JobPageServiceの受付はこのstatusを検証し、引き続き無条件に本番受付を拒否する。ACTIVEも既存の新paged設定の拒否境界を保持した。**環境認定後の受付成功・ACTIVE成功経路をこのPhaseで開いてはいない。** synthetic DRAFT→ACTIVE→受付→mock OCR E2Eは、環境認定が条件なので未実施。mockでenvironmentを認定済みに偽装して完了扱いしていない。

最終`environmentQualified=false / productionQualified=false`。実ユーザー5冊の高度要素・セル上限に関する既存gateは維持。unsupported workbookを今回rendererへ通していない。

## 37–44. 変更と検証

変更は新reference rendererへ限定。既存legacy preview、provider/model/prompt/schema/association、registrationのX/Y補正、canonical rectangles、補正/確定、XLSX/Sheets出力、Drive/Pickerは変更していない。

- 追加: `renderer_process.py`、`renderer_temp.py`、`renderer_environment.py`、環境unit test、process/cleanup integration tests、DPI/resource probe scripts、本報告・evidence。
- 更新: `office_reference_renderer.py`、`reference_qualification_service.py`、
  `tests/unit/test_reference_qualification.py`（旧timeout mockを実Job Object終了検証へ移行）、
  関連報告への今回Phase参照。
- DB schema/migration、frontendソースの変更なし。

変更前後の契約・同じ人工画像・field geometry回帰と既存snapshot境界の検証を根拠に、**既存OCR contractを維持した**。認識文字列の実API比較・実帳票Golden replay全体・UI E2Eを実施した意味ではない。OpenAI APIは今回の禁止に従い**0回**、Google APIも**0回**。

検証結果の確定値は末尾に記載する。mypyは新規違反と既存エラーを区別し、既存testの削除・skip追加・assertion弱体化は行っていない。

| 検証 | 確定結果 |
| --- | --- |
| 変更前OCR contract script | 194 passed、45.66秒 |
| 変更前print layout / qualification / geometry / job pages | 112 passed、167.11秒 |
| 変更後OCR contract script | 194 passed、47.06秒 |
| 変更後backend full（最終） | **684 passed, 1 skipped**、281.37秒。既存warning 3件 |
| 新規security/environment対象 | 21 passed |
| timeoutテスト移行後のreference/security対象 | 34 passed |
| reference qualification API対象 | 4 passed。最終fullでも再実行 |
| frontend対象4ファイル | **28 passed** |
| frontend build | 成功、既存bundle size warningあり |
| Ruff、今回の12 Pythonファイル | 新規0、成功 |
| mypy app全体 | 36 errors / 6 files（既存）、今回の新規0 |
| git diff --check、新規コードのwhitespace検査 | 成功 |

最初のfullは683 passed / 1 failed / 1 skipped。失敗は旧Popenへの直接mockだった。
既存timeoutのRENDER_FAILEDコード、temp回収、lock解放を保持し、PID限定kill命令のmock検査を
実Job Object内のprocessが終了する検査に置き換えて再実行した。最終fullは失敗0。

再現コマンド（backendでは`.venv/Scripts/python.exe`）:

- root: `backend/.venv/Scripts/python.exe scripts/verify_ocr_contract.py`
- backend: `python -m pytest -q -o addopts='' -p no:cacheprovider --basetemp=<未使用path>`
- backend: `python scripts/qualify_renderer_environment.py`、`python scripts/probe_renderer_resources.py`、`python scripts/finalize_renderer_environment.py`
- frontend: `npm test -- PrintLayoutEditor.test.tsx ReferenceQualification.test.tsx OcrPageBindings.test.tsx OcrPage.test.tsx`、`npm run build`

schema変更・既存job snapshot変更なし。環境artifact fingerprintは
`62833b7c0e02e6ea6e7bb145ba6e1a3425987b6078fe0e3aae40ebd83f3dfd51`。
Windows build/UBRは`26200.9168`、DisplayVersionは`25H2`。

## 45–47. 未解決・次Phase・Git

次Phaseの優先は(1) AppContainer等によるnetwork境界とOffice互換性の実証、(2) 実選択font/全load dependencyの同定、(3) PDFiumの別process収容、(4) 実帳票・crop clearanceによる閾値とresource値の認定。OS全方式が不可能と結論したわけではない。今回の結果は「安全な本番環境として認定できなかった」である。

開始時の未コミット差分を保持し、staging/commit/push/reset/revertなし。最終statusと今回変更ファイルの照合結果をevidenceへ保存する。

終了時もHEADは同一。tracked変更18ファイル、untracked43項目。
[git-status.txt](reference-renderer-evidence/environment/git-status.txt)と
[change-audit.json](reference-renderer-evidence/environment/change-audit.json)を保存した。
開始時ファイルの欠落0、実帳票5冊のSHAは全件一致。今回の検証temp 9個だけを
`C:/Users/hanam/.codex/tmp/llm-ocr-environment-20260908`へ移動し、以前から存在する検証tempは保持した。
終了確認時にsoffice残留processなし。

## DPI全境界分布の補足

| DPI | 軸 | px mean | px median | px p95 | px max | pt mean | pt p95 | pt max | mm mean | mm p95 | mm max |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 96 | x | 0.5413 | 0.5255 | 0.7092 | 0.7953 | 0.4060 | 0.5319 | 0.5965 | 0.1432 | 0.1876 | 0.2104 |
| 96 | y | 0.6416 | 0.5984 | 0.7953 | 0.7953 | 0.4812 | 0.5965 | 0.5965 | 0.1698 | 0.2104 | 0.2104 |
| 150 | x | 0.4475 | 0.4647 | 0.6623 | 0.7218 | 0.2148 | 0.3179 | 0.3465 | 0.0758 | 0.1121 | 0.1222 |
| 150 | y | 0.7697 | 0.7467 | 1.0551 | 1.1343 | 0.3695 | 0.5065 | 0.5445 | 0.1303 | 0.1787 | 0.1921 |
| 300 | x | 0.7113 | 0.6281 | 1.2050 | 1.3374 | 0.1707 | 0.2892 | 0.3210 | 0.0602 | 0.1020 | 0.1132 |
| 300 | y | 1.0612 | 1.0903 | 1.5321 | 1.7418 | 0.2547 | 0.3677 | 0.4180 | 0.0899 | 0.1297 | 0.1475 |
