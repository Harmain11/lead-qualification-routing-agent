import { useCallback, useEffect, useRef, useState } from "react";

import {
  changeWorkspace,
  getActiveWorkspace,
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
  getSession,
  setActiveWorkspace,
  signOut,
} from "./api";
import type { AuthSession, Lead, TimeRange } from "./types";
import { can } from "./permissions";
import { AuthView } from "./views/AuthView";
import { IntegrationsView } from "./views/IntegrationsView";
import { IcpView } from "./views/IcpView";
import { TeamView } from "./views/TeamView";
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
  // Team administration is opened from Settings and keeps the primary rail calm.
type AppView = View | "team" | "icp";

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
  const [auth, setAuth] = useState<AuthSession | null>(null);
  const [authLoading, setAuthLoading] = useState(true);
  const [view, setView] = useState<AppView>("dashboard");
  const [range, setRange] = useState<TimeRange>("monthly");
  const [data, setData] = useState<AnalyticsBundle | null>(null);
  const [leads, setLeads] = useState<Lead[]>([]);
  const [selectedLead, setSelectedLead] = useState<Lead | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [navOpen, setNavOpen] = useState(false);
  const [workspacePermissions, setWorkspacePermissions] = useState<ReadonlySet<string>>(new Set());
  const refreshVersion = useRef(0);
  const refreshController = useRef<AbortController | null>(null);
  const leadRequestVersion = useRef(0);
  const leadController = useRef<AbortController | null>(null);

  const refresh = useCallback(async (activeRange = range) => {
    refreshController.current?.abort();
    const controller = new AbortController();
    refreshController.current = controller;
    const requestVersion = ++refreshVersion.current;
    setLoading(true);
    setError(null);
    try {
      const [dashboard, channels, pipeline, revenue, reps, series, agents, review, leadRows] =
        await Promise.all([
          getDashboard(activeRange, controller.signal),
          getChannelAnalytics(activeRange, controller.signal),
          getPipelineAnalytics(activeRange, controller.signal),
          getRevenueAnalytics(activeRange, controller.signal),
          getRepAnalytics(activeRange, controller.signal),
          getTimeSeriesAnalytics(activeRange, controller.signal),
          getAgentActivity(activeRange, controller.signal),
          getManualReview(activeRange, controller.signal),
          listLeads(controller.signal),
        ]);
      if (requestVersion !== refreshVersion.current) return;
      setData({ dashboard, channels, pipeline, revenue, reps, series, agents, review });
      setLeads(leadRows);
    } catch (requestError) {
      if (requestVersion === refreshVersion.current && !controller.signal.aborted) {
        controller.abort();
        setError(requestError instanceof Error ? requestError.message : "Analytics service unavailable");
      }
    } finally {
      if (requestVersion === refreshVersion.current) setLoading(false);
    }
  }, [range]);

  useEffect(() => {
    getSession()
      .then((session) => {
        if (session) {
          setActiveWorkspace(session.current_workspace_id);
          setAuth(session);
        } else {
          setAuth(null);
        }
      })
      .catch(() => setAuth(null))
      .finally(() => setAuthLoading(false));
  }, []);

  useEffect(() => {
    if (!auth) return;
    void refresh(range);
    return () => refreshController.current?.abort();
  }, [auth?.current_workspace_id, range, refresh]);

  useEffect(() => {
    if (!auth) {
      setWorkspacePermissions(new Set());
      return;
    }
    const workspace = auth.workspaces.find(
      (item) => item.id === auth.current_workspace_id,
    );
    setWorkspacePermissions(new Set(workspace?.effective_permissions ?? []));
  }, [auth?.current_workspace_id, auth?.user.id, auth?.workspaces]);

  useEffect(() => () => {
    refreshController.current?.abort();
    leadController.current?.abort();
  }, []);

  const inspectLead = useCallback(async (id: string) => {
    leadController.current?.abort();
    const controller = new AbortController();
    leadController.current = controller;
    const requestVersion = ++leadRequestVersion.current;
    setError(null);
    try {
      const lead = await getLead(id, controller.signal);
      if (requestVersion === leadRequestVersion.current) setSelectedLead(lead);
    } catch (requestError) {
      if (requestVersion === leadRequestVersion.current && !controller.signal.aborted) {
        setError(requestError instanceof Error ? requestError.message : "Unable to load lead");
      }
    }
  }, []);

  const handleInspectLead = useCallback((id: string) => { void inspectLead(id); }, [inspectLead]);

  function navigate(next: AppView) {
    setView(next);
    setNavOpen(false);
  }

  const metadata = data?.dashboard.metadata;
  if (authLoading) return <LoadingState />;
  if (!auth) {
    return <AuthView onAuthenticated={(session) => {
      setActiveWorkspace(session.current_workspace_id);
      setAuth(session);
    }} />;
  }
  return (
    <div className="app-shell">
      <div className="app-ambient ambient-a" />
      <div className="app-ambient ambient-b" />
      <img className="app-twinkle app-twinkle-a" src="/asset/brand/twinkle-cap.svg" alt="" aria-hidden="true" />
      <img className="app-twinkle app-twinkle-b" src="/asset/brand/twinkle-role.svg" alt="" aria-hidden="true" />
      <aside className={`side-rail ${navOpen ? "open" : ""}`}>
        <button className="brand-lockup" onClick={() => navigate("dashboard")} aria-label="Ingenuity Revenue Intelligence home">
          <img src="/asset/brand/logo-light.png" alt="Ingenuity Systems" />
          <span><small>REVENUE INTELLIGENCE</small></span>
        </button>
        <nav aria-label="Primary navigation">
          {navigation.map((item, index) => (
            <div key={item.id}>
              {item.group && <span className="nav-group">{item.group}</span>}
              <button className={view === item.id || (item.id === "settings" && (view === "team" || view === "icp")) ? "active" : ""} onClick={() => navigate(item.id)}>
                <i>{item.glyph}</i><span>{item.label}</span>
                {item.id === "review" && (data?.review.total ?? 0) > 0 && <b>{data?.review.total}</b>}
              </button>
              {index === 4 && <span className="nav-separator" />}
            </div>
          ))}
        </nav>
        <div className="rail-foot">
          <div className="workspace-mark">
            <img src="/asset/brand/logo-icon.png" alt="" />
            <span>
              <select
                aria-label="Current workspace"
                value={auth.current_workspace_id}
                onChange={(event) => {
                  refreshController.current?.abort();
                  leadController.current?.abort();
                  void changeWorkspace(event.target.value).then((session) => {
                    setData(null);
                    setSelectedLead(null);
                    setAuth(session);
                  });
                }}
              >{auth.workspaces.map((workspace) => <option value={workspace.id} key={workspace.id}>{workspace.name}</option>)}</select>
              <small>{metadata?.data_mode === "demo" ? "Explicit demo workspace" : "Live workspace"}</small>
            </span>
          </div>
          <span className="system-state"><i /> Systems nominal</span>
          <button className="signout-button" onClick={() => {
            refreshController.current?.abort();
            leadController.current?.abort();
            void signOut().finally(() => { setAuth(null); setData(null); setSelectedLead(null); });
          }}>Sign out</button>
        </div>
      </aside>
      <main className="product-main">
        <header className="product-bar">
          <button className="menu-button" onClick={() => setNavOpen((current) => !current)} aria-expanded={navOpen} aria-label="Toggle navigation">☰</button>
          <div className="breadcrumb"><span>Revenue Operations</span><i>/</i><b>{view === "team" ? "Team & Permissions" : view === "icp" ? "Ideal Customer Profile" : navigation.find((item) => item.id === view)?.label}</b></div>
          <div className="bar-actions">
            <RangePicker value={range} onChange={setRange} />
            <span className={`mode-badge ${metadata?.data_mode || "demo"}`}>{metadata?.data_mode === "live" ? "LIVE DATA" : "DEMO DATA"}</span>
            <button className="icon-button" onClick={() => void refresh()} aria-label="Refresh data">↻</button>
          </div>
        </header>
        {error && <div className="api-error" role="alert"><div><b>Control plane unavailable</b><span>{error}</span></div><button onClick={() => void refresh()}>Retry</button></div>}
        {loading && !data && view !== "settings" && view !== "team" && view !== "icp" ? <ContentLoadingState /> : (
          <div className="view-transition" key={view}>
            {view === "dashboard" && <DashboardView data={data} latestLead={leads[0] ?? null} onInspect={(id) => { handleInspectLead(id); setView("leads"); }} />}
            {view === "leads" && <LeadsView leads={leads} selected={selectedLead} onInspect={handleInspectLead} canEdit={can(workspacePermissions, "leads.edit")} canExport={can(workspacePermissions, "leads.export")} onCreated={(lead) => { setLeads((current) => [lead, ...current.filter((item) => item.id !== lead.id)]); setSelectedLead(lead); void refresh(); }} />}
            {view === "pipeline" && <PipelineView data={data?.pipeline ?? null} onInspect={(id) => { handleInspectLead(id); setView("leads"); }} />}
            {view === "agents" && <AgentsView data={data?.agents ?? null} pipeline={data?.pipeline ?? null} />}
            {view === "analytics" && <AnalyticsView data={data} />}
            {view === "review" && <ManualReviewView data={data?.review ?? null} onInspect={(id) => { handleInspectLead(id); setView("leads"); }} />}
            {view === "integrations" && <IntegrationsView permissions={workspacePermissions} />}
            {view === "settings" && <SettingsView metadata={metadata} onIntegrations={() => setView("integrations")} onTeam={() => setView("team")} onIcp={() => setView("icp")} canViewTeam={can(workspacePermissions, "members.view")} canViewIcp={can(workspacePermissions, "icp.view")} />}
            {view === "team" && <TeamView key={auth.current_workspace_id} auth={auth} workspaceName={auth.workspaces.find((workspace) => workspace.id === auth.current_workspace_id)?.name || metadata?.workspace_name || "Current workspace"} onBack={() => setView("settings")} />}
            {view === "icp" && <IcpView key={auth.current_workspace_id} canManage={can(workspacePermissions, "icp.manage")} onBack={() => setView("settings")} />}
          </div>
        )}
        <footer className="product-footer">
          <span>Ingenuity Systems · AI Revenue Operations</span>
          <span>Generated {metadata ? new Date(metadata.generated_at).toLocaleString() : "—"} · Workspace {getActiveWorkspace().slice(0, 8)}</span>
        </footer>
      </main>
      {navOpen && <button className="nav-scrim" onClick={() => setNavOpen(false)} aria-label="Close navigation" />}
    </div>
  );
}

function ContentLoadingState() {
  return <div className="content-loading" aria-busy="true" aria-label="Loading workspace data">
    <div className="skeleton-hero"><i /><i /><span /></div>
    <div className="skeleton-metrics">{Array.from({ length: 4 }, (_, index) => <i key={index} />)}</div>
    <div className="skeleton-content"><i /><i /></div>
  </div>;
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
