import { useMemo, useState } from "react";
import { AgentFlow } from "../components/AgentFlow";
import type { AgentActivity, PipelineAnalytics } from "../types";
import { Icon, type IconName } from "../v3/ui/Icon";
import { Badge, Button, EmptyState, PageHeader, Segmented, type Tone } from "../v3/ui/primitives";
import { activityTitle, pretty, Unavailable } from "./productShared";

type Entry = AgentActivity["activity"][number];
type Tab = "activity" | "agents" | "how";
type Status = "all" | "done" | "attention";

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
  pipeline,
  onInspect,
}: {
  data: AgentActivity | null;
  pipeline: PipelineAnalytics | null;
  onInspect?: (leadId: string) => void;
}) {
  const [tab, setTab] = useState<Tab>("activity");
  const [status, setStatus] = useState<Status>("all");
  const [agent, setAgent] = useState("all");
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
    () => entries.filter((entry) => (agent === "all" || entry.agent === agent) && (status === "all" || (status === "attention" ? failed(entry) : !failed(entry)))),
    [entries, agent, status],
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
        actions={<Segmented<Tab> label="AI activity sections" value={tab} onChange={setTab} options={[{ value: "activity", label: "Activity" }, { value: "agents", label: "Agents" }, { value: "how", label: "How it works" }]} />}
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

      {tab === "how" && (
        <div className="ai-how">
          <AgentFlow pipeline={pipeline} activity={data} />
          <section className="v-card v-card-pad">
            <h2 className="v-card-title">What Corral will and will not do</h2>
            <ul className="ai-rules">
              <li><Icon name="check" size={14} />Scores, routes and drafts on its own, using only what was recorded.</li>
              <li><Icon name="check" size={14} />Emails, CRM writes and deletions always wait for a person to approve them.</li>
              <li><Icon name="check" size={14} />Every action here is logged with its result, so you can audit it later.</li>
            </ul>
          </section>
        </div>
      )}
    </div>
  );
}
