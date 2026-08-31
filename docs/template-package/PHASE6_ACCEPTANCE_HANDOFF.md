# Phase 6 受け入れ検証記録

最終更新: 2026-09-01

## 判定

Phase 6は**完了**である。実Google Driveを用いた環境Aから空の環境Bへの移送、実OCR、実出力、環境Bからの再Export、意味的一致、security/compatibility/failure injection、既存機能回帰、実ブラウザ、利用者・開発者文書まで確認した。

Phase 7の電子署名、publisher信頼昇格、公開配布には着手していない。

## 維持した境界

- 外部packageはfile/Driveを問わず`TemplatePackageReader`だけを検証入口とした。
- `GeneratedTemplatePackageValidator`は自己生成package専用とし、外部入力には使用していない。
- ZIPはentry単位で上限付き読込を行い、`extractall`は使用していない。
- Importと有効化を分離し、Import完了時にactive versionを変更していない。
- publisherは`UNVERIFIED`のままである。
- 正式releaseはimmutableとし、1.0.0/1.1.0を削除・上書きしていない。
- 同一identity/version・異なるdigestを上書きしていない。
- Drive/Spreadsheet ID、folder ID、Drive version、head revision、ETagをpackage内部へ含めていない。
- workspace backup同期とtemplate単体同期を分離した。

## 検証環境

通常の`backend/data`は使用していない。

- 環境A: `C:\Users\hanam\AppData\Local\Temp\llm-ocr-phase6-a-20260901`
- 環境B: `C:\Users\hanam\AppData\Local\Temp\llm-ocr-phase6-b-20260901`

環境Bは空DBからmigration head `0030_template_package_drive_sync`まで適用した。OAuth token、API key、Googleアカウント識別情報は記録・test artifact・packageへ出力していない。

## live Google Drive E2E

環境Aからgeneric/spreadsheet 1.1.0を正式releaseとして発行し、環境BでDrive一覧取得、download、inspection、非active Import、明示activationを完走した。Drive上の1.0.0/1.1.0はimmutableな証跡として保持している。

実ブラウザでは次を確認した。

- 正式版4件の一覧と`配布者未確認`
- generic 1.1.0 inspection、非active Import、明示activation、同一releaseの冪等再実行
- file uploadによる同一identity/version・異digest競合とImportボタン無効化
- Export inspection、file export進捗100%、保存完了表示
- modal close後のfocus復帰
- generic/spreadsheetの確定済みreview、XLSX、Google Sheets導線
- 新規タブでhome、Drive一覧、generic、spreadsheetを再表示し、browser console error 0件

## 実OCRとruntime再現性

### generic 1.1.0

- 実OpenAI `gpt-5-mini`でOCRを実行した。
- candidate `PHASE6-CANDIDATE`、書類名`PHASE6-GENERIC`、identity値`IDENTITY-2`を再現した。
- uncertain 1件を人手で空欄へ修正し、未解決0件でconfirm endpoint 200を確認した。
- revision 3、confirmed状態、document library反映、Google Sheets名とlocal名の収束を確認した。

### spreadsheet 1.1.0

- 環境BへImport・有効化したversionと保存済み合成画像で実OpenAI OCRを実行した。
- candidate `PHASE6-CANDIDATE`、書類名`IDENTITY-1`、複合identity値`IDENTITY-1`/`IDENTITY-2`を再現した。
- review後にconfirm endpoint 200、revision 2、document library、XLSX、Google Sheets出力を確認した。
- 確定後も空欄warning 11件が表示されるが、confirmを阻害する未解決項目ではない。これは既存review表示仕様である。

### runtimeが参照した構成

環境Bのactive runtimeから再Exportしたpackageを`TemplatePackageReader`で読み、次を確認した。

| 項目 | generic | spreadsheet |
|---|---:|---:|
| OCR region | 6 | 0 |
| field mapping | 32 | 43 |
| identity field | 2 | 3 |
| identity group | 2 | 2 |
| document name field | title identity fallback | 1 |
| candidate set / binding | 1 / 1 | 1 / 1 |
| output layout cell / binding | 1 / 1 | 1 / 1 |
| spreadsheet source | なし | あり |

spreadsheetのsource sheet名は末尾空白を含む完全一致値としてruntime、再Export、XLSX、Google Sheetsで保持された。有効化時のtemplate name/document type反映、candidate/output layout materializationは、環境Bの実OCRと再Exportの双方で検証した。

## Output

### XLSX

環境Bのspreadsheet確定結果から実際にdownloadしたXLSXを`openpyxl`で検査した。

