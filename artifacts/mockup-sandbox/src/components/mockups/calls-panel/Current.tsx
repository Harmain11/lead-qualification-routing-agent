import "./_group.css";

export function Current() {
  return (
    <main className="calls-preview">
      <section className="ops-panel calls-panel" aria-labelledby="calls-heading">
        <div className="ops-toolbar">
          <div>
            <span className="eyebrow">Calling &amp; voice agents</span>
            <h3 id="calls-heading">Calls &amp; voice activity</h3>
            <p>Lead number: +1 (415) 555-0123</p>
          </div>
          <button type="button" className="secondary-button">Sync recent calls</button>
        </div>

        <p className="notice" role="status">Consent is required before a call can be placed.</p>

        <form className="calls-form">
          <div className="ops-form-grid">
            <label>
              Calling provider
              <select defaultValue="cloudtalk-main">
                <option value="cloudtalk-main">CloudTalk · Main line</option>
              </select>
            </label>
          </div>
          <p>After confirmation, Corral prepares a CloudTalk Desktop handoff with this lead’s saved number. It does not dial through the embedded phone.</p>
          <label>
            CloudTalk caller ID
            <select defaultValue="+1-415-555-0100">
              <option value="+1-415-555-0100">+1 (415) 555-0100</option>
            </select>
            <small>Only caller IDs assigned to this CloudTalk agent can be used.</small>
          </label>
          <label className="ops-check">
            <input type="checkbox" disabled />
            <span>I confirm this individual call to the lead.</span>
          </label>
          <button type="submit" disabled>Prepare CloudTalk Desktop call</button>
        </form>

        <div className="calls-history">
          <h4>Recent activity</h4>
          <p className="ops-empty">No call activity recorded for this lead. Use Sync recent calls to import matching provider history.</p>
        </div>
        <small>History sync reads recent provider calls that match this lead number. AI insights load only when requested. Calls always require confirmation and pass consent checks.</small>
      </section>
    </main>
  );
}
