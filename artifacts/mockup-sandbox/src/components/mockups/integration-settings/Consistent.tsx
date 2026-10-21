import "./_group.css";
import "./Consistent.css";

export function Consistent() {
  return (
    <main className="integration-preview">
      <header className="integration-section-heading">
        <div>
          <span className="eyebrow">Integrations / Email</span>
          <h1>Connected integrations</h1>
        </div>
        <p>Manage the accounts and services Corral uses to qualify, route, and follow up with leads.</p>
      </header>
      <div className="integration-grid">
        <article className="integration-card state-connected">
          <div className="provider-head">
            <div className="provider-glyph">GM</div>
            <div className="provider-identity">
              <span>Email</span>
              <h2>Gmail</h2>
              <small className="provider-account-summary">sales@corral.example</small>
            </div>
            <b><i />Verified</b>
            <button className="provider-collapse-toggle" aria-label="Close Gmail details">−</button>
          </div>
          <div className="provider-details">
            <p>Secure connection to your Gmail mailbox. Corral reviews inbound messages and adds genuine sales inquiries to the right lead.</p>
            <p className="integration-direction"><strong>Automatic inbound email acquisition</strong></p>
            <div className="connection-facts">
              <span>Credential <b>Configured</b></span>
              <span>Verification <b>Verified</b></span>
              <span>Last checked <b>Today, 9:04 AM</b></span>
            </div>
            <div className="connection-state-path" aria-label="Connection state: Verified">
              <div className="complete"><i /><span>Disconnected</span><b /></div>
              <div className="complete"><i /><span>Authorizing</span><b /></div>
              <div className="complete"><i /><span>Credentials stored</span><b /></div>
              <div className="active"><i /><span>Verified</span></div>
            </div>
            <div className="card-actions integration-connection-actions">
              <button>Verify connection</button>
              <button className="button-quiet">Reconnect</button>
              <button className="button-quiet">Disconnect</button>
              <button className="button-quiet">Setup guide</button>
            </div>
            <section className="integration-guide lead-confirmation-setting">
              <div className="lead-confirmation-setting-copy">
                <span className="eyebrow">New-lead email</span>
                <strong>Automatic confirmation</strong>
                <p>Control confirmation emails only. New leads still follow the normal qualification and review rules.</p>
                <small>Using workspace default (Off).</small>
              </div>
              <div className="lead-confirmation-setting-actions">
                <button className="email-automation-toggle" role="switch" aria-checked="false">
                  <span className="email-automation-track" aria-hidden="true"><i /></span>
                  <span>Off</span>
                </button>
              </div>
            </section>
            <section className="integration-guide">
              <span className="eyebrow">Automatic inbound email acquisition</span>
              <strong>Enabled · automatic inbound</strong>
              <p>Automatically checks every 60 seconds, even when Corral is closed. New records appear without pressing Sync now.</p>
              <p>Last synced today at 9:02 AM · 124 records processed.</p>
              <p>Next sync at 9:05 AM.</p>
              <div className="card-actions">
                <button>Disable automatic inbound</button>
                <button className="button-quiet">Sync Gmail with AI</button>
              </div>
            </section>
            <section className="integration-guide">
              <span className="eyebrow">Connection briefing</span>
              <strong>Gmail messages are reviewed for lead intent.</strong>
              <p>Only relevant sales conversations are added to Corral. Routine newsletters and marketing emails are excluded.</p>
            </section>
          </div>
        </article>
      </div>
    </main>
  );
}