- 正常に開ける。
- 対象sheet名は末尾空白を含め完全一致する。
- source workbookと同じ2 sheetだけで、hidden sheetはない。
- external linkは0件である。
- source/outputの数式7件を保持する。
- 期待する9 cellだけが変更され、candidate、identity、出力値が指定位置へ入る。
- 未割当て/空OCR値でsource workbookの固定値を消さない。

### Google Sheets

generic/spreadsheetの両方で実出力を作成し、local同期状態、保存先、document名、期待値、再実行時の既存operation収束を確認した。Google固有IDはlocal/user stateだけに存在し、packageへ逆流していない。

generic確定後、local名が変化していなくても保存済みremote baselineが古い場合にDrive名を再調整する不足を検出し、再調整条件と回帰testを追加した。

## Round-trip

環境AのDrive正式版と環境Bの再Exportを、双方とも`TemplatePackageReader`だけで読み込んだ。

| 比較項目 | generic | spreadsheet |
|---|---|---|
| Canonical Template Model | 一致 | 一致 |
| Canonical quality | 一致 | 一致 |
| Canonical output layout | 一致 | 一致 |
| asset role → SHA-256 | 一致 | 一致 |
| content digest | 一致 | 一致 |

package byte列は`exportedAt`とZIP metadataにより異なる。これは正常な環境依存差分であり、意味差分ではない。禁止metadata keyは0件、secret/Google ID/local ID/pathの値漏えいは0件、publisherは両方`UNVERIFIED`である。

## Security / compatibility / failure injection

Phase 3 security fixtureを再実行し、Zip Slip、ZIP bomb、symlink、duplicate、Unicode normalization/casefold collision、Windows予約名、ADS、encrypted ZIP、unsupported compression、undeclared entry、hash/size/MIME/signature不一致、broken JSON/PNG/XLSX、XLSX external link/hidden data、内部参照切れをすべて拒否した。

上限値はpackage 128 MiB、entry 32、展開後合計256 MiB、圧縮比100、manifest 1 MiB、JSON 16 MiB、image 50 MiB/20,000 px/100M pixel、XLSX 64 MiB/5,000 entry/1 entry 32 MiB/展開後256 MiB/圧縮比100である。

compatibilityは古いversion、pre-release、future schema、unsupported required feature、minimum app version不一致、同一version同digest、同一version異digest、古いrelease、metadata conflict、activation optimistic conflictを再検証した。

failure injectionは`after_db_flush`、`after_asset_move`、`before_db_commit`を再実行し、DB record、確定asset、stageが残らず、active versionが変化しないことを確認した。

## 回帰試験

- Phase 1〜6 package targeted backend: 72 passed
- portable/workspace/document library/Sheets/template既存回帰: 117 passed
- backend全体: 426 passed, 1 skipped
- backend collect: 427 tests
- frontend全体: 20 files / 201 tests passed
- production build: 成功。既存のchunk size warningのみ
- migration test: 7 passed
- migration head: `0030_template_package_drive_sync`
- package schema同期: 合格
- Phase 6対象Ruff: 合格
- Phase 6 package core/output service対象mypy: 合格
- diff whitespace: 合格

repository全体RuffはPhase 6外を含む既存52件、repository全体mypyは既存6 files/36件が残る。`ocr_result_service.py`単独mypyで表示される3件は今回の変更行ではない既存箇所であり、今回追加したremote name再調整分はunit testとRuffを通過した。これらをPhase 6の成功として隠していない。

## 既存機能回帰

- portable backup: 合格
- workspace backup / workspace Google Drive sync: 合格
- document library hooks / bidirectional sync / Drive: 合格
- Google Sheets既存出力: 合格
- template通常管理、portable bootstrap、pipeline API: 合格
- template package Drive sync: 合格

workspace backupとtemplate単体同期のfolder root、API、UI、local stateが混在しないことをtestで確認した。

## 文書

- 利用者向け: `docs/template-package/USER_GUIDE.md`
- 開発者向け: `docs/template-package/FORMAT_V1.md`
- 実装計画・履歴: `LLMOCR_TEMPLATE_PACKAGE_IMPLEMENTATION_PHASES.md`

## 未解決事項

Phase 6 acceptance gapはない。

Phase 6外の既知事項は次のとおりである。

- frontend production buildの500 kB超chunk warning
- repository全体Ruff 52件
- repository全体mypy 36件/6 files
- 電子署名、publisher信頼昇格、公開配布はPhase 7の未着手範囲

## 停止時の扱い

- 検証用backend/frontendを停止する。
- 通常の`backend/data`は変更していない。
- 環境A/Bの受け入れdata rootとDrive正式releaseは証跡として保持する。
- commit/pushは、このPhase 6依頼では実施しない。
