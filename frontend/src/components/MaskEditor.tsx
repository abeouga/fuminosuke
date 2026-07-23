import { useCallback, useEffect, useRef, useState } from "react";

type Mode = "add" | "erase";

type HandwritingCandidate = {
  candidateId: string;
  x: number;
  y: number;
  width: number;
  height: number;
  pixelCount: number;
  confidence: number;
};

type CandidatePayload = { candidates: HandwritingCandidate[] };
type EditorSnapshot = { image: ImageData; included: string[] };

type Props = {
  source: string;
  background: string;
  candidatesSource?: string;
  onSave: (blob: Blob) => Promise<void>;
};

const candidateBounds = (candidate: HandwritingCandidate, canvas: HTMLCanvasElement) => ({
  x: Math.floor(candidate.x * canvas.width),
  y: Math.floor(candidate.y * canvas.height),
  width: Math.ceil(candidate.width * canvas.width),
  height: Math.ceil(candidate.height * canvas.height),
});

export function MaskEditor({ source, background, candidatesSource, onSave }: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const drawing = useRef(false);
  const originalMask = useRef<ImageData | null>(null);
  const history = useRef<EditorSnapshot[]>([]);
  const future = useRef<EditorSnapshot[]>([]);
  const candidatesRef = useRef<HandwritingCandidate[]>([]);
  const [candidates, setCandidates] = useState<HandwritingCandidate[]>([]);
  const [included, setIncluded] = useState<Set<string>>(new Set());
  const [mode, setMode] = useState<Mode>("erase");
  const [brush, setBrush] = useState(28);
  const [saving, setSaving] = useState(false);

  const load = useCallback(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const image = new Image();
    image.onload = () => {
      canvas.width = image.naturalWidth;
      canvas.height = image.naturalHeight;
      const temporary = document.createElement("canvas");
      temporary.width = canvas.width;
      temporary.height = canvas.height;
      const temporaryContext = temporary.getContext("2d", { willReadFrequently: true });
      const context = canvas.getContext("2d", { willReadFrequently: true });
      if (!temporaryContext || !context) return;
      temporaryContext.drawImage(image, 0, 0);
      const input = temporaryContext.getImageData(0, 0, canvas.width, canvas.height);
      const overlay = context.createImageData(canvas.width, canvas.height);
      for (let index = 0; index < input.data.length; index += 4) {
        if (input.data[index] < 128) continue;
        overlay.data[index] = 225;
        overlay.data[index + 1] = 58;
        overlay.data[index + 2] = 48;
        overlay.data[index + 3] = 220;
      }
      context.putImageData(overlay, 0, 0);
      originalMask.current = new ImageData(
        new Uint8ClampedArray(overlay.data),
        overlay.width,
        overlay.height,
      );
      const allCandidates = candidatesRef.current.map((candidate) => candidate.candidateId);
      setIncluded(new Set(allCandidates));
      history.current = [{
        image: context.getImageData(0, 0, canvas.width, canvas.height),
        included: allCandidates,
      }];
      future.current = [];
    };
    image.src = source;
  }, [source]);

  useEffect(load, [load]);

  useEffect(() => {
    if (!candidatesSource) {
      candidatesRef.current = [];
      setCandidates([]);
      setIncluded(new Set());
      return;
    }
    let cancelled = false;
    void fetch(candidatesSource)
      .then(async (response) => {
        if (!response.ok) throw new Error("候補矩形を読み込めませんでした。");
        return (await response.json()) as CandidatePayload;
      })
      .then((payload) => {
        if (cancelled) return;
        candidatesRef.current = payload.candidates;
        setCandidates(payload.candidates);
        const allCandidates = payload.candidates.map((candidate) => candidate.candidateId);
        setIncluded(new Set(allCandidates));
        if (history.current.length === 1) history.current[0].included = allCandidates;
      })
      .catch(() => {
        if (cancelled) return;
        candidatesRef.current = [];
        setCandidates([]);
      });
    return () => { cancelled = true; };
  }, [candidatesSource]);

  const point = (event: React.PointerEvent<HTMLCanvasElement>) => {
    const canvas = event.currentTarget;
    const rect = canvas.getBoundingClientRect();
    return {
      x: ((event.clientX - rect.left) / rect.width) * canvas.width,
      y: ((event.clientY - rect.top) / rect.height) * canvas.height,
    };
  };

  const draw = (event: React.PointerEvent<HTMLCanvasElement>) => {
    if (!drawing.current) return;
    const context = event.currentTarget.getContext("2d");
    if (!context) return;
    const location = point(event);
    context.save();
    context.globalCompositeOperation = mode === "add" ? "source-over" : "destination-out";
    context.fillStyle = "rgba(225, 58, 48, 0.86)";
    context.beginPath();
    context.arc(location.x, location.y, brush, 0, Math.PI * 2);
    context.fill();
    context.restore();
  };

  const snapshot = (activeCandidates = included) => {
    const canvas = canvasRef.current;
    const context = canvas?.getContext("2d", { willReadFrequently: true });
    if (!canvas || !context) return;
    history.current.push({
      image: context.getImageData(0, 0, canvas.width, canvas.height),
      included: [...activeCandidates],
    });
    if (history.current.length > 30) history.current.shift();
    future.current = [];
  };

  const restore = (direction: "undo" | "redo") => {
    const canvas = canvasRef.current;
    const context = canvas?.getContext("2d");
    if (!context) return;
    if (direction === "undo" && history.current.length > 1) {
      const current = history.current.pop();
      if (current) future.current.push(current);
      const previous = history.current.at(-1)!;
      context.putImageData(previous.image, 0, 0);
      setIncluded(new Set(previous.included));
    } else if (direction === "redo") {
      const next = future.current.pop();
      if (next) {
        history.current.push(next);
        context.putImageData(next.image, 0, 0);
        setIncluded(new Set(next.included));
      }
    }
  };

  const applyCandidateSelection = (activeCandidates: Set<string>) => {
    const canvas = canvasRef.current;
    const context = canvas?.getContext("2d");
    const original = originalMask.current;
    if (!canvas || !context || !original) return;
    context.putImageData(original, 0, 0);
    for (const candidate of candidatesRef.current) {
      if (activeCandidates.has(candidate.candidateId)) continue;
      const bounds = candidateBounds(candidate, canvas);
      context.clearRect(bounds.x, bounds.y, bounds.width, bounds.height);
    }
  };

  const toggleCandidate = (candidate: HandwritingCandidate) => {
    const next = new Set(included);
    if (next.has(candidate.candidateId)) next.delete(candidate.candidateId);
    else next.add(candidate.candidateId);
    applyCandidateSelection(next);
    setIncluded(next);
    snapshot(next);
  };

  const setAllCandidates = (active: boolean) => {
    const next = new Set(
      active ? candidatesRef.current.map((candidate) => candidate.candidateId) : [],
    );
    applyCandidateSelection(next);
    setIncluded(next);
    snapshot(next);
  };

  const save = () => {
    const canvas = canvasRef.current;
    const context = canvas?.getContext("2d", { willReadFrequently: true });
    if (!canvas || !context) return;
    const sourcePixels = context.getImageData(0, 0, canvas.width, canvas.height);
    const output = document.createElement("canvas");
    output.width = canvas.width;
    output.height = canvas.height;
    const outputContext = output.getContext("2d");
    if (!outputContext) return;
    const binary = outputContext.createImageData(output.width, output.height);
    for (let index = 0; index < binary.data.length; index += 4) {
      const value = sourcePixels.data[index + 3] > 0 ? 255 : 0;
      binary.data[index] = value;
      binary.data[index + 1] = value;
      binary.data[index + 2] = value;
      binary.data[index + 3] = 255;
    }
    outputContext.putImageData(binary, 0, 0);
    setSaving(true);
    output.toBlob((blob) => {
      if (!blob) return setSaving(false);
      void onSave(blob).finally(() => setSaving(false));
    }, "image/png");
  };

  return (
    <div className="mask-editor">
      <div className="candidate-summary">
        <div><strong>検出した文字候補を選別</strong><span>赤枠をクリックすると、その文字を「印刷文字」として除外できます。</span></div>
        <strong>{included.size} / {candidates.length} 件を除去</strong>
      </div>
      <div className="viewer-toolbar wrap">
        <div className="segmented">
          <button type="button" className={mode === "erase" ? "active" : ""} onClick={() => setMode("erase")}>− 印刷文字を除外</button>
          <button type="button" className={mode === "add" ? "active" : ""} onClick={() => setMode("add")}>＋ 手書きを追加</button>
        </div>
        <label>ブラシ <input type="range" min="5" max="80" value={brush} onChange={(event) => setBrush(Number(event.target.value))} /></label>
        <button type="button" className="ghost" onClick={() => setAllCandidates(false)}>すべて除外</button>
        <button type="button" className="ghost" onClick={() => setAllCandidates(true)}>すべて戻す</button>
        <button type="button" className="ghost" onClick={() => restore("undo")}>← 戻す</button>
        <button type="button" className="ghost" onClick={() => restore("redo")}>→ やり直す</button>
        <button type="button" className="ghost" onClick={load}>自動検出へ戻す</button>
        <button type="button" className="primary compact" disabled={saving} onClick={save}>{saving ? "保存中…" : "選別結果を保存"}</button>
      </div>
      <div className="mask-editor-stage">
        <img src={background} alt="文字候補を確認する帳票" draggable={false} />
        <canvas
          ref={canvasRef}
          aria-label="手書き候補マスク編集"
          onPointerDown={(event) => {
            event.currentTarget.setPointerCapture(event.pointerId);
            drawing.current = true;
            draw(event);
          }}
          onPointerMove={draw}
          onPointerUp={() => { drawing.current = false; snapshot(); }}
          onPointerCancel={() => { drawing.current = false; }}
        />
        <div className="candidate-layer" aria-label="検出文字候補">
          {candidates.map((candidate, index) => {
            const active = included.has(candidate.candidateId);
            return (
              <button
                type="button"
                key={candidate.candidateId}
                className={`candidate-box ${active ? "included" : "excluded"}`}
                style={{ left: `${candidate.x * 100}%`, top: `${candidate.y * 100}%`, width: `${candidate.width * 100}%`, height: `${candidate.height * 100}%` }}
                aria-pressed={!active}
                aria-label={`候補${index + 1}: ${active ? "除去対象" : "印刷文字として除外済み"}`}
                title={active ? "クリックして印刷文字として除外" : "クリックして除去対象へ戻す"}
                onClick={() => toggleCandidate(candidate)}
              ><span>{index + 1}</span></button>
            );
          })}
        </div>
      </div>
      <div className="candidate-legend"><span><i className="remove" />除去される候補</span><span><i className="keep" />保持する印刷文字</span></div>
    </div>
  );
}
