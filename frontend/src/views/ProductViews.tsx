import { lazy, memo, Suspense, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import type { CSSProperties, FormEvent, ReactNode } from "react";

import { bulkSendLeadsToCrm, createLead, exportLeads } from "../api";
import { AgentFlow } from "../components/AgentFlow";
import { Bars, Donut, TrendChart } from "../components/Charts";
import { LeadDetail } from "../components/LeadDetail";
import { NetworkDomFallback } from "../components/NetworkDomFallback";
import type {
  AgentActivity,
  AnalyticsMetadata,
  ChannelAnalytics,
  DashboardAnalytics,
  Lead,
  LeadPayload,
  ManualReviewAnalytics,
  PipelineAnalytics,
  RepAnalytics,
  RevenueAnalytics,
  TimeSeriesAnalytics,
} from "../types";

const LeadIntelligenceNetwork = lazy(() => import("../components/LeadIntelligenceNetwork"));

export type AnalyticsBundle = {
  dashboard: DashboardAnalytics;
  channels: ChannelAnalytics;
  pipeline: PipelineAnalytics;
  revenue: RevenueAnalytics;
  reps: RepAnalytics;
  series: TimeSeriesAnalytics;
  agents: AgentActivity;
  review: ManualReviewAnalytics;
};

const emptyLead: LeadPayload = {
  source: "website",
  source_detail: "manual-control-center",
  full_name: "",
  email: "",
  phone: "",
  job_title: "",
  company_name: "",
  industry: "",
  employee_count: null,
  annual_revenue: null,
  geography: "",
  budget: null,
  product_or_service: "",
  message: "",
};

export const pretty = (value: string) =>
  value.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
export const money = (value: number) =>
  new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 0,
  }).format(value);

export function DashboardView({
  data,
  latestLead,
  onInspect,
}: {
  data: AnalyticsBundle | null;
  latestLead: Lead | null;
  onInspect: (id: string) => void;
}) {
  if (!data) return <Unavailable />;
  const highlighted = [
    "total_leads",
    "qualification_rate",
    "hot_leads",
    "active_pipeline_value",
    "manual_review",
    "average_routing_velocity_minutes",
  ];
  const metrics = data.dashboard.metrics.filter((metric) => highlighted.includes(metric.key));
  const qualificationRate = metricValue(data.dashboard, "qualification_rate");

  return (
    <div className="page-view dashboard-view">
      <header className="page-hero dashboard-hero">
        <div>
          <span className="eyebrow">Executive signal field / {data.dashboard.metadata.range}</span>
          <h1>Revenue intelligence,<br /><em>in operational motion.</em></h1>
          <p>Every metric resolves to workspace-owned lead, decision and delivery evidence. No cross-workspace aggregation. No synthetic revenue presented as live.</p>
        </div>
        <div className="hero-pulse">
          <span>Qualification pulse</span>
          <strong>{qualificationRate}%</strong>
          <i><span style={{ transform: `scaleX(${qualificationRate / 100})` }} /></i>
          <small>{metricValue(data.dashboard, "sqls")} sales-qualified leads</small>
        </div>
      </header>
      <section className="metric-grid">
        {metrics.map((metric) => <MetricCard key={metric.key} metric={metric} />)}
      </section>
      <ResponsiveLeadIntelligenceNetwork pipeline={data.pipeline} activity={data.agents} lead={latestLead} />
      <section className="dashboard-command-grid">
        <Panel title="Live operational activity" eyebrow="Audited agent events" action={`${data.agents.activity.length} in range`}>
          <ActivityFeed data={data.agents} compact />
        </Panel>
        <Panel title="Attention queue" eyebrow="Human judgment required" action={`${data.review.total} open`}>
          {data.review.items.length ? (
            <div className="compact-leads">
              {data.review.items.slice(0, 5).map((lead) => (
                <LeadRow lead={lead} key={lead.id} onClick={() => onInspect(lead.id)} />
              ))}
            </div>
          ) : <Empty title="No review debt" text="No leads need human review in this period." />}
        </Panel>
      </section>
      <section className="dashboard-grid">
        <Panel title="Signal velocity" eyebrow="Lead volume over time" action="Server aggregated">
          <TrendChart values={data.series.points.map((point) => point.leads)} labels={data.series.points.map((point) => point.period)} />
        </Panel>
        <Panel title="Qualification mix" eyebrow="Decision bands">
          <Donut rows={data.dashboard.band_distribution.map((row) => ({ label: row.band, value: row.count }))} />
        </Panel>
      </section>
      <section className="dashboard-grid">
        <Panel title="Channel intelligence" eyebrow="Quality + useful volume">
          <Bars
            rows={data.channels.channels.map((row) => ({
              label: pretty(row.channel),
              value: row.importance_index,
              note: `${row.lead_volume} leads · ${row.average_score} avg score`,
            }))}
          />
        </Panel>
        <Panel title="Pipeline pulse" eyebrow="Active progression by stage">
          <Bars rows={data.pipeline.stages.map((stage) => ({ label: stage.label, value: stage.count, note: stage.value ? money(stage.value) : undefined }))} />
        </Panel>
      </section>
    </div>
  );
}

