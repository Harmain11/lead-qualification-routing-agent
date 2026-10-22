import "./_group.css";

export function Current() {
  return <main className="lead-agent-preview">
    <section className="lead-agent" aria-label="Assistant for Kathi Sitek">
      <header className="lead-agent-heading">
        <div className="lead-agent-mark" aria-hidden="true">CA</div>
        <div>
          <span className="eyebrow">Lead assistant</span>
          <h2>Work with Kathi Sitek</h2>
          <p>Ask Corral to help with this lead. Check any proposed changes, then choose the action you want.</p>
        </div>
      </header>
      <section className="lead-agent-quick-actions" aria-label="Quick lead actions">
        <div className="lead-agent-phone-card">
          <div className="lead-agent-phone-copy">
            <span className="eyebrow">Phone number</span>
            <strong>No number saved</strong>
          </div>
          <div className="lead-agent-quick-buttons">
            <button type="button" className="secondary-button">Add number with Corral</button>
          </div>
        </div>
        <button className="primary-button lead-agent-email-shortcut" type="button">Compose follow-up email</button>
        <small className="lead-agent-email-shortcut-note">Choose sender and recipient, format the message, then send it directly.</small>
      </section>
      <div className="lead-agent-transcript">
        <div className="lead-agent-welcome">
          <h3>What would you like to do with this lead?</h3>
          <p>I can answer from the record, update details, save notes and progress, compose emails, or open the right lead workflow. You’ll confirm email sends, CRM syncs, and deletion.</p>
          <div className="lead-agent-suggestions">
            <button type="button">Summarize this lead and suggest a next step</button>
            <button type="button">What details are still missing?</button>
            <button type="button">Write and send a follow-up email</button>
            <button type="button">I want to call this lead</button>
          </div>
        </div>
      </div>
      <form className="lead-agent-composer">
        <label htmlFor="lead-agent-input">Message Corral</label>
        <div>
          <textarea id="lead-agent-input" rows={5} placeholder="Ask about this lead or describe an update…" />
          <button className="primary-button" type="button">Send</button>
        </div>
        <small>Corral uses this lead’s record and recent history. Review proposed changes before applying them.</small>
      </form>
    </section>
  </main>;
}
