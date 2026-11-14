# テンプレート手動編集の拡張

テンプレート編集画面（LayoutEditor）で、同名項目・多角形・見出し階層・繰り返し群の名称・仮想見出しを手で扱う機能と、その表示・操作の仕様。

## 見出し接続線のホバー表示

見出し階層、relationの親接続、繰り返し群への親接続は通常時に線を表示しない。
見出し・接続項目・繰り返し帯のホバー中だけ直接関係する線を表示し、選択状態だけでは残さない。
重複するcell/relationの同一親接続は1本にまとめて描画する。元の接続データは統合しない。
斜めの線は緩やかな3次ベジェ曲線、水平・垂直の線は直線とする。
接続作成中のガイドと、消しゴムのホバー・ドラッグ中の対象線も同じ曲線を使用する。
消しゴムの当たり判定は曲線に追従し、従来の斜め直線上だけでは接続を消さない。
ページ変更・ツール変更・カーソル離脱でホバー表示を解除する。繰り返し帯そのものの表示は維持する。

見出し接続線のホバー表示は保存形式、接続作成規則、cell ID・targetPath・OCR領域を変えない。
ホバーでdirty状態・保存要求は発生しない。

## 結合前の矩形の重ね描き

通常・ページプレビューとも、結合親の外枠と既存の「結合」バッジを表示し、
結合前の各partsを破線で重ね描きしない（ページ表示でpartsのcanonical座標をそのまま描くと、位置がずれた矩形に見えるため）。
`cellMerges.parts`の保存情報、結合・解除・選択・意味づけ・OCRの処理は変更しない。
元の帳票画像に含まれる罫線は保持する。

## 手動の項目追加・移動・接続と区分変更

- 手動項目追加と項目名の文字候補の手編集では、前後の入力空白を除いた項目名の完全一致だけで
  登録済みと判定する。部分一致・aliases・NFKC・大小文字・OCR誤字補正では吸収しない。
  保存キーから記号を除いた結果が衝突するときは、新項目に別キーを採番する。
  OCRからの自動候補照合は従来どおりとし、手動追加の照合と分離する。
- ハンドアイコンを押した場合だけCanvasの枠をドラッグ移動できる。説明文・チュートリアル・
  toastは出さず、ボタンの選択状態と手形カーソルで示す。もう一度押すと通常選択へ戻る。
  ドロップまで保存データを変えず、Escape / pointer cancelでは変更しない。
  矩形・多角形・仮想見出し・結合枠のIDと意味づけを保ち、多角形の頂点と結合partsも平行移動する。
  所属行の外接矩形は明示的なメンバーIDから更新する。元Excelセルの移動や並べ替えではない。
  現在のXLSXページ投影画面では元Excelセルの任意座標保存を許可しない。
  手動の多角形追加は未対応だが、仮想見出しはページ専用規則により追加・移動できる。
- ステップ2・3の見出し接続は繰り返し群（列上または帯の線上）を認識する。
  群のparentLabelCellIdを設定し、先端の1列だけに親接続を付けない。既存の同一collectionの
  複数帯連動を保持し、別collectionは変えない。見出し同士のネストと群を含む循環拒否を共用する。
  relation ID・targetPath・行所属・セル座標は親接続によって再生成しない。
- 区分変更と意味づけ：`LayoutEditor.setRoles`は項目名から別区分へ変えたセルを
  relation.labelCellIdsから外すが、記入欄のrelationとtargetPathは保持する。
  結果は「ラベルなしの直接意味づけ」であり、保存・再取得・mapping生成でも残る。
  見出しへ意味づけが移るわけではない。記入欄自身を見出しに変える場合はinputCellIdのrelationを除く。

### 同名項目

### 同名項目

意味づけの作成時に現在のLayoutDefinition内で保存先の使用状況を確認する。
別の記入欄ですでに使用している単独項目には、新しいlabelPool項目と保存先を作成する。
例は `漢字 → 漢字2 → 漢字3`、保存・再読込後の次は `漢字4`。
既存の帳票文字 `text` / `printedLabelText`、既存relationのID・targetPathは変更しない。
別テンプレートの候補は連番の根拠にしない。既存の繰り返し配列の行割当ては維持する。
保存時の既存重複修復ロジックに依存して、古い項目をリネームする方式ではない。

複数の記入欄へ一括接続する経路でも、`createAutoRepeatedLabelPoolEntries`へ現在の使用済み保存先を渡し、
新しい組だけ別のキーと表示名（例: `備考2 1行目`）を採番する（同名の保存先を再利用して2組目の接続が競合しないようにするため）。
既存の接続・セル・原文は変更しない。既存の繰り返し配列の行割当ても変更しない。

### 多角形

「欄の種類」→「枠を追加」で四角形／多角形を選択する。初期値は四角形。
頂点を順番にクリックし、始点クリックまたは「多角形を確定」で閉じる。
最後の頂点の取消し、作図キャンセル、Escapeによる終了が可能。

LayoutCellは任意の `shapeType` とcanonical座標の `vertices` を持つ。
旧データの省略値はrectangleと空の頂点列。外接矩形と頂点範囲の一致、
3〜100頂点、重複・自己交差・面積0の拒否をフロント／バック双方で検証する。
クリックはpoint-in-polygonで判定する。描画・区分変更・意味づけ・削除・
保存・再読込に対応する。

