import { useEffect, useState } from "react";
import { Bars, TrendChart } from "../components/Charts";
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
] as const;

type AnalyticsPanelId = (typeof analyticsPanelOptions)[number]["id"];

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
    () => loadSectionSelection(preferenceKey, analyticsPanelOptions),
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
              <>
                Quality, velocity
                <br />
                <em>and commercial value.</em>
              </>
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
                eyebrow="Volume × quality × conversion"
              >
                <div className="channel-table">
                  <div className="channel-head">
                    <span>Channel</span>
                    <span>Volume</span>
                    <span>Qual.</span>
                    <span>SQL</span>
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
                      <span>{row.closed_won_conversion_rate}%</span>
                      <span>{money(row.revenue)}</span>
                      <em>{row.importance_index}</em>
                    </div>
                  ))}
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
              eyebrow={`${pretty(data.revenue.data_status)} data`}
            >
              <Bars
                rows={data.revenue.by_channel.map((row) => ({
                  label: pretty(row.label),
                  value: row.value,
                }))}
                formatter={money}
              />
            </Panel>
          )}
          {displayedPanels.has("revenue-by-service") && (
            <Panel
              title="Revenue by service"
              eyebrow="Closed / won attribution"
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
              <TeamPerformancePanel data={data.reps} />
            </div>
          )}
          {displayedPanels.has("revenue-trajectory") && (
            <Panel title="Revenue trajectory" eyebrow="Closed / won over time">
              <TrendChart
                values={data.revenue.over_time.map((point) => point.value)}
                labels={data.revenue.over_time.map((point) => point.period)}
              />
            </Panel>
          )}
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
