# WSL変換用runtime image

印刷用PDFや参照画像を作るWSLの変換環境（LibreOffice・Python・fontなどのuserspace）を、
内容SHAで固定した読み取り専用imageにまとめ、変換時に明示選択する仕組みの仕様。
runtimeを固定する経路であり、本番資格の発行や有効化を兼ねるものではない。

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
wsl.exe -d Ubuntu-22.04 -u root --exec /usr/bin/unshare --mount --propagation private /usr/bin/python3 -I <WSLから見たリポジトリの絶対パス>/backend/scripts/build_wsl_renderer_runtime.py
```

scriptは信頼された絶対パスで指定する。

## 変換時の明示選択

`renderer_wsl.render_wsl_pdf` / `import_wsl_geometry` / `render_wsl_reference`は
任意の`runtime_id`（64桁lowercase SHA）を受け取る。未指定時は従来のWSL経路を維持する。
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

provider/model/prompt/association、mapping/crop、原本と過去jobを変更しない。
UI・保存planがこのimageを自動選択する設定はない。
印刷確定で使う場合は設定`PRINT_WSL_RUNTIME_ID`で指定する（[固定WSL環境を使う印刷確定](pinned-print-preparation.md)）。
未指定の既存動作と過去版を維持し、作成したimageを自動選択・有効化するものではない。

## 作成時の注意

- WSL kernelで`CONFIG_SQUASHFS_ZSTD`が無効な場合はZstandardのimageをmountできない。そのためgzipで作る。
- WSL側のPythonが3.10の場合に備え、SHA-256は`hashlib.file_digest`を使わずstreamで計算する。
- Windowsではreadonly hard linkの一時名削除が拒否されるため、exclusive link・一時名削除・完成file readonly化の順で公開する。

## 固定していないもの

固定したのはimage内userspaceであり、host側WSL kernel、systemd、mount/nsenter/bwrap、
broker/workerのコード、Windows fontsまで全て固定したわけではない。
imageのSHA一致や描画一致だけで完全なinstallation closure / font / cropを認定しない。
root権限を持つホスト管理者による同時改変全般の防御を証明したものでもない。

正式な資格経路では、これらのidentity・変更時の失効、glyph/文字対応、geometry/crop証拠を
別に結び付ける（[WSL印刷確定版の参照検証](prepared-reference-qualification.md)）。
このimageを旧planや過去jobへ後付けせず、印刷成果物の取得契約も変えない。

固定runtimeを明示した変換では、Windows font directoryの全regular file（隠しfileを含む）の名前・bytes・内容SHAの集合を
変換の前後で照合する（`fontDirectoryIdentity`）。fontはcopyせず、読み取り専用mountのまま内容identityへ結び付ける。
