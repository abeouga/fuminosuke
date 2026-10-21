import { useEffect, useMemo, useState } from "react";
import { ApiError, api } from "../api";
import { DndContext, KeyboardSensor, PointerSensor, closestCenter, useSensor, useSensors, type DragEndEvent } from "@dnd-kit/core";
import { SortableContext, arrayMove, sortableKeyboardCoordinates, useSortable, verticalListSortingStrategy } from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import { isJoin, type ItemJoin, type ItemJoinMember, type ItemSection } from "../itemJoins";
import type { FieldMapping } from "../types";

type Draft = ItemJoin & { key: string; kind: "join" | "order"; separator: string };
type Suggestion = ItemJoin & { ai?: boolean };

const SEPARATORS: Array<{ value: string; label: string }> = [
  { value: "", label: "なし" },
  { value: " ", label: "空白" },
  { value: "-", label: "-" },
  { value: "・", label: "・" },
  { value: "、", label: "、" },
];

/**
 * 「項目のまとめ方・順番」: items shown as one value, or only in another order, on the result
 * screen, the shelf and the list XLSX. Display only; readings, crops and the template stay as
 * they are. Opened from the template list, after activation, or over the result screen.
 */
export function TemplateItemJoinEditor({
  templateId,
  versionId,
  overlay = false,
  onClose,
  onSaved,
  onNavigationGuardChange,
}: {
  templateId: string;
  /** Version whose form and headings are shown; the active (else latest) one when omitted. */
  versionId?: string | null;
  overlay?: boolean;
  onClose: () => void;
  onSaved?: (items: ItemJoin[]) => void;
  onNavigationGuardChange?: (shouldWarn: boolean) => void;
}) {
  const [templateName, setTemplateName] = useState("");
  const [shownVersionId, setShownVersionId] = useState<string | null>(null);
  const [artifactUrl, setArtifactUrl] = useState("");
  const [mappings, setMappings] = useState<FieldMapping[]>([]);
  const [size, setSize] = useState({ width: 1, height: 1 });
  const [drafts, setDrafts] = useState<Draft[]>([]);
  const [suggestions, setSuggestions] = useState<Suggestion[]>([]);
  const [sections, setSections] = useState<ItemSection[]>([]);
  const [saved, setSaved] = useState("");
  const [selected, setSelected] = useState<string | null>(null);
  const [picking, setPicking] = useState(false);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [asking, setAsking] = useState(false);
  const [feedback, setFeedback] = useState<{ kind: "success" | "error"; text: string } | null>(null);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setFeedback(null);
    void (async () => {
      const template = await api.template(templateId);
      const versions = template.versions;
      const version = versions.find((item) => item.id === versionId)
        ?? versions.find((item) => item.status === "ACTIVE")
        ?? [...versions].sort((a, b) => b.versionNumber - a.versionNumber)[0];
      if (!version) throw new Error("テンプレートの設定が見つかりません。");
      const [authoring, settings, artifacts] = await Promise.all([
        api.authoring(version.id),
        api.itemJoins(templateId, version.id),
        version.sourceJobId ? api.artifacts(version.sourceJobId) : Promise.resolve({ items: [] }),
      ]);
      if (cancelled) return;
      const byName = new Map(artifacts.items.map((artifact) => [artifact.name, artifact.url]));
      setTemplateName(template.name);
      setShownVersionId(version.id);
      setArtifactUrl(byName.get("cleanPreview") ?? byName.get("cleanTemplate") ?? byName.get("normalized") ?? byName.get("alignedSource.1") ?? "");
      // Joins and orders are for plain items; table rows stay in their table.
      setMappings(authoring.mapping.mappings.filter((mapping) => mapping.enabled && /^fields\.[^.]+$/.test(mapping.targetPath)));
      setSize({ width: authoring.mapping.canonicalWidth, height: authoring.mapping.canonicalHeight });
      const next = settings.items.map(toDraft);
      setDrafts(next);
      setSuggestions(settings.suggestions);
      setSections(settings.sections ?? []);
      setSaved(signature(next));
      setSelected(next[0]?.key ?? null);
    })().catch((error) => {
      if (!cancelled) setFeedback({ kind: "error", text: errorText(error, "項目のまとめ方を読み込めませんでした。") });
    }).finally(() => {
      if (!cancelled) setLoading(false);
    });
    return () => { cancelled = true; };
  }, [attempt, templateId, versionId]);

  const dirty = !loading && signature(drafts) !== saved;
  useEffect(() => { onNavigationGuardChange?.(dirty || saving); }, [dirty, onNavigationGuardChange, saving]);
  useEffect(() => () => onNavigationGuardChange?.(false), [onNavigationGuardChange]);

  const labelOf = useMemo(() => {
    const names = new Map(mappings.map((mapping) => [mapping.targetPath, mapping.label]));
    for (const section of sections) for (const member of section.members) if (member.label) names.set(member.targetPath, member.label);
    for (const suggestion of suggestions) {
      for (const member of suggestion.members) if (member.label) names.set(member.targetPath, member.label);
    }
    // 「番地)」: an unpaired bracket printed next to the item is not part of its name.
    return (path: string) => unpaired(names.get(path) ?? "") || "名称未設定の欄";
  }, [mappings, sections, suggestions]);
  // Printed text found around an item by the suggestions, reused when it is added by hand.
  const glueOf = useMemo(() => new Map(suggestions.flatMap((suggestion) => suggestion.members.map((member) => [member.targetPath, member] as const))), [suggestions]);
  const joins = drafts.filter((draft) => draft.kind === "join");
  const joinOf = useMemo(() => {
    const owners = new Map<string, Draft>();
    for (const draft of drafts) if (draft.kind === "join") for (const member of draft.members) owners.set(member.targetPath, draft);
    return owners;
  }, [drafts]);
  /** An item's place in a display order: a joined item moves with its join's first item. */
  const unitKey = (path: string) => joinOf.get(path)?.members[0]?.targetPath ?? path;
  const unitLabel = (key: string) => {
    const join = joinOf.get(key);
    return join && join.members[0]?.targetPath === key ? `${join.name}（まとめた値）` : labelOf(key);
  };
  const sectionUnits = (section: ItemSection) => [...new Set(section.members.map((member) => unitKey(member.targetPath)))];
  const joinOfSection = (section: ItemSection) => joins.find((draft) => draft.members.some((member) => section.members.some((item) => item.targetPath === member.targetPath)));
  const orderOfSection = (section: ItemSection) => drafts.find((draft) => draft.kind === "order" && draft.name === section.name);
  /** The heading's items (a join as one) in the saved order, else as on the form. */
  const unitsInOrder = (section: ItemSection) => {
    const units = sectionUnits(section);
    const order = orderOfSection(section);
    if (!order) return units;
    const rank = new Map(order.members.map((member, index) => [member.targetPath, index]));
    return [...units].sort((a, b) => (rank.get(a) ?? units.length) - (rank.get(b) ?? units.length));
  };
  const looseJoins = joins.filter((draft) => !sections.some((section) => joinOfSection(section)?.key === draft.key));
  const openSection = sections.find((section) => selected === `section:${section.name}`) ?? null;
  const openLoose = looseJoins.find((draft) => draft.key === selected) ?? null;
  /** The join being edited: the open heading's join, or an open join outside the headings. */
  const current = openLoose ?? (openSection ? joinOfSection(openSection) ?? null : null);

  const change = (key: string, update: (draft: Draft) => Draft) => {
    setDrafts((all) => all.map((draft) => draft.key === key ? update(draft) : draft));
    setFeedback(null);
  };
  const toggle = (key: string) => { setSelected((open) => open === key ? null : key); setPicking(false); setFeedback(null); };
  /** 「1つの値にまとめる」: the heading's text items in form order with their printed text. */
  const joinSection = (section: ItemSection) => {
    const proposal = suggestions.find((item) => item.name === section.name && item.ai)
      ?? suggestions.find((item) => item.name === section.name);
    const members = proposal?.members ?? section.members
      .filter((member) => member.kind === "text" && !joinOf.has(member.targetPath))
      .map((member) => ({ targetPath: member.targetPath, before: "", after: "" }));
    const draft = toDraft({ name: section.name, kind: "join", separator: proposal?.separator ?? "", members });
    setDrafts((all) => [...all, draft]);
    setFeedback(members.length < 2 ? { kind: "error", text: "まとめる欄を帳票で押して選んでください。" } : null);
    setPicking(members.length < 2);
  };
  const unjoin = (draft: Draft) => { setDrafts((all) => all.filter((item) => item.key !== draft.key)); setPicking(false); };
  /** Dragging a heading's items sets its display order; back to the form's order removes it. */
  const reorderSection = (section: ItemSection, keys: string[]) => {
    const natural = sectionUnits(section);
    const existing = orderOfSection(section);
    const same = keys.every((key, index) => key === natural[index]);
    setDrafts((all) => {
      const rest = all.filter((draft) => draft !== existing);
      if (same) return rest;
      const members = keys.map((targetPath) => ({ targetPath, before: "", after: "" }));
      return existing ? all.map((draft) => draft === existing ? { ...draft, members } : draft)
        : [...all, toDraft({ name: section.name, kind: "order", members })];
    });
    setFeedback(null);
  };
  const reorderJoin = (draft: Draft, keys: string[]) => change(draft.key, (item) => ({
    ...item, members: keys.map((key) => item.members.find((member) => member.targetPath === key)!).filter(Boolean),
  }));
  const pickCell = (path: string) => {
    if (!current) {
      const owner = joinOf.get(path);
      const section = sections.find((item) => item.members.some((member) => member.targetPath === path));
      if (owner && looseJoins.includes(owner)) setSelected(owner.key);
      else if (section) setSelected(`section:${section.name}`);
      return;
    }
    // While a join is open, a click on the form adds an item to it or takes it out.
    const member: ItemJoinMember = { targetPath: path, before: glueOf.get(path)?.before ?? "", after: glueOf.get(path)?.after ?? "" };
    setDrafts((all) => all.map((draft) => {
      if (draft.key === current.key) {
        return draft.members.some((item) => item.targetPath === path)
          ? { ...draft, members: draft.members.filter((item) => item.targetPath !== path) }
          : { ...draft, members: [...draft.members, member] };
      }
      return draft.kind === "join" ? { ...draft, members: draft.members.filter((item) => item.targetPath !== path) } : draft;
    }));
    setFeedback(null);
  };
  const askAi = async () => {
    if (!shownVersionId) return;
    setAsking(true);
    setFeedback(null);
    try {
      const result = await api.aiItemJoinSuggestions(templateId, shownVersionId);
      const fresh = result.suggestions.map((item) => ({ ...item, ai: true }));
      setSuggestions((all) => [...fresh, ...all.filter((item) => !item.ai)]);
      // Headings already joined are not proposed again; only the rest is news.
      const joined = new Set(joins.flatMap((draft) => draft.members.map((member) => member.targetPath)));
      const fresher = fresh.filter((item) => item.members.every((member) => !joined.has(member.targetPath)));
      setFeedback({ kind: "success", text: fresher.length > 0
        ? `AIは${fresher.map((item) => `「${item.name}」`).join("")}をまとめるのをおすすめしています（見出しに印を付けました）。`
        : fresh.length > 0 ? "AIのおすすめは、すでにまとめた見出しと同じでした。" : "AIのおすすめはありませんでした。" });
    } catch (error) {
      setFeedback({ kind: "error", text: errorText(error, "AIに提案を頼めませんでした。") });
    } finally {
      setAsking(false);
    }
  };
  const close = () => {
    if (saving) return;
    if (dirty && !window.confirm("保存していない変更があります。破棄して閉じますか？")) return;
    onClose();
  };
  const save = async () => {
    const incomplete = drafts.find((draft) => draft.members.length < 2);
    if (incomplete) {
      setFeedback({ kind: "error", text: `「${incomplete.name || "まとめた項目"}」には欄を2つ以上選んでください。` });
      return;
    }
    setSaving(true);
    setFeedback(null);
    try {
      const result = await api.saveItemJoins(templateId, drafts.map(({ name, kind, separator, members }) => ({
        name, kind, separator, members: members.map(({ targetPath, before, after }) => ({ targetPath, before, after })),
      })));
      const next = result.items.map(toDraft);
      setDrafts(next);
      setSaved(signature(next));
      setPicking(false);
      setFeedback({ kind: "success", text: "保存しました。読み取り済みの書類にも、この形で表示されます。" });
      onSaved?.(result.items);
    } catch (error) {
      setFeedback({ kind: "error", text: errorText(error, "保存できませんでした。") });
    } finally {
      setSaving(false);
    }
  };

  /** Numbers on the form: the open join's items, else the open heading's display order. */
  const badgeOf = (path: string): number | null => {
    if (current) {
      const index = current.members.findIndex((member) => member.targetPath === path);
      return index >= 0 ? index + 1 : null;
    }
    if (openSection) {
      const index = unitsInOrder(openSection).indexOf(unitKey(path));
      return index >= 0 && openSection.members.some((member) => member.targetPath === path) ? index + 1 : null;
    }
    return null;
  };
  /** The joined value as print + numbered items; an item's name only when the print lacks it. */
  const preview = (draft: Draft) => draft.members.length === 0 ? "—" : (
    <span className="item-join-tokens">
      {draft.members.map((member, index) => {
        const name = labelOf(member.targetPath);
        const named = !`${member.before}${member.after}`.includes(name);
        const gap = index > 0 && !member.before && !draft.members[index - 1].after ? draft.separator : "";
        return (
          <span key={member.targetPath} className="item-join-token-group">
            {gap && <span className="item-join-print">{gap === " " ? "␣" : gap}</span>}
            {member.before && <span className="item-join-print">{member.before}</span>}
            <span className="item-join-chip" title={name}><b>{index + 1}</b>{named && <span>{name}</span>}</span>
            {member.after && <span className="item-join-print">{member.after}</span>}
          </span>
        );
      })}
    </span>
  );
  const needsSeparator = (draft: Draft) => draft.members.some((member, index) => index > 0 && !member.before && !draft.members[index - 1].after);

  const joinEditor = (draft: Draft, section: ItemSection | null) => (
    <div className="item-join-panel">
      {section && (
        <label className="item-join-switch">
          <input type="checkbox" checked onChange={() => unjoin(draft)} />
          <span>1つの値にまとめる</span>
        </label>
      )}
      <p className="item-join-preview"><small>表示</small>{preview(draft)}</p>
      <p className="item-join-note">ドラッグで並べ替え。帳票の欄を押すと加えたり外したりできます。</p>
      <SortableList
        items={draft.members.map((member) => member.targetPath)}
        label={(path) => labelOf(path)}
        onReorder={(keys) => reorderJoin(draft, keys)}
        onRemove={(path) => change(draft.key, (item) => ({ ...item, members: item.members.filter((member) => member.targetPath !== path) }))}
      />
      {needsSeparator(draft) && (
        <div className="item-join-separator" role="group" aria-label="区切り">
          <span>欄と欄の間</span>
          {SEPARATORS.map((option) => (
            <button key={option.label} type="button" className={draft.separator === option.value ? "active" : ""} aria-pressed={draft.separator === option.value}
              onClick={() => change(draft.key, (item) => ({ ...item, separator: option.value }))}>{option.label}</button>
          ))}
        </div>
      )}
      <details className="item-join-details">
        <summary>名前と前後の文字</summary>
        <label className="field-label">表示する名前
          <input value={draft.name} maxLength={100} onChange={(event) => change(draft.key, (item) => ({ ...item, name: event.target.value }))} />
        </label>
        <p className="item-join-note">欄が空欄のときは、その前後の文字も出しません。</p>
        <ol className="item-join-members">
          {draft.members.map((member, index) => (
            <li key={member.targetPath}>
              <input className="item-join-glue" value={member.before} maxLength={30} placeholder="前" aria-label={`${labelOf(member.targetPath)}の前に付ける文字`}
                onChange={(event) => change(draft.key, (item) => ({ ...item, members: item.members.map((m, at) => at === index ? { ...m, before: event.target.value } : m) }))} />
              <span className="item-join-chip"><b>{index + 1}</b><span>{labelOf(member.targetPath)}</span></span>
              <input className="item-join-glue" value={member.after} maxLength={30} placeholder="後" aria-label={`${labelOf(member.targetPath)}の後に付ける文字`}
                onChange={(event) => change(draft.key, (item) => ({ ...item, members: item.members.map((m, at) => at === index ? { ...m, after: event.target.value } : m) }))} />
            </li>
          ))}
        </ol>
      </details>
      {!section && <button type="button" className="ghost compact danger-text" onClick={() => { unjoin(draft); setSelected(null); }}>まとめるのをやめる</button>}
    </div>
  );

  const headingRow = (section: ItemSection) => {
    const key = `section:${section.name}`;
    const open = selected === key;
    const join = joinOfSection(section);
    const order = orderOfSection(section);
    const proposal = !join ? suggestions.find((item) => item.name === section.name && item.ai) ?? suggestions.find((item) => item.name === section.name) : undefined;
    const units = unitsInOrder(section);
    return (
      <li key={key} className={`item-join-heading${open ? " open" : ""}`}>
        <button type="button" className="item-join-heading-toggle" aria-expanded={open} onClick={() => toggle(key)}>
          <span className="item-join-caret" aria-hidden="true">{open ? "▾" : "▸"}</span>
          <strong>{section.name}</strong>
          {join && <span className="item-join-tag">まとめて表示</span>}
          {order && <span className="item-join-tag">順番を変更</span>}
          {!join && proposal && <span className={`item-join-tag suggest${proposal.ai ? " ai" : ""}`}>{proposal.ai ? "AIのおすすめ" : "まとめられます"}</span>}
        </button>
        {open && (
          join ? (
            <>
              {joinEditor(join, section)}
              {units.length >= 2 && (
                <div className="item-join-panel">
                  <p className="item-join-note">見出しの中の並び（まとめた値は1つとして動きます）</p>
                  <SortableList items={units} label={unitLabel} onReorder={(keys) => reorderSection(section, keys)} />
                </div>
              )}
            </>
          ) : (
            <div className="item-join-panel">
              {section.members.filter((member) => member.kind === "text").length >= 2 && (
                <label className="item-join-switch">
                  <input type="checkbox" checked={false} onChange={() => joinSection(section)} />
                  <span>1つの値にまとめる{proposal && <small>{proposal.ai ? "（AIのおすすめ）" : "（帳票の並びからまとめられます）"}</small>}</span>
                </label>
              )}
              <p className="item-join-note">ドラッグで表示の並び順を変えます。</p>
              <SortableList items={units} label={unitLabel} onReorder={(keys) => reorderSection(section, keys)} />
            </div>
          )
        )}
      </li>
    );
  };

  const body = (
    <div className={`page-stack candidate-settings-page item-join-page${overlay ? " overlay" : ""}`}>
      <header className="page-header split">
        <div>
          <span className="eyebrow">{templateName}</span>
          <h1>項目のまとめ方・順番</h1>
          <p>見出しを選ぶと、中の欄の並び順を変えたり、1つの値にまとめたりできます。読み取りと修正は欄ごとのままです。</p>
        </div>
        <button type="button" className="ghost" disabled={saving} onClick={close}>{overlay ? "閉じる" : "← テンプレート一覧へ"}</button>
      </header>

      {feedback && <div className={feedback.kind === "error" ? "error-box" : "success-box"} role={feedback.kind === "error" ? "alert" : "status"}>{feedback.text}</div>}
      {feedback?.kind === "error" && !mappings.length && !loading && <button type="button" className="secondary compact candidate-retry" onClick={() => setAttempt((n) => n + 1)}>もう一度読み込む</button>}
      {loading ? <div className="empty-state">読み込んでいます…</div> : mappings.length > 0 && (
        <div className="candidate-settings-workspace">
          <section className={`candidate-preview-panel${picking ? " item-join-picking" : ""}`} aria-label="帳票">
            {current && <p className="item-join-pick-hint" role="status">帳票の欄を押すと「{current.name}」に加えたり外したりできます。</p>}
            {artifactUrl ? (
              <div className="candidate-preview-stage" style={{ aspectRatio: `${size.width} / ${size.height}` }}>
                <img src={artifactUrl} alt={`${templateName}の帳票`} />
                {mappings.map((mapping) => {
                  const badge = badgeOf(mapping.targetPath);
                  return (
                    <button
                      type="button"
                      key={mapping.mappingId}
                      className={`candidate-mapping-rectangle item-join-rectangle${joinOf.has(mapping.targetPath) ? " bound" : ""}${badge ? " active" : ""}`}
                      style={{
                        left: `${mapping.x / size.width * 100}%`,
                        top: `${mapping.y / size.height * 100}%`,
                        width: `${mapping.width / size.width * 100}%`,
                        height: `${mapping.height / size.height * 100}%`,
                      }}
                      aria-label={badge ? `${mapping.label}（${badge}番）` : mapping.label}
                      title={mapping.label}
                      onClick={() => pickCell(mapping.targetPath)}
                    >{badge && <span className="item-join-badge">{badge}</span>}</button>
                  );
                })}
              </div>
            ) : <div className="empty-state">帳票の画像がありません。</div>}
          </section>

          <aside className="candidate-control-panel">
            <section className="item-join-headings" aria-label="見出し">
              <div className="candidate-panel-heading compact">
                <div><h2>見出し</h2></div>
                <button type="button" className="ghost compact" disabled={asking || !shownVersionId} title="見出しと欄の名前だけを外部AIに送ります（1回）"
                  onClick={() => void askAi()}>{asking ? "AIが考えています…" : "AIにおすすめを聞く"}</button>
              </div>
              <ul>
                {sections.map(headingRow)}
                {looseJoins.map((draft) => (
                  <li key={draft.key} className={`item-join-heading${draft.key === selected ? " open" : ""}`}>
                    <button type="button" className="item-join-heading-toggle" aria-expanded={draft.key === selected} onClick={() => toggle(draft.key)}>
                      <span className="item-join-caret" aria-hidden="true">{draft.key === selected ? "▾" : "▸"}</span>
                      <strong>{draft.name || "まとめた項目"}</strong>
                      <span className="item-join-tag">まとめて表示</span>
                    </button>
                    {draft.key === selected && joinEditor(draft, null)}
                  </li>
                ))}
              </ul>
              <button type="button" className="ghost compact item-join-free" onClick={() => {
                const draft = toDraft({ name: "まとめた項目", kind: "join", members: [] });
                setDrafts((all) => [...all, draft]);
                setSelected(draft.key);
                setFeedback({ kind: "success", text: "まとめる欄を帳票で順に押してください。" });
              }}>＋ 見出しをまたいで欄をまとめる</button>
            </section>
          </aside>
        </div>
      )}

      {!loading && mappings.length > 0 && (
        <div className="decision-bar sticky candidate-save-bar">
          <div><strong>{dirty ? "保存していない変更があります" : "保存済みです"}</strong><p>確認画面・書類棚・一覧のXLSX・質問に使います。読み取り済みの書類にも反映されます。</p></div>
          <button type="button" className="primary" disabled={!dirty || saving} onClick={() => void save()}>{saving ? "保存中…" : "保存"}</button>
        </div>
      )}
    </div>
  );
  return overlay
    ? <div className="item-join-overlay" role="dialog" aria-modal="true" aria-label="項目のまとめ方・順番">{body}</div>
    : body;
}

