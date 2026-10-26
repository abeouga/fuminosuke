/**
 * 帳票ビュー: the readings laid over the aligned form image, each at its own field, so the
 * screen looks like the paper. Display and the same edits as the list (select, type in place,
 * check boxes); the readings, their 要確認 and what is saved do not change.
 *
 * A cell is placed by its template-coordinate box as a percentage of the page, so no
 * measuring is needed; its text is sized from the box height in container units (cqw).
 */
import { useContext, useEffect, useLayoutEffect, useRef, useState, type CSSProperties } from "react";
import { FiArrowRight, FiFileText, FiList } from "react-icons/fi";
import { privateLocalStorage } from "../auth/transport";
import { InlineEditActivity, ReviewChecklists, ReviewChoiceLabels, choiceName, choiceText,ReviewPaint, ReviewSeriesMarks, readingChoices, reviewEntryContext, type ReadingEntry, type ReviewDisplayModel } from "./OcrReviewResults";
import { useAiDraggable } from "./ReviewAiDrag";

export type ReviewView = "list" | "form";
const VIEW_KEY = "fuminosuke.reviewView";
/** The view chosen last time in this browser (the list until 帳票 is chosen once). */
export function readStoredReviewView(): ReviewView {
  try { return privateLocalStorage.getItem(VIEW_KEY) === "form" ? "form" : "list"; } catch { return "list"; }
}
export function storeReviewView(view: ReviewView) {
  try { privateLocalStorage.setItem(VIEW_KEY, view); } catch { /* display only */ }
}
export const reducedMotion = () => window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;

/** 一覧 | 帳票 on the page title row. */
export function ReviewViewSwitch({ view, disabled, onChange }: { view: ReviewView; disabled?: boolean; onChange: (view: ReviewView) => void }) {
  const option = (value: ReviewView, label: string, icon: React.ReactNode, title: string) => (
    <button type="button" role="radio" aria-checked={view === value} className={view === value ? "active" : ""} title={title} disabled={disabled} onClick={() => onChange(value)}>{icon}{label}</button>
  );
  return <span className="review-view-switch review-mode-switch" role="radiogroup" aria-label="結果の表示">
    {option("list", "一覧", <FiList aria-hidden="true" />, "結果を表と項目の一覧で確認")}
    {option("form", "帳票", <FiFileText aria-hidden="true" />, "帳票の上の、それぞれの欄の位置で確認")}
  </span>;
}

/** A mouse with its right button pressed: holding the right button shows the photo. */
function RightClickIcon() {
  return <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
    <rect x="6" y="3" width="12" height="18" rx="6" />
    <path d="M12 3v7M6 10h12" />
    <path d="M12 3.6a6 6 0 0 1 6 6V10h-6z" fill="currentColor" stroke="none" />
  </svg>;
}

/** 「写真を見る」: held down (or the right mouse button held over the form), every cell is clear. */
export function ReviewFormReveal({ revealed, onReveal }: { revealed: boolean; onReveal: (on: boolean) => void }) {
  return <button type="button" className={`review-form-reveal${revealed ? " active" : ""}`} aria-pressed={revealed}
    title="押している間、読み取った値を外して写真を見る（帳票の上で右クリックを押している間も同じ）"
    onPointerDown={event => { if (event.button === 0) { event.preventDefault(); onReveal(true); } }} onPointerUp={() => onReveal(false)} onPointerLeave={() => onReveal(false)} onPointerCancel={() => onReveal(false)}
    onKeyDown={event => { if (event.key === " " || event.key === "Enter") { event.preventDefault(); onReveal(!revealed); } }}><RightClickIcon />写真を見る</button>;
}

/** The check pen and 「次の要確認」 of 帳票 mode (the list's toolbar is closed with the list). */
export function ReviewFormActions({ confirmed, warningCount, onNextWarning }: {
  confirmed: boolean; warningCount: number; onNextWarning: () => void;
}) {
  if (confirmed) return null;
  return <div className="review-form-actions" role="toolbar" aria-label="帳票ビューの操作">
    <button type="button" className="secondary compact" disabled={warningCount === 0} onClick={onNextWarning}><FiArrowRight aria-hidden="true" />次の要確認（{warningCount}件）</button>
  </div>;
}

