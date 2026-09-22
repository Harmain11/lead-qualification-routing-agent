import { useEffect, useState } from "react";
import {
  addIntegrationCredential,
  beginIntegration,
  disconnectIntegration,
  listIntegrations,
  reconnectIntegration,
  testIntegration,
} from "../api";
import type { IntegrationCard } from "../types";

const connectionStages = ["Disconnected", "Authorizing", "Validating", "Connected"];

function connectionStage(status?: string) {
  if (status === "connected") return 3;
  if (status === "validating" || status === "needs_attention" || status === "connection_failed" || status === "authorization_expired" || status === "reconnect_required") return 2;
  if (status === "connecting" || status === "awaiting_authorization") return 1;
  return 0;
}

export function IntegrationsView() {
  const [cards, setCards] = useState<IntegrationCard[]>([]);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [accountNames, setAccountNames] = useState<Record<string, string>>({});
  const [credentials, setCredentials] = useState<Record<string, Record<string, string>>>({});
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const refresh = () => listIntegrations().then(setCards).catch((error: Error) => setNotice(error.message));
  useEffect(() => { void refresh(); }, []);
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
    setBusy(provider); setNotice(null);
    try {
      const started = await beginIntegration(provider);
      window.location.assign(started.authorization_url);
    } catch (actionError) {
      setNotice(actionError instanceof Error ? actionError.message : "Authorization could not begin");
      setBusy(null);
    }
  }

  return <div className="page-view">
    <header className="page-hero integrations-hero"><div><span className="eyebrow">Settings / integration fabric</span><h1>Connect the systems<br /><em>your agents can trust.</em></h1><p>Workspace-scoped connections, explicit permissions and secret-safe handoffs make every external action controlled and inspectable.</p></div><div className="connection-orbit" aria-hidden="true"><b>AI</b>{["CRM", "CHAT", "MAIL", "DATA"].map((node) => <i key={node}>{node}</i>)}</div></header>
    {notice && <div className="notice" role="status">{notice}</div>}
    <section className="integration-grid">{cards.map((card) => {
      const connection = card.connection;
      const status = connection?.status ?? "not_connected";
      const providerCredentials = credentials[card.provider] ?? {};
      const open = expanded === card.provider;
      const activeConnectionStage = connectionStage(connection?.status);
      return <article className={`integration-card state-${status}`} key={card.provider}>
        <div className="provider-head"><div className="provider-glyph">{card.display_name.slice(0, 2).toUpperCase()}</div><div><span>{card.category}</span><h2>{card.display_name}</h2></div><b><i />{status.replaceAll("_", " ")}</b></div>
        <p>{connection?.account_name ?? card.credential_help}</p>
        <div className="connection-facts"><span>Credential <b>{connection?.credential_state ?? "missing"}</b></span><span>Last checked <b>{connection?.last_validation_at ? new Date(connection.last_validation_at).toLocaleString() : "Never"}</b></span></div>
        <div className={`connection-state-path state-step-${activeConnectionStage}`} aria-label={`Connection state: ${connectionStages[activeConnectionStage]}`}>
          {connectionStages.map((label, index) => <div className={index < activeConnectionStage ? "complete" : index === activeConnectionStage ? "active" : ""} key={label}><i /><span>{label}</span>{index < connectionStages.length - 1 && <b />}</div>)}
        </div>
        <div className="card-actions">{!connection || connection.status === "disconnected" ? <button onClick={() => card.supported_auth.includes("oauth2") ? void connectOAuth(card.provider) : setExpanded(open ? null : card.provider)} disabled={card.availability === "future" || busy === card.provider}>{busy === card.provider ? "Connecting…" : "Connect"}</button> : <><button onClick={() => void act(card.provider, () => testIntegration(connection.id), `${card.display_name} configuration checked.`)} disabled={busy === card.provider}>Test connection</button><button className="button-quiet" onClick={() => card.supported_auth.includes("oauth2") ? void connectOAuth(card.provider) : setExpanded(open ? null : card.provider)}>Reconnect</button><button className="button-quiet danger" onClick={() => void act(card.provider, () => disconnectIntegration(connection.id), `${card.display_name} disconnected.`)} disabled={busy === card.provider}>Disconnect</button></>}<button className="button-quiet" onClick={() => setExpanded(open ? null : card.provider)}>Setup guide</button></div>
        {open && <div className="integration-guide"><span className="eyebrow">Connection briefing</span><strong>{card.dashboard_path}</strong><p>{card.credential_help}</p><p>{card.permission_reason}</p><div className="scope-list">{card.required_scopes.map((scope) => <code key={scope}>{scope}</code>)}</div><a href={card.documentation_url} target="_blank" rel="noreferrer">Official provider documentation ↗</a>{card.supported_auth.includes("oauth2") && <button className="oauth-button" onClick={() => void connectOAuth(card.provider)}>Sign in to {card.display_name} →</button>}{card.manual_fields.length > 0 && <div className="manual-credential"><span>Secure credential setup</span><input aria-label={`${card.display_name} account name`} placeholder="Connected account name" value={accountNames[card.provider] ?? ""} onChange={(event) => setAccountNames((current) => ({ ...current, [card.provider]: event.target.value }))} />{card.manual_fields.map((field) => <input key={field} type="password" autoComplete="new-password" aria-label={field.replaceAll("_", " ")} placeholder={field.replaceAll("_", " ")} value={providerCredentials[field] ?? ""} onChange={(event) => setCredentials((current) => ({ ...current, [card.provider]: { ...(current[card.provider] ?? {}), [field]: event.target.value } }))} />)}<button onClick={() => void act(card.provider, () => connection ? reconnectIntegration(connection.id, providerCredentials) : addIntegrationCredential(card.provider, accountNames[card.provider] || `${card.display_name} account`, card.preferred_auth === "api_key" ? "api_key" : "private_token", card.required_scopes, providerCredentials), `${card.display_name} credentials stored by reference.`)}>Store once and test</button><small>The secret is encrypted by the configured secret service and is never returned to this interface.</small></div>}</div>}
      </article>;
    })}</section>
  </div>;
}
