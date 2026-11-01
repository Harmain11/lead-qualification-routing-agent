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
      <div className="gm-row" data-attention={attention || undefined} key={account.id}>
        <span className="gm-mark" aria-hidden="true">G</span>
        <div className="gm-copy">
          <strong title={name}>{name}</strong>
          <small>{placeholder ? formatStartedAt(account.created_at) : "Available to Corral"}</small>
        </div>
        <span className="gm-status">{status}</span>
        {canRemove && (
          <button
            type="button"
            className="gm-remove"
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

  const ordered = [...readyAccounts.map((account) => ({ account, attention: false })), ...attentionAccounts.map((account) => ({ account, attention: true }))];

  return (
    <section className="gmail-mailbox-panel gm" aria-labelledby="gmail-mailbox-heading">
      <header className="gm-head">
        <h3 id="gmail-mailbox-heading">Mailboxes</h3>
        {canConnect && (
          <button type="button" className="gm-add" onClick={onAdd} disabled={busy}>
            {connecting ? "Connecting…" : "Add account"}
          </button>
        )}
      </header>
      {ordered.length > 0 ? (
        <div className="gm-rows">{ordered.map(({ account, attention }) => renderAccount(account, attention))}</div>
      ) : (
        <p className="gm-empty">No mailbox yet. Add an account to start receiving leads.</p>
      )}
    </section>
  );
}
