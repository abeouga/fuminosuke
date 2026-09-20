# WSL glyph解析依存の導入・固定イメージ確認

## 変更境界

専用WSL環境への依存導入は明示許可の範囲内で実施した。
この段階は環境準備であり、PDF字形検証の製品経路への接続ではない。
実DRAFT、印刷成果物、アプリのruntime選択、ACTIVE/OCR受付gateを変更していない。
backend内で未信頼PDF/fontをparseする経路も追加していない。
既存OCR contractを維持した。OpenAI/Google呼出しは0回。

## 固定依存

`backend/scripts/wsl-glyph-requirements.txt`に、公式PyPI配布のpure-Python wheel URLと
SHA-256を固定した。任意のplatform wheelや未固定の依存を解決しない。

| 依存 | 版 | wheel SHA-256 |
| --- | --- | --- |
| fonttools | 4.65.0 | `3060b8c1fc2329fa20265b7c138614143ea7c1624e26c5c180c76aeb74deae6f` |
| pypdf | 6.10.0 | `90005e959e1596c6e6c84c8b0ad383285b3e17011751cedd17f2ce8fcdfc86de` |
| typing_extensions | 4.15.0 | `f0fa19c6845758ab08074a0cfa8b7aecb71c999ca73d62883bc25cc018c4e548` |

Python 3.10ではpypdfのtyping_extensions依存も必要である。
配布条件を[fontTools](https://pypi.org/project/fonttools/4.65.0/)、
[pypdf](https://pypi.org/project/pypdf/6.10.0/)、
[typing_extensions](https://pypi.org/project/typing_extensions/4.15.0/)の版別metadataと照合した。

新規prefix `/usr/local/lib/llmocr-glyph-v1` が存在しないことを確認してから、
WSLのPython 3.10でvenvを作成した。既存venvを削除・上書きする手順ではない。
backendを作業ディレクトリとした導入コマンドは以下。WSL rootで実行する。

```sh
apt-get install --yes --no-install-recommends python3.10-venv
python3.10 -m venv /usr/local/lib/llmocr-glyph-v1
/usr/local/lib/llmocr-glyph-v1/bin/python -I -m pip --isolated --disable-pip-version-check install --no-cache-dir --require-hashes --no-deps --no-index --only-binary=:all: -r scripts/wsl-glyph-requirements.txt
/usr/local/lib/llmocr-glyph-v1/bin/python -I -m pip check
```

導入とhash照合は成功、pip checkは`No broken requirements found`。
Ubuntuの追加packageはpython3.10-venv、python3-pip-whl、python3-setuptools-whl。
依存としてPython 3.10関連の5 packageが22.04.15から22.04.18へ更新された。
pip wheelは22.0.2+dfsg-1ubuntu0.7、setuptools wheelは59.6.0-1.2ubuntu0.22.04.3。
OS全体のupgradeは行っていない。

## 新固定イメージと実probe

既存builderを変更せず、private mount namespace内で新しいイメージを作成した。

```sh
unshare --mount --propagation private python3 scripts/build_wsl_renderer_runtime.py
unshare --mount --propagation private python3 -m scripts.probe_wsl_glyph_runtime 9b513e12512fc7dfe9cb71eee69649d002b02bdd78153c8669d54299466a1e01
```

- image: `/opt/llmocr-renderer-runtimes/9b513e12512fc7dfe9cb71eee69649d002b02bdd78153c8669d54299466a1e01.squashfs`
- bytes: 622,305,280、mode 444、root所有、link数1、gzip。
- builder SHA: `c042e01bfb896bacb2c56c0e4dcb8a4da7792f372c3061fa430d2a696a6ee5c0`。
- build log: `/opt/llmocr-renderer-runtimes/build-squrwpj8/build.log`。
- LibreOffice: 7.3.7.2 30(Build:2)、kernel: 6.6.87.2-microsoft-standard-WSL2。
- 新probe SHA: `1e80a9d0676e9bcd64ae681a049afdaa95eedffcd1177adc90f63db2ea49c5f7`。
- probe内Python: 3.10.12 (main, Aug 31 2026, 10:18:17) [GCC 11.4.0]。

新probeは固定imageを前後SHA検査する既存pinned_runtimeとbuilderの隔離commandを使用する。
実image内で上記3版が専用prefixからimportされること、uid/gid 65534、capability 0、
no-new-privileges、readonly filesystem、network interfaceがloのみ、
ホスト/mnt/cとWindows font mountが存在しないことを検査して成功した。
TTFont、DecomposingRecordingPen、pypdf ContentStreamのimportも確認した。
PDF/fontの入力を受け取らず、資源制限付きの製品glyph helperを代替しない。

既存image `1465eef285317a981d1efa98faf9d6dce267e1fe446e29a7f48c65538a45d65d`
のファイルSHAを再計算して不変を確認した。そのimageに新依存probeを実行すると
終了コード1で拒否され、ホストvenvへfallbackするコマンドはない。
新imageを実テンプレートへ自動適用していない。

## 起動側環境の更新記録

host Python更新により、以前の起動側観測を現在のidentityとして流用しない。
同じread-only観測scriptでimport時の依存集合を2回再読取し、両回一致した。
詳細は[wsl-glyph-bootstrap-observation-20260913.json](wsl-glyph-bootstrap-observation-20260913.json)。

- 187 files、28,247,387 bytes、module files 147、mapped runtime files 37。
- 集合SHA: `7c125023cb37220d80da99d81a4f9a69ad346668367849db901a09097ea774dc`。
- `/usr/bin/python3.10` SHA: `a2f33a6e006989270f4340528eb61f8f97366e00a5d1b602ac8672ea44fc56ae`。
- `libpython3.10.so.1.0` SHA: `d7bc028c6a98ac8c17edeada13609ea36db324d7f49c26b6ca93994e8c830453`。
- workerは`4ef76e57fe0002aa44d4479597dfdfc8ba6952a7174599c98c0fb1e8620264e7`のまま。

import後の動的な実行依存、systemd設定、WSL/Windows起動資源の完全closureは未検証。
過去の実変換前後159件の観測を、新環境で再実行したとは扱わない。

## テストと残作業

- `pytest tests/unit/test_wsl_glyph_runtime_probe.py tests/unit/test_wsl_runtime_builder.py
  -q -o addopts='' -p no:cacheprovider --basetemp=.pt-glyph-dependencies-final-0913`:
  15 passed、0.16秒。
- 初回は巨大なparametrize IDによるWindows一時パス生成エラーが2件発生。
  不正入力とassertionを維持し、テストIDを短縮した再実行が上記結果。
- 新probeと新testのRuff成功。probeのLinux/Python 3.10指定mypy成功。
- 再描画比較の変更を含むbackend fullを、
  `pytest tests -q --tb=short -o addopts='' -p no:cacheprovider
  --basetemp=.pt-replay-glyph-deps-full-0913`で開始。開始は完走成功ではない。
  同一processは1178 passed / 1 skipped / 3 warnings、1752.48秒、終了コード0で完走した。
  収集後に追加したglyph検証本体・helperの全体回帰を含むとは扱わない。

字形とUnicodeの汎用検証、保存PDF/解決font/検証reportのidentity接続、
正式な環境・geometry/crop認定、実テンプレート有効化、実手書きOCRは未完了。
この依存probeは常に`productionQualified=false`であり、資格発行APIではない。
