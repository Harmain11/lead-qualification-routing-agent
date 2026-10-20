import type { ChannelAnalytics, RevenueAnalytics } from "../types";
import type { ExtendedAnalytics } from "../types";
import type { ReactNode } from "react";
import { Bars, TimedAreaChart } from "./Charts";
import "./dashboardInsights.css";

type DashboardInsightsProps = {
  channels: ChannelAnalytics | null;
  revenue: RevenueAnalytics | null;
  extended: ExtendedAnalytics | null;
  visible: ReadonlySet<string>;
};

const readable = (value: string) => value.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());

function currencyFormatter(currency: string) {
  try {
    return new Intl.NumberFormat(undefined, { style: "currency", currency, maximumFractionDigits: 2 });
  } catch {
    return { format: (value: number) => `${currency} ${new Intl.NumberFormat(undefined, { maximumFractionDigits: 2 }).format(value)}` };
  }
}

function InsightPanel({ eyebrow, title, detail, children, kind = "source" }: {
  kind?: "source" | "revenue";
  eyebrow: string;
  title: string;
  detail: string;
  children: ReactNode;
}) {
  return <section className={`dashboard-insight-panel insight-${kind}`}>
    <header><div><span>{eyebrow}</span><h2>{title}</h2></div><p>{detail}</p></header>
    {children}
  </section>;
}

export function DashboardInsights({ channels, revenue, extended, visible }: DashboardInsightsProps) {
  const activePanels = ["revenue-trend", "source-qualification", "source-quality"].filter((id) => visible.has(id));
  if (activePanels.length === 0) return null;

  const currency = revenue ? currencyFormatter(revenue.currency) : null;
  const revenueStatus = revenue ? readable(revenue.data_status) : "Unavailable";
  const sources = (channels?.channels ?? [])
    .filter((source) => Number.isFinite(source.lead_volume) && source.lead_volume > 0);
  const qualificationRows = [...sources]
    .filter((source) => Number.isFinite(source.qualification_rate))
    .sort((a, b) => b.qualification_rate - a.qualification_rate || b.lead_volume - a.lead_volume)
    .map((source) => ({
      label: readable(source.channel),
      value: source.qualification_rate,
      note: `${source.qualified_leads !== undefined ? `${source.qualified_leads} qualified / ` : ""}${source.lead_volume.toLocaleString()} ${source.lead_volume === 1 ? "lead" : "leads"}`,
    }));
  const qualityRows = [...sources]
    .sort((a, b) => (b.closed_won_count ?? 0) - (a.closed_won_count ?? 0)
      || b.closed_won_conversion_rate - a.closed_won_conversion_rate
      || b.lead_volume - a.lead_volume)
    .map((source) => ({
      label: readable(source.channel),
      value: source.closed_won_count ?? 0,
      note: `${source.closed_won_conversion_rate}% win rate · ${source.lead_volume.toLocaleString()} ${source.lead_volume === 1 ? "lead" : "leads"}`,
    }));
  const qualificationFormat = (value: number) => `${new Intl.NumberFormat(undefined, { maximumFractionDigits: 1 }).format(value)}%`;

  return <div className="dashboard-insights">
    {visible.has("revenue-trend") && <InsightPanel
      kind="revenue"
      eyebrow="Closed revenue · over time"
      title="Revenue trend"
      detail={revenue ? `${revenueStatus} data · ${revenue.currency}` : "Revenue data has not been provided"}
    >
      {!revenue || revenue.data_status === "unavailable" ? <div className="insight-empty" role="status">
        <b>{revenue ? "Revenue is unavailable for this period" : "Revenue data is unavailable"}</b>
        <span>{revenue ? "No revenue values are plotted while the source is marked unavailable." : "Connect analytics data to see recorded revenue over time."}</span>
        {revenue && <small>Data status: unavailable</small>}
      </div> : revenue.over_time.length === 0 ? <div className="insight-empty" role="status">
        <b>No revenue points in this period</b><span>The selected range contains no recorded time-series values.</span><small>Data status: {revenueStatus.toLowerCase()}</small>
      </div> : <div className="insight-chart">
        <div className={`insight-provenance provenance-${revenue.data_status}`}><span>Revenue data</span><b>{revenueStatus}</b></div>
        <TimedAreaChart
          points={revenue.over_time.map((point) => ({ timestamp: point.period, value: point.value }))}
          formatValue={(value) => currency!.format(value)}
          ariaLabel={`Revenue trend in ${revenue.currency}; ${revenueStatus.toLowerCase()} data`}
        />
        <div className="insight-conversion-breakdown">
          <b>Recorded conversion type</b>
          {extended?.revenue_by_conversion_type.length ? (
            <Bars rows={extended.revenue_by_conversion_type.map((row) => ({
              label: readable(row.type),
              value: row.revenue,
            }))} formatter={(value) => currency!.format(value)} />
          ) : <span>No project/sale revenue breakdown recorded for this period.</span>}
          <small>Project and Sale are the stored labels; they do not identify new business versus expansion or renewal.</small>
        </div>
      </div>}
    </InsightPanel>}
    {visible.has("source-qualification") && <InsightPanel
      eyebrow="Lead source · conversion signal"
      title="Qualification rate by source"
       detail="Share of leads marked Hot, Warm or Needs Qualifying during this period. Scale: 0–100%."
    >
      {!channels ? <div className="insight-empty" role="status"><b>Source analytics unavailable</b><span>Qualification rates will appear when channel analytics are available.</span></div>
        : qualificationRows.length === 0 ? <div className="insight-empty" role="status"><b>No source has recorded lead volume</b><span>Sources without leads are excluded rather than shown as zero-rate results.</span></div>
           : <><Bars rows={qualificationRows} formatter={qualificationFormat} scaleMaximum={100} />
             {qualificationRows.every((row) => row.value === 0) && <p className="panel-copy">No leads in these sources currently meet the qualification criteria. A 0% rate is not a missing score.</p>}</>}
    </InsightPanel>}
    {visible.has("source-quality") && <InsightPanel
      eyebrow="Lead source · assessment signal"
      title="Lead quality by source"
       detail="Sources ranked by recorded closed-won deals, with win rate and lead volume for context."
    >
      {!channels ? <div className="insight-empty" role="status"><b>Source analytics unavailable</b><span>Lead scores will appear when channel analytics are available.</span></div>
        : qualityRows.length === 0 ? <div className="insight-empty" role="status"><b>No source has recorded lead volume</b><span>Sources without leads are excluded from this ranking.</span></div>
           : <Bars rows={qualityRows} formatter={(value) => `${value} won`} />}
    </InsightPanel>}
  </div>;
}
