# 保存ページ上での区分・意味づけ・矩形編集

XLSX由来のテンプレート下書きで、保存した印刷ページ（横1ページ×縦Nページ）ごとに区分・意味づけ・結合・削除を手で直す編集の仕様。
新しい境界モデルや自動分類は導入せず、TemplateVersionの`quality_json.printLayout.pages`をそのまま使用する。
印刷ページの境界の決め方は[XLSX印刷ページ設計](xlsx-print-layout.md)を参照する。

## 操作

1. 「印刷ページを編集」を開く。保存設定がなければ、原本の合法なmanual rowBreaksを初期候補にする。
2. 行境界を目視確認し「印刷ページを下書き保存」を押す。
3. 書類プレビュー中央の左右にある「前の編集ページ」「次の編集ページ」ボタンでページを切り替える。
4. 枠を単一選択・Ctrl+クリック・範囲選択し、区分変更・完全削除・結合・意味づけで手動修正する。

セル一覧・クリック・範囲選択・意味づけ経路の座標は選択ページに限定する。
編集中の状態は全ページ共通の既存LayoutDefinitionで保持し、ページ切り替えで破棄しない。
描画用の矩形は`page.fields[].cellId`で既存矩形へ対応付ける。セルID、canonical座標、
他ページの区分・関係を、表示座標や配列indexから再生成しない。
選択欄の詳細編集は既存canonical画像と既存相対座標のまま扱う。
完全削除は選択ページでも行える。削除した矩形に属するfield・mapping・relationだけを除外し、
残る矩形のセルID・canonical座標、他ページの区分・関係、ページ境界・transformを維持する。
物理枠の追加・任意の位置やサイズの変更はページ表示中に行えない。仮想見出しは下記の専用規則で扱う。
結合・結合解除はページ数によらず各ページ内で利用できる。元セルIDとcanonical座標を
cellMerges.partsに保持し、結合親の矩形はpartsの外接矩形とする。
保存前の表示はpartsの明示IDに対応するページ矩形から生成する。保存済みfieldの
sourceParts（cellId・sourceRange）とページ原本セルの矩形により、再読込後の解除も表示できる。
ページ境界をまたぐ結合、欠落・重複のある元Excel範囲、完全非表示の部品、元セル座標改変は拒否する。
意味づけ・繰り返しに使用中のセルは接続を外してから結合・解除する。
XLSXでは既存取込処理の区分を保持し、読込時の隣接位置による見出し候補の自動降格を行わない。
目視編集していないページの区分を保存時に変えない。既存の画像テンプレートの正規化は維持する。

## 非表示・縮小文字の下書き表示

`read_source(..., draft_support=True)`は非表示行列の幅・高さを0とし、完全に非表示の
原本セルを描画しない。既存矩形が完全に非表示なら黙って除外せず拒否する。
原本の結合範囲、手動改ページ、セル値・書式は変更しない。
セル内縮小はPillow参考画像で文字列をセル内へ収める。回転文字は引き続き拒否する。

新しい下書きでこれらを使用する場合、表示モデルを`draft-visible-cells-v1`とする。
取込時の`spreadsheetPreviewGeometry`があれば、その原本SHA・シート・保存済み寸法を検証して使用する。
ない場合は従来の列幅換算による参考寸法を使用する。保存した下書きの寸法方式を後から変更しない。
既存のlegacyモデルと`lo-import-twip-v1`保存版は、保存済みの寸法方式を維持する。
Excelのフォント・組版再現や本番OCRの座標保証を意味しない。

## 保存APIと安全境界

旧layoutで省略された `shapeType=rectangle`、`vertices=[]`、`virtual=false` は、
ページ編集の保存前比較で既定値と同値に扱う（通常セルと既存結合parts）。
保存済みJSONを読み取り時に移行したり、revision / layout SHA照合を迂回したりしない。
明示的な形状変更・既存結合情報の変更は従来どおり拒否する。

保存は共通のauthoring APIを使用する。

`PUT /api/v1/template-versions/{id}/authoring?expectedRevision=...`

bodyはlayout（既存LayoutDefinition）、documentIdentityFields、documentName。
DRAFT、TemplateVersionの整数revision、保存planのlayout SHAを検証する。
通常矩形と結合partsへ展開した元セル集合は、重複のない既存集合の部分集合だけを許可する。
残存元セルの座標・形状を維持し、物理セルの追加IDは検証済み結合親と保存partsの復元だけに限定する。
元XLSXからの範囲解決・出力結合の連続矩形検証・内部値保護と、保存済みページ境界による
page_planの事前検証をDB/config保存前に実行する。任意の構造変更を許可するものではない。
LayoutService.prepare_layoutで検証し、TemplateAuthoringServiceで手動編集内容・設定参照・
品質・履歴を同時保存する。同じページ境界でfields/bindingsを更新する。
原本セル画像・transformを再利用し、TemplateVersionとその編集用PREPARE snapshotのみ更新する。
受付済みOCR job、公開版、他versionは変更しない。物理枠追加・任意の位置やサイズの変更・競合は409で拒否する。
不正な結合範囲・ページ横断は409または422で拒否し、保存前のlayout・config・planを保持する。
保存失敗時はフロントの編集状態を保持する。保存planのlayout SHAが現在のlayoutと異なる場合は
staleとして拒否する。競合後に最新revisionへ暗黙に載せ替えて再送しない。

