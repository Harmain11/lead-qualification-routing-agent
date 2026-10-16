import { trapFocus } from "./components/focusTrap";
import {
  lazy,
  Suspense,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
} from "react";

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
import { ActivityToasts } from "./components/ActivityToasts";
import { WorkspaceSwitcher } from "./components/WorkspaceSwitcher";
import { NativeSelectEnhancer } from "./components/NativeSelectEnhancer";
const IntegrationsView = lazy(() =>
  import("./views/IntegrationsView").then((module) => ({
    default: module.IntegrationsView,
  })),
);
const IcpView = lazy(() =>
  import("./views/IcpView").then((module) => ({ default: module.IcpView })),
);
const TeamView = lazy(() =>
  import("./views/TeamView").then((module) => ({ default: module.TeamView })),
);
const RecordsView = lazy(() =>
  import("./views/RecordsView").then((module) => ({
    default: module.RecordsView,
  })),
);
const PoliciesView = lazy(() =>
  import("./views/PoliciesView").then((module) => ({
    default: module.PoliciesView,
  })),
);
const ComplianceView = lazy(() =>
  import("./views/ComplianceView").then((module) => ({
    default: module.ComplianceView,
  })),
);
const IntelligenceView = lazy(() =>
  import("./views/IntelligenceView").then((module) => ({
    default: module.IntelligenceView,
  })),
);
const AutomationsView = lazy(() =>
  import("./views/AutomationsView").then((module) => ({
    default: module.AutomationsView,
  })),
);
const DialerView = lazy(() =>
  import("./views/DialerView").then((module) => ({ default: module.DialerView })),
);
const AdvancedAnalyticsPanel = lazy(() =>
  import("./components/AdvancedAnalyticsPanel").then((module) => ({
    default: module.AdvancedAnalyticsPanel,
  })),
);
import { CommandPalette } from "./components/CommandPalette";
import { opsGet } from "./operationsApi";
import { relayOAuthCallbackFromPopup } from "./oauthPopup";
import {
  PinnedLeadNavigation,
  usePinnedLeadSections,
} from "./components/PinnedLeadNavigation";
import { NavigationMenuItem } from "./components/NavigationMenuItem";
import {
  navigationSections,
  pageFromSearch,
  sectionFromSearch,
} from "./appNavigation";
import {
  leadSectionHref,
  parseLeadSection,
  type LeadDetailSection,
} from "./leadSections";
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
  | "contacts"
  | "pipeline"
  | "agents"
  | "analytics"
  | "integrations"
  | "dialer"
  | "team"
  | "review"
  | "settings"
  | "accounts"
  | "intelligence"
  | "automations"
  | "policies"
  | "compliance";
// Team administration is opened from Settings and keeps the primary rail calm.
type AppView = View | "icp";
type Theme = "light" | "dark";

function savedTheme(): Theme {
  try {
    return window.localStorage.getItem("corral-theme") === "dark"
      ? "dark"
      : "light";
  } catch {
    return "light";
  }
}

const navigation: Array<{ id: View; label: string; group?: string }> = [
  { id: "dashboard", label: "Dashboard", group: "Workspace" },
  { id: "leads", label: "Leads" },
  { id: "contacts", label: "Contacts" },
  { id: "accounts", label: "Companies" },
  { id: "pipeline", label: "Pipeline" },
  { id: "agents", label: "Agents / AI Activity" },
  { id: "analytics", label: "Analytics" },
  { id: "review", label: "Manual Review", group: "Operations" },
  { id: "integrations", label: "Integrations" },
  { id: "dialer", label: "Dialer" },
  { id: "team", label: "Users", group: "Administration" },
  { id: "settings", label: "Settings" },
];

const VIEW_PERMISSIONS: Partial<Record<View, string>> = {
  dashboard: "analytics.view",
  leads: "leads.view",
  contacts: "leads.view",
  accounts: "leads.view",
  intelligence: "agents.view",
  automations: "agents.view",
  policies: "routing.view",
  compliance: "workspace.manage",
  pipeline: "analytics.view",
  agents: "agents.view",
  analytics: "analytics.view",
  review: "manual_review.view",
  integrations: "integrations.view",
  dialer: "integrations.view",
};

function hasViewPermission(view: AppView, permissions: ReadonlySet<string>) {
  const permission =
    view === "team"
      ? "members.view"
      : view === "icp"
        ? "icp.view"
        : VIEW_PERMISSIONS[view as View];
  return !permission || can(permissions, permission);
}

function permissionsForWorkspace(
  session: AuthSession | null,
  workspaceId?: string,
) {
  const workspace = session?.workspaces.find((item) => item.id === workspaceId);
  return new Set(workspace?.effective_permissions ?? []);
}