export function LeadsView({
  leads,
  selected,
  onInspect,
  onCreated,
  onUpdated,
  canEdit = true,
  canExport = false,
  canCrmSync = false,
}: {
  leads: Lead[];
  selected: Lead | null;
  onInspect: (id: string) => void;
  onCreated: (lead: Lead) => void;
  onUpdated: (lead: Lead) => void;
  canEdit?: boolean;
  canExport?: boolean;
  canCrmSync?: boolean;
}) {
  const [search, setSearch] = useState("");
  const [band, setBand] = useState("all");
  const [source, setSource] = useState("all");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [showExport, setShowExport] = useState(false);
  const [showForm, setShowForm] = useState(false);
  const [crmNotice, setCrmNotice] = useState<string | null>(null);
  const normalizedSearch = search.trim().toLowerCase();
  const filtered = useMemo(
    () => leads.filter((lead) => {
      const haystack = `${lead.full_name} ${lead.company_name} ${lead.email} ${lead.product_or_service}`.toLowerCase();
      return haystack.includes(normalizedSearch)
        && (band === "all" || lead.qualification?.classification === band)
        && (source === "all" || lead.source === source)
        && (!dateFrom || lead.received_at.slice(0, 10) >= dateFrom)
        && (!dateTo || lead.received_at.slice(0, 10) <= dateTo);
    }),
    [leads, normalizedSearch, band, source, dateFrom, dateTo],
  );
  const visibleSelected = filtered.filter((lead) => selectedIds.has(lead.id)).length;
  const toggleSelected = (id: string) => setSelectedIds((current) => {
    const next = new Set(current);
    if (next.has(id)) next.delete(id); else next.add(id);
    return next;
  });
  const selectVisible = () => setSelectedIds((current) => {
    const next = new Set(current);
    if (visibleSelected === filtered.length) filtered.forEach((lead) => next.delete(lead.id));
    else filtered.forEach((lead) => next.add(lead.id));
    return next;
  });

  return (
    <div className="page-view operational-page">
      <header className="page-hero compact-hero">
        <div><span className="eyebrow">Lead intelligence archive</span><h1>Every decision,<br /><em>traceable to evidence.</em></h1><p>Search, filter and inspect the complete journey from original signal to connected-system delivery.</p></div>
        <div className="lead-hero-actions">
          {canExport && <button className="secondary-button" onClick={() => setShowExport(true)}>Export</button>}
          {canEdit && <button className="primary-button" onClick={() => setShowForm((current) => !current)}>{showForm ? "Close intake" : "+ Submit lead"}</button>}
        </div>
      </header>
      {showForm && <LeadForm onCreated={onCreated} />}
      <section className="lead-browser panel">
        <div className="lead-toolbar">
          <label><span className="sr-only">Search leads</span><input type="search" placeholder="Search company, contact, service…" value={search} onChange={(event) => setSearch(event.target.value)} /></label>
          <select aria-label="Qualification band" value={band} onChange={(event) => setBand(event.target.value)}><option value="all">All qualification bands</option>{["hot", "warm", "needs_qualifying", "nurture", "disqualify"].map((item) => <option value={item} key={item}>{qualificationLabel(item)}</option>)}</select>
          <select aria-label="Lead source" value={source} onChange={(event) => setSource(event.target.value)}><option value="all">All channels</option>{Array.from(new Set(leads.map((lead) => lead.source))).map((item) => <option key={item}>{item}</option>)}</select>
          <label className="date-filter"><span className="sr-only">Received from</span><input aria-label="Received from" type="date" value={dateFrom} onChange={(event) => setDateFrom(event.target.value)} /></label>
          <label className="date-filter"><span className="sr-only">Received to</span><input aria-label="Received to" type="date" value={dateTo} onChange={(event) => setDateTo(event.target.value)} /></label>
          <span>{filtered.length} records</span>
        </div>
        {filtered.length > 0 && <div className="lead-selection-bar"><label><input type="checkbox" checked={filtered.length > 0 && visibleSelected === filtered.length} onChange={selectVisible} /> Select visible</label><span>{selectedIds.size} selected</span>{canCrmSync && <button className="button-quiet" disabled={!selectedIds.size} onClick={async () => { if (!window.confirm(`Send or update ${selectedIds.size} selected leads in the connected CRM?`)) return; try { const result = await bulkSendLeadsToCrm([...selectedIds]); setCrmNotice(`${result.succeeded} CRM writes completed; ${result.failed} failed.`); } catch (error) { setCrmNotice(error instanceof Error ? error.message : "Bulk CRM write failed"); } }}>Send selected to CRM</button>}<button className="button-quiet" disabled={!selectedIds.size} onClick={() => setSelectedIds(new Set())}>Clear selection</button></div>}
        {crmNotice && <div className="notice" role="status">{crmNotice}</div>}
        <div className="lead-table-wrap">
          <table className="lead-table">
            <thead><tr><th aria-label="Select leads">Select</th><th>Lead</th><th>Source</th><th>Score</th><th>Routing</th><th>Representative</th><th>CRM</th><th>Received</th></tr></thead>
            <tbody>{filtered.map((lead) => <LeadTableRow key={lead.id} lead={lead} selected={selected?.id === lead.id} checked={selectedIds.has(lead.id)} onToggle={toggleSelected} onInspect={onInspect} />)}</tbody>
          </table>
          {filtered.length === 0 && <Empty title="No matching signals" text="Adjust filters or submit a lead." />}
        </div>
      </section>
      {showExport && <ExportDialog count={filtered.length} selectedCount={selectedIds.size} filters={{ search, band, source, dateFrom, dateTo }} onClose={() => setShowExport(false)} selectedIds={[...selectedIds]} />}
      {selected && <LeadDetail lead={selected} canCrmSync={canCrmSync} canEdit={canEdit} onUpdated={onUpdated} />}
    </div>
  );
}

