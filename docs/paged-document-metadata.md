# 複数ページの識別項目と書類名候補

複数ページの読み取りで、文書識別の項目と書類名の設定をページごとに扱う仕様。

## 全体設定とページ別設定の分け方

全体設定をそのままページ別configへ渡すと、他ページのmapping参照や文書全体の識別グループ制約に違反する。
そこで設定を削除せず、全体設定とページ所有のOCRヒントを分ける。

- 全体の`TemplateConfig`検証は維持する。識別グループ、照合役割、表示役割、順序を改変しない。
- 受付schemaVersion 2の全体configに加え、metadataがある場合だけ
  `documentSnapshot.documentMetadataAdapter=paged-document-fields-v1`を固定する。
- 各ページの`documentContext.identityFields`には、そのページが明示的に所有するmappingだけを格納する。
  `mappingId`、`targetPath`、text型、全体設定との完全一致をsnapshot読込時にも再検証する。
  識別項目のないページには空配列を保持する。ページ番号・同名ラベル・文字認識で所属を推測しない。
- ページ別config自体へ全体設定を複製しない。request準備では検証済みlocal contextから
  既存の識別項目フラグ、値形式、桁数ヒントを渡す。provider、prompt、schema、association検証器は変更しない。
- 識別項目を持たないlegacy requestは既存経路のまま。旧受付へadapterを自動追加しない。

paged requestへ識別ヒントを渡すのは意図した拡張である。
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
候補は確定・同一書類への結合・書類一覧への登録を行わない。全ページ確認とrevision付き確定が必要（[全ページ確定・再編集](paged-confirmation.md)）。