function NavIcon({ id }: { id: View }) {
  const shared = {
    viewBox: "0 0 24 24",
    width: 19,
    height: 19,
    fill: "none",
    stroke: "currentColor",
    strokeWidth: 1.8,
    strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const,
    "aria-hidden": true as const,
  };
  switch (id) {
    case "dashboard":
      return (
        <svg {...shared}>
          <rect x="3" y="3" width="7" height="7" rx="1.5" />
          <rect x="14" y="3" width="7" height="7" rx="1.5" />
          <rect x="3" y="14" width="7" height="7" rx="1.5" />
          <rect x="14" y="14" width="7" height="7" rx="1.5" />
        </svg>
      );
    case "leads":
      return (
        <svg {...shared}>
          <circle cx="9" cy="8" r="3" />
          <path d="M3.5 20v-1.5a5.5 5.5 0 0 1 11 0V20zM16 5.3a3 3 0 0 1 0 5.4M17 14a4.5 4.5 0 0 1 3.5 4.4V20" />
        </svg>
      );
    case "contacts":
      return (
        <svg {...shared}>
          <circle cx="8" cy="8" r="3" />
          <circle cx="17" cy="9" r="2.3" />
          <path d="M2.5 20v-1.3a5.5 5.5 0 0 1 11 0V20zM14.5 15a4.5 4.5 0 0 1 7 3.7V20" />
        </svg>
      );
    case "pipeline":
      return (
        <svg {...shared}>
          <path d="M4 5h16l-6.5 7.2V19l-3 1v-7.8z" />
          <path d="M8 8h8M9.5 10.5h5" />
        </svg>
      );
    case "accounts":
      return (
        <svg {...shared}>
          <path d="M4 21V5l8-2 8 2v16M2 21h20M8 8h2M14 8h2M8 12h2M14 12h2M8 16h2M14 16h2" />
        </svg>
      );
    case "agents":
      return (
        <svg {...shared}>
          <path d="m12 2 1.9 6.1L20 10l-6.1 1.9L12 18l-1.9-6.1L4 10l6.1-1.9zM19 17l.7 1.3L21 19l-1.3.7L19 21l-.7-1.3L17 19l1.3-.7z" />
        </svg>
      );
    case "analytics":
      return (
        <svg {...shared}>
          <path d="M4 20V11M10 20V4M16 20v-8M22 20V8M2 20h20" />
        </svg>
      );
    case "review":
      return (
        <svg {...shared}>
          <path d="M4 4h16v11l-3 5H7l-3-5zM4 15h5l1.5 2h3L15 15h5M9 9l2 2 4-4" />
        </svg>
      );
    case "integrations":
      return (
        <svg {...shared}>
          <path d="M8 3v5M16 3v5M6 8h12v3a6 6 0 0 1-6 6v4M12 21h5" />
        </svg>
      );
    case "dialer":
      return (
        <svg {...shared}>
          <path d="M7.5 4.5 5 6c-.7.4-.9 1.3-.6 2.1 2 5.3 6.2 9.5 11.5 11.5.8.3 1.7.1 2.1-.6l1.5-2.5-4-2-1.4 1.4a14.2 14.2 0 0 1-5.5-5.5L10 9.2z" />
          <path d="M15.5 4.5a5 5 0 0 1 4 4M15.5 1.5a8 8 0 0 1 7 7" />
        </svg>
      );
    case "team":
      return (
        <svg {...shared}>
          <circle cx="12" cy="7" r="3" />
          <circle cx="5.5" cy="10" r="2" />
          <circle cx="18.5" cy="10" r="2" />
          <path d="M5 20v-1a7 7 0 0 1 14 0v1M2 19v-.5a4 4 0 0 1 4-4M22 19v-.5a4 4 0 0 0-4-4" />
        </svg>
      );
    case "intelligence":
      return (
        <svg {...shared}>
          <circle cx="10.5" cy="10.5" r="6" />
          <path d="m15 15 5 5M10.5 7.5v6M7.5 10.5h6" />
        </svg>
      );
    case "automations":
      return (
        <svg {...shared}>
          <circle cx="5" cy="5" r="2" />
          <circle cx="19" cy="12" r="2" />
          <circle cx="5" cy="19" r="2" />
          <path d="M7 5h4a4 4 0 0 1 4 4v1M7 19h4a4 4 0 0 0 4-4v-1" />
        </svg>
      );
    case "policies":
      return (
        <svg {...shared}>
          <path d="M5 4h14v16H5zM8 8h8M8 12h8M8 16h5" />
          <path d="m16 15 1.5 1.5L20 14" />
        </svg>
      );
    case "compliance":
      return (
        <svg {...shared}>
          <path d="M12 3 19 6v5c0 4.5-2.8 7.6-7 10-4.2-2.4-7-5.5-7-10V6z" />
          <path d="m8.5 12 2.2 2.2 4.8-5" />
        </svg>
      );
    case "settings":
      return (
        <svg {...shared}>
          <path d="M4 6h10M18 6h2M4 12h2M10 12h10M4 18h10M18 18h2" />
          <circle cx="16" cy="6" r="2" />
          <circle cx="8" cy="12" r="2" />
          <circle cx="16" cy="18" r="2" />
        </svg>
      );
    default:
      return (
        <svg {...shared}>
          <path d="M12 3l1.4 1.5 2-.2.8 1.8 2 .5v2l1.6 1.2-1 1.8 1 1.8-1.6 1.2v2l-2 .5-.8 1.8-2-.2L12 21l-1.4-1.5-2 .2-.8-1.8-2-.5v-2L4.2 14l1-1.8-1-1.8L5.8 9v-2l2-.5.8-1.8 2 .2z" />
          <circle cx="12" cy="12" r="3" />
        </svg>
      );
  }
}