function ExportDialog({ count, selectedCount, selectedIds, filters, onClose }: { count: number; selectedCount: number; selectedIds: string[]; filters: { search: string; band: string; source: string; dateFrom: string; dateTo: string }; onClose: () => void }) {
  const [format, setFormat] = useState<"csv" | "xlsx" | "pdf">("xlsx");
  const [scope, setScope] = useState<"current_filters" | "selected" | "all">("current_filters");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function submit() {
    setBusy(true); setError(null);
    try {
      const result = await exportLeads({
        format, scope,
        filters: scope === "current_filters" ? { search: filters.search, source: filters.source === "all" ? null : filters.source, qualification_bands: filters.band === "all" ? [] : [filters.band], date_from: filters.dateFrom || null, date_to: filters.dateTo || null } : {},
        selected_lead_ids: scope === "selected" ? selectedIds : [],
      });
      const url = URL.createObjectURL(result.blob);
      const anchor = document.createElement("a"); anchor.href = url; anchor.download = result.filename; anchor.click(); URL.revokeObjectURL(url); onClose();
    } catch (requestError) { setError(requestError instanceof Error ? requestError.message : "Unable to generate export"); }
    finally { setBusy(false); }
  }
  const summary = scope === "all" ? "All permitted workspace leads" : `${scope === "selected" ? selectedCount : count} leads${filters.band !== "all" ? ` · ${qualificationLabel(filters.band)}` : ""}${filters.source !== "all" ? ` · ${pretty(filters.source)}` : ""}${filters.dateFrom || filters.dateTo ? ` · ${filters.dateFrom || "Any"} – ${filters.dateTo || "Any"}` : ""}`;
  return <div className="team-modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}><section className="team-modal export-dialog" role="dialog" aria-modal="true" aria-labelledby="export-title"><div className="team-modal-head"><div><span className="eyebrow">Secure workspace export</span><h2 id="export-title">Take leads with you</h2><p>{summary}</p></div><button className="modal-close" onClick={onClose} aria-label="Close export dialog">×</button></div><fieldset><legend>Format</legend><label><input type="radio" name="export-format" checked={format === "csv"} onChange={() => setFormat("csv")} /> CSV</label><label><input type="radio" name="export-format" checked={format === "xlsx"} onChange={() => setFormat("xlsx")} /> Excel workbook</label><label><input type="radio" name="export-format" checked={format === "pdf"} onChange={() => setFormat("pdf")} /> PDF report</label></fieldset><fieldset><legend>Scope</legend><label><input type="radio" name="export-scope" checked={scope === "current_filters"} onChange={() => setScope("current_filters")} /> Current filtered results ({count})</label><label><input type="radio" name="export-scope" disabled={!selectedCount} checked={scope === "selected"} onChange={() => setScope("selected")} /> Selected leads ({selectedCount})</label><label><input type="radio" name="export-scope" checked={scope === "all"} onChange={() => setScope("all")} /> All permitted workspace leads</label></fieldset>{error && <p className="team-alert" role="alert">{error}</p>}<div className="team-modal-actions"><button className="button-quiet" onClick={onClose} disabled={busy}>Cancel</button><button className="primary-button" onClick={() => void submit()} disabled={busy || (scope === "selected" && !selectedCount)}>{busy ? "Preparing…" : "Download export"}</button></div></section></div>;
}

