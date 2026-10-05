import { memo, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import type { CSSProperties, FormEvent, ReactNode } from "react";

import { bulkDeleteLeads, bulkSendLeadsToCrm, createLead, exportLeads, generateEmailTemplate, getEmailTemplates, getPublicIntakeConfig, getRoutingConfig, listIntegrations, listLeads, rotatePublicIntakeConfig, saveEmailTemplate, updateRoutingConfig } from "../api";
import { AgentFlow } from "../components/AgentFlow";
import { AccountProfilePanel } from "../components/AccountProfilePanel";
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
  RoutingConfig,
  TimeSeriesAnalytics,
  WorkspaceEmailTemplate,
} from "../types";

export type AnalyticsBundle = {
  dashboard: DashboardAnalytics | null;
  channels: ChannelAnalytics | null;
  pipeline: PipelineAnalytics | null;
  revenue: RevenueAnalytics | null;
  reps: RepAnalytics | null;
  series: TimeSeriesAnalytics | null;
  agents: AgentActivity | null;
  review: ManualReviewAnalytics | null;
};

const analyticsPanelOptions = [
  { id: "channel-performance", label: "Channel performance" },
  { id: "lead-trajectory", label: "Lead trajectory" },
  { id: "revenue-by-channel", label: "Revenue by channel" },
  { id: "revenue-by-service", label: "Revenue by service" },
  { id: "representatives", label: "Representative performance" },
  { id: "revenue-trajectory", label: "Revenue trajectory" },
] as const;

type AnalyticsPanelId = (typeof analyticsPanelOptions)[number]["id"];

const dashboardWidgetOptions = [
  { id: "qualification-pulse", label: "Qualification pulse", group: "Overview" },
  { id: "total_leads", label: "Total leads", group: "Key metrics" },
  { id: "qualification_rate", label: "Qualification rate", group: "Key metrics" },
  { id: "hot_leads", label: "Hot leads", group: "Key metrics" },
  { id: "active_pipeline_value", label: "Active pipeline value", group: "Key metrics" },
  { id: "manual_review", label: "Manual review", group: "Key metrics" },
  { id: "average_routing_velocity_minutes", label: "Routing velocity", group: "Key metrics" },
  { id: "lead-intelligence-network", label: "Lead intelligence network", group: "Dashboard panels" },
  { id: "live-activity", label: "Live operational activity", group: "Dashboard panels" },
  { id: "attention-queue", label: "Attention queue", group: "Dashboard panels" },
  { id: "signal-velocity", label: "Signal velocity", group: "Dashboard panels" },
  { id: "qualification-mix", label: "Qualification mix", group: "Dashboard panels" },
  { id: "channel-intelligence", label: "Channel intelligence", group: "Dashboard panels" },
  { id: "pipeline-pulse", label: "Pipeline pulse", group: "Dashboard panels" },
] as const;

type DashboardWidgetId = (typeof dashboardWidgetOptions)[number]["id"];
type VisibilityOption<T extends string> = { id: T; label: string; group?: string };

function defaultSectionSelection<T extends string>(options: readonly { id: T }[]) {
  return new Set<T>(options.map((option) => option.id));
}

function loadSectionSelection<T extends string>(storageKey: string, options: readonly { id: T }[]) {
  const defaults = defaultSectionSelection(options);
  if (typeof window === "undefined") return defaults;
  try {
    const saved = window.localStorage.getItem(storageKey);
    if (!saved) return defaults;
    const parsed: unknown = JSON.parse(saved);
    if (!Array.isArray(parsed)) return defaults;
    const validIds = new Set<string>(options.map((option) => option.id));
    return new Set(parsed.filter((value): value is T => typeof value === "string" && validIds.has(value)));
  } catch {
    return defaults;
  }
}

function SectionVisibilityPicker<T extends string>({
  options,
  visible,
  description,
  showAllLabel,
  onToggle,
  onShowAll,
}: {
  options: readonly VisibilityOption<T>[];
  visible: ReadonlySet<T>;
  description: string;
  showAllLabel: string;
  onToggle: (id: T, checked: boolean) => void;
  onShowAll: () => void;
}) {
  const groups = Array.from(new Set(options.map((option) => option.group).filter((group): group is string => !!group)));
  const renderOptions = (items: readonly VisibilityOption<T>[], groupLabel: string) => (
    <div className="section-picker-options" role="group" aria-label={groupLabel}>
      {items.map((option) => (
        <label className="section-picker-option" key={option.id}>
          <input type="checkbox" checked={visible.has(option.id)} onChange={(event) => onToggle(option.id, event.target.checked)} />
          <span>{option.label}</span>
        </label>
      ))}
    </div>
  );

  return (
    <section className="section-picker panel" aria-labelledby="section-picker-title">
      <div className="section-picker-heading">
        <div><span className="eyebrow">Personal view</span><h2 id="section-picker-title">Choose what to see</h2><p>{description}</p></div>
        <button className="button-quiet" type="button" onClick={onShowAll} disabled={visible.size === options.length}>{showAllLabel}</button>
      </div>
      {groups.length ? (
        <div className="section-picker-groups">
          {groups.map((group) => (
            <fieldset className="section-picker-group" key={group}>
              <legend>{group}</legend>
              {renderOptions(options.filter((option) => option.group === group), group)}
            </fieldset>
          ))}
        </div>
      ) : renderOptions(options, "Available sections")}
      <small className="section-picker-note">Your selection is saved for your account on this device.</small>
    </section>
  );
}

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

const LEAD_PAGE_SIZE = 50;
const LEAD_SOURCE_OPTIONS = [
  "email",
  "website",
  "offline",
  "unknown",
  "purchased_list",
  "web",
  "phone_inquiry",
  "partner_referral",
  "clutch",
  "salesforce",
  "hubspot",
];

export const pretty = (value: string) =>
  value.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
