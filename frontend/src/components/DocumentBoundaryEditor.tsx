import { useRef, useState, type KeyboardEvent, type PointerEvent } from "react";
import { ApiError, api } from "../api";
import type { Job } from "../types";

type BoundaryPoint = { x: number; y: number };

const INITIAL_CORNERS: BoundaryPoint[] = [
  { x: 0.03, y: 0.03 },
  { x: 0.97, y: 0.03 },
  { x: 0.97, y: 0.97 },
  { x: 0.03, y: 0.97 },
];
const CORNER_LABELS = ["左上", "右上", "右下", "左下"];

export function DocumentBoundaryEditor({
  jobId,
  onRetry,
}: {
  jobId: string;
  onRetry: (job: Job) => void;
}) {
  const stageRef = useRef<HTMLDivElement>(null);
  const [corners, setCorners] = useState<BoundaryPoint[]>(() => (
    INITIAL_CORNERS.map((point) => ({ ...point }))
  ));
  const [saving, setSaving] = useState(false);
  const [submitted, setSubmitted] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  const moveCorner = (index: number, point: BoundaryPoint) => {
    setCorners((current) => current.map((corner, cornerIndex) => (
      cornerIndex === index ? point : corner
    )));
  };
  const pointFromPointer = (event: PointerEvent<HTMLButtonElement>): BoundaryPoint | null => {
    const bounds = stageRef.current?.getBoundingClientRect();
    if (!bounds || bounds.width <= 0 || bounds.height <= 0) return null;
    return {
      x: clamp((event.clientX - bounds.left) / bounds.width),
      y: clamp((event.clientY - bounds.top) / bounds.height),
    };
  };
  const handlePointerMove = (index: number, event: PointerEvent<HTMLButtonElement>) => {
    if (!event.currentTarget.hasPointerCapture(event.pointerId)) return;
    const point = pointFromPointer(event);
    if (point) moveCorner(index, point);
  };
  const handleKeyDown = (index: number, event: KeyboardEvent<HTMLButtonElement>) => {
    const direction = {
      ArrowLeft: [-1, 0],
      ArrowRight: [1, 0],
      ArrowUp: [0, -1],
      ArrowDown: [0, 1],
    }[event.key];
    if (!direction) return;
    event.preventDefault();
    const step = event.shiftKey ? 0.02 : 0.005;
    moveCorner(index, {
      x: clamp(corners[index].x + direction[0] * step),
      y: clamp(corners[index].y + direction[1] * step),
    });
  };
  const submit = async () => {
    if (saving) return;
    setSaving(true);
    setMessage(null);
    try {
      const nextJob = await api.saveTemplateBoundary(jobId, corners);
      setSubmitted(true);
      onRetry(nextJob);
    } catch (error) {
      setMessage(
        error instanceof ApiError || error instanceof Error
          ? error.message
          : "四隅の指定を保存できませんでした。",
      );
    } finally {
      setSaving(false);
    }
  };

  return (
    <section className="document-boundary-editor" aria-labelledby="document-boundary-title">
      <div className="document-boundary-heading">
        <div>
          <h3 id="document-boundary-title">書類の四隅を指定してください</h3>
          <p>4つの印を用紙の角へ合わせてください。指定した範囲を補正して、テンプレート準備を再開します。</p>
        </div>
        <button
          type="button"
          className="secondary compact"
          disabled={saving || submitted}
          onClick={() => setCorners(INITIAL_CORNERS.map((point) => ({ ...point })))}
        >
          位置を戻す
        </button>
      </div>
      <div className="document-boundary-stage" ref={stageRef}>
        <img src={api.templateBoundaryImageUrl(jobId)} alt="四隅を指定する元の書類" />
        <svg viewBox="0 0 1000 1000" preserveAspectRatio="none" aria-hidden="true">
          <polygon points={corners.map((point) => `${point.x * 1000},${point.y * 1000}`).join(" ")} />
        </svg>
        {corners.map((point, index) => (
          <button
            type="button"
            className="document-boundary-handle"
            key={CORNER_LABELS[index]}
            aria-label={`${CORNER_LABELS[index]}の角`}
            style={{ left: `${point.x * 100}%`, top: `${point.y * 100}%` }}
            onPointerDown={(event) => {
              event.preventDefault();
              event.currentTarget.setPointerCapture(event.pointerId);
              const next = pointFromPointer(event);
              if (next) moveCorner(index, next);
            }}
            onPointerMove={(event) => handlePointerMove(index, event)}
            onKeyDown={(event) => handleKeyDown(index, event)}
          >
            <span>{index + 1}</span>
          </button>
        ))}
      </div>
      <p className="document-boundary-help">角の印はドラッグできます。選択中は矢印キー、Shift＋矢印キーでも調整できます。</p>
      {message && <p className="error-box" role="alert">{message}</p>}
      <button type="button" className="primary full" disabled={saving || submitted} onClick={() => void submit()}>
        {submitted ? "再処理を開始しました" : saving ? "四隅を保存中…" : "この範囲で再処理する"}
      </button>
    </section>
  );
}

function clamp(value: number): number {
  return Math.min(1, Math.max(0, value));
}