function LeadForm({ onCreated }: { onCreated: (lead: Lead) => void }) {
  const [form, setForm] = useState<LeadPayload>(emptyLead);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const set = (key: keyof LeadPayload, value: string | number | null) =>
    setForm((current) => ({ ...current, [key]: value }));
  async function submit(event: FormEvent) {
    event.preventDefault(); setBusy(true); setError(null);
    try { const lead = await createLead(form); onCreated(lead); setForm(emptyLead); }
    catch (requestError) { setError(requestError instanceof Error ? requestError.message : "Unable to submit lead"); }
    finally { setBusy(false); }
  }
  return (
    <form className="intake-form panel" onSubmit={submit}>
      <div className="section-heading"><div><span className="eyebrow">Manual / website intake</span><h2>Run a signal through the live pipeline</h2></div><span className="flow-status"><i /> Deterministic safeguards active</span></div>
      <div className="form-grid">
        <Field label="Full name" value={form.full_name} onChange={(value) => set("full_name", value)} />
        <Field label="Email" type="email" value={form.email} onChange={(value) => set("email", value)} />
        <Field label="Company" value={form.company_name} onChange={(value) => set("company_name", value)} />
        <Field label="Title" value={form.job_title || ""} onChange={(value) => set("job_title", value)} />
        <Field label="Industry" value={form.industry || ""} onChange={(value) => set("industry", value)} />
        <Field label="Geography" value={form.geography || ""} onChange={(value) => set("geography", value)} />
        <Field label="Employees" type="number" value={form.employee_count ?? ""} onChange={(value) => set("employee_count", value ? Number(value) : null)} />
        <Field label="Budget" type="number" value={form.budget ?? ""} onChange={(value) => set("budget", value ? Number(value) : null)} />
        <Field label="Service" value={form.product_or_service} onChange={(value) => set("product_or_service", value)} />
      </div>
      <label className="field wide"><span>Original inquiry</span><textarea required value={form.message} onChange={(event) => set("message", event.target.value)} rows={5} placeholder="Pain, desired outcome, budget, timeline and technical context…" /></label>
      {error && <p className="form-error">{error}</p>}
      <button className="primary-button" disabled={busy}>{busy ? "Processing signal…" : "Run intelligence pipeline →"}</button>
    </form>
  );
}

export function PipelineView({ data, onInspect }: { data: PipelineAnalytics | null; onInspect: (id: string) => void }) {
  const [active, setActive] = useState("received");
  if (!data) return <Unavailable />;
  const selected = data.stages.find((stage) => stage.stage === active) ?? data.stages[0];
  return (
    <div className="page-view operational-page">
      <header className="page-hero compact-hero"><div><span className="eyebrow">Commercial motion map</span><h1>Pipeline, without<br /><em>the blind spots.</em></h1><p>Inspect every stage and the qualification state that shaped progression.</p></div><div className="pipeline-total"><span>Active pipeline</span><strong>{money(data.stages.find((stage) => stage.stage === "opportunity")?.value ?? 0)}</strong></div></header>
      <section className="pipeline-track panel">{data.stages.map((stage, index) => <button key={stage.stage} className={active === stage.stage ? "active" : ""} aria-pressed={active === stage.stage} onClick={() => setActive(stage.stage)}><span>{String(index + 1).padStart(2, "0")}</span><b>{stage.label}</b><strong>{stage.count}</strong><small>{stage.value ? money(stage.value) : "—"}</small><span className="pipeline-lead-dots" aria-hidden="true">{stage.leads.slice(0, 4).map((lead) => <i key={lead.id} />)}</span>{index < data.stages.length - 1 && <i className="pipeline-link" />}</button>)}</section>
      <section className="pipeline-inspector pipeline-detail-enter" key={selected.stage}>
        <Panel title={`${selected.label} leads`} eyebrow="Underlying workspace records" action={`${selected.count} total`}>{selected.leads.length ? <div className="compact-leads">{selected.leads.map((lead) => <LeadRow lead={lead} key={lead.id} onClick={() => onInspect(lead.id)} />)}</div> : <Empty title="Stage is clear" text="No leads occupy this stage in the selected period." />}</Panel>
        <Panel title="Qualification state" eyebrow="Decision distribution"><Bars rows={data.bands.map((row) => ({ label: row.band, value: row.count }))} /></Panel>
      </section>
    </div>
  );
}