const crmOrigin = (lead: Lead) => {
  const match = lead.source_detail?.match(/^crm-import:(salesforce|hubspot):/i);
  return match ? pretty(match[1]) : null;
};
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
  userId = "local",
}: {
  data: AnalyticsBundle | null;
  latestLead: Lead | null;
  onInspect: (id: string) => void;
  userId?: string;
}) {
  const preferenceKey = `corral.dashboard.widgets.v1:${userId}`;
  const [visibleWidgets, setVisibleWidgets] = useState<Set<DashboardWidgetId>>(() => loadSectionSelection(preferenceKey, dashboardWidgetOptions));
  useEffect(() => {
    try {
      window.localStorage.setItem(preferenceKey, JSON.stringify([...visibleWidgets]));
    } catch {
      // Dashboard preferences are optional when browser storage is unavailable.
    }
  }, [preferenceKey, visibleWidgets]);

  if (!data?.dashboard || !data.pipeline || !data.series || !data.channels) return <Unavailable />;
  const metricIds = new Set<string>(dashboardWidgetOptions.filter((option) => option.group === "Key metrics").map((option) => option.id));
  const metrics = data.dashboard.metrics.filter((metric) => metricIds.has(metric.key) && visibleWidgets.has(metric.key as DashboardWidgetId));
  const qualificationRate = metricValue(data.dashboard, "qualification_rate");
  const totalLeads = metricValue(data.dashboard, "total_leads");
  const hasCommandWidgets = visibleWidgets.has("live-activity") || visibleWidgets.has("attention-queue");
  const hasTrendWidgets = visibleWidgets.has("signal-velocity") || visibleWidgets.has("qualification-mix");
  const hasBreakdownWidgets = visibleWidgets.has("channel-intelligence") || visibleWidgets.has("pipeline-pulse");
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
          <span className="eyebrow">Executive signal field / {data.dashboard.metadata.range}</span>
          <h1>Revenue intelligence,<br /><em>in operational motion.</em></h1>
          <p>Every metric resolves to workspace-owned lead, decision and delivery evidence. No cross-workspace aggregation. No unverified revenue is presented as live.</p>
        </div>
        {visibleWidgets.has("qualification-pulse") && <div className="hero-pulse">
          <span>Qualification pulse</span>
          <strong>{qualificationRate}%</strong>
          <i><span style={{ transform: `scaleX(${qualificationRate / 100})` }} /></i>
          <small>{metricValue(data.dashboard, "sqls")} sales-qualified leads</small>
        </div>}
      </header>
      <SectionVisibilityPicker
        options={dashboardWidgetOptions}
        visible={visibleWidgets}
        description="Select the metrics and cards you want on your dashboard. Unchecked items are removed from the view."
        showAllLabel="Show all cards"
        onToggle={toggleWidget}
        onShowAll={() => setVisibleWidgets(defaultSectionSelection(dashboardWidgetOptions))}
      />
      {totalLeads === 0 && <section className="panel workspace-empty"><Empty title="No leads yet" text="Connect a source or add your first lead to get started." /></section>}
      {visibleWidgets.size > 0 ? (
        <>
          {metrics.length > 0 && <section className="metric-grid dashboard-metric-grid">{metrics.map((metric) => <MetricCard key={metric.key} metric={metric} />)}</section>}
          {visibleWidgets.has("lead-intelligence-network") && data.agents && <ResponsiveLeadIntelligenceNetwork pipeline={data.pipeline} activity={data.agents} lead={latestLead} />}
          {hasCommandWidgets && <section className="dashboard-command-grid">
            {visibleWidgets.has("live-activity") && (data.agents ? <Panel title="Live operational activity" eyebrow="Audited agent events" action={`${data.agents.activity.length} in range`}>
              <ActivityFeed data={data.agents} compact />
            </Panel> : <Panel title="Live operational activity" eyebrow="Audited agent events"><Empty title="Not available" text="Your workspace role does not include agent activity access." /></Panel>)}
            {visibleWidgets.has("attention-queue") && (data.review ? <Panel title="Attention queue" eyebrow="Human judgment required" action={`${data.review.total} open`}>
              {data.review.items.length ? (
                <div className="compact-leads">
                  {data.review.items.slice(0, 5).map((lead) => (
                    <LeadRow lead={lead} key={lead.id} onClick={() => onInspect(lead.id)} />
                  ))}
                </div>
              ) : <Empty title="No review debt" text="No leads need human review in this period." />}
            </Panel> : <Panel title="Attention queue" eyebrow="Human judgment required"><Empty title="Not available" text="Your workspace role does not include manual review access." /></Panel>)}
          </section>}
          {hasTrendWidgets && <section className="dashboard-grid">
            {visibleWidgets.has("signal-velocity") && <Panel title="Signal velocity" eyebrow="Lead volume over time" action="Server aggregated">
              <TrendChart values={data.series.points.map((point) => point.leads)} labels={data.series.points.map((point) => point.period)} />
            </Panel>}
            {visibleWidgets.has("qualification-mix") && <Panel title="Qualification mix" eyebrow="Decision bands">
              <Donut rows={data.dashboard.band_distribution.map((row) => ({ label: row.band, value: row.count }))} />
            </Panel>}
          </section>}
          {hasBreakdownWidgets && <section className="dashboard-grid">
            {visibleWidgets.has("channel-intelligence") && <Panel title="Channel intelligence" eyebrow="Quality + useful volume">
              <Bars
                rows={data.channels.channels.map((row) => ({
                  label: pretty(row.channel),
                  value: row.importance_index,
                  note: `${row.lead_volume} leads · ${row.average_score} avg score`,
                }))}
              />
            </Panel>}
            {visibleWidgets.has("pipeline-pulse") && <Panel title="Pipeline pulse" eyebrow="Active progression by stage">
              <Bars rows={data.pipeline.stages.map((stage) => ({ label: stage.label, value: stage.count, note: stage.value ? money(stage.value) : undefined }))} />
            </Panel>}
          </section>}
        </>
      ) : (
        <section className="section-picker-empty panel"><h2>No dashboard cards selected</h2><p>Choose one or more items above to add them back.</p></section>
      )}
    </div>
  );
}

