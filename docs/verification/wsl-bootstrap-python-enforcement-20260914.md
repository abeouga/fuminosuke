# WSL bootstrap Pythonの内容照合

## 原因と変更境界

従来のroot bootstrapは `-I -S -B` を指定していたが、host Pythonの検索treeが変換の
前後で同一だったことを製品では確認していなかった。過去の別実行による調査記録を、
現在の変換の環境証拠として流用することはできない。

`renderer_wsl_worker.bootstrap_python_identity` を追加し、固定runtimeのOffice brokerと
glyph/vector brokerへ前後照合を接続した。Python以外のloader依存やsystemd、OS条件は別の残条件である。
この変更で環境資格やACTIVE/受付gateを開放しない。

- 実検索経路、Python 3.10系列、uid 0、isolated/no-site/no-bytecodeの実値を要求する。
  glyph/vectorの所有code archiveだけを検索経路の先頭として区別する。code自体は既存のbundle SHAで束縛する。
- Python executableとstdlib tree、検索ZIPとpyvenv.cfgの存在/不在を対象とする。
- root所有・祖先directoryの非書込条件を要求する。通常fileはno-follow descriptorで内容をhashし、
  前後statと読取byte数を照合する。集合変更、循環/installation外/未対応directory/dangling linkを拒否する。
- file linkはlink先の内容も含める。既に記録した不在entryへのlinkも成功にしない。
- 20000 entry、個別128MiB、合計512MiBまで。size/mtimeだけでSHAを再利用しない。
- 新応答の `bootstrapPythonIdentity` はpolicy、検索経路、version、flags、件数/bytes、集合SHAを持つ。
  開始/終了の不一致では成果物を返さない。Windows bridgeも新規の固定環境応答で証拠を要求する。
- `print_preparation_service` は寸法測定とPDF生成のbootstrap/core guardの一致を保存前に確認する。
  `prepared_reference_evidence` は保存graph・再描画でもこれらの対応を維持する。
  旧reportを現在の証拠で埋めず、旧単一ページOCR・意味づけ・mapping・原本へ移行しない。

## 実環境の観測

同じ製品collectorを無入力のhost Pythonで2回実行し、一致を確認した（終了コード0）。
Python 3.10.12、1454 entry / 1359 regular file / 33,671,067 bytes。
集合SHAは `3953cf9f116b2027b4cfc15212c8ec2844cf61f8caf3fe66b2c8f22b9eb28a2e`。
以前のstdlib-only観測で範囲外だったsitecustomizeとlibpythonのlink先も今回のhash対象に含む。
この集計にはPython入力を含み、1359件すべてがnative libraryという意味ではない。

## 検証

- 関連unit/bridge/保存glyph・geometry: **283 passed（2.13秒）**。
- 内容変更・link・所有者・書込権限・集合・検索経路の9条件を含む実WSL読取検証、
  live/fixed Officeページ画像比較、実glyph検証: **3 passed（67.86秒）**。
  失敗注入はPython callbackの置換のみ。hostのfileや権限を変更せず、元の全内容SHAへ戻ることも確認した。
- 保存前の欠落/不一致とDB/layout/artifact非更新、旧report非補完、新しい再描画証拠の欠落/改変:
  **216 passed（11.84秒）**。
- 対象Ruff成功。Linux対象worker 2ファイルとWindows側service 4ファイルのMypy成功。
- 変更後OCR契約script: **210 passed / 1 warning（36.66秒）**、終了コード0。
  変更前はダンプ抑止反映後の同script 210 passed / 1 warning（44.64秒）。
- `git diff --check` 成功。worker SHAは
  `12641b515fd522f7e31750e7eec8f3fc581cfc693c9609c03e579f4c09c50518`。

ダンプ抑止追加前のbackend full 2157 passed / 1 skippedは、この追加後の回帰結果ではない。
追加後の全体回帰は未実施であり、認定接続を含む実装全体の完了前に必要。
frontendの変更なし。既存OCR contractを維持し、provider/model/prompt/schema、実crop、
mapping/physical identity、原本、スキャン、本番DBは変更していない。
OpenAI 0/100、Google 0。実認識精度・association等の実API測定は未実施。
