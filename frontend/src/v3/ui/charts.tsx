import { useId, useState, type ReactNode } from "react";

/**
 * Small, quiet charts drawn with SVG and CSS. Each one carries a text
 * equivalent for screen readers; colour is never the only signal.
 */

export function BarList({
  items,
  format,
  empty = "Nothing recorded yet.",
  max: fixedMax,
  tone = "tint",
}: {
  items: Array<{ label: ReactNode; value: number; detail?: ReactNode; key?: string }>;
  format: (value: number) => string;
  empty?: string;
  max?: number;
  tone?: "tint" | "attention" | "info" | "neutral";
}) {
  if (!items.length) return <p className="k-empty">{empty}</p>;
  const max = fixedMax ?? Math.max(...items.map((item) => item.value), 1);
  return (
    <ul className="k-barlist" data-tone={tone}>
      {items.map((item, index) => (
        <li key={item.key ?? index}>
          <span className="k-barlist-label">{item.label}</span>
          <span className="k-barlist-value num">{format(item.value)}</span>
          <span className="k-barlist-track" aria-hidden="true">
            <i style={{ transform: `scaleX(${Math.max(0, Math.min(1, item.value / max))})` }} />
          </span>
          {item.detail && <span className="k-barlist-detail">{item.detail}</span>}
        </li>
      ))}
    </ul>
  );
}

export function ColumnChart({
  points,
  format,
  height = 168,
  label,
  secondary,
}: {
  points: Array<{ label: string; value: number; secondary?: number }>;
  format: (value: number) => string;
  height?: number;
  label: string;
  secondary?: { label: string; format: (value: number) => string };
}) {
  const [hover, setHover] = useState<number | null>(null);
  const id = useId();
  if (!points.length) return <p className="k-empty">No data in this period.</p>;
  const max = Math.max(...points.map((point) => Math.max(point.value, point.secondary ?? 0)), 1);
  const active = hover ?? points.length - 1;
  return (
    <figure className="k-columns" aria-labelledby={`${id}-cap`}>
      <figcaption id={`${id}-cap`} className="v-sr">{label}</figcaption>
      <div className="k-columns-readout" aria-hidden="true">
        <b className="num">{format(points[active].value)}</b>
        <span>{points[active].label}{secondary && points[active].secondary != null ? ` · ${secondary.format(points[active].secondary!)} ${secondary.label}` : ""}</span>
      </div>
      <div className="k-columns-plot" style={{ height }} onMouseLeave={() => setHover(null)} aria-hidden="true">
        {points.map((point, index) => (
          <span
            key={`${point.label}-${index}`}
            className="k-col"
            data-active={index === active || undefined}
            onMouseEnter={() => setHover(index)}
          >
            {point.secondary != null && <i className="k-col-secondary" style={{ height: `${(point.secondary / max) * 100}%` }} />}
            <i className="k-col-bar" style={{ height: `${Math.max(point.value > 0 ? 2 : 0, (point.value / max) * 100)}%` }} />
            <small>{point.label}</small>
          </span>
        ))}
      </div>
      <table className="v-sr">
        <caption>{label}</caption>
        <tbody>
          {points.map((point, index) => (
            <tr key={index}><th scope="row">{point.label}</th><td>{format(point.value)}</td>{secondary && <td>{point.secondary != null ? secondary.format(point.secondary) : ""}</td>}</tr>
          ))}
        </tbody>
      </table>
    </figure>
  );
}

export function GoalRing({ value, goal, size = 132, children, label }: { value: number; goal: number | null; size?: number; children?: ReactNode; label: string }) {
  const stroke = 10;
  const radius = (size - stroke) / 2;
  const circumference = 2 * Math.PI * radius;
  const ratio = goal && goal > 0 ? Math.min(1, value / goal) : 0;
  return (
    <span className="k-goal" style={{ width: size, height: size }} role="img" aria-label={label}>
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} aria-hidden="true">
        <circle className="k-goal-track" cx={size / 2} cy={size / 2} r={radius} strokeWidth={stroke} />
        {goal ? (
          <circle
            className="k-goal-value"
            cx={size / 2}
            cy={size / 2}
            r={radius}
            strokeWidth={stroke}
            strokeDasharray={circumference}
            strokeDashoffset={circumference * (1 - ratio)}
          />
        ) : null}
      </svg>
      <span className="k-goal-center">{children}</span>
    </span>
  );
}

export function Sparkline({ values, width = 96, height = 28, label }: { values: number[]; width?: number; height?: number; label: string }) {
  if (values.length < 2) return null;
  const max = Math.max(...values, 1);
  const min = Math.min(...values, 0);
  const span = max - min || 1;
  const step = width / (values.length - 1);
  const points = values.map((value, index) => `${(index * step).toFixed(1)},${(height - 2 - ((value - min) / span) * (height - 4)).toFixed(1)}`);
  return (
    <svg className="k-spark" width={width} height={height} viewBox={`0 0 ${width} ${height}`} role="img" aria-label={label}>
      <polyline points={points.join(" ")} fill="none" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

export function SegmentBar({ segments, label }: { segments: Array<{ key: string; value: number; label: string }>; label: string }) {
  const total = segments.reduce((sum, segment) => sum + segment.value, 0);
  return (
    <div className="k-segbar" role="img" aria-label={`${label}: ${segments.map((segment) => `${segment.label} ${segment.value}`).join(", ")}`}>
      {total === 0 ? (
        <i data-empty />
      ) : (
        segments.filter((segment) => segment.value > 0).map((segment) => (
          <i key={segment.key} data-key={segment.key} style={{ flexGrow: segment.value }} title={`${segment.label}: ${segment.value}`} />
        ))
      )}
    </div>
  );
}

export function Delta({ value, suffix = "%", invert = false }: { value: number | null | undefined; suffix?: string; invert?: boolean }) {
  if (value == null || !Number.isFinite(value)) return null;
  const good = invert ? value < 0 : value > 0;
  const flat = Math.abs(value) < 0.05;
  return (
    <span className="k-delta" data-tone={flat ? "flat" : good ? "up" : "down"}>
      {flat ? "No change" : `${value > 0 ? "▲" : "▼"} ${Math.abs(value).toFixed(Math.abs(value) < 10 ? 1 : 0)}${suffix}`}
    </span>
  );
}
