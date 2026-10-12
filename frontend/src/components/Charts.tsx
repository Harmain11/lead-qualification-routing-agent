import { useId } from "react";
import type { CSSProperties } from "react";

export function Bars({
  rows,
  formatter = (value) => String(value),
  scaleMaximum,
}: {
  rows: Array<{ label: string; value: number; note?: string }>;
  formatter?: (value: number) => string;
  scaleMaximum?: number;
}) {
  const maximum = scaleMaximum && Number.isFinite(scaleMaximum) && scaleMaximum > 0
    ? scaleMaximum : Math.max(...rows.map((row) => row.value), 1);
  if (rows.length === 0) return <EmptyChart />;
  return <div className="bar-chart">{rows.map((row, index) => (
    <div className="bar-row" key={row.label} title={`${row.label}: ${formatter(row.value)}`} style={{ "--bar-order": index } as CSSProperties}>
      <div><span>{row.label}</span><b>{formatter(row.value)}</b></div>
      <i><span style={{ "--bar-scale": Math.max(0, Math.min(1, row.value / maximum)) } as CSSProperties} /></i>
      {row.note && <small>{row.note}</small>}
    </div>
  ))}</div>;
}

export function TrendChart({ values, labels }: { values: number[]; labels: string[] }) {
  const gradientId = useId().replaceAll(":", "");
  if (values.length === 0) return <EmptyChart />;
  const width = 720;
  const height = 220;
  const maximum = Math.max(...values, 1);
  const minimum = Math.min(...values, 0);
  const range = Math.max(maximum - minimum, 1);
  const points = values.map((value, index) => {
    const x = values.length === 1 ? width / 2 : (index / (values.length - 1)) * width;
    const y = height - 24 - ((value - minimum) / range) * (height - 48);
    return { x, y, value, label: labels[index] };
  });
  const path = points.map((point, index) => `${index ? "L" : "M"}${point.x},${point.y}`).join(" ");
  const area = `${path} L${points.at(-1)?.x ?? width},${height} L${points[0]?.x ?? 0},${height} Z`;
  const animationKey = values.join("-");
  return <div className="trend-chart">
    <svg key={animationKey} viewBox={`0 0 ${width} ${height}`} role="img" aria-label="Time-series trend">
      <defs><linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1"><stop offset="0" stopColor="var(--orange)" stopOpacity=".34" /><stop offset="1" stopColor="var(--orange)" stopOpacity="0" /></linearGradient></defs>
      <path className="trend-area" d={area} style={{ fill: `url(#${gradientId})` }} />
      <path className="trend-line" d={path} pathLength="1" />
      {points.map((point, index) => <g key={`${point.label}-${point.x}`}><circle cx={point.x} cy={point.y} r="5" style={{ "--point-order": index } as CSSProperties}><title>{point.label}: {point.value}</title></circle></g>)}
    </svg>
    <div className="trend-labels"><span>{labels[0]}</span><span>{labels.at(-1)}</span></div>
  </div>;
}

export type TimedTrendPoint = { timestamp: string; value: number | null };

