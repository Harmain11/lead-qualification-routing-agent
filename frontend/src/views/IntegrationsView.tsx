import { useEffect, useState } from "react";

import {
  addIntegrationCredential,
  apiUrl,
  beginIntegration,
  configureInboundSync,
  disconnectIntegration,
  getInboundSyncState,
  listIntegrations,
  listSlackChannels,
  reconnectIntegration,
  selectSlackChannel,
  sendSlackTestNotification,
  runInboundSync,
  testIntegration,
  updateOpenAIModel,
} from "../api";
import { can } from "../permissions";
import type { CrmInboundSyncState, IntegrationCard } from "../types";

const connectionStages = ["Disconnected", "Authorizing", "Credentials stored", "Verified"];

function connectionStage(status?: string) {
  if (status === "verified") return 3;
  if (["configured", "connected", "validating", "needs_attention", "connection_failed", "authorization_expired", "reconnect_required", "expired", "needs_reauthorization", "error"].includes(status ?? "")) return 2;
  if (["connecting", "awaiting_authorization"].includes(status ?? "")) return 1;
  return 0;
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
      const inboundConnections = nextCards.filter((card) => (card.category === "CRM" || card.provider === "gmail") && card.connection);
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
      const canConnectProvider = openAI
        ? canManage
        : canConnect && (card.provider !== "slack" || canManage);
      const selectedSlackChannel = typeof connection?.metadata.slack_channel_id === "string" ? connection.metadata.slack_channel_id : "";
      const selectedSlackChannelName = typeof connection?.metadata.slack_channel_name === "string" ? connection.metadata.slack_channel_name : "";
      const selectedOpenAIModel = modelSelections[card.provider] ?? connection?.selected_model ?? card.default_model ?? "";
       return <article className={`integration-card state-${comingSoon ? "coming-soon" : status}`} key={card.provider}>
         <div className="provider-head"><div className="provider-glyph">{card.display_name.slice(0, 2).toUpperCase()}</div><div><span>{card.category}</span><h2>{card.display_name}</h2></div><b><i />{statusLabel}</b></div>
         <p>{comingSoon ? card.credential_help : connection?.account_name ?? card.credential_help}</p>
         <p className="integration-direction"><strong>{comingSoon ? card.credential_help : gmailDirection ? "Automatic inbound email acquisition" : crmDirection ? "Automatic inbound · Manual outbound" : card.provider === "slack" ? "Application → Slack · outbound notifications only" : openAI ? "Workspace OpenAI project → structured lead analysis" : card.directionality.description}</strong>{crmDirection && " — CRM records enter only after inbound sync is enabled; sending qualification back always requires an explicit action."}</p>
         {comingSoon && <div className="coming-soon-provider" role="status"><b>Coming soon</b><span>This provider is not available for connection in the current release.</span></div>}
        {card.provider === "slack" && connection?.status === "verified" && <div className="integration-guide slack-destination"><strong>Connected — Verified</strong><span>Workspace: {connection.account_name}</span><span>Notification channel: {selectedSlackChannel ? `#${selectedSlackChannelName || selectedSlackChannel}` : "Choose a channel"}</span>{canManage && <><div className="card-actions"><button className="button-quiet" onClick={() => void act(card.provider, async () => { const channels = await listSlackChannels(connection.id); setSlackChannels((current) => ({ ...current, [connection.id]: channels })); }, "Slack channels loaded.")}>Change channel</button>{selectedSlackChannel && <button className="button-quiet" onClick={() => void act(card.provider, () => sendSlackTestNotification(connection.id), "Test notification sent to Slack.")}>Send test notification</button>}</div>{(slackChannels[connection.id] ?? []).length > 0 && <label>Notification channel <select value={selectedSlackChannel} onChange={(event) => { const channelId = event.target.value; if (channelId) void act(card.provider, () => selectSlackChannel(connection.id, channelId), "Slack notification channel updated."); }}><option value="">Select a public channel</option>{slackChannels[connection.id].map((channel) => <option value={channel.id} key={channel.id}>#{channel.name} · {channel.id}</option>)}</select></label>}</>}</div>}
        {!comingSoon && <div className="connection-facts"><span>Credential <b>{connection?.credential_state ?? "missing"}</b></span><span>Verification <b>{connection?.verification_status?.replaceAll("_", " ") ?? "not verified"}</b></span><span>Last checked <b>{connection?.last_validation_at ? new Date(connection.last_validation_at).toLocaleString() : "Never"}</b></span></div>}
        {openAI && connection && <div className="connection-facts"><span>Selected model <b>{connection.selected_model ?? "Not configured"}</b></span><span>Billing <b>Your OpenAI account</b></span></div>}
        {!comingSoon && <div className={`connection-state-path state-step-${activeConnectionStage}`} aria-label={`Connection state: ${connectionStages[activeConnectionStage]}`}>
          {connectionStages.map((label, index) => <div className={index < activeConnectionStage ? "complete" : index === activeConnectionStage ? "active" : ""} key={label}><i /><span>{label}</span>{index < connectionStages.length - 1 && <b />}</div>)}
        </div>}
        {!comingSoon && <div className="card-actions">
          {!connection || connection.status === "disconnected" ? canConnectProvider && <button onClick={() => card.supported_auth.includes("oauth2") ? void connectOAuth(card.provider) : setExpanded(open ? null : card.provider)} disabled={busy === card.provider}>{busy === card.provider ? "Connecting…" : gmailDirection ? "Connect Gmail" : openAI ? "Connect OpenAI" : "Connect"}</button> : <>{canManage && <><button onClick={() => void act(card.provider, async () => { const result = await testIntegration(connection.id); if (!result.verified) throw new Error(result.message); }, `${card.display_name} connection verified.`)} disabled={busy === card.provider}>{openAI ? "Test Connection" : "Verify connection"}</button><button className="button-quiet" onClick={() => card.supported_auth.includes("oauth2") ? void connectOAuth(card.provider) : setExpanded(open ? null : card.provider)}>{openAI ? "Replace Key" : "Reconnect"}</button></>}{canDisconnect && <button className="button-quiet danger" onClick={() => void act(card.provider, () => disconnectIntegration(connection.id), `${card.display_name} disconnected.`)} disabled={busy === card.provider}>Disconnect</button>}</>}
          <button className="button-quiet" onClick={() => setExpanded(open ? null : card.provider)}>Setup guide</button>
        </div>}
        {openAI && connection && canManage && <div className="integration-guide"><span className="eyebrow">Model configuration</span><label>Allowed model<select value={selectedOpenAIModel} onChange={(event) => setModelSelections((current) => ({ ...current, [card.provider]: event.target.value }))}>{card.supported_models.map((model) => <option value={model} key={model}>{model}</option>)}</select></label><button onClick={() => void act(card.provider, () => updateOpenAIModel(connection.id, selectedOpenAIModel), "OpenAI model saved. Test the connection before use.")} disabled={busy === card.provider || !selectedOpenAIModel}>Save model</button></div>}
        {!comingSoon && (crmDirection || gmailDirection) && connection?.status === "verified" && <div className="integration-guide">
          <span className="eyebrow">{gmailDirection ? "Automatic inbound email acquisition" : "Inbound CRM synchronization"}</span>
          <strong>{syncState?.enabled ? "Enabled · automatic inbound" : "Disabled"}</strong>
          <p>{syncState?.last_successful_sync_at ? `Last synced ${new Date(syncState.last_successful_sync_at).toLocaleString()} · ${syncState.records_processed.toLocaleString()} records processed.` : "No provider records have been imported yet."}</p>
          {gmailDirection && <p>{syncState?.next_sync_at ? `Next sync ${new Date(syncState.next_sync_at).toLocaleString()}.` : `Status: ${syncState?.status ?? "not configured"}.`}{syncState?.status && ` Latest result: ${syncState.status.replaceAll("_", " ")}.`}</p>}
          {syncState?.last_error && <small>{syncState.error_code || "Sync failed"}: {syncState.last_error}</small>}
          {(canInboundSync || canManage) && <div className="card-actions">
            {canManage && <button onClick={() => void act(card.provider, () => configureInboundSync(connection.id, !syncState?.enabled, card.provider), `${gmailDirection ? "Automatic inbound" : "Inbound sync"} ${syncState?.enabled ? "disabled" : "enabled"}.`)} disabled={busy === card.provider}>{syncState?.enabled ? (gmailDirection ? "Disable automatic inbound" : "Disable inbound sync") : (gmailDirection ? "Enable automatic inbound" : "Enable inbound sync")}</button>}
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