/** A vertical list reordered by dragging (dnd-kit); the handle is the whole row. */
function SortableList({ items, label, onReorder, onRemove }: {
  items: string[];
  label: (key: string) => string;
  onReorder: (keys: string[]) => void;
  onRemove?: (key: string) => void;
}) {
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 4 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  );
  const end = (event: DragEndEvent) => {
    const { active, over } = event;
    if (!over || active.id === over.id) return;
    onReorder(arrayMove(items, items.indexOf(String(active.id)), items.indexOf(String(over.id))));
  };
  return (
    <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={end}>
      <SortableContext items={items} strategy={verticalListSortingStrategy}>
        <ol className="item-join-sortable">
          {items.map((key, index) => <SortableRow key={key} id={key} index={index} label={label(key)} onRemove={onRemove && (() => onRemove(key))} />)}
        </ol>
      </SortableContext>
    </DndContext>
  );
}

function SortableRow({ id, index, label, onRemove }: { id: string; index: number; label: string; onRemove?: () => void }) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({ id });
  return (
    <li ref={setNodeRef} className={`item-join-sortable-row${isDragging ? " dragging" : ""}`}
      style={{ transform: CSS.Transform.toString(transform), transition }}>
      <span className="item-join-grip" {...attributes} {...listeners} aria-label={`${label}を並べ替える`}>
        <span aria-hidden="true">⠿</span>
        <b>{index + 1}</b>
        <span className="item-join-sortable-label">{label}</span>
      </span>
      {onRemove && <button type="button" className="ghost compact" aria-label={`${label}を外す`} onClick={onRemove}>×</button>}
    </li>
  );
}

