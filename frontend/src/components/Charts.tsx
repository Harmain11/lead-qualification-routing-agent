import { useId, useState } from "react";
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
  const [interval, setInterval] = useState<ChartInterval>("all");
  const [hoveredPoint, setHoveredPoint] = useState<number | null>(null);
  const gradientId = useId().replaceAll(":", "");
  if (values.length === 0) return <EmptyChart />;
  const visible = aggregateLabeledPoints(values.map((value, index) => ({ value, label: labels[index] ?? "" })), interval);
  const width = 720;
  const height = 220;
  const maximum = Math.max(...visible.map((point) => point.value), 1);
  const minimum = Math.min(...visible.map((point) => point.value), 0);
  const range = Math.max(maximum - minimum, 1);
  const points = visible.map((point, index) => {
    const x = visible.length === 1 ? width / 2 : (index / (visible.length - 1)) * width;
    const y = height - 24 - ((point.value - minimum) / range) * (height - 48);
    return { x, y, value: point.value, label: point.label };
  });
  const path = points.map((point, index) => `${index ? "L" : "M"}${point.x},${point.y}`).join(" ");
  const area = `${path} L${points.at(-1)?.x ?? width},${height} L${points[0]?.x ?? 0},${height} Z`;
  const animationKey = visible.map((point) => point.value).join("-");
  const activePoint = hoveredPoint === null ? null : points[hoveredPoint] ?? null;
  return <div className="trend-chart">
    <ChartToolbar interval={interval} onIntervalChange={setInterval} />
    <svg key={animationKey} viewBox={`0 0 ${width} ${height}`} role="img" aria-label="Time-series trend">
      <defs><linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1"><stop offset="0" stopColor="var(--orange)" stopOpacity=".34" /><stop offset="1" stopColor="var(--orange)" stopOpacity="0" /></linearGradient></defs>
      <path className="trend-area" d={area} style={{ fill: `url(#${gradientId})` }} />
      <path className="trend-line" d={path} pathLength="1" />
      {points.map((point, index) => <g key={`${point.label}-${point.x}`}><circle tabIndex={0} cx={point.x} cy={point.y} r="5" style={{ "--point-order": index } as CSSProperties} onMouseEnter={() => setHoveredPoint(index)} onMouseLeave={() => setHoveredPoint(null)} onFocus={() => setHoveredPoint(index)} onBlur={() => setHoveredPoint(null)}><title>{point.label}: {point.value}</title></circle></g>)}
    </svg>
    {activePoint && <div className="chart-tooltip" style={tooltipPosition(activePoint, width, height)} role="status"><b>{activePoint.label}</b><span>{activePoint.value}</span></div>}
    <div className="trend-labels"><span>{visible[0]?.label}</span><span>{visible.at(-1)?.label}</span></div>
  </div>;
}

type ChartInterval = "all" | "daily" | "weekly" | "monthly" | "annual";

function intervalStart(timestamp: number, interval: ChartInterval) {
  const date = new Date(timestamp);
  date.setHours(0, 0, 0, 0);
  if (interval === "weekly") date.setDate(date.getDate() - date.getDay());
  if (interval === "monthly") date.setDate(1);
  if (interval === "annual") date.setMonth(0, 1);
  return date.getTime();
}

function intervalLabel(timestamp: number, interval: ChartInterval) {
  const options = interval === "annual"
    ? { year: "numeric" } as const
    : interval === "monthly"
      ? { month: "short", year: "numeric" } as const
      : { month: "short", day: "numeric", year: "numeric" } as const;
  return new Intl.DateTimeFormat(undefined, options).format(new Date(timestamp));
}

function aggregateLabeledPoints(items: Array<{ value: number; label: string }>, interval: ChartInterval) {
  if (interval === "all") return items;
  const dated = items.flatMap((item) => {
    const timestamp = Date.parse(item.label);
    return Number.isFinite(timestamp) ? [{ ...item, timestamp }] : [];
  });
  if (dated.length !== items.length) return items;
  const buckets = new Map<number, { value: number; label: string }>();
  dated.forEach((item) => {
    const bucket = intervalStart(item.timestamp, interval);
    const current = buckets.get(bucket);
    buckets.set(bucket, { value: (current?.value ?? 0) + item.value, label: intervalLabel(bucket, interval) });
  });
  return [...buckets.values()];
}

