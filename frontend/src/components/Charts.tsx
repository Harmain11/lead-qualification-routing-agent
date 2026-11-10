import { useEffect, useId, useRef, useState } from "react";
import type { CSSProperties, Dispatch, ReactNode, SetStateAction } from "react";

export type PlotRow = { label: string; value: number; note?: string; period?: string };
export type PlotMetricOption = {
  id: string;
  label: string;
  rows: PlotRow[];
  formatter?: (value: number) => string;
  scaleMaximum?: number;
};

type BasicPlotSettings = {
  chartType: "bars" | "donut";
  metricId: string;
  hiddenItems: string[];
  scaleMax: string;
  valueMode: "value" | "percent";
  periodCount: number;
};

export function Bars({
  rows,
  formatter = (value) => String(value),
  scaleMaximum,
  settingsId,
  metricOptions,
  initialChartType = "bars",
}: {
  rows: PlotRow[];
  formatter?: (value: number) => string;
  scaleMaximum?: number;
  settingsId?: string;
  metricOptions?: PlotMetricOption[];
  initialChartType?: BasicPlotSettings["chartType"];
}) {
  const initialMetric = metricOptions?.[0]?.id ?? "value";
  const [settings, setSettings] = usePlotPreferences<BasicPlotSettings>(
    settingsId,
    {
      chartType: initialChartType,
      metricId: initialMetric,
      hiddenItems: [],
      scaleMax: scaleMaximum == null ? "" : String(scaleMaximum),
      valueMode: "value",
      periodCount: 0,
    },
  );
  const metric = metricOptions?.find((option) => option.id === settings.metricId);
  const activeRows = metric?.rows ?? rows;
  const activeFormatter = metric?.formatter ?? formatter;
  const activeScaleMaximum = metric?.scaleMaximum ?? scaleMaximum;
  const periods = [...new Set(activeRows.flatMap((row) => row.period ? [row.period] : []))].sort();
  const periodLimit = settings.periodCount;
  const visiblePeriods = periodLimit && periods.length > periodLimit
    ? new Set(periods.slice(-periodLimit))
    : null;
  const visibleRows = activeRows.filter((row) =>
    !settings.hiddenItems.includes(row.label) &&
    (!visiblePeriods || !row.period || visiblePeriods.has(row.period)),
  );
  const maxValue = Math.max(...visibleRows.map((row) => row.value), 1);
  const configuredMaximum = Number(settings.scaleMax);
  const maximum = Number.isFinite(configuredMaximum) && configuredMaximum >= maxValue
    ? configuredMaximum
    : activeScaleMaximum && activeScaleMaximum >= maxValue
      ? activeScaleMaximum
      : maxValue;
  const reset = () => setSettings({
    chartType: initialChartType,
    metricId: initialMetric,
    hiddenItems: [],
    scaleMax: scaleMaximum == null ? "" : String(scaleMaximum),
    valueMode: "value",
    periodCount: 0,
  });
  const total = visibleRows.reduce((sum, row) => sum + row.value, 0);
  const valueLabel = (value: number) =>
    settings.chartType === "donut" && settings.valueMode === "percent"
      ? `${total ? (value / total * 100).toFixed(1) : "0.0"}%`
      : activeFormatter(value);

  return <div className="configured-plot">
    {settingsId && (
      <PlotSettings reset={reset}>
        {metricOptions && metricOptions.length > 1 && (
          <label><span>Metric</span><select value={metric?.id ?? metricOptions[0].id} onChange={(event) => setSettings((current) => ({ ...current, metricId: event.target.value }))}>
            {metricOptions.map((option) => <option key={option.id} value={option.id}>{option.label}</option>)}
          </select></label>
        )}
        <label><span>Chart type</span><select value={settings.chartType} onChange={(event) => setSettings((current) => ({ ...current, chartType: event.target.value as BasicPlotSettings["chartType"] }))}>
          <option value="bars">Bars</option><option value="donut">Donut</option>
        </select></label>
        {periods.length > 1 && (
          <label><span>Visible periods</span><select value={periodLimit} onChange={(event) => setSettings((current) => ({ ...current, periodCount: Number(event.target.value) }))}>
            <option value={0}>All available</option>
            {[1, 3, 6, 12].filter((count) => count < periods.length).map((count) => <option value={count} key={count}>Latest {count} {count === 1 ? "period" : "periods"}</option>)}
          </select></label>
        )}
        {settings.chartType === "bars" ? (
          <label><span>Max scale</span><input type="number" min={maxValue} step="any" value={settings.scaleMax} placeholder={`Auto · ${maxValue}`} onChange={(event) => setSettings((current) => ({ ...current, scaleMax: event.target.value }))} /></label>
        ) : (
          <label><span>Slice labels</span><select value={settings.valueMode} onChange={(event) => setSettings((current) => ({ ...current, valueMode: event.target.value as BasicPlotSettings["valueMode"] }))}>
            <option value="value">Recorded values</option><option value="percent">Share of total</option>
          </select></label>
        )}
        <fieldset className="plot-item-toggles">
          <legend>Visible items</legend>
          {activeRows.map((row) => <label key={row.label}><input type="checkbox" checked={!settings.hiddenItems.includes(row.label)} onChange={(event) => setSettings((current) => ({
            ...current,
            hiddenItems: event.target.checked
              ? current.hiddenItems.filter((label) => label !== row.label)
              : [...new Set([...current.hiddenItems, row.label])],
          }))} /><span>{row.label}</span></label>)}
        </fieldset>
      </PlotSettings>
    )}
    {settings.chartType === "donut" ? (
      visibleRows.length
        ? <DonutGraphic rows={visibleRows} formatValue={valueLabel} />
        : <EmptyChart />
    ) : (
      visibleRows.length ? <div className="bar-chart">{visibleRows.map((row, index) => (
        <div className="bar-row" key={row.label} title={`${row.label}: ${activeFormatter(row.value)}`} style={{ "--bar-order": index } as CSSProperties}>
          <div><span>{row.label}</span><b>{activeFormatter(row.value)}</b></div>
          <i><span style={{ "--bar-scale": Math.max(0, Math.min(1, row.value / maximum)) } as CSSProperties} /></i>
          {row.note && <small>{row.note}</small>}
        </div>
      ))}</div> : <EmptyChart />
    )}
  </div>;
}

