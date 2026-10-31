import { trapFocus } from "./components/focusTrap";
import {
  lazy,
  Suspense,
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
} from "react";

import {
  changeWorkspace,
  getActiveWorkspace,
  getAdvancedAnalytics,
  getAgentActivity,
  getChannelAnalytics,
  getDashboard,
  getExtendedAnalytics,
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
import { DialerPhoneDock } from "./components/DialerPhoneDock";
import { useTheme } from "./theme";
import { SUPPORT_EMAIL } from "./brand";
import { useOnline } from "./useOnline";
import {
  OPEN_DIALER_PHONE_EVENT,
  isDialerPhoneAvailable,
  isEmbeddedDialerConnection,
  ringCentralEmbedClientId,
  type DialerPhoneConnection,
} from "./components/dialerPhoneConfig";
import { LeadNotificationCenter } from "./components/LeadNotificationCenter";
import { PHONE_CALL_ENDED } from "./components/phoneBridge";
import { useAssignmentNotifications } from "./useAssignmentNotifications";
import { CorralAgentDock } from "./components/CorralAgentDock";
import { WorkspaceSwitcher } from "./components/WorkspaceSwitcher";
import { NativeSelectEnhancer } from "./components/NativeSelectEnhancer";
import { notifyActivity } from "./activity";
const ProductTour = lazy(() =>
  import("./components/ProductTour").then((module) => ({ default: module.ProductTour })),
);
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
const EmailWorkspace = lazy(() =>
  import("./views/EmailWorkspace").then((module) => ({
    default: module.EmailWorkspace,
  })),
);
const CalendarWorkspace = lazy(() =>
  import("./views/CalendarWorkspace").then((module) => ({
    default: module.CalendarWorkspace,
  })),
);
import { CommandPalette, OPEN_SEARCH_EVENT } from "./components/CommandPalette";
import { useNewLeadIndicators } from "./useNewLeadIndicators";
import { opsGet } from "./operationsApi";
import { relayOAuthCallbackFromPopup } from "./oauthPopup";
import { usePinnedLeadSections } from "./components/PinnedLeadNavigation";
import { Sidebar, TabBar, Toolbar, type NavEntry, type NavSection } from "./v3/shell/Shell";
import type { IconName } from "./v3/ui/Icon";
import { Icon } from "./v3/ui/Icon";
import { Banner, Button, IconButton, Segmented } from "./v3/ui/primitives";
import { LeadsScreen } from "./v3/screens/LeadsScreen";
import { ReviewScreen, TodayScreen } from "./v3/screens/WorkScreens";
import { PipelineBoard } from "./v3/screens/PipelineBoard";
import { PerformanceScreen, RevenueScreen } from "./v3/screens/InsightScreens";
import { PhonePanel } from "./v3/shell/PhonePanel";
import { NewMenu, QuickAccess, SearchField } from "./v3/shell/CommandBar";
import { LEAD_ACTION_EVENT, PHONE_EVENT, type LeadAction } from "./v3/shell/events";
import { bindDealWorkspace, pollDealRecords } from "./v3/deals/dealStore";
import { DealSyncStatus } from "./v3/deals/DealSyncStatus";
import { recordedStages, useDealViews, useWorkspaceLeads } from "./v3/deals/useWorkspaceLeads";
import { Monogram, Sheet } from "./v3/ui/primitives";
import type { LeadIntent } from "./v3/screens/LeadRecord";
import { AGENT_STATE_EVENT, TOGGLE_AGENT_EVENT } from "./components/shell/agentEvents";
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
  SettingsView,
} from "./views/ProductViews";
import type { AnalyticsBundle } from "./views/ProductViews";
import { EdgeFadeNav } from "./v3/ui/useScrollEdges";

type View =
  | "dashboard"
  | "leads"
  | "email"
  | "calendar"
  | "contacts"
  | "pipeline"
  | "revenue"
  | "performance"
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

// Navigation follows the day: what needs you, the people and companies it
// concerns, what Corral learns from them, and how the workspace is set up.
const navigation: Array<{ id: View; label: string; group: string; icon: IconName }> = [
  { id: "dashboard", label: "Today", group: "main", icon: "today" },
  { id: "leads", label: "Leads", group: "main", icon: "leads" },
  { id: "pipeline", label: "Pipeline", group: "main", icon: "pipeline" },
  { id: "revenue", label: "Revenue", group: "main", icon: "revenue" },
  { id: "review", label: "Review", group: "work", icon: "review" },
  { id: "email", label: "Inbox", group: "work", icon: "inbox" },
  { id: "calendar", label: "Calendar", group: "work", icon: "calendar" },
  { id: "dialer", label: "Calls", group: "work", icon: "calls" },
  { id: "contacts", label: "Contacts", group: "records", icon: "contacts" },
  { id: "accounts", label: "Companies", group: "records", icon: "companies" },
  { id: "performance", label: "Team Performance", group: "insights", icon: "trophy" },
  { id: "analytics", label: "Analytics", group: "insights", icon: "analytics" },
  { id: "agents", label: "AI Activity", group: "insights", icon: "agents" },
  { id: "intelligence", label: "Research & Coaching", group: "insights", icon: "research" },
  { id: "automations", label: "Automations", group: "insights", icon: "automations" },
  { id: "integrations", label: "Integrations", group: "workspace", icon: "integrations" },
  { id: "team", label: "Team", group: "workspace", icon: "team" },
  { id: "settings", label: "Settings", group: "workspace", icon: "settings" },
];
const NAV_GROUP_LABELS: Record<string, string | undefined> = {
  main: undefined,
  work: "Work",
  records: "Records",
  insights: "Insights",
  workspace: "Workspace",
};
const TAB_BAR_VIEWS: View[] = ["dashboard", "leads", "pipeline", "revenue"];

// Unchanged from the previous release: when a person cannot open the requested
// view, they land on the first permitted view in this order.
const FALLBACK_ORDER: View[] = [
  "dashboard", "leads", "email", "calendar", "contacts", "accounts", "pipeline", "agents",
  "analytics", "review", "integrations", "dialer", "team", "settings",
];
const LEAD_ACTION_SECTION: Record<LeadAction, LeadDetailSection> = {
  open: "overview",
  call: "calls",
  email: "email",
  quote: "deal",
  deal: "deal",
};

const VIEW_LABELS: Record<AppViewLabelKey, string> = {
  icp: "Ideal customer profile",
  policies: "Routing & capacity",
  compliance: "Consent & governance",
};
type AppViewLabelKey = "icp" | "policies" | "compliance";

function viewLabel(view: string) {
  return (
    navigation.find((item) => item.id === view)?.label ??
    VIEW_LABELS[view as AppViewLabelKey] ??
    "Workspace"
  );
}