function App() {
  useEffect(() => {
    relayOAuthCallbackFromPopup();
  }, []);

  const [branding, setBranding] = useState<{
    name: string | null;
    logo_url: string | null;
    accent: string;
  }>({ name: null, logo_url: null, accent: "#1e6b52" });
  const [auth, setAuth] = useState<AuthSession | null>(null);
  const [authLoading, setAuthLoading] = useState(true);
  const [view, setView] = useState<AppView>(() =>
    pageFromSearch(window.location.search),
  );
  const [pageSection, setPageSection] = useState(() =>
    sectionFromSearch(
      window.location.search,
      pageFromSearch(window.location.search),
    ),
  );
  const [range, setRange] = useState<TimeRange>(() => {
    const saved = new URLSearchParams(window.location.search).get("range");
    return saved === "weekly" || saved === "annual" ? saved : "monthly";
  });
  const [data, setData] = useState<AnalyticsBundle | null>(null);
  const [leads, setLeads] = useState<Lead[]>([]);
  const [leadRevision, setLeadRevision] = useState(0);
  const [tourOpen, setTourOpen] = useState(false);
  const [openaiSetupCard, setOpenaiSetupCard] =
    useState<IntegrationCard | null>(null);
  const [aiSetupCards, setAiSetupCards] = useState<IntegrationCard[]>([]);
  const [selectedLead, setSelectedLead] = useState<Lead | null>(null);
  const [leadSection, setLeadSection] = useState<LeadDetailSection>(() =>
    parseLeadSection(
      new URLSearchParams(window.location.search).get("lead_tab"),
    ),
  );
  const pinnedSections = usePinnedLeadSections(
    auth?.user.id,
    auth?.current_workspace_id,
  );
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [navOpen, setNavOpen] = useState(false);
  const [theme, setTheme] = useState<Theme>(savedTheme);
  const activeWorkspace = auth?.workspaces.find(
    (item) => item.id === auth.current_workspace_id,
  );
  const workspacePermissions = useMemo(
    () => new Set(activeWorkspace?.effective_permissions ?? []),
    [activeWorkspace],
  );
  const refreshVersion = useRef(0);
  const refreshController = useRef<AbortController | null>(null);
  const leadRequestVersion = useRef(0);
  const leadController = useRef<AbortController | null>(null);
  const leadSignature = useRef<string | null>(null);
  const reasoningPromptDismissed = useRef(new Set<string>());
  const tourSeenKey = auth?.current_workspace_id
    ? `corral-product-tour-seen:v1:${auth.current_workspace_id}`
    : null;

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    try {
      window.localStorage.setItem("corral-theme", theme);
    } catch {
      /* Appearance still works for this session. */
    }
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
      try {
        window.localStorage.setItem(tourSeenKey, "seen");
      } catch {
        /* The guide remains available for this session. */
      }
    }
    setTourOpen(false);
  }, [tourSeenKey]);

  useEffect(() => {
    if (!navOpen || !window.matchMedia("(max-width: 760px)").matches) return;
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    document.querySelector<HTMLButtonElement>(".rail-close")?.focus();
    return () => { if (previous?.isConnected) previous.focus(); };
  }, [navOpen]);

  useEffect(() => {
    const media = window.matchMedia("(min-width: 761px)");
    const syncNavigation = () => {
      if (!media.matches) setNavOpen(false);
    };
    media.addEventListener("change", syncNavigation);
    return () => media.removeEventListener("change", syncNavigation);
  }, []);

  const refresh = useCallback(
    async (activeRange = range) => {
      refreshController.current?.abort();
      const controller = new AbortController();
      refreshController.current = controller;
      const requestVersion = ++refreshVersion.current;
      setLoading(true);
      setError(null);
      try {
        const activePermissions = permissionsForWorkspace(
          auth,
          auth?.current_workspace_id,
        );
        const canViewAnalytics = can(activePermissions, "analytics.view");
        const canViewAgents = can(activePermissions, "agents.view");
        const canViewReview = can(activePermissions, "manual_review.view");
        const canViewLeads = can(activePermissions, "leads.view");
        const [
          dashboard,
          channels,
          pipeline,
          revenue,
          reps,
          series,
          agents,
          review,
          leadRows,
        ] = await Promise.all([
          canViewAnalytics
            ? getDashboard(activeRange, controller.signal)
            : Promise.resolve(null),
          canViewAnalytics
            ? getChannelAnalytics(activeRange, controller.signal)
            : Promise.resolve(null),
          canViewAnalytics
            ? getPipelineAnalytics(activeRange, controller.signal)
            : Promise.resolve(null),
          canViewAnalytics
            ? getRevenueAnalytics(activeRange, controller.signal)
            : Promise.resolve(null),
          canViewAnalytics
            ? getRepAnalytics(activeRange, controller.signal)
            : Promise.resolve(null),
          canViewAnalytics
            ? getTimeSeriesAnalytics(activeRange, controller.signal)
            : Promise.resolve(null),
          canViewAgents
            ? getAgentActivity(activeRange, controller.signal)
            : Promise.resolve(null),
          canViewReview
            ? getManualReview(activeRange, controller.signal)
            : Promise.resolve(null),
          canViewLeads
            ? listLeads({ limit: 1 }, controller.signal)
            : Promise.resolve(null),
        ]);
        if (requestVersion !== refreshVersion.current) return;
        setData(
          dashboard ||
            channels ||
            pipeline ||
            revenue ||
            reps ||
            series ||
            agents ||
            review
            ? {
                dashboard,
                channels,
                pipeline,
                revenue,
                reps,
                series,
                agents,
                review,
              }
            : null,
        );
        const nextLeads = leadRows?.items ?? [];
        setLeads(nextLeads);
        if (leadRows) {
          const nextSignature = JSON.stringify([
            leadRows.total,
            nextLeads[0] ?? null,
          ]);
          if (
            leadSignature.current !== null &&
            leadSignature.current !== nextSignature
          ) {
            setLeadRevision((current) => current + 1);
          }
          leadSignature.current = nextSignature;
        } else {
          leadSignature.current = null;
        }
      } catch (requestError) {
        if (
          requestVersion === refreshVersion.current &&
          !controller.signal.aborted
        ) {
          controller.abort();
          if (
            requestError instanceof ApiError &&
            requestError.status === 403 &&
            requestError.message === "workspace membership is required"
          ) {
            try {
              const session = await getSession();
              if (requestVersion !== refreshVersion.current) return;
              if (!session) {
                setAuth(null);
                return;
              }
              const workspaceChanged =
                session.current_workspace_id !== auth?.current_workspace_id ||
                session.current_workspace_id !== getActiveWorkspace();
              if (workspaceChanged) {
                setActiveWorkspace(session.current_workspace_id);
                setAuth(session);
                setData(null);
                setError(null);
              } else {
                setError(requestError.message);
              }
            } catch {
              setError(requestError.message);
            }
          } else if (
            requestError instanceof ApiError &&
            requestError.status === 401
          ) {
            setAuth(null);
          } else {
            setError(
              requestError instanceof Error
                ? requestError.message
                : "Analytics service unavailable",
            );
          }
        }
      } finally {
        if (requestVersion === refreshVersion.current) setLoading(false);
      }
    },
    [range, auth?.current_workspace_id, auth?.workspaces],
  );

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
    const permissions = permissionsForWorkspace(
      auth,
      auth?.current_workspace_id,
    );
    if (
      !auth ||
      !(
        [
          "leads.view",
          "analytics.view",
          "agents.view",
          "manual_review.view",
        ] as const
      ).some((permission) => can(permissions, permission))
    )
      return;
    const canPollLeads = can(permissions, "leads.view");
    const controller = new AbortController();
    let inFlight = false;
    let lastFullRefresh = Date.now();
    const poll = async () => {
      if (document.visibilityState !== "visible" || inFlight) return;
      inFlight = true;
      try {
        const result = canPollLeads
          ? await listLeads({ limit: 1 }, controller.signal)
          : null;
        const nextSignature = result
          ? JSON.stringify([result.total, result.items[0] ?? null])
          : null;
        // New leads update quickly. Also refresh periodically even if the
        // newest lead/count is unchanged: replies, older qualifications,
        // delivery results and analytics can change independently.
        if (
          (leadSignature.current !== null &&
            leadSignature.current !== nextSignature) ||
          Date.now() - lastFullRefresh >= 30000
        ) {
          await refresh();
          lastFullRefresh = Date.now();
        } else if (leadSignature.current === null) {
          leadSignature.current = nextSignature;
        }
      } catch (pollError) {
        if (pollError instanceof ApiError && pollError.status === 401)
          setAuth(null);
      } finally {
        inFlight = false;
      }
    };
    // Keep the dashboard, manual review count, and CRM lead archive in sync
    // with public intake without requiring a browser refresh.
    const timer = window.setInterval(() => void poll(), 5000);
    const onVisible = () => {
      if (document.visibilityState === "visible") {
        lastFullRefresh = 0;
        void poll();
      }
    };
    document.addEventListener("visibilitychange", onVisible);
    window.addEventListener("online", onVisible);
    return () => {
      controller.abort();
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisible);
      window.removeEventListener("online", onVisible);
    };
  }, [auth?.current_workspace_id, auth?.workspaces, refresh]);

  useEffect(() => {
    if (auth) return;
    setOpenaiSetupCard(null);
    setAiSetupCards([]);
    reasoningPromptDismissed.current.clear();
  }, [auth]);

  useEffect(() => {
    if (!auth) return;
    const workspace = auth.workspaces.find(
      (item) => item.id === auth.current_workspace_id,
    );
    if (!workspace) return;
    const permissions = new Set(workspace.effective_permissions);
    if (hasViewPermission(view, permissions)) return;
    const fallback =
      navigation.find((item) => hasViewPermission(item.id, permissions))?.id ??
      "settings";
    setView(fallback);
  }, [auth?.current_workspace_id, auth?.workspaces, view]);

  useEffect(() => {
    if (!auth) return;
    setOpenaiSetupCard(null);
    const workspace = auth.workspaces.find(
      (item) => item.id === auth.current_workspace_id,
    );
    if (
      !workspace?.effective_permissions.includes("integrations.manage") ||
      reasoningPromptDismissed.current.has(auth.current_workspace_id)
    )
      return;
    let active = true;
    void listIntegrations()
      .then((cards) => {
        if (!active) return;
        setAiSetupCards(cards.filter((card) => card.category.toLowerCase().includes("ai analysis")));
        const openai = cards.find((card) => card.provider === "openai");
        const resume = cards.find((card) => {
          const connectionId = card.connection?.id;
          return (
            card.category.toLowerCase().includes("ai analysis") &&
            connectionId !== undefined &&
            window.localStorage.getItem(`corral:reasoning-backfill:${connectionId}`) !== null
          );
        });
        // A saved key can be `connected` before verification; only ask for a key when none is stored.
        setOpenaiSetupCard(
          resume ?? (openai?.connection?.configured ? null : (openai ?? null)),
        );
      })
      .catch(() => {
        if (active) setOpenaiSetupCard(null);
      });
    return () => {
      active = false;
    };
  }, [auth?.user.id, auth?.current_workspace_id]);

  useEffect(
    () => () => {
      refreshController.current?.abort();
      leadController.current?.abort();
    },
    [],
  );

  const inspectLead = useCallback(
    async (id: string) => {
      if (
        !can(
          permissionsForWorkspace(auth, auth?.current_workspace_id),
          "leads.view",
        )
      )
        return;
      leadController.current?.abort();
      const controller = new AbortController();
      leadController.current = controller;
      const requestVersion = ++leadRequestVersion.current;
      setError(null);
      try {
        const lead = await getLead(id, controller.signal);
        if (requestVersion === leadRequestVersion.current)
          setSelectedLead(lead);
      } catch (requestError) {
        if (
          requestVersion === leadRequestVersion.current &&
          !controller.signal.aborted
        ) {
          if (requestError instanceof ApiError && requestError.status === 401)
            setAuth(null);
          else
            setError(
              requestError instanceof Error
                ? requestError.message
                : "Unable to load lead",
            );
        }
      }
    },
    [auth?.current_workspace_id, auth?.workspaces],
  );

  const openLeadSection = useCallback(
    (id: string, section: LeadDetailSection) => {
      if (
        !can(
          permissionsForWorkspace(auth, auth?.current_workspace_id),
          "leads.view",
        )
      )
        return;
      window.history.replaceState(
        { ...window.history.state, corralView: view },
        "",
        window.location.href,
      );
      window.history.pushState(
        { corralView: "leads" },
        "",
        leadSectionHref(id, section),
      );
      setView("leads");
      setLeadSection(section);
      setSelectedLead((current) => (current?.id === id ? current : null));
      void inspectLead(id);
      if (window.matchMedia("(max-width: 760px)").matches) setNavOpen(false);
    },
    [inspectLead, auth?.current_workspace_id, auth?.workspaces, view],
  );

  const handleInspectLead = useCallback(
    (id: string) =>
      openLeadSection(id, view === "leads" ? leadSection : "overview"),
    [openLeadSection, view, leadSection],
  );

  const changeLeadSection = useCallback(
    (section: LeadDetailSection) => {
      if (!selectedLead || section === leadSection) return;
      window.history.pushState(
        { corralView: "leads" },
        "",
        leadSectionHref(selectedLead.id, section),
      );
      setLeadSection(section);
    },
    [selectedLead, leadSection],
  );

  const closeLead = useCallback(() => {
    leadController.current?.abort();
    leadRequestVersion.current += 1;
    setSelectedLead(null);
    if (
      new URLSearchParams(window.location.search).has("lead") ||
      new URLSearchParams(window.location.search).has("lead_tab")
    ) {
      const url = new URL(window.location.href);
      url.searchParams.delete("lead");
      window.history.replaceState({ corralView: "leads" }, "", url);
    }
  }, []);

  useEffect(() => {
    if (!auth || !can(workspacePermissions, "leads.view")) return;
    const leadId = new URLSearchParams(window.location.search).get("lead");
    if (leadId && /^[0-9a-f-]{36}$/i.test(leadId)) void inspectLead(leadId);
  }, [auth?.current_workspace_id, workspacePermissions, inspectLead]);

  useEffect(() => {
    const onPopState = () => {
      if (!auth) return;
      const params = new URLSearchParams(window.location.search);
      const restoredView = pageFromSearch(window.location.search);
      setView(restoredView);
      setPageSection(sectionFromSearch(window.location.search, restoredView));
      const restoredRange = params.get("range");
      setRange(
        restoredRange === "weekly" || restoredRange === "annual"
          ? restoredRange
          : "monthly",
      );
      const id = params.get("lead");
      setLeadSection(parseLeadSection(params.get("lead_tab")));
      if (
        id &&
        /^[0-9a-f-]{36}$/i.test(id) &&
        can(workspacePermissions, "leads.view")
      ) {
        setView("leads");
        setSelectedLead((current) => (current?.id === id ? current : null));
        void inspectLead(id);
      } else {
        leadController.current?.abort();
        leadRequestVersion.current += 1;
        setSelectedLead(null);
      }
    };
    window.addEventListener("popstate", onPopState);
    return () => window.removeEventListener("popstate", onPopState);
  }, [auth?.current_workspace_id, workspacePermissions, inspectLead]);

  useEffect(() => {
    if (authLoading) return;
    const url = new URL(window.location.href);
    url.searchParams.set("view", view);
    url.searchParams.set("range", range);
    if (pageSection && view !== "leads")
      url.searchParams.set("section", pageSection);
    else url.searchParams.delete("section");
    if (view === "leads") url.searchParams.set("lead_tab", leadSection);
    window.history.replaceState(
      { ...window.history.state, corralView: view },
      "",
      url,
    );
  }, [authLoading, view, range, pageSection, leadSection]);

  useEffect(() => {
    window.scrollTo({ top: 0, behavior: "instant" });
  }, [view, pageSection]);

  function navigate(next: AppView, section = "") {
    if (next === "leads" && section && selectedLead) {
      changeLeadSection(parseLeadSection(section));
      return;
    }
    if (next !== "leads") closeLead();
    const url = new URL(window.location.href);
    url.searchParams.set("view", next);
    url.searchParams.set("range", range);
    url.searchParams.delete("lead");
    url.searchParams.delete("lead_tab");
    if (section && next !== "leads") url.searchParams.set("section", section);
    else url.searchParams.delete("section");
    if (next === "leads") {
      setSelectedLead(null);
      const leadTab = parseLeadSection(section);
      setLeadSection(leadTab);
      url.searchParams.set("lead_tab", leadTab);
    }
    window.history.pushState({ corralView: next }, "", url);
    setView(next);
    setPageSection(section);
    if (window.matchMedia("(max-width: 760px)").matches) setNavOpen(false);
  }

  function navigationChildren(id: AppView) {
    return (navigationSections[id] ?? []).filter(
      (child) =>
        !child.permission || can(workspacePermissions, child.permission),
    );
  }

  function navigationItemActive(item: { id: View }) {
    if (view === item.id) return true;
    const childView = navigationChildren(item.id).find((child) => child.view === view);
    // Keep a standalone top-level destination (for example Users) as the sole
    // active item when it is also linked from a settings submenu.
    return Boolean(
      childView &&
        !navigation.some((entry) => entry.id === childView.view),
    );
  }

  function navigationActiveChild(id: View) {
    if (view === "leads") return leadSection;
    if (view === id) return pageSection;
    return navigationChildren(id).some((child) => child.view === view) ? view : pageSection;
  }

  function navigateChild(id: View, childId?: string) {
    const child = navigationChildren(id).find((item) => item.id === childId);
    navigate(child?.view ?? id, child?.view ? "" : childId);
  }

  function toggleTheme() {
    const next = theme === "light" ? "dark" : "light";
    document.documentElement.dataset.theme = next;
    setTheme(next);
  }

  const visibleNavigation = navigation.filter((item) =>
    hasViewPermission(item.id, workspacePermissions),
  );
  const metadata = data?.dashboard?.metadata;
  useEffect(() => {
    const controller = new AbortController();
    setBranding({ name: null, logo_url: null, accent: "#1e6b52" });
    if (
      auth &&
      can(
        permissionsForWorkspace(auth, auth.current_workspace_id),
        "leads.view",
      )
    ) {
      void opsGet<typeof branding>("/v1/ops/branding", controller.signal)
        .then((value) => {
          if (!controller.signal.aborted) setBranding(value);
        })
        .catch(() => {});
    }
    return () => controller.abort();
  }, [auth?.current_workspace_id]);
  async function switchWorkspace(workspaceId: string) {
    refreshController.current?.abort();
    leadController.current?.abort();
    closeLead();
    const session = await changeWorkspace(workspaceId);
    setData(null);
    setSelectedLead(null);
    setAuth(session);
  }
  const accentLuminance = branding.accent
    .slice(1)
    .match(/.{2}/g)
    ?.map((value) => {
      const channel = parseInt(value, 16) / 255;
      return channel <= 0.04045
        ? channel / 12.92
        : ((channel + 0.055) / 1.055) ** 2.4;
    }) ?? [0, 0, 0];
  const accentText =
    0.2126 * accentLuminance[0] +
      0.7152 * accentLuminance[1] +
      0.0722 * accentLuminance[2] >
    0.179
      ? "#000000"
      : "#ffffff";
  const headerShortcuts = [
    {
      id: "dialer",
      label: "Dialer",
      icon: "dialer" as const,
      description: "Connect a calling provider to start confirmed calls from a lead.",
      destination: "Open calling integrations →",
      onClick: () => navigate("dialer"),
    },
    {
      id: "leads",
      label: "Leads",
      icon: "leads" as const,
      description: "Review new leads, qualification signals and routing decisions.",
      destination: "Open lead workspace →",
      onClick: () => navigate("leads", "overview"),
    },
    {
      id: "review",
      label: "Review",
      icon: "review" as const,
      description: "Resolve leads that need human judgment before work continues.",
      destination: "Open manual review →",
      onClick: () => navigate("review"),
    },
    {
      id: "reports",
      label: "Reports",
      icon: "analytics" as const,
      description: "See channel, pipeline and revenue performance for this workspace.",
      destination: "Open analytics →",
      onClick: () => navigate("analytics"),
    },
  ] as const;
  if (authLoading) return <LoadingState />;
  if (!auth) {
    return (
      <AuthView
        theme={theme}
        onToggleTheme={toggleTheme}
        onAuthenticated={(session) => {
          setActiveWorkspace(session.current_workspace_id);
          setAuth(session);
        }}
      />
    );
  }
  return (
    <div
      className={`app-shell ${navOpen ? "nav-expanded" : "nav-collapsed"}`}
      style={
        {
          "--workspace-accent": branding.accent,
          "--workspace-accent-text": accentText,
        } as CSSProperties
      }
    >
      <div className="app-ambient ambient-a" />
      <ActivityToasts />
      <NativeSelectEnhancer />
      <div className="app-ambient ambient-b" />
      <img
        className="app-twinkle app-twinkle-a"
        src="/asset/brand/twinkle-cap.svg"
        alt=""
        aria-hidden="true"
      />
      <img
        className="app-twinkle app-twinkle-b"
        src="/asset/brand/twinkle-role.svg"
        alt=""
        aria-hidden="true"
      />
      <aside
        id="corral-navigation"
        className={`side-rail ${navOpen ? "open" : ""}`}
        aria-label="Corral navigation"
        onKeyDown={(event) => {
          if (!window.matchMedia("(max-width: 760px)").matches) return;
          if (event.key === "Escape") { event.preventDefault(); setNavOpen(false); }
          else trapFocus(event);
        }}
        aria-hidden={!navOpen}
        inert={!navOpen}
      >
        <div className="rail-brand-row">
          <button
            className="brand-lockup"
            onClick={() => navigate("dashboard")}
            aria-label={(branding.name || "Corral") + " home"}
          >
            <img
              src={branding.logo_url || "/asset/brand/corral-mark.png"}
              alt=""
            />
            <span>
              <b>{branding.name || "Corral"}</b>
              <small className="rail-brand-tagline">
                <span>Powered by</span>
                <span>Ingenuity Systems</span>
              </small>
            </span>
          </button>
          <button
            className="rail-close"
            onClick={() => setNavOpen(false)}
            aria-label="Collapse navigation"
            title="Collapse navigation"
            aria-controls="corral-navigation"
            aria-expanded={navOpen}
          >
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <path d="m14.5 5-5 7 5 7" />
            </svg>
          </button>
        </div>
        <nav aria-label="Primary navigation">
          {visibleNavigation.map((item, index) => (
            <div key={item.id}>
              {item.group && (
                <>
                  {index > 0 && <span className="nav-separator" aria-hidden="true" />}
                  <span className="nav-group">{item.group}</span>
                </>
              )}
              <NavigationMenuItem
                item={{ ...item, children: navigationChildren(item.id) }}
                enabled={navOpen}
                icon={<NavIcon id={item.id} />}
                active={
                  navigationItemActive(item) || (item.id === "settings" && view === "icp")
                }
                activeChild={navigationActiveChild(item.id)}
                badge={item.id === "review" ? data?.review?.total : undefined}
                onNavigate={(child) => navigateChild(item.id, child)}
              />
            </div>
          ))}
        </nav>
        {can(workspacePermissions, "leads.view") && (
          <PinnedLeadNavigation
            pins={pinnedSections.pins}
            notice={pinnedSections.notice}
            activeLeadId={view === "leads" ? selectedLead?.id : undefined}
            activeSection={leadSection}
            onOpen={(pin) => openLeadSection(pin.leadId, pin.section)}
            onRemove={pinnedSections.remove}
          />
        )}
        <div className="rail-foot">
          <div className="workspace-mark">
            <img src="/asset/brand/corral-mark.png" alt="" />
            <WorkspaceSwitcher session={auth} onSelect={switchWorkspace} />
          </div>
          <span className={`system-state ${error ? "is-offline" : "is-online"}`} aria-live="polite">
            <i aria-hidden="true" /> {error ? "Offline" : "Online"}
          </span>
          <button
            className="signout-button"
            onClick={() => {
              refreshController.current?.abort();
              leadController.current?.abort();
              void signOut().finally(() => {
                setAuth(null);
                setData(null);
                setSelectedLead(null);
              });
            }}
          >
            Sign out
          </button>
        </div>
      </aside>
      {!navOpen && (
        <aside className="mini-rail" aria-label="Collapsed Corral navigation">
          <button
            className="mini-rail-expand"
            onClick={() => setNavOpen(true)}
            aria-controls="corral-navigation"
            aria-expanded={false}
            aria-label="Expand navigation"
            title="Expand navigation"
          >
            <img src="/asset/brand/corral-mark.png" alt="" />
            <span aria-hidden="true">›</span>
          </button>
          <nav className="mini-rail-nav" aria-label="Primary navigation">
            {visibleNavigation.map((item, index) => {
              const active =
                navigationItemActive(item) ||
                (item.id === "settings" && (view === "team" || view === "icp"));
              return (
                <div className="mini-rail-entry" key={item.id}>
                  {item.group && index > 0 && (
                    <span className="mini-rail-separator" aria-hidden="true" />
                  )}
                  <NavigationMenuItem
                    item={{ ...item, children: navigationChildren(item.id) }}
                    icon={<NavIcon id={item.id} />}
                    active={active}
                    collapsed
                    activeChild={navigationActiveChild(item.id)}
                    badge={item.id === "review" ? data?.review?.total : undefined}
                    onNavigate={(child) => navigateChild(item.id, child)}
                  />
                </div>
              );
            })}
          </nav>
        </aside>
      )}
      <main className="product-main" inert={navOpen && window.matchMedia("(max-width: 760px)").matches}>
        <header className="product-bar">
          <div className="bar-leading">
            <button
              className="menu-button"
              onClick={() => setNavOpen(true)}
              aria-controls="corral-navigation"
              aria-expanded={navOpen}
              aria-label="Open navigation"
            >
              ☰
            </button>
            <div className="breadcrumb">
              <span>{branding.name || "Corral"}</span>
              <i>/</i>
              <b>
                {view === "team"
                  ? "Users & Permissions"
                  : view === "icp"
                    ? "Ideal Customer Profile"
                    : navigation.find((item) => item.id === view)?.label}
              </b>
            </div>
          </div>
          <div className="bar-actions">
            <div className="header-shortcuts" aria-label="Quick shortcuts">
              {headerShortcuts.map((shortcut) => <div className="header-shortcut" key={shortcut.id}>
                <button
                  type="button"
                  className="header-shortcut-trigger"
                  onClick={shortcut.onClick}
                  aria-label={shortcut.label}
                  aria-describedby={`header-shortcut-help-${shortcut.id}`}
                >
                  {shortcut.icon === "dialer"
                    ? <span className="header-shortcut-icon" aria-hidden="true">☎</span>
                    : <span className="header-shortcut-icon" aria-hidden="true"><NavIcon id={shortcut.icon} /></span>}
                  <span className="header-shortcut-label">{shortcut.label}</span>
                </button>
                <div className="header-shortcut-popover" id={`header-shortcut-help-${shortcut.id}`} role="tooltip">
                  <strong>{shortcut.label}</strong>
                  <p>{shortcut.description}</p>
                  <button
                    type="button"
                    className="header-shortcut-destination"
                    onClick={shortcut.onClick}
                  >
                    {shortcut.destination}
                  </button>
                </div>
              </div>)}
            </div>
            <CommandPalette
              key={auth.current_workspace_id}
              pages={visibleNavigation}
              canSearch={can(workspacePermissions, "leads.view")}
              onNavigate={navigate}
              onInspect={(id) => {
                handleInspectLead(id);
                setView("leads");
              }}
            />
            {["dashboard", "leads", "pipeline", "agents", "analytics", "review", "team"].includes(view) && (
              <RangePicker value={range} onChange={setRange} />
            )}
            <button
              className="icon-button"
              onClick={() => void refresh()}
              aria-label="Refresh data"
            >
              ↻
            </button>
            <button
              className="guide-launch"
              onClick={() => setTourOpen(true)}
              aria-haspopup="dialog"
              aria-label="Open Corral product guide"
              title="Open Corral product guide"
            >
              <svg viewBox="0 0 20 20" aria-hidden="true">
                <circle cx="10" cy="10" r="7.5" />
                <path d="M7.8 7.4a2.3 2.3 0 1 1 3.8 1.7c-.9.8-1.6 1.1-1.6 2.3M10 14.2v.1" />
              </svg>
              <span>Guide</span>
            </button>
            <button
              className="theme-toggle"
              onClick={toggleTheme}
              aria-label={`Switch to ${theme === "light" ? "dark" : "light"} mode`}
            >
              <span aria-hidden="true">{theme === "light" ? "◐" : "☼"}</span>
              <span className="theme-label">
                {theme === "light" ? "Dark" : "Light"}
              </span>
            </button>
          </div>
        </header>
        {error && (
          <div className="api-error" role="alert">
            <div>
              <b>Control plane unavailable</b>
              <span>{error}</span>
            </div>
            <button onClick={() => void refresh()}>Retry</button>
          </div>
        )}
        {!hasViewPermission(view, workspacePermissions) ||
        (loading &&
          !data &&
          view !== "settings" &&
          view !== "team" &&
          view !== "icp") ? (
          <ContentLoadingState />
        ) : (
          <Suspense fallback={<ContentLoadingState />}>
            <div
              className="view-transition"
              key={auth.current_workspace_id + view}
            >
              {view === "dashboard" && (
                <DashboardView
                  key={auth?.user.id ?? "local"}
                  data={data}
                  onInspect={(id) => {
                    handleInspectLead(id);
                    setView("leads");
                  }}
                  userId={auth?.user.id ?? "local"}
                />
              )}
              {view === "leads" && (
                <LeadsView
                  selected={selectedLead}
                  activeSection={leadSection}
                  onSectionChange={changeLeadSection}
                  isSectionPinned={pinnedSections.pins.some(
                    (pin) =>
                      pin.leadId === selectedLead?.id &&
                      pin.section === leadSection,
                  )}
                  onTogglePin={() => {
                    if (selectedLead)
                      pinnedSections.toggle({
                        leadId: selectedLead.id,
                        company: selectedLead.company_name,
                        section: leadSection,
                      });
                  }}
                  revision={leadRevision}
                  onInspect={handleInspectLead}
                  onClose={closeLead}
                  canEdit={can(workspacePermissions, "leads.edit")}
                  canExport={can(workspacePermissions, "leads.export")}
                  canCrmSync={can(workspacePermissions, "crm.sync")}
                  canCall={can(workspacePermissions, "agents.execute") && can(workspacePermissions, "leads.edit")}
                  onUpdated={(lead) => {
                    setSelectedLead(lead);
                    setLeads((current) =>
                      current.map((item) =>
                        item.id === lead.id ? lead : item,
                      ),
                    );
                  }}
                  onDeleted={(leadId) => {
                    closeLead();
                    pinnedSections.removeLead(leadId);
                    setLeads((current) =>
                      current.filter((item) => item.id !== leadId),
                    );
                    void refresh();
                  }}
                  onCreated={(lead) => {
                    setLeads((current) => [
                      lead,
                      ...current.filter((item) => item.id !== lead.id),
                    ]);
                    setSelectedLead(lead);
                    setLeadSection("overview");
                    window.history.pushState(
                      { corralView: "leads" },
                      "",
                      leadSectionHref(lead.id, "overview"),
                    );
                    void refresh();
                  }}
                />
              )}
              {view === "pipeline" && (
                <PipelineView
                  data={data?.pipeline ?? null}
                  onInspect={(id) => {
                    handleInspectLead(id);
                    setView("leads");
                  }}
                />
              )}
              {view === "agents" && (
                <AgentsView
                  data={data?.agents ?? null}
                  pipeline={data?.pipeline ?? null}
                />
              )}
              {view === "analytics" && (
                <>
                  <AnalyticsView
                    key={auth?.user.id ?? "local"}
                    data={data}
                    userId={auth?.user.id ?? "local"}
                    selectedSection={pageSection}
                  />
                  <AdvancedAnalyticsPanel />
                </>
              )}
              {view === "review" && (
                <ManualReviewView
                  data={data?.review ?? null}
                  onInspect={(id) => {
                    openLeadSection(id, "ai");
                  }}
                />
              )}
              {(view === "contacts" || view === "accounts") && (
                <RecordsView
                  mode={view === "contacts" ? "contacts" : "companies"}
                  onInspect={(id) => {
                    handleInspectLead(id);
                    setView("leads");
                  }}
                  canEdit={can(workspacePermissions, "leads.edit")}
                  canResearch={can(workspacePermissions, "agents.execute")}
                />
              )}
              {view === "policies" && (
                <PoliciesView
                  canManage={can(workspacePermissions, "workspace.manage")}
                />
              )}
              {view === "compliance" && <ComplianceView />}
              {view === "intelligence" && (
                <IntelligenceView
                  onInspect={(id) => {
                    handleInspectLead(id);
                    setView("leads");
                  }}
                  onIcp={() => navigate("icp")}
                  canExecute={can(workspacePermissions, "agents.execute")}
                  canReview={can(workspacePermissions, "manual_review.manage")}
                  canLearn={can(workspacePermissions, "icp.manage")}
                />
              )}
              {view === "automations" && (
                <AutomationsView
                  canManage={can(workspacePermissions, "workspace.manage")}
                  canEdit={can(workspacePermissions, "leads.edit")}
                  onInspect={(id) => {
                    handleInspectLead(id);
                    setView("leads");
                  }}
                />
              )}
              {view === "integrations" && (
                <IntegrationsView
                  key={auth.current_workspace_id}
                  permissions={workspacePermissions}
                  selectedSection={pageSection}
                />
              )}
              {view === "dialer" && (
                <DialerView selectedSection={pageSection} onOpenIntegrations={() => navigate("integrations", "calling")} onOpenLead={handleInspectLead} />
              )}
              {view === "settings" && (
                <SettingsView
                  metadata={metadata}
                  selectedSection={pageSection}
                  onIntegrations={() => navigate("integrations")}
                  onTeam={() => navigate("team")}
                  onIcp={() => navigate("icp")}
                  canViewTeam={can(workspacePermissions, "members.view")}
                  canViewIcp={can(workspacePermissions, "icp.view")}
                  canViewRouting={can(workspacePermissions, "routing.view")}
                  canManageRouting={can(workspacePermissions, "routing.manage")}
                  canViewPublicIntake={can(
                    workspacePermissions,
                    "integrations.manage",
                  )}
                  canRotatePublicIntake={can(
                    workspacePermissions,
                    "integrations.manage",
                  )}
                  canManageEmail={can(
                    workspacePermissions,
                    "integrations.manage",
                  )}
                />
              )}
              {view === "team" && (
                <TeamView
                  key={auth.current_workspace_id}
                  auth={auth}
                  workspaceName={
                    auth.workspaces.find(
                      (workspace) => workspace.id === auth.current_workspace_id,
                    )?.name ||
                    metadata?.workspace_name ||
                    "Current workspace"
                  }
                  onBack={() => setView("settings")}
                />
              )}
              {view === "icp" && (
                <IcpView
                  key={auth.current_workspace_id}
                  canManage={can(workspacePermissions, "icp.manage")}
                  onBack={() => setView("settings")}
                />
              )}
            </div>
          </Suspense>
        )}
        <footer className="product-footer">
          <span>{branding.name || "Corral"} · Powered by Ingenuity Systems</span>
          <nav className="product-legal-links" aria-label="Legal information">
            <a href="/terms.html">Terms</a>
            <a href="/privacy.html">Privacy</a>
            <a href="mailto:harmainmughal8@gmail.com">Contact</a>
          </nav>
          <span>
            Generated{" "}
            {metadata ? new Date(metadata.generated_at).toLocaleString() : "—"}{" "}
            · Workspace {getActiveWorkspace().slice(0, 8)}
          </span>
        </footer>
      </main>
      {navOpen && (
        <button
          className="nav-scrim"
          onClick={() => setNavOpen(false)}
          aria-label="Close navigation"
        />
      )}
      {openaiSetupCard && !tourOpen && view === "dashboard" && (
        <OpenAISetupPrompt
          card={openaiSetupCard}
          cards={aiSetupCards}
          onVerified={() => {
            void refresh();
          }}
          onClose={() => setOpenaiSetupCard(null)}
          onLater={() => {
            reasoningPromptDismissed.current.add(auth.current_workspace_id);
            setOpenaiSetupCard(null);
          }}
          onSettings={() => {
            reasoningPromptDismissed.current.add(auth.current_workspace_id);
            setOpenaiSetupCard(null);
            setView("integrations");
          }}
        />
      )}
      <ProductTour
        open={tourOpen}
        currentView={view}
        onNavigate={navigate}
        onDismiss={closeTour}
        onComplete={closeTour}
      />
    </div>
  );
}