export function ReviewFormView({ src, entries, display, selectedPath, warningPaths, choicePaths, confirmed, editable, revealed, shown, leaving, onSelect, onEdit, onReveal }: {
  src: string;
  entries: readonly ReadingEntry[];
  display: ReviewDisplayModel;
  selectedPath: string | null;
  warningPaths: ReadonlySet<string>;
  choicePaths: ReadonlySet<string>;
  /** Values cannot be changed (confirmed, busy, conflict). */
  confirmed: boolean;
  /** Typing in place and the check boxes work (記入欄 mode and not locked). */
  editable: boolean;
  revealed: boolean;
  /** The cells fade in once the list has closed; `leaving` fades them out again. */
  shown: boolean;
  leaving: boolean;
  onSelect: (entry: ReadingEntry) => void;
  onEdit: (entry: ReadingEntry, text: string) => void;
  /** Holding the right mouse button over the form shows the photo under every cell. */
  onReveal: (on: boolean) => void;
}) {
  const image = useRef<HTMLImageElement>(null), viewport = useRef<HTMLDivElement>(null);
  const rightHeld = useRef(false);
  useEffect(() => {
    const release = () => { if (rightHeld.current) { rightHeld.current = false; onReveal(false); } };
    window.addEventListener("pointerup", release);
    window.addEventListener("pointercancel", release);
    window.addEventListener("blur", release);
    return () => { release(); window.removeEventListener("pointerup", release); window.removeEventListener("pointercancel", release); window.removeEventListener("blur", release); };
  }, [onReveal]);
  const [size, setSize] = useState<{ width: number; height: number } | null>(null);
  const measure = () => { const img = image.current; if (img?.naturalWidth) setSize({ width: img.naturalWidth, height: img.naturalHeight }); };
  useLayoutEffect(measure, [src]);
  // 「次の要確認」 and the AI's selection bring a cell outside the frame into view.
  useEffect(() => {
    if (!selectedPath || !size) return;
    const cell = [...(viewport.current?.querySelectorAll<HTMLElement>("[data-review-path]") ?? [])].find(e => e.dataset.reviewPath === selectedPath);
    const frame = viewport.current;
    if (!cell || !frame) return;
    // The frame scrolls first (instantly, so nothing that follows can cut it short); the
    // window only when the frame's own visible part still leaves the cell out.
    let box = cell.getBoundingClientRect();
    const view = frame.getBoundingClientRect();
    const visibleTop = Math.max(view.top, 0), visibleBottom = Math.min(view.bottom, window.innerHeight);
    if (box.top < visibleTop || box.bottom > visibleBottom) frame.scrollTop += box.top + box.height / 2 - (visibleTop + visibleBottom) / 2;
    if (box.left < view.left || box.right > view.right) frame.scrollLeft += box.left + box.width / 2 - (view.left + view.right) / 2;
    box = cell.getBoundingClientRect();
    if (box.top < 0 || box.bottom > window.innerHeight) cell.scrollIntoView({ block: "center", inline: "nearest", behavior: "auto" });
  }, [selectedPath, size]);
  const choiceLabels = useContext(ReviewChoiceLabels);
  const placed = entries.filter(entry => entry.value.boundingBox), loose = entries.filter(entry => !entry.value.boundingBox);
  return <div className={`review-form-view${leaving ? " is-leaving" : ""}${revealed ? " is-revealed" : ""}`}
    onPointerDown={event => { if (event.button === 2) { rightHeld.current = true; onReveal(true); } }}
    onContextMenu={event => event.preventDefault()}>
    <div ref={viewport} className="review-form-viewport">
      <div className="review-form-page">
        <img ref={image} src={src} alt="読み取った帳票全体" draggable={false} onLoad={measure} />
        {size && <div className={`review-form-cells${shown && !leaving ? " is-shown" : ""}`}>
          {placed.map(entry => <FormCell key={entry.path} entry={entry} page={size} selected={entry.path === selectedPath} warning={warningPaths.has(entry.path)} choice={choicePaths.has(entry.path)}
            confirmed={confirmed} editable={editable} context={reviewEntryContext(display, entry)} onSelect={onSelect} onEdit={onEdit} />)}
        </div>}
      </div>
    </div>
    {loose.length > 0 && <div className="review-form-loose"><span>帳票上の位置がない欄</span>{loose.map(entry => {
      const value = entry.value.normalized ?? entry.value.raw;
      return <button type="button" key={entry.path} data-review-path={entry.path} className={`${warningPaths.has(entry.path) ? "ocr-cell-warning" : "ocr-cell-ok"}${entry.path === selectedPath ? " ocr-cell-selected" : ""}`} onClick={() => onSelect(entry)}>
        <small>{reviewEntryContext(display, entry)}</small><span>{(value && choiceText(value, choiceLabels.get(entry.path))) || "空欄"}</span></button>;
    })}</div>}
  </div>;
}

