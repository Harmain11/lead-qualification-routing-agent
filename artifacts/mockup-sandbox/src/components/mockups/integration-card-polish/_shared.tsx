type MailboxAccount = {
  id: string;
  account_name: string;
  status: string;
  is_active: boolean;
  created_at: string;
};

const accounts: MailboxAccount[] = [
  {
    id: "verified",
    account_name: "inbox@northstar.example",
    status: "verified",
    is_active: true,
    created_at: "2026-10-20T10:15:00Z",
  },
  ...Array.from({ length: 7 }, (_, index) => ({
    id: `attention-${index + 1}`,
    account_name: "Google sign-in not completed",
    status: index === 0 ? "awaiting_authorization" : "needs_reauthorization",
    is_active: false,
    created_at: `2026-10-${String(19 - index).padStart(2, "0")}T08:30:00Z`,
  })),
];

function statusLabel(status: string) {
  if (status === "verified") return "Verified";
  if (["connected", "configured", "validating"].includes(status)) {
    return "Needs verification";
  }
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

function GmailAccountsPanel() {
  const readyAccounts = accounts.filter(
    (account) => account.status === "verified" && account.is_active,
  );
  const attentionAccounts = accounts.filter(
    (account) => !(account.status === "verified" && account.is_active),
  );

  function renderAccount(account: MailboxAccount, attention = false) {
    const placeholder = isPlaceholderName(account.account_name);
    const name = placeholder
      ? "Google sign-in not completed"
      : account.account_name.trim() || "Gmail mailbox";
    const label =
      account.status === "verified" && !account.is_active
        ? "Inactive"
        : statusLabel(account.status);

    return (
      <div
        className={`gmail-mailbox-row${attention ? " is-attention" : ""}`}
        key={account.id}
      >
        <span
          className={`gmail-mailbox-mark${attention ? " is-attention" : ""}`}
          aria-hidden="true"
        >
          G
        </span>
        <div className="gmail-mailbox-copy">
          <strong title={name}>{name}</strong>
          <small>
            {placeholder
              ? formatStartedAt(account.created_at)
              : "Available to Corral"}
          </small>
        </div>
        <span
          className={`gmail-mailbox-status${attention ? " is-attention" : ""}`}
        >
          {label}
        </span>
        {!attention && (
          <button type="button" className="gmail-mailbox-disconnect">
            Disconnect
          </button>
        )}
      </div>
    );
  }

  return (
    <section className="integration-guide gmail-mailbox-panel">
      <header className="gmail-mailbox-heading">
        <div>
          <span className="eyebrow">Mailbox settings</span>
          <h3>Gmail accounts</h3>
        </div>
        <button type="button" className="gmail-mailbox-add">
          <span aria-hidden="true">+</span> Add account
        </button>
      </header>
      <div className="gmail-mailbox-counts">
        <span className="gmail-mailbox-ready-count">
          <b>{readyAccounts.length}</b> ready to use
        </span>
        <span className="gmail-mailbox-attention-count">
          <i /> {attentionAccounts.length} need attention
        </span>
      </div>
      <div className="gmail-mailbox-section">
        <div className="gmail-mailbox-section-heading">
          <strong>Ready to use</strong>
          <span>{readyAccounts.length}</span>
        </div>
        <div className="gmail-mailbox-rows">
          {readyAccounts.map((account) => renderAccount(account))}
        </div>
      </div>
      <details className="gmail-mailbox-attention">
        <summary>
          <span className="gmail-mailbox-attention-copy">
            <strong>Needs attention</strong>
            <small>Saved accounts that are not ready yet</small>
          </span>
          <b>{attentionAccounts.length}</b>
          <span className="gmail-mailbox-chevron" aria-hidden="true" />
        </summary>
        <div className="gmail-mailbox-rows gmail-mailbox-pending-rows">
          {attentionAccounts.slice(0, 2).map((account) =>
            renderAccount(account, true),
          )}
          <small className="gmail-mailbox-more">
            + {attentionAccounts.length - 2} more accounts need attention
          </small>
        </div>
      </details>
    </section>
  );
}

export function ExpandedGmailCard() {
  return (
    <article className="integration-card provider-gmail state-verified">
      <div className="provider-head">
        <div className="provider-glyph">GM</div>
        <div className="provider-identity">
          <span>Email</span>
          <h2>Gmail</h2>
          <small className="provider-account-summary">
            inbox@northstar.example
          </small>
        </div>
        <b className="provider-status">
          <i /> Verified
        </b>
        <button
          type="button"
          className="provider-collapse-toggle"
          aria-label="Close Gmail details"
          aria-expanded="true"
        >
          <span aria-hidden="true">−</span>
        </button>
      </div>
      <div className="provider-details">
        <p className="account-selection-note">
          Sign in with Google to read the intake mailbox and send one
          confirmation to new leads.
        </p>
        <p className="account-selection-note">
          Connect more than one Gmail mailbox. Each account stays separate, and
          you choose which mailbox to read or send from in Corral.
        </p>
        <GmailAccountsPanel />
        <p className="integration-direction">
          <strong>Automatic inbound email acquisition</strong>
        </p>
        <div className="connection-facts">
          <span>
            Credential <b>configured</b>
          </span>
          <span>
            Verification <b>verified</b>
          </span>
          <span>
            Last checked <b>Oct 20, 2026 · 12:45 PM</b>
          </span>
        </div>
        <div className="connection-state-path">
          {["Connected", "Configured", "Verified", "Ready"].map(
            (label, index) => (
              <div className={index < 3 ? "complete" : "active"} key={label}>
                <i />
                <span>{label}</span>
                {index < 3 && <b />}
              </div>
            ),
          )}
        </div>
        <div className="card-actions integration-connection-actions">
          <button type="button">Verify connection</button>
          <button type="button" className="button-quiet">
            Reconnect
          </button>
          <button type="button" className="button-quiet danger">
            Disconnect
          </button>
          <button type="button" className="button-quiet">
            Setup guide
          </button>
        </div>
      </div>
    </article>
  );
}