const VIEW_PERMISSIONS: Partial<Record<View, string>> = {
  dashboard: "analytics.view",
  leads: "leads.view",
  email: "integrations.view",
  calendar: "leads.view",
  contacts: "leads.view",
  accounts: "leads.view",
  intelligence: "agents.view",
  automations: "agents.view",
  policies: "routing.view",
  compliance: "workspace.manage",
  pipeline: "analytics.view",
  revenue: "analytics.view",
  performance: "analytics.view",
  agents: "agents.view",
  analytics: "analytics.view",
  review: "manual_review.view",
  integrations: "integrations.view",
  dialer: "dialer.call",
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
  const viewRef = useRef<AppView>(view);
  viewRef.current = view;
  const [dialerConnections, setDialerConnections] = useState<DialerPhoneConnection[]>([]);
  const [preferredDialerId, setPreferredDialerId] = useState("");
  const [dialerPhoneOpen, setDialerPhoneOpen] = useState(false);
  const [dialerCallActive, setDialerCallActive] = useState(false);
  const preferredDialerRef = useRef("");
  const dialerPreferenceKey =
    auth?.user.id && auth.current_workspace_id
      ? `corral-preferred-dialer:v1:${auth.user.id}:${auth.current_workspace_id}`
      : null;
  const selectedDialer = dialerConnections.find(
    (connection) => connection.id === preferredDialerId,
  ) ?? null;

  useEffect(() => {
    preferredDialerRef.current = preferredDialerId;
  }, [preferredDialerId]);

  useEffect(() => {
    preferredDialerRef.current = "";
    setDialerConnections([]);
    setPreferredDialerId("");
    setDialerPhoneOpen(false);
    setDialerCallActive(false);
    const preferenceKey = dialerPreferenceKey ?? "";
    if (!preferenceKey) return;

    let active = true;
    async function refreshDialerConnections() {
      try {
        const cards = await listIntegrations();
        if (!active) return;
        const available = cards
          .filter((card) => /dialer|voice/i.test(card.category))
          .flatMap((card) =>
            card.connections
              .filter(
                (connection) =>
                  connection.is_active && connection.status === "verified",
              )
              .map((connection) => ({
                id: connection.id,
                provider: card.provider,
                account_name: connection.account_name,
                status: connection.status,
                is_active: connection.is_active,
              })),
          );
        const currentId = preferredDialerRef.current;
        const savedId = window.localStorage.getItem(preferenceKey);
        const next =
          available.find((connection) => connection.id === savedId) ??
          available.find((connection) => connection.id === currentId) ??
          available.find(isEmbeddedDialerConnection) ??
          available[0] ??
          null;
        const nextId = next?.id ?? "";
        setDialerConnections(available);
        preferredDialerRef.current = nextId;
        setPreferredDialerId(nextId);
        if (nextId !== currentId) {
          setDialerPhoneOpen(false);
          setDialerCallActive(false);
        }
        if (nextId) {
          window.localStorage.setItem(preferenceKey, nextId);
        } else {
          window.localStorage.removeItem(preferenceKey);
        }
      } catch (error) {
        console.warn("Unable to refresh connected dialers", error);
      }
    }

    const onIntegrationsChanged = () => void refreshDialerConnections();
    window.addEventListener("corral:integrations-changed", onIntegrationsChanged);
    void refreshDialerConnections();
    return () => {
      active = false;
      window.removeEventListener("corral:integrations-changed", onIntegrationsChanged);
    };
  }, [dialerPreferenceKey]);

  const selectPreferredDialer = useCallback((connectionId: string) => {
    if (dialerCallActive) return;
    const next = dialerConnections.find((connection) => connection.id === connectionId);
    const nextId = next?.id ?? "";
    preferredDialerRef.current = nextId;
    setPreferredDialerId(nextId);
    if (dialerPreferenceKey) {
      if (nextId) window.localStorage.setItem(dialerPreferenceKey, nextId);
      else window.localStorage.removeItem(dialerPreferenceKey);
    }
    setDialerPhoneOpen(false);
  }, [dialerCallActive, dialerConnections, dialerPreferenceKey]);
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
  const [analytics, setData] = useState<Partial<AnalyticsBundle> | null>(null);
  const data = useMemo<AnalyticsBundle | null>(
    () => (analytics ? { ...EMPTY_ANALYTICS, ...analytics } : null),
    [analytics],
  );
  const loadedRange = useRef<Partial<Record<AnalyticsKey, TimeRange>>>({});
  const inflightKeys = useRef(new Set<AnalyticsKey>());
  const [leads, setLeads] = useState<Lead[]>([]);
  const [leadNotificationSnapshot, setLeadNotificationSnapshot] = useState<{
    workspaceId: string;
    leads: Lead[];
  }>({ workspaceId: "", leads: [] });
  const {
    unseenLeadIds,
    observeLeadSnapshot,
    markSeen: markLeadSeen,
  } = useNewLeadIndicators(auth?.user.id, auth?.current_workspace_id);
  const notificationLeads =
    leadNotificationSnapshot.workspaceId === auth?.current_workspace_id
      ? leadNotificationSnapshot.leads
      : [];
  const unseenLeadIdSet = useMemo(
    () => new Set(unseenLeadIds),
    [unseenLeadIds],
  );
  const unseenManualReviewLeadCount = useMemo(
    () => data?.review?.items.filter((lead) => unseenLeadIdSet.has(lead.id)).length ?? 0,
    [data?.review?.items, unseenLeadIdSet],
  );
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
  const [isMobile, setIsMobile] = useState(
    () => typeof window !== "undefined" && window.matchMedia("(max-width: 767px)").matches,
  );
  const [sidebarHidden, setSidebarHidden] = useState(() => {
    try {
      return window.localStorage.getItem("corral-sidebar") === "hidden";
    } catch {
      return false;
    }
  });
  const [agentOpen, setAgentOpen] = useState(false);
  const toggleSidebar = useCallback(() => {
    setSidebarHidden((current) => {
      const next = !current;
      try {
        window.localStorage.setItem("corral-sidebar", next ? "hidden" : "shown");
      } catch {
        /* Preference storage is optional. */
      }
      return next;
    });
  }, []);
  useEffect(() => {
    const media = window.matchMedia("(max-width: 767px)");
    const sync = () => setIsMobile(media.matches);
    media.addEventListener("change", sync);
    return () => media.removeEventListener("change", sync);
  }, []);
  useEffect(() => {
    const onAgentState = (event: Event) =>
      setAgentOpen(Boolean((event as CustomEvent<{ open?: boolean }>).detail?.open));
    window.addEventListener(AGENT_STATE_EVENT, onAgentState);
    return () => window.removeEventListener(AGENT_STATE_EVENT, onAgentState);
  }, []);
  const { theme, preference: themePreference, setPreference: setThemePreference, toggle: toggleTheme } = useTheme();
  const online = useOnline();
  const activeWorkspace = auth?.workspaces.find(
    (item) => item.id === auth.current_workspace_id,
  );
  const workspacePermissions = useMemo(
    () => new Set(activeWorkspace?.effective_permissions ?? []),
    [activeWorkspace],
  );
  const {
    notifications: assignmentNotifications,
    markRead: markAssignmentRead,
  } = useAssignmentNotifications(
    auth?.user.id,
    auth?.current_workspace_id,
    workspacePermissions.has("leads.view") ||
      workspacePermissions.has("leads.edit"),
  );
  useEffect(() => {
    function openPhone(event: Event) {
      const detail = (event as CustomEvent<{ connectionId?: unknown }>).detail;
      if (
        typeof detail?.connectionId !== "string" ||
        !can(workspacePermissions, "dialer.call")
      ) {
        return;
      }
      const next = dialerConnections.find(
        (connection) => connection.id === detail.connectionId,
      );
      if (!next || !isDialerPhoneAvailable(next)) return;
      if (dialerCallActive && next.id !== preferredDialerRef.current) {
        notifyActivity(
          "Finish the active call before switching the embedded phone.",
          "info",
        );
        return;
      }
      if (next.id !== preferredDialerRef.current) {
        selectPreferredDialer(next.id);
      }
      setDialerPhoneOpen(true);
    }
    window.addEventListener(OPEN_DIALER_PHONE_EVENT, openPhone);
    return () => window.removeEventListener(OPEN_DIALER_PHONE_EVENT, openPhone);
  }, [
    dialerCallActive,
    dialerConnections,
    selectPreferredDialer,
    workspacePermissions,
  ]);
  // Deal desk: every lead with its deal state, shared by Today, Pipeline, Revenue and Performance.
  useLayoutEffect(() => {
    bindDealWorkspace(can(workspacePermissions, "leads.view") ? auth?.current_workspace_id ?? "" : "", can(workspacePermissions, "leads.edit"), can(workspacePermissions, "workspace.manage"));
  }, [auth?.current_workspace_id, workspacePermissions]);
  useEffect(() => {
    const timer = window.setInterval(() => { if (document.visibilityState === "visible") void pollDealRecords(); }, 30000);
    return () => window.clearInterval(timer);
  }, [auth?.current_workspace_id]);
  const workspaceLeads = useWorkspaceLeads(Boolean(auth) && can(workspacePermissions, "leads.view"), leadRevision);
  const dealViews = useDealViews(workspaceLeads.leads, data?.pipeline);
  const stageMap = useMemo(() => recordedStages(data?.pipeline), [data?.pipeline]);
  const [leadIntent, setLeadIntent] = useState<LeadIntent>(null);
  const [newLeadNonce, setNewLeadNonce] = useState(0);
  const [quotePicker, setQuotePicker] = useState(false);
  const [phonePanelOpen, setPhonePanelOpen] = useState(false);
  const embeddedPhoneReady = can(workspacePermissions, "dialer.call") && isDialerPhoneAvailable(selectedDialer);
  const directDialers = dialerConnections.filter((connection) => !isEmbeddedDialerConnection(connection));
  const phoneOpen = embeddedPhoneReady ? dialerPhoneOpen : phonePanelOpen;
  const setPhone = useCallback(
    (open?: boolean) => {
      if (embeddedPhoneReady) {
        setDialerPhoneOpen((current) => {
          const next = open ?? !current;
          return !next && dialerCallActive ? current : next;
        });
      } else {
        setPhonePanelOpen((current) => open ?? !current);
      }
    },
    [embeddedPhoneReady, dialerCallActive],
  );
  useEffect(() => {
    const onPhone = (event: Event) => {
      if (!can(workspacePermissions, "dialer.call")) return;
      setPhone((event as CustomEvent<{ open?: boolean }>).detail?.open);
    };
    window.addEventListener(PHONE_EVENT, onPhone);
    return () => window.removeEventListener(PHONE_EVENT, onPhone);
  }, [setPhone, workspacePermissions]);

  const refreshVersion = useRef(0);
  const refreshController = useRef<AbortController | null>(null);
  const leadRequestVersion = useRef(0);
  const leadController = useRef<AbortController | null>(null);
  const leadSignature = useRef<string | null>(null);
  const tourSeenKey = auth?.current_workspace_id
    ? `corral-product-tour-seen:v1:${auth.current_workspace_id}`
    : null;


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
    const media = window.matchMedia("(min-width: 768px)");
    const syncNavigation = () => {
      if (!media.matches) setNavOpen(false);
    };
    media.addEventListener("change", syncNavigation);
    return () => media.removeEventListener("change", syncNavigation);
  }, []);

  const refresh = useCallback(
    async (activeRange = range, keys?: AnalyticsKey[]) => {
      refreshController.current?.abort();
      const controller = new AbortController();
      refreshController.current = controller;
      const requestVersion = ++refreshVersion.current;
      setLoading(true);
      setError(null);
      let requestKeys: AnalyticsKey[] = [];
      try {
        const activePermissions = permissionsForWorkspace(
          auth,
          auth?.current_workspace_id,
        );
        const canViewAnalytics = can(activePermissions, "analytics.view");
        const canViewAgents = can(activePermissions, "agents.view");
        const canViewReview = can(activePermissions, "manual_review.view");
        const canViewLeads = can(activePermissions, "leads.view");
        const permitted: Record<AnalyticsKey, boolean> = {
          dashboard: canViewAnalytics, channels: canViewAnalytics, pipeline: canViewAnalytics,
          revenue: canViewAnalytics, reps: canViewAnalytics, series: canViewAnalytics,
          extended: canViewAnalytics, advanced: canViewAnalytics,
          agents: canViewAgents, review: canViewReview,
        };
        const fetchers: Record<AnalyticsKey, () => Promise<unknown>> = {
          dashboard: () => getDashboard(activeRange, controller.signal),
          channels: () => getChannelAnalytics(activeRange, controller.signal),
          pipeline: () => getPipelineAnalytics(activeRange, controller.signal),
          revenue: () => getRevenueAnalytics(activeRange, controller.signal),
          reps: () => getRepAnalytics(activeRange, controller.signal),
          series: () => getTimeSeriesAnalytics(activeRange, controller.signal),
          agents: () => getAgentActivity(activeRange, controller.signal),
          review: () => getManualReview(activeRange, controller.signal),
          extended: () => getExtendedAnalytics(activeRange, controller.signal),
          advanced: () => getAdvancedAnalytics(6, controller.signal),
        };
        const wanted = [...new Set(keys ?? analyticsKeysFor(viewRef.current))];
        const deferred = keys ? [] : (DEFERRED_ANALYTICS[viewRef.current] ?? []).filter((key) => !wanted.includes(key));
        requestKeys = wanted;
        wanted.forEach((key) => inflightKeys.current.add(key));
        const [values, leadRows] = await Promise.all([
          Promise.all(wanted.map((key) => (permitted[key] ? fetchers[key]() : Promise.resolve(null)))),
          canViewLeads ? listLeads({ limit: 50 }, controller.signal) : Promise.resolve(null),
        ]);
        if (requestVersion !== refreshVersion.current) return;
        const fetched = Object.fromEntries(wanted.map((key, index) => [key, values[index]])) as Partial<AnalyticsBundle>;
        wanted.forEach((key) => { loadedRange.current[key] = activeRange; inflightKeys.current.delete(key); });
        setData((previous) => ({ ...(previous ?? {}), ...fetched }));
        if (deferred.length) {
          void Promise.all(deferred.map((key) => (permitted[key] ? fetchers[key]() : Promise.resolve(null))))
            .then((more) => {
              if (requestVersion !== refreshVersion.current) return;
              deferred.forEach((key) => { loadedRange.current[key] = activeRange; });
              setData((previous) => ({ ...(previous ?? {}), ...Object.fromEntries(deferred.map((key, index) => [key, more[index]])) }));
            })
            .catch(() => { /* Secondary panels show their own unavailable state. */ });
        }
        const nextLeads = leadRows?.items ?? [];
        const newArrivalIds = leadRows
          ? await observeLeadSnapshot(nextLeads, {
              completeSnapshot: leadRows.total <= nextLeads.length,
            })
          : [];
        setLeadNotificationSnapshot({
          workspaceId: auth?.current_workspace_id ?? "",
          leads: nextLeads,
        });
        if (newArrivalIds.length) {
          const example = nextLeads.find((lead) => newArrivalIds.includes(lead.id));
          const message = newArrivalIds.length === 1
            ? `New lead arrived${example?.full_name ? `: ${example.full_name}` : ""}.`
            : `${newArrivalIds.length} new leads arrived.`;
          notifyActivity(message, "info");
        }
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
        requestKeys.forEach((key) => inflightKeys.current.delete(key));
        if (requestVersion === refreshVersion.current) setLoading(false);
      }
    },
    [range, auth?.current_workspace_id, auth?.workspaces, observeLeadSnapshot],
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
    loadedRange.current = {};
    void refresh(range);
    return () => refreshController.current?.abort();
  }, [auth?.current_workspace_id, range, refresh]);

  // Load only what the newly opened view needs; anything already loaded for
  // this range stays on screen while background refreshes keep it current.
  useEffect(() => {
    if (!auth) return;
    const missing = analyticsKeysFor(view).filter((key) => loadedRange.current[key] !== range && !inflightKeys.current.has(key));
    if (missing.length) void refresh(range, missing);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [view]);

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
    const timer = window.setInterval(() => void poll(), 10000);
    const onVisible = () => {
      if (document.visibilityState === "visible") {
        lastFullRefresh = 0;
        void poll();
      }
    };
    document.addEventListener("visibilitychange", onVisible);
    window.addEventListener("online", onVisible);
    window.addEventListener(PHONE_CALL_ENDED, onVisible);
    return () => {
      controller.abort();
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisible);
      window.removeEventListener("online", onVisible);
      window.removeEventListener(PHONE_CALL_ENDED, onVisible);
    };
  }, [auth?.current_workspace_id, auth?.workspaces, refresh]);

  useEffect(() => {
    if (auth) return;
    setOpenaiSetupCard(null);
    setAiSetupCards([]);
  }, [auth]);

  useEffect(() => {
    if (selectedLead) markLeadSeen(selectedLead.id, selectedLead.created_at);
  }, [selectedLead?.id, markLeadSeen]);

  useEffect(() => {
    if (!auth) return;
    const workspace = auth.workspaces.find(
      (item) => item.id === auth.current_workspace_id,
    );
    if (!workspace) return;
    const permissions = new Set(workspace.effective_permissions);
    if (hasViewPermission(view, permissions)) return;
    const fallback =
      FALLBACK_ORDER.find((item) => hasViewPermission(item, permissions)) ??
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
      aiSetupDismissed(auth.current_workspace_id)
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
      if (window.matchMedia("(max-width: 767px)").matches) setNavOpen(false);
    },
    [inspectLead, auth?.current_workspace_id, auth?.workspaces, view],
  );

  const handleInspectLead = useCallback(
    (id: string) =>
      openLeadSection(id, view === "leads" ? leadSection : "overview"),
    [openLeadSection, view, leadSection],
  );

  useEffect(() => {
    const onLeadAction = (event: Event) => {
      const detail = (event as CustomEvent<{ leadId?: string; action?: LeadAction }>).detail;
      if (!detail?.leadId || !detail.action || !(detail.action in LEAD_ACTION_SECTION)) return;
      openLeadSection(detail.leadId, LEAD_ACTION_SECTION[detail.action]);
      setLeadIntent({ action: detail.action, nonce: Date.now() });
      if (detail.action === "call") setPhonePanelOpen(false);
    };
    window.addEventListener(LEAD_ACTION_EVENT, onLeadAction);
    return () => window.removeEventListener(LEAD_ACTION_EVENT, onLeadAction);
  }, [openLeadSection]);

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
    if (RANGE_VIEWS.has(view)) url.searchParams.set("range", range);
    else url.searchParams.delete("range");
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
    if (RANGE_VIEWS.has(next)) url.searchParams.set("range", range);
    else url.searchParams.delete("range");
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
    if (window.matchMedia("(max-width: 767px)").matches) setNavOpen(false);
  }

  function navigationChildren(id: AppView) {
    return (navigationSections[id] ?? []).filter(
      (child) =>
        !child.permission || can(workspacePermissions, child.permission),
    );
  }

  function navigationItemActive(item: { id: View }) {
    if (view === item.id) return true;
    if (item.id === "settings" && ["icp", "policies", "compliance"].includes(view)) return true;
    return item.id === "agents" && navigationChildren("agents").some((child) => child.view === view) && !navigation.some((entry) => entry.id === view);
  }


  const visibleNavigation = navigation.filter((item) =>
    hasViewPermission(item.id, workspacePermissions),
  );
  const reviewTotal = data?.review?.total ?? 0;
  const navEntries: Array<NavEntry & { group: string }> = visibleNavigation.map((item) => ({
    id: item.id,
    label: item.label,
    icon: item.icon,
    group: item.group,
    active: navigationItemActive(item),
    count: item.id === "leads" ? unseenLeadIds.length : item.id === "review" ? reviewTotal : 0,
    countTone: item.id === "review" ? "attention" : item.id === "leads" ? "tint" : undefined,
    children: item.id === "integrations" ? [
      { id: "email", label: "Email", children: [{ id: "gmail", label: "Gmail" }, { id: "outlook", label: "Outlook" }] },
      { id: "crm", label: "CRM", children: [{ id: "salesforce", label: "Salesforce" }, { id: "hubspot", label: "HubSpot" }, { id: "gohighlevel", label: "GoHighLevel" }] },
      { id: "calling", label: "Calling", children: [{ id: "calling", label: "All dialers" }, { id: "cloudtalk", label: "CloudTalk" }, { id: "twilio", label: "Twilio" }, { id: "aircall", label: "Aircall" }, { id: "ringcentral", label: "RingCentral" }, { id: "vapi", label: "Vapi" }, { id: "retell", label: "Retell AI" }] },
      { id: "calendar", label: "Calendar", children: [{ id: "google_calendar", label: "Google Calendar" }, { id: "calendly", label: "Calendly" }] },
      { id: "social", label: "Social & sources", children: [{ id: "linkedin", label: "LinkedIn" }, { id: "instagram", label: "Instagram" }, { id: "messenger", label: "Messenger" }, { id: "whatsapp_business", label: "WhatsApp Business" }, { id: "clutch", label: "Clutch" }] },
      { id: "ai", label: "AI connections" }, { id: "slack", label: "Slack" },
    ] : ["analytics", "dialer", "settings", "dashboard"].includes(item.id)
      ? navigationChildren(item.id).filter((child) => !child.view).map((child) => ({ id: child.id, label: child.label }))
      : undefined,
    countLabel:
      item.id === "review"
        ? `${reviewTotal} lead${reviewTotal === 1 ? "" : "s"} waiting for review${unseenManualReviewLeadCount ? `, ${unseenManualReviewLeadCount} new` : ""}`
        : `${unseenLeadIds.length} new lead${unseenLeadIds.length === 1 ? "" : "s"}`,
  }));
  const navSections: NavSection[] = ["main", "work", "records", "insights", "workspace"]
    .map((group) => ({ id: group, label: NAV_GROUP_LABELS[group], items: navEntries.filter((entry) => entry.group === group) }))
    .filter((section) => section.items.length > 0);
  const tabBarItems = navEntries.filter((entry) => TAB_BAR_VIEWS.includes(entry.id as View)).slice(0, 4);
  const moreSections: NavSection[] = navSections
    .map((section) => ({ ...section, items: section.items.filter((item) => !tabBarItems.some((tab) => tab.id === item.id)) }))
    .filter((section) => section.items.length > 0);
  // Pages with their own sections show them as tabs under the title.
  const SECTION_AREAS = new Set<AppView>(["analytics"]);
  const sectionTabs = SECTION_AREAS.has(view) ? navigationChildren(view).filter((child) => !child.view) : [];
  const activeSectionTab =
    view === "settings" ? (pageSection === "user" || pageSection === "users" ? "user" : "company") : pageSection || sectionTabs[0]?.id;
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
  // Pages with their own header carry the range there; the rest keep it in the toolbar.
  const wantsRange = ["agents", "analytics", "review"].includes(view);
  const rangeControl = (
    <Segmented
      label="Time range"
      size="sm"
      value={range}
      onChange={setRange}
      options={[
        { value: "weekly", label: "Week" },
        { value: "monthly", label: "Month" },
        { value: "annual", label: "Year" },
      ]}
    />
  );
  const account = {
    name: auth.user.display_name || "",
    email: auth.user.email || "",
    online,
    theme,
    themePreference,
    onSetThemePreference: setThemePreference,
    onGuide: () => setTourOpen(true),
    onSignOut: () => {
      refreshController.current?.abort();
      leadController.current?.abort();
      void signOut().finally(() => {
        setAuth(null);
        setData(null);
        setSelectedLead(null);
      });
    },
  };
  const toolbarTitle = view === "leads" && selectedLead ? selectedLead.company_name || selectedLead.full_name : viewLabel(view);
  const inspect = (id: string) => {
    handleInspectLead(id);
    setView("leads");
  };
  return (
    <div
      className="app-shell v-app"
      data-sidebar={sidebarHidden ? "hidden" : "shown"}
      data-inspector={agentOpen ? "open" : undefined}
      style={{ "--workspace-accent": branding.accent, "--workspace-accent-text": accentText } as CSSProperties}
    >
      <a className="v-skip" href="#main">Skip to content</a>
      <NativeSelectEnhancer />
      <Sidebar
        sections={navSections}
        workspace={<WorkspaceSwitcher session={auth} onSelect={switchWorkspace} />}
        pins={can(workspacePermissions, "leads.view") ? pinnedSections.pins : []}
        pinNotice={pinnedSections.notice}
        activePin={{ leadId: view === "leads" ? selectedLead?.id : undefined, section: leadSection }}
        account={account}
        onNavigate={(id, section) => navigate(id as AppView, section ?? "")}
        onOpenPin={(pin) => openLeadSection(pin.leadId, pin.section)}
        onRemovePin={pinnedSections.remove}
      />
      <div className="v-window">
        <DealSyncStatus />
        <Toolbar
          title={toolbarTitle}
          leading={
            isMobile ? (
              <img className="v-toolbar-mark" src={branding.logo_url || "/asset/brand/corral-mark.png"} alt={branding.name || "Corral"} width={26} height={26} />
            ) : (
              <IconButton
                icon="sidebar"
                label={sidebarHidden ? "Show sidebar" : "Hide sidebar"}
                aria-pressed={!sidebarHidden}
                onClick={toggleSidebar}
              />
            )
          }
          trailing={
            <>
              {!isMobile && <SearchField />}
              {wantsRange && !isMobile && rangeControl}
              {isMobile && (
                <IconButton icon="search" label="Search" onClick={() => window.dispatchEvent(new CustomEvent(OPEN_SEARCH_EVENT))} />
              )}
              {!isMobile && (
                <QuickAccess
                  items={[
                    ...(can(workspacePermissions, "dialer.call")
                      ? [{
                          id: "phone",
                          label: "Phone",
                          icon: "phone" as IconName,
                          pressed: phoneOpen,
                          live: dialerCallActive,
                          liveLabel: "On call",
                          ariaLabel: dialerCallActive ? "Phone, call in progress" : phoneOpen ? "Close phone" : "Open phone",
                          onClick: () => setPhone(),
                        }]
                      : []),
                    ...(can(workspacePermissions, "manual_review.view")
                      ? [{ id: "review", label: "Review", icon: "review" as IconName, count: reviewTotal, active: view === "review", ariaLabel: `Review, ${reviewTotal} waiting`, onClick: () => navigate("review") }]
                      : []),
                    ...(can(workspacePermissions, "analytics.view")
                      ? [{ id: "analytics", label: "Analytics", icon: "analytics" as IconName, active: view === "analytics", onClick: () => navigate("analytics") }]
                      : []),
                  ]}
                />
              )}
              {(can(workspacePermissions, "leads.edit") || can(workspacePermissions, "dialer.call")) && (
                <NewMenu
                  actions={[
                    ...(can(workspacePermissions, "leads.edit")
                      ? [
                          { id: "lead", label: "Lead", icon: "leads" as IconName, onSelect: () => { navigate("leads"); setNewLeadNonce(Date.now()); } },
                          ...(can(workspacePermissions, "leads.view") ? [{ id: "quote", label: "Quote", icon: "quote" as IconName, onSelect: () => setQuotePicker(true) }] : []),
                        ]
                      : []),
                    ...(can(workspacePermissions, "dialer.call") ? [{ id: "call", label: "Call", icon: "phone" as IconName, onSelect: () => setPhone(true) }] : []),
                    ...(can(workspacePermissions, "integrations.view") && can(workspacePermissions, "leads.edit") ? [{ id: "email", label: "Email", icon: "mail" as IconName, onSelect: () => navigate("email") }] : []),
                  ]}
                />
              )}
              {(can(workspacePermissions, "leads.view") || can(workspacePermissions, "leads.edit")) && (
                <LeadNotificationCenter
                  unseenLeadIds={unseenLeadIds}
                  leads={notificationLeads}
                  assignmentNotifications={assignmentNotifications}
                  onOpenLead={(leadId, section) => {
                    if (!can(workspacePermissions, "leads.view")) return;
                    if (section) openLeadSection(leadId, section);
                    else handleInspectLead(leadId);
                  }}
                  onMarkAssignmentRead={markAssignmentRead}
                />
              )}
              {can(workspacePermissions, "agents.execute") && (
                <button
                  type="button"
                  className="v-ask"
                  data-ask-corral
                  aria-controls="corral-agent-window"
                  aria-expanded={agentOpen}
                  onClick={() => window.dispatchEvent(new CustomEvent(TOGGLE_AGENT_EVENT))}
                  title="Ask Corral"
                >
                  <img src="/asset/brand/corral-mark.png" alt="" />
                  <span className="v-ask-label">Ask Corral</span>
                </button>
              )}
            </>
          }
        />
        <main className="v-content" id="main" tabIndex={-1}>
          <CommandPalette
            key={auth.current_workspace_id}
            pages={visibleNavigation}
            canSearch={can(workspacePermissions, "leads.view")}
            onNavigate={navigate}
            onInspect={inspect}
            hideTrigger
          />
          {isMobile && wantsRange && <div className="v-mobile-range">{rangeControl}</div>}
          {error && (
            <div className="v-alert-bar">
              <Banner
                tone="danger"
                title="Corral can’t reach the workspace right now"
                action={<Button size="sm" onClick={() => void refresh()}>Try Again</Button>}
              >
                {error}
              </Banner>
            </div>
          )}
          {!hasViewPermission(view, workspacePermissions) ||
          (!error && !VIEWS_WITHOUT_ANALYTICS.has(view) && !analyticsKeysFor(view).every((key) => analytics?.[key] !== undefined)) ? (
            <ContentLoadingState />
          ) : (
            <Suspense fallback={<ContentLoadingState />}>
              <div className="cx-view" key={auth.current_workspace_id + view}>
                {sectionTabs.length > 1 && (
                  <div className="v-page v-section-tabs-wrap">
                    <h1 className="v-section-title">{viewLabel(view)}</h1>
                    <EdgeFadeNav className="v-section-tabs" aria-label={`${viewLabel(view)} sections`}>
                      {sectionTabs.map((child) => (
                        <button
                          key={child.id}
                          type="button"
                          className="v-tab-pill"
                          aria-current={activeSectionTab === child.id ? "page" : undefined}
                          onClick={() => navigate(view, child.id)}
                        >
                          {child.label}
                        </button>
                      ))}
                    </EdgeFadeNav>
                  </div>
                )}
                {view === "dashboard" && (
                  <TodayScreen
                    data={data}
                    views={dealViews}
                    userName={auth.user.display_name || ""}
                    mode={pageSection === "dashboard" ? "dashboard" : "today"}
                    onModeChange={(mode) => navigate("dashboard", mode === "dashboard" ? "dashboard" : "")}
                    onInspect={(id, section) => openLeadSection(id, section ?? "overview")}
                    canCall={can(workspacePermissions, "dialer.call")}
                    canEmail={can(workspacePermissions, "integrations.view") && can(workspacePermissions, "leads.edit")}
                    rangeControl={isMobile ? null : rangeControl}
                    onNavigate={(next) => navigate(next as AppView)}
                    canViewLeads={can(workspacePermissions, "leads.view")}
                    dashboard={
                      <DashboardView key={auth?.user.id ?? "local"} data={data} onInspect={inspect} userId={auth?.user.id ?? "local"} />
                    }
                  />
                )}
                {view === "leads" && (
                  <div className="v-content-inner">
                    <LeadsScreen
                      selected={selectedLead}
                      activeSection={leadSection}
                      onSectionChange={changeLeadSection}
                      onNavigateToDialer={() => navigate("dialer")}
                      boardViews={dealViews}
                      onOpenPipeline={() => navigate("pipeline")}
                      isSectionPinned={pinnedSections.pins.some((pin) => pin.leadId === selectedLead?.id && pin.section === leadSection)}
                      onTogglePin={() => {
                        if (selectedLead) pinnedSections.toggle({ leadId: selectedLead.id, company: selectedLead.company_name, section: leadSection });
                      }}
                      revision={leadRevision}
                      newLeadIds={unseenLeadIdSet}
                      onInspect={handleInspectLead}
                      onClose={closeLead}
                      canEdit={can(workspacePermissions, "leads.edit")}
                      canManageRouting={can(workspacePermissions, "routing.manage")}
                      canManageManualReview={can(workspacePermissions, "manual_review.manage")}
                      canExport={can(workspacePermissions, "leads.export")}
                      canCrmSync={can(workspacePermissions, "crm.sync")}
                      canViewEmail={can(workspacePermissions, "integrations.view")}
                      canUseAgent={can(workspacePermissions, "agents.execute")}
                      dialerPhoneConnectionId={
                        can(workspacePermissions, "dialer.call") && isDialerPhoneAvailable(selectedDialer) ? selectedDialer?.id ?? null : null
                      }
                      canEnrollAutomations={can(workspacePermissions, "agents.view") && can(workspacePermissions, "leads.edit")}
                      onOpenAutomations={() => navigate("automations")}
                      canCall={can(workspacePermissions, "dialer.call")}
                      recordedStages={stageMap}
                      intent={leadIntent}
                      embeddedPhone={embeddedPhoneReady}
                      newLeadNonce={newLeadNonce}
                      onUpdated={(lead) => {
                        setSelectedLead((current) => (current?.id === lead.id ? lead : current));
                        setLeads((current) => current.map((item) => (item.id === lead.id ? lead : item)));
                        workspaceLeads.replace(lead);
                      }}
                      onDeleted={(leadId) => {
                        closeLead();
                        markLeadSeen(leadId);
                        pinnedSections.removeLead(leadId);
                        setLeads((current) => current.filter((item) => item.id !== leadId));
                        void refresh();
                      }}
                      onCreated={(lead) => {
                        setLeads((current) => [lead, ...current.filter((item) => item.id !== lead.id)]);
                        setSelectedLead(lead);
                        setLeadSection("overview");
                        window.history.pushState({ corralView: "leads" }, "", leadSectionHref(lead.id, "overview"));
                        void refresh();
                      }}
                    />
                  </div>
                )}
                {view === "pipeline" && (
                  <PipelineBoard
                    views={dealViews}
                    loading={workspaceLeads.loading}
                    error={workspaceLeads.error}
                    truncated={workspaceLeads.truncated}
                    total={workspaceLeads.total}
                    canEdit={can(workspacePermissions, "leads.edit")}
                    canCall={can(workspacePermissions, "dialer.call")}
                    canEmail={can(workspacePermissions, "integrations.view") && can(workspacePermissions, "leads.edit")}
                    onInspect={(id) => openLeadSection(id, "pipeline")}
                    onMoved={(lead) => {
                      if (lead) workspaceLeads.replace(lead);
                      else workspaceLeads.reload();
                      void refresh();
                    }}
                  />
                )}
                {view === "revenue" && (
                  <RevenueScreen
                    data={data}
                    views={dealViews}
                    range={range}
                    rangeControl={rangeControl}
                    canEdit={can(workspacePermissions, "workspace.manage")}
                    onInspect={(id, section) => openLeadSection(id, section ?? "overview")}
                  />
                )}
                {view === "performance" && (
                  <PerformanceScreen data={data} views={dealViews} range={range} rangeControl={rangeControl} />
                )}
                {view === "review" && (
                  <ReviewScreen data={data?.review ?? null} newLeadIds={unseenLeadIdSet} onInspect={(id) => openLeadSection(id, "ai")} canManageReview={can(workspacePermissions, "leads.view") && can(workspacePermissions, "manual_review.manage")} onReviewCompleted={() => { void refresh(); }} />
                )}
                {view === "agents" && (
                  <div className="v-page" data-width="wide">
                    <AgentsView data={data?.agents ?? null} pipeline={data?.pipeline ?? null} onInspect={(id) => openLeadSection(id, "history")} />
                  </div>
                )}
                {view === "analytics" && (
                  <div className="v-page" data-width="wide">
                    <AnalyticsView key={auth?.user.id ?? "local"} data={data} userId={auth?.user.id ?? "local"} selectedSection={pageSection} />
                    <AdvancedAnalyticsPanel />
                  </div>
                )}
                {(view === "contacts" || view === "accounts") && (
                  <div className="v-page" data-width="wide">
                    <RecordsView
                      mode={view === "contacts" ? "contacts" : "companies"}
                      onInspect={inspect}
                      canEdit={can(workspacePermissions, "leads.edit")}
                      canResearch={can(workspacePermissions, "agents.execute")}
                    />
                  </div>
                )}
                {view === "policies" && (
                  <div className="v-page"><PoliciesView canManage={can(workspacePermissions, "workspace.manage")} onBack={() => setView("settings")} /></div>
                )}
                {view === "compliance" && <div className="v-page"><ComplianceView onBack={() => setView("settings")} /></div>}
                {view === "intelligence" && (
                  <div className="v-page" data-width="wide">
                    <IntelligenceView
                      onInspect={inspect}
                      onIcp={() => navigate("icp")}
                      canExecute={can(workspacePermissions, "agents.execute")}
                      canReview={can(workspacePermissions, "manual_review.manage")}
                      canLearn={can(workspacePermissions, "icp.manage")}
                    />
                  </div>
                )}
                {view === "automations" && (
                  <div className="v-page" data-width="wide">
                    <AutomationsView
                      canManage={can(workspacePermissions, "workspace.manage")}
                      canEdit={can(workspacePermissions, "leads.edit")}
                      onInspect={inspect}
                      leadName={(id) => workspaceLeads.leads.find((lead) => lead.id === id)?.full_name}
                    />
                  </div>
                )}
                {view === "integrations" && (
                  <div className="v-page" data-width="wide">
                    <IntegrationsView key={auth.current_workspace_id} permissions={workspacePermissions} selectedSection={pageSection} />
                  </div>
                )}
                {view === "email" && (
                  <div className="v-page" data-width="wide">
                    <EmailWorkspace canSend={can(workspacePermissions, "leads.edit")} onOpenIntegrations={() => navigate("integrations", "gmail")} />
                  </div>
                )}
                {view === "calendar" && (
                  <CalendarWorkspace
                    leads={workspaceLeads.leads.length ? workspaceLeads.leads : leads}
                    canEdit={can(workspacePermissions, "leads.edit")}
                    onOpenLead={(id) => openLeadSection(id, "overview")}
                    onOpenIntegrations={() => navigate("integrations", "google_calendar")}
                  />
                )}
                {view === "dialer" && (
                  <div className="v-page" data-width="wide">
                    <DialerView
                      selectedSection={pageSection}
                      onOpenIntegrations={() => navigate("integrations", "calling")}
                      onOpenLead={handleInspectLead}
                      canViewLeads={can(workspacePermissions, "leads.view")}
                      canEditLeads={can(workspacePermissions, "leads.view") && can(workspacePermissions, "leads.edit")}
                      canCall={can(workspacePermissions, "dialer.call")}
                      canCallLeads={can(workspacePermissions, "dialer.call") && can(workspacePermissions, "leads.view") && can(workspacePermissions, "leads.edit")}
                      canViewIntegrations={can(workspacePermissions, "integrations.view")}
                      preferredDialerId={preferredDialerId}
                      onPreferredDialerChange={selectPreferredDialer}
                      dialerCallActive={dialerCallActive}
                      ringCentralEmbedReady={Boolean(ringCentralEmbedClientId)}
                    />
                  </div>
                )}
                {view === "settings" && (
                  <div className="v-page">
                    <SettingsView
                      metadata={metadata}
                      selectedSection={pageSection}
                      onIntegrations={() => navigate("integrations")}
                      onTeam={() => navigate("team")}
                      onIcp={() => navigate("icp")}
                      onPolicies={() => navigate("policies")}
                      onCompliance={() => navigate("compliance")}
                      onOpenSection={(section) => navigate("settings", section)}
                      canViewTeam={can(workspacePermissions, "members.view")}
                      canManageWorkspace={can(workspacePermissions, "workspace.manage")}
                      canViewIcp={can(workspacePermissions, "icp.view")}
                      canViewPolicies={can(workspacePermissions, "routing.view")}
                      canViewCompliance={can(workspacePermissions, "workspace.manage")}
                      canViewRouting={can(workspacePermissions, "routing.view")}
                      canManageRouting={can(workspacePermissions, "routing.manage")}
                      canViewPublicIntake={can(workspacePermissions, "integrations.manage")}
                      canRotatePublicIntake={can(workspacePermissions, "integrations.manage")}
                      canManageEmail={can(workspacePermissions, "integrations.manage")}
                    />
                  </div>
                )}
                {view === "team" && (
                  <div className="v-page">
                    <TeamView
                      key={auth.current_workspace_id}
                      auth={auth}
                      workspaceName={auth.workspaces.find((workspace) => workspace.id === auth.current_workspace_id)?.name || metadata?.workspace_name || "Current workspace"}
                      onBack={() => setView("settings")}
                    />
                  </div>
                )}
                {view === "icp" && (
                  <div className="v-page">
                    <IcpView key={auth.current_workspace_id} canManage={can(workspacePermissions, "icp.manage")} onBack={() => setView("settings")} />
                  </div>
                )}
              </div>
            </Suspense>
          )}
        </main>
        <footer className="v-footer">
          <span>{branding.name || "Corral"} by Ingenuity Systems</span>
          <nav aria-label="Legal information">
            <a href="/terms.html">Terms</a>
            <a href="/privacy.html">Privacy</a>
            <a href={`mailto:${SUPPORT_EMAIL}`}>Contact</a>
          </nav>
        </footer>
      </div>
      {isMobile && (
        <TabBar
          primary={tabBarItems}
          rest={moreSections}
          workspace={<WorkspaceSwitcher session={auth} onSelect={switchWorkspace} />}
          account={account}
          onNavigate={(id) => navigate(id as AppView)}
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
            dismissAiSetup(auth.current_workspace_id);
            setOpenaiSetupCard(null);
          }}
          onSettings={() => {
            dismissAiSetup(auth.current_workspace_id);
            setOpenaiSetupCard(null);
            setView("integrations");
          }}
        />
      )}
      {tourOpen && (
        <Suspense fallback={null}>
          <ProductTour open currentView={view} onNavigate={navigate} onDismiss={closeTour} onComplete={closeTour} />
        </Suspense>
      )}
      {can(workspacePermissions, "agents.execute") && (
        <CorralAgentDock
          key={auth.current_workspace_id}
          selectedLead={selectedLead}
          permissions={workspacePermissions}
          onNavigate={(area) => navigate(area)}
          onNavigateToDialer={() => navigate("dialer")}
          canViewLeads={can(workspacePermissions, "leads.view")}
          canEdit={can(workspacePermissions, "leads.edit")}
          canCrmSync={can(workspacePermissions, "crm.sync")}
          canViewEmail={can(workspacePermissions, "integrations.view")}
          dialerPhoneConnectionId={
            can(workspacePermissions, "dialer.call") && isDialerPhoneAvailable(selectedDialer) ? selectedDialer?.id ?? null : null
          }
          onInspectLead={handleInspectLead}
          onUpdated={(lead) => {
            setSelectedLead((current) => (current?.id === lead.id ? lead : current));
            void refresh();
          }}
          onDeleted={(leadId) => {
            if (selectedLead?.id === leadId) closeLead();
            void refresh();
          }}
          onOpenLeadSection={openLeadSection}
          onRefresh={refresh}
        />
      )}
      {can(workspacePermissions, "dialer.call") && !embeddedPhoneReady && (
        <PhonePanel
          open={phonePanelOpen}
          onClose={() => setPhonePanelOpen(false)}
          connections={directDialers}
          leads={workspaceLeads.leads}
          canCall={can(workspacePermissions, "dialer.call")}
          canViewLeads={can(workspacePermissions, "leads.view")}
          canEditLeads={can(workspacePermissions, "leads.edit")}
          canSetUp={can(workspacePermissions, "integrations.view")}
          preferredConnectionId={preferredDialerId}
          onPreferredConnectionChange={selectPreferredDialer}
          onCallLead={(leadId) => {
            setPhonePanelOpen(false);
            openLeadSection(leadId, "calls");
            setLeadIntent({ action: "call", nonce: Date.now() });
          }}
          onSetUp={() => {
            setPhonePanelOpen(false);
            navigate("integrations", "calling");
          }}
        />
      )}
      {quotePicker && (
        <LeadPickerSheet
          title="New quote"
          subtitle="Choose the lead you’re quoting."
          leads={workspaceLeads.leads}
          onClose={() => setQuotePicker(false)}
          onPick={(leadId) => {
            setQuotePicker(false);
            openLeadSection(leadId, "deal");
            setLeadIntent({ action: "quote", nonce: Date.now() });
          }}
        />
      )}
      {can(workspacePermissions, "dialer.call") && (
        <DialerPhoneDock
          connection={selectedDialer}
          open={dialerPhoneOpen}
          callActive={dialerCallActive}
          notificationPreferenceKey={dialerPreferenceKey ? `${dialerPreferenceKey}:cloudtalk-alerts:v1` : null}
          onOpen={() => setDialerPhoneOpen(true)}
          onToggle={() => {
            if (dialerCallActive && dialerPhoneOpen) return;
            setDialerPhoneOpen((current) => !current);
          }}
          onClose={() => {
            if (!dialerCallActive) setDialerPhoneOpen(false);
          }}
          onCallActiveChange={setDialerCallActive}
        />
      )}
    </div>
  );
}