function FormCell({ entry, page, selected, warning, choice, confirmed, editable, context, onSelect, onEdit }: {
  entry: ReadingEntry; page: { width: number; height: number }; selected: boolean; warning: boolean; choice: boolean;
  confirmed: boolean; editable: boolean; context: string; onSelect: (entry: ReadingEntry) => void; onEdit: (entry: ReadingEntry, text: string) => void;
}) {
  const box = entry.value.boundingBox!;
  const value = entry.value.normalized ?? entry.value.raw;
  const checklistLabels = useContext(ReviewChecklists).get(entry.path);
  const choiceLabels = useContext(ReviewChoiceLabels).get(entry.path);
  const painted = useContext(ReviewPaint).has(entry.path);
  const marks = useContext(ReviewSeriesMarks);
  const added = marks?.added.has(entry.path) ?? false, carried = marks?.carried.has(entry.path) ?? false;
  const hasChoices = !confirmed && !choice && readingChoices(entry.value).length > 0;
  const [editing, setEditing] = useState(false), [editValue, setEditValue] = useState(value ?? "");
  const reportInlineEdit = useContext(InlineEditActivity);
  const drag = useAiDraggable(`cell:${entry.path}`, { kind: "cell", path: entry.path }, editing), { ref: dragRef, ...dragListeners } = drag.handle;
  useEffect(() => {
    reportInlineEdit(entry.path, editing && editValue !== (value ?? ""));
    return () => reportInlineEdit(entry.path, false);
  }, [entry.path, editing, editValue, value, reportInlineEdit]);
  useEffect(() => { if (!editing) setEditValue(value ?? ""); }, [editing, value]);
  const commit = () => { if (!editing || !editable) return; setEditing(false); onEdit(entry, editValue); };
  const text = (value && choiceText(value, choiceLabels)) || (entry.value.status === "blank" && choice ? "-" : "");
  // The print is sized from the box height, and shrinks so the whole value fits its width
  // (a full-width character counts as 1em, ASCII as 0.6em). While typing, the text being typed
  // decides it, so the value is edited in place exactly as it will be shown.
  const shown = editing ? editValue : text;
  const units = Math.max(1, [...(shown.split("\n")[0] ?? "")].reduce((sum, ch) => sum + (ch.charCodeAt(0) < 0x3000 ? 0.6 : 1), 0));
  const style = {
    left: `${(box.x / page.width) * 100}%`, top: `${(box.y / page.height) * 100}%`,
    width: `${(box.width / page.width) * 100}%`, height: `${(box.height / page.height) * 100}%`,
    "--cell-h": `${(box.height / page.width) * 100}`, "--cell-w": `${(box.width / page.width) * 100}`, "--len": `${units}`,
  } as CSSProperties;
  // A check box is never 「空欄」: unchecked is an answer too.
  const tone = warning ? "warning" : value || checklistLabels ? "ok" : "blank";
  return <div className={`review-form-cell is-${tone}${checklistLabels ? ` is-check${checklistLabels.length > 1 ? " is-check-list" : ""}` : text ? "" : " is-empty"}${selected ? " is-selected" : ""}${painted ? " is-painted" : ""}${added ? " is-added" : ""}${carried ? " is-carried" : ""}${hasChoices ? " has-choices" : ""}${drag.dragging ? " is-ai-dragging" : ""}${editing ? " is-editing" : ""}`}
    data-review-path={entry.path} style={style} title={`${context}\n${checklistLabels ? (value ? `チェックあり${checklistLabels.length > 1 ? `（${value}）` : ""}` : "チェックなし") : text ? value : "（空欄）"}`} {...drag.holder}>
    {checklistLabels
      ? <FormChecklist labels={checklistLabels} entry={entry} context={context} editable={editable} onEdit={onEdit} />
      : editing
        // The same box, font and size as the printed value: only a caret appears in it.
        ? <input className="review-form-editor" autoFocus aria-label={`${entry.label}を直接編集`} value={editValue} spellCheck={false}
            onFocus={event => { const end = event.currentTarget.value.length; event.currentTarget.setSelectionRange(end, end); }}
            onChange={event => setEditValue(event.target.value)} onBlur={commit}
            onKeyDown={event => {
              if (event.key === "Enter" && !event.nativeEvent.isComposing) { event.preventDefault(); commit(); }
              if (event.key === "Escape") { event.preventDefault(); setEditing(false); }
            }} />
        : <button type="button" ref={dragRef} {...dragListeners}
            aria-label={`${context}：${value || (entry.value.status === "blank" ? (choice ? "未選択" : "空欄") : "未入力")}。${confirmed ? "画像を確認" : "選択して確認・編集"}`}
            onClick={() => onSelect(entry)}
            onDoubleClick={event => {
              if (!editable) return;
              event.preventDefault();
              onSelect(entry);
              if (choice) return;
              setEditValue(value ?? "");
              setEditing(true);
            }}><span>{text}</span></button>}
    {/* 要確認 and 候補 outrank the 追加 tag of an update's newly read field. */}
    {(warning || hasChoices || added) && !editing && <i className={`review-form-flag${!warning && !hasChoices ? " is-added" : ""}`} aria-hidden="true">{warning ? "要確認" : hasChoices ? "候補" : "追加"}</i>}
  </div>;
}