export function usePlotPreferences<T extends object>(
  settingsId: string | undefined,
  defaults: T,
): [T, Dispatch<SetStateAction<T>>] {
  const storageKey = settingsId ? `corral.plot-settings.v1:${settingsId}` : null;
  const defaultsRef = useRef(defaults);
  defaultsRef.current = defaults;
  const [preferences, setPreferences] = useState<T>(() => {
    if (!storageKey || typeof window === "undefined") return defaults;
    try {
      const saved: unknown = JSON.parse(
        window.localStorage.getItem(storageKey) || "null",
      );
      return saved && typeof saved === "object" && !Array.isArray(saved)
        ? { ...defaults, ...saved }
        : defaults;
    } catch {
      return defaults;
    }
  });
  const [loadedStorageKey, setLoadedStorageKey] = useState(storageKey);
  useEffect(() => {
    if (storageKey === loadedStorageKey) return;
    if (!storageKey) {
      setLoadedStorageKey(null);
      return;
    }
    let loaded: T = defaultsRef.current;
    try {
      const saved: unknown = JSON.parse(window.localStorage.getItem(storageKey) || "null");
      if (saved && typeof saved === "object" && !Array.isArray(saved)) {
        loaded = { ...defaultsRef.current, ...saved };
      }
    } catch {
      // A malformed saved setting does not prevent the chart from loading.
    }
    setPreferences(loaded);
    setLoadedStorageKey(storageKey);
  }, [loadedStorageKey, storageKey]);
  useEffect(() => {
    if (!storageKey || loadedStorageKey !== storageKey) return;
    try {
      window.localStorage.setItem(storageKey, JSON.stringify(preferences));
    } catch {
      // Plot preferences are optional when browser storage is unavailable.
    }
  }, [loadedStorageKey, preferences, storageKey]);
  return [preferences, setPreferences];
}

export function PlotSettings({
  children,
  reset,
}: {
  children: ReactNode;
  reset: () => void;
}) {
  return (
    <details className="plot-settings">
      <summary>Plot settings</summary>
      <div className="plot-settings-content">
        <div className="plot-settings-controls">{children}</div>
        <button type="button" className="plot-settings-reset" onClick={reset}>
          Reset plot
        </button>
      </div>
    </details>
  );
}

function DonutGraphic({
  rows,
  formatValue,
}: {
  rows: PlotRow[];
  formatValue: (value: number) => string;
}) {
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
    <div className="donut" key={stops.join("-")} style={{ background: total ? `conic-gradient(${stops.join(",")})` : "var(--surface-sunken)" }}><div><strong>{total}</strong><span>total</span></div></div>
    <div className="donut-legend">{rows.map((row, index) => <span key={row.label}><i style={{ background: colors[index % colors.length] }} />{row.label}<b>{formatValue(row.value)}</b></span>)}</div>
  </div>;
}

