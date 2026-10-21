import "./_group.css";

const sections = [
  "Overview",
  "Ask Corral",
  "AI & qualification",
  "Follow-ups & meetings",
  "Email",
  "Comments & closing",
  "History",
];

export function Current() {
  return (
    <main className="lead-email-preview">
      <article className="lead-story">
        <aside className="attention-callout lead-review-reason" role="status">
          <b>Human review required</b>
          <p>
            <strong>Matched conditions and field evidence</strong>
            <span>
              This lead remains on a human-review hold, but no currently enabled rule identifies a specific trigger.
              Current evidence: Email: jordan@harbor.example; Full name: Jordan Blake; Company name: Harbor Studio.
              Analysis confidence: 65%. Missing qualification fields: job title, industry, employee count, annual revenue,
              geography, budget, requested service, pain point, explicit timeline, company size, buyer role, decision maker,
              competitors, and technical requirements.
            </span>
          </p>
        </aside>
        <nav className="lead-detail-navigation" aria-label="Lead detail sections">
          <div className="lead-detail-tabs" role="tablist" aria-label="Lead detail">
            {sections.map((section) => (
              <a key={section} className="lead-detail-tab" href="#" role="tab" aria-selected={section === "Email"}>
                {section}
              </a>
            ))}
          </div>
          <button className="lead-section-pin" type="button"><span aria-hidden="true">◇</span>Pin this section</button>
        </nav>
        <section className="lead-detail-panel" role="tabpanel" aria-label="Email conversation">
          <section className="lead-email-panel">
            <header className="lead-email-heading">
              <div>
                <span className="eyebrow">Email conversation</span>
                <h2>Jordan Blake</h2>
                <p>Messages stay linked to their Gmail mailbox. Refresh imports new messages and re-checks lead qualification.</p>
              </div>
              <div className="lead-email-actions">
                <button type="button">Ask Corral</button>
                <button type="button">Compose</button>
                <button type="button">Refresh from Gmail</button>
              </div>
            </header>
            <div className="email-empty-state">
              <strong>No email has been recorded for this lead.</strong>
              <span>Use Refresh from Gmail to check connected inboxes, or compose a new message.</span>
            </div>
          </section>
        </section>
      </article>
    </main>
  );
}
