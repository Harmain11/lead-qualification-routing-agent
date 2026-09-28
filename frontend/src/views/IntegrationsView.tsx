import { useEffect, useState } from "react";

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
  selectSlackChannel,
  sendSlackTestNotification,
  runInboundSync,
  testIntegration,
  updateSlackNotificationConfig,
  updateOpenAIModel,
} from "../api";
import { can } from "../permissions";
import type { CrmInboundSyncState, IntegrationCard, ProviderConnection, SlackNotificationConfig, SlackNotificationRule } from "../types";

const connectionStages = ["Disconnected", "Authorizing", "Credentials stored", "Verified"];

function connectionStage(status?: string) {
  if (status === "verified") return 3;
  if (["configured", "connected", "validating", "needs_attention", "connection_failed", "authorization_expired", "reconnect_required", "expired", "needs_reauthorization", "error"].includes(status ?? "")) return 2;
  if (["connecting", "awaiting_authorization"].includes(status ?? "")) return 1;
  return 0;
}

function commaList(values: string[]) {
  return values.join(", ");
}

function parseCommaList(value: string) {
  return value.split(",").map((item) => item.trim()).filter(Boolean);
}

function SlackRoutingEditor({
  connections,
  config,
  canManage,
  onSaved,
  onNotice,
}: {
  connections: ProviderConnection[];
  config: SlackNotificationConfig | null;
  canManage: boolean;
  onSaved: (next: SlackNotificationConfig) => void;
  onNotice: (message: string) => void;
}) {
  const verified = connections.filter((item) => item.status === "verified" && item.is_active);
  const [defaultConnectionId, setDefaultConnectionId] = useState<string | null>(null);
  const [rules, setRules] = useState<SlackNotificationRule[]>([]);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    setDefaultConnectionId(config?.default_connection_id ?? (verified.length === 1 ? verified[0].id : null));
    setRules(config?.rules ?? []);
  }, [config?.default_connection_id, config?.rules, verified.length]);

  if (!connections.length) return null;
  const save = async () => {
    setSaving(true);
    try {
      const next = await updateSlackNotificationConfig({ default_connection_id: defaultConnectionId, rules });
      onSaved(next);
      onNotice("Slack notification routing saved.");
    } catch (error) {
      onNotice(error instanceof Error ? error.message : "Slack notification routing could not be saved.");
    } finally { setSaving(false); }
  };
  const addRule = () => {
    const target = verified[0]?.id;
    if (!target) { onNotice("Connect and verify a Slack workspace before adding a rule."); return; }
    setRules((current) => [...current, { id: crypto.randomUUID(), name: `Rule ${current.length + 1}`, connection_id: target, queues: [], qualification_bands: [], sources: [], weight: 1 }]);
  };
  const updateRule = (id: string, patch: Partial<SlackNotificationRule>) => setRules((current) => current.map((rule) => rule.id === id ? { ...rule, ...patch } : rule));
  const sourceOptions = [
    ["", "Any lead"], ["salesforce", "Salesforce leads"], ["hubspot", "HubSpot leads"],
    ["gmail", "Gmail leads"], ["website", "Website leads"], ["email", "Email leads"],
  ];

  return <div className="integration-guide slack-routing-guide">
    <span className="eyebrow">Admin notification routing</span>
    <strong>{verified.length <= 1 ? "Single-workspace default" : "Multi-workspace delivery logic"}</strong>
    <p>{verified.length <= 1 ? "All lead summaries go to the connected Slack workspace after you choose its channel." : "Rules are optional. Without rules, every lead summary goes to the selected default channel. Add rules only when different workspaces or channels should receive different lead types."}</p>
    {canManage ? <>
      <label>Default Slack workspace<select value={defaultConnectionId ?? ""} onChange={(event) => setDefaultConnectionId(event.target.value || null)}><option value="">Use newest verified workspace</option>{verified.map((item) => <option value={item.id} key={item.id}>{item.account_name}{item.metadata.slack_channel_name ? ` · #${String(item.metadata.slack_channel_name)}` : " · choose a channel first"}</option>)}</select></label>
      <div className="slack-routing-rules"><div className="card-actions"><b>Ordered rules</b><button className="button-quiet" onClick={addRule} disabled={saving || !verified.length}>Add rule</button></div>{rules.length === 0 && <small>No rules yet. The default workspace receives every notification.</small>}{rules.map((rule) => <div className="slack-routing-rule" key={rule.id}>
        <input aria-label="Slack routing rule name" value={rule.name} onChange={(event) => updateRule(rule.id, { name: event.target.value })} />
        <label>Lead temperature<select value={rule.qualification_bands[0] ?? ""} onChange={(event) => updateRule(rule.id, { qualification_bands: event.target.value ? [event.target.value] : [] })}><option value="">Any temperature</option>{["hot", "warm", "nurture", "disqualify", "manual_review"].map((band) => <option value={band} key={band}>{band.replace("_", " ")}</option>)}</select></label>
        <label>Queue<input placeholder="Any queue" value={commaList(rule.queues)} onChange={(event) => updateRule(rule.id, { queues: parseCommaList(event.target.value) })} /></label>
        <label>Lead source<select value={rule.sources[0] ?? ""} onChange={(event) => updateRule(rule.id, { sources: event.target.value ? [event.target.value] : [] })}>{sourceOptions.map(([value, label]) => <option value={value} key={value || "any"}>{label}</option>)}</select></label>
        <label>Send to<select value={rule.connection_id} onChange={(event) => updateRule(rule.id, { connection_id: event.target.value })}>{verified.map((item) => <option value={item.id} key={item.id}>{item.account_name}{item.metadata.slack_channel_name ? ` · #${String(item.metadata.slack_channel_name)}` : " · choose channel"}</option>)}</select></label>
        <label>Weight<input aria-label={`Weight for ${rule.name}`} type="number" min="0.01" max="100" step="0.25" value={rule.weight ?? 1} onChange={(event) => updateRule(rule.id, { weight: Number(event.target.value) || 1 })} /></label>
        <small className="slack-rule-preview">Send {rule.sources[0] ? `${rule.sources[0].replaceAll("_", " ")} leads` : "all matching leads"} {rule.qualification_bands[0] ? `that are ${rule.qualification_bands[0].replaceAll("_", " ")}` : ""} to the selected Slack channel. Weight: {rule.weight ?? 1}.</small>
        <button className="button-quiet danger" onClick={() => setRules((current) => current.filter((item) => item.id !== rule.id))}>Remove</button>
      </div>)}</div>
      <button onClick={() => void save()} disabled={saving || !verified.length}>{saving ? "Saving…" : "Save Slack routing"}</button>
    </> : <small>{config?.rules.length ? `${config.rules.length} admin rule${config.rules.length === 1 ? "" : "s"} configured.` : "The workspace admin has not added custom Slack rules."}</small>}
  </div>;
}

