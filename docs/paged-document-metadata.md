# 複数ページの識別項目と書類名候補

2026-09-13。本番化要求に含まれるページ別設定の対応。これ単独で本番認定・有効化は成立しない。

## 原因と変更境界

`JobPageService.accept`は、文書識別または書類名設定があるconfigを一律に
`PRINT_PAGE_CONFIG_UNSUPPORTED`で拒否していた。全体設定をそのままページ別configへ
渡すと、他ページのmapping参照や文書全体の識別グループ制約に違反するためである。
フロントだけで拒否を消すと情報を失う。この設定を削除せず、全体設定とページ所有のOCRヒントを分離した。

- 全体の`TemplateConfig`検証は維持する。識別グループ、照合役割、表示役割、順序を改変しない。
- 受付schemaVersion 2の全体configに加え、metadataがある場合だけ
  `documentSnapshot.documentMetadataAdapter=paged-document-fields-v1`を固定する。
- 各ページの`documentContext.identityFields`には、そのページが明示的に所有するmappingだけを格納する。
  `mappingId`、`targetPath`、text型、全体設定との完全一致をsnapshot読込時にも再検証する。
  識別項目のないページには空配列を保持する。ページ番号・同名ラベル・文字認識で所属を推測しない。
- ページ別config自体へ全体設定を複製しない。request準備では検証済みlocal contextから
  既存の識別項目フラグ、値形式、桁数ヒントを渡す。provider、prompt、schema、association検証器は変更しない。
- 識別項目を持たないlegacy requestは既存経路のまま。旧受付へadapterを自動追加しない。

新paged requestへ識別ヒントを渡す挙動は意図的な拡張である。
mapping・physical identity・画像/crop・response associationの契約を緩める変更ではない。

## レビューと書類名

レビュー応答に、明示page ID付きの識別項目と書類名候補を返す。
候補は受付全体configの`documentName.fieldPaths`順に現在値から作る。
設定がない場合だけ、明示`displayRole=title`の識別項目を用いる。
全構成項目が非空のreadableであることを要求し、欠落・不明文字から部分的な名前を推測しない。
設定済みprefix/separator/suffixを保持し、候補は最大180文字とする。

初回表示では保存済み書類名を優先する。補正応答に新しい候補が含まれても入力中の書類名を
上書きせず、「設定した項目から書類名を反映」の明示操作で取り込む。
識別表示はpage IDとmapping IDの両方が一致する欄だけに付ける。
候補は確定・同一書類への結合・書類一覧への登録を行わない。全ページ確認とrevision付き確定は従来どおり必要。

## 検証

- 変更前OCR contract: 210 passed（39.83秒）。
- 変更後OCR contract: 210 passed / 1 warning（44.40秒）。前後とも
  `backend/.venv/Scripts/python.exe scripts/verify_ocr_contract.py`を実行した。
- 新規unit: グループがページをまたぐ場合、primary/confirmationの分割、形式/桁数、
  requestのmapping/sourceRegionId/targetPath/bbox、画像SHAとmappedCellsの不変、
  不正context拒否、全体順の候補生成を固定する。
  最終全体試験で13件すべて通過した。
- 新規API fixture: 2 passed（43.31秒）。受付後の現行config変更に依存せず、
  ページ別provider request、補正、全ページ確定、XLSX取得までの対応を確認。
  外側SHAを再計算した不正contextでも、内側の所有権/形式照合で拒否した。
- frontend: 全34ファイル369 passed（13.80秒）、build成功。既存bundleサイズ警告あり。
- 対象7 PythonファイルのRuff、変更5モジュールの限定mypyは通過。全体mypy成功の主張ではない。
- 実DRAFT版をSQLite read-only/query_onlyで診断し、4ページのmapping数60/51/37/3、
  識別項目数1/0/0/0を確認した。全151項目の配置、保存config、quality JSONは不変。
  仮値からの名前候補生成を確認したが、実OCR・実確定・実有効化ではない。

初回の全体試験は新規unit/integrationファイルの同名によるcollection errorで停止した。
integrationを`test_paged_document_metadata_api.py`へ改名し、全assertionを保持して再実行した。
最終backend全体: 979 passed / 1 skipped / 3 warnings（1253.95秒）。
`cd backend`の上で`.venv/Scripts/python.exe -m pytest tests -q -o addopts='' -p no:cacheprovider
--basetemp=.pt-paged-metadata-full2-0913`を実行した。skipと3件のdeprecated font.copy警告は既存。
テスト削除・assertion弱体化なし。`git diff --check`も通過した。

実アプリの8000/5173は確認時に停止中で、実Chrome操作は未検証。

## 残る範囲

本番gateは閉鎖したまま。WSLのfont/runtime・geometry・実手書きcrop資格検証、
全ページ共通のAPI予算、prompt/schema実体と実行resourceの固定、実UI/APIによる有効化確認は別途必要。
後続の[API予算管理](paged-ocr-budget.md)で全ページ予算を実装した。残る認定条件は維持する。
mark/composite/読取項目のないページは引き続き明示拒否する。
書類一覧・既存reconciliationへの統合は実装していない。
今回の実OpenAI/Google呼び出しは0回。DB schema変更・実DB/config更新・commit/pushなし。
