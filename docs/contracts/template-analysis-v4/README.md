# template-analysis.compact.v4 — 設計契約

フェーズ14で固定した製品外比較用の契約。実装・実APIの受理・製品保存は未検証。正本は本書、`proposal.schema.json`、`provider-prompt.txt`、`phase15-protocol.json`。Phase15の実装はこれらを同時に満たすこと。

## 1. 責任分担と非互換境界

```mermaid
flowchart LR
    A[既存セル・画像・任意の対象範囲] --> B[不変snapshotと候補index]
    B --> C[GPT-6 Luna / Responses 1回]
    C --> D[v4案の完全受信・検査]
    D --> E[既存セルへの展開・全体の所有検査]
    E --> F[共通compiler・manual変換]
    F --> G[検証済みの案を表示]
    G --> H[案を反映・確認して保存]
```

- AIは意味、直接ラベル、局所親、表の列用途を選ぶ。幾何候補を意味上の正解とは扱わない。
- サーバーは取得済み行列の展開、名前の参照解決、読取定義の再利用、所有・依存・保護を検証する。欠けた行/欄/文字を補完しない。
- wire形式だけをv4へ置換する。新endpoint、AI専用の保存、LayoutDefinitionの全面置換、OCR provider変更は行わない。既存セルの新規検出・分割も今回の対象外。
- v3をv4として黙って解釈しない。対照実験のv3 decoderは別入口に固定し、v4失敗からの自動fallbackにしない。既存保存テンプレートの移行は不要。
- provider DTO → 内部の意味単位 → 共通commandという境界を維持する。新モデル形式が画面・DB・手動編集へ漏れない構成とする。

## 2. 取得時の入力と対象範囲

各runに所有resource/version/page、編集revision、catalog generation、画像SHA、物理/文字digest、candidate policy/index digest、request schema/prompt hash、scope digestを固定する。所有run側の期待値は復元snapshotと別に保持する。

Phase13の不変snapshotを基礎とし、scopeは後からliveな選択状態を参照しない。scope/versionを追加した新policyとして記録し、旧snapshotの意味を変更しない。実製品の認証・所有者確認・保存transactionをfixture bindingで代用しない。

- `c` →物理cell ID、`g/r/k` →候補内の行列を取得時に固定する。応答時に現在の配列から再採番しない。
- write対象の `target=true` と参照専用の `contextOnly=true` を区別する。scope外のセルをAI判断で対象へ追加しない。
- 範囲外セルは文字・画像・選択肢の根拠に使えるが、role・parent・意味・表所属などを変更できない。分類が必要な参照先は既存catalogでそのroleが成立していなければ依存案を保留する。範囲外に見出しを捏造しない。
- 表選択なしは対象ページ全体。選択ありは明示された表の既存セルと明示的な表題を対象とする。範囲外の関係は参照だけにする。候補の一部しか対象に含まれないrecords/formは勝手に切り詰めない。
- 未選択ページ全体の依頼を、表示中の狭い範囲だけの完了に変更しない。範囲選択は必須にしない。
- Phase15の範囲比較では同じ全ページ画像と全c一覧を渡し、write範囲と提示候補だけを狭める。crop・解像度・文字取得の変更を同時に測らない。

## 3. 出力の意味

rootは `version / marks / units / roles / headings / hierarchy`。すべてrequired、追加key禁止。配列が空でもキーを省略しない。Structured Outputsの形状検査に加え、以下をサーバーで検査する。

### fields：共通設定を一度だけ出す

`kind=fields` は同じparent・axis・readingを共有する独立項目の集合。`items` は `value / labels / name`。

- 圧縮のための集合であり、全itemsを一つの業務項目へ併合しない。内部では1値セルごとの独立field単位へ展開する。
- labelsは直接の項目名。parentは局所グループ見出し。例えば法人名→フリガナ→記入欄では、法人名をparent、フリガナをlabelsへ指定する。両方を並列labelsにしない。
- parentの誤りをnullへ落として救済しない。parentが省略可能というのは実際に局所親が存在しない場合に限る。
- axisは既存のhorizontal/vertical/directを保持する。v4で座標から意味やaxisを新たに推測しない。directにlabelsは付けられない。