**通常の単一ページOCRまで対応する。** 頂点はmapping API、保存config、受付snapshot、
RegionConfigへ引き継ぎ、既存RegionCropperの切り出し後に領域外を白くする。
ink_differenceだけは領域外を0にする。印刷ラベル文字候補OCRも同じ形状でマスクする。
矩形では従来の切り出しをそのまま使用し、config/snapshotに空のverticesを追加しない。
既存画像や隣接セル、providerのmodel・prompt・association処理は変更しない。

同じbboxで矩形と多角形を切り出した比較（青線は説明用の重ね描きで、入力画像には含めない）。

![矩形と多角形の切り出し比較](images/template-authoring-crop.png)

### 見出し階層

「読み取る内容」または「繰り返し部分」の「親項目を接続」で、
見出しから子の見出し・項目名・記入欄・選択欄へ接続できる。
役割は既存の `parent_label` のまま。LayoutCellの `parentLabelCellId` に接続を保存し、
意味づけ済みの項目名では既存relationの親情報も整合させる。
新しい接続でtargetPath・relation ID・cell ID・mark sets・結合を作り直さない。
既存の親relationも参照して自己参照と循環を拒否し、単一親を維持する。
消しゴム・意味づけを戻す操作でもセルとrelationの親情報を整合させる。

### 繰り返し群の任意名称

「繰り返し部分をまとめる」で列ラベルを接続しても、名称入力を自動表示しない。
接続済みの帯を単クリックすると列ラベル群を選択し、帯上のセルをダブルクリックすると
そのセルだけを選択する。選択した繰り返し群の右サイドバーで「名前を付けない」または
「名前を付ける」を選び、名前を付ける場合だけ入力欄を表示する。

名前を付けない場合は `displayName=null` とし、`parentLabelCellId` の帳票見出しがあれば
その見出し名を画面上の識別に使用する。見出しもなければ名称なしとして表示する。
既存の名称、section ID、collectionPath、列ラベル・行グループ、targetPath、mappingは変更しない。
旧データで `displayName` 自体が未定義の場合は、従来のtitle表示を維持する。

### 仮想見出し

「仮想見出し」ツールで設置位置をクリックし、表示された矩形へ名前を直接入力する。
Enterまたは仮想見出し外のクリックで名前と配置を確定し、ツールバーには名称フォームと配置ボタンを置かない。
配置済みの仮想見出しをクリックすると同じ矩形内入力を再表示し、既存ID・位置・親子関係を保持したまま名称だけを変更する。
配置された見出しからドラッグして対象を囲う。項目名・記入欄・選択欄だけが直接の子になる。
他の見出しは囲い込みに含めず、通常の親項目接続で接続する。

仮想見出しは `virtual=true`、`source=manual`、`role=parent_label` の名前付きノード。
配置用の座標は持つが、見出し自身のfield mappingを作らず、ラベル文字候補OCRからも除外する。
囲った範囲は選択手段であり、確定後は明示的なcell IDの親子関係を保存する。
現在の論理セル一覧から選ぶため、結合済みセルは1項目として扱い、partsから作り直さない。

## 後方互換性と制限

- DB migrationや過去版・過去jobの一括変換はない。DRAFT制約、版分離、logicalFieldIdの継承を維持する。
- 新metadataはLayoutDefinitionの任意フィールド。パッケージJSON Schema（`template-v2.schema.json`）にも含め、従来の厳密なfield制約を保持する。
- 既存矩形の画像画素とpadding込みbbox、単一ページjobの従来snapshot形式を回帰テストで比較する。
- 多角形の新規追加は従来のプレビューで行う。ページ単位編集では物理枠追加不可の制限を維持する。
  pageDisplayCellsの頂点変換は表示投影のみであり、保存するgeometryやページidentityを変更しない。
- 保存ページ上でも仮想見出しを追加・接続・ハンド移動できる。
  仮想見出しだけにpageRangeとページ内表示座標を保存する。元Excelセルの座標・ID・出力先は動かさない。
  保存・再表示・ページ設定変更の制約は[ページ単位の目視編集](page-authoring.md)を参照。
- 多角形・仮想見出し・新しい見出し接続を含むセルの結合は拒否する。
  先に結合した論理セルを仮想見出しの子にする操作は可能。
- 別形式のAI汎用TemplateDefinitionは新しい形状・明示的階層を保持できないため、
  `GENERIC_DEFINITION_AUTHORING_UNSUPPORTED`で生成・承認による変換を拒否する。
  通常の手動レイアウト保存・mapping OCRとは別の制限である。
- 多段見出しはテンプレート編集・保存で扱う。ReviewPageに多段ツリー表示はない。
  過去OCR結果・補正・confirmed状態は変更しない。
- 外部OCRでの新しい多角形の実帳票での認識精度は測っていない。

## 実装の場所

- UI・型: `frontend/src/components/LayoutEditor.tsx`、`frontend/src/components/layoutAuthoring.ts`、`frontend/src/components/headingConnections.ts`、
  `frontend/src/types.ts`、`frontend/src/pageAuthoring.ts`
- データ・切り出し: `backend/app/domain/polygon.py`、`backend/app/domain/template_config.py`、
  `backend/app/layout_analysis/models.py`、`backend/app/extraction/region_cropper.py`
- 保存・境界: `backend/app/api/v1/routes/templates.py`、`backend/app/services/layout_service.py`、
  `backend/app/services/label_cell_ocr_service.py`、`backend/app/services/template_definition_service.py`
- パッケージ: `backend/app/template_packages/schemas/template-v2.schema.json`
- 契約: [OCR Safety Contract §3.4](ocr-safety-contract.md#34-手動追加した多角形)
