import { useEffect, useState } from "react";
import { Bars, Donut, TrendChart } from "../components/Charts";
import { AnalyticsInsights } from "../components/AnalyticsInsights";
import { TeamPerformancePanel } from "../components/TeamPerformancePanel";
import type { AnalyticsBundle } from "./productShared";
import {
  defaultSectionSelection,
  Empty,
  loadSectionSelection,
  metricValue,
  money,
  Panel,
  pretty,
  SectionVisibilityPicker,
  Unavailable,
} from "./productShared";

const analyticsPanelOptions = [
  { id: "channel-performance", label: "Channel performance" },
  { id: "lead-trajectory", label: "Lead trajectory" },
  { id: "revenue-by-channel", label: "Revenue by channel" },
  { id: "revenue-by-service", label: "Revenue by service" },
  { id: "representatives", label: "Team member performance" },
  { id: "revenue-trajectory", label: "Revenue trajectory" },
  { id: "qualification-mix", label: "Qualification mix" },
  { id: "revenue-by-representative", label: "Revenue by representative" },
  { id: "cohort-analysis", label: "Cohort Analysis" },
  { id: "forecast-commentary", label: "Forecast Commentary Block" },
  { id: "score-calibration", label: "Score Calibration Report" },
  { id: "sales-cycle", label: "Sales Cycle Length by Segment" },
  { id: "lead-source-payback", label: "Lead Source Payback" },
  { id: "win-loss-reasons", label: "Win/Loss Reason Analysis" },
  { id: "icp-fit-distribution", label: "ICP Fit Distribution" },
  { id: "response-heatmap", label: "Response-Pattern Heatmap" },
  { id: "deal-velocity", label: "Deal Velocity by Stage" },
  { id: "competitive-mentions", label: "Competitive Mention Tracker" },
  { id: "channel-mix-shift", label: "Channel Mix Shift" },
  { id: "team-utilization", label: "Team Utilization Trend" },
  { id: "quarterly-scorecard", label: "Quarterly Scorecard" },
] as const;

type AnalyticsPanelId = (typeof analyticsPanelOptions)[number]["id"];
const newlyAddedPanelIds = [
  "cohort-analysis", "forecast-commentary", "score-calibration", "sales-cycle",
  "lead-source-payback", "win-loss-reasons", "icp-fit-distribution",
  "response-heatmap", "deal-velocity", "competitive-mentions",
  "channel-mix-shift", "team-utilization", "quarterly-scorecard",
] as const;

function loadAnalyticsPanelSelection(storageKey: string) {
  const selected = loadSectionSelection(storageKey, analyticsPanelOptions);
  try {
    const saved = JSON.parse(window.localStorage.getItem(storageKey) || "null");
    const marker = `${storageKey}:expanded-v2`;
    if (Array.isArray(saved) && saved.length && !window.localStorage.getItem(marker)) {
      newlyAddedPanelIds.forEach((id) => selected.add(id));
    }
    window.localStorage.setItem(marker, "seen");
  } catch {
    // Analytics preferences remain optional when browser storage is unavailable.
  }
  return selected;
}

