import { Children, isValidElement, useEffect, useRef, useState, type ReactElement, type ReactNode } from "react";
import type { CloudTalkAgent, LeadConfirmationSources, SlackChannel } from "../api";
import { IntegrationAccountChoice } from "../components/IntegrationAccountChoice";
import { FeatureGuide } from "../components/FeatureGuide";
import { GmailAccountsPanel } from "../components/GmailAccountsPanel";
import { IntegrationSetupGuide } from "../components/IntegrationSetupGuide";
import { BRAND_MARKS } from "../v3/ui/brandMarks";
import { Icon } from "../v3/ui/Icon";
import { OperationsDialog } from "../components/OperationsDialog";
import { SlackChannelDestination } from "../components/SlackChannelDestination";
import { SlackSetupGuide } from "../components/SlackSetupGuide";
import { createSlackRule } from "../slackRouting";

import {
addIntegrationCredential,
apiUrl,
backfillReasoning,
beginIntegration,
  cancelInboundSync,
configureInboundSync,
disconnectIntegration,
  getReasoningBackfillStatus,
  getLeadConfirmationSources,
getInboundSyncState,
getInboundSyncRuns,
getSlackNotificationConfig,
listIntegrations,
  listCloudTalkAgents,
listSlackChannelMembers,
listSlackChannels,
reconnectIntegration,
runInboundSync,
  setLeadConfirmationSource,
selectSlackChannel,
  selectCloudTalkAgent,
sendSlackTestNotification,
testIntegration,
updateReasoningModel,
updateSlackNotificationConfig,
} from "../api";
import { OpenAIUsagePanel } from "../components/OpenAIUsagePanel";
import { AiUsagePanel } from "../components/AiUsagePanel";
import { SalesforceSetupGuide } from "../components/SalesforceSetupGuide";
import {
  isEmbeddedDialerConnection,
  OPEN_DIALER_PHONE_EVENT,
  ringCentralEmbedClientId,
} from "../components/dialerPhoneConfig";
import { can } from "../permissions";
import { isOAuthPopupResult, OAUTH_RESULT_CHANNEL } from "../oauthPopup";
import { pollInboundSync } from "../inboundSyncPolling";
import { parseGmailReviewNotes } from "../gmailSyncPresentation";
import type { CrmInboundSyncRun, CrmInboundSyncState, IntegrationCard, ProviderConnection, SlackNotificationConfig, SlackNotificationRule } from "../types";

function syncRunMessage(run: CrmInboundSyncRun) {
  const previous = run.mode === "review_previous";
  const review = run.mode === "review_recent" || previous;
  if (run.status === "completed") {
    return previous
      ? "Previous Gmail review finished; no more older messages remain."
      : review ? "AI review finished." : "Sync finished.";
  }
  if (run.status === "cancelled") return review ? "AI review cancelled. Completed leads were kept." : "Sync cancelled. Completed records were kept.";
  if (run.status === "completed_with_errors") {
    return previous
      ? "Previous-email review finished with some messages needing attention."
      : review ? "AI review finished with some messages needing attention." : "Sync finished with some records needing attention.";
  }
  if (run.status === "completed_bounded") {
    return previous
      ? "Previous-email batch complete. Run again to continue further back."
      : review ? "AI review batch finished." : "Sync batch finished.";
  }
  return review ? "AI review failed." : "Sync failed.";
}

function syncRunKind(run: CrmInboundSyncRun): "success" | "error" | "cancelled" {
  if (run.status === "cancelled") return "cancelled";
  return run.status === "completed" || run.status === "completed_bounded" ? "success" : "error";
}

function ProviderDetailsPanel({
  provider,
  open,
  onClose,
  id,
  title,
  subtitle,
  children,
}: {
  provider: string;
  open: boolean;
  onClose: () => void;
  id: string;
  title: string;
  subtitle?: ReactNode;
  children: ReactNode;
}) {
  const [tab, setTab] = useState<string | null>(null);
  useEffect(() => { if (!open) setTab(null); }, [open]);
  if (provider === "slack") {
    return open ? <OperationsDialog id={id} title="Slack connection" onClose={onClose} className="slack-connection-dialog">
      <div className="provider-details slack-provider-details">{children}</div>
    </OperationsDialog> : null;
  }
  void title; void subtitle; void onClose;
  if (!open) return null;

  // The setup is organised in three places: a summary (what this connection does and
  // its main actions), then one tab per area, each holding what used to be a disclosure.
  const summary: ReactNode[] = [];
  const actions: ReactNode[] = [];
  const panes: Array<{ id: string; label: string; open: boolean; body: ReactNode }> = [];
  Children.toArray(children).forEach((child) => {
    if (isValidElement(child) && child.type === IntegrationDisclosure) {
      const el = child as ReactElement<{ title: string; className?: string; open?: boolean; children: ReactNode }>;
      const cls = el.props.className ?? "";
      const paneId = cls.includes("settings") ? "settings" : cls.includes("briefing") ? "connection" : "guide";
      if (paneId === "connection" && !cls.includes("has-form")) return;
      if (paneId === "settings" && !cls.includes("is-live") && !el.props.title.startsWith("Usage")) return;
      const label = paneId === "settings" ? (el.props.title.startsWith("Usage") ? "Usage" : "Settings") : paneId === "connection" ? "Connection" : "Setup guide";
      panes.push({ id: paneId, label, open: Boolean(el.props.open), body: el.props.children });
    } else if (isValidElement(child) && String((child.props as { className?: string }).className ?? "").includes("integration-direction")) {
      // The card header already says what this connection does.
    } else if (isValidElement(child) && String((child.props as { className?: string }).className ?? "").includes("integration-connection-actions")) {
      actions.push(child);
    } else {
      summary.push(child);
    }
  });
  const order = ["overview", "connection", "settings", "guide"];
  panes.sort((x, y) => order.indexOf(x.id) - order.indexOf(y.id));
  // A compact provider header is enough until the user chooses Manage. Everything
  // else—including mailbox rows and automatic-sync controls—belongs in a tab.
  const overview = summary.length > 0
    ? { id: "overview", label: "Overview", open: true, body: <div className="int-overview">{summary}</div> }
    : null;
  // Working panes (overview, connection, settings, usage) get tabs; the setup guide is one quiet row beneath them.
  const working = [...(overview ? [overview] : []), ...panes.filter((pane) => pane.id !== "guide")];
  const guide = panes.find((pane) => pane.id === "guide");
  const active = working.find((pane) => pane.id === tab) ?? working.find((pane) => pane.open) ?? working[0];
  const showTabs = working.length > 1;

  return (
    <div className="int-detail" id={id}>
      {actions.length > 0 && <div className="int-detail-actions">{actions}</div>}
      {showTabs && (
        <div className="int-tabs" role="tablist" aria-label="Integration sections">
          {working.map((pane) => (
            <button key={pane.id} type="button" role="tab" id={`${id}-tab-${pane.id}`} aria-selected={active?.id === pane.id} aria-controls={`${id}-pane-${pane.id}`} tabIndex={active?.id === pane.id ? 0 : -1} onClick={() => setTab(pane.id)}>{pane.label}</button>
          ))}
        </div>
      )}
      {active && <div className="int-pane" role={showTabs ? "tabpanel" : undefined} id={`${id}-pane-${active.id}`} aria-labelledby={showTabs ? `${id}-tab-${active.id}` : undefined}>{active.body}</div>}
      {guide && (
        <details className="int-guide-row" open={!active && actions.length === 0}>
          <summary>Setup guide</summary>
          <div className="int-guide-body">{guide.body}</div>
        </details>
      )}
    </div>
  );
}

function IntegrationDisclosure({
  title,
  children,
  className = "",
  open = false,
}: {
  title: string;
  children: ReactNode;
  className?: string;
  open?: boolean;
}) {
  return <details className={`integration-section-disclosure ${className}`} open={open}>
    <summary>{title}</summary>
    <div className="integration-section-disclosure-content">{children}</div>
  </details>;
}

const AI_PROVIDER_GROUP = "ai-analysis";
const AI_PROVIDER_IDS = ["openai", "gemini", "claude", "deepseek", "xai"] as const;
type ReasoningProviderId = typeof AI_PROVIDER_IDS[number];
function isReasoningProvider(provider: string): provider is ReasoningProviderId {
  return (AI_PROVIDER_IDS as readonly string[]).includes(provider);
}
type ConnectionOperation = "connect" | "reconnect" | "disconnect";
const operationProgress: Record<ConnectionOperation, string> = {
  connect: "Connecting…", reconnect: "Reconnecting…", disconnect: "Disconnecting…",
};
const adminOnlyProviders = new Set(["instagram", "messenger", "whatsapp_business"]);

const integrationCategoryOptions = [
  ["all", "All integrations"],
  ["email", "Email"],
  ["crm", "CRM"],
  ["ai", "AI"],
  ["calling", "Calling"],
  ["calendar", "Calendar"],
  ["notifications", "Notifications"],
  ["social", "Social"],
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
  if (category.includes("social")) return "social";
  return "other";
}

const FAILED_STATUSES = new Set(["needs_attention", "connection_failed", "authorization_expired", "reconnect_required", "expired", "needs_reauthorization", "error"]);

type ConnectionTone = "ok" | "pending" | "error" | "idle";
function connectionTone(status?: string): ConnectionTone {
  if (status === "verified") return "ok";
  if (FAILED_STATUSES.has(status ?? "")) return "error";
  if (["connected", "configured", "validating", "connecting", "awaiting_authorization"].includes(status ?? "")) return "pending";
  return "idle";
}

function connectionStatusLabel(status?: string) {
  if (!status || ["disconnected", "not_connected"].includes(status)) return "Not connected";
  if (status === "verified") return "Connected";
  if (FAILED_STATUSES.has(status)) return status.includes("expire") || status.includes("reauth") ? "Sign-in expired" : "Needs attention";
  if (["connecting", "awaiting_authorization"].includes(status)) return "Waiting for sign-in";
  return "Not verified yet";
}

