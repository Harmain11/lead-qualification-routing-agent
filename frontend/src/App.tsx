import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { flushSync } from "react-dom";

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
  listIntegrations,
  getSession,
  setActiveWorkspace,
  signOut,
  ApiError,
} from "./api";
import type { AuthSession, IntegrationCard, Lead, TimeRange } from "./types";
import { can } from "./permissions";
import { AuthView } from "./views/AuthView";
import { OpenAISetupPrompt } from "./components/OpenAISetupPrompt";
import { ProductTour } from "./components/ProductTour";
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
type Theme = "light" | "dark";

function savedTheme(): Theme {
  try { return window.localStorage.getItem("corral-theme") === "dark" ? "dark" : "light"; }
  catch { return "light"; }
}

const navigation: Array<{ id: View; label: string; group?: string }> = [
  { id: "dashboard", label: "Dashboard" },
  { id: "leads", label: "Leads" },
  { id: "pipeline", label: "Pipeline" },
  { id: "agents", label: "Agents / AI Activity" },
  { id: "analytics", label: "Analytics" },
  { id: "review", label: "Manual Review", group: "Control" },
  { id: "integrations", label: "Integrations" },
  { id: "settings", label: "Settings" },
];

const VIEW_PERMISSIONS: Partial<Record<View, string>> = {
  dashboard: "analytics.view",
  leads: "leads.view",
  pipeline: "analytics.view",
  agents: "agents.view",
  analytics: "analytics.view",
  review: "manual_review.view",
  integrations: "integrations.view",
};

function hasViewPermission(view: AppView, permissions: ReadonlySet<string>) {
  const permission = view === "team"
    ? "members.view"
    : view === "icp"
      ? "icp.view"
      : VIEW_PERMISSIONS[view as View];
  return !permission || can(permissions, permission);
}

function permissionsForWorkspace(session: AuthSession | null, workspaceId?: string) {
  const workspace = session?.workspaces.find((item) => item.id === workspaceId);
  return new Set(workspace?.effective_permissions ?? []);
}

function NavIcon({ id }: { id: View }) {
  const shared = { viewBox: "0 0 24 24", width: 19, height: 19, fill: "none", stroke: "currentColor", strokeWidth: 1.8, strokeLinecap: "round" as const, strokeLinejoin: "round" as const, "aria-hidden": true as const };
  switch (id) {
    case "dashboard": return <svg {...shared}><rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/></svg>;
    case "leads": return <svg {...shared}><circle cx="9" cy="8" r="3"/><path d="M3.5 20v-1.5a5.5 5.5 0 0 1 11 0V20zM16 5.3a3 3 0 0 1 0 5.4M17 14a4.5 4.5 0 0 1 3.5 4.4V20"/></svg>;
    case "pipeline": return <svg {...shared}><path d="M4 5v5a5 5 0 0 0 5 5h9M4 19v-4a5 5 0 0 1 5-5h9M15 6l4 4-4 4"/></svg>;
    case "agents": return <svg {...shared}><path d="m12 2 1.9 6.1L20 10l-6.1 1.9L12 18l-1.9-6.1L4 10l6.1-1.9zM19 17l.7 1.3L21 19l-1.3.7L19 21l-.7-1.3L17 19l1.3-.7z"/></svg>;
    case "analytics": return <svg {...shared}><path d="M4 20V11M10 20V4M16 20v-8M22 20V8M2 20h20"/></svg>;
    case "review": return <svg {...shared}><path d="M4 4h16v11l-3 5H7l-3-5zM4 15h5l1.5 2h3L15 15h5M9 9l2 2 4-4"/></svg>;
    case "integrations": return <svg {...shared}><path d="M8 3v5M16 3v5M6 8h12v3a6 6 0 0 1-6 6v4M12 21h5"/></svg>;
    case "settings": return <svg {...shared}><path d="M12 3l1.4 1.5 2-.2.8 1.8 2 .5v2l1.6 1.2-1 1.8 1 1.8-1.6 1.2v2l-2 .5-.8 1.8-2-.2L12 21l-1.4-1.5-2 .2-.8-1.8-2-.5v-2L4.2 14l1-1.8-1-1.8L5.8 9v-2l2-.5.8-1.8 2 .2z"/><circle cx="12" cy="12" r="3"/></svg>;
  }
}