export function AgentsView({ data, pipeline }: { data: AgentActivity | null; pipeline: PipelineAnalytics | null }) {
  if (!data) return <Unavailable />;
  return (
    <div className="page-view">
      <header className="page-hero compact-hero"><div><span className="eyebrow">Agentic operations center</span><h1>Autonomy with<br /><em>visible boundaries.</em></h1><p>Business-facing results, tool invocations and audit states—without exposing hidden model reasoning.</p></div><div className="agent-count"><strong>{data.capabilities.filter((item) => item.status === "implemented").length}</strong><span>implemented capabilities</span></div></header>
      <AgentFlow pipeline={pipeline} activity={data} />
      <section className="agent-layout">
        <Panel title="Capability registry" eyebrow="Implemented vs reserved"><div className="capability-grid">{data.capabilities.map((item) => <div className={item.status} key={item.name}><i /> <b>{item.name}</b><span>{item.status}</span></div>)}</div></Panel>
        <Panel title="Recent runs" eyebrow="Workspace audit stream" action={`${data.activity.length} events`}><ActivityFeed data={data} /></Panel>
      </section>
    </div>
  );
}

export function AnalyticsView({ data }: { data: AnalyticsBundle | null }) {
  if (!data) return <Unavailable />;
  const provenance = data.revenue.data_status === "demo"
    ? "Seeded values—not production revenue"
    : data.revenue.data_status === "crm"
      ? "CRM-derived outcomes"
      : "No revenue facts available";
  return (
    <div className="page-view">
      <header className="page-hero compact-hero"><div><span className="eyebrow">Workspace analytics</span><h1>Quality, velocity<br /><em>and commercial value.</em></h1><p>Time-bounded metrics generated server-side from workspace-owned records.</p></div><div className={`data-provenance ${data.revenue.data_status}`}><span>Revenue provenance</span><b>{pretty(data.revenue.data_status)}</b><small>{provenance}</small></div></header>
      <section className="analytics-grid analytics-motion">
        <Panel title="Channel performance" eyebrow="Volume × quality × conversion"><div className="channel-table"><div className="channel-head"><span>Channel</span><span>Volume</span><span>Qual.</span><span>SQL</span><span>Won</span><span>Revenue</span><span>Importance</span></div>{data.channels.channels.map((row) => <div key={row.channel}><b>{pretty(row.channel)}</b><span>{row.lead_volume} · {row.share_percent}%</span><span>{row.qualification_rate}%</span><span>{row.sql_conversion_rate}%</span><span>{row.closed_won_conversion_rate}%</span><span>{money(row.revenue)}</span><em>{row.importance_index}</em></div>)}</div></Panel>
        <Panel title="Lead trajectory" eyebrow="Volume by period"><TrendChart values={data.series.points.map((point) => point.leads)} labels={data.series.points.map((point) => point.period)} /></Panel>
        <Panel title="Revenue by channel" eyebrow={`${pretty(data.revenue.data_status)} data`}><Bars rows={data.revenue.by_channel.map((row) => ({ label: pretty(row.label), value: row.value }))} formatter={money} /></Panel>
        <Panel title="Revenue by service" eyebrow="Closed / won attribution"><Bars rows={data.revenue.by_service.map((row) => ({ label: row.label, value: row.value }))} formatter={money} /></Panel>
        <Panel title="Representative performance" eyebrow="Context, not leaderboard"><div className="rep-table">{data.reps.reps.map((rep) => <div key={rep.rep_name}><span className="rep-avatar">{rep.rep_name.split(" ").map((word) => word[0]).join("")}</span><span><b>{rep.rep_name}</b><small>{rep.assigned_leads} assigned · {rep.qualified_leads} qualified</small></span><span><b>{rep.closed_leads}</b><small>closed</small></span><span><b>{rep.conversion_rate}%</b><small>conversion</small></span><span><b>{money(rep.revenue)}</b><small>revenue</small></span><span><b>{rep.average_response_minutes ?? "—"}</b><small>avg min</small></span></div>)}</div></Panel>
        <Panel title="Revenue trajectory" eyebrow="Closed / won over time"><TrendChart values={data.revenue.over_time.map((point) => point.value)} labels={data.revenue.over_time.map((point) => point.period)} /></Panel>
      </section>
    </div>
  );
}

