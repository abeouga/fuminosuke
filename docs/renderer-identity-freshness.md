# Renderer依存ファイルSHAの再検証

2026-09-13。本番OCR対応中の全体回帰試験で検出した、既存の認定情報失効処理の不具合。

## 原因と修正

`tests/unit/test_renderer_environment.py::test_font_sha_mutation_is_observed`が失敗した。
同じファイルを`one`から`two`へ書き換えても、`file_identity`が以前のSHAを返した。
呼出し先`renderer_dependencies.file_record`はpath・size・mtime_ns・ctime_nsをキーに
SHAをcacheしており、内容が異なっても同じキーならファイルを読まなかった。

サイズと時刻が同一の再現fixtureを追加すると、古いSHAを返すだけでなく、古いmanifestも
有効と判断する2件の失敗を確認した。既存試験にsleepを足したり、時刻を変更して通したりしていない。

このmetadataのみのcacheを除去し、検証時にファイル内容を読み直す。
hash中のsize/mtime/ctime変更を拒否する既存検査、返却schema、欠落ファイルの扱いは維持する。
不変条件「依存内容が変わったら旧認定を流用しない」を復元する修正である。

`renderer_dependencies.py`自体は環境identityの実装SHAに含まれるため、旧実装の認定情報は
現実装と不一致になる。本番フラグを変更したり、新たな資格を発行したりしていない。
legacy OCR画像・crop・provider・mapping・値保存、原本と過去jobは変更しない。

## 検証と限界

- 発見時のbackend全体: 1006 passed / 1 failed / 1 skipped、1271.60秒。
  失敗は上記1件。予算管理の新規試験もこの実行に含まれる。
- 同一metadata再現fixture: 修正前2 failed、修正後2 passed。
- 既存renderer環境試験と追加fixture: 合計12 passed（0.13秒）。
- 修正直前のOCR契約: 210 passed（52.46秒）、修正後210 passed / 1 warning（42.13秒）。
  前後とも`backend/.venv/Scripts/python.exe scripts/verify_ocr_contract.py`を実行した。
- 対象2 PythonファイルのRuff、変更moduleの限定mypyは通過。全体再試験の結果は後述する。
- 実インストールのOffice program配下170対象ファイル、418148508 bytesを2回確認。
  1.003秒/1.059秒、全identity一致。回帰試験と同時実行中のread-only測定であり、
  GUI/API全体の応答時間、WSL環境資格、全runtime closureの証明ではない。
- `one/two`以外のfixture期待値・旧試験は変更していない。実API呼出し0回。

この変更は同じ時刻・サイズでの古いcache再利用をなくすものであり、
敵対的な同時書換え全般、動的module/config読込の完全追跡、runtimeの完全不変化を証明しない。
network/font/crop等を含む本番認定の残条件は維持する。

変更ファイルは`backend/app/services/renderer_dependencies.py`と
`backend/tests/unit/test_renderer_identity_freshness.py`。
診断scriptはrepo外`C:/Users/hanam/.codex/tmp/paged-budget-inspection-20260913/check_identity_cost.py`。

後続で`renderer_environment.runtimeDependencies.hashCache`の説明値も
`none-content-rehashed-v1`へ訂正した。旧説明値だけが残っていたもので、新たなcache方式を追加していない。
completeTrace/environmentQualified=falseを保つことを別unit testで固定した。
後続full結果と未解決の別renderer終了試験は[WSL font検証](verification/wsl-font-requests-20260913.md)を参照。
