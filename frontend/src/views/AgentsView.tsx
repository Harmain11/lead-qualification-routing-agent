import { useMemo, useState } from "react";
import type { AgentActivity, PipelineAnalytics } from "../types";
import { Icon, type IconName } from "../v3/ui/Icon";
import { Badge, Button, EmptyState, PageHeader, Segmented, type Tone } from "../v3/ui/primitives";
import { activityTitle, pretty, Unavailable } from "./productShared";

type Entry = AgentActivity["activity"][number];
type Tab = "activity" | "agents";
type Status = "all" | "done" | "attention";
type When = "all" | "today" | "yesterday" | "7d" | "30d" | "custom";

const WHEN_LABELS: Array<{ value: When; label: string }> = [
  { value: "all", label: "All time" }, { value: "today", label: "Today" }, { value: "yesterday", label: "Yesterday" },
  { value: "7d", label: "Last 7 days" }, { value: "30d", label: "Last 30 days" }, { value: "custom", label: "Custom range" },
];

function withinWhen(iso: string, when: When, from: string, to: string) {
  if (when === "all") return true;
  const time = new Date(iso).getTime();
  const startOfToday = new Date(); startOfToday.setHours(0, 0, 0, 0);
  const day = 86_400_000;
  if (when === "today") return time >= startOfToday.getTime();
  if (when === "yesterday") return time >= startOfToday.getTime() - day && time < startOfToday.getTime();
  if (when === "7d") return time >= startOfToday.getTime() - 6 * day;
  if (when === "30d") return time >= startOfToday.getTime() - 29 * day;
  const start = from ? new Date(`${from}T00:00:00`).getTime() : -Infinity;
  const end = to ? new Date(`${to}T23:59:59.999`).getTime() : Infinity;
  return time >= start && time <= end;
}

const AGENT_ICON: Record<string, IconName> = {
  routing: "route", qualification: "target", context: "sparkle", enrichment: "layers", notification: "bell", normalization: "listBullets", analytics: "analytics", crm: "share",
};

const iconFor = (entry: Entry): IconName => {
  const key = Object.keys(AGENT_ICON).find((name) => `${entry.agent} ${entry.stage} ${entry.tool_name ?? ""}`.toLowerCase().includes(name));
  return key ? AGENT_ICON[key] : "bolt";
};
const failed = (entry: Entry) => Boolean(entry.error) || /fail|error|blocked|denied/i.test(entry.status);

function dayLabel(date: Date) {
  const today = new Date();
  const yesterday = new Date(today.getTime() - 86_400_000);
  if (date.toDateString() === today.toDateString()) return "Today";
  if (date.toDateString() === yesterday.toDateString()) return "Yesterday";
  return date.toLocaleDateString(undefined, { weekday: "long", month: "short", day: "numeric" });
}

function resultFacts(result: Record<string, unknown>): Array<[string, string]> {
  return Object.entries(result)
    .filter(([, value]) => value !== null && value !== undefined && value !== "" && typeof value !== "object")
    .slice(0, 8)
    .map(([key, value]) => [pretty(key), String(value)]);
}

