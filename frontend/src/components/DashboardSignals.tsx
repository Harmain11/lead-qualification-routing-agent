import type { AdvancedAnalytics, RepAnalytics, AgentActivity } from "../types";
import { Bars } from "./Charts";
import {
  Empty,
  LeadRow,
  Panel,
  money,
  pretty,
} from "../views/productShared";
import type { AnalyticsBundle } from "../views/productShared";
import "./dashboardSignals.css";

type DashboardWidgetSet = ReadonlySet<string>;

export function DashboardSignals({
  data,
  visible,
  onInspect,
  settingsPrefix,
}: {
  data: AnalyticsBundle;
  visible: DashboardWidgetSet;
  onInspect: (id: string) => void;
  settingsPrefix: string;
}) {
  const active = [
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
  ].some((id) => visible.has(id));
  if (!active) return null;

  const extended = data.extended;
  const advanced = data.advanced;
  const pipelineOrigins = data.dashboard?.active_pipeline_by_origin ?? [];
  const gap = (key: string) => extended?.data_gaps.find((item) => item.key === key);

  return (
    <section className="dashboard-grid dashboard-signals">
      {visible.has("active-pipeline-origin") && (
        <Panel title="Active pipeline value" eyebrow="Source intake vs manually added in Corral">
          {pipelineOrigins.length ? (
            <Bars
              settingsId={`${settingsPrefix}:active-pipeline-origin`}
              rows={pipelineOrigins.map((row) => ({
                label: row.origin === "manual" ? "Manually added in Corral" : "Source intake",
                value: row.value,
              }))}
              formatter={money}
            />
          ) : (
            <Empty title="No active pipeline value" text="Recorded opportunity values are required for this comparison." />
          )}
        </Panel>
      )}

      {visible.has("speed-to-qualify") && (
        <Panel title="Speed to Qualify" eyebrow="Inquiry received to score assigned">
          {extended?.speed_to_qualify.average_minutes != null ? (
            <SignalMetric
              value={`${extended.speed_to_qualify.average_minutes.toLocaleString()} min`}
              detail={`Median ${extended.speed_to_qualify.median_minutes ?? "—"} min · ${extended.speed_to_qualify.sample_size} scored leads`}
            />
          ) : (
            <Empty title="No qualification timing yet" text="Timing appears after leads receive a recorded qualification score." />
          )}
        </Panel>
      )}

      {visible.has("sla-compliance") && (
        <Panel title="SLA Compliance" eyebrow="Recorded first-action evidence vs routing deadline">
          {!extended || extended.sla.configured === 0 ? (
            <Empty title="SLA target not configured" text="No leads in this period have a routing SLA deadline." />
          ) : (
            <SignalMetric
              value={extended.sla.rate == null ? "Awaiting responses" : `${extended.sla.rate}%`}
              detail={`${extended.sla.compliant} on time · ${extended.sla.breached} breached · ${extended.sla.pending} pending · ${extended.sla.unverified} closed without response evidence`}
            />
          )}
        </Panel>
      )}

      {visible.has("override-rate") && (
        <Panel title="Override Rate" eyebrow="Human score changes">
          <NotTracked gap={gap("override_rate")} />
        </Panel>
      )}

      {visible.has("qualified-to-booked") && (
        <Panel title="Qualified → Booked Rate" eyebrow="Recorded qualified leads with a scheduled meeting">
          {extended?.qualified_to_booked.rate != null ? (
            <SignalMetric
              value={`${extended.qualified_to_booked.rate}%`}
              detail={`${extended.qualified_to_booked.booked} booked from ${extended.qualified_to_booked.qualified} qualified leads`}
            />
          ) : (
            <Empty title="No qualified leads in this period" text="The rate is withheld when the denominator is zero." />
          )}
        </Panel>
      )}

      {visible.has("funnel-bottleneck") && (
        <Panel title="Funnel Bottleneck View" eyebrow="Median time between recorded stages">
          {extended?.stage_velocity.length ? (
            <Bars
              settingsId={`${settingsPrefix}:funnel-bottleneck`}
              rows={extended.stage_velocity.slice(0, 5).map((row) => ({
                label: row.transition,
                value: row.median_hours,
                note: `${row.sample_size} observed transition${row.sample_size === 1 ? "" : "s"}`,
              }))}
              metricOptions={[
                {
                  id: "median-hours",
                  label: "Median hours",
                  rows: extended.stage_velocity.slice(0, 5).map((row) => ({
                    label: row.transition,
                    value: row.median_hours,
                    note: `${row.sample_size} observed`,
                  })),
                  formatter: (value) => `${value} hr`,
                },
                {
                  id: "sample-size",
                  label: "Observed transitions",
                  rows: extended.stage_velocity.slice(0, 5).map((row) => ({
                    label: row.transition,
                    value: row.sample_size,
                  })),
                },
              ]}
              formatter={(value) => `${value} hr`}
            />
          ) : (
            <Empty title="Not enough stage history" text="Stage timing needs manually recorded pipeline progress events." />
          )}
        </Panel>
      )}

      {visible.has("rep-capacity") && (
        <Panel title="Rep Capacity & Load" eyebrow="Open leads alongside assigned-cohort close rate">
          <RepLoad reps={data.reps} settingsId={`${settingsPrefix}:rep-capacity`} />
        </Panel>
      )}

      {visible.has("pipeline-coverage") && (
        <Panel title="Pipeline Coverage vs Target" eyebrow="Recorded USD open pipeline against configured monthly quota">
          <ForecastCoverage advanced={advanced} />
        </Panel>
      )}

      {visible.has("decay-momentum") && (
        <Panel title="Decay / Momentum Alerts" eyebrow="Open leads with no recorded activity for 7+ days">
          {extended?.stale_leads.length ? (
            <div className="dashboard-signal-list">
              {extended.stale_leads.slice(0, 5).map((lead) => (
                <div className="dashboard-signal-row" key={lead.id}>
                  <LeadRow lead={lead} onClick={() => onInspect(lead.id)} />
                  <small>{lead.inactive_days}d inactive</small>
                </div>
              ))}
            </div>
          ) : (
            <Empty title="No stale open leads" text="This card flags only leads with recorded inactivity, not historical value drops." />
          )}
        </Panel>
      )}

      {visible.has("forecast-narrative") && (
        <Panel title="Forecast Narrative" eyebrow={advanced?.summary_method ?? "Recorded metrics"}>
          {advanced ? (
            <p className="dashboard-forecast-copy">{advanced.summary}</p>
          ) : (
            <Empty title="Forecast unavailable" text="Forecast commentary requires analytics access and recorded pipeline data." />
          )}
        </Panel>
      )}

      {visible.has("first-response") && (
        <Panel title="First-Response Leaderboard" eyebrow="Individual rep attribution">
          <NotTracked gap={gap("rep_first_response")} />
        </Panel>
      )}

      {visible.has("escalation-alerts") && (
        <Panel
          title="Escalation Alerts"
          eyebrow="Open leads past their configured SLA target"
          action={`${extended?.escalations.length ?? 0} overdue`}
        >
          {extended?.escalations.length ? (
            <div className="dashboard-signal-list">
              {extended.escalations.slice(0, 5).map((lead) => (
                <div className="dashboard-signal-row" key={lead.id}>
                  <LeadRow lead={lead} onClick={() => onInspect(lead.id)} />
                  <small>{lead.late_response ? `First action was ${lead.hours_overdue}h late` : `${lead.hours_overdue}h overdue`}</small>
                </div>
              ))}
            </div>
          ) : (
            <Empty title="No breached SLAs" text="Escalations appear only when a configured deadline has passed without recorded first action." />
          )}
        </Panel>
      )}

      {visible.has("agent-activity") && (
        <Panel title="Agent Activity Snapshot" eyebrow="Latest recorded AI and tool activity">
          <AgentFeed agents={data.agents} />
        </Panel>
      )}
    </section>
  );
}

