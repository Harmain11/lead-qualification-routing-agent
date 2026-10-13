import { useEffect,useRef,useState } from "react";
import type { SlackChannel } from "../api";
import { IntegrationAccountChoice } from "../components/IntegrationAccountChoice";
import { SlackChannelDestination } from "../components/SlackChannelDestination";
import { createSlackRule } from "../slackRouting";

import {
addIntegrationCredential,
apiUrl,
beginIntegration,
configureInboundSync,
disconnectIntegration,
getInboundSyncState,
getSlackNotificationConfig,
listIntegrations,
listSlackChannelMembers,
listSlackChannels,
reconnectIntegration,
runInboundSync,
selectSlackChannel,
sendSlackTestNotification,
testIntegration,
updateOpenAIModel,
updateSlackNotificationConfig,
} from "../api";
import { OpenAIUsagePanel } from "../components/OpenAIUsagePanel";
import { SalesforceSetupGuide } from "../components/SalesforceSetupGuide";
import { can } from "../permissions";
import type { CrmInboundSyncState,IntegrationCard,ProviderConnection,SlackNotificationConfig,SlackNotificationRule } from "../types";

const connectionStages = ["Disconnected", "Authorizing", "Credentials stored", "Verified"];
type ConnectionOperation = "connect" | "reconnect" | "disconnect";
const operationProgress: Record<ConnectionOperation, string> = {
  connect: "Connecting…", reconnect: "Reconnecting…", disconnect: "Disconnecting…",
};

const integrationCategoryOptions = [
  ["all", "All integrations"],
  ["email", "Email"],
  ["crm", "CRM"],
  ["ai", "AI"],
  ["calling", "Calling"],
  ["calendar", "Calendar"],
  ["notifications", "Notifications"],
  ["other", "Other"],
] as const;
type IntegrationCategory = (typeof integrationCategoryOptions)[number][0];

function integrationCategory(card: IntegrationCard): IntegrationCategory {
  const category = card.category.toLowerCase();
  if (category.includes("email")) return "email";
  if (category.includes("crm")) return "crm";
  if (category.includes("ai")) return "ai";
  if (category.includes("dialer") || category.includes("voice")) return "calling";
  if (category.includes("calendar")) return "calendar";
  if (category.includes("notification")) return "notifications";
  return "other";
}

function connectionStage(status?: string) {
  if (status === "verified") return 3;
  if (["configured", "connected", "validating", "needs_attention", "connection_failed", "authorization_expired", "reconnect_required", "expired", "needs_reauthorization", "error"].includes(status ?? "")) return 2;
  if (["connecting", "awaiting_authorization"].includes(status ?? "")) return 1;
  return 0;
}

function connectionStatusLabel(status?: string) {
  if (!status || ["disconnected", "not_connected"].includes(status)) return "Not connected";
  if (["verified", "connected", "configured", "validating"].includes(status)) return "Connected";
  return "Needs authorization";
}

function oauthConnectFailureMessage(provider: string, message: string) {
  const settings: Record<string, { name: string; keys: [string, string] }> = {
    slack: { name: "Slack", keys: ["SLACK_CLIENT_ID", "SLACK_CLIENT_SECRET"] },
    salesforce: { name: "Salesforce", keys: ["SALESFORCE_CLIENT_ID", "SALESFORCE_CLIENT_SECRET"] },
    hubspot: { name: "HubSpot", keys: ["HUBSPOT_CLIENT_ID", "HUBSPOT_CLIENT_SECRET"] },
    google_calendar: { name: "Google Calendar", keys: ["GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET"] },
    calendly: { name: "Calendly", keys: ["CALENDLY_CLIENT_ID", "CALENDLY_CLIENT_SECRET"] },
  };
  const providerSettings = settings[provider];
  if (providerSettings && /OAuth application credentials are not configured/i.test(message)) {
    return `${providerSettings.name} can't connect yet. Add ${providerSettings.keys.join(" and ")} to Replit Secrets, then try again.`;
  }
  return message;
}

function qualificationBandLabel(value: string) {
  const labels: Record<string, string> = {
    hot: "Hot",
    warm: "Warm",
    needs_qualifying: "Needs qualification",
    nurture: "Cold / nurture",
    disqualify: "Disqualified",
    manual_review: "Manual review",
  };
  return labels[value.trim().toLowerCase()] ?? value.replaceAll("_", " ");
}

const slackBandOptions = [
  ["hot", "Hot"],
  ["warm", "Warm"],
  ["needs_qualifying", "Needs qualification"],
  ["nurture", "Cold / nurture"],
  ["disqualify", "Disqualified"],
  ["manual_review", "Manual review"],
] as const;

const slackSourceOptions = [
  ["website", "Website"],
  ["email", "Email"],
  ["salesforce", "Salesforce"],
  ["hubspot", "HubSpot"],
  ["offline", "Offline"],
  ["partner_referral", "Partner referral"],
  ["phone_inquiry", "Phone inquiry"],
  ["purchased_list", "Purchased list"],
] as const;

const slackQueueOptions = [
  ["senior_rep", "Senior sales"],
  ["standard_rep", "Standard sales"],
  ["sdr", "SDR qualification"],
  ["nurture", "Nurture"],
  ["archive", "Disqualified archive"],
  ["manual_review", "Manual review"],
] as const;

function SlackMultiSelect({
  label,
  values,
  options,
  onChange,
  hint,
}: {
  label: string;
  values: string[];
  options: ReadonlyArray<readonly [string, string]>;
  onChange: (values: string[]) => void;
  hint: string;
}) {
  const [open, setOpen] = useState(false);
  const selected = options.filter(([value]) => values.includes(value));
  const toggle = (value: string) => onChange(values.includes(value) ? values.filter((item) => item !== value) : [...values, value]);
  return <div className="slack-multi-select">
    <span className="slack-field-label">{label}</span>
    <button type="button" className={`slack-multi-trigger${open ? " open" : ""}`} onClick={() => setOpen((current) => !current)} aria-expanded={open}>
      <span>{selected.length ? selected.map(([, optionLabel]) => optionLabel).join(", ") : `Any ${label.toLowerCase()}`}</span><b>⌄</b>
    </button>
    {open && <div className="slack-multi-menu" role="listbox" aria-label={label}>
      <button type="button" className="slack-multi-all" onClick={() => onChange([])}><span className={!values.length ? "selected" : ""}>✓</span> Any / all</button>
      {options.map(([value, optionLabel]) => <label key={value} className="slack-multi-option"><input type="checkbox" checked={values.includes(value)} onChange={() => toggle(value)} /><span>{optionLabel}</span></label>)}
    </div>}
    <small>{hint}</small>
  </div>;
}

