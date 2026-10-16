import { useRef, useState } from "react";
import type { FieldMappingTarget, LayoutCell, LayoutDefinition, LayoutMarkSet } from "../types";
import { type Rect, type Point } from "./layoutEditorGeometry";

function suggestedChoiceLabels(text: string): string[] {
  const normalized = text.normalize("NFKC").trim();
  const compact = normalized.replace(/[\s　・,、/／]+/g, "");
  const knownGroups = [
    ["都", "道", "府", "県"],
    ["市", "区", "町", "村"],
  ];
  const known = knownGroups.find((labels) => (
    labels.every((label) => compact.includes(label))
    && compact.length <= labels.join("").length + 4
  ));
  if (known) return known;
  const separated = normalized
    .split(/[\s　・,、/／]+/)
    .map((value) => value.trim())
    .filter(Boolean);
  return separated.length >= 2 && separated.length <= 12
    ? [...new Set(separated)]
    : [];
}
function reflowChoiceOptions(
  cell: LayoutCell,
  options: LayoutMarkSet["options"],
): LayoutMarkSet["options"] {
  if (options.length === 0) return [];
  return options.map((option, index) => {
    const left = Math.round(cell.width * index / options.length);
    const right = Math.round(cell.width * (index + 1) / options.length);
    return {
      ...option,
      x: left,
      y: 0,
      width: Math.max(1, right - left),
      height: Math.max(1, cell.height),
    };
  });
}
function nextChoiceOptionId(options: LayoutMarkSet["options"]): string {
  const ids = new Set(options.map((option) => option.id));
  let index = 1;
  while (ids.has(`choice_${index}`)) index += 1;
  return `choice_${index}`;
}
export function genericManualChoicePreset(
  cell: LayoutCell,
  targetPath = "",
): LayoutMarkSet[] {
  const labels = suggestedChoiceLabels(cell.text);
  return [{
    id: "choice",
    targetPath,
    recognitionMode: "manual",
    selectionMode: "single",
    options: reflowChoiceOptions(
      cell,
      labels.map((label, index) => ({
        id: `choice_${index + 1}`,
        label,
        value: label,
        x: 0,
        y: 0,
        width: 1,
        height: Math.max(1, cell.height),
      })),
    ),
  }];
}
export function checklistPreset(cell: LayoutCell, targetPath = ""): LayoutMarkSet[] {
  const labels = /^[□☐☑☒■]\s*$/.test(cell.text.trim()) ? ["チェック済み"] : [...new Set(cell.text.split(/[□☐☑☒■]/).slice(1)
    .map(label => label.trim()).filter(Boolean))];
  return [{ id: "choice", targetPath, recognitionMode: "checklist", selectionMode: "multiple",
    options: labels.map((label, index) => ({ id: `choice_${index + 1}`, label, value: label,
      x: 0, y: 0, width: cell.width, height: cell.height })) }];
}
export function MarkCellEditor({
  cell,
  sets,
  targets,
  source,
  canonicalWidth,
  canonicalHeight,
  onChange,
  selectedChoiceId,
  onChoiceSelect,
}: {
  cell: LayoutCell;
  sets: LayoutMarkSet[];
  targets: FieldMappingTarget[];
  source: string;
  canonicalWidth: number;
  canonicalHeight: number;
  onChange: (sets: LayoutMarkSet[]) => void;
  selectedChoiceId?: string | null;
  onChoiceSelect?: (markSetId: string, choiceId: string) => void;
}) {
  const [newOptionLabel, setNewOptionLabel] = useState("");
  const markSet = sets[0];
  const isSymbol = markSet?.recognitionMode === "symbol";
  const isChecklist = markSet?.recognitionMode === "checklist";
  const sections = [...new Set(targets.map((target) => target.section))];
  if (!markSet) {
    return (
      <fieldset className="mark-cell-editor generic-choice-editor">
        <legend>選択欄の設定</legend>
        <p>保存先、選択肢、読み取り時の判定方法を設定します。</p>
        <button
          type="button"
          className="primary compact"
          onClick={() => onChange(genericManualChoicePreset(cell))}
        >
          選択欄を設定する
        </button>
      </fieldset>
    );
  }
  const patchSet = (patch: Partial<LayoutMarkSet>) => onChange([
    { ...markSet, ...patch },
    ...sets.slice(1),
  ]);
  const patchOption = (optionId: string, patch: Partial<LayoutMarkSet["options"][number]>) => {
    patchSet({
      options: markSet.options.map((option) => (
        option.id === optionId ? { ...option, ...patch } : option
      )),
    });
  };
  const cleanedNewOption = newOptionLabel.trim();
  const duplicateNewOption = markSet.options.some((option) => (
    option.label === cleanedNewOption || option.value === cleanedNewOption
  ));
  return (
    <fieldset className="mark-cell-editor generic-choice-editor">
      <legend>{isSymbol ? "記号の候補と保存先" : isChecklist ? "チェック項目と保存先" : "選択欄の設定"}</legend>
      <label>
        保存する情報
        <select
          value={markSet.targetPath}
          onChange={(event) => patchSet({ targetPath: event.target.value })}
        >
          <option value="">保存先を選択</option>
          {sections.map((section) => (
            <optgroup label={section} key={section}>
              {targets.filter((target) => target.section === section).map((target) => (
                <option value={target.path} key={target.path}>{target.label}</option>
              ))}
            </optgroup>
          ))}
        </select>
      </label>
      {!isSymbol && !isChecklist && <div className="choice-setting-grid">
        <label>
          判定方法
          <select
            value={markSet.recognitionMode ?? "automatic"}
            onChange={(event) => patchSet({
              recognitionMode: event.target.value as "automatic" | "manual",
            })}
          >
            <option value="manual">読み取り時に人が選ぶ</option>
            <option value="automatic">画像から自動判定する</option>
          </select>
        </label>
        <label>
          選択数
          <select
            value={markSet.selectionMode}
            onChange={(event) => patchSet({
              selectionMode: event.target.value as "single" | "multiple",
            })}
          >
            <option value="single">1つだけ</option>
            <option value="multiple">複数可</option>
          </select>
        </label>
      </div>}
      <p>
        {isSymbol ? "この欄全体の画像から、候補の記号を1つ判定します。空欄は空欄、候補外や判読できない記号は確認対象にします。"
          : isChecklist ? "欄全体をOCRで読み、チェックされた項目を複数取得します。全未選択と判別不能を区別します。項目名はチェック記号を除いて入力してください。区切り文字（・、,;）は使えません。"
          : markSet.recognitionMode === "manual"
          ? "この欄はOCRへ送らず、読み取り確認画面で利用者が画像を見て選択します。"
          : "選択肢ごとの範囲を画像差分で判定し、不確実な場合は確認対象にします。"}
      </p>
      {!isSymbol && !isChecklist && <svg
        className="mark-option-canvas"
        viewBox={`0 0 ${cell.width} ${cell.height}`}
        preserveAspectRatio="none"
        aria-label="選択欄の設定範囲"
      >
        <image
          href={source}
          x={-cell.x}
          y={-cell.y}
          width={canonicalWidth}
          height={canonicalHeight}
          preserveAspectRatio="none"
        />
        {markSet.options.map((option) => (
          <g key={option.id}>
            <rect
              className="mark-option-region"
              x={option.x}
              y={option.y}
              width={option.width}
              height={option.height}
            />
            <text
              className="mark-option-canvas-label"
              x={option.x + option.width / 2}
              y={option.y + option.height / 2}
            >
              {option.label}
            </text>
          </g>
        ))}
      </svg>}
      <div className="generic-choice-options">
        {markSet.options.map((option) => (
          <div className="generic-choice-option" key={option.id}>
            {onChoiceSelect && <button type="button" className="ghost compact" aria-pressed={selectedChoiceId === option.id}
              aria-label={`${option.label}を操作対象に選択`} onClick={() => onChoiceSelect(markSet.id, option.id)}>操作対象に選択</button>}
            <label>
              {isSymbol ? "記号" : "表示名"}
              <input
                value={option.label}
                maxLength={100}
                onChange={(event) => patchOption(option.id, {
                  label: event.target.value,
                  ...((isSymbol || isChecklist) ? { value: event.target.value } : {}),
                })}
              />
            </label>
            {!isSymbol && !isChecklist && <label>
              内部識別値
              <input
                value={option.value}
                maxLength={100}
                onChange={(event) => patchOption(option.id, { value: event.target.value })}
              />
            </label>}
            {markSet.recognitionMode === "automatic" && (["x", "y", "width", "height"] as const).map((key) => (
              <label key={key}>
                {{ x: "X", y: "Y", width: "幅", height: "高さ" }[key]}
                <input
                  type="number"
                  min={key === "width" || key === "height" ? 1 : 0}
                  value={option[key]}
                  onChange={(event) => patchOption(option.id, {
                    [key]: Math.max(
                      key === "width" || key === "height" ? 1 : 0,
                      Number(event.target.value) || 0,
                    ),
                  })}
                />
              </label>
            ))}
            <button
              type="button"
              className="danger-button compact"
              onClick={() => patchSet({
                options: reflowChoiceOptions(
                  cell,
                  markSet.options.filter((item) => item.id !== option.id),
                ),
              })}
            >
              削除
            </button>
          </div>
        ))}
      </div>
      <div className="choice-option-addition">
        <label>
          {isSymbol ? "新しい記号" : "新しい選択肢"}
          <input
            value={newOptionLabel}
            maxLength={100}
            placeholder={isSymbol ? "例: □" : "例: 有"}
            onChange={(event) => setNewOptionLabel(event.target.value)}
          />
        </label>
        <button
          type="button"
          className="secondary compact"
          disabled={!cleanedNewOption || duplicateNewOption || (isChecklist && /[・、,;\n\r]/.test(cleanedNewOption)) || markSet.options.length >= 50}
          onClick={() => {
            const label = cleanedNewOption;
            if (!label) return;
            const nextOptions = [
              ...markSet.options,
              {
                id: nextChoiceOptionId(markSet.options),
                label,
                value: label,
                x: 0,
                y: 0,
                width: 1,
                height: Math.max(1, cell.height),
              },
            ];
            patchSet({ options: reflowChoiceOptions(cell, nextOptions) });
            setNewOptionLabel("");
          }}
        >
          選択肢を追加
        </button>
        {duplicateNewOption && (
          <small className="error-text">同じ表示名または内部識別値は追加できません。</small>
        )}
      </div>
    </fieldset>
  );
}
