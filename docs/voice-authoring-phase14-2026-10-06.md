# 文之助：共通編集APIの意味付け操作

2026-10-06 / フェーズ14 / `editing.v1`

## 到達点

見出し5、繰り返し表4、マーク2、書類識別1、書類名1の計13操作を追加した。フェーズ13の9操作と合わせて22操作である。通常のAPI有効化と業務DBのmigrationは行っていない。画面、手動描画、音声の動作はこのフェーズでは変更しない。

対象は `C:\Users\hanam\Desktop\llm-ocr-deploy`。開始時HEADは `35adae6df378fe288593b113b12615f5c47f2a2a`。970ソース・文書、DB、855業務ファイルを先に退避した。既存の未コミット差分を保持し、staging・commit・pushは行っていない。

保存前の契約ファイルを再読込した検証では、追加対象13操作がすべて `union_tag_invalid` で拒否されることを確認した。変更前資料と今回分差分は製品外に保存している。

## 実装

| 対象 | 保存する変更と境界 |
| --- | --- |
| 見出し | 仮想見出し作成、名称、親子の接続・解除、仮想見出し削除。物理見出しを削除せず、印字文字を表示名変更で書き換えない。循環・複数親・孤立参照・ページ越境を拒否 |
| 繰り返し表 | 列の意味と明示セルから行を定義。既存行のhandleは同じ物理セル配列だけを許可。並べ替えで行ID・relation ID・targetPathを保持。除外・解除でも物理セルと既存意味を保持 |
| マーク | 既存set/optionの更新と新規作成、inputへの解除。automatic/manualは同じ欄に保存済みの領域を参照。symbol/checklistは欄全体。複数setの解除では次の意味を同じbatchで明示 |
| 書類識別 | 最終graphの通常text mappingからmappingId/pathを解決。個別・組合せ、digits長さ、役割を検証。空配列で明示解除 |
| 書類名 | 最終graphの文字欄を順序付きで指定。separator/prefix/suffixを保存。重複・存在しない欄を拒否。nullで解除 |
| 履歴 | 複合操作も一つのrevision・receipt。削除済み対象への後続参照はtyped failureで全体拒否。undoは意味graphと識別・書類名を復元し、品質確認や有効化は復活させない |

`SemanticCommands`は共通参照と見出しを担当し、表・マーク・書類情報は別モジュールに分けた。全体の保存は既存 `TemplateAuthoringService.prepare/persist` とreceipt transactionへ集約する。依存不整合によるPydantic検証失敗も、部分保存や500/PENDINGを残す形ではなく失敗receiptとして返す。

catalogへ表・行・マーク群・選択肢・既存領域・識別グループを追加した。所属、意味、行のセル配列は生のIDではなく同じ有限handleで返す。ツールschemaはcapabilitiesで操作型を絞り、paged sessionではマークをchecklist/multipleへ限定できる。HTTPでも同じ制限を検査する。実際にモデルへツールを渡すcontrollerはフェーズ16で接続する。

ページと局所録音の制約は、変更セルだけでなく、意味の参照先、表全体、識別・書類名の参照先へ適用する。現在ページの操作で別ページの書類名設定を消すことも拒否する。全ページcontextであっても、見出しや一つの表を異なるページへまたがらせることはできない。

## 設計を現行保存仕様へ合わせた点

- マーク選択肢は1set **50件**。設計メモの100件を訂正し、既存の保存validatorを広げていない。
- `IdentityEntry`には独立したlabel引数を設けない。現行authoringが識別名をmappingの表示名へ同期するため、名称変更は `cell.rename`、識別指定はfield参照と役割・書式で行う。送った引数を黙って無視するAPIにしない。
- 表の各セルは通常の記入欄か、一つの意味を持つマーク欄を対象とする。未対応compositeや複数意味のマークを一列へ推測変換しない。
- 既存reviewStructureの `recordLabelCellIds` は一要素を一列として扱う。一列の複数印字ラベルはrelationにすべて保持し、表示用の列配列へ平坦化しない。代表ラベルが全列分そろわない場合は、明示したmapping名から列を表示する。
- `recordStructureVerified`は明示した行・意味の対応を表す。`layoutVerified`、renderer資格、ACTIVE化を代行するフラグではない。
- 物理矩形・多角形、crop、元XLSXのgeometry、印刷ページの再編成は新APIに入れていない。新しいマーク領域の位置は引き続き手動作成する。

