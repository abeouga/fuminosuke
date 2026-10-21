import type { TemplateVersion } from "./types";

// Pressing "編集" forks the active template into a draft right away (revision 1). Every
// save bumps the version revision, so an untouched fork is not yet "being edited": the
// active template stays usable and the list shows it as in use until a value changes.
export function isEditingDraft(version: TemplateVersion) {
  if (version.status !== "DRAFT") return false;
  return !version.quality.forkParentVersionId || version.revision > 1;
}

/** 「〇〇 のコピー」, numbered when that name is already taken. */
export function suggestedCopyName(sourceName: string, existingNames: string[]): string {
  const base = `${sourceName} のコピー`;
  const existing = new Set(existingNames.map((name) => name.normalize("NFKC").trim().toLocaleLowerCase()));
  for (let number = 1; ; number += 1) {
    const suffix = number === 1 ? "" : ` ${number}`;
    const name = `${base.slice(0, 200 - suffix.length)}${suffix}`;
    if (!existing.has(name.normalize("NFKC").trim().toLocaleLowerCase())) return name;
  }
}