function ChartToolbar({ interval, onIntervalChange }: { interval: ChartInterval; onIntervalChange: (value: ChartInterval) => void }) {
  return <div className="chart-toolbar">
    <label><span>Date interval</span><select value={interval} onChange={(event) => onIntervalChange(event.target.value as ChartInterval)} aria-label="Chart date interval"><option value="all">All dates</option><option value="daily">Daily</option><option value="weekly">Weekly</option><option value="monthly">Monthly</option><option value="annual">Annual</option></select></label>
  </div>;
}

function tooltipPosition(point: { x: number; y: number }, width: number, height: number): CSSProperties {
  const transform = point.x < width * 0.15
    ? "translate(0, calc(-100% - 10px))"
    : point.x > width * 0.85
      ? "translate(-100%, calc(-100% - 10px))"
      : "translate(-50%, calc(-100% - 10px))";
  return { left: `${(point.x / width) * 100}%`, top: `${(point.y / height) * 100}%`, transform };
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
  const [interval, setInterval] = useState<ChartInterval>("all");
  const [hoveredPoint, setHoveredPoint] = useState<number | null>(null);
  const gradientId = useId().replaceAll(":", "");
  const valid = points.flatMap((point, index) => {
    const timestamp = Date.parse(point.timestamp);
    return Number.isFinite(timestamp) && point.value !== null && Number.isFinite(point.value)
      ? [{ timestamp, value: point.value, source: point, index }]
      : [];
  });
  if (!valid.length) return <EmptyChart />;
  const visible = interval === "all"
    ? valid
    : aggregateLabeledPoints(valid.map((point) => ({ value: point.value, label: point.source.timestamp })), interval).map((point, index) => ({
      timestamp: Date.parse(point.label), value: point.value, source: { timestamp: point.label, value: point.value }, index,
    }));
  const width = 760;
  const height = 252;
  const inset = { left: 14, right: 14, top: 18, bottom: 30 };
  const minTime = Math.min(...visible.map((point) => point.timestamp));
  const maxTime = Math.max(...visible.map((point) => point.timestamp));
  const minValue = Math.min(0, ...visible.map((point) => point.value));
  const maxValue = Math.max(0, ...visible.map((point) => point.value));
  const range = maxValue - minValue || 1;
  const timeRange = maxTime - minTime;
  const baseline = height - inset.bottom - ((0 - minValue) / range) * (height - inset.top - inset.bottom);
  const plotted = visible.map((point) => ({
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
  const activePoint = hoveredPoint === null ? null : plotted[hoveredPoint] ?? null;
  return <div className="trend-chart timed-area-chart">
    <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={ariaLabel}>
      <defs><linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1"><stop offset="0" stopColor="var(--orange)" stopOpacity=".3" /><stop offset="1" stopColor="var(--orange)" stopOpacity=".025" /></linearGradient></defs>
      <line className="timed-area-baseline" x1={inset.left} x2={width - inset.right} y1={baseline} y2={baseline} />
      {segments.map((items, segmentIndex) => {
        const line = makePath(items);
        const area = `${line} L${items.at(-1)!.x},${baseline} L${items[0].x},${baseline} Z`;
        return <g key={`segment-${segmentIndex}`}><path className="trend-area" d={area} style={{ fill: `url(#${gradientId})` }} /><path className="trend-line" d={line} /></g>;
      })}
      {plotted.map((point, index) => <circle tabIndex={0} key={`${point.source.timestamp}-${point.index}`} cx={point.x} cy={point.y} r="4.5" onMouseEnter={() => setHoveredPoint(index)} onMouseLeave={() => setHoveredPoint(null)} onFocus={() => setHoveredPoint(index)} onBlur={() => setHoveredPoint(null)}><title>{`${point.source.timestamp}: ${formatValue(point.value)}`}</title></circle>)}
    </svg>
    {activePoint && <div className="chart-tooltip" style={tooltipPosition(activePoint, width, height)} role="status"><b>{activePoint.source.timestamp}</b><span>{formatValue(activePoint.value)}</span></div>}
    <ChartToolbar interval={interval} onIntervalChange={setInterval} />
    <div className="timed-area-labels"><span>{dateLabel(first.timestamp)}</span><span>{dateLabel(last.timestamp)}</span></div>
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
