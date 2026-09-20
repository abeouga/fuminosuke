# WSL起動側の依存観測

固定image/worker照合とWSL参照検証の変更後full実行中に、製品コードを変更せず観測した。
目的はhost側Python・起動プログラム等の認定対象を特定することであり、正式資格の発行ではない。

## 読み込み直後の観測

workerをmain実行せずに読み込み、Pythonの `__file__` と `__cached__`、
ホストPythonと稼働中systemdの `/proc/*/maps`、固定9起動プログラムの解決済みELF依存を確認した。
bytecode cacheの新規書込みは無効化した。repo workerの内容は前後不変だった。

- Python 3.10.12、kernel `6.6.87.2-microsoft-standard-WSL2`。
- Python moduleに関連するファイル147件、maps上のruntime資源37件。
  これらは重複を含むカテゴリで、単純合計ではない。
- 起動プログラム/loader cacheも含む重複除外後187件、28,243,205 bytes。
- `.py` 72件と `.pyc` 70件を含む。mapped資源にはlocale/cacheもあり、全てがnative codeではない。
- 集合の内容SHA: `17402d5c140d563acbb6200d6f0da5e132becd134752710f3a1afad34e5cc136`。
  全ファイルを2回読み直して一致し、各読取前後のinode/size/mtime/ctimeも一致した。

最初の診断は非empty条件で停止した。原因は標準libraryの
`/usr/lib/python3.10/urllib/__init__.py` が正当に0 bytesだったためである。
診断scriptは空ファイルも集合に含めてSHAを計算するように修正した。
ファイルを除外したり、製品の検証を弱めたりして通過させたものではない。

個別のpath/size/SHAと解決済み依存は
[観測JSON](wsl-bootstrap-observation-20260913.json)に保存した。
これは観測したfile集合のhashであり、起動条件全体を網羅するfingerprintではない。

## 実変換前後の観測

repo外のworkerコピーに起動前・終了時の観測を追加し、人工2ページのreference変換を実行した。
起動側はこの診断workerのsnapshot SHAを照合し、既存の固定runtime・cgroup・network隔離を使用した。
診断workerは本番workerと異なるSHAであり、本番workerの正式認定済み実行を装っていない。

- 元worker SHA: `4ef76e57fe0002aa44d4479597dfdfc8ba6952a7174599c98c0fb1e8620264e7`。
- 診断worker SHA: `fd18115739781d1a56daa1182a7daf1a84a11e96a29bd589ab36a4bae6bec2a7`。
- runtime SHA: `1465eef285317a981d1efa98faf9d6dce267e1fe446e29a7f48c65538a45d65d`。
- 実行前159件、実行後159件。追加・削除・共通ファイルの内容変更はいずれも0。
- 159件すべてが先行187件の集合に含まれ、対応するSHAも一致した。
- 2ページPNGのSHAは先行の同fixtureと一致した。
  `aac18dc26106f6f698f36a87e67b200aabac23ecea781519ae9842db0029c63d` /
  `94ac84c0fe6fd6c2b80869bef34dfbfd6aee75e2b8a32b32863869f82c824d0f`。

詳細は[実行観測JSON](wsl-bootstrap-execution-20260913.json)。
元workerとユーザー原本/実DB/既存成果物は変更していない。
この前後観測は途中だけ読み込まれて解除された資源の連続traceではなく、
全形式・全異常系の依存closureの証明でもない。

## Windows側の現在値

`Get-Command wsl.exe` は `C:/Windows/System32/wsl.exe` を返した。
WSLServiceはRunningで、登録された実体は `C:/Program Files/WSL/wslservice.exe` だった。
実行中serviceのprocess Pathは取得できなかったため、サービス登録pathとファイル内容を確認した。

- `wsl --version`: WSL 2.6.3.0、kernel package 6.6.87.2-1。
- Windows registry: CurrentBuildNumber 26200、UBR 9445、DisplayVersion 25H2。
  `wsl --version` のWindows表示も10.0.26200.9445で一致した。