export function ManualReviewView({ data, onInspect }: { data: ManualReviewAnalytics | null; onInspect: (id: string) => void }) {
  if (!data) return <Unavailable />;
  return (
    <div className="page-view operational-page">
      <header className="page-hero compact-hero"><div><span className="eyebrow">Human judgment queue</span><h1>Uncertainty made<br /><em>safe and inspectable.</em></h1><p>Provider failures, low confidence and contradictory evidence pause here. The system does not invent a resolution.</p></div><div className="review-total"><strong>{data.total}</strong><span>open review{data.total === 1 ? "" : "s"}</span></div></header>
      <section className="review-grid">{data.items.length ? data.items.map((lead) => <article className="review-card panel" key={lead.id}><div className="review-card-head"><span className={`source-tag source-${lead.source}`}>{pretty(lead.source)}</span><div className="review-qualification"><b>{lead.score}</b><QualificationBadge value={lead.band} manualReview /></div></div><h2>{lead.company}</h2><p>{lead.name} · {lead.service}</p><div className="review-reasons">{lead.review_reasons.map((reason) => <span key={reason}>{reason}</span>)}</div><div><small>Current queue</small><b>{pretty(lead.routing_status)}</b></div><button onClick={() => onInspect(lead.id)}>Inspect evidence →</button></article>) : <Empty title="Manual review is clear" text="No leads need human judgment in this period." />}</section>
    </div>
  );
}

export function SettingsView({ metadata, onIntegrations, onTeam, onIcp, canViewTeam, canViewIcp }: { metadata?: AnalyticsMetadata; onIntegrations: () => void; onTeam: () => void; onIcp: () => void; canViewTeam: boolean; canViewIcp: boolean }) {
  return (
    <div className="page-view operational-page">
      <header className="page-hero compact-hero"><div><span className="eyebrow">Workspace control plane</span><h1>Clear boundaries,<br /><em>durable context.</em></h1><p>Workspace identity, data provenance and team access stay visible here. Choose a control surface without losing the workspace context.</p></div></header>
      <section className="settings-grid">
        <Panel title="Workspace" eyebrow="Tenant boundary"><div className="settings-facts"><Fact label="Name" value={metadata?.workspace_name || "Unavailable"} /><Fact label="Workspace ID" value={metadata?.workspace_id || "Unavailable"} /><Fact label="Data mode" value={pretty(metadata?.data_mode || "unknown")} /><Fact label="Analytics freshness" value={metadata ? new Date(metadata.generated_at).toLocaleString() : "Unavailable"} /></div></Panel>
        <Panel title="External systems" eyebrow="Provider connections"><p className="panel-copy">Manage workspace-owned CRM, notification and future mail connections. Stored credentials are represented only by safe state metadata.</p><button className="primary-button" onClick={onIntegrations}>Open Integration Center →</button></Panel>
        <Panel title="Team & permissions" eyebrow="Workspace access"><p className="panel-copy">Invite staff, review roles and make deliberate permission exceptions. Backend authorization remains the source of truth for every action.</p><button className="primary-button" disabled={!canViewTeam} onClick={onTeam}>{canViewTeam ? "Open Team & Permissions →" : "Members view permission required"}</button></Panel>
        <Panel title="Ideal Customer Profile" eyebrow="Qualification policy"><p className="panel-copy">Build, validate and safely test the workspace-specific 40-point ICP. Published versions remain immutable and historical scores retain their exact policy reference.</p><button className="primary-button" disabled={!canViewIcp} onClick={onIcp}>{canViewIcp ? "Open ICP Builder →" : "ICP view permission required"}</button></Panel>
        <Panel title="Data range" eyebrow="Analytics contract"><p className="panel-copy">Weekly, monthly and annual windows are available. Period-over-period change is withheld when the prior period has no valid denominator.</p></Panel>
        <Panel title="Product state" eyebrow="Phase 7B"><p className="panel-copy">Analytics, team controls, workspace-specific versioned ICP qualification and secure exports are active. Live provider validation remains intentionally deferred.</p></Panel>
      </section>
    </div>
  );
}