function SignalMetric({ value, detail }: { value: string; detail: string }) {
  return <div className="dashboard-signal-metric"><strong>{value}</strong><span>{detail}</span></div>;
}

function NotTracked({ gap }: { gap?: { reason: string } }) {
  return (
    <div className="dashboard-signal-unavailable" role="status">
      <b>Not tracked yet</b>
      <span>{gap?.reason ?? "This measure needs additional source data."}</span>
    </div>
  );
}

function RepLoad({ reps, settingsId }: { reps: RepAnalytics | null; settingsId: string }) {
  const rows = reps?.members?.filter((member) => member.status === "active") ?? [];
  if (!rows.length) {
    return <Empty title="Rep data unavailable" text="No active workspace members have assigned-lead records in this period." />;
  }
  const metrics = [
    {
      id: "open-leads",
      label: "Open leads",
      rows: rows.map((member) => ({
        label: member.display_name,
        value: member.active_leads,
        note: `${member.active_leads} open · ${member.conversion_rate}% win rate`,
      })),
      formatter: (value: number) => `${value} open`,
    },
    {
      id: "assigned-leads",
      label: "Assigned leads",
      rows: rows.map((member) => ({
        label: member.display_name,
        value: member.assigned_leads,
      })),
    },
    {
      id: "win-rate",
      label: "Recorded win rate",
      rows: rows.map((member) => ({
        label: member.display_name,
        value: member.conversion_rate,
      })),
      formatter: (value: number) => `${value}%`,
      scaleMaximum: 100,
    },
    {
      id: "revenue",
      label: "Recorded revenue",
      rows: rows.map((member) => ({
        label: member.display_name,
        value: member.revenue,
      })),
      formatter: money,
    },
  ];
  return (
    <>
      <Bars
        rows={metrics[0].rows}
        metricOptions={metrics}
        settingsId={settingsId}
        formatter={metrics[0].formatter}
      />
      <p className="panel-copy">Open-lead counts are workload signals; configured capacity limits are not stored.</p>
    </>
  );
}