### form：行ごとに意味が異なる通常フォーム

`kind=form` は `grid / rows / parent / labelColumns / valueColumn / reading`。
指定候補の閉区間から、各行のvalueColumnを一つのfield、同じ行のlabelColumnsをその直接labelsへ展開する。表/recordSectionは作らない。

- 住所・氏名・電話番号のような項目群が対象。行ごとに値の意味が異なる。繰り返しレコードを、通常フォームとして扱ってはならない。
- labelColumnsは左からの明示順、重複禁止、valueColumnとの重複禁止。各行に同数の既存セルがあり、直接ラベルの文字がすべて非空であることを要求する。左右列の関係からこの限定形式のaxisはhorizontalとする。
- 同じparent/readingで説明できる区間だけを指定する。途中で親が変わるT1の行などは、正しい区間に分けるかfieldsで指定する。
- formは印字textから名前をそのまま作る圧縮構文。名前を画像から訂正したい場合はfieldsの明示nameを使う。空ラベル、欠けた行、異なる読取設定を推測補充しない。
- 範囲や列定義自体の不正はform全体保留。展開後の既存手動保護等は独立field単位で保留する。roleだけを先に反映しない。

### records：同じ列の意味を繰り返す表

`kind=records` は `grid / rows / parent / name / columns / rowKeyStrategy / includeEmptyRows`。

- rowsは候補内のfirst/last参照の閉区間。範囲外・逆順は拒否/保留し、配列indexを物理identityへ使わない。
- columnsは候補の全kを一度ずつ宣言する。value列は `column / use / labels / name / reading`。その他は `column / use / headers` だけで、不要なname/readingのnull反復を除く。
- useはvalue、row_label、row_key、context、unknown。意味はPhase13の展開と同じ。row_labelとvalue列labelsを行別cellLabelCellsとして保持する。
- printedにはちょうど1本のrow_key、positionには0本を要求する。手書きの番号や値を印刷済み行キーへ変えない。
- 初回AI案のincludeEmptyRowsはtrueを要求する。wire schemaのbooleanだけでは検査が完了しない。falseはrecords単位を保留し、元の行を削除しない。
- unknown列、用途と設定の不整合、欠けた列、重複列、支持できない混在読取は表全体保留。部分的に列を取り除いて通さない。
- context bodyはignored。文字と物理参照は案metadataに保持する。headersは明示された項目名だけをlabelへ導く。contextの分類は表の受理に依存し、表が保留なら独立適用しない。

### 名前と読取定義

- field/value列のnameがnullなら、labelsの入力textを**指定順に各文字列の前後だけtrimし、半角スペース一つで結合**する。内部空白・改行・文字種・表記を補正しない。全ラベル非空・結果1〜100文字が必須。不足時は保留し、parent名や値から補充しない。
- records.name=nullは非nullのparent.textを前後trimして使用する。parent=nullなら明示name必須。既存name・targetPathの自動変更はしない。
- 明示nameもtrim済み非空・最大100文字を要求する。人の確認前の意味候補であり、名前の一致だけではbindingを証明しない。
- reading=`text`は通常の文字読取。その他はmarks内の一意m参照。m参照は応答内だけのIDであり永続IDではない。
- marksはkind=symbol/checklist、明示options、evidenceセルを持つ。symbolはsingle、checklistはmultipleに決定的に展開する。単数checklistへのfallbackは禁止。
- optionsの順序・名称を保持し、重複・禁則・上限は既存mark契約で検証する。定義を共有しても各値セルは固有の意味/markSet/targetPathを持つ。同一性がない既存markSetを上書きしない。
- m参照が未知/重複、未使用の定義、異なる根拠への差替えを見逃さない。参照完全性の不正は応答全体拒否。根拠が意味上不十分な定義は、それを使う全単位を保留する。

### 補助分類と階層