/** Checked items of a checklist reading (an undecided one counts what was read). */
function checkedItems(entry: ReadingEntry): Set<string> {
  const text = entry.value.status === "blank" ? "" : (entry.value.normalized ?? entry.value.raw ?? "");
  return new Set(text.split("・").map(item => item.trim()).filter(Boolean));
}

const CheckMark = () => <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12.5l4.5 4.5L19 7.5" /></svg>;

/**
 * Check boxes drawn on the printed box: a lone box fills its cell (the cell is the printed
 * square), several items line up as small boxes with their names. A click toggles, as in the list.
 */
function FormChecklist({ labels, entry, context, editable, onEdit }: {
  labels: readonly string[]; entry: ReadingEntry; context: string; editable: boolean; onEdit: (entry: ReadingEntry, text: string) => void;
}) {
  const checked = checkedItems(entry);
  const toggle = (label: string) => {
    if (!editable) return;
    const next = new Set(checked);
    if (next.has(label)) next.delete(label); else next.add(label);
    onEdit(entry, labels.filter(item => next.has(item)).join("・"));
  };
  const box = (label: string, name: string, children?: React.ReactNode) => {
    const on = checked.has(label);
    return <button type="button" key={label} role="checkbox" aria-checked={on} aria-label={name} disabled={!editable}
      className={`review-form-check${on ? " is-on" : ""}`} title={editable ? (on ? "クリックでチェックを外す" : "クリックでチェックを付ける") : undefined}
      onClick={() => toggle(label)}><i><CheckMark /></i>{children}</button>;
  };
  return labels.length === 1
    ? box(labels[0], context)
    : <span className="review-form-checks">{labels.map(label => box(label, `${context} ${choiceName(label, labels)}`, <span>{choiceName(label, labels)}</span>))}</span>;
}