function unpaired(name: string): string {
  let text = name.trim();
  for (const [open, close] of [["(", ")"], ["（", "）"], ["「", "」"]]) {
    if (text.endsWith(close) && !text.includes(open)) text = text.slice(0, -1).trim();
    if (text.startsWith(open) && !text.includes(close)) text = text.slice(1).trim();
  }
  return text;
}

let draftCounter = 0;
function toDraft(join: ItemJoin): Draft {
  draftCounter += 1;
  return {
    // Unique even when the module is reloaded while drafts are on screen.
    key: `join-${Date.now().toString(36)}-${draftCounter}-${Math.random().toString(36).slice(2, 7)}`,
    name: join.name,
    kind: isJoin(join) ? "join" : "order",
    separator: join.separator ?? "",
    members: join.members.map(({ targetPath, before, after }) => ({ targetPath, before: before ?? "", after: after ?? "" })),
  };
}

function signature(drafts: Draft[]) {
  return JSON.stringify(drafts.map(({ name, kind, separator, members }) => [name.trim(), kind, separator, members.map(({ targetPath, before, after }) => [targetPath, before, after])]));
}

function errorText(error: unknown, fallback: string) {
  return error instanceof ApiError || error instanceof Error ? error.message : fallback;
}

const OPEN_EVENT = "ledger-open-item-joins";
const SAVED_EVENT = "ledger-item-joins-saved";
type OpenRequest = { templateId: string; versionId?: string | null };