- wsl.exe: 274,432 bytes、file version 10.0.26100.8875、
  SHA `27cc8dd52be326e138a89f8889241b1d8c51dd1978b22eb70be77036ccdee3c2`。
- wslservice.exe: 5,598,752 bytes、file version 2.6.3.0、
  SHA `2813b218d439a958b2c86934e5c80e86013fa4c296199381d5d7530eed6d0ed5`。

旧 `renderer-environment-qualification.md` のWindows 26200.9168とは異なる。
旧環境artifactを現在の証拠として流用できないが、この差から更新が今回の試験中に起きたとは推論しない。
WSLのWindows側全依存や設定を確認済みとは扱わない。

## 残条件・再現

systemd manager設定、起動時環境、実行中の一時的なdynamic load、Windows/WSL側の
完全な依存と変更時失効を、今回の集合だけで代替しない。
正式な環境発行経路には、これらと現在の隔離/資源制限の証拠を結び付ける必要がある。
glyph/文字配置と実手書きcropの資格も別条件として残る。

repo外の診断script:

- `C:/Users/hanam/.codex/tmp/paged-vector-inspection-20260913/probe_wsl_bootstrap.py`
  をWSLの `/usr/bin/python3 -I -B` で実行。終了コード0。
- 同directoryの `probe_wsl_executed_bootstrap.py` をbackend venv Pythonで実行。終了コード0。

OpenAI/Google呼出し0。製品コード・設定・依存パッケージの変更0。

## systemd設定と管理プロセスの追加観測（2026-09-14）

変更後の実参照検証が終了した後、repo外の `probe_wsl_manager_identity.py` を
`/usr/bin/python3 -I -S -B` で実行した。終了コード0、2回の観測が一致した。
設定ファイルを変更せず、設定内容・環境変数の値は出力していない。

- 稼働manager: `/usr/lib/systemd/systemd`、Version `249.11-0ubuntu3.17`。
- `systemd-analyze cat-config` の選択元は `/etc/systemd/system.conf` のみ。
  1993 bytes、root所有0644、SHA `3365b281b5bad284654a5c97d7233683b16677e51a02c3073d954f447872aa16`。
- `/etc`、`/run`、`/usr/local/lib`、`/usr/lib` のsystemd main/drop-in候補を列挙。
  選択元以外のmain設定と追加drop-inはこの観測では存在しなかった。
- 合成設定のSHA: `fde043e1a862c7075e60ddbfde3a7c4168fa791bb1c3f3270eaade6844af5c6c`。
- 実効manager設定: start/stop timeout各90秒、DefaultTasksMax 2270、
  DefaultLimitNOFILE 1048576、NPROC 7569、CORE infinity、CPU/Memory/Tasks accounting有効。
  これはmanagerのdefaultであり、rendererのtransient unitが明示する制限と混同しない。
  実workerのcore dump可否をこの値だけで断定しない。
- `/etc/wsl.conf` は20 bytes、`/etc/ld.so.conf` は34 bytes、`/etc/ld.so.preload` は不在。
  manager環境値はSHAだけを取得し、変数名・値を記録へ転記しなかった。

この観測は設定の選択元・現在の一部実効値を識別するためのもの。
変換中の読取trace、全manager設定、完全な起動依存closureや正式資格を証明していない。
診断scriptは前述のrepo外directoryに追加した。製品コード・原本・実DB・環境設定の変更0。

## Python検索経路の全ファイル観測（2026-09-14、後続）

同一backend full processが稼働している間、別のOffice/native rendererは起動せず、
host Pythonを `-I -S -B` で起動してread-onlyの検索経路inventoryを観測した。
前の「import済みmoduleだけ」の集合より広く、stdlibの未importファイルも対象にする。

- Python 3.10.12、実検索経路は `/usr/lib/python310.zip`、`/usr/lib/python3.10`、
  `/usr/lib/python3.10/lib-dynload`。isolated/no_site/dont_write_bytecodeを実値で確認。