export type TrendSeries = {
  id: string;
  label: string;
  values: number[];
  weights?: number[];
  aggregation?: "sum" | "weighted_average";
  formatter?: (value: number) => string;
};

type TrendPlotSettings = {
  interval: ChartInterval;
  windowSize: number;
  chartType: "area" | "line" | "columns";
  scale: "zero" | "auto";
  visibleSeries: string[];
  dateStart: string;
  dateEnd: string;
};

const trendColors = ["#df8a4c", "#4a9a82", "#6e8cc7", "#c26262", "#aa84bd"];

export function TrendChart({
  values,
  labels,
  series,
  settingsId,
  defaultSeriesIds,
}: {
  values: number[];
  labels: string[];
  series?: TrendSeries[];
  settingsId?: string;
  defaultSeriesIds?: string[];
}) {
  const availableSeries: TrendSeries[] = series?.length
    ? series
    : [{ id: "value", label: "Value", values }];
  const initialVisible = defaultSeriesIds?.length
    ? defaultSeriesIds
    : [availableSeries[0]?.id ?? "value"];
  const [settings, setSettings] = usePlotPreferences<TrendPlotSettings>(
    settingsId,
    {
      interval: "all",
      windowSize: 0,
      chartType: "area",
      scale: "zero",
      visibleSeries: initialVisible,
      dateStart: "",
      dateEnd: "",
    },
  );
  const [hoveredPoint, setHoveredPoint] = useState<{ seriesId: string; index: number } | null>(null);
  const gradientId = useId().replaceAll(":", "");
  const selectedIds = settings.visibleSeries.filter((id) =>
    availableSeries.some((item) => item.id === id),
  );
  const activeSeries = availableSeries.filter((item) => selectedIds.includes(item.id));
  const pointCount = Math.min(labels.length, ...activeSeries.map((item) => item.values.length));
  const monthBuckets = labels.slice(0, pointCount).length > 0 &&
    labels.slice(0, pointCount).every((label) => /^\d{4}-\d{2}$/.test(label));
  const dateInputType = monthBuckets ? "month" : "date";
  const dateOptions = labels.slice(0, pointCount).map((label) => {
    const timestamp = Date.parse(label);
    return Number.isFinite(timestamp)
      ? {
          label,
          timestamp,
          inputValue: monthBuckets ? label : new Date(timestamp).toISOString().slice(0, 10),
        }
      : null;
  }).filter((item): item is { label: string; timestamp: number; inputValue: string } => item !== null);
  const minimumDate = dateOptions[0]?.inputValue ?? "";
  const maximumDate = dateOptions.at(-1)?.inputValue ?? "";
  const startTime = settings.dateStart
    ? Date.parse(monthBuckets ? `${settings.dateStart}-01T00:00:00.000Z` : `${settings.dateStart}T00:00:00.000Z`)
    : Number.NEGATIVE_INFINITY;
  const [endYear, endMonth] = settings.dateEnd.split("-").map(Number);
  const endMonthTime = settings.dateEnd && monthBuckets
    ? Date.UTC(endYear, endMonth, 0, 23, 59, 59, 999)
    : Number.POSITIVE_INFINITY;
  const endTime = settings.dateEnd
    ? monthBuckets
      ? endMonthTime
      : Date.parse(`${settings.dateEnd}T23:59:59.999Z`)
    : Number.POSITIVE_INFINITY;
  const selectedIndices = labels.slice(0, pointCount).flatMap((label, index) => {
    const timestamp = Date.parse(label);
    return Number.isFinite(timestamp) && timestamp >= startTime && timestamp <= endTime
      ? [index]
      : [];
  });
  const windowIndices = settings.windowSize && selectedIndices.length > settings.windowSize
    ? selectedIndices.slice(-settings.windowSize)
    : selectedIndices;
  const sourceLabels = windowIndices.map((index) => labels[index] ?? "");
  const plottedSeries = activeSeries.map((item) => {
    const items = sourceLabels.map((label, index) => ({
      label,
      value: item.values[windowIndices[index]] ?? 0,
      weight: item.weights?.[windowIndices[index]] ?? 1,
    }));
    return {
      ...item,
      points: aggregateLabeledPoints(items, settings.interval, item.aggregation ?? "sum"),
    };
  }).filter((item) => item.points.length);
  const visible = plottedSeries[0]?.points ?? [];
  const valuesInView = plottedSeries.flatMap((item) => item.points.map((point) => point.value));
  const dataMin = valuesInView.length ? Math.min(...valuesInView) : 0;
  const dataMax = valuesInView.length ? Math.max(...valuesInView) : 1;
  const minimum = settings.scale === "zero" ? Math.min(dataMin, 0) : dataMin;
  const maximum = settings.scale === "zero" ? Math.max(dataMax, 0) : dataMax;
  const range = Math.max(maximum - minimum, 1);
  const width = 720;
  const height = 220;
  const y = (value: number) => height - 24 - ((value - minimum) / range) * (height - 48);
  const x = (index: number) => visible.length === 1
    ? width / 2
    : (index / (visible.length - 1)) * width;
  const baseline = y(0);
  const pointsBySeries = plottedSeries.map((item, seriesIndex) => ({
    ...item,
    color: trendColors[seriesIndex % trendColors.length],
    points: item.points.map((point, index) => ({ ...point, x: x(index), y: y(point.value) })),
  }));
  const hovered = hoveredPoint
    ? pointsBySeries.find((item) => item.id === hoveredPoint.seriesId)?.points[hoveredPoint.index] ?? null
    : null;
  const reset = () => setSettings({
    interval: "all",
    windowSize: 0,
    chartType: "area",
    scale: "zero",
    visibleSeries: initialVisible,
    dateStart: "",
    dateEnd: "",
  });
  const timelineSettings = (
    <>
      <label><span>From date</span><input type={dateInputType} min={minimumDate || undefined} max={maximumDate || undefined} value={settings.dateStart} onChange={(event) => setSettings((current) => ({
        ...current,
        dateStart: event.target.value,
        dateEnd: current.dateEnd && event.target.value > current.dateEnd ? event.target.value : current.dateEnd,
      }))} /></label>
      <label><span>To date</span><input type={dateInputType} min={minimumDate || undefined} max={maximumDate || undefined} value={settings.dateEnd} onChange={(event) => setSettings((current) => ({
        ...current,
        dateEnd: event.target.value,
        dateStart: current.dateStart && event.target.value < current.dateStart ? event.target.value : current.dateStart,
      }))} /></label>
      <label><span>Visible points</span><select value={settings.windowSize} onChange={(event) => setSettings((current) => ({ ...current, windowSize: Number(event.target.value) }))}>
        <option value={0}>All available</option>
        {[7, 14, 30, 90].filter((count) => count < pointCount).map((count) => <option value={count} key={count}>Latest {count}</option>)}
      </select></label>
      <label><span>Granularity</span><select value={settings.interval} onChange={(event) => setSettings((current) => ({ ...current, interval: event.target.value as ChartInterval }))}>
        <option value="all">Source periods</option><option value="daily">Daily</option><option value="weekly">Weekly</option><option value="monthly">Monthly</option><option value="annual">Annual</option>
      </select></label>
      <label><span>Chart type</span><select value={settings.chartType} onChange={(event) => setSettings((current) => ({ ...current, chartType: event.target.value as TrendPlotSettings["chartType"] }))}>
        <option value="area">Area</option><option value="line">Line</option><option value="columns">Columns</option>
      </select></label>
      <label><span>Value scale</span><select value={settings.scale} onChange={(event) => setSettings((current) => ({ ...current, scale: event.target.value as TrendPlotSettings["scale"] }))}>
        <option value="zero">Include zero</option><option value="auto">Fit values</option>
      </select></label>
      <fieldset className="plot-item-toggles">
        <legend>Visible series</legend>
        {availableSeries.map((item) => <label key={item.id}><input type="checkbox" checked={selectedIds.includes(item.id)} disabled={selectedIds.length === 1 && selectedIds.includes(item.id)} onChange={(event) => setSettings((current) => ({
          ...current,
          visibleSeries: event.target.checked
            ? [...new Set([...current.visibleSeries, item.id])]
            : current.visibleSeries.filter((id) => id !== item.id),
        }))} /><span>{item.label}</span></label>)}
      </fieldset>
    </>
  );

  return <div className="trend-chart configured-plot">
    {settingsId ? (
      <PlotSettings reset={reset}>{timelineSettings}</PlotSettings>
    ) : (
      <ChartToolbar interval={settings.interval} onIntervalChange={(interval) => setSettings((current) => ({ ...current, interval }))} />
    )}
    {plottedSeries.length > 0 && visible.length > 0 ? <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label="Time-series trend">
      <defs>{pointsBySeries.map((item, index) => <linearGradient id={`${gradientId}-${index}`} x1="0" y1="0" x2="0" y2="1" key={item.id}><stop offset="0" stopColor={item.color} stopOpacity=".34" /><stop offset="1" stopColor={item.color} stopOpacity="0" /></linearGradient>)}</defs>
      {pointsBySeries.map((item, seriesIndex) => {
        const path = item.points.map((point, index) => `${index ? "L" : "M"}${point.x},${point.y}`).join(" ");
        const area = `${path} L${item.points.at(-1)?.x ?? width},${baseline} L${item.points[0]?.x ?? 0},${baseline} Z`;
        const groupWidth = width / Math.max(visible.length, 1);
        const barWidth = Math.min(22, groupWidth * .72 / pointsBySeries.length);
        const columnX = (pointIndex: number) =>
          x(pointIndex) - (barWidth * pointsBySeries.length) / 2 + seriesIndex * barWidth;
        return <g key={item.id}>
          {settings.chartType === "area" && <path className="trend-area" d={area} style={{ fill: `url(#${gradientId}-${seriesIndex})` }} />}
          {settings.chartType !== "columns" && <path className="trend-line" d={path} pathLength="1" style={{ stroke: item.color }} />}
          {item.points.map((point, index) => settings.chartType === "columns" ? (
            <rect key={`${point.label}-${index}`} x={columnX(index)} y={Math.min(point.y, baseline)} width={barWidth * .86} height={Math.max(1, Math.abs(baseline - point.y))} rx="2" fill={item.color} tabIndex={0} onMouseEnter={() => setHoveredPoint({ seriesId: item.id, index })} onMouseLeave={() => setHoveredPoint(null)} onFocus={() => setHoveredPoint({ seriesId: item.id, index })} onBlur={() => setHoveredPoint(null)}><title>{`${point.label}: ${item.formatter?.(point.value) ?? point.value}`}</title></rect>
          ) : (
            <circle key={`${point.label}-${index}`} tabIndex={0} cx={point.x} cy={point.y} r="5" fill={item.color} style={{ "--point-order": index } as CSSProperties} onMouseEnter={() => setHoveredPoint({ seriesId: item.id, index })} onMouseLeave={() => setHoveredPoint(null)} onFocus={() => setHoveredPoint({ seriesId: item.id, index })} onBlur={() => setHoveredPoint(null)}><title>{`${point.label}: ${item.formatter?.(point.value) ?? point.value}`}</title></circle>
          ))}
        </g>;
      })}
    </svg> : <EmptyChart />}
    {hovered && hoveredPoint && <div className="chart-tooltip" style={tooltipPosition(hovered, width, height)} role="status"><b>{hovered.label}</b>{pointsBySeries.map((item) => <span key={item.id}>{item.label}: {item.formatter?.(item.points[hoveredPoint.index]?.value ?? 0) ?? item.points[hoveredPoint.index]?.value}</span>)}</div>}
    {pointsBySeries.length > 1 && <div className="plot-series-legend">{pointsBySeries.map((item) => <span key={item.id}><i style={{ background: item.color }} />{item.label}</span>)}</div>}
    {visible.length > 0 && <div className="trend-labels"><span>{visible[0]?.label}</span><span>{visible.at(-1)?.label}</span></div>}
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

function aggregateLabeledPoints(
  items: Array<{ value: number; label: string; weight?: number }>,
  interval: ChartInterval,
  aggregation: "sum" | "weighted_average" = "sum",
) {
  if (interval === "all") return items.map(({ value, label }) => ({ value, label }));
  const dated = items.flatMap((item) => {
    const timestamp = Date.parse(item.label);
    return Number.isFinite(timestamp) ? [{ ...item, timestamp }] : [];
  });
  if (dated.length !== items.length) return items;
  const buckets = new Map<number, { value: number; weight: number; label: string }>();
  dated.forEach((item) => {
    const bucket = intervalStart(item.timestamp, interval);
    const current = buckets.get(bucket);
    const weight = aggregation === "weighted_average" ? Math.max(0, item.weight ?? 1) : 1;
    buckets.set(bucket, {
      value: (current?.value ?? 0) + item.value * weight,
      weight: (current?.weight ?? 0) + weight,
      label: intervalLabel(bucket, interval),
    });
  });
  return [...buckets.values()].map((item) => ({
    label: item.label,
    value: aggregation === "weighted_average"
      ? item.weight ? item.value / item.weight : 0
      : item.value,
  }));
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
      <defs><linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1"><stop offset="0" stopColor="var(--tint)" stopOpacity=".3" /><stop offset="1" stopColor="var(--tint)" stopOpacity=".025" /></linearGradient></defs>
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

export function Donut({
  rows,
  settingsId,
}: {
  rows: Array<{ label: string; value: number }>;
  settingsId?: string;
}) {
  return <Bars rows={rows} settingsId={settingsId} initialChartType="donut" />;
}

function EmptyChart() {
  return <div className="chart-empty"><span />No data in this period</div>;
}