/** Opens 「項目のまとめ方・順番」 over the current screen (from a toast or the result screen). */
export function openItemJoinEditor(templateId: string, versionId?: string | null) {
  window.dispatchEvent(new CustomEvent<OpenRequest>(OPEN_EVENT, { detail: { templateId, versionId } }));
}

/** Calls `listener` with the saved joins of a template, wherever they were saved from. */
export function onItemJoinsSaved(listener: (templateId: string, items: ItemJoin[]) => void) {
  const receive = (event: Event) => {
    const detail = (event as CustomEvent<{ templateId: string; items: ItemJoin[] }>).detail;
    listener(detail.templateId, detail.items);
  };
  window.addEventListener(SAVED_EVENT, receive);
  return () => window.removeEventListener(SAVED_EVENT, receive);
}

export function ItemJoinEditorHost() {
  const [request, setRequest] = useState<OpenRequest | null>(null);
  useEffect(() => {
    const receive = (event: Event) => setRequest((event as CustomEvent<OpenRequest>).detail);
    window.addEventListener(OPEN_EVENT, receive);
    return () => window.removeEventListener(OPEN_EVENT, receive);
  }, []);
  if (!request) return null;
  return <TemplateItemJoinEditor
    key={`${request.templateId}:${request.versionId ?? ""}`}
    templateId={request.templateId}
    versionId={request.versionId}
    overlay
    onClose={() => setRequest(null)}
    onSaved={(items) => window.dispatchEvent(new CustomEvent(SAVED_EVENT, { detail: { templateId: request.templateId, items } }))}
  />;
}