export function AnalyticsView({
  data,
  userId = "local",
  selectedSection = "",
}: {
  data: AnalyticsBundle | null;
  userId?: string;
  selectedSection?: string;
}) {
  const preferenceKey = `corral.analytics.panels.v1:${userId}`;
  const [preferredPanels, setVisiblePanels] = useState<Set<AnalyticsPanelId>>(
    () => loadAnalyticsPanelSelection(preferenceKey),
  );
  const displayedPanels = selectedSection
    ? new Set<AnalyticsPanelId>([selectedSection as AnalyticsPanelId])
    : preferredPanels;
  const visiblePanels = displayedPanels;
  useEffect(() => {
    try {
      window.localStorage.setItem(
        preferenceKey,
        JSON.stringify([...preferredPanels]),
      );
    } catch {
      // Analytics preferences are optional when browser storage is unavailable.
    }
  }, [preferenceKey, preferredPanels]);

  if (
    !data?.dashboard ||
    !data.channels ||
    !data.pipeline ||
    !data.series ||
    !data.revenue ||
    !data.reps
  )
    return <Unavailable />;
  const provenance =
    data.revenue.data_status === "demo"
      ? "Illustrative values—not production revenue"
      : data.revenue.data_status === "crm"
        ? "CRM-derived outcomes"
        : data.revenue.data_status === "manual"
          ? "Outcomes recorded manually in Corral"
          : data.revenue.data_status === "mixed"
            ? "Outcomes from multiple sources, including manual records"
            : "No revenue facts available";
  const totalLeads = metricValue(data.dashboard, "total_leads");
  const togglePanel = (id: AnalyticsPanelId, checked: boolean) => {
    setVisiblePanels((current) => {
      const next = new Set(current);
      if (checked) next.add(id);
      else next.delete(id);
      return next;
    });
  };
  return (
    <div className={`page-view${selectedSection ? " focused-page" : ""}`}>
      <header className="page-hero compact-hero">
        <div>
          <span className="eyebrow">Workspace analytics</span>
          <h1>
            {selectedSection ? (
              analyticsPanelOptions.find((item) => item.id === selectedSection)
                ?.label
            ) : (
              "Analytics"
            )}
          </h1>
          <p>
            Time-bounded metrics generated server-side from workspace-owned
            records.
          </p>
        </div>
        <div className={`data-provenance ${data.revenue.data_status}`}>
          <span>Revenue provenance</span>
          <b>{pretty(data.revenue.data_status)}</b>
          <small>{provenance}</small>
        </div>
      </header>
      {totalLeads === 0 && (
        <section className="panel workspace-empty">
          <Empty
            title="No analytics yet"
            text="Metrics remain at zero until real lead activity is recorded."
          />
        </section>
      )}
      {!selectedSection && (
        <SectionVisibilityPicker
          options={analyticsPanelOptions}
          visible={visiblePanels}
          description="Select the analytics sections you want. Only selected sections appear below."
          showAllLabel="Show all sections"
          onToggle={togglePanel}
          onShowAll={() =>
            setVisiblePanels(defaultSectionSelection(analyticsPanelOptions))
          }
        />
      )}
      {displayedPanels.size > 0 ? (
        <section
          className={`analytics-grid analytics-motion${displayedPanels.size === 1 ? " analytics-grid-single" : ""}`}
          data-tour="analytics"
        >
          {visiblePanels.has("channel-performance") && (
            <div className="analytics-panel-wide">
              <Panel
                title="Channel performance"
                eyebrow="Channel quality, conversion and booking"
              >
                <div
                  className="channel-table-wrap"
                  role="region"
                  aria-label="Channel performance; scroll horizontally to view all columns"
                  tabIndex={0}
                >
                  <div className="channel-table">
                    <div className="channel-head">
                      <span>Channel</span>
                      <span>Volume</span>
                      <span>Qualified</span>
                      <span>SQL</span>
                      <span>Booked</span>
                      <span>Booked rate</span>
                      <span>Won</span>
                      <span>Revenue</span>
                      <span>Importance</span>
                    </div>
                    {data.channels.channels.map((row) => (
                      <div key={row.channel}>
                        <b>{pretty(row.channel)}</b>
                        <span>
                          {row.lead_volume} · {row.share_percent}%
                        </span>
                        <span>{row.qualification_rate}%</span>
                        <span>{row.sql_conversion_rate}%</span>
                        <span>{row.booked_leads ?? 0}</span>
                        <span>{row.booked_rate ?? 0}%</span>
                        <span>{row.closed_won_conversion_rate}%</span>
                        <span>{money(row.revenue)}</span>
                        <em>{row.importance_index}</em>
                      </div>
                    ))}
                  </div>
                </div>
              </Panel>
            </div>
          )}
          {displayedPanels.has("lead-trajectory") && (
            <Panel title="Lead trajectory" eyebrow="Volume by period">
              <TrendChart
                values={data.series.points.map((point) => point.leads)}
                labels={data.series.points.map((point) => point.period)}
              />
            </Panel>
          )}
          {displayedPanels.has("revenue-by-channel") && (
            <Panel
              title="Revenue by channel"
              eyebrow={data.advanced ? "Monthly intake-cohort revenue · spend and ROI" : `${pretty(data.revenue.data_status)} data`}
            >
              {data.advanced?.channels.length ? (
                <Bars
                  rows={data.advanced.channels.slice(0, 12).map((row) => ({
                    label: `${pretty(row.source)} · ${row.month}`,
                    value: row.closed_usd,
                    note: `Spend ${row.cost_usd == null ? "not configured" : money(row.cost_usd)} · ${row.roi_percent == null ? "ROI —" : `${row.roi_percent}% ROI`}`,
                  }))}
                  formatter={money}
                />
              ) : (
                <>
                  <Bars
                    rows={data.revenue.by_channel.map((row) => ({
                      label: pretty(row.label),
                      value: row.value,
                    }))}
                    formatter={money}
                  />
                  <p className="panel-copy">Channel cost and ROI appear after costs are configured.</p>
                </>
              )}
            </Panel>
          )}
          {displayedPanels.has("revenue-by-service") && (
            <Panel
              title="Revenue by service"
              eyebrow="Closed-won attribution"
            >
              <Bars
                rows={data.revenue.by_service.map((row) => ({
                  label: row.label,
                  value: row.value,
                }))}
                formatter={money}
              />
            </Panel>
          )}
          {displayedPanels.has("representatives") && (
            <div
              className="analytics-panel-wide"
              data-navigation-section="representatives"
              tabIndex={-1}
            >
              <TeamPerformancePanel
                data={data.reps}
                fairness={data.advanced?.members}
                fairnessMethod={data.advanced?.fairness_method}
              />
            </div>
          )}
          {displayedPanels.has("revenue-trajectory") && (
            <Panel title="Revenue trajectory" eyebrow="Closed-won over time">
              <TrendChart
                values={data.revenue.over_time.map((point) => point.value)}
                labels={data.revenue.over_time.map((point) => point.period)}
              />
            </Panel>
          )}
          {displayedPanels.has("qualification-mix") && (
            <Panel title="Qualification mix" eyebrow="Decision bands across leads">
              <Donut
                rows={data.pipeline.bands.map((row) => ({
                  label: pretty(row.band),
                  value: row.count,
                }))}
              />
            </Panel>
          )}
          {displayedPanels.has("revenue-by-representative") && (
            <Panel title="Revenue by representative" eyebrow="Closed-won attribution">
              <Bars
                rows={data.revenue.by_rep.map((row) => ({
                  label: row.label,
                  value: row.value,
                }))}
                formatter={money}
              />
            </Panel>
          )}
          <AnalyticsInsights data={data} visible={visiblePanels} />
        </section>
      ) : (
        <section className="section-picker-empty panel">
          <h2>No sections selected</h2>
          <p>Choose at least one analytics section above to display it here.</p>
        </section>
      )}
    </div>
  );
}