function MetricCard({ metric }: { metric: DashboardAnalytics["metrics"][number] }) {
  const animated = useCountUp(metric.value);
  const featured = metric.key === "total_leads" || metric.key === "active_pipeline_value";
  const formatted = metric.key.includes("value") || metric.key.includes("revenue")
    ? money(animated)
    : metric.key.includes("rate")
      ? `${animated}%`
      : metric.key.includes("minutes") ? `${animated}m` : String(animated);
  return <article className={`metric-card metric-${metric.key} ${featured ? "metric-featured" : ""}`}><span>{metric.label}</span><strong>{formatted}</strong><small className={metric.change_percent != null && metric.change_percent < 0 ? "negative" : ""}><i>{metric.change_percent == null ? "·" : metric.change_percent >= 0 ? "↗" : "↘"}</i>{metric.change_percent == null ? "No valid prior comparison" : `${metric.change_percent > 0 ? "+" : ""}${metric.change_percent}% vs prior period`}</small><span className="metric-baseline"><i style={{ transform: `scaleX(${Math.min(1, Math.max(.08, Math.abs(metric.change_percent ?? 8) / 35))})` }} /></span><i /></article>;
}

function activityTitle(stage: string, toolName: string | null) {
  const value = stage.toLowerCase();
  if (value.includes("intake") || value.includes("received")) return "Lead received";
  if (value.includes("context") || value.includes("analysis")) return "Context analyzed";
  if (value.includes("icp")) return "ICP evaluated";
  if (value.includes("qualif")) return "Qualification completed";
  if (value.includes("rout")) return "Route selected";
  if (value.includes("notif") || toolName?.toLowerCase().includes("slack")) return "Slack notified";
  if (value.includes("crm") || value.includes("sync")) return "CRM synchronized";
  return pretty(toolName || stage);
}

function ActivityFeed({ data, compact = false }: { data: AgentActivity; compact?: boolean }) {
  const rows = data.activity.slice(0, compact ? 7 : 25);
  return <div className={`activity-list ${compact ? "activity-compact" : ""}`}>
    {rows.map((item, index) => <details key={item.id} style={{ "--activity-order": index } as CSSProperties}>
      <summary>
        <span className="activity-sequence">{String(index + 1).padStart(2, "0")}</span>
        <i className={`activity-status status-${item.status}`} />
        <span><b>{activityTitle(item.stage, item.tool_name)}</b><small>{item.tool_name || item.agent} · {new Date(item.created_at).toLocaleString()}</small></span>
        <span className="activity-runtime">{item.duration_ms != null ? `${item.duration_ms}ms` : item.provider || "internal"}</span>
        <em>{pretty(item.status)}</em>
      </summary>
      {!compact && <><pre>{JSON.stringify(item.structured_result, null, 2)}</pre>{item.error && <p>{item.error}</p>}</>}
    </details>)}
    {rows.length === 0 && <Empty title="No agent events" text="Audited activity will appear here as leads move through the system." />}
  </div>;
}