function ForecastCoverage({ advanced }: { advanced: AdvancedAnalytics | null }) {
  if (!advanced) {
    return <Empty title="Forecast unavailable" text="Recorded pipeline and quota data are not available." />;
  }
  const forecast = advanced.forecast;
  if (forecast.quota_usd == null || forecast.quota_usd <= 0) {
    return <Empty title="No monthly quota configured" text={`${money(forecast.open_pipeline_usd)} in recorded open pipeline; add a target to calculate coverage.`} />;
  }
  return (
    <div className="dashboard-signal-metric">
      <strong>{forecast.coverage == null ? "—" : `${forecast.coverage.toFixed(2)}×`}</strong>
      <span>{money(forecast.open_pipeline_usd)} open pipeline against {money(forecast.quota_usd)} monthly quota</span>
      <small>{forecast.expected_open_pipeline_usd == null ? "Expected value withheld until enough closed outcomes exist." : `${money(forecast.expected_open_pipeline_usd)} expected at observed close rate.`}</small>
    </div>
  );
}

function AgentFeed({ agents }: { agents: AgentActivity | null }) {
  if (!agents) {
    return <Empty title="Activity unavailable" text="Your workspace role does not include agent activity access." />;
  }
  if (!agents.activity.length) {
    return <Empty title="No recent agent activity" text="Recorded tool and pipeline events will appear here." />;
  }
  return (
    <ol className="dashboard-agent-feed">
      {agents.activity.slice(0, 5).map((item) => (
        <li key={item.id}>
          <div><b>{item.agent}</b><span>{pretty(item.stage)} · {pretty(item.status)}</span></div>
          <time dateTime={item.created_at}>{new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(new Date(item.created_at))}</time>
        </li>
      ))}
    </ol>
  );
}
