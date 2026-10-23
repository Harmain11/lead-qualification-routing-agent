import "./_group.css";
import { sampleGmailAccounts } from "./_data";

function connectionStatusLabel(status: string) {
  if (status === "verified") return "Verified";
  if (["connected", "configured", "validating"].includes(status)) return "Needs verification";
  return "Needs authorization";
}

function isPlaceholderName(name: string) {
  return /^(pending|new)\s+(?:gmail\s+)?account$/i.test(name.trim());
}

function formatStartedAt(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "Authorization attempt";
  return `Started ${new Intl.DateTimeFormat(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
  }).format(date)}`;
}

function MailboxRow({ account, attention = false }: {
  account: (typeof sampleGmailAccounts)[number];
  attention?: boolean;
}) {
  const placeholder = isPlaceholderName(account.account_name);
  const name = placeholder ? "Google sign-in not completed" : account.account_name;
  const status = account.status === "verified" && !account.is_active
    ? "Inactive"
    : connectionStatusLabel(account.status);

  return (
    <div className={`gmail-mailbox-row${attention ? " is-attention" : ""}`}>
      <span className={`gmail-mailbox-mark${attention ? " is-attention" : ""}`} aria-hidden="true">G</span>
      <div className="gmail-mailbox-copy">
        <strong title={name}>{name}</strong>
        <small>{placeholder ? formatStartedAt(account.created_at) : "Available to Corral"}</small>
      </div>
      <span className={`gmail-mailbox-status${attention ? " is-attention" : ""}`}>{status}</span>
      {account.is_active && (
        <button type="button" className="gmail-mailbox-disconnect" aria-label={`Disconnect ${name}`}>
          Disconnect
        </button>
      )}
    </div>
  );
}

export function Refined() {
  const verifiedAccounts = sampleGmailAccounts.filter((account) => account.status === "verified" && account.is_active);
  const attentionAccounts = sampleGmailAccounts.filter((account) => !(account.status === "verified" && account.is_active));

  return (
    <main className="app-shell gmail-accounts-preview">
      <article className="integration-card provider-gmail state-verified">
        <div className="provider-head">
          <div className="provider-glyph">GM</div>
          <div className="provider-identity">
            <span>Email</span>
            <h2>Gmail</h2>
            <small className="provider-account-summary">Manage Corral mailboxes</small>
          </div>
          <b><i />Connected</b>
          <button type="button" className="provider-collapse-toggle" aria-label="Close Gmail details" aria-expanded="true">
            <span aria-hidden="true">−</span>
          </button>
        </div>
        <div className="provider-details">
          <p className="account-selection-note">
            Each verified mailbox stays separate. Choose which one Corral reads or sends from.
          </p>
          <section className="integration-guide gmail-mailbox-panel" aria-labelledby="gmail-mailbox-heading">
            <header className="gmail-mailbox-heading">
              <div>
                <span className="eyebrow">Mailbox settings</span>
                <h3 id="gmail-mailbox-heading">Gmail accounts</h3>
              </div>
              <button type="button" className="gmail-mailbox-add"><span aria-hidden="true">+</span> Add account</button>
            </header>

            <div className="gmail-mailbox-counts" aria-label={`${verifiedAccounts.length} verified, ${attentionAccounts.length} needing attention`}>
              <span><b>{verifiedAccounts.length}</b> ready to use</span>
              {attentionAccounts.length > 0 && <span className="needs-attention"><i />{attentionAccounts.length} need authorization</span>}
            </div>

            <div className="gmail-mailbox-section">
              <div className="gmail-mailbox-section-heading">
                <strong>Ready to use</strong>
                <span>{verifiedAccounts.length}</span>
              </div>
              {verifiedAccounts.length ? (
                <div className="gmail-mailbox-rows">
                  {verifiedAccounts.map((account) => <MailboxRow key={account.id} account={account} />)}
                </div>
              ) : (
                <div className="gmail-mailbox-empty">No verified mailbox yet. Add an account to get started.</div>
              )}
            </div>

            {attentionAccounts.length > 0 && (
              <details className="gmail-mailbox-attention" open={verifiedAccounts.length === 0}>
                <summary>
                  <span className="gmail-mailbox-attention-copy">
                    <strong>Needs attention</strong>
                    <small>Review each saved authorization attempt</small>
                  </span>
                  <b>{attentionAccounts.length}</b>
                  <span className="gmail-mailbox-chevron" aria-hidden="true" />
                </summary>
                <div className="gmail-mailbox-rows gmail-mailbox-pending-rows">
                  {attentionAccounts.map((account) => <MailboxRow key={account.id} account={account} attention />)}
                </div>
              </details>
            )}
          </section>
        </div>
      </article>
    </main>
  );
}
