/**
 * Items shown as one value (「調査場所」 = 高槻市美大町3丁目1番12号), or only shown in another
 * order. Display only, per template series: each item keeps its own reading, crop, warning
 * and correction.
 */
export type ItemJoinMember = { targetPath: string; before: string; after: string; label?: string | null };
/**
 * `kind`: "join" shows the items as one value; "order" only sets their display order;
 * "sections" orders the blocks of the result (tables, headings), one member path per block.
 */
export type ItemJoin = { id?: string | null; name: string; kind?: "join" | "order" | "sections"; separator?: string; members: ItemJoinMember[] };
export type ItemSection = { name: string; members: Array<{ targetPath: string; label: string; kind: "text" | "mark" }> };
/** A table, a heading or items under no heading, in form order; `key` is its first item's path. */
export type ItemBlock = { kind: "record" | "parent" | "independent"; title: string; key: string; paths: string[] };
export type ItemJoinSettings = { items: ItemJoin[]; suggestions: ItemJoin[]; sections?: ItemSection[]; blocks?: ItemBlock[] };

/**
 * Each non-empty item with its printed text, in order; an empty item drops its text too.
 * `separator` goes only between two items with no printed text between them (姓 名).
 */
export function joinedText(members: readonly ItemJoinMember[], valueOf: (path: string) => string | null | undefined, separator = ""): string {
  let text = "";
  let previousAfter: string | null = null;
  for (const member of members) {
    const value = (valueOf(member.targetPath) ?? "").trim();
    if (!value) continue;
    if (previousAfter !== null && !previousAfter && !member.before) text += separator;
    text += `${member.before}${value}${member.after}`;
    previousAfter = member.after;
  }
  return text;
}

export function isJoin(item: ItemJoin): boolean {
  return (item.kind ?? "join") === "join";
}

/** Path → its join (only "join" items). */
export function joinByPath(items: readonly ItemJoin[]): Map<string, ItemJoin> {
  const result = new Map<string, ItemJoin>();
  for (const join of items.filter(isJoin)) for (const member of join.members) result.set(member.targetPath, join);
  return result;
}

export function displayOrders(items: readonly ItemJoin[]): ItemJoin[] {
  return items.filter((item) => item.kind === "order");
}

/** Units (by their key path) in a saved display order; unlisted ones go after, in place. */
export function sortByOrder<T>(units: readonly T[], keyOf: (unit: T) => string, orders: readonly ItemJoin[]): T[] {
  for (const order of orders) {
    const position = new Map(order.members.map((member, index) => [member.targetPath, index]));
    if (units.filter((unit) => position.has(keyOf(unit))).length < 2) continue;
    return units
      .map((unit, index) => ({ unit, index, rank: position.get(keyOf(unit)) ?? position.size }))
      .sort((a, b) => a.rank - b.rank || a.index - b.index)
      .map((item) => item.unit);
  }
  return [...units];
}

/** The saved block order, if any (one member path per block). */
export function sectionOrder(items: readonly ItemJoin[]): ItemJoin | undefined {
  return items.find((item) => item.kind === "sections");
}

/** Blocks in the saved block order; blocks it does not name keep their form order after. */
export function sortBlocks<T extends { paths: readonly string[] }>(blocks: readonly T[], items: readonly ItemJoin[]): T[] {
  const order = sectionOrder(items);
  if (!order) return [...blocks];
  const position = new Map(order.members.map((member, index) => [member.targetPath, index]));
  const rank = (block: T) => Math.min(position.size, ...block.paths.map((path) => position.get(path) ?? position.size));
  return blocks
    .map((block, index) => ({ block, index, rank: rank(block) }))
    .sort((a, b) => a.rank - b.rank || a.index - b.index)
    .map((item) => item.block);
}

/** ①②③… for the order numbers (plain digits past ⑳). */
export function orderMark(index: number): string {
  return index < 20 ? String.fromCodePoint(0x2460 + index) : `${index + 1}.`;
}