function useCountUp(value: number) {
  const [display, setDisplay] = useState(() => window.matchMedia("(prefers-reduced-motion: reduce)").matches ? value : 0);
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

function ResponsiveLeadIntelligenceNetwork({ pipeline, activity, lead }: { pipeline: PipelineAnalytics; activity: AgentActivity; lead: Lead | null }) {
  const [mobile, setMobile] = useState(() => window.matchMedia("(max-width: 700px)").matches);
  useEffect(() => {
    const query = window.matchMedia("(max-width: 700px)");
    const sync = () => setMobile(query.matches);
    query.addEventListener("change", sync);
    return () => query.removeEventListener("change", sync);
  }, []);

  if (mobile) return <NetworkDomFallback pipeline={pipeline} activity={activity} lead={lead} />;
  return <Suspense fallback={<div className="network-loading panel"><span className="eyebrow">Loading intelligence topology</span><i /></div>}>
    <LeadIntelligenceNetwork pipeline={pipeline} activity={activity} lead={lead} />
  </Suspense>;
}

export function Panel({ title, eyebrow, action, children }: { title: string; eyebrow: string; action?: string; children: ReactNode }) {
  return <section className="panel data-panel"><div className="section-heading"><div><span className="eyebrow">{eyebrow}</span><h2>{title}</h2></div>{action && <span className="panel-action">{action}</span>}</div>{children}</section>;
}

function LeadRow({ lead, onClick }: { lead: DashboardAnalytics["recent_leads"][number]; onClick: () => void }) {
  return <button className="compact-lead" onClick={onClick}><span className={`source-monogram source-${lead.source}`}>{lead.source.slice(0, 2).toUpperCase()}</span><span><b>{lead.company}</b><small>{lead.name} · {lead.service}</small></span><span className="compact-lead-qualification"><b>{lead.score}</b><QualificationBadge value={lead.band} manualReview={lead.manual_review} /></span><i>→</i></button>;
}

const QualificationBadge = memo(function QualificationBadge({ value, manualReview = false }: { value: string; manualReview?: boolean }) {
  const label = manualReview ? "Manual Review" : qualificationLabel(value);
  const tone = manualReview ? "manual-review" : value.toLowerCase().replaceAll("_", "-").replace(/[^a-z0-9-]/g, "-");
  return <span className={`qualification-badge qualification-${tone}`}>{label}</span>;
});

function qualificationLabel(value: string) {
  const normalized = value.toLowerCase().replaceAll("-", "_");
  const labels: Record<string, string> = {
    hot: "Hot",
    warm: "Warm",
    needs_qualifying: "Needs Qualifying",
    nurture: "Nurture",
    disqualify: "Disqualified",
    disqualified: "Disqualified",
    manual_review: "Manual Review",
    pending: "Pending",
  };
  return labels[normalized] ?? pretty(value);
}

const LeadTableRow = memo(function LeadTableRow({ lead, selected, checked, onToggle, onInspect }: { lead: Lead; selected: boolean; checked: boolean; onToggle: (id: string) => void; onInspect: (id: string) => void }) {
  return <tr className={selected ? "selected" : ""} onClick={() => onInspect(lead.id)} tabIndex={0} onKeyDown={(event) => {
    if (event.key === "Enter" || event.key === " ") { event.preventDefault(); onInspect(lead.id); }
  }}>
    <td className="lead-select-cell"><input type="checkbox" aria-label={`Select ${lead.full_name}`} checked={checked} onChange={() => onToggle(lead.id)} onClick={(event) => event.stopPropagation()} /></td>
    <td><b>{lead.full_name}</b><span>{lead.company_name}</span></td>
    <td><span className={`source-tag source-${lead.source}`}>{pretty(lead.source)}</span></td>
    <td><div className="qualification-cell" aria-label={`Score ${lead.qualification?.total_score ?? "unavailable"}; ${lead.analysis?.requires_manual_review ? "Manual Review" : qualificationLabel(lead.qualification?.classification || "pending")}`}><b>{lead.qualification?.total_score ?? "—"}</b><QualificationBadge value={lead.qualification?.classification || "pending"} manualReview={lead.analysis?.requires_manual_review} /></div></td>
    <td>{pretty(lead.routing?.assigned_queue || "Pending")}</td>
    <td>{lead.routing?.assigned_rep_name || "Unassigned"}</td>
    <td><span className={`state-dot state-${lead.crm_sync?.status || "none"}`}>{pretty(lead.crm_sync?.status || "Not synced")}</span></td>
    <td>{new Date(lead.received_at).toLocaleDateString()}</td>
  </tr>;
});

function Field({ label, value, onChange, type = "text" }: { label: string; value: string | number; onChange: (value: string) => void; type?: string }) {
  return <label className="field"><span>{label}</span><input required={["Full name", "Email", "Company", "Service"].includes(label)} type={type} value={value} onChange={(event) => onChange(event.target.value)} /></label>;
}
function Fact({ label, value }: { label: string; value: string }) { return <div className="fact"><span>{label}</span><b>{value}</b></div>; }
export function Empty({ title, text }: { title: string; text: string }) { return <div className="empty-state"><i /><b>{title}</b><span>{text}</span></div>; }
export function Unavailable() { return <div className="page-view"><Empty title="Analytics unavailable" text="Retry when the API is reachable." /></div>; }
function metricValue(data: DashboardAnalytics, key: string) { return data.metrics.find((metric) => metric.key === key)?.value ?? 0; }
