# 固定WSL runtimeのfont集合identity

本番化要求の範囲で、明示的な`runtime_id`とWindows fontsを使用する呼出しだけに
`wsl-font-directory-v1`を追加した。既存のlive runtime/保存reportを暗黙更新しない。

## 変更境界

`renderer_wsl_worker.py`はreadonlyの`/reference-fonts`にある全regular fileを列挙し、
名前・bytes・内容SHAのsorted canonical JSONをhashする。hidden属性や非font metadataも含む。
候補に出たfontだけをhashする方式では、追加fontによる解決先変化を検出できないためである。

- 4096 files、各64MiB、全体1GiBを上限とする。空集合、directory、symlink、特殊fileを拒否。
- Linuxではno-follow/nonblocking descriptorを使用し、同じdescriptorから1MiBずつ読む。
- size/mtimeが同一でも内容を毎回読む。inode・mode・size・mtime/ctimeの前後照合も行う。
- Windows単体試験でlstat/fstatのctime差を実測したため、ctimeは取得APIごとの前後比較とする。
  Linuxではpathとdescriptor間のctime一致も要求し、WSLの検査を弱めていない。
- 最終file集合・metadataを再列挙し、描画/raster完了後にも全内容を再hashする。
- 集合が変わった場合は成功reportを書かない。font copy・配布・設定更新は行わない。
- Windows bridgeは新reportのpolicy/SHA/count/bytesの型・上限・存在を要求する。

報告は`fontDirectoryIdentity`にpolicy、fileCount、totalBytes、sha256を保存する。
fontの候補face情報とは別の証拠であり、実際に描画した全文字/glyphの照合を代替しない。
非隔離fallback、cgroupの時間/資源上限拡大、ACTIVE/受付gateの変更は行っていない。

## 検証

直前の固定runtime実装全体回帰は1073 passed / 1 skipped / 3 warnings（1523.43秒）。
これは本font集合変更より前の結果であり、変更後fullの代替とはしない。

- 新規13件は未実装時にAttributeErrorで失敗し、実装後に通過した。
- Windowsのctime差で初回2件が失敗した。取得API別の前後照合に修正後、既存font/runtime/bridgeと
  合わせて55件通過（0.36秒）。後続で全file hash後の集合追加拒否も追加した。
  最終対象セットは56 passed（0.32秒）。
- 実WSL固定/live経路: 13 passed（61.52秒）。固定経路の全font集合が独立したWindows側読取と
  一致し、既存live経路には新metadataが付かない。PNG一致・資源制限・owner切断拒否も維持。
- 変更後OCR contract: `backend/.venv/Scripts/python.exe scripts/verify_ocr_contract.py`、
  210 passed / 1 warning（40.38秒）。変更前の契約検証はruntime段階の210 passedを参照。
- 対象Ruff、Windows bridge mypy、Linux workerのPython 3.10指定mypyは成功。
- 変更後fullは`pytest tests -q -o addopts='' -p no:cacheprovider
  --basetemp=.pt-wsl-font-directory-full-0913`で実行する。完走結果は別途記録し、
  直前の1073件通過を今回の変更後結果として流用しない。

後続の同一process完走結果: 1097 passed / 1 skipped / 3 warnings（1521.18秒）、終了コード0。
これはfont集合実装後の全体結果である。実ブラウザ・本番OCR・有効化の証明とは区別する。

実DRAFTの同一print.xlsxを固定runtimeで再変換し、全4ページのPNGが保存artifact、旧report、
DB reference SHAと一致した。原本/保存XLSX/PDF/PNG/report/quality_jsonの変更はない。
今回のfont集合は578 files / 665,977,778 bytes、SHAは
`47071a85470e130419c56f116d4294978ba536e78f30ac164f860f5a32786571`。
runtime imageは`1465eef285317a981d1efa98faf9d6dce267e1fe446e29a7f48c65538a45d65d`を維持した。
実OpenAI/Google呼出し0。API予算/認識/association/mapping/cropは変更していない。

## 限界

集合SHAは取得時の内容identityであり、正式な環境資格への接続は未完了。
管理者による同時改変と復元を含む敵対的な全操作の防御証明ではない。
host bootstrap/worker identity、文字対応、実手書きcropの認定は別に必要である。
実テンプレートはDRAFTのままで、本番有効化完了とは扱わない。
