import "./_group.css";

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <div className="preview-fact">
      <span>{label}</span>
      <b>{value}</b>
    </div>
  );
}

export function Collapsible() {
  return (
    <main className="settings-card-preview">
      <details className="preview-disclosure" open>
        <summary>
          <div className="preview-summary">
            <span className="preview-eyebrow">Tenant boundary</span>
            <h2>Workspace</h2>
          </div>
          <span className="preview-icon" aria-hidden="true" />
        </summary>
        <div className="preview-disclosure-content">
          <div className="preview-facts">
            <Fact label="Name" value="Northstar Workspace" />
            <Fact label="Workspace ID" value="8d1a4c2e-75b3-4d19-a684-55c726" />
            <Fact label="Data mode" value="Live" />
            <Fact label="Analytics freshness" value="Oct 21, 2026 · 2:54 PM" />
          </div>
        </div>
      </details>
      <details className="preview-disclosure">
        <summary>
          <div className="preview-summary">
            <span className="preview-eyebrow">Optional analysis context</span>
            <h2>Business identity for AI</h2>
          </div>
          <span className="preview-icon" aria-hidden="true" />
        </summary>
        <div className="preview-disclosure-content">
          <p>
            Add company details that help AI distinguish your organization from
            prospective leads.
          </p>
        </div>
      </details>
      <details className="preview-disclosure">
        <summary>
          <div className="preview-summary">
            <span className="preview-eyebrow">Analytics contract</span>
            <h2>Data range</h2>
          </div>
          <span className="preview-icon" aria-hidden="true" />
        </summary>
        <div className="preview-disclosure-content">
          <p>Weekly, monthly, and annual reporting windows are available.</p>
        </div>
      </details>
    </main>
  );
}
