# Test Artifact Storage Policy

更新日: 2026-09-17

## 移動履歴

検証済みのpytest一時生成物を、CドライブからDドライブへ移動した。

- 移動元: `C:\Users\hanam\Desktop\llm-ocr\backend\.pt-*`
- 移動元: `C:\Users\hanam\Desktop\llm-ocr\backend\.pytest-*`
- 移動先: `D:\llm-ocr-archive\2026-09-17\backend-test-artifacts\`
- 対象: 364ディレクトリ
- 容量: 事前集計で約23.18 GiB
- 結果: D側364ディレクトリ、C側の対象ディレクトリ0件
- 検証: コピー後に全364ディレクトリをrobocopyの差分なしモードで照合してから、C側を除去

対象ディレクトリには、pytestの各テスト用DB、画像、`result.json`、ログなどが含まれる。これは実行時の正式データではなく、検証実行の一時成果物である。

## 今後の必須ルール

同種の生成物をCドライブへ作成しない。pytestを実行する場合は、必ずDドライブ上の絶対パスを `--basetemp` に指定する。

例:

```powershell
python -m pytest tests -q --basetemp="D:\llm-ocr-archive\pytest-work\20260917-backend-full-01"
```

次の指定は禁止する。

```text
--basetemp=.pt-...
--basetemp=.pytest-...
```

`backend`をカレントディレクトリにした相対パス指定は、Cドライブ上に生成物を作るため使用しない。過去の検証Markdownに記載された相対 `--basetemp` は履歴であり、現在の実行コマンドとしてそのまま再利用しない。

## スクリプト固有の注意

`scripts/verify_ocr_contract.py` は現状、`backend`配下に固有の `.pt-ocr-<id>` を作る実装になっている。Dドライブの一時領域を受け取れるようにする変更を行うまでは、このスクリプトを変更なしで実行しない。

## 今回の移動対象外

以下は移動していない。

- `backend/data`: 現行DB、アップロード、OCR成果物、バックアップ
- `backend/.venv`: バックエンド実行環境
- `.git`: Git履歴およびGit内部オブジェクト
- `.mypy_cache`、`.pytest_cache`、`.ruff_cache`: 既存のキャッシュ
- `frontend/node_modules`: フロントエンド依存関係

このファイルを、以後の作業でpytest生成物の保存先を判断する際の参照元とする。
