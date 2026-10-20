import type { ReactNode } from "react";
import type { AnalyticsBundle } from "../views/productShared";
import { Bars } from "./Charts";
import { Empty, Panel, money, pretty } from "../views/productShared";
import "./analyticsInsights.css";

const weekdays = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

export function AnalyticsInsights({
  data,
  visible,
}: {
  data: AnalyticsBundle;
  visible: ReadonlySet<string>;
}) {
  const extended = data.extended;
  const advanced = data.advanced;
  const gap = (key: string) => extended?.data_gaps.find((item) => item.key === key);
  const anyVisible = [
    "cohort-analysis",
    "forecast-commentary",
    "score-calibration",
    "sales-cycle",
    "lead-source-payback",
    "win-loss-reasons",
    "icp-fit-distribution",
    "response-heatmap",
    "deal-velocity",
    "competitive-mentions",
    "channel-mix-shift",
    "team-utilization",
    "quarterly-scorecard",
  ].some((id) => visible.has(id));
  if (!anyVisible) return null;

  return (
    <>
      {visible.has("cohort-analysis") && (
        <div className="analytics-panel-wide">
          <Panel title="Cohort Analysis" eyebrow="Weekly intake cohorts by campaign · six-month window">
            {advanced?.cohorts.length ? (
              <DataTable headers={["Week", "Campaign", "Leads", "Qualified", "Won", "Lost", "Win rate"]}>
                {advanced.cohorts.slice(0, 16).map((row, index) => (
                  <tr key={`${row.week}-${row.campaign}-${index}`}>
                    <td>{row.week}</td><td>{row.campaign}</td><td>{row.leads}</td>
                    <td>{row.qualified}</td><td>{row.won}</td><td>{row.lost}</td>
                    <td>{row.leads ? `${(row.won / row.leads * 100).toFixed(1)}%` : "—"}</td>
                  </tr>
                ))}
              </DataTable>
            ) : <Empty title="No cohort records" text="Cohorts appear as leads arrive with intake dates and recorded outcomes." />}
          </Panel>
        </div>
      )}

      {visible.has("forecast-commentary") && (
        <div className="analytics-panel-wide">
          <Panel title="Forecast Commentary Block" eyebrow={advanced?.summary_method ?? "Recorded metrics"}>
            {advanced ? (
              <div className="analytics-commentary">
                <p>{advanced.summary}</p>
                <small>{advanced.forecast.method} {advanced.forecast.scope}</small>
              </div>
            ) : <Empty title="Forecast commentary unavailable" text="Recorded pipeline and quota data are not available." />}
          </Panel>
        </div>
      )}

      {visible.has("score-calibration") && (
        <Panel title="Score Calibration Report" eyebrow="Observed win rate among closed leads by recorded score">
          {extended?.calibration.some((row) => row.closed > 0) ? (
            <Bars
              rows={extended.calibration.filter((row) => row.closed > 0).map((row) => ({
                label: row.score_band,
                value: row.win_rate ?? 0,
                note: `${row.won} wins / ${row.closed} closed`,
              }))}
              formatter={(value) => `${value}%`}
              scaleMaximum={100}
            />
          ) : <Empty title="No closed outcomes to calibrate" text="Calibration is withheld until outcomes are recorded." />}
        </Panel>
      )}

      {visible.has("sales-cycle") && (
          <Panel title="Sales Cycle Length by Segment" eyebrow="Median days from opportunity open (or lead receipt) to recorded close">
          {extended?.sales_cycle_by_segment.length ? (
            <Bars rows={extended.sales_cycle_by_segment.map((row) => ({
              label: row.segment,
              value: row.median_days,
              note: `${row.sample_size} closed · ${row.average_days}d average`,
            }))} formatter={(value) => `${value} days`} />
          ) : <Empty title="No closed-cycle data" text="A segment is shown once its leads have a recorded close date." />}
        </Panel>
      )}

      {visible.has("lead-source-payback") && (
        <div className="analytics-panel-wide">
          <Panel title="Lead Source Payback" eyebrow="Configured spend compared with intake-cohort results">
            <PaybackTable rows={advanced?.channels ?? []} />
          </Panel>
        </div>
      )}

      {visible.has("win-loss-reasons") && (
        <Panel title="Win/Loss Reason Analysis" eyebrow="Structured close reasons">
          <NotTracked reason={gap("loss_reasons")?.reason} />
        </Panel>
      )}

      {visible.has("icp-fit-distribution") && (
        <div className="analytics-panel-wide">
          <Panel title="ICP Fit Distribution" eyebrow="Recorded status per evaluated ICP criterion">
            {extended?.icp_fit_distribution.length ? (
              <DataTable headers={["Criterion", "Recorded fit statuses"]}>
                {extended.icp_fit_distribution.map((row) => (
                  <tr key={row.criterion}>
                    <td>{row.criterion}</td>
                    <td>{Object.entries(row.statuses).map(([status, count]) => `${pretty(status)}: ${count}`).join(" · ")}</td>
                  </tr>
                ))}
              </DataTable>
            ) : <Empty title="No ICP evaluations recorded" text="The report uses saved analysis results and does not infer fit from missing fields." />}
          </Panel>
        </div>
      )}

      {visible.has("response-heatmap") && (
        <div className="analytics-panel-wide">
          <Panel title="Response-Pattern Heatmap" eyebrow="Recorded Gmail activity by weekday and UTC hour">
            <ResponseHeatmap rows={extended?.response_heatmap ?? []} />
          </Panel>
        </div>
      )}

      {visible.has("deal-velocity") && (
        <Panel title="Deal Velocity by Stage" eyebrow="Median time between recorded pipeline updates">
          {extended?.stage_velocity.length ? (
            <Bars rows={extended.stage_velocity.map((row) => ({
              label: row.transition,
              value: row.median_hours,
              note: `${row.sample_size} observed`,
            }))} formatter={(value) => `${value} hr`} />
          ) : <Empty title="No stage timing history" text="Record lead progress to build a stage-by-stage velocity report." />}
        </Panel>
      )}

      {visible.has("competitive-mentions") && (
        <Panel title="Competitive Mention Tracker" eyebrow="Extracted from recorded lead analysis">
          {extended?.competitive_mentions.length ? (
            <Bars rows={extended.competitive_mentions.map((row) => ({
              label: row.mention,
              value: row.count,
              note: `${row.count} lead${row.count === 1 ? "" : "s"}`,
            }))} formatter={(value) => `${value}`} />
          ) : <Empty title="No competitor mentions found" text="Only explicit saved analysis values are counted." />}
        </Panel>
      )}

      {visible.has("channel-mix-shift") && (
        <div className="analytics-panel-wide">
          <Panel title="Channel Mix Shift" eyebrow="Latest intake month compared with the previous month">
            <ChannelMixTable rows={extended?.channel_mix ?? []} />
          </Panel>
        </div>
      )}

      {visible.has("team-utilization") && (
        <div className="analytics-panel-wide">
          <Panel title="Team Utilization Trend" eyebrow="Monthly lead assignment volume; not a capacity estimate">
            {extended?.team_utilization_trend.length ? (
              <DataTable headers={["Month", "Representative", "Assignments"]}>
                {extended.team_utilization_trend.slice(-24).map((row, index) => (
                  <tr key={`${row.month}-${row.rep}-${index}`}><td>{row.month}</td><td>{row.rep}</td><td>{row.assignments}</td></tr>
                ))}
              </DataTable>
            ) : <Empty title="No assignment history" text="Assignment records will appear as leads are routed to workspace representatives." />}
          </Panel>
        </div>
      )}

      {visible.has("quarterly-scorecard") && (
        <div className="analytics-panel-wide">
          <Panel title="Quarterly Scorecard" eyebrow="Quarter-to-date intake cohort vs the same elapsed period last quarter">
            {extended?.quarterly_scorecard.length ? (
              <DataTable headers={["Metric", "Current quarter", "Prior comparable period", "Change"]}>
                {extended.quarterly_scorecard.map((row) => (
                  <tr key={row.metric}>
                    <td>{row.metric}</td><td>{formatScorecard(row.metric, row.current)}</td>
                    <td>{formatScorecard(row.metric, row.previous)}</td>
                    <td>{formatDelta(row.current, row.previous)}</td>
                  </tr>
                ))}
              </DataTable>
            ) : <Empty title="Quarterly scorecard unavailable" text="No lead data is recorded for the current or previous quarter-to-date window." />}
          </Panel>
        </div>
      )}
    </>
  );
}

