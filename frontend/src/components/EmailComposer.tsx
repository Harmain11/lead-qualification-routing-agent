import { useEffect, useState, type FormEvent } from "react";
import { sendGmailEmail } from "../api";
import type { GmailSendResult, ProviderConnection } from "../types";

const GMAIL_SEND_SCOPE = "https://www.googleapis.com/auth/gmail.send";

export type GmailComposerSeed = {
  recipient?: string;
  subject?: string;
  threadId?: string;
  inReplyTo?: string;
  connectionId?: string;
};

type Props = {
  accounts: ProviderConnection[];
  canSend: boolean;
  leadId?: string;
  seed?: GmailComposerSeed;
  onSent?: (result: GmailSendResult) => void;
};

export function EmailComposer({ accounts, canSend, leadId, seed, onSent }: Props) {
  const sendableAccounts = accounts.filter((account) =>
    account.is_active && account.status === "verified" && account.scopes.includes(GMAIL_SEND_SCOPE),
  );
  const [connectionId, setConnectionId] = useState(
    seed?.connectionId ?? sendableAccounts[0]?.id ?? "",
  );
  const [recipient, setRecipient] = useState(seed?.recipient ?? "");
  const [subject, setSubject] = useState(seed?.subject ?? "");
  const [body, setBody] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!sendableAccounts.some((account) => account.id === connectionId)) {
      setConnectionId(sendableAccounts[0]?.id ?? "");
    }
  }, [accounts, connectionId]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!canSend || !connectionId || busy) return;
    setBusy(true);
    setNotice(null);
    setError(null);
    try {
      const result = await sendGmailEmail({
        connection_id: connectionId,
        recipient: recipient.trim(),
        subject: subject.trim(),
        body: body.trim(),
        ...(leadId ? { lead_id: leadId } : {}),
        ...(seed?.threadId ? { thread_id: seed.threadId } : {}),
        ...(seed?.inReplyTo ? { in_reply_to: seed.inReplyTo } : {}),
      });
      const account = sendableAccounts.find((item) => item.id === connectionId);
      setNotice(`Email sent from ${account?.account_name ?? result.sender}.`);
      setBody("");
      onSent?.(result);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Email could not be sent.");
    } finally {
      setBusy(false);
    }
  }

  if (!canSend) {
    return <div className="email-composer-empty">You can read this conversation. Sending email requires lead edit permission.</div>;
  }
  if (sendableAccounts.length === 0) {
    return <div className="email-composer-empty">No verified Gmail account has send permission. Reconnect an account from Integrations to enable sending.</div>;
  }

  return <form className="email-composer" onSubmit={(event) => void submit(event)}>
    <div className="email-composer-heading">
      <div><span className="eyebrow">{seed?.threadId ? "Reply" : "Compose"}</span><h3>{seed?.threadId ? "Reply to email" : "New email"}</h3></div>
      <small>Review the recipient and message before sending.</small>
    </div>
    <label>From
      <select value={connectionId} onChange={(event) => setConnectionId(event.target.value)}>
        {sendableAccounts.map((account) => <option key={account.id} value={account.id}>{account.account_name}</option>)}
      </select>
    </label>
    <label>To
      <input value={recipient} onChange={(event) => setRecipient(event.target.value)} maxLength={1024} autoComplete="email" placeholder="name@example.com" required />
      <small>Use one or more addresses, separated by commas.</small>
    </label>
    <label>Subject
      <input value={subject} onChange={(event) => setSubject(event.target.value)} maxLength={180} required />
    </label>
    <label>Message
      <textarea value={body} onChange={(event) => setBody(event.target.value)} rows={7} maxLength={8000} required />
    </label>
    {error && <div className="notice" role="alert">{error}</div>}
    {notice && <div className="notice" role="status">{notice}</div>}
    <button className="primary-button" type="submit" disabled={busy || !connectionId || !recipient.trim() || !subject.trim() || !body.trim()}>
      {busy ? "Sending…" : "Send email"}
    </button>
  </form>;
}
