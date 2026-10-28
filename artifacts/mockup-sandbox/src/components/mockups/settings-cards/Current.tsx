import "./_group.css";

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <div className="preview-fact">
      <span>{label}</span>
      <b>{value}</b>
    </div>
  );
}

export function Current() {
  return (
    <main className="settings-card-preview">
      <section className="settings-preview-panel current-workspace">
        <div className="preview-heading">
          <div>
            <span className="preview-eyebrow">Tenant boundary</span>
            <h2>Workspace</h2>
          </div>
        </div>
        <div className="preview-facts">
          <Fact label="Name" value="Northstar Workspace" />
          <Fact label="Workspace ID" value="8d1a4c2e-75b3-4d19-a684-55c726" />
          <Fact label="Data mode" value="Live" />
          <Fact label="Analytics freshness" value="Oct 21, 2026 · 2:54 PM" />
        </div>
      </section>
    </main>
  );
}