function App() {
  const [auth, setAuth] = useState<AuthSession | null>(null);
  const [authLoading, setAuthLoading] = useState(true);
  const [view, setView] = useState<AppView>(() =>
    new URLSearchParams(window.location.search).has("integration")
      ? "integrations"
      : new URLSearchParams(window.location.search).has("lead")
      ? "leads"
      : "dashboard",
  );
  const [range, setRange] = useState<TimeRange>("monthly");
  const [data, setData] = useState<AnalyticsBundle | null>(null);
  const [leads, setLeads] = useState<Lead[]>([]);
  const [leadRevision, setLeadRevision] = useState(0);
  const [tourOpen, setTourOpen] = useState(false);
  const [openaiSetupCard, setOpenaiSetupCard] = useState<IntegrationCard | null>(null);
  const [selectedLead, setSelectedLead] = useState<Lead | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [navOpen, setNavOpen] = useState(() => window.matchMedia("(min-width: 761px)").matches);
  const [theme, setTheme] = useState<Theme>(savedTheme);
  const activeWorkspace = auth?.workspaces.find((item) => item.id === auth.current_workspace_id);
  const workspacePermissions = useMemo(
    () => new Set(activeWorkspace?.effective_permissions ?? []),
    [activeWorkspace],
  );
  const refreshVersion = useRef(0);
  const refreshController = useRef<AbortController | null>(null);
  const leadRequestVersion = useRef(0);
  const leadController = useRef<AbortController | null>(null);
  const leadSignature = useRef<string | null>(null);
  const openaiPromptDismissed = useRef(new Set<string>());
  const tourSeenKey = auth?.current_workspace_id
    ? `corral-product-tour-seen:v1:${auth.current_workspace_id}`
    : null;

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    try { window.localStorage.setItem("corral-theme", theme); } catch { /* Appearance still works for this session. */ }
  }, [theme]);

  useEffect(() => {
    if (!tourSeenKey) {
      setTourOpen(false);
      return;
    }
    try {
      setTourOpen(window.localStorage.getItem(tourSeenKey) !== "seen");
    } catch {
      setTourOpen(true);
    }
  }, [tourSeenKey]);

  const closeTour = useCallback(() => {
    if (tourSeenKey) {
      try { window.localStorage.setItem(tourSeenKey, "seen"); } catch { /* The guide remains available for this session. */ }
    }
    setTourOpen(false);
  }, [tourSeenKey]);

  useEffect(() => {
    const media = window.matchMedia("(min-width: 761px)");
    const syncNavigation = () => setNavOpen(media.matches);
    media.addEventListener("change", syncNavigation);
    return () => media.removeEventListener("change", syncNavigation);
  }, []);

  const refresh = useCallback(async (activeRange = range) => {
    refreshController.current?.abort();
    const controller = new AbortController();
    refreshController.current = controller;
    const requestVersion = ++refreshVersion.current;
    setLoading(true);
    setError(null);
    try {
      const activePermissions = permissionsForWorkspace(auth, auth?.current_workspace_id);
      const canViewAnalytics = can(activePermissions, "analytics.view");
      const canViewAgents = can(activePermissions, "agents.view");
      const canViewReview = can(activePermissions, "manual_review.view");
      const canViewLeads = can(activePermissions, "leads.view");
      const [dashboard, channels, pipeline, revenue, reps, series, agents, review, leadRows] =
        await Promise.all([
          canViewAnalytics ? getDashboard(activeRange, controller.signal) : Promise.resolve(null),
          canViewAnalytics ? getChannelAnalytics(activeRange, controller.signal) : Promise.resolve(null),
          canViewAnalytics ? getPipelineAnalytics(activeRange, controller.signal) : Promise.resolve(null),
          canViewAnalytics ? getRevenueAnalytics(activeRange, controller.signal) : Promise.resolve(null),
          canViewAnalytics ? getRepAnalytics(activeRange, controller.signal) : Promise.resolve(null),
          canViewAnalytics ? getTimeSeriesAnalytics(activeRange, controller.signal) : Promise.resolve(null),
          canViewAgents ? getAgentActivity(activeRange, controller.signal) : Promise.resolve(null),
          canViewReview ? getManualReview(activeRange, controller.signal) : Promise.resolve(null),
          canViewLeads ? listLeads({ limit: 1 }, controller.signal) : Promise.resolve(null),
        ]);
      if (requestVersion !== refreshVersion.current) return;
      setData(
        dashboard || channels || pipeline || revenue || reps || series || agents || review
          ? { dashboard, channels, pipeline, revenue, reps, series, agents, review }
          : null,
      );
      const nextLeads = leadRows?.items ?? [];
      setLeads(nextLeads);
      if (leadRows) {
        const nextSignature = JSON.stringify([leadRows.total, nextLeads[0] ?? null]);
        if (leadSignature.current !== null && leadSignature.current !== nextSignature) {
          setLeadRevision((current) => current + 1);
        }
        leadSignature.current = nextSignature;
      } else {
        leadSignature.current = null;
      }
    } catch (requestError) {
      if (requestVersion === refreshVersion.current && !controller.signal.aborted) {
        controller.abort();
        if (requestError instanceof ApiError && requestError.status === 401) {
          setAuth(null);
        } else {
          setError(requestError instanceof Error ? requestError.message : "Analytics service unavailable");
        }
      }
    } finally {
      if (requestVersion === refreshVersion.current) setLoading(false);
    }
  }, [range, auth?.current_workspace_id, auth?.workspaces]);

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
    leadSignature.current = null;
    void refresh(range);
    return () => refreshController.current?.abort();
  }, [auth?.current_workspace_id, range, refresh]);

  useEffect(() => {
    if (!auth || !can(permissionsForWorkspace(auth, auth.current_workspace_id), "leads.view")) return;
    const controller = new AbortController();
    let inFlight = false;
    const poll = async () => {
      if (document.visibilityState !== "visible" || inFlight) return;
      inFlight = true;
      try {
        const result = await listLeads({ limit: 1 }, controller.signal);
        const nextSignature = JSON.stringify([result.total, result.items[0] ?? null]);
        if (leadSignature.current !== null && leadSignature.current !== nextSignature) {
          void refresh();
        } else if (leadSignature.current === null) {
          leadSignature.current = nextSignature;
        }
      } catch (pollError) {
        if (pollError instanceof ApiError && pollError.status === 401) setAuth(null);
      } finally {
        inFlight = false;
      }
    };
    // Keep the dashboard, manual review count, and CRM lead archive in sync
    // with public intake without requiring a browser refresh.
    const timer = window.setInterval(() => void poll(), 5000);
    const onVisible = () => { if (document.visibilityState === "visible") void poll(); };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      controller.abort();
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [auth?.current_workspace_id, auth?.workspaces, refresh]);

  useEffect(() => {
    if (auth) return;
    setOpenaiSetupCard(null);
    openaiPromptDismissed.current.clear();
  }, [auth]);

  useEffect(() => {
    if (!auth) return;
    const workspace = auth.workspaces.find((item) => item.id === auth.current_workspace_id);
    if (!workspace) return;
    const permissions = new Set(workspace.effective_permissions);
    if (hasViewPermission(view, permissions)) return;
    const fallback = navigation.find((item) => hasViewPermission(item.id, permissions))?.id ?? "settings";
    setView(fallback);
  }, [auth?.current_workspace_id, auth?.workspaces, view]);

  useEffect(() => {
    if (!auth) return;
    setOpenaiSetupCard(null);
    const workspace = auth.workspaces.find((item) => item.id === auth.current_workspace_id);
    if (!workspace?.effective_permissions.includes("integrations.manage") ||
        openaiPromptDismissed.current.has(auth.current_workspace_id)) return;
    let active = true;
    void listIntegrations().then((cards) => {
      if (!active) return;
      const openai = cards.find((card) => card.provider === "openai");
      setOpenaiSetupCard(openai?.connection?.status === "verified" ? null : openai ?? null);
    }).catch(() => { if (active) setOpenaiSetupCard(null); });
    return () => { active = false; };
  }, [auth?.user.id, auth?.current_workspace_id]);

  useEffect(() => () => {
    refreshController.current?.abort();
    leadController.current?.abort();
  }, []);

  const inspectLead = useCallback(async (id: string) => {
    if (!can(permissionsForWorkspace(auth, auth?.current_workspace_id), "leads.view")) return;
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
        if (requestError instanceof ApiError && requestError.status === 401) setAuth(null);
        else setError(requestError instanceof Error ? requestError.message : "Unable to load lead");
      }
    }
  }, [auth?.current_workspace_id, auth?.workspaces]);

  const handleInspectLead = useCallback((id: string) => {
    if (!can(permissionsForWorkspace(auth, auth?.current_workspace_id), "leads.view")) return;
    setView("leads");
    void inspectLead(id);
  }, [inspectLead, auth?.current_workspace_id, auth?.workspaces]);

  const closeLead = useCallback(() => {
    leadController.current?.abort();
    leadRequestVersion.current += 1;
    setSelectedLead(null);
    if (new URLSearchParams(window.location.search).has("lead")) {
      const url = new URL(window.location.href);
      url.searchParams.delete("lead");
      window.history.replaceState({}, "", url);
    }
  }, []);

  useEffect(() => {
    if (!auth || !can(workspacePermissions, "leads.view")) return;
    const leadId = new URLSearchParams(window.location.search).get("lead");
    if (leadId && /^[0-9a-f-]{36}$/i.test(leadId)) void inspectLead(leadId);
  }, [auth?.current_workspace_id, workspacePermissions, inspectLead]);

  function navigate(next: AppView) {
    setView(next);
    if (window.matchMedia("(max-width: 760px)").matches) setNavOpen(false);
  }

  function toggleTheme(event?: React.MouseEvent<HTMLButtonElement>) {
    const next = theme === "light" ? "dark" : "light";
    const apply = () => {
      document.documentElement.dataset.theme = next;
      flushSync(() => setTheme(next));
    };
    if (document.startViewTransition && !window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      const bounds = event?.currentTarget?.getBoundingClientRect();
      const x = bounds ? bounds.left + bounds.width / 2 : window.innerWidth / 2;
      const y = bounds ? bounds.top + bounds.height / 2 : window.innerHeight / 2;
      const radius = Math.hypot(Math.max(x, window.innerWidth - x), Math.max(y, window.innerHeight - y));
      const transition = document.startViewTransition(apply);
      void transition.ready.then(() => {
        document.documentElement.animate(
          { clipPath: [`circle(0px at ${x}px ${y}px)`, `circle(${radius}px at ${x}px ${y}px)`] },
          { duration: 680, easing: "cubic-bezier(.16, 1, .3, 1)", pseudoElement: "::view-transition-new(root)" },
        );
      }).catch(() => { /* A cancelled transition still applies the chosen theme. */ });
    } else {
      apply();
    }
  }

  const visibleNavigation = navigation.filter((item) => hasViewPermission(item.id, workspacePermissions));
  const metadata = data?.dashboard?.metadata;
  if (authLoading) return <LoadingState />;
  if (!auth) {
    return <AuthView theme={theme} onToggleTheme={toggleTheme} onAuthenticated={(session) => {
      setActiveWorkspace(session.current_workspace_id);
      setAuth(session);
    }} />;
  }
  return (
    <div className={`app-shell ${navOpen ? "nav-expanded" : "nav-collapsed"}`}>
      <div className="app-ambient ambient-a" />
      <div className="app-ambient ambient-b" />
      <img className="app-twinkle app-twinkle-a" src="/asset/brand/twinkle-cap.svg" alt="" aria-hidden="true" />
      <img className="app-twinkle app-twinkle-b" src="/asset/brand/twinkle-role.svg" alt="" aria-hidden="true" />
      <aside id="corral-navigation" className={`side-rail ${navOpen ? "open" : ""}`} aria-label="Corral navigation" aria-hidden={!navOpen} inert={!navOpen}>
        <div className="rail-brand-row">
          <button className="brand-lockup" onClick={() => navigate("dashboard")} aria-label="Corral home">
            <img src="/asset/brand/corral-mark.png" alt="" />
            <span><b>Corral</b><small>POWERED BY INGENUITY SYSTEMS</small></span>
          </button>
          <button className="rail-close" onClick={() => setNavOpen(false)} aria-label="Collapse navigation" title="Collapse navigation"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="m14.5 5-5 7 5 7" /></svg></button>
        </div>
        <nav aria-label="Primary navigation">
          {visibleNavigation.map((item, index) => (
            <div key={item.id}>
              {item.group && <span className="nav-group">{item.group}</span>}
              <button className={view === item.id || (item.id === "settings" && (view === "team" || view === "icp")) ? "active" : ""} onClick={() => navigate(item.id)}>
                <i><NavIcon id={item.id} /></i><span>{item.label}</span>
                {item.id === "review" && (data?.review?.total ?? 0) > 0 && <b>{data?.review?.total}</b>}
              </button>
              {index === 4 && <span className="nav-separator" />}
            </div>
          ))}
        </nav>
        <div className="rail-foot">
          <div className="workspace-mark">
            <img src="/asset/brand/corral-mark.png" alt="" />
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
              <small>{metadata?.data_mode === "demo" ? "Illustrative workspace" : "Live workspace"}</small>
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
      {!navOpen && <aside className="mini-rail" aria-label="Collapsed Corral navigation">
        <button className="mini-rail-expand" onClick={() => setNavOpen(true)} aria-controls="corral-navigation" aria-expanded={false} aria-label="Expand navigation" title="Expand navigation">
          <img src="/asset/brand/corral-mark.png" alt="" />
          <span aria-hidden="true">›</span>
        </button>
        <nav className="mini-rail-nav" aria-label="Primary navigation">
          {visibleNavigation.map((item) => {
            const active = view === item.id || (item.id === "settings" && (view === "team" || view === "icp"));
            return <button key={item.id} className={active ? "active" : ""} onClick={() => navigate(item.id)} aria-label={item.label} aria-current={active ? "page" : undefined} title={item.label}>
              <NavIcon id={item.id} />
              {item.id === "review" && (data?.review?.total ?? 0) > 0 && <span className="mini-alert-dot" />}
            </button>;
          })}
        </nav>
      </aside>}
      <main className="product-main">
        <header className="product-bar">
          <div className="bar-leading">
            <button className="menu-button" onClick={() => setNavOpen(true)} aria-controls="corral-navigation" aria-expanded={navOpen} aria-label="Open navigation">☰</button>
            <div className="breadcrumb"><span>Corral</span><i>/</i><b>{view === "team" ? "Team & Permissions" : view === "icp" ? "Ideal Customer Profile" : navigation.find((item) => item.id === view)?.label}</b></div>
          </div>
          <div className="bar-actions">
            <RangePicker value={range} onChange={setRange} />
            <span className={`mode-badge ${metadata?.data_mode || "live"}`}>{metadata?.data_mode === "demo" ? "ILLUSTRATIVE DATA" : "LIVE DATA"}</span>
            <button className="icon-button" onClick={() => void refresh()} aria-label="Refresh data">↻</button>
            <button className="guide-launch" onClick={() => setTourOpen(true)} aria-haspopup="dialog" aria-label="Open Corral product guide" title="Open Corral product guide">
              <svg viewBox="0 0 20 20" aria-hidden="true"><circle cx="10" cy="10" r="7.5" /><path d="M7.8 7.4a2.3 2.3 0 1 1 3.8 1.7c-.9.8-1.6 1.1-1.6 2.3M10 14.2v.1" /></svg>
              <span>Guide</span>
            </button>
            <button className="theme-toggle" onClick={toggleTheme} aria-label={`Switch to ${theme === "light" ? "dark" : "light"} mode`}><span aria-hidden="true">{theme === "light" ? "◐" : "☼"}</span><span className="theme-label">{theme === "light" ? "Dark" : "Light"}</span></button>
          </div>
        </header>
        {error && <div className="api-error" role="alert"><div><b>Control plane unavailable</b><span>{error}</span></div><button onClick={() => void refresh()}>Retry</button></div>}
        {!hasViewPermission(view, workspacePermissions) || (loading && !data && view !== "settings" && view !== "team" && view !== "icp") ? <ContentLoadingState /> : (
          <div className="view-transition" key={view}>
            {view === "dashboard" && <DashboardView data={data} latestLead={leads[0] ?? null} onInspect={(id) => { handleInspectLead(id); setView("leads"); }} />}
            {view === "leads" && <LeadsView selected={selectedLead} revision={leadRevision} onInspect={handleInspectLead} onClose={closeLead} canEdit={can(workspacePermissions, "leads.edit")} canExport={can(workspacePermissions, "leads.export")} canCrmSync={can(workspacePermissions, "crm.sync")} onUpdated={(lead) => { setSelectedLead(lead); setLeads((current) => current.map((item) => item.id === lead.id ? lead : item)); }} onDeleted={(leadId) => { setSelectedLead(null); setLeads((current) => current.filter((item) => item.id !== leadId)); void refresh(); }} onCreated={(lead) => { setLeads((current) => [lead, ...current.filter((item) => item.id !== lead.id)]); setSelectedLead(lead); void refresh(); }} />}
            {view === "pipeline" && <PipelineView data={data?.pipeline ?? null} onInspect={(id) => { handleInspectLead(id); setView("leads"); }} />}
            {view === "agents" && <AgentsView data={data?.agents ?? null} pipeline={data?.pipeline ?? null} />}
            {view === "analytics" && <AnalyticsView data={data} />}
            {view === "review" && <ManualReviewView data={data?.review ?? null} onInspect={(id) => { handleInspectLead(id); setView("leads"); }} />}
            {view === "integrations" && <IntegrationsView permissions={workspacePermissions} />}
            {view === "settings" && <SettingsView metadata={metadata} onIntegrations={() => setView("integrations")} onTeam={() => setView("team")} onIcp={() => setView("icp")} canViewTeam={can(workspacePermissions, "members.view")} canViewIcp={can(workspacePermissions, "icp.view")} canViewRouting={can(workspacePermissions, "routing.view")} canManageRouting={can(workspacePermissions, "routing.manage")} canViewPublicIntake={can(workspacePermissions, "integrations.manage")} canRotatePublicIntake={can(workspacePermissions, "integrations.manage")} canManageEmail={can(workspacePermissions, "integrations.manage")} />}
            {view === "team" && <TeamView key={auth.current_workspace_id} auth={auth} workspaceName={auth.workspaces.find((workspace) => workspace.id === auth.current_workspace_id)?.name || metadata?.workspace_name || "Current workspace"} onBack={() => setView("settings")} />}
            {view === "icp" && <IcpView key={auth.current_workspace_id} canManage={can(workspacePermissions, "icp.manage")} onBack={() => setView("settings")} />}
          </div>
        )}
        <footer className="product-footer">
          <span>Corral · Powered by Ingenuity Systems</span>
          <span>Generated {metadata ? new Date(metadata.generated_at).toLocaleString() : "—"} · Workspace {getActiveWorkspace().slice(0, 8)}</span>
        </footer>
      </main>
      {navOpen && <button className="nav-scrim" onClick={() => setNavOpen(false)} aria-label="Close navigation" />}
      {openaiSetupCard && <OpenAISetupPrompt card={openaiSetupCard} onVerified={() => { setOpenaiSetupCard(null); void refresh(); }} onLater={() => { openaiPromptDismissed.current.add(auth.current_workspace_id); setOpenaiSetupCard(null); }} onSettings={() => { openaiPromptDismissed.current.add(auth.current_workspace_id); setOpenaiSetupCard(null); setView("integrations"); }} />}
      <ProductTour open={tourOpen} currentView={view} onNavigate={navigate} onDismiss={closeTour} onComplete={closeTour} />
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
