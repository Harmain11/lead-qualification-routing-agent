import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { Bars, Donut, TrendChart } from "../components/Charts";
import { DashboardInsights } from "../components/DashboardInsights";
import type { DashboardAnalytics } from "../types";
import type { AnalyticsBundle } from "./productShared";
import {
  ActivityFeed,
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
    group: "Overview",
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
  {
    id: "average_routing_velocity_minutes",
    label: "Routing velocity",
    group: "Key metrics",
  },
  {
    id: "live-activity",
    label: "Live operational activity",
    group: "Dashboard panels",
  },
  {
    id: "attention-queue",
    label: "Attention queue",
    group: "Dashboard panels",
  },
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
  useEffect(() => {
    try {
      window.localStorage.setItem(
        preferenceKey,
        JSON.stringify([...visibleWidgets]),
      );
      window.localStorage.setItem(`${preferenceKey}:insights-v1`, "seen");
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
  const hasCommandWidgets =
    visibleWidgets.has("live-activity") ||
    visibleWidgets.has("attention-queue");
  const hasTrendWidgets =
    visibleWidgets.has("signal-velocity") ||
    visibleWidgets.has("qualification-mix");
  const hasBreakdownWidgets =
    visibleWidgets.has("channel-intelligence") ||
    visibleWidgets.has("pipeline-pulse");
  const toggleWidget = (id: DashboardWidgetId, checked: boolean) => {
    setVisibleWidgets((current) => {
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
        visible={visibleWidgets}
        description="Select the metrics and cards you want on your dashboard. Unchecked items are removed from the view."
        showAllLabel="Show all cards"
        onToggle={toggleWidget}
        onShowAll={() =>
          setVisibleWidgets(defaultSectionSelection(dashboardWidgetOptions))
        }
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
              {visibleWidgets.has("live-activity") &&
                (data.agents ? (
                  <Panel
                    title="Live operational activity"
                    eyebrow="Audited agent events"
                    action={`${data.agents.activity.length} in range`}
                  >
                    <ActivityFeed data={data.agents} compact />
                  </Panel>
                ) : (
                  <Panel
                    title="Live operational activity"
                    eyebrow="Audited agent events"
                  >
                    <Empty
                      title="Not available"
                      text="Your workspace role does not include agent activity access."
                    />
                  </Panel>
                ))}
              {visibleWidgets.has("attention-queue") &&
                (data.review ? (
                  <Panel
                    title="Attention queue"
                    eyebrow="Human judgment required"
                    action={`${data.review.total} open`}
                  >
                    {data.review.items.length ? (
                      <div className="compact-leads">
                        {data.review.items.slice(0, 5).map((lead) => (
                          <LeadRow
                            lead={lead}
                            key={lead.id}
                            onClick={() => onInspect(lead.id)}
                          />
                        ))}
                      </div>
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
                  eyebrow="Quality + useful volume"
                >
                  <Bars
                    rows={data.channels.channels.map((row) => ({
                      label: pretty(row.channel),
                      value: row.importance_index,
                      note: `${row.lead_volume} leads · ${row.average_score} avg score`,
                    }))}
                  />
                </Panel>
              )}
              {visibleWidgets.has("pipeline-pulse") && (
                <Panel
                  title="Pipeline pulse"
                  eyebrow="Active progression by stage"
                >
                  <Bars
                    rows={data.pipeline.stages.map((stage) => ({
                      label: stage.label,
                      value: stage.count,
                      note: stage.value ? money(stage.value) : undefined,
                    }))}
                  />
                </Panel>
              )}
            </section>
          )}
          <DashboardInsights
            channels={data.channels}
            revenue={data.revenue}
            visible={visibleWidgets}
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
          : String(animated);
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
  const [display, setDisplay] = useState(() =>
    window.matchMedia("(prefers-reduced-motion: reduce)").matches ? value : 0,
  );
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
