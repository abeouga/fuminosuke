import { type FormEvent, useEffect, useState } from "react";
import { ApiError, api } from "../api";
import type { SireName, SireNamePool } from "../types";

export function SireNamesPage() {
  const [pool, setPool] = useState<SireNamePool | null>(null);
  const [name, setName] = useState("");
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  const load = async () => {
    try {
      setPool(await api.sireNames());
      setMessage(null);
    } catch (error) {
      setMessage(
        error instanceof ApiError
          ? error.message
          : "種雄牛名の一覧を読み込めませんでした。",
      );
    }
  };

  useEffect(() => {
    void load();
  }, []);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!name.trim()) return;
    setSaving(true);
    setMessage(null);
    try {
      await api.createSireName(name);
      setName("");
      await load();
    } catch (error) {
      setMessage(
        error instanceof ApiError
          ? error.message
          : "種雄牛名を追加できませんでした。",
      );
    } finally {
      setSaving(false);
    }
  };

  const remove = async (item: SireName) => {
    if (!window.confirm(`「${item.name}」を候補プールから削除しますか？`)) return;
    setMessage(null);
    try {
      await api.deleteSireName(item.id);
      await load();
    } catch (error) {
      setMessage(
        error instanceof ApiError
          ? error.message
          : "種雄牛名を削除できませんでした。",
      );
    }
  };

  return (
    <div className="page-stack narrow">
      <header className="page-header">
        <span className="eyebrow">SIRE NAME POOL</span>
        <h1>種雄牛名を追加</h1>
        <p>登録した名称を、種雄牛名の手書きセルを読み取るときの候補として使用します。</p>
      </header>

      <section className="form-card sire-name-form">
        <div className="section-title">
          <div>
            <h2>候補を登録</h2>
            <p>全角・半角と空白を整えて重複を防ぎます。</p>
          </div>
        </div>
        <form onSubmit={submit}>
          <label className="field-label">
            種雄牛名
            <input
              value={name}
              autoComplete="off"
              maxLength={100}
              placeholder="例: 勝忠平"
              onChange={(event) => {
                setName(event.target.value);
                setMessage(null);
              }}
            />
          </label>
          <button className="primary" disabled={saving || !name.trim()}>
            {saving ? "追加中…" : "候補に追加"}
          </button>
        </form>
        <aside className="notice compact">
          <strong>ET付きの表記にも対応します</strong>
          <span>
            基本名だけを登録してください。「勝忠平」を登録すると、手書きの「勝忠平ET」も候補として判定します。
          </span>
        </aside>
        {message && <p className="error-box">{message}</p>}
      </section>

      <section className="form-card sire-name-pool">
        <div className="sire-name-pool-head">
          <div>
            <span className="eyebrow">REGISTERED</span>
            <h2>登録済みの候補</h2>
          </div>
          <strong>{pool?.count ?? "…"}件</strong>
        </div>
        {pool === null && !message ? (
          <p className="empty-state compact">候補を読み込んでいます…</p>
        ) : pool?.items.length === 0 ? (
          <p className="empty-state compact">登録済みの種雄牛名はありません。</p>
        ) : (
          <ul className="sire-name-list">
            {pool?.items.map((item) => (
              <li key={item.id}>
                <div>
                  <strong>{item.name}</strong>
                  <small>
                    読み取り候補: {item.name}
                    {pool.allowedSuffixes.map((suffix) => ` / ${item.name}${suffix}`)}
                  </small>
                </div>
                <button
                  type="button"
                  className="danger-button compact"
                  aria-label={`${item.name}を削除`}
                  onClick={() => void remove(item)}
                >
                  削除
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