function DataTable({
  headers,
  children,
}: {
  headers: string[];
  children: ReactNode;
}) {
  return (
    <div className="analytics-data-table-wrap" role="region" aria-label="Analytics details table" tabIndex={0}>
      <table className="analytics-data-table">
        <thead><tr>{headers.map((header) => <th scope="col" key={header}>{header}</th>)}</tr></thead>
        <tbody>{children}</tbody>
      </table>
    </div>
  );
}

function PaybackTable({ rows }: { rows: NonNullable<AnalyticsBundle["advanced"]>["channels"] }) {
  if (!rows.length) {
    return <Empty title="No cost-aware channel data" text="Configure channel costs to compare spend, qualified leads, revenue and ROI." />;
  }
  return (
    <DataTable headers={["Month", "Source", "Leads", "Qualified", "Spend", "Closed revenue", "Cost / qualified", "ROI"]}>
      {rows.slice(0, 24).map((row, index) => (
        <tr key={`${row.month}-${row.source}-${index}`}>
          <td>{row.month}</td><td>{pretty(row.source)}</td><td>{row.leads}</td><td>{row.qualified}</td>
          <td>{row.cost_usd == null ? "Not configured" : money(row.cost_usd)}</td><td>{money(row.closed_usd)}</td>
          <td>{row.cost_per_qualified == null ? "—" : money(row.cost_per_qualified)}</td>
          <td>{row.roi_percent == null ? "—" : `${row.roi_percent}%`}</td>
        </tr>
      ))}
      <tr className="analytics-table-note"><td colSpan={8}>{rows[0]?.method}</td></tr>
    </DataTable>
  );
}