function ContentLoadingState() {
  return (
    <div
      className="content-loading"
      aria-busy="true"
      aria-label="Loading workspace data"
    >
      <div className="skeleton-hero">
        <i />
        <i />
        <span />
      </div>
      <div className="skeleton-metrics">
        {Array.from({ length: 4 }, (_, index) => (
          <i key={index} />
        ))}
      </div>
      <div className="skeleton-content">
        <i />
        <i />
      </div>
    </div>
  );
}

function RangePicker({
  value,
  onChange,
}: {
  value: TimeRange;
  onChange: (value: TimeRange) => void;
}) {
  return (
    <div className="range-picker" aria-label="Analytics time range">
      {(["weekly", "monthly", "annual"] as const).map((item) => (
        <button
          className={value === item ? "active" : ""}
          aria-pressed={value === item}
          onClick={() => onChange(item)}
          key={item}
        >
          {item.slice(0, 1).toUpperCase() + item.slice(1)}
        </button>
      ))}
    </div>
  );
}

function LoadingState() {
  return (
    <div className="loading-state">
      <div className="loading-core">
        <i />
        <i />
        <i />
      </div>
      <b>Synchronizing workspace intelligence</b>
      <span>Loading analytics, pipeline and agent activity…</span>
    </div>
  );
}

export default App;