export function IntegrationsView({ permissions }: { permissions: ReadonlySet<string> }) {
  const [cards, setCards] = useState<IntegrationCard[]>([]);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [accountNames, setAccountNames] = useState<Record<string, string>>({});
  const [credentials, setCredentials] = useState<Record<string, Record<string, string>>>({});
  const [modelSelections, setModelSelections] = useState<Record<string, string>>({});
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [syncStates, setSyncStates] = useState<Record<string, CrmInboundSyncState | null>>({});
  const [slackChannels, setSlackChannels] = useState<Record<string, Array<{ id: string; name: string }>>>({});
  const [slackMembers, setSlackMembers] = useState<Record<string, Array<{ id: string; name: string }>>>({});
  const [slackRouting, setSlackRouting] = useState<SlackNotificationConfig | null>(null);
  const canView = can(permissions, "integrations.view");
  const canManage = can(permissions, "integrations.manage");
  const canConnect = can(permissions, "integrations.connect");
  const canDisconnect = can(permissions, "integrations.disconnect");
  const canSync = can(permissions, "crm.sync");
  const canInboundSync = canSync;

  const refresh = async () => {
    try {
      const nextCards = await listIntegrations();
      setCards(nextCards);
      try { setSlackRouting(await getSlackNotificationConfig()); } catch { setSlackRouting(null); }
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
      setNotice(status === "connected" ? `${provider} connected successfully.` : `${provider} authorization failed. Reconnect to try again.`);
      window.history.replaceState({}, "", window.location.pathname);
      void refresh();
    }
  }, []);

  async function act(provider: string, operation: () => Promise<unknown>, success: string) {
    setBusy(provider); setNotice(null);
    try {
      await operation();
      setNotice(success);
      setCredentials((current) => ({ ...current, [provider]: {} }));
      await refresh();
    } catch (actionError) {
      setNotice(actionError instanceof Error ? actionError.message : "Integration action failed");
    } finally { setBusy(null); }
  }

  async function connectOAuth(provider: string) {
    if (provider === "slack" && !canManage) {
      setNotice("Integration management permission is required to connect Slack.");
      return;
    }
    setBusy(provider); setNotice(null);
    try {
      const started = await beginIntegration(provider);
      window.location.assign(apiUrl(started.authorization_url));
    } catch (actionError) {
      setNotice(actionError instanceof Error ? actionError.message : "Authorization could not begin");
      setBusy(null);
    }
  }

  const sections = [
    {
      id: "available",
      eyebrow: "Ready to connect",
      title: "Available Integrations",
      description: "Certified workspace connections for inbound acquisition, CRM coordination and notifications.",
      providers: ["gmail", "salesforce", "hubspot", "slack", "openai"],
    },
    {
      id: "coming-soon",
      eyebrow: "Release roadmap",
      title: "Coming Soon",
      description: "Visible for planning only. Connection and setup actions remain unavailable.",
      providers: ["outlook", "linkedin"],
    },
  ];
  const cardByProvider = new Map(cards.map((card) => [card.provider, card]));

  return <div className="page-view operational-page">
    <header className="page-hero integrations-hero"><div><span className="eyebrow">Settings / integration fabric</span><h1>Connect the systems<br /><em>your agents can trust.</em></h1><p>Workspace-scoped connections, explicit permissions and secret-safe handoffs make every external action controlled and inspectable.</p></div><div className="connection-orbit" aria-hidden="true"><b>AI</b>{["CRM", "CHAT", "MAIL", "DATA"].map((node) => <i key={node}>{node}</i>)}</div></header>
    {notice && <div className="notice" role="status">{notice}</div>}
    {!canView ? <div className="team-denied panel"><span className="eyebrow">Integration visibility</span><h2>Integration controls are restricted</h2><p>Your current workspace permissions do not include <code>integrations.view</code>.</p></div> : <div className="integration-sections">{sections.map((section) => <section className={`integration-section integration-section-${section.id}`} key={section.id} aria-labelledby={`integration-section-${section.id}`}>
      <div className="integration-section-heading"><div><span className="eyebrow">{section.eyebrow}</span><h2 id={`integration-section-${section.id}`}>{section.title}</h2></div><p>{section.description}</p></div>
      <div className="integration-grid">{section.providers.map((provider) => cardByProvider.get(provider)).filter((card): card is IntegrationCard => Boolean(card)).map((card) => {
       const connection = card.connection;
       const status = connection?.status ?? "not_connected";
       const comingSoon = section.id === "coming-soon";
       const openAI = card.provider === "openai";
       const statusLabel = comingSoon
         ? "Coming soon"
         : openAI
           ? !connection || status === "disconnected"
             ? "Not connected"
             : status === "verified"
               ? "Connected — Verified"
               : ["needs_attention", "needs_reauthorization", "error", "connection_failed"].includes(status)
                 ? "Needs attention"
                 : "Connected — Not verified"
           : status.replaceAll("_", " ");
      const providerCredentials = credentials[card.provider] ?? {};
      const open = expanded === card.provider;
      const activeConnectionStage = connectionStage(connection?.status);
      const syncState = connection ? syncStates[connection.id] : null;
      const crmDirection = card.category === "CRM";
      const gmailDirection = card.provider === "gmail";
      const outlookDirection = card.provider === "outlook";
      const mailDirection = gmailDirection || outlookDirection;
      const canConnectProvider = openAI
        ? canManage
        : canConnect && (card.provider !== "slack" || canManage);
      const slackConnections = card.provider === "slack" ? (card.connections.length ? card.connections : connection ? [connection] : []) : [];
      const selectedOpenAIModel = modelSelections[card.provider] ?? connection?.selected_model ?? card.default_model ?? "";
       return <article className={`integration-card state-${comingSoon ? "coming-soon" : status}`} key={card.provider}>
         <div className="provider-head"><div className="provider-glyph">{card.display_name.slice(0, 2).toUpperCase()}</div><div><span>{card.category}</span><h2>{card.display_name}</h2></div><b><i />{statusLabel}</b></div>
         <p>{comingSoon ? card.credential_help : connection?.account_name ?? card.credential_help}</p>
         <p className="integration-direction"><strong>{comingSoon ? card.credential_help : gmailDirection ? "Automatic inbound email acquisition" : crmDirection ? "Automatic inbound · Manual outbound" : card.provider === "slack" ? "Application → Slack · outbound notifications only" : openAI ? "Workspace OpenAI project → structured lead analysis" : card.directionality.description}</strong>{crmDirection && " — CRM records enter only after inbound sync is enabled; sending qualification back always requires an explicit action."}</p>
         {comingSoon && <div className="coming-soon-provider" role="status"><b>Coming soon</b><span>This provider is not available for connection in the current release.</span></div>}
        {card.provider === "slack" && <>{slackConnections.map((slackConnection) => { const selectedSlackChannel = typeof slackConnection.metadata.slack_channel_id === "string" ? slackConnection.metadata.slack_channel_id : ""; const selectedSlackChannelName = typeof slackConnection.metadata.slack_channel_name === "string" ? slackConnection.metadata.slack_channel_name : ""; const memberKey = `${slackConnection.id}:${selectedSlackChannel}`; return slackConnection.status === "verified" && <div className="integration-guide slack-destination" key={slackConnection.id}><strong>Connected — Verified</strong><span>Workspace: {slackConnection.account_name}</span><span>Notification channel: {selectedSlackChannel ? `#${selectedSlackChannelName || selectedSlackChannel}` : "Choose a channel"}</span>{canManage && <><div className="card-actions"><button className="button-quiet" onClick={() => void act(card.provider, async () => { const channels = await listSlackChannels(slackConnection.id); setSlackChannels((current) => ({ ...current, [slackConnection.id]: channels })); }, "Slack channels loaded.")}>Change channel</button>{selectedSlackChannel && <><button className="button-quiet" onClick={() => void act(card.provider, () => sendSlackTestNotification(slackConnection.id), "Test notification sent to Slack.")}>Send test notification</button><button className="button-quiet" onClick={() => void act(card.provider, async () => { const members = await listSlackChannelMembers(slackConnection.id, selectedSlackChannel); setSlackMembers((current) => ({ ...current, [memberKey]: members })); }, "Slack channel members loaded.")}>View channel members</button></>}</div>{(slackChannels[slackConnection.id] ?? []).length > 0 && <label>Notification channel <select value={selectedSlackChannel} onChange={(event) => { const channelId = event.target.value; if (channelId) void act(card.provider, () => selectSlackChannel(slackConnection.id, channelId), "Slack notification channel updated."); }}><option value="">Select a public channel</option>{slackChannels[slackConnection.id].map((channel) => <option value={channel.id} key={channel.id}>#{channel.name} · {channel.id}</option>)}</select></label>}{(slackMembers[memberKey] ?? []).length > 0 && <div className="slack-member-list"><small>Members in this channel</small>{slackMembers[memberKey].map((member) => <span key={member.id}>{member.name} · {member.id}</span>)}</div>}</>}</div>; })}<SlackRoutingEditor connections={slackConnections} config={slackRouting} canManage={canManage} onSaved={setSlackRouting} onNotice={setNotice} /></>}
        {!comingSoon && <div className="connection-facts"><span>Credential <b>{connection?.credential_state ?? "missing"}</b></span><span>Verification <b>{connection?.verification_status?.replaceAll("_", " ") ?? "not verified"}</b></span><span>Last checked <b>{connection?.last_validation_at ? new Date(connection.last_validation_at).toLocaleString() : "Never"}</b></span></div>}
        {openAI && connection && <div className="connection-facts"><span>Selected model <b>{connection.selected_model ?? "Not configured"}</b></span><span>Billing <b>Your OpenAI account</b></span></div>}
        {!comingSoon && <div className={`connection-state-path state-step-${activeConnectionStage}`} aria-label={`Connection state: ${connectionStages[activeConnectionStage]}`}>
          {connectionStages.map((label, index) => <div className={index < activeConnectionStage ? "complete" : index === activeConnectionStage ? "active" : ""} key={label}><i /><span>{label}</span>{index < connectionStages.length - 1 && <b />}</div>)}
        </div>}
        {!comingSoon && <div className="card-actions">
          {!connection || connection.status === "disconnected" ? canConnectProvider && <button onClick={() => card.supported_auth.includes("oauth2") ? void connectOAuth(card.provider) : setExpanded(open ? null : card.provider)} disabled={busy === card.provider}>{busy === card.provider ? "Connecting…" : gmailDirection ? "Connect Gmail" : outlookDirection ? "Connect Outlook" : openAI ? "Connect OpenAI" : "Connect"}</button> : <>{canManage && <><button onClick={() => void act(card.provider, async () => { const result = await testIntegration(connection.id); if (!result.verified) throw new Error(result.message); }, `${card.display_name} connection verified.`)} disabled={busy === card.provider}>{openAI ? "Test Connection" : "Verify connection"}</button><button className="button-quiet" onClick={() => card.supported_auth.includes("oauth2") ? void connectOAuth(card.provider) : setExpanded(open ? null : card.provider)}>{openAI ? "Replace Key" : "Reconnect"}</button></>}{canDisconnect && <button className="button-quiet danger" onClick={() => void act(card.provider, () => disconnectIntegration(connection.id), `${card.display_name} disconnected.`)} disabled={busy === card.provider}>Disconnect</button>}</>}
          <button className="button-quiet" onClick={() => setExpanded(open ? null : card.provider)}>Setup guide</button>
        </div>}
        {openAI && connection && canManage && <div className="integration-guide"><span className="eyebrow">Model configuration</span><label>Allowed model<select value={selectedOpenAIModel} onChange={(event) => setModelSelections((current) => ({ ...current, [card.provider]: event.target.value }))}>{card.supported_models.map((model) => <option value={model} key={model}>{model}</option>)}</select></label><button onClick={() => void act(card.provider, () => updateOpenAIModel(connection.id, selectedOpenAIModel), "OpenAI model saved. Test the connection before use.")} disabled={busy === card.provider || !selectedOpenAIModel}>Save model</button></div>}
        {!comingSoon && (crmDirection || mailDirection) && connection?.status === "verified" && <div className="integration-guide">
          <span className="eyebrow">{mailDirection ? "Automatic inbound email acquisition" : "Inbound CRM synchronization"}</span>
          <strong>{syncState?.enabled ? "Enabled · automatic inbound" : "Disabled"}</strong>
          <p>{syncState?.last_successful_sync_at ? `Last synced ${new Date(syncState.last_successful_sync_at).toLocaleString()} · ${syncState.records_processed.toLocaleString()} records processed.` : "No provider records have been imported yet."}</p>
          {mailDirection && <p>{syncState?.next_sync_at ? `Next sync ${new Date(syncState.next_sync_at).toLocaleString()}.` : `Status: ${syncState?.status ?? "not configured"}.`}{syncState?.status && ` Latest result: ${syncState.status.replaceAll("_", " ")}.`}</p>}
          {syncState?.last_error && <small>{syncState.error_code || "Sync failed"}: {syncState.last_error}</small>}
          {(canInboundSync || canManage) && <div className="card-actions">
            {canManage && <button onClick={() => void act(card.provider, () => configureInboundSync(connection.id, !syncState?.enabled, card.provider), `${mailDirection ? "Automatic inbound" : "Inbound sync"} ${syncState?.enabled ? "disabled" : "enabled"}.`)} disabled={busy === card.provider}>{syncState?.enabled ? (mailDirection ? "Disable automatic inbound" : "Disable inbound sync") : (mailDirection ? "Enable automatic inbound" : "Enable inbound sync")}</button>}
            {syncState?.enabled && <button className="button-quiet" onClick={() => void act(card.provider, () => runInboundSync(connection.id, syncState.initial_import_completed ? "manual" : "initial", card.provider), "Bounded inbound sync completed.")} disabled={busy === card.provider}>Sync now</button>}
          </div>}
        </div>}
        {!comingSoon && open && <div className="integration-guide">
          <span className="eyebrow">Connection briefing</span>
          <strong>{card.dashboard_path}</strong>
          <p>{card.credential_help}</p>
          <p>{card.permission_reason}</p>
          {openAI && <>
            <p>Create a dedicated OpenAI Project and project-scoped API key for this workspace. Restrict its permissions where possible, configure project budgets and limits, and rotate it periodically. Do not use a personal master key.</p>
            <p><strong>OpenAI API usage is billed directly to your OpenAI account.</strong></p>
          </>}
          <div className="scope-list">{card.required_scopes.map((scope) => <code key={scope}>{scope}</code>)}</div>
          <a href={card.documentation_url} target="_blank" rel="noreferrer">Official provider documentation →</a>
          {card.supported_auth.includes("oauth2") && canConnect && <button className="oauth-button" onClick={() => void connectOAuth(card.provider)}>Sign in to {card.display_name} →</button>}
          {card.manual_fields.length > 0 && canManage && <div className="manual-credential">
            <span>Secure credential setup</span>
            {!openAI && <input aria-label={`${card.display_name} account name`} placeholder="Connected account name" value={accountNames[card.provider] ?? ""} onChange={(event) => setAccountNames((current) => ({ ...current, [card.provider]: event.target.value }))} />}
            {openAI && !connection && <label>Model<select value={selectedOpenAIModel} onChange={(event) => setModelSelections((current) => ({ ...current, [card.provider]: event.target.value }))}>{card.supported_models.map((model) => <option value={model} key={model}>{model}</option>)}</select></label>}
            {card.manual_fields.map((field) => <input key={field} type="password" autoComplete="new-password" aria-label={field.replaceAll("_", " ")} placeholder={openAI ? "OpenAI project API key" : field.replaceAll("_", " ")} value={providerCredentials[field] ?? ""} onChange={(event) => setCredentials((current) => ({ ...current, [card.provider]: { ...(current[card.provider] ?? {}), [field]: event.target.value } }))} />)}
            <button disabled={openAI && !providerCredentials.api_key} onClick={() => void act(card.provider, () => connection ? reconnectIntegration(connection.id, providerCredentials) : addIntegrationCredential(card.provider, accountNames[card.provider] || (openAI ? "OpenAI project" : `${card.display_name} account`), card.preferred_auth === "api_key" ? "api_key" : "private_token", card.required_scopes, providerCredentials, openAI ? { selected_model: selectedOpenAIModel } : {}), `${card.display_name} credential stored securely; test it before use.`)}>{connection && openAI ? "Replace Key" : "Store credentials"}</button>
            <small>The key is sent only to the backend, encrypted by the configured secret service, cleared from this form after success, and never returned.</small>
          </div>}
        </div>}
      </article>;
    })}</div>
    </section>)}</div>}
  </div>;
}
