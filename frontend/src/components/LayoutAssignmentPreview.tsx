import { FiCheck, FiGrid, FiMousePointer } from "react-icons/fi";
import type { FieldMappingTarget, LayoutCell, LayoutDefinition } from "../types";
import { recordSectionDisplayName } from "./layoutEditorRecords";

/** Read-only view of saved assignments. Selection never creates a binding. */
export function LayoutAssignmentPreview({ layout, targets, visibleCells, selectedId, expanded, onSelect }: {
  layout: LayoutDefinition;
  targets: FieldMappingTarget[];
  visibleCells: LayoutCell[];
  selectedId: string | null;
  expanded: boolean;
  onSelect: (cell: LayoutCell) => void;
}) {
  const cells = new Map(layout.cells.map(cell => [cell.id, cell]));
  const visible = new Map(visibleCells.map(cell => [cell.id, cell]));
  const names = new Map(targets.map(target => [target.path, target.label]));
  const groups = layout.recordGroups ?? [];
  const grouped = new Set(groups.flatMap(group => group.inputCellIds));
  const fields = layout.cells.filter(cell => cell.role === "input" || cell.role === "mark");
  const pathsFor = (id: string) => [
    ...layout.relations.filter(relation => relation.inputCellId === id).map(relation => relation.targetPath),
    ...(cells.get(id)?.markSets ?? []).map(mark => mark.targetPath),
  ].filter(Boolean);
  const fieldButton = (cellId: string, label: string) => {
    const cell = visible.get(cellId);
    const paths = pathsFor(cellId);
    return <button type="button" key={cellId} disabled={!cell} aria-pressed={selectedId === cellId}
      aria-label={`${label}の記入欄を表示`} className={paths.length ? "assignment-bound" : "assignment-missing"}
      onClick={() => { if (cell) onSelect(cell); }}>
      {paths.length ? <FiCheck aria-hidden="true" /> : <FiMousePointer aria-hidden="true" />}
      <span>{paths.length ? paths.map(path => names.get(path) ?? "設定済みの項目").join(" / ") : "保存先未設定"}</span>
      {!cell && <small>別ページ</small>}
    </button>;
  };
  return <details className="assignment-preview" open={expanded}>
    <summary><FiGrid aria-hidden="true" /><strong>設定内容プレビュー</strong><span>{fields.filter(cell => pathsFor(cell.id).length > 0).length} / {fields.length}欄に保存先を設定</span></summary>
    <p>項目を押すと対応する帳票の欄を選択します。表示は現在の設定です。未設定の欄は自動で補いません。</p>
    {(layout.recordSections ?? []).map(section => {
      const title = recordSectionDisplayName(section, cells);
      const rows = groups.filter(group => group.sectionId === section.id).sort((a, b) => a.order - b.order);
      return <section className="assignment-record" key={section.id} aria-label={`${title}の設定結果`}>
        <h4>{title}<span>{rows.length}行{section.recordLabelCellIds.length > 0 ? ` · ${section.recordLabelCellIds.length}列` : " · 列ラベル未設定"}</span></h4>
        {section.recordLabelCellIds.length === 0 && <p>列ラベルが未設定のため、保存済みの行ごとに項目を表示しています。</p>}
        <div className="assignment-table-scroll" role="region" tabIndex={0} aria-label={`${title}の行と列`}>
          <table><thead><tr><th scope="col">行</th>{section.recordLabelCellIds.map((id, index) => <th scope="col" key={id}>{cells.get(id)?.text.trim() || `列 ${index + 1}`}</th>)}{section.recordLabelCellIds.length === 0 && <th scope="col">この行に設定された項目</th>}</tr></thead>
            <tbody>{rows.map((row, rowIndex) => <tr key={row.id}><th scope="row">{rowIndex + 1}</th>
              {section.recordLabelCellIds.length === 0 && <td><div className="assignment-row-fields">{row.inputCellIds.map((id, index) => fieldButton(id, `${title} ${rowIndex + 1}行目の項目${index + 1}`))}</div></td>}
              {section.recordLabelCellIds.map((labelId, columnIndex) => {
                const ids = row.inputCellIds.filter(inputId => layout.relations.some(relation =>
                  relation.inputCellId === inputId && relation.labelCellIds.includes(labelId)));
                const label = cells.get(labelId)?.text.trim() || `列 ${columnIndex + 1}`;
                return <td key={labelId}>{ids.length
                  ? ids.map(id => fieldButton(id, `${title} ${rowIndex + 1}行目 ${label}`))
                  : <span className="assignment-unlinked">列との接続なし</span>}</td>;
              })}</tr>)}</tbody>
          </table>
        </div>
      </section>;
    })}
    <div className="assignment-independent" aria-label="個別項目の設定結果">
      {fields.filter(cell => !grouped.has(cell.id)).map((cell, index) => {
        const relation = layout.relations.find(item => item.inputCellId === cell.id);
        const label = relation?.labelCellIds.map(id => cells.get(id)?.text.trim()).filter(Boolean).join(" / ")
          || cell.printedLabelText || cell.text.trim() || `記入欄 ${index + 1}`;
        const parent = relation?.parentLabelCellId ? cells.get(relation.parentLabelCellId)?.text.trim() : null;
        return <div key={cell.id}><span>{parent ? `${parent} › ${label}` : label}</span>{fieldButton(cell.id, parent ? `${parent} ${label}` : label)}</div>;
      })}
    </div>
    {fields.length === 0 && <p>記入欄を設定すると、ここに対応する項目を表示します。</p>}
  </details>;
}
