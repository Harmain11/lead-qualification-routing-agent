import "./_group.css";

const keys = [
  ["1", ""], ["2", "ABC"], ["3", "DEF"],
  ["4", "GHI"], ["5", "JKL"], ["6", "MNO"],
  ["7", "PQRS"], ["8", "TUV"], ["9", "WXYZ"],
  ["+", ""], ["0", "+"], ["⌫", ""],
];

export function Polished() {
  return (
    <main className="dialer-preview direct-dialer-panel">
      <header className="dialer-panel-head">
        <div><span className="eyebrow">Direct calling</span><h2>Call a new number</h2></div>
        <span className="dialer-panel-count">One confirmed call at a time</span>
      </header>
      <p className="panel-copy">Choose a verified calling account and enter one number. Every call still requires an individual confirmation and remains subject to workspace consent and residency rules.</p>
      <form className="direct-dialer-form">
        <div className="direct-dialer-fields">
          <div className="direct-dialer-number-field">
            <label htmlFor="polished-phone">Phone number
              <input id="polished-phone" type="tel" value="+14155550123" readOnly />
            </label>
            <small>Use +country code format, for example +14155550123.</small>
            <div className="direct-dialer-keypad">
              <div className="direct-dialer-keypad-head"><span>Dial pad</span><button className="direct-dialer-clear" type="button">Clear</button></div>
              <div className="direct-dialer-keypad-grid" role="group" aria-label="Phone number keypad">
                {keys.map(([number, letters]) => (
                  <button className="direct-dialer-key" type="button" key={number}>
                    <strong>{number}</strong>{letters && <small>{letters}</small>}
                  </button>
                ))}
              </div>
            </div>
          </div>
          <div className="direct-dialer-account-field">
            <label htmlFor="polished-account">Calling account</label>
            <select id="polished-account" defaultValue="cloudtalk-main">
              <option value="cloudtalk-main">CloudTalk · Main line</option>
            </select>
            <div className="direct-dialer-status" role="status">
              <span aria-hidden="true" />
              <div><strong>CloudTalk verified account ready</strong><p>Main line is connected and available for a confirmed call.</p></div>
            </div>
          </div>
        </div>
        <label className="direct-dialer-confirm">
          <input type="checkbox" checked readOnly />
          <span><strong>I have permission to call this number and authorize this individual call.</strong><small>This confirmation is for one call only. Workspace do-not-call, consent, and residency safeguards still apply.</small></span>
        </label>
        <div className="direct-dialer-actions">
          <small>Calls are not attached to a lead unless you explicitly add one after the call.</small>
          <button type="button">Call number</button>
        </div>
      </form>
      <section className="direct-call-history">
        <div className="direct-call-history-head"><h3>Recent direct calls</h3><button type="button">Refresh</button></div>
        <div className="preview-call-row">
          <div><strong>+1 (415) 555-0188</strong><span>CloudTalk · 8 minutes ago</span></div>
          <span className="preview-call-status">Completed</span>
        </div>
      </section>
    </main>
  );
}