function SlackRoutingEditor({
  connections,
  config,
  channels,
  canManage,
  onSaved,
  onNotice,
  onLoadChannels,
  loading,
  loadError,
}: {
  connections: ProviderConnection[];
  config: SlackNotificationConfig | null;
  channels: Record<string, SlackChannel[]>;
  canManage: boolean;
  onSaved: (next: SlackNotificationConfig) => void;
  onNotice: (message: string) => void;
  onLoadChannels: (connectionId: string) => void;
  loading: boolean;
  loadError: string | null;
}) {
  const verified = connections.filter((item) => item.status === "verified" && item.is_active);
  const verifiedIds = verified.map((item) => item.id).join(",");
  const [defaultConnectionId, setDefaultConnectionId] = useState<string | null>(null);
  const [rules, setRules] = useState<SlackNotificationRule[]>([]);
  const [saving, setSaving] = useState(false);
  const [editing, setEditing] = useState(false);
  const [editorError, setEditorError] = useState<string | null>(null);
  const [newRuleId, setNewRuleId] = useState<string | null>(null);

  useEffect(() => {
    if (!newRuleId) return;
    document.querySelector<HTMLInputElement>(`[data-slack-rule-id="${newRuleId}"] input`)?.focus();
    setNewRuleId(null);
  }, [newRuleId, rules]);

  useEffect(() => {
    if (editing) return;
    setDefaultConnectionId(config?.default_connection_id && verified.some((item) => item.id === config.default_connection_id)
      ? config.default_connection_id : verified.length === 1 ? verified[0].id : null);
    setRules(config?.rules ?? []);
  }, [config?.default_connection_id, config?.rules, verifiedIds, editing]);

  if (loading || loadError) return <section className="integration-guide slack-routing-guide"><div className="slack-routing-heading"><div><span className="eyebrow">Admin notification routing</span><h3>{loading ? "Loading saved routing" : "Routing could not be loaded"}</h3><p>{loading ? "Reading the current Slack rules before enabling edits." : loadError}</p></div></div></section>;
  const save = async () => {
    if (rules.some((rule) => !rule.name.trim())) {
      setEditorError("Give each Slack routing rule a name before saving.");
      return;
    }
    if (rules.some((rule) => rule.destination_type === "direct_message" && !rule.channel_id?.trim())) {
      setEditorError("Enter a Slack member ID for every direct-message rule before saving.");
      return;
    }
    setEditorError(null);
    setSaving(true);
    try {
      const next = await updateSlackNotificationConfig({ default_connection_id: defaultConnectionId, rules });
      onSaved(next);
      setEditing(false);
      onNotice("Slack notification routing saved.");
    } catch (error) {
      const message = error instanceof Error ? error.message : "Slack notification routing could not be saved.";
      setEditorError(message);
      onNotice(message);
    } finally { setSaving(false); }
  };
  const addRule = () => {
    try {
      const rule = createSlackRule(connections, defaultConnectionId, rules.length);
      setEditorError(null);
      setEditing(true);
      setRules((current) => [...current, rule]);
      setNewRuleId(rule.id);
    } catch (error) {
      setEditorError(error instanceof Error ? error.message : "A Slack routing rule could not be created.");
    }
  };
  const updateRule = (id: string, patch: Partial<SlackNotificationRule>) => { setEditing(true); setRules((current) => current.map((rule) => rule.id === id ? { ...rule, ...patch } : rule)); };
  const channelOptions = (rule: SlackNotificationRule) => {
    const loadedChannels = (channels[rule.connection_id] ?? []).filter((channel) => channel.is_member);
    const savedChannel = rule.channel_id && !loadedChannels.some((channel) => channel.id === rule.channel_id)
      ? [{ id: rule.channel_id, name: `Saved channel (${rule.channel_id})` }]
      : [];
    return [...savedChannel, ...loadedChannels];
  };
  return <section className="integration-guide slack-routing-guide">
    <div className="slack-routing-heading">
      <div>
        <span className="eyebrow">Admin notification routing</span>
        <h3>Decide where each lead goes</h3>
        <p>{verified.length <= 1 ? "Use the default destination for every lead, or add conditions for specific lead types." : "Route different lead types to different Slack workspaces and channels."}</p>
      </div>
      <div className="slack-routing-health"><i /> <span>{verified.length} verified workspace{verified.length === 1 ? "" : "s"}</span></div>
    </div>
    {canManage ? <>
      {!verified.length && <div className="notice">Connect and verify Slack above to create routing rules. No verified Slack destination is available yet.</div>}
      {verified.length > 0 && !verified.some((item) => item.metadata.slack_channel_id) && <div className="notice">Choose a notification channel above before routing leads to Slack. Public channels can be joined automatically with the channel permissions enabled; private channels require an invitation in Slack.</div>}
      {editorError && <div className="notice" role="alert">{editorError}</div>}
      <div className="slack-default-destination">
        <div className="slack-destination-copy"><span className="slack-section-label">Fallback destination</span><strong>Used when no rule matches</strong><small>Every lead still has a safe destination.</small></div>
        <label><span>Slack workspace</span><select value={defaultConnectionId ?? ""} onChange={(event) => { setEditing(true); setDefaultConnectionId(event.target.value || null); }}><option value="">Use newest verified workspace</option>{verified.map((item) => <option value={item.id} key={item.id}>{item.account_name}{item.metadata.slack_channel_name ? ` · #${String(item.metadata.slack_channel_name)}` : " · choose a channel first"}</option>)}</select></label>
      </div>
      <div className="slack-routing-rules">
        <div className="slack-rules-toolbar"><div><span className="slack-section-label">Routing rules</span><strong>{rules.length ? `${rules.length} rule${rules.length === 1 ? "" : "s"} configured` : "No custom rules yet"}</strong></div><button type="button" className="button-quiet" onClick={addRule} disabled={saving}>+ Add rule</button></div>
        <p className="slack-rules-help">Open each dropdown to choose several values. Choices inside one field mean OR; classification, source and queue fields combine as AND. Leave a dropdown on “Any / all” to ignore that condition.</p>
        {rules.length === 0 && <div className="slack-empty-rules"><span>◎</span><div><strong>Default routing is active</strong><small>Add a rule when a lead classification needs a different Slack destination.</small></div></div>}
        {rules.map((rule, index) => <div className="slack-routing-rule" data-slack-rule-id={rule.id} key={rule.id}>
          <div className="slack-rule-heading"><div><span className="slack-rule-index">Rule {String(index + 1).padStart(2, "0")}</span><input aria-label="Slack routing rule name" value={rule.name} onChange={(event) => updateRule(rule.id, { name: event.target.value })} /></div><button className="button-quiet danger" onClick={() => { setEditing(true); setRules((current) => current.filter((item) => item.id !== rule.id)); }}>Remove</button></div>
          <div className="slack-condition-grid">
            <SlackMultiSelect label="Lead classifications" values={rule.qualification_bands} options={slackBandOptions} onChange={(values) => updateRule(rule.id, { qualification_bands: values })} hint="Choose one or more; empty means all." />
            <SlackMultiSelect label="Lead sources" values={rule.sources} options={slackSourceOptions} onChange={(values) => updateRule(rule.id, { sources: values })} hint="Choose one or more; empty means all." />
            <SlackMultiSelect label="Queues" values={rule.queues} options={slackQueueOptions} onChange={(values) => updateRule(rule.id, { queues: values })} hint="Choose one or more; empty means all." />
          </div>
          <div className="slack-rule-destination"><label><span>Send to workspace</span><select value={rule.connection_id} onChange={(event) => { const connectionId = event.target.value; const connection = verified.find((item) => item.id === connectionId); updateRule(rule.id, { connection_id: connectionId, channel_id: typeof connection?.metadata.slack_channel_id === "string" ? connection.metadata.slack_channel_id : null }); }}>{verified.map((item) => <option value={item.id} key={item.id}>{item.account_name}</option>)}</select></label><label><span>Destination type</span><select value={rule.destination_type ?? "channel"} onChange={(event) => { const destinationType = event.target.value as "channel" | "direct_message"; const connection = verified.find((item) => item.id === rule.connection_id); updateRule(rule.id, { destination_type: destinationType, channel_id: destinationType === "channel" && typeof connection?.metadata.slack_channel_id === "string" ? connection.metadata.slack_channel_id : null }); }}><option value="channel">Slack channel</option><option value="direct_message">Direct message</option></select></label>{(rule.destination_type ?? "channel") === "direct_message" ? <label><span>Slack member ID</span><input placeholder="U0123456789" value={rule.channel_id ?? ""} onChange={(event) => updateRule(rule.id, { channel_id: event.target.value.trim() || null })} /><small>Paste the member ID from Slack.</small></label> : <label><span>Send to channel</span><select value={rule.channel_id ?? ""} onChange={(event) => updateRule(rule.id, { channel_id: event.target.value || null })}><option value="">Use workspace default channel</option>{channelOptions(rule).map((channel) => <option value={channel.id} key={channel.id}>#{channel.name}</option>)}</select></label>}{(rule.destination_type ?? "channel") === "channel" && (channels[rule.connection_id] ?? []).length === 0 && <button className="button-quiet" type="button" onClick={() => onLoadChannels(rule.connection_id)}>Load channels</button>}<label className="slack-weight"><span>Weight</span><input aria-label={`Weight for ${rule.name}`} type="number" min="0.01" max="100" step="0.25" value={rule.weight ?? 1} onChange={(event) => updateRule(rule.id, { weight: Number(event.target.value) || 1 })} /></label></div>
          <div className="slack-rule-preview"><span>↳</span><small>Send {rule.sources.length ? `${rule.sources.map((source) => source.replaceAll("_", " ")).join(", ")} leads` : "all matching leads"} {rule.qualification_bands.length ? `that are ${rule.qualification_bands.map(qualificationBandLabel).join(", ")}` : ""} to {rule.destination_type === "direct_message" ? "the selected Slack member by direct message" : "the selected Slack channel"}.</small></div>
        </div>)}
      </div>
      <div className="slack-routing-actions"><small>Changes are kept in the editor until you save.</small><button onClick={() => void save()} disabled={saving || !verified.length}>{saving ? "Saving…" : "Save Slack routing"}</button></div>
    </> : <small>{config?.rules.length ? `${config.rules.length} admin rule${config.rules.length === 1 ? "" : "s"} configured.` : "The workspace admin has not added custom Slack rules."}</small>}
  </section>;
}

export function IntegrationsView({ permissions, selectedSection = "" }: { permissions: ReadonlySet<string>; selectedSection?: string }) {
  const [cards, setCards] = useState<IntegrationCard[]>([]);
  const [activeCategory, setActiveCategory] = useState<IntegrationCategory>("all");
  const [expandedProviders, setExpandedProviders] = useState<Set<string>>(() => new Set());
  useEffect(() => {
    if (!selectedSection) return;
    setExpandedProviders((previous) => new Set([...previous, selectedSection]));
  }, [selectedSection, cards.length]);
  const [salesforceSetupOpen, setSalesforceSetupOpen] = useState(false);
  const [accountNames, setAccountNames] = useState<Record<string, string>>({});
  const [credentials, setCredentials] = useState<Record<string, Record<string, string>>>({});
  const [modelSelections, setModelSelections] = useState<Record<string, string>>({});
  const [notice, setNotice] = useState<string | null>(null);
  const [actionErrors, setActionErrors] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [accountChoiceProvider, setAccountChoiceProvider] = useState<"calendly" | null>(null);
  const [busyAction, setBusyAction] = useState<ConnectionOperation | null>(null);
  const [operationFeedback, setOperationFeedback] = useState<Record<string, { state: "running" | "success" | "error"; message: string }>>({});
  const runningProvider = useRef<string | null>(null);
  const oauthIntent = useRef<ConnectionOperation>("connect");
  const slackVerificationAttempts = useRef(new Set<string>());
  const slackChannelLoads = useRef(new Set<string>());
  const [syncingProvider, setSyncingProvider] = useState<string | null>(null);
  const [syncFeedback, setSyncFeedback] = useState<Record<string, { kind: "progress" | "success" | "error"; message: string }>>({});
  const [syncStates, setSyncStates] = useState<Record<string, CrmInboundSyncState | null>>({});
  const [slackChannels, setSlackChannels] = useState<Record<string, SlackChannel[]>>({});
  const [slackMembers, setSlackMembers] = useState<Record<string, Array<{ id: string; name: string }>>>({});
  const [slackRouting, setSlackRouting] = useState<SlackNotificationConfig | null>(null);
  const [slackRoutingReady, setSlackRoutingReady] = useState(false);
  const [slackRoutingLoadError, setSlackRoutingLoadError] = useState<string | null>(null);
  const canView = can(permissions, "integrations.view");
  const canManage = can(permissions, "integrations.manage");
  const canConnect = can(permissions, "integrations.connect");
  const canDisconnect = can(permissions, "integrations.disconnect");
  const canSync = can(permissions, "crm.sync");
  const canInboundSync = canSync;
  function toggleProviderExpanded(provider: string) {
    const opening = !expandedProviders.has(provider);
    setExpandedProviders((current) => {
      const next = new Set(current);
      if (next.has(provider)) next.delete(provider);
      else next.add(provider);
      return next;
    });
    if (opening) {
      requestAnimationFrame(() => document.querySelector<HTMLElement>(`.integration-card.provider-${provider}`)?.scrollIntoView({ block: "start", behavior: "smooth" }));
    }
  }
  function showCredentialSetup(provider: string) {
    setExpanded(provider);
    requestAnimationFrame(() => document.getElementById(`integration-details-${provider}`)?.querySelector<HTMLInputElement>(".manual-credential input")?.focus());
  }

  function setExpanded(provider: string | null) {
    if (provider) {
      setExpandedProviders((current) => new Set(current).add(provider));
      return;
    }
    setExpandedProviders((current) => current.size === 1 ? new Set() : current);
  }

  const refresh = async () => {
    try {
      let nextCards = await listIntegrations();
      // Older production callbacks saved credentials without verifying Slack.
      // Finish that read-only check once; never send a notification here.
      const slack = nextCards.find((card) => card.provider === "slack")?.connection;
      if (canManage && slack?.is_active && slack.status === "connected"
        && slack.credential_state === "configured" && !slackVerificationAttempts.current.has(slack.id)) {
        slackVerificationAttempts.current.add(slack.id);
        try {
          const result = await testIntegration(slack.id);
          if (!result.verified) throw new Error(result.message);
          nextCards = await listIntegrations();
        } catch (error) {
          const message = error instanceof Error ? error.message : "Slack verification could not finish.";
          setActionErrors((current) => ({ ...current, slack: message }));
        }
      }
      setCards(nextCards);
      try {
        setSlackRouting(await getSlackNotificationConfig());
        setSlackRoutingLoadError(null);
      } catch (error) {
        setSlackRouting(null);
        setSlackRoutingLoadError(error instanceof Error ? error.message : "Saved Slack routing could not be loaded.");
      } finally {
        setSlackRoutingReady(true);
      }
      const inboundConnections = nextCards.filter((card) => (card.category === "CRM" || ["gmail", "outlook"].includes(card.provider)) && card.connection);
      const entries = await Promise.all(inboundConnections.map(async (card) => {
        try { return [card.connection!.id, await getInboundSyncState(card.connection!.id, card.provider)] as const; }
        catch { return [card.connection!.id, null] as const; }
      }));
      setSyncStates(Object.fromEntries(entries));
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Integrations could not be loaded");
    }
  };
  useEffect(() => { if (canView) void refresh(); }, [canView]);
  useEffect(() => {
    const connection = cards.find((card) => card.provider === "slack")?.connection;
    if (!canManage || !expandedProviders.has("slack") || connection?.status !== "verified"
      || !connection.is_active || slackChannelLoads.current.has(connection.id)) return;
    slackChannelLoads.current.add(connection.id);
    void loadSlackChannelsForRouting(connection.id);
  }, [cards, canManage, expandedProviders]);
  useEffect(() => {
    if (!canView) return;
    const timer = window.setInterval(() => {
      if (document.visibilityState === "visible") void refresh();
    }, 15000);
    return () => window.clearInterval(timer);
  }, [canView]);
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const provider = params.get("integration");
    const status = params.get("status");
    if (provider && status) {
      const name = provider === "salesforce" ? "Salesforce" : provider === "slack" ? "Slack" : provider;
      const message = params.get("message")?.trim().slice(0, 500);
      setNotice(status === "connected" ? `${name} connected successfully.` : message || `${name} authorization failed. Reconnect to try again.`);
      setOperationFeedback((current) => ({ ...current, [provider]: {
        state: status === "connected" ? "success" : "error",
        message: status === "connected" ? `${name} connected successfully.` : message || `${name} authorization failed.`,
      } }));
      if (provider === "salesforce" && status !== "connected" && canConnect) {
        setSalesforceSetupOpen(true);
        setActionErrors((current) => ({
          ...current,
          salesforce: message || "Salesforce authorization failed. Check the package installation and organization before trying again.",
        }));
      }
      window.history.replaceState({}, "", window.location.pathname);
      void refresh();
    }
  }, []);

  async function act(provider: string, operation: () => Promise<unknown>, success: string, kind?: ConnectionOperation) {
    if (runningProvider.current) return;
    runningProvider.current = provider;
    setBusy(provider); setBusyAction(kind ?? null); setNotice(null);
    if (kind) setOperationFeedback((current) => ({ ...current, [provider]: { state: "running", message: operationProgress[kind] } }));
    try {
      await operation();
      if (kind === "disconnect") setCards((current) => current.map((card) => {
        if (card.provider !== provider || !card.connection) return card;
        const id = card.connection.id;
        const disconnected: ProviderConnection = { ...card.connection, status: "disconnected", is_active: false, credential_state: "missing" };
        return { ...card, connection: disconnected, connections: card.connections.map((item) => item.id === id ? disconnected : item) };
      }));
      setNotice(success);
      if (kind) setOperationFeedback((current) => ({ ...current, [provider]: { state: "success", message: success } }));
      setCredentials((current) => ({ ...current, [provider]: {} }));
      await refresh();
    } catch (actionError) {
      const message = actionError instanceof Error ? actionError.message : "Integration action failed";
      setNotice(message);
      if (kind) setOperationFeedback((current) => ({ ...current, [provider]: { state: "error", message } }));
    } finally { setBusy(null); setBusyAction(null); runningProvider.current = null; }
  }

  async function syncInbound(connectionId: string, provider: string, mode: "initial" | "manual") {
    setBusy(provider);
    setSyncingProvider(provider);
    setNotice(null);
    setSyncFeedback((current) => ({
      ...current,
      [connectionId]: { kind: "progress", message: "Syncing provider records. This may take a moment." },
    }));
    try {
      const result = await runInboundSync(connectionId, mode, provider);
      const summary = `Sync ${result.status}: ${result.records_fetched} fetched, ${result.records_created} created, ${result.records_updated} updated, ${result.records_skipped} skipped, ${result.records_failed} failed.`;
      setSyncFeedback((current) => ({
        ...current,
        [connectionId]: {
          kind: result.status === "completed" ? "success" : "error",
          message: summary,
        },
      }));
      await refresh();
    } catch (error) {
      const message = error instanceof Error ? error.message : "Inbound sync failed.";
      setSyncFeedback((current) => ({
        ...current,
        [connectionId]: { kind: "error", message: `Sync failed: ${message}` },
      }));
    } finally {
      setBusy(null);
      setSyncingProvider(null);
    }
  }

  async function loadSlackChannelsForRouting(connectionId: string) {
    try {
      const channels = await listSlackChannels(connectionId);
      setSlackChannels((current) => ({ ...current, [connectionId]: channels }));
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Slack channels could not be loaded.");
    }
  }

  function connectOAuth(provider: string) {
    if (!canConnect || (provider === "slack" && !canManage) || busy) return;
    const connection = cards.find((card) => card.provider === provider)?.connection;
    oauthIntent.current = connection && connection.status !== "disconnected" ? "reconnect" : "connect";
    if (provider === "calendly") {
      setExpanded(provider);
      setAccountChoiceProvider(provider);
      return;
    }
    if (provider === "salesforce") {
      setSalesforceSetupOpen(true);
      setActionErrors((current) => {
        const next = { ...current };
        delete next.salesforce;
        return next;
      });
      return;
    }
    if (provider === "slack") setExpanded(provider);
    void authorizeOAuth(provider);
  }

  async function authorizeOAuth(provider: string) {
    if (!canConnect || busy || runningProvider.current) return;
    if (provider === "slack" && !canManage) {
      const message = "Integration management permission is required to connect Slack.";
      setActionErrors((current) => ({ ...current, [provider]: message }));
      setNotice(message);
      return;
    }
    runningProvider.current = provider;
    setBusy(provider);
    setBusyAction(oauthIntent.current);
    setOperationFeedback((current) => ({ ...current, [provider]: { state: "running", message: operationProgress[oauthIntent.current] } }));
    setNotice(null);
    setActionErrors((current) => {
      const next = { ...current };
      delete next[provider];
      return next;
    });
    try {
      const started = await beginIntegration(provider);
      window.location.assign(apiUrl(started.authorization_url));
    } catch (actionError) {
      const message = oauthConnectFailureMessage(
        provider,
        actionError instanceof Error ? actionError.message : "Authorization could not begin",
      );
      setActionErrors((current) => ({ ...current, [provider]: message }));
      setNotice(message);
      setBusy(null);
      setBusyAction(null);
      runningProvider.current = null;
      setOperationFeedback((current) => ({ ...current, [provider]: { state: "error", message } }));
    }
  }

  const sections = [
    {
      id: "available",
      eyebrow: "Ready to connect",
      title: "Available Integrations",
      description: "Connect email, calendars, CRMs and notification services to your workspace.",
      providers: ["gmail", "outlook", "google_calendar", "calendly", "salesforce", "hubspot", "gohighlevel", "slack", "openai"],
    },
    {
      id: "calling",
      eyebrow: "Calling & voice agents",
      title: "Dialers & Voice Integrations",
      description: "Connect a calling account, then start confirmed calls and read recent call activity from a lead.",
      providers: ["twilio", "aircall", "ringcentral", "vapi", "retell"],
    },
    {
      id: "coming-soon",
      eyebrow: "Release roadmap",
      title: "Coming Soon",
      description: "Visible for planning only. Connection and setup actions remain unavailable.",
      providers: ["linkedin"],
    },
  ];
  const cardByProvider = new Map(cards.map((card) => [card.provider, card]));
  const categoryCounts = cards.reduce<Record<string, number>>((counts, card) => {
    const category = integrationCategory(card);
    counts[category] = (counts[category] ?? 0) + 1;
    return counts;
  }, {});
  const displayedSections = sections.filter((section) => !selectedSection || section.providers.includes(selectedSection))
    .map((section) => ({
      ...section,
      providers: section.providers.filter((provider) => {
        if (selectedSection && provider !== selectedSection) return false;
        if (selectedSection || activeCategory === "all") return true;
        const card = cardByProvider.get(provider);
        return card ? integrationCategory(card) === activeCategory : false;
      }),
    }))
    .filter((section) => section.providers.length > 0);

  return <div className={`page-view operational-page${selectedSection ? " focused-page" : ""}`} data-integration-page={selectedSection || "overview"}>
    <header className="page-hero integrations-hero" data-tour="integrations"><div><span className="eyebrow">Integrations{selectedSection ? ` / ${cardByProvider.get(selectedSection)?.display_name || selectedSection}` : ""}</span><h1>{selectedSection ? cardByProvider.get(selectedSection)?.display_name || selectedSection : "Integrations"}</h1><p>Workspace-scoped connections, explicit permissions and secret-safe handoffs make every external action controlled and inspectable.</p></div></header>
    {!selectedSection && cards.length > 0 && <div className="integration-category-filter">
      <div className="integration-category-heading">
        <span className="eyebrow">Browse integrations</span>
        <strong>Choose a category</strong>
      </div>
      <label>
        <span className="sr-only">Integration category</span>
        <select value={activeCategory} onChange={(event) => setActiveCategory(event.target.value as IntegrationCategory)}>
          {integrationCategoryOptions.filter(([id]) => id === "all" || categoryCounts[id] > 0).map(([id, label]) => <option value={id} key={id}>{label}</option>)}
        </select>
      </label>
    </div>}
    {notice && <div className="notice" role="status">{notice}</div>}
    {!canView ? <div className="team-denied panel"><span className="eyebrow">Integration visibility</span><h2>Integration controls are restricted</h2><p>Your current workspace permissions do not include <code>integrations.view</code>.</p></div> : <div className="integration-sections">{displayedSections.map((section) => <section className={`integration-section integration-section-${section.id}`} key={section.id} aria-labelledby={`integration-section-${section.id}`}>
      <div className="integration-section-heading"><div><span className="eyebrow">{section.eyebrow}</span><h2 id={`integration-section-${section.id}`}>{section.title}</h2></div><p>{section.description}</p></div>
      <div className="integration-grid">{section.providers.map((provider) => cardByProvider.get(provider)).filter((card): card is IntegrationCard => Boolean(card)).map((card) => {
       const connection = card.connection;
       const status = connection?.status ?? "not_connected";
       const comingSoon = section.id === "coming-soon";
       const openAI = card.provider === "openai";
       const statusLabel = comingSoon ? "Coming soon" : connectionStatusLabel(connection?.status);
      const providerCredentials = credentials[card.provider] ?? {};
      const open = expandedProviders.has(card.provider);
      const activeConnectionStage = connectionStage(connection?.status);
      const syncState = connection ? syncStates[connection.id] : null;
      const crmDirection = card.category === "CRM";
      const gmailDirection = card.provider === "gmail";
      const outlookDirection = card.provider === "outlook";
       const googleCalendarDirection = card.provider === "google_calendar";
       const calendlyDirection = card.provider === "calendly";
      const mailDirection = gmailDirection || outlookDirection;
      const canConnectProvider = openAI
        ? canManage
        : canConnect && (card.provider !== "slack" || canManage);
       const slackConnections = card.provider === "slack"
         ? (card.connections.length ? card.connections : connection ? [connection] : [])
           .filter((item) => item.is_active && item.status === "verified" && item.credential_state === "configured")
         : [];
      const selectedOpenAIModel = modelSelections[card.provider] ?? connection?.selected_model ?? card.default_model ?? "";
        return <article data-navigation-section={card.provider} tabIndex={-1} className={`integration-card provider-${card.provider} state-${comingSoon ? "coming-soon" : status}${!open ? " is-collapsed" : ""}`} key={card.provider}>
          <div className="provider-head"><div className="provider-glyph">{card.display_name.slice(0, 2).toUpperCase()}</div><div className="provider-identity"><span>{card.category}</span><h2>{card.display_name}</h2><small className="provider-account-summary">{connection?.account_name ?? (comingSoon ? "Not available yet" : "No account connected")}</small></div><b><i />{statusLabel}</b><button type="button" className="provider-collapse-toggle" aria-label={`${open ? "Collapse" : "Expand"} ${card.display_name} details`} aria-expanded={open} aria-controls={`integration-details-${card.provider}`} onClick={() => toggleProviderExpanded(card.provider)}><span aria-hidden="true">{open ? "−" : "+"}</span></button></div>
          <div className="provider-details" id={`integration-details-${card.provider}`} hidden={!open}>
         <p>{card.credential_help}</p>
         {connection?.status === "connection_failed" && typeof connection.last_validation_result.message === "string" && <div className="notice" role="alert">{connection.last_validation_result.message}</div>}
         {!comingSoon && card.supported_auth.includes("oauth2") && <p className="account-selection-note">Choose your account or organization at provider sign-in. Only one account per provider remains active after verification.</p>}
         <p className="integration-direction"><strong>{comingSoon ? card.credential_help : gmailDirection ? "Automatic inbound email acquisition" : crmDirection ? "Automatic contact import · Manual contact writeback" : card.provider === "slack" ? "Application → Slack · outbound notifications only" : openAI ? "Workspace OpenAI project → structured lead analysis" : card.directionality.description}</strong>{crmDirection && " — CRM records enter only after inbound sync is enabled; sending contact data back always requires an explicit action."}</p>
         {comingSoon && <div className="coming-soon-provider" role="status"><b>Coming soon</b><span>This provider is not available for connection in the current release.</span></div>}
         {card.provider === "slack" && <>
           {slackConnections.map((slackConnection) => {
             const channelId = typeof slackConnection.metadata.slack_channel_id === "string" ? slackConnection.metadata.slack_channel_id : "";
             const memberKey = `${slackConnection.id}:${channelId}`;
             return <SlackChannelDestination key={slackConnection.id} connection={slackConnection}
               channels={slackChannels[slackConnection.id]} members={slackMembers[memberKey] ?? []}
               canManage={canManage} busy={busy === card.provider}
               onReconnect={() => void connectOAuth(card.provider)}
               onRefresh={() => void act(card.provider, async () => {
                 const channels = await listSlackChannels(slackConnection.id);
                 setSlackChannels((current) => ({ ...current, [slackConnection.id]: channels }));
               }, "Slack channels refreshed.")}
               onSelect={(id) => void act(card.provider, async () => {
                 const selected = await selectSlackChannel(slackConnection.id, id);
                 setSlackChannels((current) => ({
                   ...current, [slackConnection.id]: (current[slackConnection.id] ?? []).map((item) => item.id === id ? selected : item),
                 }));
               }, "Slack channel selected and app membership confirmed.")}
               onTest={() => void act(card.provider, () => sendSlackTestNotification(slackConnection.id), "Test notification sent to Slack.")}
               onMembers={() => void act(card.provider, async () => {
                 const members = await listSlackChannelMembers(slackConnection.id, channelId);
                 setSlackMembers((current) => ({ ...current, [memberKey]: members }));
               }, "Slack channel members loaded.")}
             />;
           })}
           <SlackRoutingEditor connections={slackConnections} config={slackRouting} channels={slackChannels}
             onLoadChannels={(connectionId) => void loadSlackChannelsForRouting(connectionId)}
             canManage={canManage} onSaved={setSlackRouting} onNotice={setNotice}
             loading={!slackRoutingReady} loadError={slackRoutingLoadError} />
         </>}
        {!comingSoon && <div className="connection-facts"><span>Credential <b>{connection?.credential_state ?? "missing"}</b></span><span>Verification <b>{connection?.verification_status?.replaceAll("_", " ") ?? "not verified"}</b></span><span>Last checked <b>{connection?.last_validation_at ? new Date(connection.last_validation_at).toLocaleString() : "Never"}</b></span></div>}
        {openAI && connection && <div className="connection-facts"><span>Selected model <b>{connection.selected_model ?? "Not configured"}</b></span><span>Billing <b>Your OpenAI account</b></span></div>}
        {openAI && <OpenAIUsagePanel />}
        {!comingSoon && <div className={`connection-state-path state-step-${activeConnectionStage}`} aria-label={`Connection state: ${connectionStages[activeConnectionStage]}`}>
          {connectionStages.map((label, index) => <div className={index < activeConnectionStage ? "complete" : index === activeConnectionStage ? "active" : ""} key={label}><i /><span>{label}</span>{index < connectionStages.length - 1 && <b />}</div>)}
        </div>}
        {!comingSoon && <div className="card-actions">
           {!connection || connection.status === "disconnected" ? canConnectProvider && <button onClick={() => card.supported_auth.includes("oauth2") ? void connectOAuth(card.provider) : showCredentialSetup(card.provider)} disabled={Boolean(busy)}>{busy === card.provider && busyAction === "connect" ? "Connecting…" : gmailDirection ? "Connect Gmail" : outlookDirection ? "Connect Outlook" : googleCalendarDirection ? "Connect Google Calendar" : calendlyDirection ? "Connect Calendly" : openAI ? "Connect OpenAI" : "Connect"}</button> : <>{canManage && <><button onClick={() => void act(card.provider, async () => { const result = await testIntegration(connection.id); if (!result.verified) throw new Error(result.message); }, `${card.display_name} connection verified.`)} disabled={Boolean(busy)}>{openAI ? "Test Connection" : "Verify connection"}</button><button className="button-quiet" onClick={() => card.supported_auth.includes("oauth2") ? void connectOAuth(card.provider) : showCredentialSetup(card.provider)} disabled={Boolean(busy)}>{busy === card.provider && busyAction === "reconnect" ? "Reconnecting…" : openAI ? "Replace Key" : "Reconnect"}</button></>}{canDisconnect && <button className="button-quiet danger" onClick={() => void act(card.provider, () => disconnectIntegration(connection.id), `${card.display_name} disconnected successfully.`, "disconnect")} disabled={Boolean(busy)}>{busy === card.provider && busyAction === "disconnect" ? "Disconnecting…" : "Disconnect"}</button>}</>}
          <button className="button-quiet" onClick={() => card.provider === "salesforce" ? setSalesforceSetupOpen(true) : toggleProviderExpanded(card.provider)}>Setup guide</button>
        </div>}
        {card.provider === "calendly" && accountChoiceProvider === card.provider && <IntegrationAccountChoice
          key={card.provider} provider={card.provider} accountName={connection?.account_name}
          busy={busy === card.provider}
          onContinue={() => void authorizeOAuth(card.provider)}
          onCancel={() => setAccountChoiceProvider(null)}
        />}
        {openAI && connection && canManage && <div className="integration-guide openai-model-config"><span className="eyebrow">Model configuration</span><label>Allowed model<select value={selectedOpenAIModel} onChange={(event) => setModelSelections((current) => ({ ...current, [card.provider]: event.target.value }))}>{card.supported_models.map((model) => <option value={model} key={model}>{model}</option>)}</select></label><button className="button-quiet" onClick={() => void act(card.provider, () => updateOpenAIModel(connection.id, selectedOpenAIModel), "OpenAI model saved. Test the connection before use.")} disabled={busy === card.provider || !selectedOpenAIModel}>Save model</button></div>}
        {!comingSoon && (crmDirection || mailDirection) && connection?.status === "verified" && <div className="integration-guide">
          <span className="eyebrow">{mailDirection ? "Automatic inbound email acquisition" : "Inbound CRM synchronization"}</span>
          <strong>{syncState?.enabled ? "Enabled · automatic inbound" : "Disabled"}</strong>
          {syncState?.enabled && <p>Automatically checks every {syncState.sync_interval_seconds} seconds, even when Corral is closed. New records appear without pressing Sync now.</p>}
          <p>{syncState?.last_successful_sync_at ? `Last synced ${new Date(syncState.last_successful_sync_at).toLocaleString()} · ${syncState.records_processed.toLocaleString()} records processed.` : "No provider records have been imported yet."}</p>
          {mailDirection && <p>{syncState?.next_sync_at ? `Next sync ${new Date(syncState.next_sync_at).toLocaleString()}.` : `Status: ${syncState?.status ?? "not configured"}.`}{syncState?.status && ` Latest result: ${syncState.status.replaceAll("_", " ")}.`}</p>}
          {syncState?.last_error && <small>{syncState.error_code || "Sync failed"}: {syncState.last_error}</small>}
          {(canInboundSync || canManage) && <div className="card-actions">
            {canManage && <button onClick={() => void act(card.provider, () => configureInboundSync(connection.id, !syncState?.enabled, card.provider), `${mailDirection ? "Automatic inbound" : "Inbound sync"} ${syncState?.enabled ? "disabled" : "enabled"}.`)} disabled={busy === card.provider}>{syncState?.enabled ? (mailDirection ? "Disable automatic inbound" : "Disable inbound sync") : (mailDirection ? "Enable automatic inbound" : "Enable inbound sync")}</button>}
            {syncState?.enabled && <button className="button-quiet" onClick={() => void syncInbound(connection.id, card.provider, syncState.initial_import_completed ? "manual" : "initial")} disabled={busy === card.provider}>{syncingProvider === card.provider ? "Syncing…" : "Sync now"}</button>}
            {syncFeedback[connection.id] && <div className={`notice integration-sync-feedback sync-feedback-${syncFeedback[connection.id].kind}`} role={syncFeedback[connection.id].kind === "error" ? "alert" : "status"} aria-live="polite">{syncFeedback[connection.id].message}</div>}
          </div>}
        </div>}
        {!comingSoon && open && <div className="integration-guide">
          <span className="eyebrow">Connection briefing</span>
          <strong>{card.dashboard_path}</strong>
          <p>{card.permission_reason}</p>
          {card.provider === "slack" && <p>New connections also request <code>channels:join</code> to join a public channel you select, and <code>groups:read</code> to list private channels after the app is invited. Corral does not read channel messages or join channels until you choose one.</p>}
          {openAI && <>
            <p>Create a dedicated OpenAI Project and project-scoped API key for this workspace. Restrict its permissions where possible, configure project budgets and limits, and rotate it periodically. Do not use a personal master key.</p>
            <p><strong>OpenAI API usage is billed directly to your OpenAI account.</strong></p>
          </>}
          <div className="scope-list">{card.required_scopes.map((scope) => <code key={scope}>{scope}</code>)}</div>
          <a href={card.documentation_url} target="_blank" rel="noreferrer">Official provider documentation →</a>
          {card.supported_auth.includes("oauth2") && canConnect && <button className="oauth-button" onClick={() => void connectOAuth(card.provider)}>Sign in to {card.display_name} →</button>}
          {card.manual_fields.length > 0 && canManage && <div className="manual-credential">
            <span>Secure credential setup</span>
            {!openAI && <label>Account label<input placeholder="Connected account name" value={accountNames[card.provider] ?? ""} onChange={(event) => setAccountNames((current) => ({ ...current, [card.provider]: event.target.value }))} /></label>}
            {openAI && !connection && <label>Model<select value={selectedOpenAIModel} onChange={(event) => setModelSelections((current) => ({ ...current, [card.provider]: event.target.value }))}>{card.supported_models.map((model) => <option value={model} key={model}>{model}</option>)}</select></label>}
            {card.manual_fields.map((field) => {
              const secret = /token|secret|jwt|api_key/.test(field);
              const label = field === "from_number" ? "Phone number (+country code)" : field.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
              const inputProps = {
                spellCheck: false,
                placeholder: openAI ? "OpenAI project API key" : field === "from_number" ? "+14155550123" : undefined,
                value: providerCredentials[field] ?? "",
                onChange: (event: React.ChangeEvent<HTMLInputElement>) => setCredentials((current) => ({ ...current, [card.provider]: { ...(current[card.provider] ?? {}), [field]: event.target.value } })),
              };
              return <label key={field}>{label}{secret
                ? <input type="password" autoComplete="new-password" {...inputProps} />
                : <input type={field === "from_number" ? "tel" : "text"} autoComplete="off" {...inputProps} />}
              </label>;
            })}
            <button disabled={Boolean(busy) || card.manual_fields.some((field) => !providerCredentials[field]?.trim())} onClick={() => void act(card.provider, () => connection ? reconnectIntegration(connection.id, providerCredentials) : addIntegrationCredential(card.provider, accountNames[card.provider] || (openAI ? "OpenAI project" : `${card.display_name} account`), card.preferred_auth === "api_key" ? "api_key" : "private_token", card.required_scopes, providerCredentials, openAI ? { selected_model: selectedOpenAIModel } : {}), `${card.display_name} credential stored securely; test it before use.`)}>{connection && openAI ? "Replace Key" : "Store credentials"}</button>
            <small>The key is sent only to the backend, encrypted by the configured secret service, cleared from this form after success, and never returned.</small>
          </div>}
        </div>}
        {operationFeedback[card.provider] && <div className={`notice integration-operation-feedback operation-${operationFeedback[card.provider].state}`} role={operationFeedback[card.provider].state === "error" ? "alert" : "status"} aria-live="polite" aria-busy={operationFeedback[card.provider].state === "running"}>{operationFeedback[card.provider].message}</div>}
        {actionErrors[card.provider] && <div className="integration-action-error" role="alert">{actionErrors[card.provider]}</div>}
          </div>
       </article>;
    })}</div>
    </section>)}</div>}
     {salesforceSetupOpen && <SalesforceSetupGuide
       accountName={cards.find((card) => card.provider === "salesforce")?.connection?.account_name}
       onClose={() => setSalesforceSetupOpen(false)}
       onContinue={() => { if (canConnect && !busy) void authorizeOAuth("salesforce"); }}
       busy={busy !== null}
       canAuthorize={canConnect}
       error={actionErrors.salesforce}
     />}
   </div>;
}