export function LeadsView({
  selected,
  revision,
  onInspect,
  onClose,
  onCreated,
  onUpdated,
  onDeleted,
  canEdit = true,
  canExport = false,
  canCrmSync = false,
}: {
  selected: Lead | null;
  revision: number;
  onInspect: (id: string) => void;
  onClose: () => void;
  onCreated: (lead: Lead) => void;
  onUpdated: (lead: Lead) => void;
  onDeleted: (leadId: string) => void;
  canEdit?: boolean;
  canExport?: boolean;
  canCrmSync?: boolean;
}) {
  const [search, setSearch] = useState("");
  const [band, setBand] = useState("all");
  const [source, setSource] = useState("all");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [rows, setRows] = useState<Lead[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(0);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);
  const hasLoadedRows = useRef(false);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [showExport, setShowExport] = useState(false);
  const [showForm, setShowForm] = useState(false);
  const [crmNotice, setCrmNotice] = useState<string | null>(null);
  const [crmProviders, setCrmProviders] = useState<string[]>([]);
  const [bulkProvider, setBulkProvider] = useState("");
  useEffect(() => {
    if (!selected) return;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.body.style.overflow = previousOverflow;
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [selected, onClose]);
  useEffect(() => {
    if (!canCrmSync) return;
    let active = true;
    void listIntegrations().then((cards) => {
      if (!active) return;
      const providers = cards.filter((card) =>
        card.category.toLowerCase() === "crm" && card.directionality.manual_outbound &&
        card.connection?.is_active && card.connection.verification_status === "verified"
      ).map((card) => card.provider);
      setCrmProviders(providers);
      setBulkProvider((current) => providers.includes(current) ? current : providers.length === 1 ? providers[0] : "");
    }).catch(() => { if (active) setCrmProviders([]); });
    return () => { active = false; };
  }, [canCrmSync]);
  useEffect(() => {
    setPage(0);
    setSelectedIds(new Set());
  }, [search, band, source, dateFrom, dateTo]);
  useEffect(() => {
    const timer = window.setInterval(() => {
      if (document.visibilityState === "visible") setRefreshKey((value) => value + 1);
    }, 5000);
    return () => window.clearInterval(timer);
  }, []);
  useEffect(() => {
    let active = true;
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      if (!hasLoadedRows.current) setLoading(true);
      setLoadError(null);
      void listLeads({
        limit: LEAD_PAGE_SIZE,
        offset: page * LEAD_PAGE_SIZE,
        search: search.trim() || undefined,
        source: source === "all" ? undefined : source,
        qualification_band: band === "all" ? undefined : band,
        date_from: dateFrom || undefined,
        date_to: dateTo || undefined,
      }, controller.signal)
        .then((result) => {
          if (!active) return;
          setRows(result.items);
          setTotal(result.total);
          hasLoadedRows.current = true;
        })
        .catch((reason) => {
          if (active && !controller.signal.aborted) {
            setLoadError(reason instanceof Error ? reason.message : "Unable to load leads");
          }
        })
        .finally(() => {
          if (active) setLoading(false);
        });
    }, 180);
    return () => {
      active = false;
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [search, band, source, dateFrom, dateTo, page, refreshKey, revision]);

  const visibleSelected = rows.filter((lead) => selectedIds.has(lead.id)).length;
  const toggleSelected = (id: string) => setSelectedIds((current) => {
    const next = new Set(current);
    if (next.has(id)) next.delete(id); else next.add(id);
    return next;
  });
  const selectVisible = () => setSelectedIds((current) => {
    const next = new Set(current);
    if (visibleSelected === rows.length) rows.forEach((lead) => next.delete(lead.id));
    else rows.forEach((lead) => next.add(lead.id));
    return next;
  });
  const pageCount = Math.max(1, Math.ceil(total / LEAD_PAGE_SIZE));
  const firstRecord = total === 0 ? 0 : page * LEAD_PAGE_SIZE + 1;
  const lastRecord = Math.min((page + 1) * LEAD_PAGE_SIZE, total);
  const hasFilters = Boolean(search.trim() || band !== "all" || source !== "all" || dateFrom || dateTo);
  const deleteSelected = async () => {
    if (!selectedIds.size || !window.confirm(`Delete ${selectedIds.size} selected lead${selectedIds.size === 1 ? "" : "s"}? A later matching inquiry can be imported again.`)) return;
    try {
      const result = await bulkDeleteLeads([...selectedIds]);
      setSelectedIds(new Set());
      setCrmNotice(`${result.deleted} lead${result.deleted === 1 ? "" : "s"} deleted. Later matching inquiries can be imported again.`);
      setRefreshKey((value) => value + 1);
      void onDeleted("bulk");
    } catch (error) {
      setCrmNotice(error instanceof Error ? error.message : "Unable to delete selected leads");
    }
  };

  return (
    <div className="page-view operational-page">
      <header className="page-hero compact-hero">
        <div><span className="eyebrow">Lead intelligence archive</span><h1>Every decision,<br /><em>traceable to evidence.</em></h1><p>Search, filter and inspect the complete journey from original signal to connected-system delivery.</p></div>
        <div className="lead-hero-actions">
          {canExport && <button className="secondary-button" onClick={() => setShowExport(true)}>Export</button>}
          {canEdit && <button className="primary-button" onClick={() => setShowForm((current) => !current)}>{showForm ? "Close intake" : "+ Submit lead"}</button>}
        </div>
      </header>
      {showForm && <LeadForm onCreated={(lead) => { onCreated(lead); setRefreshKey((value) => value + 1); }} />}
      <section className="lead-browser panel" data-tour="leads">
        <div className="lead-toolbar">
          <label><span className="sr-only">Search leads</span><input type="search" placeholder="Search company, contact, service…" value={search} onChange={(event) => setSearch(event.target.value)} /></label>
          <select aria-label="Qualification band" value={band} onChange={(event) => setBand(event.target.value)}><option value="all">All qualification bands</option>{["hot", "warm", "needs_qualifying", "nurture", "disqualify"].map((item) => <option value={item} key={item}>{qualificationLabel(item)}</option>)}</select>
           <select aria-label="Lead source" value={source} onChange={(event) => setSource(event.target.value)}><option value="all">All channels</option>{LEAD_SOURCE_OPTIONS.map((item) => <option key={item}>{item}</option>)}</select>
          <label className="date-filter"><span className="sr-only">Received from</span><input aria-label="Received from" type="date" value={dateFrom} onChange={(event) => setDateFrom(event.target.value)} /></label>
          <label className="date-filter"><span className="sr-only">Received to</span><input aria-label="Received to" type="date" value={dateTo} onChange={(event) => setDateTo(event.target.value)} /></label>
           <span className="live-feed-status"><i />{total.toLocaleString()} records · live</span>
         </div>
         {rows.length > 0 && <div className="lead-selection-bar"><label><input type="checkbox" checked={rows.length > 0 && visibleSelected === rows.length} onChange={selectVisible} /> Select visible</label><span>{selectedIds.size} selected</span>{canCrmSync && <><label>Transfer to <select aria-label="Bulk CRM provider" value={bulkProvider} onChange={(event) => setBulkProvider(event.target.value)}><option value="">Select CRM</option>{crmProviders.map((provider) => <option key={provider} value={provider}>{provider === "hubspot" ? "HubSpot" : pretty(provider)}</option>)}</select></label><button className="button-quiet" disabled={!selectedIds.size || !bulkProvider} onClick={async () => { if (!window.confirm(`Transfer or update ${selectedIds.size} selected leads in ${bulkProvider === "hubspot" ? "HubSpot" : pretty(bulkProvider)}?`)) return; try { const result = await bulkSendLeadsToCrm([...selectedIds], bulkProvider); const providerLabel = bulkProvider === "hubspot" ? "HubSpot" : pretty(bulkProvider); const firstFailure = result.results.find((item) => item.status !== "synced" && item.error)?.error; setCrmNotice(`${result.succeeded} ${providerLabel} transfers completed; ${result.failed} skipped or failed.${firstFailure ? ` ${firstFailure}` : ""}`); setRefreshKey((value) => value + 1); } catch (error) { setCrmNotice(error instanceof Error ? error.message : "Bulk CRM transfer failed"); } }}>Transfer selected</button></>}{canEdit && <button className="danger-button" disabled={!selectedIds.size} onClick={() => void deleteSelected()}>Delete selected</button>}<button className="button-quiet" disabled={!selectedIds.size} onClick={() => setSelectedIds(new Set())}>Clear selection</button></div>}
         {crmNotice && <div className="notice" role="status">{crmNotice}</div>}
         {loadError && <div className="notice" role="alert"><span>{loadError}</span><button className="button-quiet" onClick={() => setRefreshKey((value) => value + 1)}>Retry</button></div>}
         <div className="lead-table-wrap" role="region" aria-label="Lead results table; scroll horizontally to view every column" tabIndex={0} aria-busy={loading}>
           <table className="lead-table">
             <thead><tr><th aria-label="Select leads">Select</th><th>Lead</th><th>Source</th><th>Score</th><th>Routing</th><th>Representative</th><th>CRM</th><th>Received</th></tr></thead>
             <tbody>{rows.map((lead) => <LeadTableRow key={lead.id} lead={lead} selected={selected?.id === lead.id} checked={selectedIds.has(lead.id)} onToggle={toggleSelected} onInspect={onInspect} />)}</tbody>
           </table>
           {loading && <div className="table-state" role="status">Loading workspace leads…</div>}
           {!loading && rows.length === 0 && <Empty title={hasFilters ? "No matching leads" : "No leads yet"} text={hasFilters ? "Adjust the filters to widen your search." : "Connect a source or add your first lead to get started."} />}
         </div>
         {total > LEAD_PAGE_SIZE && <nav className="lead-pagination" aria-label="Lead list pages"><span>Showing {firstRecord.toLocaleString()}–{lastRecord.toLocaleString()} of {total.toLocaleString()}</span><div><button className="button-quiet" disabled={page === 0 || loading} onClick={() => setPage((value) => Math.max(0, value - 1))}>Previous</button><span>Page {page + 1} of {pageCount}</span><button className="button-quiet" disabled={page >= pageCount - 1 || loading} onClick={() => setPage((value) => Math.min(pageCount - 1, value + 1))}>Next</button></div></nav>}
       </section>
       {showExport && <ExportDialog count={total} selectedCount={selectedIds.size} filters={{ search, band, source, dateFrom, dateTo }} onClose={() => setShowExport(false)} selectedIds={[...selectedIds]} />}
      {selected && createPortal(
        <div className="lead-drawer-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
          <section className="lead-drawer" role="dialog" aria-modal="true" aria-label={`${selected.company_name} lead details`}>
            <div className="lead-drawer-toolbar"><span>Lead details · {selected.source}</span><button className="modal-close" type="button" autoFocus aria-label="Close lead details" onClick={onClose}>×</button></div>
            <LeadDetail key={selected.id} lead={selected} canCrmSync={canCrmSync} canEdit={canEdit} onUpdated={(lead) => { onUpdated(lead); setRows((current) => current.map((item) => item.id === lead.id ? lead : item)); }} onDeleted={(leadId) => { onDeleted(leadId); setRows((current) => current.filter((item) => item.id !== leadId)); onClose(); setRefreshKey((value) => value + 1); }} />
          </section>
        </div>, document.body,
      )}
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
  const totalLeads = data.stages.find((stage) => stage.stage === "received")?.count ?? 0;
  return (
    <div className="page-view operational-page">
      <header className="page-hero compact-hero"><div><span className="eyebrow">Commercial motion map</span><h1>Pipeline, without<br /><em>the blind spots.</em></h1><p>Inspect every stage and the qualification state that shaped progression.</p></div><div className="pipeline-total"><span>Active pipeline</span><strong>{money(data.stages.find((stage) => stage.stage === "opportunity")?.value ?? 0)}</strong></div></header>
      {totalLeads === 0 && <section className="panel workspace-empty"><Empty title="Pipeline is ready" text="Qualified leads will appear here as connected sources begin sending records." /></section>}
       <section className="pipeline-track panel" data-tour="pipeline">{data.stages.map((stage, index) => <button key={stage.stage} className={active === stage.stage ? "active" : ""} aria-pressed={active === stage.stage} onClick={() => setActive(stage.stage)}><span>{String(index + 1).padStart(2, "0")}</span><b>{stage.label}</b><strong>{stage.count}</strong><small>{stage.value ? money(stage.value) : "—"}</small><span className="pipeline-lead-dots" aria-hidden="true">{stage.leads.slice(0, 4).map((lead) => <i key={lead.id} />)}</span>{index < data.stages.length - 1 && <i className="pipeline-link" />}</button>)}</section>
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
      <Panel title="Active agent insights" eyebrow="Read-only · selected date range">
        <div className="agent-insight-grid">
          {data.insights.map((insight) => <article className="agent-insight-card" key={insight.agent}>
            <header className="agent-insight-card-header"><span className="eyebrow">{insight.agent}</span><span className="agent-insight-mode">Read-only</span></header>
            <p>{insight.summary}</p>
            <dl className="agent-insight-metrics">{insight.metrics.map((metric) => <div key={metric.key}><dt>{metric.label}</dt><dd>{metric.value}</dd></div>)}</dl>
            {insight.details.length > 0 && <ul>{insight.details.map((detail) => <li key={detail}>{detail}</li>)}</ul>}
          </article>)}
        </div>
      </Panel>
      <section className="agent-layout">
        <Panel title="Capability registry" eyebrow="Implemented vs reserved"><div className="capability-grid">{data.capabilities.map((item) => <div className={item.status} key={item.name}><i /> <b>{item.name}</b><span>{item.status}</span></div>)}</div></Panel>
        <Panel title="Recent runs" eyebrow="Workspace audit stream" action={`${data.activity.length} events`}><ActivityFeed data={data} /></Panel>
      </section>
    </div>
  );
}

export function AnalyticsView({ data, userId = "local" }: { data: AnalyticsBundle | null; userId?: string }) {
  const preferenceKey = `corral.analytics.panels.v1:${userId}`;
  const [visiblePanels, setVisiblePanels] = useState<Set<AnalyticsPanelId>>(() => loadSectionSelection(preferenceKey, analyticsPanelOptions));
  useEffect(() => {
    try {
      window.localStorage.setItem(preferenceKey, JSON.stringify([...visiblePanels]));
    } catch {
      // Analytics preferences are optional when browser storage is unavailable.
    }
  }, [preferenceKey, visiblePanels]);

  if (!data?.dashboard || !data.channels || !data.series || !data.revenue || !data.reps) return <Unavailable />;
  const provenance = data.revenue.data_status === "demo"
    ? "Illustrative values—not production revenue"
    : data.revenue.data_status === "crm"
      ? "CRM-derived outcomes"
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
    <div className="page-view">
      <header className="page-hero compact-hero"><div><span className="eyebrow">Workspace analytics</span><h1>Quality, velocity<br /><em>and commercial value.</em></h1><p>Time-bounded metrics generated server-side from workspace-owned records.</p></div><div className={`data-provenance ${data.revenue.data_status}`}><span>Revenue provenance</span><b>{pretty(data.revenue.data_status)}</b><small>{provenance}</small></div></header>
      {totalLeads === 0 && <section className="panel workspace-empty"><Empty title="No analytics yet" text="Metrics remain at zero until real lead activity is recorded." /></section>}
      <SectionVisibilityPicker
        options={analyticsPanelOptions}
        visible={visiblePanels}
        description="Select the analytics sections you want. Only selected sections appear below."
        showAllLabel="Show all sections"
        onToggle={togglePanel}
        onShowAll={() => setVisiblePanels(defaultSectionSelection(analyticsPanelOptions))}
      />
      {visiblePanels.size > 0 ? (
        <section className={`analytics-grid analytics-motion${visiblePanels.size === 1 ? " analytics-grid-single" : ""}`} data-tour="analytics">
          {visiblePanels.has("channel-performance") && <div className="analytics-panel-wide"><Panel title="Channel performance" eyebrow="Volume × quality × conversion"><div className="channel-table"><div className="channel-head"><span>Channel</span><span>Volume</span><span>Qual.</span><span>SQL</span><span>Won</span><span>Revenue</span><span>Importance</span></div>{data.channels.channels.map((row) => <div key={row.channel}><b>{pretty(row.channel)}</b><span>{row.lead_volume} · {row.share_percent}%</span><span>{row.qualification_rate}%</span><span>{row.sql_conversion_rate}%</span><span>{row.closed_won_conversion_rate}%</span><span>{money(row.revenue)}</span><em>{row.importance_index}</em></div>)}</div></Panel></div>}
          {visiblePanels.has("lead-trajectory") && <Panel title="Lead trajectory" eyebrow="Volume by period"><TrendChart values={data.series.points.map((point) => point.leads)} labels={data.series.points.map((point) => point.period)} /></Panel>}
          {visiblePanels.has("revenue-by-channel") && <Panel title="Revenue by channel" eyebrow={`${pretty(data.revenue.data_status)} data`}><Bars rows={data.revenue.by_channel.map((row) => ({ label: pretty(row.label), value: row.value }))} formatter={money} /></Panel>}
          {visiblePanels.has("revenue-by-service") && <Panel title="Revenue by service" eyebrow="Closed / won attribution"><Bars rows={data.revenue.by_service.map((row) => ({ label: row.label, value: row.value }))} formatter={money} /></Panel>}
          {visiblePanels.has("representatives") && <div className="analytics-panel-wide"><Panel title="Representative performance" eyebrow="Context, not leaderboard"><div className="rep-table">{data.reps.reps.map((rep) => <div key={rep.rep_name}><span className="rep-avatar">{rep.rep_name.split(" ").map((word) => word[0]).join("")}</span><span><b>{rep.rep_name}</b><small>{rep.assigned_leads} assigned · {rep.qualified_leads} qualified</small></span><span><b>{rep.closed_leads}</b><small>closed</small></span><span><b>{rep.conversion_rate}%</b><small>conversion</small></span><span><b>{money(rep.revenue)}</b><small>revenue</small></span><span><b>{rep.average_response_minutes ?? "—"}</b><small>avg min</small></span></div>)}</div></Panel></div>}
          {visiblePanels.has("revenue-trajectory") && <Panel title="Revenue trajectory" eyebrow="Closed / won over time"><TrendChart values={data.revenue.over_time.map((point) => point.value)} labels={data.revenue.over_time.map((point) => point.period)} /></Panel>}
        </section>
      ) : (
        <section className="section-picker-empty panel"><h2>No sections selected</h2><p>Choose at least one analytics section above to display it here.</p></section>
      )}
    </div>
  );
}

export function ManualReviewView({ data, onInspect }: { data: ManualReviewAnalytics | null; onInspect: (id: string) => void }) {
  if (!data) return <Unavailable />;
  return (
    <div className="page-view operational-page">
      <header className="page-hero compact-hero"><div><span className="eyebrow">Human judgment queue</span><h1>Uncertainty made<br /><em>safe and inspectable.</em></h1><p>Provider failures, low confidence and contradictory evidence pause here. The system does not invent a resolution.</p></div><div className="review-total"><strong>{data.total}</strong><span>open review{data.total === 1 ? "" : "s"}</span></div></header>
       <section className="review-grid" data-tour="review">{data.items.length ? data.items.map((lead) => <article className="review-card panel" key={lead.id}><div className="review-card-head"><span className={`source-tag source-${lead.source}`}>{pretty(lead.source)}</span><div className="review-qualification"><b>{lead.score}</b><QualificationBadge value={lead.band} manualReview /></div></div><h2>{lead.company}</h2><p>{lead.name} · {lead.service}</p><div className="review-reasons">{lead.review_reasons.map((reason) => <span key={reason}>{reason}</span>)}</div><div><small>Current queue</small><b>{pretty(lead.routing_status)}</b></div><button onClick={() => onInspect(lead.id)}>Inspect evidence →</button></article>) : <Empty title="Manual review is clear" text="No leads need human judgment in this period." />}</section>
    </div>
  );
}

export function SettingsView({ metadata, onIntegrations, onTeam, onIcp, canViewTeam, canViewIcp, canViewRouting, canManageRouting, canViewPublicIntake, canRotatePublicIntake, canManageEmail }: { metadata?: AnalyticsMetadata; onIntegrations: () => void; onTeam: () => void; onIcp: () => void; canViewTeam: boolean; canViewIcp: boolean; canViewRouting: boolean; canManageRouting: boolean; canViewPublicIntake: boolean; canRotatePublicIntake: boolean; canManageEmail: boolean }) {
  return (
    <div className="page-view operational-page">
      <header className="page-hero compact-hero"><div><span className="eyebrow">Workspace control plane</span><h1>Clear boundaries,<br /><em>durable context.</em></h1><p>Workspace identity, data provenance and team access stay visible here. Choose a control surface without losing the workspace context.</p></div></header>
      <section className="settings-grid" data-tour="settings">
        <AccountProfilePanel />
        <Panel title="Workspace" eyebrow="Tenant boundary"><div className="settings-facts"><Fact label="Name" value={metadata?.workspace_name || "Unavailable"} /><Fact label="Workspace ID" value={metadata?.workspace_id || "Unavailable"} /><Fact label="Data mode" value={pretty(metadata?.data_mode || "unknown")} /><Fact label="Analytics freshness" value={metadata ? new Date(metadata.generated_at).toLocaleString() : "Unavailable"} /></div></Panel>
        <Panel title="External systems" eyebrow="Provider connections"><p className="panel-copy">Manage workspace-owned email, CRM and notification connections. Stored credentials are represented only by safe state metadata.</p><button className="primary-button" onClick={onIntegrations}>Open Integration Center →</button></Panel>
        <Panel title="Users & permissions" eyebrow="Workspace access"><p className="panel-copy">Invite users, review roles and make deliberate per-person permission changes. Backend authorization remains the source of truth for every action.</p><button className="primary-button" disabled={!canViewTeam} onClick={onTeam}>{canViewTeam ? "Open Users →" : "Members view permission required"}</button></Panel>
        <RoutingWeightsPanel canView={canViewRouting} canManage={canManageRouting} />
        <PublicIntakePanel canView={canViewPublicIntake} canRotate={canRotatePublicIntake} />
        <EmailTemplatesPanel canManage={canManageEmail} />
        <Panel title="Ideal Customer Profile" eyebrow="Qualification policy"><p className="panel-copy">Build, validate and safely test the workspace-specific 40-point ICP. Published versions remain immutable and historical scores retain their exact policy reference.</p><button className="primary-button" disabled={!canViewIcp} onClick={onIcp}>{canViewIcp ? "Open ICP Builder →" : "ICP view permission required"}</button></Panel>
        <Panel title="Data range" eyebrow="Analytics contract"><p className="panel-copy">Weekly, monthly and annual windows are available. Period-over-period change is withheld when the prior period has no valid denominator.</p></Panel>
        <Panel title="Product state" eyebrow="Release readiness"><p className="panel-copy">Analytics, team controls, workspace-specific ICP qualification, secure exports and certified provider workflows are active.</p></Panel>
      </section>
    </div>
  );
}

function EmailTemplatesPanel({ canManage }: { canManage: boolean }) {
  const [templates, setTemplates] = useState<WorkspaceEmailTemplate[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  useEffect(() => {
    if (!canManage) return;
    void getEmailTemplates().then(setTemplates).catch((error) => setNotice(error instanceof Error ? error.message : "Unable to load email templates"));
  }, [canManage]);
  const update = (kind: WorkspaceEmailTemplate["kind"], field: "subject" | "body" | "generation_method", value: string) =>
    setTemplates((current) => current.map((item) => item.kind === kind ? { ...item, [field]: value } : item));
  const save = async (template: WorkspaceEmailTemplate) => {
    setBusy(`${template.kind}-save`); setNotice(null);
    try {
      const saved = await saveEmailTemplate(template.kind, { subject: template.subject, body: template.body, generation_method: template.generation_method });
      setTemplates((current) => current.map((item) => item.kind === saved.kind ? saved : item));
      setNotice(`${template.kind === "confirmation" ? "Confirmation" : "Follow-up"} email sample saved.`);
    } catch (error) { setNotice(error instanceof Error ? error.message : "Unable to save email sample"); }
    finally { setBusy(null); }
  };
  const generate = async (kind: WorkspaceEmailTemplate["kind"]) => {
    setBusy(`${kind}-ai`); setNotice(null);
    try {
      const generated = await generateEmailTemplate(kind);
      setTemplates((current) => current.map((item) => item.kind === kind ? generated : item));
      setNotice("AI sample generated. Review it, then save to use it.");
    } catch (error) { setNotice(error instanceof Error ? error.message : "Unable to generate email sample"); }
    finally { setBusy(null); }
  };
  if (!canManage) return <Panel title="Email templates" eyebrow="Workspace messaging"><p className="panel-copy">Integration management permission is required to configure email templates.</p></Panel>;
  const preview = (value: string) => value.replaceAll("{{lead_name}}", "Alex").replaceAll("{{company_name}}", "Northstar Labs").replaceAll("{{product_or_service}}", "lead workflow automation").replaceAll("{{job_title}}", "Sales Director");
  return <Panel title="Email templates" eyebrow="Workspace messaging"><p className="panel-copy">Edit sample emails here. Confirmation is sent automatically when a new lead arrives. Follow-up is never sent automatically; a teammate reviews the lead-specific draft and clicks Send in Corral.</p><div className="email-template-grid">{templates.map((template) => {
    const confirmation = template.kind === "confirmation";
    return <article className="email-template-card" key={template.kind}><div className="email-template-heading"><h3>{confirmation ? "New-lead confirmation" : "Lead follow-up"}</h3><span>{confirmation ? "Automatic on intake" : "Human-send only"}</span></div><p className="panel-copy">{confirmation ? "Acknowledges receipt using this sample." : "Used as the workspace style for lead-specific drafts."} Available fields: <code>{"{{lead_name}}"}</code>, <code>{"{{company_name}}"}</code>, <code>{"{{product_or_service}}"}</code>, <code>{"{{job_title}}"}</code>.</p><label className="email-template-field">Sample subject<input value={template.subject} onChange={(event) => update(template.kind, "subject", event.target.value)} disabled={busy !== null} /></label><label className="email-template-field">Sample body<textarea rows={8} value={template.body} onChange={(event) => update(template.kind, "body", event.target.value)} disabled={busy !== null} /></label><div className="email-template-actions"><label>Draft style<select value={template.generation_method} onChange={(event) => update(template.kind, "generation_method", event.target.value)} disabled={busy !== null}><option value="manual">Use my saved sample</option><option value="ai">AI personalize from my sample</option></select></label><button className="button-quiet" disabled={busy !== null} onClick={() => void generate(template.kind)}>{busy === `${template.kind}-ai` ? "Generating…" : "Generate sample with AI"}</button><button className="primary-button" disabled={busy !== null || !template.subject.trim() || !template.body.trim()} onClick={() => void save(template)}>{busy === `${template.kind}-save` ? "Saving…" : "Save sample"}</button></div><div className="email-template-preview"><small>PREVIEW</small><b>{preview(template.subject)}</b><p>{preview(template.body)}</p></div></article>;
  })}</div>{notice && <div className="notice" role="status">{notice}</div>}</Panel>;
}

function routingPercentages(members: RoutingConfig["members"]) {
  if (!members.length) return {};
  const weights = members.map((member) => Number.isFinite(member.weight) && member.weight > 0 ? member.weight : 1);
  const totalWeight = weights.reduce((sum, weight) => sum + weight, 0);
  let assigned = 0;
  return Object.fromEntries(members.map((member, index) => {
    const share = index === members.length - 1
      ? Math.round((100 - assigned) * 100) / 100
      : Math.round((weights[index] / totalWeight) * 10000) / 100;
    assigned += share;
    return [member.user_id, share];
  }));
}

function RoutingWeightsPanel({ canView, canManage }: { canView: boolean; canManage: boolean }) {
  const [config, setConfig] = useState<RoutingConfig | null>(null);
  const [percentages, setPercentages] = useState<Record<string, number>>({});
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (!canView) return;
    void getRoutingConfig().then((value) => {
      setConfig(value);
      setPercentages(routingPercentages(value.members));
    }).catch((error) => setNotice(error instanceof Error ? error.message : "Unable to load routing distribution"));
  }, [canView]);
  const totalPercentage = config?.members.reduce(
    (total, member) => total + (percentages[member.user_id] ?? 0),
    0,
  ) ?? 0;
  const percentagesValid = Boolean(config?.members.length)
    && config!.members.every((member) => {
      const percentage = percentages[member.user_id];
      return Number.isFinite(percentage) && percentage >= 0.01 && percentage <= 100;
    })
    && Math.abs(totalPercentage - 100) < 0.005;
  const save = async () => {
    setBusy(true); setNotice(null);
    try {
      const value = await updateRoutingConfig(percentages);
      setConfig(value);
      setPercentages(routingPercentages(value.members));
      setNotice("Routing percentages saved. New leads will use the updated shares.");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Unable to save routing ratios");
    } finally { setBusy(false); }
  };
  if (!canView) return <Panel title="Routing distribution" eyebrow="Workspace assignment"><p className="panel-copy">Routing visibility permission required.</p></Panel>;
  return (
    <Panel title="Routing distribution" eyebrow="Workspace assignment">
      <p className="panel-copy">Set each eligible member’s share as a percentage; all shares must total 100%. New leads are routed by relative workload, and existing assignments are unchanged.</p>
      {config?.members.length ? (
        <div className="routing-ratio-list">
          {config.members.map((member) => (
            <div className="routing-ratio-row" key={member.user_id}>
              <div>
                <b>{member.display_name}</b>
                <small>{member.role.replaceAll("_", " ")} · {member.open_leads} open lead{member.open_leads === 1 ? "" : "s"}</small>
              </div>
              <label>
                <span>Share</span>
                <div className="routing-percentage-control">
                  <input
                    aria-label={`Routing percentage for ${member.display_name}`}
                    type="number"
                    min="0.01"
                    max="100"
                    step="0.01"
                    disabled={!canManage || busy}
                    value={percentages[member.user_id] ?? 0}
                    onChange={(event) => {
                      setNotice(null);
                      setPercentages((current) => ({
                        ...current,
                        [member.user_id]: Number(event.target.value),
                      }));
                    }}
                  />
                  <b aria-hidden="true">%</b>
                </div>
              </label>
            </div>
          ))}
        </div>
      ) : <p className="panel-copy">No active workspace member with lead assignment permission is available.</p>}
      {config?.members.length ? (
        <div className={`routing-percent-total ${percentagesValid ? "valid" : "invalid"}`} role="status">
          <span>Total share</span>
          <b>{totalPercentage.toFixed(2)}%</b>
          <small>{percentagesValid ? "Ready to save" : "Shares must total 100%"}</small>
        </div>
      ) : null}
      {canManage && config?.members.length ? (
        <button className="primary-button" disabled={busy || !percentagesValid} onClick={() => void save()}>
          {busy ? "Saving…" : "Save percentages"}
        </button>
      ) : null}
      {notice && <div className="notice" role="status">{notice}</div>}
    </Panel>
  );
}