- `/usr/lib/python310.zip`、`/usr/bin/pyvenv.cfg`、`/usr/pyvenv.cfg`は不在として記録。
- Python executableとstdlib treeのregular fileは1357件、27,836,224 bytes。
  各ファイルはno-follow descriptorで前後stat・読取bytes・内容SHAを照合し、
  開始/終了のentry集合および2回の全snapshotが一致した。
- 集合SHA: `e1b9279b38730c7b8479237a7bad93825aa0138e8306562d21babff6c7c46e49`。
- 観測entryに非root所有やgroup/other書込可能な通常ファイル・directoryはなかった。
  symlinkはlink自体の所有者とtargetを記録し、0777を通常ファイルの書込許可と混同しない。
- 4本のsymlinkのうち、`config-3.10-x86_64-linux-gnu/libpython3.10.so` と
  `sitecustomize.py` は検索tree外を指す。この観測はそれらのtarget内容SHAを含まない。
  `-S`で通常site起動は無効だが、外部targetやELF共有依存の検証を済ませたとは扱わない。

repo外script `probe_wsl_python_tree.py` を追加。最初の全record出力はtool表示で切り詰められたため、
同じscriptを再実行し、stdoutをprocess内でJSONとして検証後、summaryのみを出力した。
再実行は終了コード0。サイズ/時刻一致だけで内容SHAを再利用しない。
検索treeの全ファイル集合は再実行可能だが、今回は資格artifactとして製品へ保存していない。

これはbootstrap Pythonのコード検索面の観測であり、実行中の一時的なnative load、
systemd全設定、Windows/WSL全依存や変換前後の製品enforcementの証明ではない。
正式な環境issuer・本番gateは変更していない。OpenAI/Google 0、原本/実DB変更0。

### 検索tree外とELF依存の追加観測

上記1357ファイルの次に、47個のPython lib-dynload拡張、libpythonへのconfig link、
Python/隔離起動tool/systemd/bashの10実体、計58 rootに対して、root-ownedの実ファイルを
確認してからlddを実行した。帳票・未信頼入力へのldd実行やOffice起動はない。
ld.so.cache/config/drop-in、ldd script、検索tree外sitecustomizeと解決先libpythonも内容hashした。

- 対象112ファイル、41,888,891 bytes。resolved pathでの重複なし。
  これは実行ファイル・module・共有ライブラリ・設定の合計であって、112個すべてがlibraryではない。
- 全内容hashとdependency edgeを含む2回のsnapshotが一致。集合SHAは
  `70b84a57f107942fb40d10404a4126f99518b10220cbde5bd2af92c30b484fbd`。
- 9 rootはlddが`statically linked`と報告し、外部dependency edgeなしとして明示記録。
  最初の診断はこれを想定せず停止した。エラーを隠さず、この出力型を分けて再実行した。
  実行時dlopen・interpreterのsymbol供給・全設定を解決したという意味ではない。
- `/usr/lib/x86_64-linux-gnu/libpython3.10.so.1.0`: 5,834,688 bytes、
  SHA `d7bc028c6a98ac8c17edeada13609ea36db324d7f49c26b6ca93994e8c830453`。
- `/etc/python3.10/sitecustomize.py`: 155 bytes、
  SHA `43d81125d92376b1a69d53a71126a041cc9a18d8080e92dea0a2ae23be138b1e`。
  内容や環境変数値は報告へ転記していない。

[全record/依存graph](wsl-python-native-dependencies-20260914.json)のfile SHAは
`398ebc3bd0744af45fcae19fef851797b9e306845f9559a4a4a7b1e1f04adfab`。
repo外script `probe_wsl_python_native_dependencies.py`、最終実行は終了コード0。
backend fullは同じsessionで継続中。製品の起動前後enforcement・全closure・正式資格発行は未実装。