## 検証

コピーDBの実API/保存serviceで **既存23シナリオと追加21シナリオ、計44シナリオ**が通過した。各scriptが繰り返すmigration保持検証は重複集計していない。in-process HTTP/DBの検証であり、実マイクやブラウザー受け入れとは区別する。

| 検証 | 結果 |
| --- | --- |
| `verify_phase13_regression.py` | 既存23件。OCR値・ゼロ保持、同時revision競合、同一要求の再送、flush失敗、基本意味付け、局所turn、default無効など |
| `verify_semantics.py` | 追加7件。見出し循環と削除依存、行並べ替え、ID付け替え拒否、書類情報、マーク、22操作の型とpaged用schema |
| `verify_pages.py` | 追加5件。実XLSXの保存serviceで `A1:AA16` / `A17:AA31` を作成。仮想見出しのページ座標、基準画像・transform保持、越境拒否、checklist制限、別ページmetadataの削除拒否 |
| `verify_guards.py` | 追加3件。手動保存した領域の再利用と所有者照合、最終識別validatorの失敗receipt、選択外metadataの局所録音拒否 |
| `verify_graph_inverse.py` | 追加6件。見出し・印字行キー・表・識別・書類名の一括undo、行の追加/除外、複数マーク解除、削除後参照、複数/未印字ラベルの2列レビュー対応、未作成localRefの拒否 |
| 静的検査 | `ruff check app/editing`、`mypy app/editing app/api/v1/routes/editing.py --follow-imports=silent --no-incremental`。15 sourceを確認 |
| 生成型 | 既存TypeScriptで `tsc --strict --noEmit --skipLibCheck --target ES2020 --module ESNext --moduleResolution bundler`。HTTP・tool型の生成物を検査 |
| 保持照合 | 通常DB全31テーブル、855業務ファイル、API台帳25件を開始時と照合。専用意味付けコピーでも保存済みOCR・revision・確定結果・受付jobと既存ファイルを照合 |
| 差分 | `git diff --check`と開始時SHA比較。予定外の変更・削除なし |

途中の拒否と失敗は最終成功と区別して残した。手動でgeometryを変更済みのチェック帳票は既存の元セル対応検査でpaged化を拒否されたため、その制限を解除せず、元セル対応が検証できる別の保存XLSXを使用した。ページ区切りも既存APIが返す有効行境界を使用した。通常OCR有効化、正式な印刷資格発行、Office再描画は実施していない。

## 変更ファイルと保護範囲

- 変更：`backend/app/editing/contracts.py`、`catalog.py`、`commands.py`、`service.py`、`schemas.py`。
- 追加：同ディレクトリの `semantic_commands.py`、`record_commands.py`、`mark_commands.py`、`metadata_commands.py`、`scope.py`。
- 文書：本書、`AGENTS.md`、`ARTIFACT_STORAGE_POLICY.md`、`docs/ocr-safety-contract.md`、フェーズ12統合設計。

既存OCR contractを維持した。新APIは既存の保存serviceを使用し、OCR provider/prompt/schema/model、画像前処理、crop、OCR再実行・確定・出力処理を変更していない。受付済みjobのsnapshotを現在のテンプレートから再構築していない。既存のDBモデル・migration・保存layoutの型も変更していない。

検証の正本はdeploy版AGENTS §7とSafety Contract §8である。退役済み `verify_ocr_contract.py` や旧Google経路の全件テストを再導入していない。全体回帰、全テンプレート形状、ブラウザー、音声品質を完全に検証したという意味ではない。重い統合検証はフェーズ18、利用者参加の実マイク試験はフェーズ19に残る。

## 証拠と次フェーズ

保存先は `D:\llm-ocr-archive\voice-assistant\20261006-phase14`。開始時backup、失敗を含む各run、最終成功log、生成schema/TypeScript、reviewStructure、`copied-ocr-preservation.json`、`preservation.json`、`phase14-only.diff`を保管する。試験script・fixtureは製品へ追加していない。

実外部AI APIは追加 **0回**、累計 **25/100回、残り75回**。APIのローカルHTTP要求数を外部AI使用回数へ混同しない。

フェーズ14で停止する。次は **フェーズ15：GPT-6.1-Sol / high**。手動editorの操作終了時保存、共通command、queue、未送信入力・競合復旧を実装する。新APIの通常利用への一斉切替は引き続きフェーズ19の受け入れ後とする。