function LeadPickerSheet({
  title,
  subtitle,
  leads,
  onClose,
  onPick,
}: {
  title: string;
  subtitle: string;
  leads: Lead[];
  onClose: () => void;
  onPick: (leadId: string) => void;
}) {
  const [query, setQuery] = useState("");
  const needle = query.trim().toLowerCase();
  const matches = leads
    .filter((lead) => !needle || `${lead.company_name} ${lead.full_name} ${lead.email}`.toLowerCase().includes(needle))
    .slice(0, 40);
  return (
    <Sheet title={title} subtitle={subtitle} onClose={onClose} width={480}>
      <div className="lp">
        <label className="p-search">
          <Icon name="search" size={15} />
          <span className="v-sr">Search leads</span>
          <input type="search" autoFocus placeholder="Company or contact" value={query} onChange={(event) => setQuery(event.target.value)} />
        </label>
        <ul className="p-leads lp-list">
          {matches.map((lead) => (
            <li key={lead.id}>
              <button type="button" className="lp-item" onClick={() => onPick(lead.id)}>
                <Monogram text={lead.company_name || lead.full_name} size={32} />
                <span className="p-lead-text"><b>{lead.company_name || lead.full_name}</b><small>{lead.full_name}{lead.product_or_service ? ` · ${lead.product_or_service}` : ""}</small></span>
                <Icon name="chevronRight" size={14} />
              </button>
            </li>
          ))}
          {!matches.length && <li className="p-none">No leads match.</li>}
        </ul>
      </div>
    </Sheet>
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

// Views whose figures depend on the selected time range.
const RANGE_VIEWS: ReadonlySet<string> = new Set(["dashboard", "revenue", "performance", "review", "agents", "analytics"]);

const AI_SETUP_DISMISSED = "corral:ai-setup-dismissed:";
function aiSetupDismissed(workspaceId: string): boolean {
  try { return window.localStorage.getItem(AI_SETUP_DISMISSED + workspaceId) === "1"; } catch { return false; }
}
function dismissAiSetup(workspaceId: string) {
  try { window.localStorage.setItem(AI_SETUP_DISMISSED + workspaceId, "1"); } catch { /* Session-only dismissal. */ }
}

type AnalyticsKey = keyof AnalyticsBundle;

const EMPTY_ANALYTICS: AnalyticsBundle = {
  dashboard: null, channels: null, pipeline: null, revenue: null, reps: null,
  series: null, agents: null, review: null, extended: null, advanced: null,
};

// Every view keeps the review count current for the sidebar badge.
const ALWAYS_LOADED: AnalyticsKey[] = ["review"];
const VIEW_ANALYTICS: Partial<Record<string, AnalyticsKey[]>> = {
  dashboard: ["dashboard", "pipeline", "review"],
  pipeline: ["pipeline"],
  revenue: ["revenue", "reps", "series", "pipeline"],
  performance: ["revenue", "reps", "series", "pipeline"],
  review: ["review"],
  agents: ["agents", "pipeline"],
  analytics: ["dashboard", "channels", "pipeline", "revenue", "reps", "series", "extended", "advanced"],
  settings: ["dashboard"],
  team: ["dashboard"],
};

// Loaded after the first paint, for panels further down the page.
const DEFERRED_ANALYTICS: Partial<Record<string, AnalyticsKey[]>> = {
  dashboard: ["channels", "extended", "revenue", "series", "advanced", "agents", "reps"],
};

function analyticsKeysFor(view: string): AnalyticsKey[] {
  return [...new Set([...ALWAYS_LOADED, ...(VIEW_ANALYTICS[view] ?? [])])];
}

const VIEWS_WITHOUT_ANALYTICS: ReadonlySet<string> = new Set([
  "settings",
  "team",
  "icp",
  "email",
  "calendar",
  "dialer",
  "contacts",
  "accounts",
  "automations",
  "integrations",
  "policies",
  "compliance",
  "intelligence",
]);

function LoadingState() {
  return (
    <div className="loading-state" role="status" aria-live="polite">
      <div className="loading-core">
        <i />
        <i />
        <i />
      </div>
      <b>Signing you in</b>
      <span>Checking your session…</span>
    </div>
  );
}

export default App;
