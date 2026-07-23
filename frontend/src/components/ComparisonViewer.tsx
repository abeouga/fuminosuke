import { useState } from "react";

export function ComparisonViewer({ before, after }: { before: string; after: string }) {
  const [split, setSplit] = useState(50);
  const [zoom, setZoom] = useState(1);
  return (
    <div className="comparison-shell">
      <div className="viewer-toolbar">
        <span>比較スライダー</span>
        <label>
          ズーム
          <input
            type="range"
            min="1"
            max="2.5"
            step="0.1"
            value={zoom}
            onChange={(event) => setZoom(Number(event.target.value))}
          />
        </label>
      </div>
      <div className="comparison-viewport">
        <div className="comparison-stage" style={{ transform: `scale(${zoom})` }}>
          <img src={before} alt="処理前" />
          <img
            src={after}
            alt="処理後"
            className="comparison-after"
            style={{ clipPath: `inset(0 ${100 - split}% 0 0)` }}
          />
          <span className="split-line" style={{ left: `${split}%` }} />
        </div>
      </div>
      <input
        className="split-range"
        aria-label="比較位置"
        type="range"
        min="0"
        max="100"
        value={split}
        onChange={(event) => setSplit(Number(event.target.value))}
      />
    </div>
  );
}
