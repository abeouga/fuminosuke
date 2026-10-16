import type { LayoutCell } from "../types";

export type OcrLabelAlignment = "auto" | "left" | "center" | "right";
export type OcrLabelVerticalAlignment = "auto" | "top" | "middle" | "bottom";
export function drawUnassignedMeaningOverlay(
  context: CanvasRenderingContext2D,
  image: CanvasImageSource,
  cells: LayoutCell[],
  canonicalWidth: number,
  canonicalHeight: number,
) {
  context.save();
  context.fillStyle = "rgba(10, 20, 29, 0.68)";
  context.fillRect(0, 0, canonicalWidth, canonicalHeight);
  context.restore();

  cells.forEach((cell, index) => {
    context.save();
    context.drawImage(
      image,
      cell.x,
      cell.y,
      cell.width,
      cell.height,
      cell.x,
      cell.y,
      cell.width,
      cell.height,
    );
    context.fillStyle = "rgba(255, 214, 10, 0.28)";
    context.fillRect(cell.x, cell.y, cell.width, cell.height);
    context.beginPath();
    context.rect(cell.x, cell.y, cell.width, cell.height);
    context.clip();
    context.strokeStyle = "rgba(120, 25, 15, 0.58)";
    context.lineWidth = Math.max(2, canonicalWidth / 900);
    const stripeGap = Math.max(12, canonicalWidth / 120);
    for (
      let offset = -cell.height;
      offset < cell.width + cell.height;
      offset += stripeGap
    ) {
      context.beginPath();
      context.moveTo(cell.x + offset, cell.y + cell.height);
      context.lineTo(cell.x + offset + cell.height, cell.y);
      context.stroke();
    }
    context.restore();

    context.save();
    context.strokeStyle = "#b42318";
    context.lineWidth = Math.max(7, canonicalWidth / 240);
    context.setLineDash([]);
    context.strokeRect(cell.x, cell.y, cell.width, cell.height);
    context.strokeStyle = "#fff3b0";
    context.lineWidth = Math.max(2, canonicalWidth / 800);
    context.strokeRect(
      cell.x + Math.max(4, canonicalWidth / 600),
      cell.y + Math.max(4, canonicalWidth / 600),
      Math.max(1, cell.width - Math.max(8, canonicalWidth / 300)),
      Math.max(1, cell.height - Math.max(8, canonicalWidth / 300)),
    );
    const fontSize = Math.max(13, Math.min(24, canonicalWidth / 70));
    const badgeText = `未設定 ${index + 1}`;
    context.font = `900 ${fontSize}px sans-serif`;
    const badgeHeight = fontSize + 12;
    const badgeWidth = Math.max(82, context.measureText(badgeText).width + 20);
    const badgeX = Math.max(0, Math.min(cell.x, canonicalWidth - badgeWidth));
    const badgeY = Math.max(0, cell.y - badgeHeight);
    context.fillStyle = "#b42318";
    context.fillRect(badgeX, badgeY, badgeWidth, badgeHeight);
    context.fillStyle = "#ffffff";
    context.textAlign = "left";
    context.textBaseline = "middle";
    context.fillText(badgeText, badgeX + 10, badgeY + badgeHeight / 2);
    context.restore();
  });
}
export const LAYOUT_ROLE_STYLE = {
  label: { stroke: "#d13c2f", fill: "#d13c2f", dash: [] },
  parent_label: { stroke: "#a21caf", fill: "#a21caf", dash: [12, 5] },
  input: { stroke: "#0067c0", fill: "#0067c0", dash: [] },
  mark: { stroke: "#6d28d9", fill: "#6d28d9", dash: [7, 4] },
  // Legacy layouts are normalized to input before display. Keep defensive
  // styles so an old browser draft never fails while it is being migrated.
  record_key_cell: { stroke: "#0067c0", fill: "#0067c0", dash: [] },
  column_key_cell: { stroke: "#0067c0", fill: "#0067c0", dash: [] },
  ignored: { stroke: "#59636e", fill: "#59636e", dash: [12, 8] },
} as const;
export const LAYOUT_ROLE_LABEL: Record<LayoutCell["role"], string> = {
  label: "項目名", parent_label: "見出し", input: "記入欄", mark: "記入欄（選択・記号）",
  record_key_cell: "記入欄", column_key_cell: "記入欄", ignored: "読み取らない",
};
const OCR_LABEL_BADGE_STYLE = {
  label: {
    background: "rgba(255, 240, 213, 0.92)",
    text: "#5f3a00",
  },
  parent_label: {
    background: "rgba(226, 243, 236, 0.92)",
    text: "#0f513d",
  },
} as const;
function wrapCanvasText(
  context: CanvasRenderingContext2D,
  text: string,
  maxWidth: number,
) {
  const lines: string[] = [];
  text.split(/\r?\n/).forEach((sourceLine) => {
    const characters = Array.from(sourceLine);
    if (characters.length === 0) {
      lines.push("");
      return;
    }
    let currentLine = "";
    characters.forEach((character) => {
      const candidate = `${currentLine}${character}`;
      if (currentLine && context.measureText(candidate).width > maxWidth) {
        lines.push(currentLine);
        currentLine = character;
      } else {
        currentLine = candidate;
      }
    });
    if (currentLine) lines.push(currentLine);
  });
  return lines;
}
function balanceCanvasText(text: string, lineCount: number) {
  const characters = Array.from(text.replace(/\r?\n/g, ""));
  const charactersPerLine = Math.max(1, Math.ceil(characters.length / lineCount));
  return Array.from({ length: lineCount }, (_, index) => (
    characters.slice(index * charactersPerLine, (index + 1) * charactersPerLine).join("")
  )).filter(Boolean);
}
export function drawOcrLabelText(
  context: CanvasRenderingContext2D,
  cell: LayoutCell,
  canonicalWidth: number,
  alignment: OcrLabelAlignment = "auto",
  verticalAlignment: OcrLabelVerticalAlignment = "auto",
) {
  if (cell.role !== "label" && cell.role !== "parent_label") return;
  const text = cell.text.trim();
  if (!text) return;
  const resolvedAlignment = alignment === "auto"
    ? cell.textAlignment ?? "left"
    : alignment;
  const padding = 4;
  const resolvedVerticalAlignment = verticalAlignment === "auto"
    ? cell.textVerticalAlignment ?? "top"
    : verticalAlignment;
  const compactText = Array.from(text.replace(/\s+/g, ""));
  const inferredDirection = cell.textDirection
    ?? (compactText.length >= 2 && cell.height >= cell.width * 1.4 ? "vertical" : "horizontal");
  const fontSize = Math.max(8, Math.min(
    22,
    inferredDirection === "vertical" ? cell.width - 6 : cell.height - 6,
    inferredDirection === "vertical" && compactText.length > 0
      ? (cell.height - padding * 2 - 4) / compactText.length
      : canonicalWidth / 85,
  ));
  const badgeX = cell.x + 2;
  const alignedBadgeY = (height: number) => cell.y + 2 + Math.max(0, cell.height - 4 - height)
    * (resolvedVerticalAlignment === "middle" ? 0.5 : resolvedVerticalAlignment === "bottom" ? 1 : 0);
  const badgeWidth = Math.max(1, cell.width - 4);
  const badgeStyle = OCR_LABEL_BADGE_STYLE[cell.role];
  context.save();
  if (inferredDirection === "vertical" && compactText.length > 0) {
    const verticalBadgeWidth = Math.max(1, Math.min(badgeWidth, fontSize + padding * 2));
    const verticalBadgeX = resolvedAlignment === "left"
      ? badgeX
      : resolvedAlignment === "right"
        ? badgeX + badgeWidth - verticalBadgeWidth
        : badgeX + (badgeWidth - verticalBadgeWidth) / 2;
    const verticalBadgeHeight = Math.max(1, Math.min(
      cell.height - 4, fontSize * 1.15 * compactText.length + padding * 2,
    ));
    const badgeY = alignedBadgeY(verticalBadgeHeight);
    context.fillStyle = badgeStyle.background;
    context.fillRect(verticalBadgeX, badgeY, verticalBadgeWidth, verticalBadgeHeight);
    context.fillStyle = badgeStyle.text;
    context.font = `700 ${fontSize}px sans-serif`;
    context.textBaseline = "middle";
    context.textAlign = "center";
    const step = Math.max(1, (verticalBadgeHeight - padding * 2) / compactText.length);
    compactText.forEach((character, index) => {
      context.fillText(
        character,
        verticalBadgeX + verticalBadgeWidth / 2,
        badgeY + padding + step * (index + 0.5),
        Math.max(1, verticalBadgeWidth - padding * 2),
      );
    });
    context.restore();
    return;
  }
  const maxTextWidth = Math.max(1, badgeWidth - padding * 2);
  const availableTextHeight = Math.max(1, cell.height - 4 - padding * 2);
  const minimumReadableFontSize = Math.min(
    fontSize,
    Math.max(8, canonicalWidth / 120),
  );
  context.font = `700 ${fontSize}px sans-serif`;
  const singleLineWidth = context.measureText(text).width;
  const effectiveSingleLineFontSize = fontSize * Math.min(
    1,
    maxTextWidth / Math.max(singleLineWidth, 1),
  );
  const shouldWrap = (
    (text.includes("\n") || effectiveSingleLineFontSize < minimumReadableFontSize)
    && availableTextHeight >= minimumReadableFontSize * 2.2
  );
  let renderFontSize = fontSize;
  let lines = [text];
  let lineHeight = fontSize * 1.15;
  if (shouldWrap) {
    for (
      let candidateFontSize = fontSize;
      candidateFontSize >= minimumReadableFontSize;
      candidateFontSize -= 1
    ) {
      context.font = `700 ${candidateFontSize}px sans-serif`;
      const candidateLineHeight = candidateFontSize * 1.15;
      const maximumLines = Math.min(
        4,
        Math.floor(availableTextHeight / candidateLineHeight),
      );
      if (maximumLines < 2) continue;
      const candidateLines = wrapCanvasText(context, text, maxTextWidth);
      if (candidateLines.length >= 2 && candidateLines.length <= maximumLines) {
        renderFontSize = candidateFontSize;
        lineHeight = candidateLineHeight;
        lines = candidateLines;
        break;
      }
    }
    if (lines.length === 1) {
      renderFontSize = minimumReadableFontSize;
      lineHeight = renderFontSize * 1.15;
      context.font = `700 ${renderFontSize}px sans-serif`;
      const maximumLines = Math.min(4, Math.floor(availableTextHeight / lineHeight));
      const wrappedLines = wrapCanvasText(context, text, maxTextWidth);
      lines = wrappedLines.length <= maximumLines
        ? wrappedLines
        : balanceCanvasText(text, Math.max(2, maximumLines));
    }
  }
  const badgeHeight = Math.max(
    1,
    Math.min(cell.height - 4, lineHeight * lines.length + padding * 2),
  );
  const badgeY = alignedBadgeY(badgeHeight);
  context.fillStyle = badgeStyle.background;
  context.fillRect(badgeX, badgeY, badgeWidth, badgeHeight);
  context.fillStyle = badgeStyle.text;
  context.font = `700 ${renderFontSize}px sans-serif`;
  context.textBaseline = "top";
  context.textAlign = resolvedAlignment;
  const textX = resolvedAlignment === "left"
    ? badgeX + padding
    : resolvedAlignment === "center"
      ? badgeX + badgeWidth / 2
      : badgeX + badgeWidth - padding;
  lines.forEach((line, index) => {
    context.fillText(
      line,
      textX,
      badgeY + padding + lineHeight * index,
      maxTextWidth,
    );
  });
  context.restore();
}
export function drawCompositeSubfields(
  context: CanvasRenderingContext2D,
  cell: LayoutCell,
  canonicalWidth: number,
) {
  if (cell.role !== "input" || !cell.compositeSubfields?.length) return;
  const fontSize = Math.max(8, Math.min(14, canonicalWidth / 100));
  context.save();
  context.strokeStyle = "#0f766e";
  context.fillStyle = "rgba(15, 118, 110, 0.9)";
  context.lineWidth = Math.max(1, canonicalWidth / 700);
  context.setLineDash([Math.max(4, canonicalWidth / 220), Math.max(3, canonicalWidth / 300)]);
  context.font = `700 ${fontSize}px sans-serif`;
  context.textBaseline = "top";
  context.textAlign = "left";
  cell.compositeSubfields.forEach((subfield, index) => {
    const x = cell.x + cell.width * subfield.xRatio;
    if (index > 0) {
      context.beginPath();
      context.moveTo(x, cell.y + 2);
      context.lineTo(x, cell.y + cell.height - 2);
      context.stroke();
    }
    context.fillText(
      subfield.label,
      x + 4,
      cell.y + 4,
      Math.max(1, cell.width * subfield.widthRatio - 8),
    );
  });
  context.restore();
}
