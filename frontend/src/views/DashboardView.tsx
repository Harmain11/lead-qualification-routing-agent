import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { Bars, Donut, TrendChart } from "../components/Charts";
import { DashboardInsights } from "../components/DashboardInsights";
import { DashboardSignals } from "../components/DashboardSignals";
import type { DashboardAnalytics } from "../types";
import type { AnalyticsBundle } from "./productShared";
import {
  Empty,
  LeadRow,
  Panel,
  SectionVisibilityPicker,
  Unavailable,
  defaultSectionSelection,
  loadSectionSelection,
  metricValue,
  money,
  pretty,
} from "./productShared";

const dashboardWidgetOptions = [
  {
    id: "qualification-pulse",
    label: "Qualification pulse",
    group: "Key metrics",
  },
  { id: "total_leads", label: "Total leads", group: "Key metrics" },
  {
    id: "qualification_rate",
    label: "Qualification rate",
    group: "Key metrics",
  },
  { id: "hot_leads", label: "Hot leads", group: "Key metrics" },
  {
    id: "active_pipeline_value",
    label: "Active pipeline value",
    group: "Key metrics",
  },
  { id: "manual_review", label: "Manual review", group: "Key metrics" },
  { id: "active-pipeline-origin", label: "Active pipeline by source", group: "Dashboard panels" },
  {
    id: "attention-queue",
    label: "Attention queue",
    group: "Dashboard panels",
  },
  { id: "speed-to-qualify", label: "Speed to Qualify", group: "Dashboard panels" },
  { id: "sla-compliance", label: "SLA Compliance", group: "Dashboard panels" },
  { id: "override-rate", label: "Override Rate", group: "Dashboard panels" },
  { id: "qualified-to-booked", label: "Qualified → Booked Rate", group: "Dashboard panels" },
  { id: "funnel-bottleneck", label: "Funnel Bottleneck View", group: "Dashboard panels" },
  { id: "rep-capacity", label: "Rep Capacity & Load", group: "Dashboard panels" },
  { id: "pipeline-coverage", label: "Pipeline Coverage vs Target", group: "Dashboard panels" },
  { id: "decay-momentum", label: "Decay / Momentum Alerts", group: "Dashboard panels" },
  { id: "forecast-narrative", label: "Forecast Narrative", group: "Dashboard panels" },
  { id: "first-response", label: "First-Response Leaderboard", group: "Dashboard panels" },
  { id: "escalation-alerts", label: "Escalation Alerts", group: "Dashboard panels" },
  { id: "agent-activity", label: "Agent Activity Snapshot", group: "Dashboard panels" },
  {
    id: "signal-velocity",
    label: "Signal velocity",
    group: "Dashboard panels",
  },
  {
    id: "qualification-mix",
    label: "Qualification mix",
    group: "Dashboard panels",
  },
  {
    id: "channel-intelligence",
    label: "Channel intelligence",
    group: "Dashboard panels",
  },
  { id: "pipeline-pulse", label: "Pipeline pulse", group: "Dashboard panels" },
  { id: "revenue-trend", label: "Revenue trend", group: "Useful insights" },
  {
    id: "source-qualification",
    label: "Qualification rate by source",
    group: "Useful insights",
  },
  {
    id: "source-quality",
    label: "Lead quality by source",
    group: "Useful insights",
  },
] as const;

type DashboardWidgetId = (typeof dashboardWidgetOptions)[number]["id"];
const newlyAddedDashboardWidgetIds = [
  "active-pipeline-origin",
  "speed-to-qualify",
  "sla-compliance",
  "override-rate",
  "qualified-to-booked",
  "funnel-bottleneck",
  "rep-capacity",
  "pipeline-coverage",
  "decay-momentum",
  "forecast-narrative",
  "first-response",
  "escalation-alerts",
  "agent-activity",
] as const;

function loadDashboardWidgetSelection(storageKey: string) {
  const selected = loadSectionSelection(storageKey, dashboardWidgetOptions);
  if (typeof window === "undefined") return selected;
  try {
    const saved = JSON.parse(window.localStorage.getItem(storageKey) || "null");
    // Restore the remaining useful panels if the removed topology was the
    // only card a returning user had selected.
    if (Array.isArray(saved) && saved.includes("lead-intelligence-network")) {
      if (!selected.size)
        return defaultSectionSelection(dashboardWidgetOptions);
    }
    // Introduce new insight cards once, without restoring cards the user hid
    // or overriding an intentionally empty dashboard.
    if (
      selected.size &&
      !window.localStorage.getItem(`${storageKey}:insights-v1`)
    ) {
      selected.add("revenue-trend");
      selected.add("source-qualification");
      selected.add("source-quality");
    }
    if (
      Array.isArray(saved) &&
      saved.length &&
      !window.localStorage.getItem(`${storageKey}:expanded-v2`)
    ) {
      newlyAddedDashboardWidgetIds.forEach((id) =>
        selected.add(id as DashboardWidgetId),
      );
    }
  } catch {
    // Dashboard preferences remain optional when browser storage is unavailable.
  }
  return selected;
}

