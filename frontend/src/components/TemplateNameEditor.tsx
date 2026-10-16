import { useEffect, useRef, useState, type FocusEvent, type KeyboardEvent } from "react";
import { FiCheck, FiEdit3, FiX } from "react-icons/fi";

export function TemplateNameEditor({ headingId, name, onSave }: {
  headingId: string;
  name: string;
  onSave: (name: string) => Promise<string>;
}) {
  const [displayName, setDisplayName] = useState(name);
  const [draft, setDraft] = useState(name);
  const [editing, setEditing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const inputRef = useRef<HTMLInputElement | null>(null);
  const savingRef = useRef(false);
  const cancelOnBlurRef = useRef(false);
  const editingRef = useRef(false);
  editingRef.current = editing;

  useEffect(() => {
    setDisplayName(name);
    if (!editingRef.current) setDraft(name);
  }, [name]);

  useEffect(() => {
    if (!editing) return;
    inputRef.current?.focus();
    inputRef.current?.select();
  }, [editing]);

  const startEditing = () => {
    cancelOnBlurRef.current = false;
    setDraft(displayName);
    setError("");
    setEditing(true);
  };

  const cancelEditing = () => {
    cancelOnBlurRef.current = true;
    setDraft(displayName);
    setError("");
    setEditing(false);
  };

  const save = async () => {
    if (!editing || savingRef.current) return;
    const nextName = draft.trim();
    if (!nextName) {
      setError("テンプレート名を入力してください。");
      inputRef.current?.focus();
      return;
    }
    if (nextName.length > 200) {
      setError("テンプレート名は200文字以内で入力してください。");
      inputRef.current?.focus();
      return;
    }
    if (nextName === displayName) {
      setError("");
      setEditing(false);
      return;
    }

    savingRef.current = true;
    setSaving(true);
    setError("");
    try {
      const savedName = await onSave(nextName);
      setDisplayName(savedName);
      setDraft(savedName);
      setEditing(false);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "テンプレート名を保存できませんでした。");
      inputRef.current?.focus();
    } finally {
      savingRef.current = false;
      setSaving(false);
    }
  };

  const handleBlur = (event: FocusEvent<HTMLDivElement>) => {
    if (cancelOnBlurRef.current) {
      cancelOnBlurRef.current = false;
      return;
    }
    if (event.relatedTarget instanceof Node && event.currentTarget.contains(event.relatedTarget)) return;
    void save();
  };

  const handleKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "Enter") {
      event.preventDefault();
      void save();
    } else if (event.key === "Escape") {
      event.preventDefault();
      cancelEditing();
    }
  };

  return (
    <div className="ocr-template-name-editor" onBlur={handleBlur}>
      <div className="ocr-template-name-editor-row">
        <h2 id={headingId}>
          {editing
            ? <input ref={inputRef} className="ocr-template-name-editor-input" aria-label="テンプレート名"
                maxLength={200} value={draft} disabled={saving}
                onChange={(event) => setDraft(event.target.value)} onKeyDown={handleKeyDown} />
            : displayName}
        </h2>
        {editing
          ? <>
              <button type="button" className="ocr-template-name-action" aria-label="テンプレート名を保存"
                title="保存" disabled={saving} onClick={() => void save()}>
                <FiCheck aria-hidden="true" focusable="false" />
              </button>
              <button type="button" className="ocr-template-name-action" aria-label="変更をキャンセル"
                title="キャンセル" disabled={saving} onClick={cancelEditing}>
                <FiX aria-hidden="true" focusable="false" />
              </button>
            </>
          : <button type="button" className="ocr-template-name-edit" aria-label="テンプレート名を変更"
              title="テンプレート名を変更" onClick={startEditing}>
              <FiEdit3 aria-hidden="true" focusable="false" />
            </button>}
      </div>
      {error && <p className="ocr-template-name-error" role="alert">{error}</p>}
    </div>
  );
}
