import { useState } from "react";
import "./_group.css";

const providers = [
  { id: "openai", name: "OpenAI", account: "OpenAI project", status: "Connected — Verified", connected: true },
  { id: "gemini", name: "Google Gemini", account: "No account connected", status: "Not connected", connected: false },
  { id: "claude", name: "Anthropic Claude", account: "No account connected", status: "Not connected", connected: false },
];

export function Unified() {
  const [selectedProvider, setSelectedProvider] = useState("openai");
  const [expanded, setExpanded] = useState(true);
  const provider = providers.find((item) => item.id === selectedProvider) ?? providers[0];

  return (
    <main className="ai-connections-preview">
      <div className="integration-grid">
        <article className={`integration-card provider-ai-analysis state-${provider.connected ? "verified" : "not-connected"}${expanded ? "" : " is-collapsed"}`}>
          <div className="provider-head">
            <div className="provider-glyph">AI</div>
            <div className="provider-identity">
              <span>AI Analysis</span>
              <h2>AI Connection</h2>
              <small className="provider-account-summary">{provider.name} · {provider.account}</small>
            </div>
            <b><i />{provider.status}</b>
            <button
              type="button"
              className="provider-collapse-toggle"
              aria-label={`${expanded ? "Collapse" : "Expand"} AI connection details`}
              aria-expanded={expanded}
              onClick={() => setExpanded((open) => !open)}
            >
              <span aria-hidden="true">{expanded ? "−" : "+"}</span>
            </button>
          </div>
          <div className="provider-details" hidden={!expanded}>
            <label className="ai-provider-picker">
              Select an AI provider
              <select value={selectedProvider} onChange={(event) => setSelectedProvider(event.target.value)}>
                {providers.map((item) => (
                  <option value={item.id} key={item.id}>{item.name}</option>
                ))}
              </select>
            </label>
            <p className="ai-provider-hint">
              Choose which provider handles lead analysis. Each provider keeps its own API key, model, and connection status.
            </p>
            <div className="connection-facts">
              <span>Connection status<b>{provider.status}</b></span>
              <span>Analysis usage<b>Billed to {provider.name}</b></span>
              <span>Provider choice<b>{provider.connected ? "Connected" : "Ready to connect"}</b></span>
            </div>
            <div className="card-actions">
              <button type="button">{provider.connected ? "Test connection" : `Connect ${provider.name}`}</button>
              {provider.connected && <button type="button" className="button-quiet">Replace API key</button>}
            </div>
          </div>
        </article>
      </div>
    </main>
  );
}