export function AgentsView({
  data,
  onInspect,
}: {
  data: AgentActivity | null;
  pipeline: PipelineAnalytics | null;
  onInspect?: (leadId: string) => void;
}) {
  const [tab, setTab] = useState<Tab>("activity");
  const [status, setStatus] = useState<Status>("all");
  const [agent, setAgent] = useState("all");
  const [when, setWhen] = useState<When>("all");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [open, setOpen] = useState<string | null>(null);

  const entries = data?.activity ?? [];
  const agents = useMemo(() => [...new Set(entries.map((entry) => entry.agent))].sort(), [entries]);
  const stats = useMemo(() => {
    const issues = entries.filter(failed);
    const durations = entries.map((entry) => entry.duration_ms).filter((value): value is number => typeof value === "number");
    const leads = new Set(entries.map((entry) => entry.lead_id).filter(Boolean));
    return {
      total: entries.length,
      leads: leads.size,
      issues,
      success: entries.length ? Math.round(((entries.length - issues.length) / entries.length) * 100) : null,
      average: durations.length ? Math.round(durations.reduce((sum, value) => sum + value, 0) / durations.length) : null,
    };
  }, [entries]);
  const shown = useMemo(
    () => entries.filter((entry) => withinWhen(entry.created_at, when, from, to) && (agent === "all" || entry.agent === agent) && (status === "all" || (status === "attention" ? failed(entry) : !failed(entry)))),
    [entries, agent, status, when, from, to],
  );
  const groups = useMemo(() => {
    const map = new Map<string, Entry[]>();
    for (const entry of shown) {
      const label = dayLabel(new Date(entry.created_at));
      map.set(label, [...(map.get(label) ?? []), entry]);
    }
    return [...map.entries()];
  }, [shown]);

  if (!data) return <Unavailable />;
  const implemented = data.capabilities.filter((item) => item.status === "implemented").length;

  return (
    <div className="ai-page">
      <PageHeader
        title="AI activity"
        subtitle="What Corral did on your behalf, what it found, and anything that needs you."
        actions={<Segmented<Tab> label="AI activity sections" value={tab} onChange={setTab} options={[{ value: "activity", label: "Activity" }, { value: "agents", label: "Agents" }]} />}
      />

      <section className="ai-stats" aria-label="Summary for this period">
        <div><span>Actions</span><b className="num">{stats.total}</b><small>{implemented} capabilities active</small></div>
        <div><span>Leads touched</span><b className="num">{stats.leads}</b><small>In this period</small></div>
        <div><span>Completed cleanly</span><b className="num">{stats.success === null ? "—" : `${stats.success}%`}</b><small>{stats.issues.length ? `${stats.issues.length} need a look` : "Nothing needs a look"}</small></div>
        <div><span>Typical speed</span><b className="num">{stats.average === null ? "—" : stats.average < 1000 ? `${stats.average} ms` : `${(stats.average / 1000).toFixed(1)} s`}</b><small>Per action</small></div>
      </section>

      {stats.issues.length > 0 && (
        <section className="ai-attention" aria-label="Needs attention">
          <div className="ai-attention-head"><Icon name="alert" size={16} /><b>{stats.issues.length} action{stats.issues.length === 1 ? "" : "s"} did not finish</b></div>
          <ul>
            {stats.issues.slice(0, 3).map((entry) => (
              <li key={entry.id}>
                <span><b>{activityTitle(entry.stage, entry.tool_name)}</b><small>{entry.error || pretty(entry.status)}</small></span>
                {entry.lead_id && onInspect && <Button kind="gray" size="sm" onClick={() => onInspect(entry.lead_id!)}>Open lead</Button>}
              </li>
            ))}
          </ul>
        </section>
      )}

      {tab === "activity" && (
        <section aria-label="Activity log">
          <div className="ai-filters">
            <Segmented<Status> label="Show" value={status} onChange={setStatus} options={[{ value: "all", label: "All" }, { value: "done", label: "Completed" }, { value: "attention", label: stats.issues.length ? `Needs a look · ${stats.issues.length}` : "Needs a look" }]} />
            <div className="ai-when" role="group" aria-label="Date range">
              <label className="ai-select"><span className="v-sr">Date range</span>
                <select className="v-popup" value={when} onChange={(event) => setWhen(event.target.value as When)}>
                  {WHEN_LABELS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
                </select>
              </label>
              {when === "custom" && (
                <span className="ai-custom">
                  <label><span className="v-sr">From</span><input type="date" value={from} max={to || undefined} onChange={(event) => setFrom(event.target.value)} /></label>
                  <i aria-hidden="true">to</i>
                  <label><span className="v-sr">To</span><input type="date" value={to} min={from || undefined} onChange={(event) => setTo(event.target.value)} /></label>
                </span>
              )}
            </div>
            {agents.length > 1 && (
              <label className="ai-agent-filter">
                <span className="v-sr">Agent</span>
                <select className="v-popup" value={agent} onChange={(event) => setAgent(event.target.value)}>
                  <option value="all">All agents</option>
                  {agents.map((name) => <option key={name} value={name}>{name}</option>)}
                </select>
              </label>
            )}
          </div>
          {groups.length === 0 ? (
            <div className="v-card"><EmptyState icon="bolt" title="No activity in this view">Actions appear here as Corral scores, routes and follows up on leads.</EmptyState></div>
          ) : groups.map(([label, items]) => (
            <div key={label} className="ai-day">
              <h2>{label}</h2>
              <ol className="ai-log">
                {items.map((entry) => {
                  const facts = resultFacts(entry.structured_result);
                  const isOpen = open === entry.id;
                  const tone: Tone = failed(entry) ? "danger" : "tint";
                  return (
                    <li key={entry.id} className="ai-row" data-failed={failed(entry) || undefined}>
                      <button type="button" className="ai-row-main" aria-expanded={isOpen} onClick={() => setOpen(isOpen ? null : entry.id)}>
                        <span className="ai-row-icon"><Icon name={iconFor(entry)} size={16} /></span>
                        <span className="ai-row-text">
                          <b>{activityTitle(entry.stage, entry.tool_name)}</b>
                          <small>{entry.agent}{entry.provider ? ` · ${pretty(entry.provider)}` : ""}</small>
                        </span>
                        <Badge tone={tone}>{failed(entry) ? "Needs a look" : pretty(entry.status)}</Badge>
                        <time dateTime={entry.created_at}>{new Date(entry.created_at).toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" })}</time>
                        <span className="ai-chevron" aria-hidden="true" />
                      </button>
                      {isOpen && (
                        <div className="ai-row-detail">
                          {entry.error && <p className="ai-error">{entry.error}</p>}
                          {facts.length > 0 && <dl>{facts.map(([key, value]) => <div key={key}><dt>{key}</dt><dd>{value}</dd></div>)}</dl>}
                          <div className="ai-row-foot">
                            {entry.duration_ms != null && <small>Took {entry.duration_ms < 1000 ? `${entry.duration_ms} ms` : `${(entry.duration_ms / 1000).toFixed(1)} s`}</small>}
                            {entry.lead_id && onInspect && <Button kind="gray" size="sm" onClick={() => onInspect(entry.lead_id!)}>Open lead</Button>}
                          </div>
                        </div>
                      )}
                    </li>
                  );
                })}
              </ol>
            </div>
          ))}
        </section>
      )}

      {tab === "agents" && (
        <section className="ai-agents" aria-label="Agents">
          {data.insights.map((insight) => (
            <article className="v-card v-card-pad ai-agent" key={insight.agent}>
              <header><h2 className="v-card-title">{insight.agent}</h2><Badge tone="neutral">Read-only</Badge></header>
              <p>{insight.summary}</p>
              <dl>{insight.metrics.map((metric) => <div key={metric.key}><dt>{metric.label}</dt><dd className="num">{metric.value}</dd></div>)}</dl>
              {insight.details.length > 0 && <ul>{insight.details.map((detail) => <li key={detail}>{detail}</li>)}</ul>}
            </article>
          ))}
        </section>
      )}
    </div>
  );
}
