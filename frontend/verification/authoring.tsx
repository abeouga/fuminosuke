import React, { useState } from "react";
import { createRoot } from "react-dom/client";
import { LayoutEditor, type LayoutWorkflowStep } from "../src/components/LayoutEditor";
import type { LayoutCell, LayoutDefinition } from "../src/types";
import "../src/styles.css";

const cell = (id: string, role: LayoutCell["role"], x: number, y: number, text = ""): LayoutCell =>
  ({ id, role, x, y, width: 120, height: 50, text, source: "manual", confidence: 1, textConfidence: null });
const initial: LayoutDefinition = { canonicalWidth: 800, canonicalHeight: 500, detector: "verification",
  ocrAvailable: false, allowCrossAxisLabels: true, warnings: [],
  cells: [cell("outer", "parent_label", 40, 30, "検証帳票"),
    { ...cell("virtual", "parent_label", 250, 30, "仮想見出し"), virtual: true },
    cell("labelA", "label", 180, 200, "検査日"), cell("labelB", "label", 420, 200, "結果"),
    { ...cell("polygon", "input", 180, 300), shapeType: "polygon",
      vertices: [{ x: 180, y: 300 }, { x: 300, y: 300 }, { x: 180, y: 350 }] },
    cell("inputB", "input", 420, 300)],
  relations: [{ id: "a", inputCellId: "polygon", labelCellIds: ["labelA"],
    targetPath: "customFields.date", axis: "vertical", order: 1 }],
  recordSections: [{ id: "tests", title: "検査一覧", collectionPath: "genericRecords.tests", recordType: "generic",
    parentLabelCellId: null, recordLabelCellIds: ["labelA", "labelB"], rowKeyStrategy: "position", source: "manual" }],
  labelPool: [{ id: "name", label: "氏名", targetPath: "customFields.name", aliases: [], section: "検証", source: "manual" }],
};
function App() {
  const [step, setStep] = useState<LayoutWorkflowStep>("classify");
  const [saved, setSaved] = useState(initial);
  return <main style={{ maxWidth: 1400, margin: "20px auto" }}>
    <h1>テンプレート操作検証（実データ・API接続なし）</h1>
    <nav>{(["classify", "meaning", "records"] as const).map((value, i) =>
      <button key={value} onClick={() => setStep(value)}>検証ステップ{i + 1}</button>)}</nav>
    <LayoutEditor initialLayout={initial} workflowStep={step} targets={[]}
      source={'data:image/svg+xml,' + encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" width="800" height="500"><rect width="800" height="500" fill="white"/></svg>')}
      onDetect={async () => initial} onSave={async value => { setSaved(value); return value; }} />
    <details><summary>保存された検証データ</summary><pre aria-label="保存データ">{JSON.stringify(saved, null, 2)}</pre></details>
  </main>;
}
createRoot(document.getElementById("root")!).render(<App />);
