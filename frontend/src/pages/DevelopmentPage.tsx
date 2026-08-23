import { useEffect, useState } from "react";
import { ApiError, api } from "../api";
import { showToast } from "../components/Toast";
import type { OpenAIUsageReport } from "../types";

export function DevelopmentPage() {
  const [report, setReport] = useState<OpenAIUsageReport | null>(null);
  const [loading, setLoading] = useState(true);

  const load = async () => {
    setLoading(true);
    try {
      setReport(await api.openaiUsage());
    } catch (reason) {
      showToast({
        tone: "error",
        title: "利用履歴を取得できませんでした",
        message: reason instanceof ApiError ? reason.message : "バックエンドとの通信を確認してください。",
      });
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { void load(); }, []);

  return (
    <div className="page-stack development-page">
      <header className="page-header development-header">
        <div>
          <span className="eyebrow">DEVELOPMENT / OPENAI API</span>
          <h1>OpenAI API 使用量</h1>
          <p>全アカウント共通の開発情報です。APIキーや入力画像、OCR結果本文は表示しません。</p>
        </div>
        <button type="button" className="secondary" disabled={loading} onClick={() => void load()}>
          {loading ? "更新中…" : "最新情報に更新"}
        </button>
      </header>

      <section className="usage-summary" aria-label="OpenAI API 使用量集計">
        <UsageMetric label="対象ジョブ" value={formatNumber(report?.summary.jobCount)} suffix="件" />
        <UsageMetric label="API呼び出し" value={formatNumber(report?.summary.apiCallCount)} suffix="回" />
        <UsageMetric label="入力トークン" value={formatNumber(report?.summary.inputTokens)} />
        <UsageMetric label="出力トークン" value={formatNumber(report?.summary.outputTokens)} />
        <UsageMetric label="合計トークン" value={formatNumber(report?.summary.totalTokens)} accent />
        <UsageMetric
          label="推定コスト"
          value={report?.summary.estimatedCost ? `$${report.summary.estimatedCost}` : "算出対象外"}
          muted={!report?.summary.estimatedCost}
        />
      </section>

      <section className="usage-history-panel">
        <header>
          <div>
            <h2>ジョブ別利用履歴</h2>
            <p>今後の実行は監査イベント、既存データは保存済みOCR結果から表示します。</p>
          </div>
          <span>{report ? `${report.summary.recordCount} レコード` : "読み込み中"}</span>
        </header>
        {!loading && report?.records.length === 0 ? (
          <div className="empty-state large">
            <span>API</span>
            <h2>OpenAI APIの利用履歴はありません</h2>
            <p>OpenAI OCRを実行すると、ここにジョブ単位の使用量が記録されます。</p>
          </div>
        ) : (
          <div className="usage-table-wrap">
            <table className="usage-table">
              <thead>
                <tr>
                  <th>日時</th><th>ジョブ</th><th>モデル</th><th>呼び出し</th>
                  <th>入力</th><th>出力</th><th>合計</th><th>推定コスト</th><th>記録元</th>
                </tr>
              </thead>
              <tbody>
                {report?.records.map((record) => (
                  <tr key={`${record.source}-${record.jobId}-${record.attemptNumber}-${record.recordedAt}`}>
                    <td>{formatDate(record.recordedAt)}</td>
                    <td><strong>{record.jobId.slice(0, 8)}</strong><small>試行 {record.attemptNumber} · {record.jobStatus}</small></td>
                    <td>{record.modelId ?? "不明"}</td>
                    <td>{formatNumber(record.apiCallCount)}</td>
                    <td>{formatNumber(record.inputTokens)}</td>
                    <td>{formatNumber(record.outputTokens)}</td>
                    <td><strong>{formatNumber(record.totalTokens)}</strong></td>
                    <td>{record.estimatedCost ? `$${record.estimatedCost}` : "-"}</td>
                    <td><span className={`usage-source ${record.source}`}>{record.source === "audit" ? "監査履歴" : "既存結果"}</span></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}

function UsageMetric({ label, value, suffix, accent = false, muted = false }: {
  label: string;
  value: string;
  suffix?: string;
  accent?: boolean;
  muted?: boolean;
}) {
  return <div className={`${accent ? "accent" : ""} ${muted ? "muted" : ""}`}><span>{label}</span><strong>{value}{suffix && <small>{suffix}</small>}</strong></div>;
}

function formatNumber(value: number | null | undefined): string {
  return value === null || value === undefined ? "-" : value.toLocaleString("ja-JP");
}

function formatDate(value: string): string {
  return new Intl.DateTimeFormat("ja-JP", {
    year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit",
  }).format(new Date(value));
}
