import { useId } from "react";
import type { CSSProperties } from "react";

export function Bars({
  rows,
  formatter = (value) => String(value),
}: {
  rows: Array<{ label: string; value: number; note?: string }>;
  formatter?: (value: number) => string;
}) {
  const maximum = Math.max(...rows.map((row) => row.value), 1);
  if (rows.length === 0) return <EmptyChart />;
  return <div className="bar-chart">{rows.map((row, index) => (
    <div className="bar-row" key={row.label} title={`${row.label}: ${formatter(row.value)}`} style={{ "--bar-order": index } as CSSProperties}>
      <div><span>{row.label}</span><b>{formatter(row.value)}</b></div>
      <i><span style={{ "--bar-scale": row.value / maximum } as CSSProperties} /></i>
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
      <defs><linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1"><stop offset="0" stopColor="#F48067" stopOpacity=".34" /><stop offset="1" stopColor="#E13511" stopOpacity="0" /></linearGradient></defs>
      <path className="trend-area" d={area} style={{ fill: `url(#${gradientId})` }} />
      <path className="trend-line" d={path} pathLength="1" />
      {points.map((point, index) => <g key={`${point.label}-${point.x}`}><circle cx={point.x} cy={point.y} r="5" style={{ "--point-order": index } as CSSProperties}><title>{point.label}: {point.value}</title></circle></g>)}
    </svg>
    <div className="trend-labels"><span>{labels[0]}</span><span>{labels.at(-1)}</span></div>
  </div>;
}

export function Donut({ rows }: { rows: Array<{ label: string; value: number }> }) {
  const total = rows.reduce((sum, row) => sum + row.value, 0);
  let cursor = 0;
  const colors = ["#E13511", "#F48067", "#f8c88c", "#8c918f", "#555b5a"];
  const stops = rows.map((row, index) => {
    const start = total ? (cursor / total) * 100 : 0;
    cursor += row.value;
    const end = total ? (cursor / total) * 100 : 0;
    return `${colors[index % colors.length]} ${start}% ${end}%`;
  });
  return <div className="donut-wrap">
    <div className="donut" key={stops.join("-")} style={{ background: total ? `conic-gradient(${stops.join(",")})` : "#252827" }}><div><strong>{total}</strong><span>leads</span></div></div>
    <div className="donut-legend">{rows.map((row, index) => <span key={row.label}><i style={{ background: colors[index % colors.length] }} />{row.label}<b>{row.value}</b></span>)}</div>
  </div>;
}

function EmptyChart() {
  return <div className="chart-empty"><span />No data in this period</div>;
}
