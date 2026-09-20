# WSL変換用runtime image

2026-09-13。明示的な本番化・WSL専用変換環境導入の要求に対する追加実装。
これはruntimeを固定する経路であり、本番資格の発行や有効化の完了ではない。

## 作成と利用境界

`backend/scripts/build_wsl_renderer_runtime.py`はWSLの`/usr`と
`/etc/fonts`、`/etc/libreoffice`を専用Squashfsへ収録する。
Squashfsは[Linuxの読み取り専用filesystem](https://docs.kernel.org/filesystems/squashfs.html)である。
Windows文書・Windows fonts・ユーザーhome・backend DBを収録しない。
`/usr`は非再帰bindの内容であり、ホストの全てのネストしたmountを複製するものではない。
bin/sbin/lib/lib64の既存aliasを検証して同じaliasを作る。

作成はroot-ownedの`/opt/llmocr-renderer-runtimes`配下の固有build directoryだけで行う。
祖先のsymlink、非root所有、group/world writableを拒否する。
他processへmountが伝播しないprivate namespaceを要求し、元`/usr`を変更しない。
gzip圧縮、mksquashfsの2 processors / 256MiB buffer指定で作成する。
作成失敗時はbuildを保持し、mountが残り得るdirectoryを再帰削除しない。

完成imageは内容SHAを名前にし、exclusive hard linkを使って既存imageを上書きせず保存する。
その後に元の一時名を除き、完成imageを0444とする。既存imageの自動選択・切替はしない。

```powershell
wsl.exe -d Ubuntu-22.04 -u root --exec /usr/bin/unshare --mount --propagation private /usr/bin/python3 -I /mnt/c/Users/hanam/Desktop/llm-ocr/backend/scripts/build_wsl_renderer_runtime.py
```

上記はこの開発hostでの実行例。別配置では信頼されたscript絶対パスへ読み替える。

## 変換時の明示選択

`renderer_wsl.render_wsl_pdf` / `import_wsl_geometry` / `render_wsl_reference`に
任意の`runtime_id`（64桁lowercase SHA）を追加した。未指定時は従来のWSL経路を維持する。
指定がある場合はprivate mount namespaceでbrokerを起動する。pathや大文字・余分な文字は拒否する。

- brokerはroot-owned固定store内の通常fileを`O_NOFOLLOW`で開く。
- fileの所有者、write bitなし、hard link数1、8GiB以下を検査する。
- 開いた同じdescriptorの内容を毎回SHA検査し、そのinodeをloop/ro/nodev/nosuidでmountする。
  サイズ・時刻のみのcacheを使わない。mountには`--no-canonicalize`を指定する。
- systemdはbrokerと別namespaceでunitを開始するため、unit内で生存するbroker自身のmount
  namespaceだけへnsenterし、その後に従来のbubblewrap隔離へ入る。cgroup membershipは移さない。
- Office/Python/Popplerと設定の6つの既存mount元をimage内へ置換する。
  Windows fontsは引き続き固定readonly mountであり、imageへコピーしない。
- childは6 mountが全てread-only Squashfsであることを確認する。uid、capability、network、
  memory/swap/pids/cpuの従来検査・制限も維持する。
- 成功終了後にも同じdescriptorを再hashする。終了後に所有mountだけをunmountする。
  unmount失敗時はdirectoryを保持し、再帰削除しない。
- reportにはruntime image policy/SHA/filesystem/read-onlyとkernel versionを追加し、
  Windows bridgeは選択したSHA・policy・read-onlyとの一致を要求する。
- 欠落/変更/危険な権限/mount失敗時は拒否し、live runtimeへfallbackしない。

既存の1GiB memory / swap 0 / tasks 64 / CPUQuota 100% / unit 60秒 / owner leaseを維持する。
provider/model/prompt/association、mapping/crop、原本と過去jobを変更しない。
UI・保存planがこのimageを自動選択する設定は追加していない。
後続で任意の`PRINT_WSL_RUNTIME_ID`を[印刷確定へ接続](pinned-print-preparation.md)した。
未指定の既存動作と過去版を維持し、作成したimageを自動選択・有効化するものではない。

## 作成された実環境と比較

- image: `/opt/llmocr-renderer-runtimes/1465eef285317a981d1efa98faf9d6dce267e1fe446e29a7f48c65538a45d65d.squashfs`
- SHA: `1465eef285317a981d1efa98faf9d6dce267e1fe446e29a7f48c65538a45d65d`
- 大きさ: 611,487,744 bytes、gzip、policy `wsl-squashfs-runtime-v1`。
- kernel: `6.6.87.2-microsoft-standard-WSL2`。
- image内Office: `LibreOffice 7.3.7.2 30(Build:2)`。
- image内Pythonでuid 65534、capability 0、no-new-privileges、readonly filesystem、
  network interfaceがloのみ、ホスト/mnt/cなしを確認した。
- 実DRAFT `6d2187c0-ac7f-4d0c-90c8-a0fc381c2cc8`の同じprint.xlsxを固定imageで再変換。
  全4 PNGが保存artifact・DB reference SHA・旧reportとbyte一致した。
  保存済みXLSX/PDF/PNG/report、版quality_jsonの前後は一致し、実DBへの書込みなし。

実比較scriptはrepo外の
`C:/Users/hanam/.codex/tmp/paged-vector-inspection-20260913/compare_font_render.py`へ
上記runtime SHAを引数に渡した。無指定の従来比較も維持している。

途中の失敗を区別する。

1. 初回Zstandard imageは作成できたが、WSL kernelの`CONFIG_SQUASHFS_ZSTD`が未設定で
   mountできなかった。gzipが有効であることを確認し方式を変更した。kernel設定は変更していない。
2. gzip imageの起動probe成功後、Ubuntu Python 3.10にない`hashlib.file_digest`で失敗した。
   同じSHA-256をstreamで計算する実装へ修正し、Python 3.10指定のmypyも実行した。
3. Windows単体試験ではreadonly hard linkの一時名削除が拒否されたため、exclusive link・
   一時名削除・完成file readonly化の順へ修正した。既存fileを上書きしないassertionは保持した。

失敗buildのimage/logと成功buildのlogは固定store内の各build directoryに保持している。
原本や既存テンプレート成果物を削除していない。

## 検証

- 変更前OCR contract: 210 passed / 1 warning（54.85秒）。
- runtime単体・font境界・実WSL（新固定経路2件を含む）: 52 passed（48.52秒）。
- builder単体8件はunsafe ancestor / dangling symlink / 排他的公開 / probe bindingを検査。
- runtime selectionは不正selector、同metadata内容変更の再hash、binding、report改変を検査。
- 実固定経路は人工2ページで従来と全PNG一致・隔離/資源条件維持、存在しないimageの拒否を確認。
- Ruff対象成功。Windows bridge限定mypy成功。
  worker/builderは`--platform linux --python-version 3.10`で成功。
- 固定runtimeのowner切断を追加した実統合セット: 3 passed（19.83秒）。
  EOFで所有unitが停止し、出力PDFを返さず、systemdのinactive/failed/unknown状態を確認。
- 変更後OCR contract: 210 passed / 1 warning（62.80秒）。
  前後とも`backend/.venv/Scripts/python.exe scripts/verify_ocr_contract.py`。
- 完成imageは`444 root 1 611487744`（mode/owner/link数/bytes）。
  終了後の`losetup -l`は空で、loop mountの残留を認めなかった。
- `git diff --check`成功。既存OCR contractを維持した。実OpenAI/Google呼出し0回。
- backend全体を`pytest tests -q -o addopts='' -p no:cacheprovider
  --basetemp=.pt-wsl-frozen-full-0913`で開始した。完走結果は後続記録とし、開始を成功扱いしない。

同一processの完走結果は1073 passed / 1 skipped / 3 warnings（1523.43秒）。
終了コード0。過去のWindows子process終了試験もこの実行では通過したが、
単一の成功を以前に観測した終了タイミング問題の修正証明とはしない。

## 本番化の残条件

固定したのはimage内userspaceであり、host側WSL kernel、systemd、mount/nsenter/bwrap、
broker/workerのコード、Windows fontsまで全て固定したわけではない。
imageのSHA一致や描画一致だけで完全なinstallation closure / font / cropを認定しない。
root権限を持つホスト管理者による同時改変全般の防御を証明したものでもない。

正式な資格経路にはこれらのidentity・変更時の失効、glyph/文字対応、geometry/crop証拠を
結び付ける必要がある。ACTIVE・paged OCR受付gateは引き続き閉じている。
このimageを旧planや過去jobへ後付けせず、印刷成果物の取得契約も変更しない。

## 起動側とfont集合の追加調査

全体回帰試験の同一processが稼働中の間、製品コードを変更せずread-onlyで調査した。
現在のhost側bwrap/mount/umount/unshare/nsenter/systemd-run/systemctl/Python/systemdと
ld.so.cacheのSHAを取得した。動的linkの一覧からもlibc・libmount・libselinux・libcap等への
依存を確認し、実行ファイル本体のSHAだけを完全なbootstrap identityと扱わない。
Pythonの動的import、systemd設定、WSL/Windows側の起動資源はこの一覧だけでは網羅されない。
後続の[起動側依存観測](verification/wsl-bootstrap-observation-20260913.md)では、
Python source/cache・mapped runtime資源・起動ELF依存を含む187件を観測し、
実reference変換前後の159件も比較した。全依存closure/正式認定の代替ではない。

Windows font directoryは通常のPowerShell一覧では576 files / 645,773,985 bytesだったが、
隠し属性の`fms_metadata.xml`と`StaticCache.dat`も存在する。
全regular fileを列挙して2回読み直すと、578 files / 665,977,778 bytesとなった。
名前・bytes・内容SHAのcanonical JSON集合は両回一致し、集合SHAは
`47071a85470e130419c56f116d4294978ba536e78f30ac164f860f5a32786571`。
所要時間は2.375秒/2.438秒（全体回帰との同時実行、warm cacheを含み得る診断値）。
各fileは64MiB以下、symlinkなし。読取前後のsize/mtime/ctimeと終了時のfile集合も照合した。

これは読み取り専用mountのfont集合を、copyなしで内容identityへ結び付ける方式の測定である。
候補に出たfamily/fileだけではなく、追加fontや隠しfileも含む集合変化を検出する必要がある。
同時の敵対的書換え全般、実Officeが使用した文字/glyph、性能上限、本番認定の証明ではない。
この調査時点では製品の毎変換前後チェックへは組み込んでいなかった。
後続で[font集合の前後照合](verification/wsl-font-directory-20260913.md)を、
固定runtimeを明示したWindows font経路に限定して追加した。
診断scriptはrepo外
`C:/Users/hanam/.codex/tmp/paged-vector-inspection-20260913/probe_font_manifest.py`。
font copy 0、実DB/原本変更0、OpenAI/Google呼出し0。

## glyph解析用の追加環境

2026-09-13に[専用の固定依存環境](verification/wsl-glyph-dependencies-20260913.md)を追加し、
別SHAの新imageを作成した。既存image・実DRAFTのruntime選択は維持する。
新image内の非特権・readonly・ネットワーク分離import probeは成功したが、
PDF字形検証の製品経路への接続や本番資格発行は未完了である。
host Pythonも更新されたため、旧bootstrap観測を現在のidentityとして流用しない。