本番rendererの`environmentQualified` / `productionQualified`、ACTIVE化・paged OCR受付の条件、
reference qualificationのモデル照合、Office adapterのhidden行列拒否は、この編集では変えない。
このため目視編集できる帳票でも、印刷用XLSX/PDFの厳格なadapterでは未対応となる場合がある。

## ページ上の仮想見出し

区分・意味づけ・繰り返しの各ステップで「仮想見出し」を選び、プレビュー上の配置位置をクリックする。
表示された矩形へ名称を直接入力し、Enterまたは仮想見出し外のクリックで配置を確定する。
配置済みの仮想見出しをクリックした場合も、同じ矩形内入力で名称だけを変更する。
配置後の囲い込みは、現在ページの表示座標と表示中のセルIDだけを使用する。
既存の見出し接続で階層化・繰り返し群への接続もできる。所属ページを変更する操作ではない。
ハンドボタンを選択した場合だけ、そのページの仮想見出しをドラッグ移動できる。
通常のExcel由来の矩形はハンド操作でも動かない。モード切替のチュートリアルは表示しない。

`LayoutCell.pageRange` は仮想見出しに限る任意metadataで、保存ページのrangeに一致する。
このタグのあるセルのx/y/width/heightはページ内の表示座標であり、canonical OCR座標ではない。
未指定の場合は従来形式を維持し、nullキーを旧セルへ追加しない。
仮想見出しはpage.fieldsや元セル集合に追加せず、OCR入力・出力先を生成しない。
ページ切替は未確定の矩形内入力・囲い込みを解除するが、配置済み見出しと接続は全ページの下書きに保持する。
保存・再読込・版コピーでも所属rangeと接続を保持し、版コピー時のID分離は従来経路を使用する。

仮想見出しのページ外配置、元Excel ID・既存物理セルIDの流用、結合部品への混入を拒否する。
通常layout APIでも同じ検証を行う。所属rangeが消える改ページ変更・ページ設定解除や、
用紙縮小で範囲外になる変更は `PRINT_VIRTUAL_HEADING_PAGE_INVALID` で保存前に拒否する。
対象の見出しを明示的に削除してからページ設定を変更し、必要な位置に再作成する。
意味づけを保持する場合は、印刷ページ編集の「仮想見出しの所属ページ」で
各見出しの移動先rangeを明示し、改ページと一緒に保存できる。空欄は所属を変更しない。
既存ID・名称・位置・接続を保ち、変更前rangeとrevisionを検証する。
保存失敗では入力を保持し、未知ID・物理セル・重複ID・存在しない範囲・配置範囲外を拒否する。
見出しを自動的に他ページへ移す・消す・通常のOCR枠へ変換する処理はない。
テンプレートパッケージはprintLayoutを持たない（書出しは`PRINT_LAYOUT_PACKAGE_UNSUPPORTED`で拒否する）ため、ページ仮想見出しだけを混入させるimportも拒否する。
従来のページ所属を持たない仮想見出しのパッケージ互換性は維持する。

## 結合・解除の境界

結合・解除は、LayoutEditor/pageAuthoring、print_layout_source/service、
page_authoring_validationと既存XLSX出力の元範囲検証入口で扱う。page fieldは任意のsourcePartsを持つ。
元セル座標・既存snapshotの形式を移行せず、結合親だけに意図した新ID・外接矩形を作る。
provider/model/prompt/schema/association、registration、crop計算、補正/確定処理は変えない。
ページ横断、欠落セル、親/parts座標改変、parts重複はlayout/config/plan不変のまま拒否する。
従来の結合パターンCtrl+C/Vはページ表示中には使えず、該当案内を表示しない。

## 実装の場所

- backend: `app/api/v1/routes/print_layout.py`、`app/services/draft_page_geometry.py`、
  `app/services/print_layout_source.py`、`app/services/print_layout_renderer.py`、
  `app/services/print_layout_service.py`、`app/services/page_authoring_validation.py`
- backend tests: `tests/unit/test_draft_page_authoring.py`
- frontend: `src/api.ts`、`src/pageAuthoring.ts`、`src/components/LayoutEditor.tsx`、
  `src/components/PrintLayoutEditor.tsx`、`src/pages/TemplatePage.tsx`
- 実帳票での確認データ: [page-authoring-evidence.json](page-authoring-evidence.json)