const BRAND_TINT: Record<string, string> = {
  gmail: "#d93025", outlook: "#0a64ad", google_calendar: "#1a73e8", calendly: "#006bff", slack: "#4a154b",
  salesforce: "#0b72c7", hubspot: "#e0613f", gohighlevel: "#155ec9", twilio: "#e22e43", aircall: "#00a379",
  ringcentral: "#e36b00", vapi: "#1f2937", retell: "#111827", cloudtalk: "#1f5fe0", openai: "#111827",
  gemini: "#2f6fe4", claude: "#c96442", deepseek: "#4d6bfe", xai: "#111827", linkedin: "#0a66c2",
  instagram: "#c1356f", messenger: "#0078ff", whatsapp_business: "#1a9e57", clutch: "#17313b",
};

function ProviderMark({ provider, name }: { provider: string; name: string }) {
  const mark = BRAND_MARKS[provider];
  const [hasFile, setHasFile] = useState(false);
  const [ext, setExt] = useState<"svg" | "png">("svg");
  if (mark) {
    return (
      <span className="int-mark int-mark-logo" aria-hidden="true">
        <svg viewBox="0 0 24 24" fill={mark.hex} focusable="false"><path d={mark.path} /></svg>
      </span>
    );
  }
  // Official artwork can be dropped in public/brand/logos/<provider>.svg; until then the lettered tile stands in.
  return (
    <span className={`int-mark${hasFile ? " int-mark-logo" : ""}`} style={{ "--int-tint": BRAND_TINT[provider] ?? "var(--tint)" } as React.CSSProperties} aria-hidden="true">
      {!hasFile && name.trim().charAt(0).toUpperCase()}
      <img src={`/brand/logos/${provider}.${ext}`} alt="" hidden={!hasFile} onLoad={() => setHasFile(true)} onError={() => { setHasFile(false); if (ext === "svg") setExt("png"); }} />
    </span>
  );
}

