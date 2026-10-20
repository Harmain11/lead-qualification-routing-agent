import "./_group.css";
import "./Panel.css";

export function Current() {
  return (
    <main className="cloudtalk-preview-stage">
      <section className="cloudtalk-preview-window" aria-label="Current CloudTalk phone panel">
        <header className="cloudtalk-preview-header cloudtalk-current-header">
          <div className="cloudtalk-preview-title cloudtalk-current-title">
            <strong>CloudTalk Phone</strong>
            <small>366193011001</small>
            <small className="cloudtalk-current-error">Notification permission was not granted.</small>
          </div>
          <div className="cloudtalk-preview-actions">
            <button className="cloudtalk-preview-alert-button" type="button">Enable alerts</button>
            <button className="cloudtalk-preview-close" type="button" aria-label="Close CloudTalk phone">×</button>
          </div>
        </header>
        <div className="cloudtalk-preview-content">
          <p className="cloudtalk-preview-note cloudtalk-current-note">
            Use the header alert control for incoming-call notifications. CloudTalk’s embedded phone has no separate notification setting.
          </p>
          <p className="cloudtalk-preview-live-status cloudtalk-current-status">
            Sign in with your CloudTalk account or organization SSO. Your phone session is managed by CloudTalk.
          </p>
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
