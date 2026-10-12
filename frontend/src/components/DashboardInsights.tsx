import type { ChannelAnalytics, RevenueAnalytics } from "../types";
import type { ReactNode } from "react";
import { Bars, TimedAreaChart } from "./Charts";
import "./dashboardInsights.css";

type DashboardInsightsProps = {
  channels: ChannelAnalytics | null;
  revenue: RevenueAnalytics | null;
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

export function DashboardInsights({ channels, revenue, visible }: DashboardInsightsProps) {
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
    .filter((source) => Number.isFinite(source.average_score) && source.scored_leads !== 0)
    .sort((a, b) => b.average_score - a.average_score || b.lead_volume - a.lead_volume)
    .map((source) => ({
      label: readable(source.channel),
      value: source.average_score,
      note: source.scored_leads !== undefined
        ? `${source.scored_leads} scored / ${source.lead_volume.toLocaleString()} ${source.lead_volume === 1 ? "lead" : "leads"}`
        : `${source.lead_volume.toLocaleString()} ${source.lead_volume === 1 ? "lead" : "leads"}`,
    }));
  const qualificationFormat = (value: number) => `${new Intl.NumberFormat(undefined, { maximumFractionDigits: 1 }).format(value)}%`;
  const scoreFormat = (value: number) => `${new Intl.NumberFormat(undefined, { maximumFractionDigits: 1 }).format(value)}/100`;

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
      </div>}
    </InsightPanel>}
    {visible.has("source-qualification") && <InsightPanel
      eyebrow="Lead source · conversion signal"
      title="Qualification rate by source"
       detail="Hot, Warm or Needs Qualifying leads ÷ total leads in this period × 100. Fixed 0–100% scale."
    >
      {!channels ? <div className="insight-empty" role="status"><b>Source analytics unavailable</b><span>Qualification rates will appear when channel analytics are available.</span></div>
        : qualificationRows.length === 0 ? <div className="insight-empty" role="status"><b>No source has recorded lead volume</b><span>Sources without leads are excluded rather than shown as zero-rate results.</span></div>
           : <><Bars rows={qualificationRows} formatter={qualificationFormat} scaleMaximum={100} />
             {qualificationRows.every((row) => row.value === 0) && <p className="panel-copy">No leads in these sources currently meet the qualification criteria. A 0% rate is not a missing score.</p>}</>}
    </InsightPanel>}
    {visible.has("source-quality") && <InsightPanel
      eyebrow="Lead source · assessment signal"
      title="Lead quality by source"
       detail="Average recorded qualification score out of 100. Bars use a fixed 0–100 scale."
    >
      {!channels ? <div className="insight-empty" role="status"><b>Source analytics unavailable</b><span>Lead scores will appear when channel analytics are available.</span></div>
        : qualityRows.length === 0 ? <div className="insight-empty" role="status"><b>No scored leads with recorded volume</b><span>Sources without leads are excluded from this ranking.</span></div>
           : <Bars rows={qualityRows} formatter={scoreFormat} scaleMaximum={100} />}
    </InsightPanel>}
  </div>;
}
