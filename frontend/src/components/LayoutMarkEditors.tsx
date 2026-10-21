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
/**
 * New choice IDs are never fixed names such as "choice_1": a draft made from an active
 * template refuses IDs the earlier version used, so a reused fixed name would block saving.
 */
/** Checklist results join checked names with "・", so a name may not contain a separator. */
export function checklistLabel(name: string): string {
  return name.replace(/[・、,;\n\r]+/g, "／").replace(/^／+|／+$/g, "").trim();
}
export function newMarkSetId(): string {
  return `mark_set_${crypto.randomUUID().replace(/-/g, "")}`;
}
export function newMarkOptionId(): string {
  return `mark_option_${crypto.randomUUID().replace(/-/g, "")}`;
}
function nextChoiceOptionId(_options: LayoutMarkSet["options"]): string {
  return newMarkOptionId();
}
export function genericManualChoicePreset(
  cell: LayoutCell,
  targetPath = "",
): LayoutMarkSet[] {
  const labels = suggestedChoiceLabels(cell.text);
  return [{
    id: newMarkSetId(),
    targetPath,
    recognitionMode: "manual",
    selectionMode: "single",
    options: reflowChoiceOptions(
      cell,
      labels.map((label) => ({
        id: newMarkOptionId(),
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
  const listed = [...new Set(cell.text.split(/[□☐☑☒■]/).slice(1).map(label => checklistLabel(label)).filter(Boolean))];
  // A box without listed choices (a lone "□", or a box imported without text) is one checkbox.
  const labels = listed.length ? listed : ["チェック済み"];
  return [{ id: newMarkSetId(), targetPath, recognitionMode: "checklist", selectionMode: "multiple",
    options: labels.map((label) => ({ id: newMarkOptionId(), label, value: label,
      x: 0, y: 0, width: cell.width, height: cell.height })) }];
}
export function MarkCellEditor({
  cell,
  sets,
  targets,
  writeInTargets = [],
  source,
  canonicalWidth,
  canonicalHeight,
  onChange,
  selectedChoiceId,
  onChoiceSelect,
  hideTarget = false,
}: {
  cell: LayoutCell;
  sets: LayoutMarkSet[];
  targets: FieldMappingTarget[];
  writeInTargets?: FieldMappingTarget[];
  source: string;
  canonicalWidth: number;
  canonicalHeight: number;
  onChange: (sets: LayoutMarkSet[]) => void;
  selectedChoiceId?: string | null;
  onChoiceSelect?: (markSetId: string, choiceId: string) => void;
  /** The storage target already comes from the label connection drawn on the form. */
  hideTarget?: boolean;
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
      {!hideTarget && <label>
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
      </label>}
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
        {isSymbol ? "この欄全体の画像から、候補の記号を1つ判定します。空欄は空欄、候補外や判読できない記号は確認対象にします。「その他（　）」のように記入欄がある項目は「記入欄」を選ぶと、確認画面で記号と記入内容を1行に並べ、記入があるのに記号が空欄なら要確認にします。"
          : isChecklist ? "欄全体をOCRで読み、チェックされた項目を複数取得します。全未選択と判別不能を区別します。項目名はチェック記号を除いて入力してください。区切り文字（・、,;）は使えません。「その他（　）」のように記入欄がある項目は「記入欄」を選ぶと、記入があればチェックありとして扱います。"
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
      {isSymbol && <label title="この項目の「その他（　）」などの記入欄。確認画面で記号と並べて表示します。">
        記入欄
        <select
          value={markSet.options.find((option) => option.writeInTargetPath)?.writeInTargetPath ?? ""}
          onChange={(event) => {
            const path = event.target.value || undefined;
            // One symbol (○, or the first) holds the item's write-in.
            const holder = markSet.options.find((option) => option.label === "○") ?? markSet.options[0];
            patchSet({ options: markSet.options.map(({ writeInTargetPath: _old, ...option }) => (
              path && option.id === holder?.id ? { ...option, writeInTargetPath: path } : option
            )) });
          }}
        >
          <option value="">なし</option>
          {writeInTargets.map((target) => <option value={target.path} key={target.path}>{target.label}</option>)}
        </select>
      </label>}
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
                onChange={(event) => {
                  const label = isChecklist ? event.target.value.replace(/[・、,;\n\r]/g, "／") : event.target.value;
                  patchOption(option.id, { label, ...((isSymbol || isChecklist) ? { value: label } : {}) });
                }}
              />
            </label>
            {isChecklist && <label title="この記入欄に文字があれば、この項目にチェックがあるものとして扱います。">
              記入欄
              <select
                value={option.writeInTargetPath ?? ""}
                onChange={(event) => patchOption(option.id, { writeInTargetPath: event.target.value || undefined })}
              >
                <option value="">なし</option>
                {writeInTargets.map((target) => (
                  <option value={target.path} key={target.path}
                    disabled={markSet.options.some((other) => other.id !== option.id && other.writeInTargetPath === target.path)}>
                    {target.label}
                  </option>
                ))}
              </select>
            </label>}
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