- rolesは同じrole/reasonのセルをまとめる。reasonはv3の列挙値のまま。role/reasonの組合せ不一致を検査し、意味構造で既に使用したセルとの衝突を検査する。
- 未出力セルは既存状態を維持し、blank fixtureではunknown。未出力をignored、role=inputだけを意味付け完了へ読み替えない。
- units.parentは受理される意味単位の依存見出しとして分類する。headingsは追加の見出しだけ。hierarchyはchild/parentの独立接続で、fieldの直接ラベルを代替しない。
- 必須の局所親不正は依存するfield/表を保留。任意の上位階層不正はそのedgeを保留して、独立に成立する表を保持できる。循環・自己参照・手動parent変更を拒否する。

## 4. 全体検証と部分成功

一つのunits配列に統一しても、モデルが重複しなくなる保証はない。サーバーの共通所有検査を正本とする。

1. 完全受信、completed状態、拒否/incompleteなし、JSON重複keyなし、schema/version適合を確認する。部分JSONを公開しない。
2. 別保存の期待snapshot/所有者/画像/世代/scope/policyを照合。すべての参照を保留予定単位も含めて検査する。
3. 全unitsを展開し、値所有、role、parent、reading/mark、records等のwrite集合と依存を収集する。不正単位のclaimを消して競合を隠さない。
4. 同じ値の複数所有、役割の不整合、既存手動意図との競合は全関係側を保留。順番・records優先・より大きい候補優先で解決しない。fields集合はitem単位、recordsは表単位の保留。
5. 依存単位が保留なら、関連contextや従属案も保留。独立した意味単位だけを既存共通compilerへ渡す。
6. physical identity・target scope・facet保護・semantic projectionを照合し、ManualCompiler変換後も保持することを確認する。1つ以上の実際の意味単位が成立して初めて「使える案」とする。

未知の物理/candidate/row/column/m参照、所有snapshot不一致、範囲外write、壊れたJSON/schemaは応答全体拒否。既知参照による意味競合/未対応構造は上記単位の保留。拒否・保留は同じものとして集計しない。

## 5. 保護と適用

解析中の手動操作、同値の再指定、明示解除も保護する。既存binding/meaning/parent/reading/mark/recordsを新案から再生成しない。保護解除は既存の明示操作だけを使用する。

案は既存の編集draftへ「案を反映」で取り込み、「確認して保存」で共通保存へ進む。run復元・取消・receipt・revision・既存IDの保持は製品採用後の統合要件。v4実験の成功をDB保存成功と呼ばない。
複数セルAI枠、移動アニメーション、自動スクロール、表題overlay等は検証済み案のセルIDを消費する。AI出力の座標で枠を動かさない。

## 6. Phase15で追加確認する受入例

以下は**実装要件であり、Phase14で実行済みの動作テストではない**。

| 入力/状態 | 必須結果 |
| --- | --- |
| H1型の住所/氏名/電話番号をformとして指定 | 独立3field、正しい直接ラベルと局所親、recordSectionなし |
| 同じ値をformとfields、またはrecordsとfieldsへ重複宣言 | 両側を保留。先勝ちなし |
| T1型の途中で局所親が変わる区間 | 明示した区間/fieldの親を保持。名前集合だけで合格にしない |
| fields.name=nullでラベル文字なし、または長さ超過 | 当該field保留。親名や既存値から補充なし |
| 正しいsymbol/checklist定義を複数値で共有 | 選択肢順と各値の固有binding保持、mark関係のmanual欠落なし |
| m参照の重複・未知参照、古いsnapshot、範囲外write | 応答拒否、draft変更なし |
| recordsの最初/途中/末尾空行、row_label、context | 行保持・行別labels保持・context非所有 |
| 表の不正と独立fieldの正常が同居 | 表と従属contextを保留、正常fieldのみ利用可能 |
| 手動同値parent/meaning/role、mark解除後の遅着 | 上書き/再生成しない |
| complete前のJSON、refusal、timeout、上限到達 | 適用なし、API予約は戻さない |

Phase13の保存20応答・63境界を同じ入力で再確認し、新しいform・圧縮・scopeの差分境界を短いオフライン検証で追加する。大量unitや全E2Eはこの製品外比較で行わない。
