import { useEffect, useState } from "react";
import { FiRefreshCw } from "react-icons/fi";
import { authenticatedFetch, isSignInMode } from "../auth/transport";
import { InfoTip } from "../components/InfoTip";
import { Spinner } from "../components/Spinner";

type Counts = { documents: number; read: number; corrected: number; formatOnly: number; missed: number };
type UserRates = {
  user: string;
  total: Counts;
  byTemplate: (Counts & { templateId: string; templateName: string })[];
  byMonth: (Counts & { month: string })[];
};

const percent = (part: number, whole: number) => whole > 0 ? `${(part / whole * 100).toFixed(1)}%` : "—";

/**
 * The correction rate (admin): of the text fields OCR read, how many people corrected before
 * confirming, so a change to the reading can be measured before and after. With sign-in it
 * shows every user (the server reads each user's documents for the admin only).
 */
export function CorrectionRatePage() {
  const [users, setUsers] = useState<UserRates[] | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const load = async () => {
    setLoading(true);
    setError("");
    try {
      const response = await authenticatedFetch(isSignInMode() ? "/api/admin/correction-rates" : "/api/v1/correction-rates", { cache: "no-store" });
      if (!response.ok) throw new Error(response.status === 403 ? "修正率は管理者だけが見られます。" : "修正率を読み込めませんでした。");
      setUsers(((await response.json()) as { users: UserRates[] }).users);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "修正率を読み込めませんでした。");
    } finally {
      setLoading(false);
    }
  };
  useEffect(() => { void load(); }, []);
  return (
    <div className="page correction-rate-page">
      <header className="page-header split title-end">
        <button type="button" className="secondary compact correction-rate-reload" disabled={loading} onClick={() => void load()} aria-label="更新" title="更新">
          {loading ? <Spinner /> : <FiRefreshCw aria-hidden="true" />}
        </button>
        <div className="correction-rate-title">
          <h1>修正率</h1>
          <InfoTip align="end" text="OCRが読んだ欄のうち、人が直した割合です。" />
        </div>
      </header>
      {error && <p className="error-box" role="alert">{error}</p>}
      {!users && !error && <p className="correction-rate-empty"><Spinner />読み込んでいます…</p>}
      {users?.length === 0 && <p className="correction-rate-empty">確定した書類はまだありません。</p>}
      {users?.map((user) => <UserCard key={user.user} rates={user} showName={users.length > 1 || isSignInMode()} />)}
    </div>
  );
}

function UserCard({ rates, showName }: { rates: UserRates; showName: boolean }) {
  const { total } = rates;
  return (
    <section className="correction-rate-card">
      {showName && <h2 className="correction-rate-user" title={rates.user}>{rates.user}</h2>}
      <div className="correction-rate-tiles">
        <Tile label="修正率" value={percent(total.corrected, total.read)} detail={`${total.corrected} / ${total.read} 欄`} strong />
        <Tile label="書き方だけの修正" value={percent(total.formatOnly, total.read)} detail={`${total.formatOnly} 欄`} />
        <Tile label="読み落とし" value={`${total.missed} 欄`} detail="空欄と読んで書き足した" />
        <Tile label="確定した書類" value={`${total.documents} 件`} />
      </div>
      {total.documents > 0 && <>
        <RateTable title="テンプレート別" rows={rates.byTemplate.map((row) => ({ key: row.templateId, name: row.templateName, counts: row }))} />
        <RateTable title="月別（確定した月）" rows={rates.byMonth.map((row) => ({ key: row.month, name: row.month.replace("-", "年") + "月", counts: row }))} />
      </>}
    </section>
  );
}

function Tile({ label, value, detail, strong }: { label: string; value: string; detail?: string; strong?: boolean }) {
  return <div className={`correction-rate-tile${strong ? " is-main" : ""}`}><span>{label}</span><strong>{value}</strong>{detail && <small>{detail}</small>}</div>;
}

function RateTable({ title, rows }: { title: string; rows: { key: string; name: string; counts: Counts }[] }) {
  return (
    <div className="correction-rate-table-wrap">
      <h3>{title}</h3>
      <table className="correction-rate-table">
        <thead><tr><th scope="col"></th><th scope="col">修正率</th><th scope="col">修正</th><th scope="col">読んだ欄</th><th scope="col">書き方だけ</th><th scope="col">読み落とし</th><th scope="col">書類</th></tr></thead>
        <tbody>
          {rows.map(({ key, name, counts }) => (
            <tr key={key}>
              <th scope="row">{name}</th>
              <td className="is-rate">{percent(counts.corrected, counts.read)}</td>
              <td>{counts.corrected}</td><td>{counts.read}</td><td>{counts.formatOnly}</td><td>{counts.missed}</td><td>{counts.documents}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
