import { normalizeJapaneseDateDisplay } from "../japaneseDates";
import type { FieldReading } from "../types";

type SaleCalf = Record<string, FieldReading>;

type SalePricePoint = {
  date: Date;
  dateLabel: string;
  price: number;
  parity: string;
  sireName: string;
};

const WIDTH = 820;
const HEIGHT = 360;
const PLOT = { left: 78, right: 28, top: 48, bottom: 78 };

export function SalePriceChart({ saleCalves }: { saleCalves: SaleCalf[] }) {
  const points = saleCalves
    .map(toPoint)
    .filter((point): point is SalePricePoint => point !== null)
    .sort((left, right) => left.date.getTime() - right.date.getTime());
  const excludedCount = saleCalves.length - points.length;

  if (points.length === 0) {
    return (
      <div className="sale-price-chart-empty" role="status">
        <strong>グラフに表示できる記録がありません。</strong>
        <span>売却日と価格の両方がある記録を表示します。</span>
        {excludedCount > 0 && <small>{excludedCount}件は日付または価格が不足しています。</small>}
      </div>
    );
  }

  const plotWidth = WIDTH - PLOT.left - PLOT.right;
  const plotHeight = HEIGHT - PLOT.top - PLOT.bottom;
  const firstTime = points[0].date.getTime();
  const lastTime = points.at(-1)?.date.getTime() ?? firstTime;
  const timeSpan = lastTime - firstTime;
  const maximumPrice = Math.max(...points.map((point) => point.price));
  const yMaximum = Math.max(100_000, Math.ceil(maximumPrice / 100_000) * 100_000);
  const positioned = points.map((point) => ({
    ...point,
    x: timeSpan === 0
      ? PLOT.left + plotWidth / 2
      : PLOT.left + ((point.date.getTime() - firstTime) / timeSpan) * plotWidth,
    y: PLOT.top + plotHeight - (point.price / yMaximum) * plotHeight,
  }));
  const yTicks = Array.from({ length: 5 }, (_, index) => (yMaximum / 4) * index);
  const linePoints = positioned.map((point) => `${point.x},${point.y}`).join(" ");

  return (
    <figure className="sale-price-chart" aria-label="売却価格の推移">
      <figcaption>
        <strong>売却価格の推移</strong>
        <span>点は売却子牛1頭、線は価格の推移を示します。</span>
      </figcaption>
      <div className="sale-price-chart-scroll">
        <svg
          viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
          role="img"
          aria-label={`売却日と売却価格のグラフ。${points.length}件を表示。`}
        >
          {yTicks.map((tick) => {
            const y = PLOT.top + plotHeight - (tick / yMaximum) * plotHeight;
            return (
              <g className="sale-chart-grid" key={tick}>
                <line x1={PLOT.left} x2={WIDTH - PLOT.right} y1={y} y2={y} />
                <text x={PLOT.left - 12} y={y + 4} textAnchor="end">
                  {formatAxisPrice(tick)}
                </text>
              </g>
            );
          })}
          <text
            className="sale-chart-axis-title"
            x={18}
            y={PLOT.top + plotHeight / 2}
            textAnchor="middle"
            transform={`rotate(-90 18 ${PLOT.top + plotHeight / 2})`}
          >
            売却価格
          </text>
          {positioned.length > 1 && <polyline className="sale-chart-line" points={linePoints} />}
          {positioned.map((point, index) => {
            const accessibleLabel = [
              point.dateLabel,
              formatPrice(point.price),
              point.parity ? `産次${point.parity}` : "",
              point.sireName ? `種雄牛名 ${point.sireName}` : "",
            ].filter(Boolean).join("、");
            return (
              <g className="sale-chart-point" key={`${point.dateLabel}-${point.price}-${index}`}>
                <line
                  className="sale-chart-date-guide"
                  x1={point.x}
                  x2={point.x}
                  y1={point.y}
                  y2={PLOT.top + plotHeight}
                />
                <circle cx={point.x} cy={point.y} r={6} tabIndex={0} aria-label={accessibleLabel}>
                  <title>{accessibleLabel}</title>
                </circle>
                <text className="sale-chart-price-label" x={point.x} y={point.y - 14} textAnchor="middle">
                  {formatPrice(point.price)}
                </text>
                <text
                  className="sale-chart-date-label"
                  x={point.x}
                  y={PLOT.top + plotHeight + 24}
                  textAnchor="end"
                  transform={`rotate(-35 ${point.x} ${PLOT.top + plotHeight + 24})`}
                >
                  {formatShortDate(point.date)}
                </text>
              </g>
            );
          })}
        </svg>
      </div>
      <div className="sale-price-chart-note">
        <span>横軸：売却日</span>
        <span>縦軸：売却価格</span>
        {excludedCount > 0 && (
          <strong>{excludedCount}件は日付または価格が不足しているため非表示です。</strong>
        )}
      </div>
    </figure>
  );
}

function toPoint(calf: SaleCalf): SalePricePoint | null {
  const displayedDate = normalizeJapaneseDateDisplay(calf.saleDate?.raw, calf.saleDate?.normalized)
    || readingValue(calf.saleDate);
  const dateMatch = displayedDate.match(/^(\d{4})年(\d{1,2})月(\d{1,2})日$/);
  const price = parsePrice(readingValue(calf.priceYen));
  if (!dateMatch || price === null) return null;
  const [, year, month, day] = dateMatch;
  const date = new Date(Date.UTC(Number(year), Number(month) - 1, Number(day)));
  if (
    date.getUTCFullYear() !== Number(year)
    || date.getUTCMonth() !== Number(month) - 1
    || date.getUTCDate() !== Number(day)
  ) return null;
  return {
    date,
    dateLabel: `${year}年${Number(month)}月${Number(day)}日`,
    price,
    parity: readingValue(calf.parity),
    sireName: readingValue(calf.sireName),
  };
}

function readingValue(reading?: FieldReading) {
  return reading?.normalized ?? reading?.raw ?? "";
}

function parsePrice(value: string) {
  if (!value) return null;
  const compact = value.normalize("NFKC").replace(/\s+/g, "").replace(/円$/, "");
  const grouped = /^\d{1,3}(?:,\d{3})+$/.test(compact)
    || /^\d{1,3}(?:\.\d{3})+$/.test(compact);
  const canonical = grouped ? compact.replace(/[,.]/g, "") : compact.replace(/,/g, "");
  const numeric = Number(canonical);
  return Number.isFinite(numeric) && numeric >= 0 ? numeric : null;
}

function formatPrice(value: number) {
  return `${value.toLocaleString("ja-JP")}円`;
}

function formatAxisPrice(value: number) {
  return `${Math.round(value / 10_000)}万円`;
}

function formatShortDate(date: Date) {
  return `${date.getUTCFullYear()}/${date.getUTCMonth() + 1}/${date.getUTCDate()}`;
}
