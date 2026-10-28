import "./_group.css";
import "./polished.css";

export function Polished() {
  return (
    <main className="calls-preview">
      <section className="ops-panel calls-panel calls-panel-polished" aria-labelledby="calls-heading-polished">
        <div className="ops-toolbar">
          <div className="calls-panel-heading">
            <span className="calls-heading-mark" aria-hidden="true">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                <path d="M7 4.8 9.2 8l-1.5 1.6a14 14 0 0 0 6.7 6.7L16 14.8l3.2 2.2-.7 3a1.5 1.5 0 0 1-1.7 1.1A17 17 0 0 1 3 7.3 1.5 1.5 0 0 1 4.1 5.6L7 4.8Z" />
                <path d="M14 5a5 5 0 0 1 5 5" />
                <path d="M14 2a8 8 0 0 1 8 8" />
              </svg>
            </span>
            <div className="calls-heading-copy">
              <span className="eyebrow">Calling &amp; voice agents</span>
              <h3 id="calls-heading-polished">Calls &amp; voice activity</h3>
              <p className="calls-lead-number">Lead number: +1 (415) 555-0123</p>
            </div>
          </div>
          <button type="button" className="secondary-button calls-sync">Sync recent calls</button>
        </div>

        <div className="calls-consent-notice" role="status">
          <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <path d="M10 2.7 18 17H2L10 2.7Z" />
            <path d="M10 7.2v4.5M10 14.5h.01" />
          </svg>
          <span>Consent is required before a call can be placed.</span>
        </div>

        <form className="calls-form">
          <div className="ops-form-grid calls-form-grid">
            <label>
              Calling provider
              <select defaultValue="cloudtalk-main">
                <option value="cloudtalk-main">CloudTalk · Main line</option>
              </select>
            </label>
            <label>
              CloudTalk caller ID
              <select defaultValue="+1-415-555-0100">
                <option value="+1-415-555-0100">+1 (415) 555-0100</option>
              </select>
              <small>Only caller IDs assigned to this CloudTalk agent can be used.</small>
            </label>
          </div>
          <p className="calls-provider-note">After confirmation, Corral prepares a CloudTalk Desktop handoff with this lead’s saved number. It does not dial through the embedded phone.</p>
          <label className="ops-check calls-confirm">
            <input type="checkbox" disabled />
            <span>
              <strong>Confirm this individual call</strong>
              <small>I confirm this individual call to the lead. Consent checks still apply.</small>
            </span>
          </label>
          <button className="calls-submit" type="submit" disabled>Prepare CloudTalk Desktop call</button>
        </form>

        <div className="calls-history">
          <div className="calls-history-head"><h4>Recent activity</h4></div>
          <div className="calls-history-empty">
            <span aria-hidden="true">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
                <path d="M7 4.8 9.2 8l-1.5 1.6a14 14 0 0 0 6.7 6.7L16 14.8l3.2 2.2-.7 3a1.5 1.5 0 0 1-1.7 1.1A17 17 0 0 1 3 7.3 1.5 1.5 0 0 1 4.1 5.6L7 4.8Z" />
              </svg>
            </span>
            <span>
              <strong>No call activity yet</strong>
              <small>Use Sync recent calls to import provider activity that matches this lead.</small>
            </span>
          </div>
        </div>
        <small>History sync reads recent provider calls that match this lead number. AI insights load only when requested. Calls always require confirmation and pass consent checks.</small>
      </section>
    </main>
  );
}