export function DashboardView({
  data,
  onInspect,
  userId = "local",
}: {
  data: AnalyticsBundle | null;
  onInspect: (id: string) => void;
  userId?: string;
}) {
  const preferenceKey = `corral.dashboard.widgets.v1:${userId}`;
  const [visibleWidgets, setVisibleWidgets] = useState<Set<DashboardWidgetId>>(
    () => loadDashboardWidgetSelection(preferenceKey),
  );
  const [widgetSelectionDraft, setWidgetSelectionDraft] =
    useState<Set<DashboardWidgetId>>(() => new Set(visibleWidgets));
  useEffect(() => {
    try {
      window.localStorage.setItem(
        preferenceKey,
        JSON.stringify([...visibleWidgets]),
      );
      window.localStorage.setItem(`${preferenceKey}:insights-v1`, "seen");
      window.localStorage.setItem(`${preferenceKey}:expanded-v2`, "seen");
    } catch {
      // Dashboard preferences are optional when browser storage is unavailable.
    }
  }, [preferenceKey, visibleWidgets]);

  if (!data?.dashboard || !data.pipeline || !data.series || !data.channels)
    return <Unavailable />;
  const metricIds = new Set<string>(
    dashboardWidgetOptions
      .filter((option) => option.group === "Key metrics")
      .map((option) => option.id),
  );
  const metrics = data.dashboard.metrics.filter(
    (metric) =>
      metricIds.has(metric.key) &&
      visibleWidgets.has(metric.key as DashboardWidgetId),
  );
  const qualificationRate = metricValue(data.dashboard, "qualification_rate");
  const totalLeads = metricValue(data.dashboard, "total_leads");
  const settingsPrefix = `dashboard:${userId}:${data.dashboard.metadata.workspace_id}`;
  const channelMetrics = [
    {
      id: "qualified-to-booked",
      label: "Qualified → booked rate",
      rows: data.channels.channels.filter((row) => row.qualified_to_booked_rate != null).map((row) => ({
        label: pretty(row.channel),
        value: row.qualified_to_booked_rate ?? 0,
        note: `${row.qualified_leads ?? 0} qualified · ${row.booked_leads ?? 0} booked`,
      })),
      formatter: (value: number) => `${value}%`,
      scaleMaximum: 100,
    },
    {
      id: "qualification-rate",
      label: "Qualification rate",
      rows: data.channels.channels.filter((row) => row.qualification_rate != null).map((row) => ({
        label: pretty(row.channel),
        value: row.qualification_rate ?? 0,
      })),
      formatter: (value: number) => `${value}%`,
      scaleMaximum: 100,
    },
    {
      id: "sql-rate",
      label: "SQL conversion rate",
      rows: data.channels.channels.filter((row) => row.sql_conversion_rate != null).map((row) => ({
        label: pretty(row.channel),
        value: row.sql_conversion_rate ?? 0,
      })),
      formatter: (value: number) => `${value}%`,
      scaleMaximum: 100,
    },
    {
      id: "won",
      label: "Closed-won deals",
      rows: data.channels.channels.map((row) => ({
        label: pretty(row.channel),
        value: row.closed_won_count ?? 0,
      })),
      formatter: (value: number) => `${value} won`,
    },
    {
      id: "revenue",
      label: "Recorded revenue",
      rows: data.channels.channels.map((row) => ({
        label: pretty(row.channel),
        value: row.revenue,
      })),
      formatter: money,
    },
  ];
  const pipelineMetrics = [
    {
      id: "lead-count",
      label: "Leads by stage",
      rows: data.pipeline.stages.map((stage) => ({
        label: stage.label,
        value: stage.count,
        note: stage.value ? money(stage.value) : undefined,
      })),
    },
    {
      id: "opportunity-value",
      label: "Opportunity value",
      rows: data.pipeline.stages.map((stage) => ({
        label: stage.label,
        value: stage.value,
        note: `${stage.count} leads`,
      })),
      formatter: money,
    },
  ];
  const hasCommandWidgets = visibleWidgets.has("attention-queue");
  const hasTrendWidgets =
    visibleWidgets.has("signal-velocity") ||
    visibleWidgets.has("qualification-mix");
  const hasBreakdownWidgets =
    visibleWidgets.has("channel-intelligence") ||
    visibleWidgets.has("pipeline-pulse");
  const toggleWidget = (id: DashboardWidgetId, checked: boolean) => {
    setWidgetSelectionDraft((current) => {
      const next = new Set(current);
      if (checked) next.add(id);
      else next.delete(id);
      return next;
    });
  };

  return (
    <div className="page-view dashboard-view">
      <header className="page-hero dashboard-hero" data-tour="dashboard">
        <div>
          <span className="eyebrow">
            Workspace overview / {data.dashboard.metadata.range}
          </span>
          <h1>Dashboard</h1>
          <p>
            Follow lead volume, qualification, routing and recorded revenue for this workspace.
          </p>
        </div>
        {visibleWidgets.has("qualification-pulse") && (
          <div className="hero-pulse">
            <span>Qualification pulse</span>
            <strong>{qualificationRate}%</strong>
            <i>
              <span
                style={{ transform: `scaleX(${qualificationRate / 100})` }}
              />
            </i>
            <small>
              {metricValue(data.dashboard, "sqls")} sales-qualified leads
            </small>
          </div>
        )}
      </header>
      <SectionVisibilityPicker
        options={dashboardWidgetOptions}
        visible={widgetSelectionDraft}
        applied={visibleWidgets}
        description="Select the metrics and cards you want, then submit to update your dashboard."
        showAllLabel="Show all cards"
        onToggle={toggleWidget}
        onShowAll={() =>
          setWidgetSelectionDraft(defaultSectionSelection(dashboardWidgetOptions))
        }
        onSubmit={() => setVisibleWidgets(new Set(widgetSelectionDraft))}
      />
      {totalLeads === 0 && (
        <section className="panel workspace-empty">
          <Empty
            title="No leads yet"
            text="Connect a source or add your first lead to get started."
          />
        </section>
      )}
      {visibleWidgets.size > 0 ? (
        <>
          {metrics.length > 0 && (
            <section className="metric-grid dashboard-metric-grid">
              {metrics.map((metric) => (
                <MetricCard key={metric.key} metric={metric} />
              ))}
            </section>
          )}
          {hasCommandWidgets && (
            <section className="dashboard-command-grid">
              {visibleWidgets.has("attention-queue") &&
                (data.review ? (
                  <Panel
                    title="Attention queue"
                    eyebrow="Human judgment · highest combined score first"
                    action={`${data.review.total} open`}
                  >
                    {data.review.items.length ? (
                      <>
                        <p className="panel-copy">Ranked by fit (45%), urgency (35%) and recorded opportunity value (20%).</p>
                        <div className="compact-leads">
                          {data.review.items.slice(0, 5).map((lead) => (
                            <div className="dashboard-ranked-lead" key={lead.id}>
                              <LeadRow
                                lead={lead}
                                onClick={() => onInspect(lead.id)}
                              />
                              {lead.attention_score != null && (
                                <small>{lead.attention_score}/100 attention · {lead.sla_overdue ? "SLA overdue" : pretty(lead.priority ?? "normal")}</small>
                              )}
                            </div>
                          ))}
                        </div>
                      </>
                    ) : (
                      <Empty
                        title="No review debt"
                        text="No leads need human review in this period."
                      />
                    )}
                  </Panel>
                ) : (
                  <Panel
                    title="Attention queue"
                    eyebrow="Human judgment required"
                  >
                    <Empty
                      title="Not available"
                      text="Your workspace role does not include manual review access."
                    />
                  </Panel>
                ))}
            </section>
          )}
          {hasTrendWidgets && (
            <section className="dashboard-grid">
              {visibleWidgets.has("signal-velocity") && (
                <Panel
                  title="Signal velocity"
                  eyebrow="Lead volume over time"
                  action="Server aggregated"
                >
                  <TrendChart
                    values={data.series.points.map((point) => point.leads)}
                    labels={data.series.points.map((point) => point.period)}
                    settingsId={`${settingsPrefix}:signal-velocity`}
                    defaultSeriesIds={["leads"]}
                    series={[
                      {
                        id: "leads",
                        label: "Leads",
                        values: data.series.points.map((point) => point.leads),
                      },
                      {
                        id: "sqls",
                        label: "SQLs",
                        values: data.series.points.map((point) => point.sqls),
                      },
                      {
                        id: "average-score",
                        label: "Average score",
                        values: data.series.points.map((point) => point.average_score),
                        weights: data.series.points.map((point) => point.leads),
                        aggregation: "weighted_average",
                        formatter: (value) => value.toFixed(1),
                      },
                      {
                        id: "revenue",
                        label: "Revenue",
                        values: data.series.points.map((point) => point.revenue),
                        formatter: money,
                      },
                    ]}
                  />
                </Panel>
              )}
              {visibleWidgets.has("qualification-mix") && (
                <Panel title="Qualification mix" eyebrow="Decision bands">
                  <Donut
                    rows={data.dashboard.band_distribution.map((row) => ({
                      label: row.band,
                      value: row.count,
                    }))}
                    settingsId={`${settingsPrefix}:qualification-mix`}
                  />
                </Panel>
              )}
            </section>
          )}
          {hasBreakdownWidgets && (
            <section className="dashboard-grid">
              {visibleWidgets.has("channel-intelligence") && (
                <Panel
                  title="Channel intelligence"
                  eyebrow="Qualified and booked rates"
                >
                  <Bars
                    rows={channelMetrics[0].rows}
                    metricOptions={channelMetrics}
                    settingsId={`${settingsPrefix}:channel-intelligence`}
                    formatter={channelMetrics[0].formatter}
                    scaleMaximum={channelMetrics[0].scaleMaximum}
                  />
                </Panel>
              )}
              {visibleWidgets.has("pipeline-pulse") && (
                <Panel
                  title="Pipeline pulse"
                  eyebrow="Active progression by stage"
                >
                  <Bars
                    rows={pipelineMetrics[0].rows}
                    metricOptions={pipelineMetrics}
                    settingsId={`${settingsPrefix}:pipeline-pulse`}
                  />
                </Panel>
              )}
            </section>
          )}
          <DashboardSignals
            data={data}
            visible={visibleWidgets}
            onInspect={onInspect}
            settingsPrefix={settingsPrefix}
          />
          <DashboardInsights
            channels={data.channels}
            revenue={data.revenue}
            extended={data.extended}
            visible={visibleWidgets}
            settingsPrefix={settingsPrefix}
          />
        </>
      ) : (
        <section className="section-picker-empty panel">
          <h2>No dashboard cards selected</h2>
          <p>Choose one or more items above to add them back.</p>
        </section>
      )}
    </div>
  );
}

