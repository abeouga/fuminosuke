# WSL起動用Pythonのsite依存確認

## 確認結果

2026-09-13、境界測定のbackend full検証中に読み取り専用で確認した。
現行root broker起動の `python3 -I` はsiteの読み込みを無効化していない。
実ホストでOffice/glyph両workerをimportすると、標準ライブラリとプロジェクト外から
以下の2ファイルが読み込まれた。

- `/usr/lib/python3/dist-packages/apport_python_hook.py`
- `/usr/lib/python3/dist-packages/zope/__init__.py`

`-I -S -B` で同じ2モジュールをimportした場合は両方とも読み込まれず、
標準ライブラリと明示したプロジェクト以外のmodule fileは0件だった。
両試験とも終了コード0。workerのmain/broker/innerは呼び出していない。

| 条件 | isolated | no_site | site module | module数 |
| --- | --- | --- | --- | --- |
| `-I -B` | 1 | 0 | あり | 160 |
| `-I -S -B` | 1 | 1 | なし | 155 |

`-S`側のsys.pathは明示したbackend pathのほか、`/usr/lib/python310.zip`、
`/usr/lib/python3.10`、`/usr/lib/python3.10/lib-dynload`だけだった。
通常側はさらに `/usr/local/lib/python3.10/dist-packages` と
`/usr/lib/python3/dist-packages` を含む。
未知の環境変数・鍵・認証情報は取得・出力していない。

## 意味と限界

起動brokerのPython検索範囲を限定する具体的な候補が得られた。
単に依存リストへ上記2ファイルを足すより、必要のないsite初期化を
rootの起動経路から除く方が、追加・差替えを含む資格対象を限定できる。

ただし、今回確認したのは**import成功まで**である。`-S`を製品に追加したわけではなく、
実brokerのmount/systemd/lease/output/recoveryを同条件で検証済みとは扱わない。
固定runtime内のOffice/PDF解析用Pythonとは別の層であり、PDF/font依存が必要なinnerへ
同じ変更を一律適用する根拠にはならない。
stdlib/bytecode/native loader、systemd/起動コマンド、kernel/WSL/Windowsの
依存identityと変更時失効も引き続き必要である。

再現はWSL Ubuntu-22.04のrootで `/usr/bin/python3` を各オプションにより起動し、
backend pathを明示追加した後、`app.services.renderer_wsl_worker` と
`app.services.renderer_glyph_worker` をimportする。`sys.flags`、`sys.path`、
`sys.modules` の `__file__` を分類した。両条件とも `-B` で新規bytecode書込みを抑止した。

製品コード、依存パッケージ、実DB、原本、保存版を変更していない。
Office変換0回、OpenAI/Google呼出し0回。実施中の全体pytestとは別のread-only調査であり、
その全体pytestが本候補を検証したとは主張しない。

## 起動経路の回帰テスト追加

`backend/tests/unit/test_wsl_bootstrap_startup.py` を独立追加した。
実subprocessの直前で起動を捕捉し、固定runtimeのPDF/FODS/referenceとglyph/vectorの
root起動だけに `-I -S -B` を要求する。OfficeやPDF parserを実行しない。
別のテストでinnerは既存の `-I` と非特権/network隔離条件を保持することを要求する。

修正前は5 failed / 1 passed（0.47秒）。5件はいずれもrootの `-S -B` 欠落であり、
innerの保持テストは成功した。最初の起動は既存のpytest共通temp権限によりsetupで失敗し、
未使用の `.pt-bootstrap-startup-before-0913` を指定して上記の再現結果を得た。
進行中の `.pt-border-probe-full-0913` は追加前に収集済みなので、この新規テストを含まない。
既存のテストや製品コードはこの再現時点では変更していない。

修正対象は `renderer_wsl.py` のruntime明示起動、`renderer_glyph_bridge.py` と
`renderer_vector_bridge.py` の固定root起動。innerのpackage検索や旧runtime未指定経路を
一律変更しない。

## 起動側への適用と検証

先行全体検証の1602 passed / 1 skipped / 3 warnings（1947.34秒）を確認した後に、
上記3 bridgeの固定root起動へ `-S -B` を追加した。`renderer_wsl_worker.py`、
glyph/vector worker、Office・PDF parser・font・保存データ形式は変更していない。
runtime未指定の3形式を保持するテストも加え、最終修正前は5 failed / 4 passed（0.15秒）。
修正後は同じ9件を含む関連unit 117件が成功した（0.56秒）。

実WSL integrationのowner切断試験2か所は新しい起動引数へ合わせた。
拒否・他unit保護・成果物非生成のassertionを削除せず、旧起動方法の結果で代替しない。
固定runtime・glyph・vectorの実integrationは **14 passed / 190.72秒**。
参照PNGの既存方式との一致、全ページの字形/文字プログラム、環境・worker/code改変拒否、
存在しないruntimeの拒否、owner切断時の所有unit終了を確認した。
実DBではなくテスト用入力・環境を使用しており、実テンプレートのACTIVE成功ではない。

対象6 PythonファイルのRuff、bridge 3ファイルの限定mypy、`git diff --check` は成功。
寸法読取から保存までの実印刷準備APIは **2 passed / 44.69秒**。
固定runtimeのFODS/reference環境一致とPDF取得、runtime欠落時のDRAFT/成果物不変を確認した。
変更後OCR contractは **210 passed / 1 warning / 39.66秒**。
変更後全体回帰を `RUN_OPENAI_INTEGRATION_TESTS=false`、
`pytest -q --tb=short -o addopts='' -p no:cacheprovider
--basetemp=.pt-bootstrap-startup-full-0913` で実施した。
最終結果は **1 failed / 1610 passed / 1 skipped / 3 warnings（1975.58秒）**。
失敗は `test_print_layout_api.py::test_conflict_invalid_and_published_mutation_rejected` の
期待422に対する409だった。同時に開始した新しいAPI fixture側で `RENDERER_BUSY` を
確認しており、共有mutex競合の影響が疑われる。直列の再現確認を別途行う。
直列の関連API検証では当該print-layoutテストが通過し、同じ失敗は再現しなかった。
この全体実行の開始後に項目別geometryをAPI接続したため、新しい接続の回帰証拠に使わない。
先行1602件の成功を今回の起動修正後の全体成功として扱わない。

`-S`はrootでのsite初期化を止め、`-B`はbytecodeの新規書込みを止める。
既存bytecodeの読込み、標準ライブラリ・native loader・systemd等の全依存固定を
証明するものではない。完全な環境資格・本番閾値・有効化/受付接続は未完了である。
保存済み資格reportのbooleanを変更せず、bridge内容を含む既存fingerprintの失効検査を維持する。
既存OCR contractを維持した。OpenAIは作業全体0/100回、Google 0回。
