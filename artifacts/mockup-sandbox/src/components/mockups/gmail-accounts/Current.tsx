import "./_group.css";
import { sampleGmailAccounts } from "./_data";

function connectionStatusLabel(status: string) {
  if (status === "verified") return "Verified";
  if (["connected", "configured", "validating"].includes(status)) return "Needs verification";
  return "Needs authorization";
}

export function Current() {
  const gmailConnections = sampleGmailAccounts;

  return (
    <main className="app-shell gmail-accounts-preview">
      <article className="integration-card provider-gmail state-verified">
        <div className="provider-head">
          <div className="provider-glyph">GM</div>
          <div className="provider-identity">
            <span>Email</span>
            <h2>Gmail</h2>
            <small className="provider-account-summary">sales@northstar.example</small>
          </div>
          <b><i />Connected</b>
          <button type="button" className="provider-collapse-toggle" aria-label="Close Gmail details" aria-expanded="true">
            <span aria-hidden="true">−</span>
          </button>
        </div>
        <div className="provider-details">
          <p className="account-selection-note">
            Connect more than one Gmail mailbox. Each account stays separate, and you choose which mailbox to read or send from in Corral.
          </p>
          <div className="integration-guide gmail-account-list">
            <span className="eyebrow">Connected Gmail accounts</span>
            {gmailConnections.map((gmailAccount) => (
              <div className="gmail-account-row" key={gmailAccount.id}>
                <div>
                  <strong>{gmailAccount.account_name}</strong>
                  <small>{connectionStatusLabel(gmailAccount.status)}</small>
                </div>
                {gmailAccount.is_active && (
                  <button type="button" className="button-quiet danger">Disconnect</button>
                )}
              </div>
            ))}
          </div>
        </div>
      </article>
    </main>
  );
}