export function TimedAreaChart({
  points,
  formatValue,
  ariaLabel,
}: {
  points: TimedTrendPoint[];
  formatValue: (value: number) => string;
  ariaLabel: string;
}) {
  const gradientId = useId().replaceAll(":", "");
  const valid = points.flatMap((point, index) => {
    const timestamp = Date.parse(point.timestamp);
    return Number.isFinite(timestamp) && point.value !== null && Number.isFinite(point.value)
      ? [{ timestamp, value: point.value, source: point, index }]
      : [];
  });
  if (!valid.length) return <EmptyChart />;
  const width = 760;
  const height = 252;
  const inset = { left: 14, right: 14, top: 18, bottom: 30 };
  const minTime = Math.min(...valid.map((point) => point.timestamp));
  const maxTime = Math.max(...valid.map((point) => point.timestamp));
  const minValue = Math.min(0, ...valid.map((point) => point.value));
  const maxValue = Math.max(0, ...valid.map((point) => point.value));
  const range = maxValue - minValue || 1;
  const timeRange = maxTime - minTime;
  const baseline = height - inset.bottom - ((0 - minValue) / range) * (height - inset.top - inset.bottom);
  const plotted = valid.map((point) => ({
    ...point,
    x: timeRange === 0 ? width / 2 : inset.left + ((point.timestamp - minTime) / timeRange) * (width - inset.left - inset.right),
    y: height - inset.bottom - ((point.value - minValue) / range) * (height - inset.top - inset.bottom),
  }));
  const segments: typeof plotted[] = [];
  let segment: typeof plotted = [];
  plotted.forEach((point, index) => {
    const previous = plotted[index - 1];
    const skippedSourcePoints = previous ? point.index - previous.index > 1 : false;
    if (skippedSourcePoints && segment.length) {
      segments.push(segment);
      segment = [];
    }
    segment.push(point);
  });
  if (segment.length) segments.push(segment);
  const makePath = (items: typeof plotted) => items.map((point, index) => `${index ? "L" : "M"}${point.x},${point.y}`).join(" ");
  const dateLabel = (timestamp: number) => new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric", year: "numeric" }).format(new Date(timestamp));
  const first = plotted[0];
  const last = plotted.at(-1)!;
  return <div className="trend-chart timed-area-chart">
    <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={ariaLabel}>
      <defs><linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1"><stop offset="0" stopColor="var(--orange)" stopOpacity=".3" /><stop offset="1" stopColor="var(--orange)" stopOpacity=".025" /></linearGradient></defs>
      <line className="timed-area-baseline" x1={inset.left} x2={width - inset.right} y1={baseline} y2={baseline} />
      {segments.map((items, segmentIndex) => {
        const line = makePath(items);
        const area = `${line} L${items.at(-1)!.x},${baseline} L${items[0].x},${baseline} Z`;
        return <g key={`segment-${segmentIndex}`}><path className="trend-area" d={area} style={{ fill: `url(#${gradientId})` }} /><path className="trend-line" d={line} /></g>;
      })}
      {plotted.map((point) => <circle key={`${point.source.timestamp}-${point.index}`} cx={point.x} cy={point.y} r="4.5"><title>{`${point.source.timestamp}: ${formatValue(point.value)}`}</title></circle>)}
    </svg>
    <div className="timed-area-labels"><span>{dateLabel(first.timestamp)}</span><span>{dateLabel(last.timestamp)}</span></div>
    <ol className="chart-data-list" aria-label="Revenue values by period">{plotted.map((point) => <li key={`${point.source.timestamp}-${point.index}`}><span>{point.source.timestamp}</span><b>{formatValue(point.value)}</b></li>)}</ol>
  </div>;
}

export function Donut({ rows }: { rows: Array<{ label: string; value: number }> }) {
  const total = rows.reduce((sum, row) => sum + row.value, 0);
  let cursor = 0;
  const colors = ["#1e6b52", "#d79728", "#457a9e", "#8a98a3", "#b8524b"];
  const stops = rows.map((row, index) => {
    const start = total ? (cursor / total) * 100 : 0;
    cursor += row.value;
    const end = total ? (cursor / total) * 100 : 0;
    return `${colors[index % colors.length]} ${start}% ${end}%`;
  });
  return <div className="donut-wrap">
    <div className="donut" key={stops.join("-")} style={{ background: total ? `conic-gradient(${stops.join(",")})` : "var(--surface-high)" }}><div><strong>{total}</strong><span>leads</span></div></div>
    <div className="donut-legend">{rows.map((row, index) => <span key={row.label}><i style={{ background: colors[index % colors.length] }} />{row.label}<b>{row.value}</b></span>)}</div>
  </div>;
}

function EmptyChart() {
  return <div className="chart-empty"><span />No data in this period</div>;
}
