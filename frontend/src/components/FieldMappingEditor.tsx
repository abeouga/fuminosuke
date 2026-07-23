import { useEffect, useMemo, useRef, useState } from "react";
import type { FieldMapping, FieldMappingTarget } from "../types";

type Rect = { x: number; y: number; width: number; height: number };

export function FieldMappingEditor({
  source,
  canonicalWidth,
  canonicalHeight,
  initialMappings,
  targets,
  onSave,
}: {
  source: string;
  canonicalWidth: number;
  canonicalHeight: number;
  initialMappings: FieldMapping[];
  targets: FieldMappingTarget[];
  onSave: (mappings: FieldMapping[]) => Promise<void>;
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const imageRef = useRef<HTMLImageElement | null>(null);
  const dragStart = useRef<{ x: number; y: number } | null>(null);
  const [mappings, setMappings] = useState(initialMappings);
  const [selectedPath, setSelectedPath] = useState(targets[0]?.path ?? "");
  const [selectedMappingId, setSelectedMappingId] = useState<string | null>(null);
  const [draft, setDraft] = useState<Rect | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => setMappings(initialMappings), [initialMappings]);
  useEffect(() => {
    if (!selectedPath && targets[0]) setSelectedPath(targets[0].path);
  }, [selectedPath, targets]);

  const targetByPath = useMemo(() => new Map(targets.map((item) => [item.path, item])), [targets]);
  const sections = useMemo(() => {
    const grouped = new Map<string, FieldMappingTarget[]>();
    targets.forEach((target) => grouped.set(target.section, [...(grouped.get(target.section) ?? []), target]));
    return [...grouped.entries()];
  }, [targets]);

  const redraw = () => {
    const canvas = canvasRef.current;
    const image = imageRef.current;
    if (!canvas || !image) return;
    const context = canvas.getContext("2d");
    if (!context) return;
    context.clearRect(0, 0, canvas.width, canvas.height);
    context.drawImage(image, 0, 0, canvas.width, canvas.height);
    const drawRect = (rect: Rect, color: string, label?: string) => {
      context.strokeStyle = color;
      context.lineWidth = Math.max(3, canonicalWidth / 500);
      context.strokeRect(rect.x, rect.y, rect.width, rect.height);
      if (label) {
        context.font = `700 ${Math.max(16, canonicalWidth / 65)}px sans-serif`;
        const textWidth = context.measureText(label).width;
        const textHeight = Math.max(22, canonicalWidth / 48);
        const textY = Math.max(textHeight, rect.y);
        context.fillStyle = color;
        context.fillRect(rect.x, textY - textHeight, Math.min(textWidth + 14, canvas.width - rect.x), textHeight);
        context.fillStyle = "white";
        context.fillText(label, rect.x + 7, textY - 5);
      }
    };
    mappings.forEach((mapping) => drawRect(
      mapping,
      mapping.mappingId === selectedMappingId ? "#d97706" : "#167c72",
      mapping.label,
    ));
    if (draft) drawRect(draft, "#d97706");
  };

  useEffect(redraw, [mappings, draft, selectedMappingId, canonicalWidth, canonicalHeight]);
  useEffect(() => {
    const image = new Image();
    image.onload = () => { imageRef.current = image; redraw(); };
    image.src = source;
    return () => { image.onload = null; };
  }, [source]);

  const point = (event: React.PointerEvent<HTMLCanvasElement>) => {
    const canvas = event.currentTarget;
    const bounds = canvas.getBoundingClientRect();
    return {
      x: Math.max(0, Math.min(canvas.width, (event.clientX - bounds.left) * canvas.width / bounds.width)),
      y: Math.max(0, Math.min(canvas.height, (event.clientY - bounds.top) * canvas.height / bounds.height)),
    };
  };
  const rectFrom = (start: { x: number; y: number }, end: { x: number; y: number }): Rect => ({
    x: Math.round(Math.min(start.x, end.x)),
    y: Math.round(Math.min(start.y, end.y)),
    width: Math.round(Math.abs(start.x - end.x)),
    height: Math.round(Math.abs(start.y - end.y)),
  });
  const finish = (event: React.PointerEvent<HTMLCanvasElement>) => {
    const start = dragStart.current;
    if (!start) return;
    const next = rectFrom(start, point(event));
    dragStart.current = null;
    setDraft(null);
    event.currentTarget.releasePointerCapture?.(event.pointerId);
    if (!selectedPath || next.width < 5 || next.height < 5) return;
    const target = targetByPath.get(selectedPath);
    if (!target) return;
    const mapping: FieldMapping = {
      mappingId: `field_${selectedPath.replace(/[^a-zA-Z0-9]+/g, "_")}`,
      targetPath: target.path,
      label: target.label,
      ...next,
      padding: 4,
      enabled: true,
      modelDetail: "high",
    };
    setMappings((current) => [...current.filter((item) => item.targetPath !== selectedPath), mapping]);
    setSelectedMappingId(mapping.mappingId);
  };

  return (
    <div className="field-mapping-editor">
      <div className="mapping-toolbar">
        <label className="field-label">割り当てるJSON項目
          <select value={selectedPath} onChange={(event) => setSelectedPath(event.target.value)}>
            {sections.map(([section, items]) => <optgroup key={section} label={section}>
              {items.map((target) => <option key={target.path} value={target.path}>
                {target.label}{mappings.some((item) => item.targetPath === target.path) ? " ✓" : ""}
              </option>)}
            </optgroup>)}
          </select>
        </label>
        <div className="mapping-help"><strong>1. 項目を選択　2. 記入欄をドラッグ</strong><span>同じ項目を描き直すと矩形を置き換えます。</span></div>
      </div>
      <div className="mapping-canvas-wrap">
        <canvas
          ref={canvasRef}
          width={canonicalWidth}
          height={canonicalHeight}
          onPointerDown={(event) => {
            if (!selectedPath) return;
            event.currentTarget.setPointerCapture?.(event.pointerId);
            dragStart.current = point(event);
            setDraft({ ...dragStart.current, width: 0, height: 0 });
          }}
          onPointerMove={(event) => {
            if (dragStart.current) setDraft(rectFrom(dragStart.current, point(event)));
          }}
          onPointerUp={finish}
          onPointerCancel={() => { dragStart.current = null; setDraft(null); }}
          aria-label="帳票上で読み取り範囲を指定"
        />
      </div>
      <div className="mapping-list">
        <div className="mapping-list-head"><strong>設定済み {mappings.length}件</strong><span>クリックすると帳票上で強調表示します。</span></div>
        {mappings.length === 0 && <p className="empty-state compact">まだ項目矩形がありません。上の帳票をドラッグしてください。</p>}
        {mappings.map((mapping) => <div key={mapping.mappingId} className={`mapping-row ${selectedMappingId === mapping.mappingId ? "selected" : ""}`}>
          <button type="button" className="mapping-select" onClick={() => { setSelectedMappingId(mapping.mappingId); setSelectedPath(mapping.targetPath); }}>
            <strong>{mapping.label}</strong><small>{mapping.targetPath} · x{mapping.x} y{mapping.y} {mapping.width}×{mapping.height}</small>
          </button>
          <label className="mapping-enabled"><input type="checkbox" checked={mapping.enabled} onChange={(event) => setMappings((items) => items.map((item) => item.mappingId === mapping.mappingId ? { ...item, enabled: event.target.checked } : item))} />使用</label>
          <button type="button" className="danger-button compact" onClick={() => setMappings((items) => items.filter((item) => item.mappingId !== mapping.mappingId))}>削除</button>
        </div>)}
      </div>
      {mappings.length === 0 && <p className="inline-warning">矩形が0件のままでは互換用の旧読取方式です。責任分離した新方式を使うには、必要な項目を1件以上設定して保存してください。</p>}
      <button className="primary" disabled={saving} onClick={async () => { setSaving(true); try { await onSave(mappings); } finally { setSaving(false); } }}>
        {saving ? "保存中…" : "矩形とJSON項目の対応を保存"}
      </button>
    </div>
  );
}
