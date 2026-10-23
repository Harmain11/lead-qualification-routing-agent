import "./_group.css";
import "./Panel.css";

export function Refined() {
  return (
    <main className="cloudtalk-preview-stage">
      <section className="cloudtalk-preview-window cloudtalk-refined" aria-label="Refined CloudTalk phone panel">
        <header className="cloudtalk-preview-header cloudtalk-refined-header">
          <div className="cloudtalk-preview-title">
            <strong>CloudTalk Phone</strong>
            <small>366193011001</small>
          </div>
          <div className="cloudtalk-preview-actions">
            <button className="cloudtalk-preview-alert-button" type="button">
              <svg viewBox="0 0 20 20" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                <path d="M15.5 8a5.5 5.5 0 0 0-11 0c0 6-2 6-2 7.5h15C17.5 14 15.5 14 15.5 8Z" />
                <path d="M8 18h4" />
              </svg>
              Enable alerts
            </button>
            <button className="cloudtalk-preview-close" type="button" aria-label="Close CloudTalk phone">×</button>
          </div>
        </header>
        <div className="cloudtalk-preview-content">
          <div className="cloudtalk-preview-note is-warning" role="status">
            <span className="cloudtalk-preview-note-icon" aria-hidden="true">!</span>
            <span>Permission wasn’t granted. Choose Allow in your browser prompt. CloudTalk has no separate alert setting.</span>
          </div>
          <CloudTalkSignInPreview />
        </div>
      </section>
      <div className="cloudtalk-preview-bubble"><span>CT</span>CloudTalk</div>
    </main>
  );
}

function CloudTalkSignInPreview() {
  return (
    <div className="cloudtalk-login-preview">
      <div className="cloudtalk-login-logo">CloudTalk</div>
      <h2 className="cloudtalk-login-copy">Sign in to continue to your phone</h2>
      <button className="cloudtalk-login-button" type="button">Continue to CloudTalk</button>
      <p className="cloudtalk-trial">Use your CloudTalk account or organization SSO.</p>
      <p className="cloudtalk-login-footer">Your phone session is managed by CloudTalk.</p>
    </div>
  );
}
