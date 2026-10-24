import { Fragment, type ReactNode } from "react";

/**
 * A small Markdown renderer for chat answers: headings (#〜###), bullet and numbered lists,
 * paragraphs and line breaks, **bold**, *italic*, `code` and backslash escapes.
 * Only React elements are produced (no HTML is parsed), so answer text cannot inject markup.
 * "_" is not emphasis: document names like 「UX検証_こよみ」 must stay as written.
 * `links`: words (a document's name) whose first appearance in the text becomes a link.
 */
export type MarkdownLink = { text: string; onClick: () => void; active?: boolean; title?: string };

export function Markdown({ text, className, links }: { text: string; className?: string; links?: MarkdownLink[] }) {
  // Longest first, so 「UX検証_こよみ」 is not linked as 「こよみ」.
  const words = (links ?? []).filter((link) => link.text).sort((a, b) => b.text.length - a.text.length);
  const context: Context = { links: words, used: new Set() };
  return <div className={className}>{blocks(text, context)}</div>;
}

type Context = { links: MarkdownLink[]; used: Set<string> };

type Block =
  | { kind: "heading"; level: number; text: string }
  | { kind: "list"; ordered: boolean; items: string[] }
  | { kind: "paragraph"; lines: string[] };

function parse(text: string): Block[] {
  const result: Block[] = [];
  for (const line of text.replace(/\r\n?/g, "\n").split("\n")) {
    const last = result[result.length - 1];
    const heading = /^(#{1,3})\s+(.*)$/.exec(line);
    const bullet = /^\s*[-*・]\s+(.*)$/.exec(line);
    const numbered = /^\s*\d+[.)]\s+(.*)$/.exec(line);
    if (!line.trim()) {
      result.push({ kind: "paragraph", lines: [] });
    } else if (heading) {
      result.push({ kind: "heading", level: heading[1].length, text: heading[2] });
    } else if (bullet || numbered) {
      const ordered = !bullet;
      const item = (bullet ?? numbered)![1];
      if (last?.kind === "list" && last.ordered === ordered) last.items.push(item);
      else result.push({ kind: "list", ordered, items: [item] });
    } else if (last?.kind === "paragraph") {
      last.lines.push(line);
    } else {
      result.push({ kind: "paragraph", lines: [line] });
    }
  }
  return result.filter((block) => block.kind !== "paragraph" || block.lines.length > 0);
}

function blocks(text: string, context: Context): ReactNode[] {
  return parse(text).map((block, index) => {
    if (block.kind === "heading") {
      const Tag = (["h3", "h4", "h5"] as const)[block.level - 1];
      return <Tag key={index}>{inline(block.text, context)}</Tag>;
    }
    if (block.kind === "list") {
      const Tag = block.ordered ? "ol" : "ul";
      return <Tag key={index}>{block.items.map((item, i) => <li key={i}>{inline(item, context)}</li>)}</Tag>;
    }
    return (
      <p key={index}>
        {block.lines.map((line, i) => <Fragment key={i}>{i > 0 && <br />}{inline(line, context)}</Fragment>)}
      </p>
    );
  });
}

/** Inline marks, innermost first: escapes and code are literal, then bold, then italic. */
function inline(text: string, context: Context): ReactNode[] {
  const out: ReactNode[] = [];
  let plain = "";
  let i = 0;
  const flush = () => { if (plain) { out.push(...linked(plain, context, out.length)); plain = ""; } };
  while (i < text.length) {
    const char = text[i];
    if (char === "\\" && i + 1 < text.length) {
      plain += text[i + 1];
      i += 2;
      continue;
    }
    if (char === "`") {
      const end = text.indexOf("`", i + 1);
      if (end > i + 1) {
        flush();
        out.push(<code key={out.length}>{text.slice(i + 1, end)}</code>);
        i = end + 1;
        continue;
      }
    }
    if (text.startsWith("**", i)) {
      const end = closing(text, "**", i + 2);
      if (end > i + 2) {
        flush();
        out.push(<strong key={out.length}>{inline(text.slice(i + 2, end), context)}</strong>);
        i = end + 2;
        continue;
      }
    }
    if (char === "*" && text[i + 1] !== "*") {
      const end = closing(text, "*", i + 1);
      if (end > i + 1) {
        flush();
        out.push(<em key={out.length}>{inline(text.slice(i + 1, end), context)}</em>);
        i = end + 1;
        continue;
      }
    }
    plain += char;
    i += 1;
  }
  flush();
  return out;
}

/** Plain text with each link word's first appearance (in the whole answer) made a link. */
function linked(text: string, context: Context, base: number): ReactNode[] {
  let best: { at: number; link: MarkdownLink } | null = null;
  for (const link of context.links) {
    if (context.used.has(link.text)) continue;
    const at = text.indexOf(link.text);
    if (at >= 0 && (best === null || at < best.at)) best = { at, link };
  }
  if (best === null) return [text];
  const { at, link } = best;
  context.used.add(link.text);
  const before = text.slice(0, at);
  const after = text.slice(at + link.text.length);
  return [
    ...(before ? [before] : []),
    <button key={`link-${base}-${at}`} type="button" className={link.active ? "md-link active" : "md-link"}
      aria-pressed={link.active ?? false} title={link.title} onClick={link.onClick}>{link.text}</button>,
    ...linked(after, context, base + 1),
  ];
}

/** The next unescaped `mark` at or after `from`, or -1. */
function closing(text: string, mark: string, from: number): number {
  for (let i = from; i < text.length; i += 1) {
    if (text[i] === "\\") { i += 1; continue; }
    if (text.startsWith(mark, i) && (mark !== "*" || text[i + 1] !== "*")) return i;
    if (mark === "*" && text.startsWith("**", i)) i += 1;
  }
  return -1;
}
