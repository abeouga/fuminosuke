import { forwardRef, useCallback, useImperativeHandle, useRef, type ClipboardEvent, type KeyboardEvent, type MouseEvent } from "react";
import type { AiAttachment } from "../components/ReviewAiDrag";

export type InstructionFieldHandle = {
  /** Puts a cell into the sentence where the caret is (at the end when the field is not focused). */
  insert(chip: AiAttachment): void;
  clear(): void;
  focus(): void;
};
type Props = {
  enabled: boolean; placeholder: string;
  /** `message` is what will be sent; `hasText` says whether any typed words are in it (not only chips). */
  onChange(message: string, hasText: boolean): void;
  onSend(): void; onEscape(): void;
  /** The result path of the chip under the pointer, so the page can show the cell it stands for. */
  onHoverChip(path: string | null): void;
};

const ZWSP = "​";

/**
 * The typed instruction, with dropped cells as mention chips inside the sentence. A chip is sent as
 * 「table row column」, the way the instruction AI names a cell; typing around it reads as one sentence.
 */
export const InstructionField = forwardRef<InstructionFieldHandle, Props>(function InstructionField(props, ref) {
  const root = useRef<HTMLDivElement>(null);
  const latest = useRef(props); latest.current = props;

  const emit = useCallback(() => {
    const field = root.current; if (!field) return;
    const message = serialize(field), hasText = serialize(field, true).trim().length > 0;
    // Chrome leaves a lone <br> behind after the last character is deleted; drop it so the placeholder shows.
    if (!message && field.childNodes.length) field.replaceChildren();
    field.classList.toggle("is-empty", !message);
    latest.current.onChange(message, hasText);
  }, []);

  useImperativeHandle(ref, () => ({
    focus: () => root.current?.focus(),
    clear: () => { root.current?.replaceChildren(); emit(); },
    insert: chip => {
      const field = root.current; if (!field) return;
      field.focus();
      const selection = window.getSelection();
      let range: Range;
      if (selection?.rangeCount && field.contains(selection.anchorNode)) { range = selection.getRangeAt(0); range.deleteContents(); }
      else { range = document.createRange(); range.selectNodeContents(field); range.collapse(false); }
      const node = chipElement(chip), tail = document.createTextNode(ZWSP);
      range.insertNode(node); node.after(tail);
      // The caret goes after the chip (the ZWSP gives it a place to stand at the end of the line).
      range.setStart(tail, 1); range.collapse(true);
      selection?.removeAllRanges(); selection?.addRange(range);
      emit();
    },
  }), [emit]);

  function onKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    event.stopPropagation();
    if (event.key === "Escape") { event.preventDefault(); props.onEscape(); return; }
    if (event.key === "Enter" && !event.nativeEvent.isComposing) {
      event.preventDefault();
      if (event.shiftKey) { document.execCommand("insertLineBreak"); emit(); } else props.onSend();
      return;
    }
    // Backspace right after a chip removes the chip as one unit (its ZWSP comes along).
    if (event.key === "Backspace") {
      const selection = window.getSelection();
      if (!selection?.isCollapsed || !selection.rangeCount) return;
      const range = selection.getRangeAt(0), node = range.startContainer;
      if (node.nodeType === Node.TEXT_NODE && range.startOffset > 0 && node.textContent?.[range.startOffset - 1] === ZWSP
        && node.previousSibling instanceof HTMLElement && node.previousSibling.classList.contains("ai-chip")) {
        event.preventDefault();
        node.previousSibling.remove(); (node as Text).deleteData(range.startOffset - 1, 1);
        emit();
      }
    }
  }
  function onPaste(event: ClipboardEvent<HTMLDivElement>) {
    event.preventDefault();
    document.execCommand("insertText", false, event.clipboardData.getData("text/plain"));
    emit();
  }
  function chipUnder(event: MouseEvent<HTMLDivElement>) {
    return (event.target as HTMLElement).closest<HTMLElement>(".ai-chip")?.dataset.path ?? null;
  }
  return <div ref={root} className="ai-field is-empty" role="textbox" aria-multiline="true" aria-label="AIへの指示"
    contentEditable={props.enabled ? "plaintext-only" : "false"} suppressContentEditableWarning spellCheck={false}
    data-placeholder={props.placeholder}
    onInput={emit} onKeyDown={onKeyDown} onPaste={onPaste}
    onMouseOver={event => props.onHoverChip(chipUnder(event))} onMouseOut={() => props.onHoverChip(null)} />;
});

/** A chip looks like the cell it came from: its colour, the item's name, and where it is in a lighter face. */
function chipElement(chip: AiAttachment): HTMLElement {
  const node = document.createElement("span");
  node.className = `ai-chip is-${chip.tone}`;
  node.contentEditable = "false";
  node.dataset.quote = chip.quote;
  if (chip.path) node.dataset.path = chip.path;
  node.title = chip.kind === "column" || chip.kind === "row" ? chip.quote.replace(/^「|」$/g, "") : `${chip.quote.replace(/^「|」$/g, "")}：${chip.value}`;
  if (chip.detail) { const where = document.createElement("small"); where.textContent = chip.detail; node.appendChild(where); }
  node.appendChild(document.createTextNode(chip.title));
  return node;
}

/** The sentence as sent: chips become 「…」, line breaks stay, the caret's ZWSPs go. */
function serialize(node: Node, withoutChips = false): string {
  let out = "";
  node.childNodes.forEach(child => {
    if (child.nodeType === Node.TEXT_NODE) out += (child.textContent ?? "").replaceAll(ZWSP, "");
    else if (child instanceof HTMLElement) {
      if (child.classList.contains("ai-chip")) out += withoutChips ? "" : child.dataset.quote ?? "";
      else if (child.tagName === "BR") out += "\n";
      else out += (child.tagName === "DIV" && out && !out.endsWith("\n") ? "\n" : "") + serialize(child, withoutChips);
    }
  });
  return out;
}
