import type { ProviderConnection } from "../types";

type GmailAccountsPanelProps = {
  accounts: ProviderConnection[];
  canConnect: boolean;
  canDisconnect: boolean;
  busy: boolean;
  busyAccountId: string | null;
  connecting: boolean;
  onAdd: () => void;
  onDisconnect: (account: ProviderConnection) => void;
  statusLabel: (status?: string) => string;
};

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

export function GmailAccountsPanel({
  accounts,
  canConnect,
  canDisconnect,
  busy,
  busyAccountId,
  connecting,
  onAdd,
  onDisconnect,
  statusLabel,
}: GmailAccountsPanelProps) {
  const readyAccounts = accounts.filter((account) => account.status === "verified" && account.is_active);
  const attentionAccounts = accounts.filter((account) => !(account.status === "verified" && account.is_active));

  function renderAccount(account: ProviderConnection, attention = false) {
    const placeholder = isPlaceholderName(account.account_name);
    const name = placeholder
      ? "Google sign-in not completed"
      : account.account_name.trim() || "Gmail mailbox";
    const status = account.status === "verified" && !account.is_active
      ? "Inactive"
      : statusLabel(account.status);
    const canRemove = canDisconnect && account.is_active;

    return (
      <div className={`gmail-mailbox-row${attention ? " is-attention" : ""}`} key={account.id}>
        <span className={`gmail-mailbox-mark${attention ? " is-attention" : ""}`} aria-hidden="true">G</span>
        <div className="gmail-mailbox-copy">
          <strong title={name}>{name}</strong>
          <small>{placeholder ? formatStartedAt(account.created_at) : "Available to Corral"}</small>
        </div>
        <span className={`gmail-mailbox-status${attention ? " is-attention" : ""}`}>{status}</span>
        {canRemove && (
          <button
            type="button"
            className="gmail-mailbox-disconnect"
            aria-label={`Disconnect ${name}`}
            onClick={() => onDisconnect(account)}
            disabled={busy}
          >
            {busyAccountId === account.id ? "Disconnecting…" : "Disconnect"}
          </button>
        )}
      </div>
    );
  }

  return (
    <section className="integration-guide gmail-mailbox-panel" aria-labelledby="gmail-mailbox-heading">
      <header className="gmail-mailbox-heading">
        <div>
          <span className="eyebrow">Mailbox settings</span>
          <h3 id="gmail-mailbox-heading">Gmail accounts</h3>
        </div>
        {canConnect && (
          <button type="button" className="gmail-mailbox-add" onClick={onAdd} disabled={busy}>
            <span aria-hidden="true">+</span> {connecting ? "Connecting…" : "Add account"}
          </button>
        )}
      </header>

      <div className="gmail-mailbox-counts" aria-label={`${readyAccounts.length} ready to use, ${attentionAccounts.length} needing attention`}>
        <span className="gmail-mailbox-ready-count"><b>{readyAccounts.length}</b> ready to use</span>
        {attentionAccounts.length > 0 && (
          <span className="gmail-mailbox-attention-count"><i />{attentionAccounts.length} need attention</span>
        )}
      </div>

      <div className="gmail-mailbox-section">
        <div className="gmail-mailbox-section-heading">
          <strong>Ready to use</strong>
          <span>{readyAccounts.length}</span>
        </div>
        {readyAccounts.length > 0 ? (
          <div className="gmail-mailbox-rows">
            {readyAccounts.map((account) => renderAccount(account))}
          </div>
        ) : (
          <div className="gmail-mailbox-empty">No verified mailbox yet. Add an account to get started.</div>
        )}
      </div>

      {attentionAccounts.length > 0 && (
        <details className="gmail-mailbox-attention" open={readyAccounts.length === 0}>
          <summary>
            <span className="gmail-mailbox-attention-copy">
              <strong>Needs attention</strong>
              <small>Saved accounts that are not ready yet</small>
            </span>
            <b>{attentionAccounts.length}</b>
            <span className="gmail-mailbox-chevron" aria-hidden="true" />
          </summary>
          <div className="gmail-mailbox-rows gmail-mailbox-pending-rows">
            {attentionAccounts.map((account) => renderAccount(account, true))}
          </div>
        </details>
      )}
    </section>
  );
}