function NotTracked({ reason }: { reason?: string }) {
  return <div className="analytics-not-tracked" role="status"><b>Not tracked yet</b><span>{reason ?? "This measure needs additional recorded data."}</span></div>;
}

function ResponseHeatmap({ rows }: { rows: NonNullable<AnalyticsBundle["extended"]>["response_heatmap"] }) {
  const counts = new Map(rows.map((row) => [`${row.weekday}-${row.hour}`, row]));
  const max = Math.max(1, ...rows.map((row) => row.inbound + row.outbound));
  if (!rows.length) return <Empty title="No message timing data" text="Sync lead-linked Gmail activity to see message patterns." />;
  return (
    <div className="response-heatmap-scroll">
      <table className="response-heatmap">
        <thead><tr><th scope="col">Day</th>{Array.from({ length: 24 }, (_, hour) => <th scope="col" key={hour}>{hour}</th>)}</tr></thead>
        <tbody>{weekdays.map((day, weekday) => <tr key={day}><th scope="row">{day}</th>{Array.from({ length: 24 }, (_, hour) => {
          const cell = counts.get(`${weekday}-${hour}`);
          const total = (cell?.inbound ?? 0) + (cell?.outbound ?? 0);
          return <td key={hour} title={`${day} ${String(hour).padStart(2, "0")}:00 UTC · ${cell?.inbound ?? 0} inbound · ${cell?.outbound ?? 0} outbound`} aria-label={`${day} at ${hour}:00 UTC, ${total} messages`}>
            <i style={{ opacity: total ? 0.2 + 0.8 * total / max : 0.04 }} />
          </td>;
        })}</tr>)}</tbody>
      </table>
      <div className="heatmap-legend"><span>Cell intensity shows total message volume.</span><small>Hover a cell for inbound and outbound counts.</small></div>
    </div>
  );
}

function ChannelMixTable({ rows }: { rows: NonNullable<AnalyticsBundle["extended"]>["channel_mix"] }) {
  const months = [...new Set(rows.map((row) => row.month))].sort();
  if (!months.length) return <Empty title="No channel mix history" text="Channel mix will appear as lead sources record intake over time." />;
  const current = months.at(-1)!;
  const previous = months.at(-2);
  const sources = [...new Set(rows.map((row) => row.source))];
  return (
    <DataTable headers={["Source", current, previous ?? "Prior month", "Change"]}>
      {sources.map((source) => {
        const currentCount = rows.find((row) => row.source === source && row.month === current)?.leads ?? 0;
        const previousCount = previous ? rows.find((row) => row.source === source && row.month === previous)?.leads ?? 0 : 0;
        return <tr key={source}><td>{pretty(source)}</td><td>{currentCount}</td><td>{previous ? previousCount : "—"}</td><td>{previous ? formatDelta(currentCount, previousCount) : "No baseline"}</td></tr>;
      })}
    </DataTable>
  );
}

function formatScorecard(metric: string, value: number | null) {
  if (value == null) return "—";
  return metric === "Qualification Rate" ? `${value}%` : metric === "Revenue" ? money(value) : value.toLocaleString();
}

function formatDelta(current: number | null, previous: number | null) {
  if (current == null || previous == null) return "—";
  if (previous === 0) return current === 0 ? "0%" : "New";
  const change = (current - previous) / previous * 100;
  return `${change > 0 ? "+" : ""}${change.toFixed(1)}%`;
}
