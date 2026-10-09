import "./_group.css";

const providers = [
  {
    name: "OpenAI",
    status: "Connected — Verified",
    account: "OpenAI project",
    state: "verified",
  },
  {
    name: "Google Gemini",
    status: "Not connected",
    account: "No account connected",
    state: "not-connected",
  },
  {
    name: "Anthropic Claude",
    status: "Not connected",
    account: "No account connected",
    state: "not-connected",
  },
];

export function Current() {
  return (
    <main className="ai-connections-preview">
      <div className="integration-grid" aria-label="Current AI provider connections">
        {providers.map((provider) => (
          <article
            className={`integration-card provider-${provider.name.toLowerCase().replaceAll(" ", "-")} state-${provider.state} is-collapsed`}
            key={provider.name}
          >
            <div className="provider-head">
              <div className="provider-glyph">{provider.name.slice(0, 2).toUpperCase()}</div>
              <div className="provider-identity">
                <span>AI Analysis</span>
                <h2>{provider.name}</h2>
                <small className="provider-account-summary">{provider.account}</small>
              </div>
              <b><i />{provider.status}</b>
              <button
                type="button"
                className="provider-collapse-toggle"
                aria-label={`Expand ${provider.name} details`}
                aria-expanded="false"
              >
                <span aria-hidden="true">+</span>
              </button>
            </div>
          </article>
        ))}
      </div>
    </main>
  );
}
