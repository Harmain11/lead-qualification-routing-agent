import { useEffect, useState } from "react";

import {
  ACTIVE_WORKSPACE_ID,
  getAgentActivity,
  getChannelAnalytics,
  getDashboard,
  getLead,
  getManualReview,
  getPipelineAnalytics,
  getRepAnalytics,
  getRevenueAnalytics,
  getTimeSeriesAnalytics,
  listLeads,
} from "./api";
import type { Lead, TimeRange } from "./types";
import { IntegrationsView } from "./views/IntegrationsView";
import {
  AgentsView,
  AnalyticsView,
  DashboardView,
  LeadsView,
  ManualReviewView,
  PipelineView,
  SettingsView,
} from "./views/ProductViews";
import type { AnalyticsBundle } from "./views/ProductViews";

type View =
  | "dashboard"
  | "leads"
  | "pipeline"
  | "agents"
  | "analytics"
  | "integrations"
  | "review"
  | "settings";

const navigation: Array<{ id: View; label: string; glyph: string; group?: string }> = [
  { id: "dashboard", label: "Dashboard", glyph: "⌁" },
  { id: "leads", label: "Leads", glyph: "◈" },
  { id: "pipeline", label: "Pipeline", glyph: "⇢" },
  { id: "agents", label: "Agents / AI Activity", glyph: "✦" },
  { id: "analytics", label: "Analytics", glyph: "⌇" },
  { id: "review", label: "Manual Review", glyph: "!", group: "Control" },
  { id: "integrations", label: "Integrations", glyph: "∞" },
  { id: "settings", label: "Settings", glyph: "◇" },
];

function App() {
  const [view, setView] = useState<View>("dashboard");
  const [range, setRange] = useState<TimeRange>("monthly");
  const [data, setData] = useState<AnalyticsBundle | null>(null);
  const [leads, setLeads] = useState<Lead[]>([]);
  const [selectedLead, setSelectedLead] = useState<Lead | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [navOpen, setNavOpen] = useState(false);

  async function refresh(activeRange = range) {
    setLoading(true);
    setError(null);
    try {
      const [dashboard, channels, pipeline, revenue, reps, series, agents, review, leadRows] =
        await Promise.all([
          getDashboard(activeRange),
          getChannelAnalytics(activeRange),
          getPipelineAnalytics(activeRange),
          getRevenueAnalytics(activeRange),
          getRepAnalytics(activeRange),
          getTimeSeriesAnalytics(activeRange),
          getAgentActivity(activeRange),
          getManualReview(activeRange),
          listLeads(),
        ]);
      setData({ dashboard, channels, pipeline, revenue, reps, series, agents, review });
      setLeads(leadRows);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "Analytics service unavailable");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void refresh(range);
  }, [range]);

  async function inspectLead(id: string) {
    setError(null);
    try {
      setSelectedLead(await getLead(id));
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "Unable to load lead");
    }
  }

  function navigate(next: View) {
    setView(next);
    setNavOpen(false);
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  const metadata = data?.dashboard.metadata;
  return (
    <div className="app-shell">
      <div className="app-ambient ambient-a" />
      <div className="app-ambient ambient-b" />
      <aside className={`side-rail ${navOpen ? "open" : ""}`}>
        <button className="brand-lockup" onClick={() => navigate("dashboard")} aria-label="Ingenuity Revenue Intelligence home">
          <span className="brand-symbol"><i /><i /></span>
          <span><b>INGENUITY</b><small>REVENUE INTELLIGENCE</small></span>
        </button>
        <nav aria-label="Primary navigation">
          {navigation.map((item, index) => (
            <div key={item.id}>
              {item.group && <span className="nav-group">{item.group}</span>}
              <button className={view === item.id ? "active" : ""} onClick={() => navigate(item.id)}>
                <i>{item.glyph}</i><span>{item.label}</span>
                {item.id === "review" && (data?.review.total ?? 0) > 0 && <b>{data?.review.total}</b>}
              </button>
              {index === 4 && <span className="nav-separator" />}
            </div>
          ))}
        </nav>
        <div className="rail-foot">
          <div className="workspace-mark">
            <i>{metadata?.workspace_name.slice(0, 2).toUpperCase() || "IS"}</i>
            <span><b>{metadata?.workspace_name || "Workspace"}</b><small>{metadata?.data_mode === "demo" ? "Explicit demo workspace" : "Live workspace"}</small></span>
          </div>
          <span className="system-state"><i /> Systems nominal</span>
        </div>
      </aside>
      <main className="product-main">
        <header className="product-bar">
          <button className="menu-button" onClick={() => setNavOpen((current) => !current)} aria-expanded={navOpen} aria-label="Toggle navigation">☰</button>
          <div className="breadcrumb"><span>Revenue Operations</span><i>/</i><b>{navigation.find((item) => item.id === view)?.label}</b></div>
          <div className="bar-actions">
            <RangePicker value={range} onChange={setRange} />
            <span className={`mode-badge ${metadata?.data_mode || "demo"}`}>{metadata?.data_mode === "live" ? "LIVE DATA" : "DEMO DATA"}</span>
            <button className="icon-button" onClick={() => void refresh()} aria-label="Refresh data">↻</button>
          </div>
        </header>
        {error && <div className="api-error" role="alert"><div><b>Control plane unavailable</b><span>{error}</span></div><button onClick={() => void refresh()}>Retry</button></div>}
        {loading && !data ? <LoadingState /> : (
          <>
            {view === "dashboard" && <DashboardView data={data} onInspect={(id) => { void inspectLead(id); setView("leads"); }} />}
            {view === "leads" && <LeadsView leads={leads} selected={selectedLead} onInspect={(id) => void inspectLead(id)} onCreated={(lead) => { setLeads((current) => [lead, ...current.filter((item) => item.id !== lead.id)]); setSelectedLead(lead); void refresh(); }} />}
            {view === "pipeline" && <PipelineView data={data?.pipeline ?? null} onInspect={(id) => { void inspectLead(id); setView("leads"); }} />}
            {view === "agents" && <AgentsView data={data?.agents ?? null} pipeline={data?.pipeline ?? null} />}
            {view === "analytics" && <AnalyticsView data={data} />}
            {view === "review" && <ManualReviewView data={data?.review ?? null} onInspect={(id) => { void inspectLead(id); setView("leads"); }} />}
            {view === "integrations" && <IntegrationsView />}
            {view === "settings" && <SettingsView metadata={metadata} onIntegrations={() => setView("integrations")} />}
          </>
        )}
        <footer className="product-footer">
          <span>Ingenuity Systems · AI Revenue Operations</span>
          <span>Generated {metadata ? new Date(metadata.generated_at).toLocaleString() : "—"} · Workspace {ACTIVE_WORKSPACE_ID.slice(0, 8)}</span>
        </footer>
      </main>
      {navOpen && <button className="nav-scrim" onClick={() => setNavOpen(false)} aria-label="Close navigation" />}
    </div>
  );
}

function RangePicker({ value, onChange }: { value: TimeRange; onChange: (value: TimeRange) => void }) {
  return (
    <div className="range-picker" aria-label="Analytics time range">
      {(["weekly", "monthly", "annual"] as const).map((item) => (
        <button className={value === item ? "active" : ""} onClick={() => onChange(item)} key={item}>{item.slice(0, 1).toUpperCase() + item.slice(1)}</button>
      ))}
    </div>
  );
}

function LoadingState() {
  return <div className="loading-state"><div className="loading-core"><i /><i /><i /></div><b>Synchronizing workspace intelligence</b><span>Loading analytics, pipeline and agent activity…</span></div>;
}

export default App;
