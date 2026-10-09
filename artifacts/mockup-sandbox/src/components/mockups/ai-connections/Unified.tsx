import { useState } from "react";
import "./_group.css";

const providers = [
  { id: "openai", name: "OpenAI", account: "OpenAI project", status: "Connected — Verified", connected: true },
  { id: "gemini", name: "Google Gemini", account: "No account connected", status: "Not connected", connected: false },
  { id: "claude", name: "Anthropic Claude", account: "No account connected", status: "Not connected", connected: false },
  { id: "deepseek", name: "DeepSeek", account: "No account connected", status: "Not connected", connected: false },
  { id: "xai", name: "xAI", account: "No account connected", status: "Not connected", connected: false },
];

export function Unified() {
  const [selectedProvider, setSelectedProvider] = useState("openai");
  const [activeProviderId, setActiveProviderId] = useState("openai");
  const [expanded, setExpanded] = useState(true);
  const provider = providers.find((item) => item.id === selectedProvider) ?? providers[0];
  const activeProvider = providers.find((item) => item.id === activeProviderId) ?? providers[0];

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
              Select a provider to configure
              <select value={selectedProvider} onChange={(event) => setSelectedProvider(event.target.value)}>
                {providers.map((item) => (
                  <option value={item.id} key={item.id}>{item.name}</option>
                ))}
              </select>
            </label>
            <p className="ai-provider-hint">
              This selection only opens that provider’s own key, model, and connection settings. It does not change the active provider for lead analysis.
            </p>
            <div className="connection-facts">
              <span>Provider status<b>{provider.status}</b></span>
              <span>Provider being configured<b>{provider.name}</b></span>
              <span>Active for lead analysis<b>{activeProvider.name}</b></span>
            </div>
            <div className="card-actions">
              {provider.connected ? (
                <button
                  type="button"
                  disabled={provider.id === activeProviderId}
                  onClick={() => setActiveProviderId(provider.id)}
                >
                  {provider.id === activeProviderId ? "Active for lead analysis" : "Use for lead analysis"}
                </button>
              ) : (
                <button type="button">Connect {provider.name}</button>
              )}
              {provider.connected && <button type="button" className="button-quiet">Replace API key</button>}
            </div>
          </div>
        </article>
      </div>
    </main>
  );
}