function MetricCard({
  metric,
}: {
  metric: DashboardAnalytics["metrics"][number];
}) {
  const animated = useCountUp(metric.value);
  const featured =
    metric.key === "total_leads" || metric.key === "active_pipeline_value";
  const formatted =
    metric.key.includes("value") || metric.key.includes("revenue")
      ? money(animated)
      : metric.key.includes("rate")
        ? `${animated}%`
        : metric.key.includes("minutes")
          ? `${animated}m`
          : Math.round(animated).toLocaleString();
  return (
    <article
      className={`metric-card metric-${metric.key} ${featured ? "metric-featured" : ""}`}
    >
      <span>{metric.label}</span>
      <strong>{formatted}</strong>
      <small
        className={
          metric.change_percent != null && metric.change_percent < 0
            ? "negative"
            : ""
        }
      >
        <i>
          {metric.change_percent == null
            ? "·"
            : metric.change_percent >= 0
              ? "↗"
              : "↘"}
        </i>
        {metric.change_percent == null
          ? "No valid prior comparison"
          : `${metric.change_percent > 0 ? "+" : ""}${metric.change_percent}% vs prior period`}
      </small>
      <span className="metric-baseline">
        <i
          style={{
            transform: `scaleX(${Math.min(1, Math.max(0.08, Math.abs(metric.change_percent ?? 8) / 35))})`,
          }}
        />
      </span>
      <i />
    </article>
  );
}

function useCountUp(value: number) {
  const [display, setDisplay] = useState(value);
  const displayRef = useRef(display);
  useLayoutEffect(() => {
    const setCurrent = (next: number) => {
      displayRef.current = next;
      setDisplay(next);
    };
    const motion = window.matchMedia("(prefers-reduced-motion: reduce)");
    let frame = 0;
    const applyReducedMotion = () => {
      if (!motion.matches) return;
      cancelAnimationFrame(frame);
      setCurrent(value);
    };
    motion.addEventListener("change", applyReducedMotion);
    if (motion.matches) {
      setCurrent(value);
      return () => motion.removeEventListener("change", applyReducedMotion);
    }
    const from = displayRef.current;
    if (from === value) {
      return () => motion.removeEventListener("change", applyReducedMotion);
    }
    const started = performance.now();
    const duration = 650;
    const tick = (now: number) => {
      const progress = Math.min(1, (now - started) / duration);
      const eased = 1 - Math.pow(1 - progress, 3);
      setCurrent(Math.round((from + (value - from) * eased) * 10) / 10);
      if (progress < 1) frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => {
      cancelAnimationFrame(frame);
      motion.removeEventListener("change", applyReducedMotion);
    };
  }, [value]);
  return display;
}