function PublicIntakePanel({ canView, canRotate }: { canView: boolean; canRotate: boolean }) {
  const [url, setUrl] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const publicIntakeBaseUrl = import.meta.env.VITE_PUBLIC_INTAKE_BASE_URL?.replace(/\/$/, "");
  const displayUrl = (value: string) => publicIntakeBaseUrl
    ? `${publicIntakeBaseUrl}${new URL(value).pathname}`
    : value;
  const embedCode = url ? `<form action="${url}" method="post">\n  <input name="full_name" required placeholder="Your name">\n  <input type="email" name="email" required placeholder="Work email">\n  <input name="company_name" required placeholder="Company">\n  <input name="product_or_service" required placeholder="What do you need?">\n  <textarea name="message" required placeholder="Tell us about your inquiry"></textarea>\n  <button type="submit">Send inquiry</button>\n</form>` : "";
  useEffect(() => {
    if (!canView) return;
    void getPublicIntakeConfig().then((value) => setUrl(displayUrl(value.url))).catch((error) => setNotice(error instanceof Error ? error.message : "Unable to load website form URL"));
  }, [canView]);
  const copy = async () => {
    if (!url) return;
    try { await navigator.clipboard.writeText(url); setNotice("Public form URL copied."); }
    catch { setNotice("Copy was blocked; select the URL and copy it manually."); }
  };
  const rotate = async () => {
    if (!window.confirm("Rotate this URL? The old website form URL will stop accepting leads.")) return;
    setBusy(true); setNotice(null);
    try { const value = await rotatePublicIntakeConfig(); setUrl(displayUrl(value.url)); setNotice("New public form URL generated. Update your website form action."); }
    catch (error) { setNotice(error instanceof Error ? error.message : "Unable to rotate public form URL"); }
    finally { setBusy(false); }
  };
  if (!canView) return <Panel title="Website lead form" eyebrow="Public intake"><p className="panel-copy">Lead view permission required.</p></Panel>;
  const copyEmbed = async () => {
    if (!embedCode) return;
    try { await navigator.clipboard.writeText(embedCode); setNotice("Website form HTML copied."); }
    catch { setNotice("Copy was blocked; select the form HTML manually."); }
  };
  return <Panel title="Website lead form" eyebrow="Public intake"><p className="panel-copy">Send your website form to this URL. Each submission enters analysis, qualification, routing, Slack notification and CRM workflows automatically.</p><div className="public-intake-url"><input aria-label="Public website lead form URL" readOnly value={url || "Generating secure URL…"} /><button className="button-quiet" disabled={!url} onClick={() => void copy()}>Copy URL</button></div><p className="panel-copy">Email is included and required so the connected Gmail account can send an automatic confirmation to the lead. Required fields: <code>full_name</code>, <code>email</code>, <code>company_name</code>, <code>product_or_service</code>, and <code>message</code>.</p>{url && <><textarea className="embed-code" aria-label="Website form HTML" readOnly value={embedCode} /><button className="button-quiet" onClick={() => void copyEmbed()}>Copy form HTML</button></>}{canRotate && <button className="button-quiet" disabled={busy} onClick={() => void rotate()}>{busy ? "Rotating…" : "Rotate URL"}</button>}{notice && <div className="notice" role="status">{notice}</div>}</Panel>;
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
  return <NetworkDomFallback pipeline={pipeline} activity={activity} lead={lead} />;
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
  const origin = crmOrigin(lead);
  return <tr className={selected ? "selected" : ""} onClick={() => onInspect(lead.id)} tabIndex={0} onKeyDown={(event) => {
    if (event.key === "Enter" || event.key === " ") { event.preventDefault(); onInspect(lead.id); }
  }}>
    <td className="lead-select-cell"><input type="checkbox" aria-label={`Select ${lead.full_name}`} checked={checked} onChange={() => onToggle(lead.id)} onClick={(event) => event.stopPropagation()} /></td>
    <td><b>{lead.full_name}</b><span>{lead.company_name}</span></td>
    <td><span className={`source-tag source-${origin?.toLowerCase() || lead.source}`}>{origin || pretty(lead.source)}</span><small>{origin ? "CRM import" : lead.source_detail || "Original intake"}</small></td>
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