function oauthConnectFailureMessage(provider: string, message: string) {
  const settings: Record<string, { name: string; keys: [string, string] }> = {
    slack: { name: "Slack", keys: ["SLACK_CLIENT_ID", "SLACK_CLIENT_SECRET"] },
    salesforce: { name: "Salesforce", keys: ["SALESFORCE_CLIENT_ID", "SALESFORCE_CLIENT_SECRET"] },
    hubspot: { name: "HubSpot", keys: ["HUBSPOT_CLIENT_ID", "HUBSPOT_CLIENT_SECRET"] },
    google_calendar: { name: "Google Calendar", keys: ["GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET"] },
    calendly: { name: "Calendly", keys: ["CALENDLY_CLIENT_ID", "CALENDLY_CLIENT_SECRET"] },
    instagram: { name: "Instagram", keys: ["META_INSTAGRAM_CLIENT_ID", "META_INSTAGRAM_CLIENT_SECRET"] },
    messenger: { name: "Messenger", keys: ["META_MESSENGER_CLIENT_ID", "META_MESSENGER_CLIENT_SECRET"] },
    whatsapp_business: { name: "WhatsApp Business", keys: ["META_WHATSAPP_CLIENT_ID", "META_WHATSAPP_CLIENT_SECRET"] },
  };
  const providerSettings = settings[provider];
  if (providerSettings && /OAuth application credentials are not configured|Meta social connections require real provider OAuth credentials/i.test(message)) {
    return `${providerSettings.name} sign-in isn't set up for this workspace yet. Ask your Corral administrator to enable it.`;
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
      <span>{selected.length ? selected.map(([, optionLabel]) => optionLabel).join(", ") : `Any ${label.toLowerCase()}`}</span><i aria-hidden="true"><svg viewBox="0 0 16 16"><path d="m4 6 4 4 4-4" /></svg></i>
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

  if (loading || loadError) return <section className="integration-guide slack-routing-guide"><div className="slack-routing-heading"><div><h3>{loading ? "Loading saved routing" : "Routing could not be loaded"}</h3><p>{loading ? "Reading the current Slack rules before enabling edits." : loadError}</p></div></div></section>;
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
      const next = await updateSlackNotificationConfig({
        default_connection_id: defaultConnectionId,
        rules: rules.map((rule) => ({
          ...rule,
          channel_id: rule.destination_type === "direct_message"
            ? rule.channel_id?.trim().toUpperCase() || null
            : rule.channel_id,
        })),
      });
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
        
        <h3>Decide where each lead goes</h3>
        <p>{verified.length <= 1 ? "Use the default destination for every lead, or add conditions for specific lead types." : "Route different lead types to different Slack workspaces and channels."}</p>
      </div>
      <div className="slack-routing-health"><span>{verified.length} verified workspace{verified.length === 1 ? "" : "s"}</span></div>
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
          <div className="slack-rule-heading"><div><span className="slack-rule-index">Rule {index + 1}</span><input aria-label="Slack routing rule name" value={rule.name} onChange={(event) => updateRule(rule.id, { name: event.target.value })} /></div><button className="button-quiet danger" onClick={() => { setEditing(true); setRules((current) => current.filter((item) => item.id !== rule.id)); }}>Remove</button></div>
          <div className="slack-condition-grid">
            <SlackMultiSelect label="Lead classifications" values={rule.qualification_bands} options={slackBandOptions} onChange={(values) => updateRule(rule.id, { qualification_bands: values })} hint="Choose one or more; empty means all." />
            <SlackMultiSelect label="Lead sources" values={rule.sources} options={slackSourceOptions} onChange={(values) => updateRule(rule.id, { sources: values })} hint="Choose one or more; empty means all." />
            <SlackMultiSelect label="Queues" values={rule.queues} options={slackQueueOptions} onChange={(values) => updateRule(rule.id, { queues: values })} hint="Choose one or more; empty means all." />
          </div>
          <div className="slack-rule-destination"><label><span>Send to workspace</span><select value={rule.connection_id} onChange={(event) => { const connectionId = event.target.value; const connection = verified.find((item) => item.id === connectionId); updateRule(rule.id, { connection_id: connectionId, channel_id: typeof connection?.metadata.slack_channel_id === "string" ? connection.metadata.slack_channel_id : null }); }}>{verified.map((item) => <option value={item.id} key={item.id}>{item.account_name}</option>)}</select></label><label><span>Destination type</span><select value={rule.destination_type ?? "channel"} onChange={(event) => { const destinationType = event.target.value as "channel" | "direct_message"; const connection = verified.find((item) => item.id === rule.connection_id); updateRule(rule.id, { destination_type: destinationType, channel_id: destinationType === "channel" && typeof connection?.metadata.slack_channel_id === "string" ? connection.metadata.slack_channel_id : null }); }}><option value="channel">Slack channel</option><option value="direct_message">Direct message</option></select></label>{(rule.destination_type ?? "channel") === "direct_message" ? <label><span>Slack member ID</span><input placeholder="U0123456789" value={rule.channel_id ?? ""} onChange={(event) => updateRule(rule.id, { channel_id: event.target.value || null })} /><small>Paste the member ID from Slack.</small></label> : <label><span>Send to channel</span><select value={rule.channel_id ?? ""} onChange={(event) => updateRule(rule.id, { channel_id: event.target.value || null })}><option value="">Use workspace default channel</option>{channelOptions(rule).map((channel) => <option value={channel.id} key={channel.id}>#{channel.name}</option>)}</select></label>}{(rule.destination_type ?? "channel") === "channel" && (channels[rule.connection_id] ?? []).length === 0 && <button className="button-quiet" type="button" onClick={() => onLoadChannels(rule.connection_id)}>Load channels</button>}<label className="slack-weight"><span>Weight</span><input aria-label={`Weight for ${rule.name}`} type="number" min="0.01" max="100" step="0.25" value={rule.weight ?? 1} onChange={(event) => updateRule(rule.id, { weight: Number(event.target.value) || 1 })} /></label></div>
          {rule.destination_type === "direct_message" && <label className="slack-fallback-toggle"><input type="checkbox" checked={rule.fallback_to_channel ?? false} onChange={(event) => updateRule(rule.id, { fallback_to_channel: event.target.checked })} /><span>Only if this DM fails, fall back to the configured workspace channel</span></label>}
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
  const [selectedAIProvider, setSelectedAIProvider] = useState("openai");
  const [expandedProviders, setExpandedProviders] = useState<Set<string>>(() => new Set());
  const [slackGuideOpen, setSlackGuideOpen] = useState(false);
  const [slackPanelOpen, setSlackPanelOpen] = useState(false);
  const callingSection = selectedSection === "calling";
  const aiSection = selectedSection === "ai";
  const selectedProvider = !callingSection && !aiSection && selectedSection ? selectedSection : "";
  useEffect(() => {
    if (!selectedProvider) return;
    setExpandedProviders((previous) => new Set([...previous, selectedProvider]));
  }, [selectedProvider, cards.length]);
  const [salesforceSetupOpen, setSalesforceSetupOpen] = useState(false);
  const [accountNames, setAccountNames] = useState<Record<string, string>>({});
  const [credentials, setCredentials] = useState<Record<string, Record<string, string>>>({});
  const [cloudTalkAgents, setCloudTalkAgents] = useState<Record<string, CloudTalkAgent[]>>({});
  const [cloudTalkAgentSelections, setCloudTalkAgentSelections] = useState<Record<string, string>>({});
  const [modelSelections, setModelSelections] = useState<Record<string, string>>({});
  const aiProviderSelectionInitialized = useRef(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [actionErrors, setActionErrors] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [busyTargetConnectionId, setBusyTargetConnectionId] = useState<string | null>(null);
  const [accountChoiceProvider, setAccountChoiceProvider] = useState<"calendly" | null>(null);
  const [busyAction, setBusyAction] = useState<ConnectionOperation | null>(null);
  const [operationFeedback, setOperationFeedback] = useState<Record<string, { state: "running" | "success" | "error"; message: string }>>({});
  const runningProvider = useRef<string | null>(null);
  const oauthPopupRef = useRef<{
    provider: string;
    popup: Window;
    pollId: number;
    timeoutId: number;
  } | null>(null);
  const oauthIntent = useRef<ConnectionOperation>("connect");
  const slackVerificationAttempts = useRef(new Set<string>());
  const slackChannelLoads = useRef(new Set<string>());
  const syncWatchers = useRef(new Set<string>());
  const [syncingProvider, setSyncingProvider] = useState<string | null>(null);
  const [reviewLimits, setReviewLimits] = useState<Record<string, string>>({});
  const [syncFeedback, setSyncFeedback] = useState<Record<string, { kind: "progress" | "success" | "error" | "cancelled"; message: string }>>({});
  const [syncResults, setSyncResults] = useState<Record<string, CrmInboundSyncRun | null>>({});
  const [syncStates, setSyncStates] = useState<Record<string, CrmInboundSyncState | null>>({});
  const [syncCancelRequests, setSyncCancelRequests] = useState<Record<string, boolean>>({});
  const [leadConfirmationSources, setLeadConfirmationSources] =
    useState<LeadConfirmationSources | null>(null);
  const [leadConfirmationSourcesLoading, setLeadConfirmationSourcesLoading] =
    useState(true);
  const [leadConfirmationSourceBusy, setLeadConfirmationSourceBusy] =
    useState<string | null>(null);
  const [leadConfirmationSourceError, setLeadConfirmationSourceError] =
    useState<string | null>(null);
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
  const canCallDialer = can(permissions, "dialer.call");
  const canInboundSync = canSync;
  useEffect(() => {
    if (isReasoningProvider(selectedSection)) {
      setSelectedAIProvider(selectedSection);
      aiProviderSelectionInitialized.current = true;
      return;
    }
    if (aiProviderSelectionInitialized.current || cards.length === 0) return;
    const availableAiCards = cards.filter((card) => isReasoningProvider(card.provider));
    const preferred = availableAiCards.find((card) => card.connection?.status === "verified" && card.connection.is_active)
      ?? availableAiCards.find((card) => card.connection?.status === "verified")
      ?? availableAiCards[0];
    if (preferred) setSelectedAIProvider(preferred.provider);
    aiProviderSelectionInitialized.current = true;
  }, [cards, selectedSection]);
  function toggleProviderExpanded(provider: string) {
    // One setup sheet at a time.
    setExpandedProviders((current) => current.has(provider) ? new Set() : new Set([provider]));
  }
  function showCredentialSetup(provider: string) {
    setExpanded(provider);
    requestAnimationFrame(() => document.getElementById(`integration-details-${provider}`)?.querySelector<HTMLInputElement>(".manual-credential input")?.focus());
  }

  async function updateConfirmationSource(source: string, enabled: boolean | null) {
    setLeadConfirmationSourceBusy(source);
    setNotice(null);
    try {
      const updated = await setLeadConfirmationSource(source, enabled);
      setLeadConfirmationSources(updated);
      const selected = updated.sources.find((item) => item.source === source);
      setNotice(
        enabled === null
          ? `${selected?.label || source} confirmations now use the workspace default.`
          : `${selected?.label || source} confirmation emails are ${enabled ? "on" : "off"}.`,
      );
      setLeadConfirmationSourceError(null);
    } catch (error) {
      setNotice(
        error instanceof Error
          ? error.message
          : "Unable to update platform confirmation setting",
      );
    } finally {
      setLeadConfirmationSourceBusy(null);
    }
  }

  function setExpanded(provider: string | null) {
    if (provider) {
      setExpandedProviders(new Set([provider]));
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
      window.dispatchEvent(new Event("corral:integrations-changed"));
      if (canManage) {
        try {
          setLeadConfirmationSources(await getLeadConfirmationSources());
          setLeadConfirmationSourceError(null);
        } catch (error) {
          setLeadConfirmationSourceError(
            error instanceof Error
              ? error.message
              : "Platform confirmation settings could not be loaded.",
          );
        } finally {
          setLeadConfirmationSourcesLoading(false);
        }
      } else {
        setLeadConfirmationSourcesLoading(false);
      }
      setCloudTalkAgentSelections((current) => {
        const next = { ...current };
        for (const card of nextCards) {
          const connection = card.provider === "cloudtalk" ? card.connection : null;
          const selected = connection?.metadata.cloudtalk_agent_id;
          if (connection && typeof selected === "string" && !next[connection.id]) {
            next[connection.id] = selected;
          }
        }
        return next;
      });
      try {
        setSlackRouting(await getSlackNotificationConfig());
        setSlackRoutingLoadError(null);
      } catch (error) {
        setSlackRouting(null);
        setSlackRoutingLoadError(error instanceof Error ? error.message : "Saved Slack routing could not be loaded.");
      } finally {
        setSlackRoutingReady(true);
      }
      const inboundConnections = nextCards.flatMap((card) =>
        card.category === "CRM" || ["gmail", "outlook"].includes(card.provider)
          ? (card.connections.length ? card.connections : card.connection ? [card.connection] : [])
              .filter((connection) => connection.is_active)
              .map((connection) => ({ provider: card.provider, connection }))
          : [],
      );
      const entries = await Promise.all(inboundConnections.map(async ({ connection, provider }) => {
        try { return [connection.id, await getInboundSyncState(connection.id, provider)] as const; }
        catch { return [connection.id, null] as const; }
      }));
      setSyncStates(Object.fromEntries(entries));
      const gmailConnections = inboundConnections.filter((item) => item.provider === "gmail");
      const gmailRuns = await Promise.all(gmailConnections.map(async ({ connection }) => {
        try {
          const runs = await getInboundSyncRuns(connection.id);
          return [connection.id, runs[0] ?? null] as const;
        } catch {
          return [connection.id, null] as const;
        }
      }));
      setSyncResults((current) => ({
        ...current,
        ...Object.fromEntries(gmailRuns.filter((entry): entry is readonly [string, CrmInboundSyncRun] => Boolean(entry[1]))),
      }));
      setSyncFeedback((current) => {
        const next = { ...current };
        for (const [connectionId, run] of gmailRuns) {
          if (!run || syncWatchers.current.has(connectionId)) continue;
          next[connectionId] = ["queued", "running", "cancel_requested"].includes(run.status)
            ? {
              kind: "progress",
              message: run.status === "cancel_requested"
                ? "Cancellation requested. Waiting for the current Gmail operation to stop…"
                : "Sync is still running; reconnecting to progress…",
            }
            : { kind: syncRunKind(run), message: syncRunMessage(run) };
        }
        return next;
      });
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Integrations could not be loaded");
    }
  };
  function finishOAuthPopup(provider: string, status: "connected" | "failed", callbackMessage?: string) {
    const attempt = oauthPopupRef.current;
    if (!attempt || attempt.provider !== provider) return;
    window.clearInterval(attempt.pollId);
    window.clearTimeout(attempt.timeoutId);
    if (!attempt.popup.closed) attempt.popup.close();
    oauthPopupRef.current = null;
    setBusy(null);
    setBusyAction(null);
    runningProvider.current = null;

    const displayName = cards.find((card) => card.provider === provider)?.display_name
      ?? provider.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
    const message = status === "connected"
      ? `${displayName} signed in. Verify the connection before relying on it.`
      : callbackMessage || `${displayName} authorization was cancelled or failed. Reconnect to try again.`;
    setNotice(message);
    setActionErrors((current) => {
      const next = { ...current };
      if (status === "connected") delete next[provider];
      else next[provider] = message;
      return next;
    });
    setOperationFeedback((current) => ({
      ...current,
      [provider]: { state: status === "connected" ? "success" : "error", message },
    }));
    void refresh();
  }

  function finishClosedOAuthPopup(provider: string, message: string) {
    const attempt = oauthPopupRef.current;
    if (!attempt || attempt.provider !== provider) return;
    finishOAuthPopup(provider, "failed", message);
  }

  useEffect(() => {
    const onResult = (value: unknown) => {
      if (isOAuthPopupResult(value)) {
        finishOAuthPopup(value.provider, value.status, value.message);
      }
    };
    const onWindowMessage = (event: MessageEvent) => {
      if (event.origin === window.location.origin) onResult(event.data);
    };
    window.addEventListener("message", onWindowMessage);
    let channel: BroadcastChannel | null = null;
    if ("BroadcastChannel" in window) {
      channel = new BroadcastChannel(OAUTH_RESULT_CHANNEL);
      channel.onmessage = (event: MessageEvent<unknown>) => onResult(event.data);
    }
    return () => {
      window.removeEventListener("message", onWindowMessage);
      channel?.close();
    };
  }, [cards]);

  useEffect(() => () => {
    const attempt = oauthPopupRef.current;
    if (!attempt) return;
    window.clearInterval(attempt.pollId);
    window.clearTimeout(attempt.timeoutId);
    if (!attempt.popup.closed) attempt.popup.close();
    oauthPopupRef.current = null;
  }, []);

  useEffect(() => { if (canView) void refresh(); }, [canView]);
  useEffect(() => {
    const aiProviders = cards.filter((card) => integrationCategory(card) === "ai").map((card) => card.provider);
    const requestedProvider = selectedProvider && aiProviders.includes(selectedProvider) ? selectedProvider : "";
    if (requestedProvider) {
      setSelectedAIProvider(requestedProvider);
    } else if (!aiProviders.includes(selectedAIProvider)) {
      setSelectedAIProvider(aiProviders[0] ?? "openai");
    }
  }, [cards, selectedProvider, selectedAIProvider]);
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
      const name = provider === "salesforce"
        ? "Salesforce"
        : provider === "slack"
          ? "Slack"
          : provider.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
      const message = params.get("message")?.trim().slice(0, 500);
      setNotice(status === "connected" ? `${name} signed in. Verify the connection before relying on it.` : message || `${name} authorization failed. Reconnect to try again.`);
      setOperationFeedback((current) => ({ ...current, [provider]: {
        state: status === "connected" ? "success" : "error",
        message: status === "connected" ? `${name} signed in. Verify the connection before relying on it.` : message || `${name} authorization failed.`,
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

  async function act(
    provider: string,
    operation: () => Promise<unknown>,
    success: string | ((result: unknown) => string),
    kind?: ConnectionOperation,
    targetConnectionId?: string,
  ) {
    if (runningProvider.current) return;
    runningProvider.current = provider;
    setBusy(provider); setBusyAction(kind ?? null); setBusyTargetConnectionId(targetConnectionId ?? null); setNotice(null);
    if (kind) setOperationFeedback((current) => ({ ...current, [provider]: { state: "running", message: operationProgress[kind] } }));
    try {
      const result = await operation();
      const successMessage = typeof success === "function" ? success(result) : success;
      if (kind === "disconnect") setCards((current) => current.map((card) => {
        if (card.provider !== provider) return card;
        const id = targetConnectionId ?? card.connection?.id;
        if (!id) return card;
        const row = card.connections.find((item) => item.id === id) ?? (card.connection?.id === id ? card.connection : null);
        if (!row) return card;
        const disconnected: ProviderConnection = { ...row, status: "disconnected", is_active: false, credential_state: "missing" };
        return {
          ...card,
          connection: card.connection?.id === id ? disconnected : card.connection,
          connections: card.connections.map((item) => item.id === id ? disconnected : item),
        };
      }));
      setNotice(successMessage);
      if (kind) setOperationFeedback((current) => ({ ...current, [provider]: { state: "success", message: successMessage } }));
      setCredentials((current) => ({ ...current, [provider]: {} }));
      await refresh();
    } catch (actionError) {
      const message = actionError instanceof Error ? actionError.message : "Integration action failed";
      setNotice(message);
      if (kind) setOperationFeedback((current) => ({ ...current, [provider]: { state: "error", message } }));
    } finally { setBusy(null); setBusyAction(null); setBusyTargetConnectionId(null); runningProvider.current = null; }
  }

  async function runReasoningBackfill(
    connectionId: string,
    provider: string,
  ): Promise<{ message: string }> {
    const queued = await backfillReasoning(connectionId);
    if (queued.status === "no_eligible_leads") {
      setOperationFeedback((current) => ({
        ...current,
        [provider]: { state: "success", message: queued.message },
      }));
      return { message: queued.message };
    }
    setOperationFeedback((current) => ({
      ...current,
      [provider]: {
        state: "running",
        message: `AI re-analysis queued for ${queued.queued_count} workspace leads.`,
      },
    }));
    const terminalStatuses = new Set(["completed", "partial_failure", "failed", "no_eligible_leads"]);
    let latestStatus: Awaited<ReturnType<typeof getReasoningBackfillStatus>> | null = null;
    for (let attempt = 0; attempt < 60; attempt += 1) {
      latestStatus = await getReasoningBackfillStatus(connectionId, queued.job_id);
      const processed = latestStatus.completed + latestStatus.failed + latestStatus.skipped;
      setOperationFeedback((current) => ({
        ...current,
        [provider]: {
          state: "running",
          message: `AI re-analysis: ${processed} / ${latestStatus?.total ?? 0} processed · ${latestStatus?.completed ?? 0} updated · ${latestStatus?.failed ?? 0} failed · ${latestStatus?.skipped ?? 0} already current.`,
        },
      }));
      if (terminalStatuses.has(latestStatus.status)) break;
      await new Promise((resolve) => window.setTimeout(resolve, 1500));
    }
    if (!latestStatus || !terminalStatuses.has(latestStatus.status)) {
      return { message: "AI re-analysis is still running. Check the AI connection again shortly." };
    }
    if (latestStatus.status === "no_eligible_leads") {
      return { message: "There are no existing leads that need this provider configuration." };
    }
    if (latestStatus.status === "failed" || latestStatus.status === "partial_failure") {
      const categories = Object.entries(latestStatus.failure_categories ?? {})
        .map(([category, count]) => `${category}: ${count}`)
        .join(", ");
      const reason = categories ? ` (${categories})` : "";
      const failedLeadIds = (latestStatus.failures ?? [])
        .slice(0, 5)
        .map((failure) => failure.lead_id)
        .join(", ");
      const failedLeads = failedLeadIds
        ? ` Affected lead IDs: ${failedLeadIds}${(latestStatus.failures?.length ?? 0) > 5 ? ", and more" : ""}.`
        : "";
      const message = `AI re-analysis finished with ${latestStatus.failed} failed of ${latestStatus.total} leads${reason}.${failedLeads}`;
      setOperationFeedback((current) => ({
        ...current,
        [provider]: { state: "error", message },
      }));
      throw new Error(
        message,
      );
    }
    const message = `AI re-analysis complete: ${latestStatus.completed} updated, ${latestStatus.skipped} already current.`;
    setOperationFeedback((current) => ({
      ...current,
      [provider]: { state: "success", message },
    }));
    return { message };
  }

  async function syncInbound(
    connectionId: string,
    provider: string,
    mode: "initial" | "manual" | "review_recent" | "review_previous",
  ) {
    if (syncWatchers.current.has(connectionId)) return;
    syncWatchers.current.add(connectionId);
    setBusy(provider);
    setSyncingProvider(provider);
    setSyncCancelRequests((current) => ({ ...current, [connectionId]: false }));
    setNotice(null);
    setSyncResults((current) => ({ ...current, [connectionId]: null }));
    setSyncFeedback((current) => ({
      ...current,
      [connectionId]: {
        kind: "progress",
        message: mode === "review_recent"
          ? "Scanning recent Gmail messages and reviewing them with AI."
          : mode === "review_previous"
            ? "Scanning previous Gmail messages and reviewing them with AI."
          : "Syncing provider records. This may take a moment.",
      },
    }));
    try {
      let result = await runInboundSync(connectionId, mode, provider, Number(reviewLimits[connectionId] ?? "100"));
      if ("queued_at" in result) {
        const accepted = result;
        setSyncFeedback((current) => ({
          ...current,
          [connectionId]: { kind: "progress", message: accepted.message },
        }));
        result = await pollInboundSync({
          queuedAt: accepted.queued_at,
          getRuns: () => getInboundSyncRuns(connectionId),
          getState: () => getInboundSyncState(connectionId, provider),
          onProgress: (message) => {
            setSyncFeedback((current) => ({
              ...current,
              [connectionId]: { kind: "progress", message },
            }));
          },
        });
      }
      if ("queued_at" in result) throw new Error("Gmail sync did not return a completed run.");
      setSyncResults((current) => ({ ...current, [connectionId]: result as CrmInboundSyncRun }));
      setSyncFeedback((current) => ({
        ...current,
        [connectionId]: {
          kind: syncRunKind(result),
          message: syncRunMessage(result),
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
      syncWatchers.current.delete(connectionId);
      setSyncCancelRequests((current) => ({ ...current, [connectionId]: false }));
      setBusy(null);
      setSyncingProvider(null);
    }
  }

  async function resumeInboundSync(connectionId: string, provider: string) {
    if (syncWatchers.current.has(connectionId)) return;
    syncWatchers.current.add(connectionId);
    setBusy(provider);
    setSyncingProvider(provider);
    setSyncCancelRequests((current) => ({ ...current, [connectionId]: false }));
    setSyncFeedback((current) => ({
      ...current,
      [connectionId]: { kind: "progress", message: "Reconnecting to the active Gmail sync…" },
    }));
    try {
      const runs = await getInboundSyncRuns(connectionId);
      const activeRun = runs.find((run) => ["queued", "running", "cancel_requested"].includes(run.status));
      const state = await getInboundSyncState(connectionId, provider);
      if (!activeRun && !["queued", "running", "cancel_requested"].includes(state.status)) return;
      const result = await pollInboundSync({
        queuedAt: activeRun?.created_at ?? state.updated_at,
        getRuns: () => getInboundSyncRuns(connectionId),
        getState: () => getInboundSyncState(connectionId, provider),
        onProgress: (message) => setSyncFeedback((current) => ({
          ...current,
          [connectionId]: { kind: "progress", message },
        })),
      });
      setSyncResults((current) => ({ ...current, [connectionId]: result }));
      setSyncFeedback((current) => ({
        ...current,
        [connectionId]: {
          kind: syncRunKind(result),
          message: syncRunMessage(result),
        },
      }));
      await refresh();
    } catch (error) {
      const message = error instanceof Error ? error.message : "Gmail sync could not be resumed.";
      setSyncFeedback((current) => ({
        ...current,
        [connectionId]: { kind: "error", message: `Sync failed: ${message}` },
      }));
    } finally {
      syncWatchers.current.delete(connectionId);
      setSyncCancelRequests((current) => ({ ...current, [connectionId]: false }));
      setBusy(null);
      setSyncingProvider(null);
    }
  }

  async function cancelGmailSync(connectionId: string) {
    if (syncCancelRequests[connectionId]) return;
    setSyncCancelRequests((current) => ({ ...current, [connectionId]: true }));
    setSyncFeedback((current) => ({
      ...current,
      [connectionId]: {
        kind: "progress",
        message: "Requesting cancellation…",
      },
    }));
    try {
      const result = await cancelInboundSync(connectionId);
      if (result.status === "not_running") {
        setSyncCancelRequests((current) => ({ ...current, [connectionId]: false }));
        setSyncFeedback((current) => ({
          ...current,
          [connectionId]: {
            kind: "progress",
            message: "The sync has just finished. Refreshing its result…",
          },
        }));
        await refresh();
        return;
      }
      setSyncFeedback((current) => ({
        ...current,
        [connectionId]: { kind: "progress", message: result.message },
      }));
    } catch (error) {
      const message = error instanceof Error ? error.message : "Cancellation could not be requested.";
      setSyncCancelRequests((current) => ({ ...current, [connectionId]: false }));
      setSyncFeedback((current) => ({
        ...current,
        [connectionId]: {
          kind: "progress",
          message: `${message} The Gmail sync may still be running.`,
        },
      }));
    }
  }

  useEffect(() => {
    if (!canView) return;
    for (const card of cards) {
      if (card.provider !== "gmail") continue;
      const connections = card.connections.length ? card.connections : card.connection ? [card.connection] : [];
      for (const connection of connections) {
        const state = syncStates[connection.id];
        if (
          connection.is_active
          && state
          && ["running", "queued", "cancel_requested"].includes(state.status)
          && !syncWatchers.current.has(connection.id)
        ) {
          void resumeInboundSync(connection.id, card.provider);
        }
      }
    }
  }, [canView, cards, syncStates]);

  async function loadSlackChannelsForRouting(connectionId: string) {
    try {
      const channels = await listSlackChannels(connectionId);
      setSlackChannels((current) => ({ ...current, [connectionId]: channels }));
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Slack channels could not be loaded.");
    }
  }

  function connectOAuth(provider: string, intent?: "connect" | "reconnect") {
    if (!canConnect || (adminOnlyProviders.has(provider) && !canManage) || (provider === "slack" && !canManage) || busy) return;
    const connection = cards.find((card) => card.provider === provider)?.connection;
    oauthIntent.current = intent ?? (connection && connection.status !== "disconnected" ? "reconnect" : "connect");
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
    if (adminOnlyProviders.has(provider) && !canManage) {
      const message = "Only workspace admins can connect social integrations.";
      setActionErrors((current) => ({ ...current, [provider]: message }));
      setNotice(message);
      return;
    }
    if (provider === "slack" && !canManage) {
      const message = "Integration management permission is required to connect Slack.";
      setActionErrors((current) => ({ ...current, [provider]: message }));
      setNotice(message);
      return;
    }
    const popup = window.open(
      "about:blank",
      "_blank",
      "popup,width=620,height=760,resizable=yes,scrollbars=yes",
    );
    if (!popup) {
      const message = "The sign-in window was blocked. Allow pop-ups for this site, then try again.";
      setActionErrors((current) => ({ ...current, [provider]: message }));
      setNotice(message);
      setOperationFeedback((current) => ({ ...current, [provider]: { state: "error", message } }));
      return;
    }
    popup.name = `corral-oauth-${provider}-${Date.now()}`;
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
    const attempt = {
      provider,
      popup,
      pollId: 0,
      timeoutId: 0,
    };
    oauthPopupRef.current = attempt;
    attempt.pollId = window.setInterval(() => {
      if (popup.closed) {
        finishClosedOAuthPopup(
          provider,
          `${cards.find((card) => card.provider === provider)?.display_name ?? "Provider"} sign-in window closed before completion. Reconnect to try again.`,
        );
      }
    }, 400);
    attempt.timeoutId = window.setTimeout(() => {
      finishClosedOAuthPopup(
        provider,
        `${cards.find((card) => card.provider === provider)?.display_name ?? "Provider"} sign-in timed out. Reconnect to try again.`,
      );
    }, 5 * 60 * 1000);
    try {
      const started = await beginIntegration(provider);
      if (popup.closed) {
        finishClosedOAuthPopup(
          provider,
          `${cards.find((card) => card.provider === provider)?.display_name ?? "Provider"} sign-in window closed before completion. Reconnect to try again.`,
        );
        return;
      }
      popup.location.href = apiUrl(started.authorization_url);
    } catch (actionError) {
      const message = oauthConnectFailureMessage(
        provider,
        actionError instanceof Error ? actionError.message : "Authorization could not begin",
      );
      finishOAuthPopup(provider, "failed", message);
    }
  }

  const categorySections = [
    {
      id: "email",
      eyebrow: "Email",
      title: "Email Integrations",
      description: "Connect inboxes to receive and route workspace email leads.",
    },
    {
      id: "crm",
      eyebrow: "CRM",
      title: "CRM Integrations",
      description: "Connect your CRM to keep lead and contact records in sync.",
    },
    {
      id: "ai",
      eyebrow: "AI analysis",
      title: "AI connection & analysis",
      description: "Choose one AI provider for qualification and structured lead analysis.",
    },
    {
      id: "calling",
      eyebrow: "Calling & voice agents",
      title: "Dialers & Voice Integrations",
      description: "Connect a calling account, then start confirmed calls and read recent call activity from a lead.",
    },
    {
      id: "calendar",
      eyebrow: "Calendar",
      title: "Calendar Integrations",
      description: "Connect calendars to coordinate meetings and follow-ups.",
    },
    {
      id: "notifications",
      eyebrow: "Notifications",
      title: "Notification Integrations",
      description: "Connect notification tools for controlled workspace updates.",
    },
    {
      id: "social",
      eyebrow: "Social & lead sources",
      title: "Social Integrations",
      description: "Bring approved leads from social and marketplace channels into the same workspace view.",
    },
    {
      id: "other",
      eyebrow: "Other tools",
      title: "Other Integrations",
      description: "Connect additional tools that support your lead operations.",
    },
  ];
  const sections = [
    ...categorySections.map((section) => ({
      ...section,
      providers: cards
        .filter((card) => card.availability === "available" && integrationCategory(card) === section.id)
        .map((card) => card.provider),
    })),
    {
      id: "coming-soon",
      eyebrow: "Release roadmap",
      title: "Coming Soon",
      description: "Visible for planning only. Connection and setup actions remain unavailable.",
      providers: cards.filter((card) => card.availability === "future").map((card) => card.provider),
    },
  ];
  const cardByProvider = new Map(cards.map((card) => [card.provider, card]));
  const categoryCounts = cards.filter((card) => card.availability === "available").reduce<Record<string, number>>((counts, card) => {
    const category = integrationCategory(card);
    counts[category] = (counts[category] ?? 0) + 1;
    return counts;
  }, {});
  const displayedSections = sections.filter((section) => (!selectedProvider || section.providers.includes(selectedProvider)) && (!callingSection || section.id === "calling") && (!aiSection || section.id === "ai"))
    .map((section) => ({
      ...section,
      providers: section.id === "ai"
        ? [selectedProvider && section.providers.includes(selectedProvider) ? selectedProvider : selectedAIProvider].filter((provider) => section.providers.includes(provider))
        : section.providers.filter((provider) => {
          if (selectedProvider && provider !== selectedProvider) return false;
          if (selectedProvider || callingSection || activeCategory === "all") return true;
          const card = cardByProvider.get(provider);
          return card ? integrationCategory(card) === activeCategory : false;
        }),
    }))
    .filter((section) => section.providers.length > 0);

  const focusedLabel = selectedProvider ? cardByProvider.get(selectedProvider)?.display_name || selectedProvider : aiSection ? "AI connection" : callingSection ? "All dialers" : "";
  const activeGmailSyncs = cards
    .filter((card) => card.provider === "gmail")
    .flatMap((card) => (card.connections.length ? card.connections : card.connection ? [card.connection] : [])
      .map((connection) => ({
        connection,
        state: syncStates[connection.id],
        feedback: syncFeedback[connection.id],
      })))
    .filter(({ state, feedback }) =>
      state?.status === "running"
      || state?.status === "queued"
      || state?.status === "cancel_requested"
      || (syncingProvider === "gmail" && feedback?.kind === "progress"),
    );
  const providerCards = cards.filter((card) => card.availability === "available");
  const connectedCount = providerCards.filter((card) => connectionTone(card.connection?.status) === "ok").length;
  const attentionCount = providerCards.filter((card) => connectionTone(card.connection?.status) === "error").length;
  const comingSoonCards = cards.filter((card) => card.availability === "future");
  return <div className={`page-view operational-page integrations-page${selectedProvider || aiSection || callingSection ? " focused-page" : ""}`} data-integration-page={selectedProvider || (aiSection ? "ai" : callingSection ? "calling" : "overview")}>
    <header className="v-pagehead" data-tour="integrations">
      <div className="v-pagehead-text">
        <h1>{focusedLabel || "Integrations"}</h1>
        <p>{focusedLabel ? "Connection, setup and settings" : `${connectedCount} connected${attentionCount ? `, ${attentionCount} need${attentionCount === 1 ? "s" : ""} attention` : ""}`}</p>
      </div>
      <div className="v-pagehead-actions"><FeatureGuide topic="integrations" /></div>
    </header>
    {activeGmailSyncs.map(({ connection, state, feedback }) => <section className="gmail-sync-active-banner" key={connection.id} role="status" aria-live="polite">
      <span className="gmail-sync-active-indicator" aria-hidden="true" />
      <div><span className="eyebrow">Gmail · AI review active</span><strong>{state?.status === "cancel_requested" ? "Stopping Gmail sync" : "Sync is still running"}</strong><p>{feedback?.message || "Checking recent messages and matching them to existing leads…"}</p></div>
      <span className="gmail-sync-active-state">{state?.status === "cancel_requested" ? "Stopping" : "In progress"}</span>
    </section>)}
    {!selectedProvider && !aiSection && !callingSection && cards.length > 0 && <nav className="int-categories" aria-label="Integration categories">
      {integrationCategoryOptions.filter(([id]) => id === "all" || categoryCounts[id] > 0).map(([id, label]) => (
        <button type="button" key={id} aria-pressed={activeCategory === id} onClick={() => setActiveCategory(id)}>
          {id === "all" ? "All" : label}
          <span className="num">{id === "all" ? providerCards.length : categoryCounts[id]}</span>
        </button>
      ))}
    </nav>}
    {notice && (() => {
      const noticeState = Object.values(operationFeedback).find((item) => item.message === notice)?.state;
      return <div className={`notice${noticeState ? ` notice-${noticeState}` : ""}`} role="status">{notice}</div>;
    })()}
    {!canView ? <div className="team-denied panel"><h2>Integrations are managed by your admins</h2><p>Ask a workspace admin for access if you need to connect or change an integration.</p></div> : <div className="integration-sections">{displayedSections.filter((section) => section.id !== "coming-soon").map((section) => <section className={`integration-section integration-section-${section.id}`} key={section.id} aria-labelledby={`integration-section-${section.id}`}>
      <div className="integration-section-heading"><h2 id={`integration-section-${section.id}`}>{section.title.replace(/ Integrations$/, "")}</h2></div>
      <div className={`integration-grid${section.id === "ai" ? " integration-grid-ai" : ""}`}>{section.providers.map((provider) => cardByProvider.get(provider)).filter((card): card is IntegrationCard => Boolean(card)).map((card) => {
       const connection = card.connection;
       const status = connection?.status ?? "not_connected";
       const comingSoon = section.id === "coming-soon" || card.availability === "future";
       const openAI = card.provider === "openai";
        const cloudTalk = card.provider === "cloudtalk";
        const embeddedDialer = isEmbeddedDialerConnection({ provider: card.provider });
        const canOpenPhone = Boolean(
          embeddedDialer &&
            canCallDialer &&
            connection?.is_active &&
            connection.status === "verified",
        );
        const requiredManualFields = cloudTalk
          ? ["access_key_id", "access_key_secret"]
          : card.manual_fields;
        const reasoningProvider = isReasoningProvider(card.provider);
       const statusLabel = comingSoon ? "Coming soon" : cloudTalk && connection?.status === "connected" && !connection.metadata.cloudtalk_agent_id
         ? "Select an agent"
         : connectionStatusLabel(connection?.status);
      const providerCredentials = credentials[card.provider] ?? {};
       const open = expandedProviders.has(card.provider);
      const syncState = connection ? syncStates[connection.id] : null;
      const crmDirection = card.category === "CRM";
      const gmailDirection = card.provider === "gmail";
      const outlookDirection = card.provider === "outlook";
       const googleCalendarDirection = card.provider === "google_calendar";
       const calendlyDirection = card.provider === "calendly";
       const metaWebhookSubscription = card.provider === "instagram"
         ? "Instagram object · messages"
         : card.provider === "messenger"
           ? "Page object · messages"
           : card.provider === "whatsapp_business"
             ? "WhatsApp Business Account object · messages"
             : null;
      const mailDirection = gmailDirection || outlookDirection;
        const confirmationSource = card.directionality.automatic_inbound
          ? leadConfirmationSources?.sources.find(
              (source) => source.source === card.provider,
            )
          : undefined;
       const canConnectProvider = reasoningProvider
        ? canManage
        : canConnect && (!adminOnlyProviders.has(card.provider) || canManage) && (card.provider !== "slack" || canManage);
       const slackConnections = card.provider === "slack"
         ? (card.connections.length ? card.connections : connection ? [connection] : [])
           .filter((item) => item.is_active && item.status === "verified" && item.credential_state === "configured")
         : [];
        const gmailConnections = gmailDirection
          ? (card.connections.length ? card.connections : connection ? [connection] : [])
              .filter((item) => item.status !== "disconnected")
          : [];
       const selectedReasoningModel = modelSelections[card.provider] ?? connection?.selected_model ?? card.default_model ?? card.supported_models[0] ?? "";
       const selectedOpenAIModel = selectedReasoningModel;
        const detailsOpen = card.provider === "slack" ? slackPanelOpen : open;
        const hasExpandableSettings = Boolean(
          openAI ||
          section.id === "ai" ||
          (card.directionality.automatic_inbound &&
            !comingSoon &&
            canManage &&
            (confirmationSource || leadConfirmationSourcesLoading || leadConfirmationSourceError)) ||
          canOpenPhone ||
          (card.provider === "calendly" && accountChoiceProvider === card.provider) ||
          (cloudTalk && connection && canManage) ||
          (reasoningProvider && connection && canManage) ||
          (!comingSoon && (crmDirection || mailDirection) && connection?.status === "verified"),
        );
      const oauthConnected =
        (card.provider === "instagram" || card.provider === "messenger") &&
        connection &&
        ["connected", "verified"].includes(connection.status);
         return <article data-navigation-section={section.id === "ai" ? "ai" : card.provider} tabIndex={-1} className={`integration-card int-card provider-${card.provider}${section.id === "ai" ? " ai-connection-card" : ""} state-${comingSoon ? "coming-soon" : status}${card.provider === "slack" || !open ? " is-collapsed" : " is-expanded"}`} key={card.provider}>
           <div className="int-head">
             <ProviderMark provider={section.id === "ai" ? card.provider : card.provider} name={section.id === "ai" ? card.display_name : card.display_name} />
             <div className="int-id">
               <h2>{card.display_name}</h2>
               <p>{section.id === "ai" ? "Scores and summarises leads with your own provider key" : connection?.account_name && connection.status !== "disconnected" ? connection.account_name : card.directionality.description || card.category}</p>
             </div>
             <span className="int-status" data-tone={comingSoon ? "idle" : cloudTalk && connection?.status === "connected" && !connection.metadata.cloudtalk_agent_id ? "pending" : connectionTone(connection?.status)}><i aria-hidden="true" />{statusLabel}</span>
             <button type="button" className="v-btn int-toggle" data-kind={connectionTone(connection?.status) === "ok" ? "gray" : connectionTone(connection?.status) === "error" ? "destructive" : "tinted"} data-size="md" aria-expanded={detailsOpen} aria-haspopup={card.provider === "slack" ? "dialog" : undefined} aria-controls={`integration-details-${card.provider}`} onClick={() => card.provider === "slack" ? setSlackPanelOpen((current) => !current) : toggleProviderExpanded(card.provider)}>
               <span className="v-btn-label">{detailsOpen ? <><Icon name="close" size={14} /><span className="v-sr">Close</span></> : connectionTone(connection?.status) === "ok" ? "Manage" : connectionTone(connection?.status) === "error" ? "Fix" : connection && connection.status !== "disconnected" ? "Continue setup" : "Connect"}</span>
             </button>
           </div>
          <ProviderDetailsPanel
            provider={card.provider}
            open={detailsOpen}
            onClose={() => card.provider === "slack" ? setSlackPanelOpen(false) : toggleProviderExpanded(card.provider)}
            id={`integration-details-${card.provider}`}
            title={section.id === "ai" ? `AI analysis · ${card.display_name}` : card.display_name}
            subtitle={<span className="int-status" data-tone={comingSoon ? "idle" : connectionTone(connection?.status)}><i aria-hidden="true" />{statusLabel}{connection?.account_name && connection.status !== "disconnected" ? ` · ${connection.account_name}` : ""}</span>}
          >
         {section.id === "ai" && <label className="ai-provider-picker ai-provider-picker-details"><span>Provider</span><select value={card.provider} onChange={(event) => { const provider = event.target.value; setSelectedAIProvider(provider); setExpandedProviders((current) => new Set(current).add(provider)); }} aria-label="AI analysis provider">{cards.filter((item) => integrationCategory(item) === "ai").map((item) => <option value={item.provider} key={item.provider}>{item.display_name}</option>)}</select></label>}
         {connection?.status === "connection_failed" && typeof connection.last_validation_result.message === "string" && connection.last_validation_result.message !== operationFeedback[card.provider]?.message && connection.last_validation_result.message !== actionErrors[card.provider] && <div className="notice" role="alert">{connection.last_validation_result.message}</div>}
          {gmailDirection && gmailConnections.length > 0 && <GmailAccountsPanel
            accounts={gmailConnections}
            canConnect={canConnectProvider}
            canDisconnect={canDisconnect}
            busy={Boolean(busy)}
            busyAccountId={busyTargetConnectionId}
            connecting={busy === "gmail" && busyAction === "connect"}
            onAdd={() => void connectOAuth("gmail", "connect")}
            onDisconnect={(gmailAccount) => void act("gmail", () => disconnectIntegration(gmailAccount.id), `${gmailAccount.account_name} disconnected successfully.`, "disconnect", gmailAccount.id)}
            statusLabel={connectionStatusLabel}
          />}
         {(comingSoon || gmailDirection || crmDirection || card.provider === "slack" || openAI) && <p className="integration-direction"><strong>{comingSoon ? card.credential_help : gmailDirection ? "Automatic inbound email acquisition" : crmDirection ? "Automatic contact import · Manual contact writeback" : card.provider === "slack" ? "Application › Slack · outbound notifications only" : "Workspace OpenAI project › structured lead analysis"}</strong>{crmDirection && " — CRM records enter only after inbound sync is enabled; sending contact data back always requires an explicit action."}</p>}
         {comingSoon && <div className="coming-soon-provider" role="status"><b>Coming soon</b><span>This provider is not available for connection in the current release.</span></div>}
         {card.provider === "slack" && <>
             <SlackSetupGuide open={slackGuideOpen} onOpenChange={setSlackGuideOpen} />
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
        {!comingSoon && connection && connection.status !== "disconnected" && <section className="int-health" aria-label="Connection health">
          <dl className="int-facts">
            <div><dt>Credential</dt><dd>{(connection.credential_state ?? "missing").replaceAll("_", " ")}</dd></div>
            <div><dt>Verification</dt><dd>{(connection.verification_status ?? "not verified").replaceAll("_", " ")}</dd></div>
            <div><dt>Last checked</dt><dd>{connection.last_validation_at ? new Date(connection.last_validation_at).toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }) : "Never"}</dd></div>
            {openAI && <div><dt>Model</dt><dd>{connection.selected_model ?? "Not configured"}</dd></div>}
          </dl>
        </section>}
          {!comingSoon && (card.provider !== "slack" || canConnectProvider || canManage || canDisconnect) && <div className="card-actions integration-connection-actions">
            {!connection || connection.status === "disconnected" ? canConnectProvider && (card.supported_auth.includes("oauth2") || card.manual_fields.length === 0 || !canManage) && <button onClick={() => card.supported_auth.includes("oauth2") ? void connectOAuth(card.provider) : showCredentialSetup(card.provider)} disabled={Boolean(busy)}>{busy === card.provider && busyAction === "connect" ? "Connecting…" : gmailDirection ? "Connect Gmail" : outlookDirection ? "Connect Outlook" : googleCalendarDirection ? "Connect Google Calendar" : calendlyDirection ? "Connect Calendly" : openAI ? "Connect OpenAI" : "Connect"}</button> : <>{canManage && <><button onClick={() => void act(card.provider, async () => { const result = await testIntegration(connection.id); if (!result.verified) throw new Error(result.message); }, `${card.display_name} connection verified.`)} disabled={Boolean(busy) || (cloudTalk && !connection.metadata.cloudtalk_agent_id)}>{openAI ? "Test Connection" : "Verify connection"}</button><button className="button-quiet" onClick={() => card.supported_auth.includes("oauth2") ? void connectOAuth(card.provider) : showCredentialSetup(card.provider)} disabled={Boolean(busy)}>{busy === card.provider && busyAction === "reconnect" ? "Reconnecting…" : openAI ? "Replace Key" : "Reconnect"}</button></>}{canDisconnect && <button className="button-quiet danger" onClick={() => void act(card.provider, () => disconnectIntegration(connection.id), `${card.display_name} disconnected successfully.`, "disconnect")} disabled={Boolean(busy)}>{busy === card.provider && busyAction === "disconnect" ? "Disconnecting…" : "Disconnect"}</button>}</>}
            {section.id === "ai" && connection?.status === "verified" && connection.is_active && canManage && <button className="button-quiet" onClick={() => void act(card.provider, () => runReasoningBackfill(connection.id, card.provider), (result) => result && typeof result === "object" && "message" in result ? String(result.message) : "AI re-analysis finished.")} disabled={Boolean(busy)}>Re-analyze leads</button>}
            {card.provider === "salesforce" && <button className="int-tertiary" onClick={() => setSalesforceSetupOpen(true)}>Prepare org</button>}
          </div>}
         {hasExpandableSettings && <IntegrationDisclosure
           title={openAI || section.id === "ai" ? "Usage and settings" : crmDirection || mailDirection ? "Sync and settings" : "More settings"}
           className={`integration-settings-disclosure${connection && connection.status !== "disconnected" ? " is-live" : ""}`}
         >
         {openAI && <OpenAIUsagePanel />}
         {section.id === "ai" && !openAI && <AiUsagePanel providerName={card.display_name} />}
          {card.directionality.automatic_inbound && !comingSoon && canManage && (
            leadConfirmationSourceError ? (
              <p className="lead-confirmation-error" role="alert">
                {card.display_name} confirmation settings could not be loaded: {leadConfirmationSourceError}
              </p>
            ) : confirmationSource ? (
              <section
                className="integration-guide lead-confirmation-setting"
                aria-label={`${card.display_name} confirmation email settings`}
              >
                <div className="lead-confirmation-setting-copy">
                  <span className="eyebrow">New-lead email</span>
                  <strong>Automatic confirmation</strong>
                  <p>
                    Control confirmation emails only. New leads still follow the
                    normal qualification and review rules.
                  </p>
                  {confirmationSource.uses_default && (
                    <small>
                      Using workspace default ({leadConfirmationSources?.default_enabled ? "On" : "Off"}).
                    </small>
                  )}
                </div>
                <div className="lead-confirmation-setting-actions">
                  {!confirmationSource.uses_default && (
                    <button
                      className="button-quiet"
                      type="button"
                      disabled={leadConfirmationSourceBusy !== null}
                      onClick={() =>
                        void updateConfirmationSource(card.provider, null)
                      }
                    >
                      Use default
                    </button>
                  )}
                  <button
                    type="button"
                    className={`email-automation-toggle${confirmationSource.enabled ? " is-on" : ""}`}
                    role="switch"
                    aria-checked={confirmationSource.enabled}
                    aria-label={`Automatic confirmation emails for ${card.display_name}`}
                    disabled={leadConfirmationSourceBusy !== null}
                    onClick={() =>
                      void updateConfirmationSource(
                        card.provider,
                        !confirmationSource.enabled,
                      )
                    }
                  >
                    <span className="email-automation-track" aria-hidden="true">
                      <i />
                    </span>
                    <span>
                      {leadConfirmationSourceBusy === card.provider
                        ? "Saving…"
                        : confirmationSource.enabled
                          ? "On"
                          : "Off"}
                    </span>
                  </button>
                </div>
              </section>
            ) : leadConfirmationSourcesLoading ? (
              <p className="lead-confirmation-loading">Loading confirmation setting…</p>
            ) : null
          )}
         {canOpenPhone && <div className="integration-guide phone-session-setup">
           <span className="eyebrow">Phone sign-in for this browser</span>
           <strong>Use your own {card.display_name} account</strong>
           <p>
             Corral’s integration connection authorizes workspace API actions.
             Open the phone and sign in with your provider account or organization
             SSO. The floating phone stays mounted as you move around Corral; the
             provider may ask you to sign in again after its session expires.
           </p>
           {card.provider === "ringcentral" && !ringCentralEmbedClientId && (
             <p role="note">
               RingCentral’s live phone also needs a registered Embeddable app
               client ID. The dock will show its setup instructions until that is
               configured.
             </p>
           )}
           <div className="card-actions">
             <button
               type="button"
               className="button-quiet"
               onClick={() =>
                 window.dispatchEvent(
                   new CustomEvent(OPEN_DIALER_PHONE_EVENT, {
                     detail: { connectionId: connection?.id },
                   }),
                 )
               }
             >
               Open {card.display_name} phone
             </button>
           </div>
         </div>}
        {card.provider === "calendly" && accountChoiceProvider === card.provider && <IntegrationAccountChoice
          key={card.provider} provider={card.provider} accountName={connection?.account_name}
          busy={busy === card.provider}
          onContinue={() => void authorizeOAuth(card.provider)}
          onCancel={() => setAccountChoiceProvider(null)}
        />}
         {cloudTalk && connection && canManage && <div className="integration-guide cloudtalk-agent-picker">
           <span className="eyebrow">CloudTalk calling agent</span>
           <p>Store the Access Key ID and Secret first. Loading this list only reads agent names and IDs; it never places a call.</p>
           <div className="card-actions">
             <button type="button" className="button-quiet" disabled={Boolean(busy)} onClick={() => void act(card.provider, async () => {
               const result = await listCloudTalkAgents(connection.id);
               setCloudTalkAgents((current) => ({ ...current, [connection.id]: result.agents }));
               if (result.selected_agent_id) {
                 setCloudTalkAgentSelections((current) => ({ ...current, [connection.id]: result.selected_agent_id! }));
               }
             }, "CloudTalk agents loaded.")}>{busy === card.provider ? "Loading agents…" : "Load agents"}</button>
             {cloudTalkAgents[connection.id] && <>
               <label>Agent<select value={cloudTalkAgentSelections[connection.id] ?? ""} onChange={(event) => setCloudTalkAgentSelections((current) => ({ ...current, [connection.id]: event.target.value }))}>
                 <option value="">Choose a CloudTalk agent</option>
                 {cloudTalkAgents[connection.id].map((agent) => <option key={agent.id} value={agent.id}>{agent.name} · ID {agent.id}</option>)}
               </select></label>
               <button type="button" disabled={Boolean(busy) || !cloudTalkAgentSelections[connection.id]} onClick={() => void act(card.provider, async () => {
                 await selectCloudTalkAgent(connection.id, cloudTalkAgentSelections[connection.id]);
               }, "CloudTalk agent saved. Verify the connection before placing calls.")}>Save agent</button>
             </>}
            </div>
           {typeof connection.metadata.cloudtalk_agent_name === "string" && <small>Selected: {connection.metadata.cloudtalk_agent_name} · Verify the connection to enable calling.</small>}
         </div>}
         {reasoningProvider && connection && canManage && <div className="integration-guide reasoning-model-config"><span className="eyebrow">Model configuration</span><label>Allowed model<select value={selectedReasoningModel} onChange={(event) => setModelSelections((current) => ({ ...current, [card.provider]: event.target.value }))}>{card.supported_models.map((model) => <option value={model} key={model}>{model}</option>)}</select></label><button className="button-quiet" onClick={() => void act(card.provider, () => updateReasoningModel(connection.id, selectedReasoningModel), `${card.display_name} model saved. Test the connection before use.`)} disabled={busy === card.provider || !selectedReasoningModel}>Save model</button></div>}
        {!comingSoon && (crmDirection || mailDirection) && connection?.status === "verified" && <div className="integration-guide">
          <span className="eyebrow">{mailDirection ? "Automatic inbound email acquisition" : "Inbound CRM synchronization"}</span>
          <strong>{syncState?.enabled ? "Enabled · automatic inbound" : "Disabled"}</strong>
          {syncState?.enabled && <p>Automatically checks every {syncState.sync_interval_seconds} seconds, even when Corral is closed. New records appear without pressing Sync now.</p>}
          <p>{syncState?.last_successful_sync_at ? `Last synced ${new Date(syncState.last_successful_sync_at).toLocaleString()} · ${syncState.records_processed.toLocaleString()} records processed.` : "No provider records have been imported yet."}</p>
          {mailDirection && <p>{syncState?.next_sync_at ? `Next sync ${new Date(syncState.next_sync_at).toLocaleString()}.` : `Status: ${syncState?.status ?? "not configured"}.`}{syncState?.status && ` Latest result: ${syncState.status.replaceAll("_", " ")}.`}</p>}
          {syncState?.last_error && <small>{syncState.error_code || "Sync failed"}: {syncState.last_error}</small>}
           {(canInboundSync || canManage) && <div className={`card-actions${card.provider === "gmail" ? " gmail-sync-actions" : ""}`}>
            {canManage && <button onClick={() => void act(card.provider, () => configureInboundSync(connection.id, !syncState?.enabled, card.provider), `${mailDirection ? "Automatic inbound" : "Inbound sync"} ${syncState?.enabled ? "disabled" : "enabled"}.`)} disabled={busy === card.provider}>{syncState?.enabled ? (mailDirection ? "Disable automatic inbound" : "Disable inbound sync") : (mailDirection ? "Enable automatic inbound" : "Enable inbound sync")}</button>}
            {syncState?.enabled && card.provider !== "gmail" && <button className="button-quiet" onClick={() => void syncInbound(connection.id, card.provider, syncState.initial_import_completed ? "manual" : "initial")} disabled={busy === card.provider}>{syncingProvider === card.provider ? "Syncing…" : "Sync now"}</button>}
            {syncState && card.provider === "gmail" && <div className="gmail-sync-controls">
                <label htmlFor={`review-limit-${connection.id}`}>Messages to scan
                  <input id={`review-limit-${connection.id}`} type="number" min="1" max="5000" step="1" value={reviewLimits[connection.id] ?? "100"} disabled={busy === card.provider} onChange={(event) => setReviewLimits((current) => ({ ...current, [connection.id]: event.target.value }))} />
                </label>
                <p>Recent email scans the last 30 days; previous email scans older messages. Both use AI and the usual lead review rules. Choose up to 5,000 messages per batch; run again to continue further back.</p>
                <div className="gmail-sync-buttons">
                  <button className="primary-button gmail-sync-button" onClick={() => void syncInbound(connection.id, card.provider, "review_recent")} disabled={!syncState.enabled || busy === card.provider || !Number.isInteger(Number(reviewLimits[connection.id] ?? "100")) || Number(reviewLimits[connection.id] ?? "100") < 1 || Number(reviewLimits[connection.id] ?? "100") > 5000}>
                    Sync recent emails
                  </button>
                  <button className="button-quiet gmail-sync-button" title="Scans Gmail messages older than 30 days" onClick={() => void syncInbound(connection.id, card.provider, "review_previous")} disabled={!syncState.enabled || busy === card.provider || !Number.isInteger(Number(reviewLimits[connection.id] ?? "100")) || Number(reviewLimits[connection.id] ?? "100") < 1 || Number(reviewLimits[connection.id] ?? "100") > 5000}>
                    Sync previous emails
                  </button>
                  {syncingProvider === card.provider && syncFeedback[connection.id]?.kind === "progress" && <button className="button-quiet gmail-sync-cancel-button" type="button" onClick={() => void cancelGmailSync(connection.id)} disabled={syncCancelRequests[connection.id]} aria-label="Cancel Gmail AI sync">
                    {syncCancelRequests[connection.id] ? "Stopping…" : "Cancel sync"}
                  </button>}
                </div>
            </div>}
            {syncFeedback[connection.id] && (syncFeedback[connection.id].kind === "progress"
              ? <div className="gmail-sync-progress" role="status" aria-live="polite"><span className="gmail-sync-spinner" aria-hidden="true" /><span>{syncFeedback[connection.id].message}</span></div>
              : syncResults[connection.id]
                ? (() => {
                  const result = syncResults[connection.id]!;
                  const review = parseGmailReviewNotes(result.last_error);
                  const stats = [
                    ["Scanned", result.records_fetched],
                    ["New leads", result.records_created],
                    ["Updated", result.records_updated],
                    ["Already known", result.records_unchanged],
                    ["Skipped", result.records_skipped],
                    ["Failed", result.records_failed],
                  ];
                  return <section className={`gmail-sync-result sync-result-${syncFeedback[connection.id].kind}`} role={syncFeedback[connection.id].kind === "error" ? "alert" : "status"} aria-live="polite">
                    <header><div><span className={`gmail-sync-result-badge sync-result-badge-${syncFeedback[connection.id].kind}`}>{result.status === "cancelled" ? "Cancelled" : result.status === "completed" ? "Complete" : result.status === "completed_bounded" ? "Batch complete" : result.status === "completed_with_errors" ? "Needs attention" : "Failed"}</span><strong>{syncFeedback[connection.id].message}</strong></div><time>{new Date(result.created_at).toLocaleString()}</time></header>
                    <div className="gmail-sync-result-stats">{stats.map(([label, value]) => <div key={label}><strong>{value}</strong><span>{label}</span></div>)}</div>
                    {review.general && <p className="gmail-sync-result-summary">{review.general}</p>}
                    {review.notes.length > 0 && <details className="gmail-sync-review-details">
                      <summary>AI review notes <span>{review.notes.length}</span></summary>
                      <ul>{review.notes.map((note, index) => <li key={`${note.outcome}-${index}`} className={`gmail-review-note gmail-review-note-${note.outcome}`}>
                        <span>{note.outcome === "selected" ? "Lead" : "Skipped"}</span>
                        <div><strong>{note.title}</strong>{note.detail && <p>{note.detail}</p>}</div>
                      </li>)}</ul>
                    </details>}
                    {!review.notes.length && !review.general && result.last_error && <details className="gmail-sync-review-details"><summary>Run details</summary><pre>{result.last_error}</pre></details>}
                  </section>;
                })()
                : <div className={`notice integration-sync-feedback sync-feedback-${syncFeedback[connection.id].kind}`} role="alert" aria-live="polite">{syncFeedback[connection.id].message}</div>)}
          </div>}
         </div>}
         </IntegrationDisclosure>}
           {!comingSoon && (open || card.provider === "slack") && <IntegrationDisclosure title="Connection briefing and credentials" className={`connection-briefing-disclosure${card.manual_fields.length > 0 && canManage && !oauthConnected ? " has-form" : ""}`} open={!connection || !["connected", "verified"].includes(connection.status)}>
           <div className="integration-guide" id={`connection-briefing-${card.provider}`}>
          <strong>{card.dashboard_path}</strong>
          <p>{card.permission_reason}</p>
          {card.provider === "slack" && <p>New connections also request <code>channels:join</code> to join a public channel you select, and <code>groups:read</code> to list private channels after the app is invited. Corral does not read channel messages or join channels until you choose one.</p>}
           {reasoningProvider && <>
             <p>Create a dedicated API key for this workspace in the {card.display_name} console. Restrict its permissions where possible, configure provider-side budgets and limits, and rotate it periodically.</p>
             <p><strong>{card.display_name} API usage is billed directly to your provider account.</strong></p>
          </>}
          <div className="scope-list">{card.required_scopes.map((scope) => <code key={scope}>{scope}</code>)}</div>
          <a href={card.documentation_url} target="_blank" rel="noopener noreferrer">Official documentation<Icon name="external" size={13} /></a>
          {card.manual_fields.length > 0 && canManage && !oauthConnected && <div className="manual-credential">
            <span>Secure credential setup</span>
            {!openAI && <label>Account label<input placeholder="Account name" value={accountNames[card.provider] ?? ""} onChange={(event) => setAccountNames((current) => ({ ...current, [card.provider]: event.target.value }))} /></label>}
            {openAI && !connection && <label>Model<select value={selectedOpenAIModel} onChange={(event) => setModelSelections((current) => ({ ...current, [card.provider]: event.target.value }))}>{card.supported_models.map((model) => <option value={model} key={model}>{model}</option>)}</select></label>}
            {card.manual_fields.filter((field) => !cloudTalk || field !== "agent_id").map((field) => {
              const secret = /token|secret|jwt|api_key/.test(field);
              const label = field === "bot_token" ? "Bot User OAuth Token (secure)" : field === "from_number" ? "Phone number (+country code)" : field.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
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
            <button disabled={Boolean(busy) || requiredManualFields.some((field) => !providerCredentials[field]?.trim())} onClick={() => void act(card.provider, async () => {
              if (connection) {
                await reconnectIntegration(connection.id, providerCredentials);
                if (cloudTalk) {
                  setCloudTalkAgents((current) => ({ ...current, [connection.id]: [] }));
                  setCloudTalkAgentSelections((current) => ({ ...current, [connection.id]: "" }));
                }
              } else {
                await addIntegrationCredential(card.provider, accountNames[card.provider] || (openAI ? "OpenAI project" : `${card.display_name} account`), card.preferred_auth === "api_key" ? "api_key" : "private_token", card.required_scopes, providerCredentials, openAI ? { selected_model: selectedOpenAIModel } : {});
              }
            }, `${card.display_name} credential stored securely; test it before use.`)}>{connection && openAI ? "Replace key" : connection ? "Replace credentials" : `Connect ${card.display_name}`}</button>
            <small>The key is sent only to the backend, encrypted by the configured secret service, cleared from this form after success, and never returned.</small>
            </div>}
            </div>
          </IntegrationDisclosure>}
        {operationFeedback[card.provider] && <div className={`notice notice-${operationFeedback[card.provider].state} integration-operation-feedback operation-${operationFeedback[card.provider].state}`} role={operationFeedback[card.provider].state === "error" ? "alert" : "status"} aria-live="polite" aria-busy={operationFeedback[card.provider].state === "running"}>{operationFeedback[card.provider].message}</div>}
        {actionErrors[card.provider] && actionErrors[card.provider] !== operationFeedback[card.provider]?.message && <div className="integration-action-error" role="alert">{actionErrors[card.provider]}</div>}
           <IntegrationDisclosure title="Setup guide and permissions" className="int-guidance">
             <IntegrationSetupGuide card={card} />
             {metaWebhookSubscription && <div className="integration-guide meta-webhook-guide">
               <span className="eyebrow">Meta inbound message setup</span>
               <p><strong>Callback path</strong><br /><code>/v1/webhooks/meta</code></p>
               <p><strong>Subscribe</strong><br />{metaWebhookSubscription}</p>
               <p>Use the configured <code>WEBHOOK_SIGNING_SECRET</code> as Meta’s verify token. POST signatures are checked with this provider’s existing Meta app secret.</p>
               <p>Connect the matching Instagram account, Facebook Page, or WhatsApp Business account. Events from an unknown or ambiguous account are rejected.</p>
             </div>}
             {!comingSoon && card.supported_auth.includes("oauth2") && <p className="account-selection-note">{gmailDirection ? "Connect more than one Gmail mailbox. Each account stays separate, and you choose which mailbox to read or send from in Corral." : "Choose your account or organization at provider sign-in. Only one account per provider remains active after verification."}</p>}
           </IntegrationDisclosure>
          </ProviderDetailsPanel>
       </article>;
    })}</div>
    </section>)}
      {!selectedProvider && !aiSection && !callingSection && comingSoonCards.length > 0 && activeCategory === "all" && <section className="int-coming-soon" aria-label="Coming soon">
        <h2>Coming soon</h2>
        <p>{comingSoonCards.map((card) => card.display_name).join(", ")}</p>
      </section>}
    </div>}
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
