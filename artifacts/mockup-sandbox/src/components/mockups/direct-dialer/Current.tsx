import "./_group.css";

const keys = [
  ["1", ""], ["2", "ABC"], ["3", "DEF"],
  ["4", "GHI"], ["5", "JKL"], ["6", "MNO"],
  ["7", "PQRS"], ["8", "TUV"], ["9", "WXYZ"],
  ["+", ""], ["0", "+"], ["⌫", ""],
];

export function Current() {
  return (
    <main className="dialer-preview direct-dialer-panel">
      <header className="dialer-panel-head">
        <div><span className="eyebrow">Direct calling</span><h2>Call a new number</h2></div>
        <span className="dialer-panel-count">One confirmed call at a time</span>
      </header>
      <p className="panel-copy">Choose a calling account and enter a number to place a direct call.</p>
      <form className="direct-dialer-form legacy-form">
        <label className="legacy-field">Phone number
          <input type="tel" value="+14155550123" readOnly />
          <small>Include the + country code.</small>
        </label>
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
        <label className="legacy-field">Calling account
          <select defaultValue="cloudtalk-main">
            <option value="cloudtalk-main">CloudTalk · Main line</option>
          </select>
        </label>
        <label className="legacy-field">Your rep number (optional override)
          <input type="tel" value="+14155550222" readOnly />
          <small>Leave blank to use the rep number saved in Integrations.</small>
        </label>
        <label className="direct-dialer-confirm">
          <input type="checkbox" checked readOnly />
          <span>I have permission to call this number and confirm this one individual call.</span>
        </label>
        <button className="legacy-submit" type="button">Call number</button>
      </form>
    </main>
  );
}
