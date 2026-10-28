import { useState } from "react";
import "./_group.css";
import "./Refined.css";

const sections = [
  "Overview",
  "Ask Corral",
  "AI & qualification",
  "Follow-ups & meetings",
  "Email",
  "Comments & closing",
  "History",
];

export function Refined() {
  const [pinned, setPinned] = useState(false);

  return (
    <main className="lead-email-preview refined-email">
      <article className="lead-story refined-story">
        <aside className="review-banner" role="status" aria-labelledby="review-title">
          <span className="review-mark" aria-hidden="true">!</span>
          <div className="review-content">
            <div className="review-title-row">
              <b id="review-title">Human review required</b>
              <span className="review-status">Review hold</span>
            </div>
            <p className="review-summary">
              <strong>Matched conditions and field evidence</strong>
              <span>
                This lead remains on a human-review hold. No currently enabled rule identifies a specific trigger.
              </span>
            </p>
            <details className="evidence-details">
              <summary>View evidence and missing qualification fields</summary>
              <div className="evidence-grid">
                <div className="evidence-item">
                  <span className="evidence-label">Email</span>
                  <span>avery@northstar.example</span>
                </div>
                <div className="evidence-item">
                  <span className="evidence-label">Full name</span>
                  <span>Avery Chen</span>
                </div>
                <div className="evidence-item">
                  <span className="evidence-label">Company name</span>
                  <span>Northstar Works</span>
                </div>
                <div className="evidence-item">
                  <span className="evidence-label">Analysis confidence</span>
                  <span>65%</span>
                </div>
                <div className="evidence-item evidence-missing">
                  <span className="evidence-label">Missing qualification fields</span>
                  <span>
                    Job title, industry, employee count, annual revenue, geography, budget, requested service,
                    pain point, explicit timeline, company size, buyer role, decision maker, competitors, and technical requirements.
                  </span>
                </div>
              </div>
            </details>
          </div>
        </aside>

        <nav className="lead-detail-navigation refined-navigation" aria-label="Lead detail sections">
          <div className="lead-detail-tabs" role="tablist" aria-label="Lead detail">
            {sections.map((section) => (
              <a
                key={section}
                className="lead-detail-tab"
                href="#"
                role="tab"
                aria-selected={section === "Email"}
                onClick={(event) => event.preventDefault()}
              >
                {section}
              </a>
            ))}
          </div>
          <button
            className={`lead-section-pin${pinned ? " is-pinned" : ""}`}
            type="button"
            aria-pressed={pinned}
            onClick={() => setPinned((value) => !value)}
          >
            <span aria-hidden="true">{pinned ? "◆" : "◇"}</span>
            {pinned ? "Pinned in navigation" : "Pin this section"}
          </button>
        </nav>

        <section className="lead-detail-panel refined-panel" role="tabpanel" aria-label="Email conversation">
          <section className="lead-email-panel refined-email-panel">
            <header className="lead-email-heading refined-email-heading">
              <div className="email-heading-copy">
                <span className="eyebrow">Email conversation</span>
                <h2>Avery Chen</h2>
                <p>Messages stay linked to their Gmail mailbox. Refresh imports new messages and re-checks lead qualification.</p>
              </div>
              <div className="lead-email-actions refined-actions">
                <button className="action-secondary" type="button">Ask Corral</button>
                <button className="action-primary" type="button">Compose</button>
                <button className="action-secondary refresh-action" type="button">
                  <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M13.2 7.1A5.3 5.3 0 0 0 4 3.4L2.8 4.6M2.8 4.6V1.9m0 2.7h2.7M2.8 8.9A5.3 5.3 0 0 0 12 12.6l1.2-1.2m0 0v2.7m0-2.7h-2.7" /></svg>
                  Refresh from Gmail
                </button>
              </div>
            </header>

            <div className="email-empty-state refined-empty">
              <span className="empty-icon" aria-hidden="true">
                <svg viewBox="0 0 32 32"><rect x="4.5" y="7" width="23" height="18" rx="3" /><path d="m6 9 10 8 10-8" /></svg>
              </span>
              <div className="empty-copy">
                <strong>No Gmail conversation linked yet</strong>
                <span>Check connected inboxes for messages, or compose a new email to start a conversation with Avery.</span>
              </div>
            </div>
          </section>
        </section>
      </article>
    </main>
  );
}
