import { useEffect, useState } from "react";
import {
  getLeadPipelineHistory,
  listIntegrations,
  syncLeadGmailConversation,
} from "../api";
import type { GmailComposerSeed } from "./EmailComposer";
import { EmailComposer } from "./EmailComposer";
import type { Lead, LeadPipelineEvent, ProviderConnection } from "../types";

const GMAIL_READONLY_SCOPE = "https://www.googleapis.com/auth/gmail.readonly";

function stringDetail(details: Record<string, unknown>, key: string): string {
  const value = details[key];
  return typeof value === "string" ? value : "";
}

export function LeadEmailPanel({
  lead,
  active,
  canView,
  canSend,
  onConversationSynced,
}: {
  lead: Lead;
  active: boolean;
  canView: boolean;
  canSend: boolean;
  onConversationSynced: () => void;
}) {
  const [accounts, setAccounts] = useState<ProviderConnection[]>([]);
  const [events, setEvents] = useState<LeadPipelineEvent[]>([]);
  const [loading, setLoading] = useState(false);
  const [accountsLoading, setAccountsLoading] = useState(true);
  const [syncBusy, setSyncBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [revision, setRevision] = useState(0);
  const [composeSeed, setComposeSeed] = useState<GmailComposerSeed | null>(null);
  const [composerRevision, setComposerRevision] = useState(0);

  useEffect(() => {
    if (!canView) return;
    let alive = true;
    setAccountsLoading(true);
    listIntegrations().then((cards) => {
      if (!alive) return;
      const gmail = cards.find((card) => card.provider === "gmail");
      const rows = gmail?.connections.length ? gmail.connections : gmail?.connection ? [gmail.connection] : [];
      setAccounts(rows);
      setAccountsLoading(false);
    }).catch((reason) => {
      if (!alive) return;
      setError(reason instanceof Error ? reason.message : "Gmail accounts could not be loaded.");
      setAccountsLoading(false);
    });
    return () => { alive = false; };
  }, [canView]);

  useEffect(() => {
    if (!active || !canView) return;
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    getLeadPipelineHistory(lead.id, controller.signal).then((rows) => {
      if (!controller.signal.aborted) {
        setEvents(rows.filter((event) => event.stage === "gmail_message"));
        setLoading(false);
      }
    }).catch((reason) => {
      if (!controller.signal.aborted) {
        setError(reason instanceof Error ? reason.message : "Email history could not be loaded.");
        setLoading(false);
      }
    });
    return () => controller.abort();
  }, [active, canView, lead.id, revision]);

  if (!canView) return null;

  const groups = new Map<string, LeadPipelineEvent[]>();
  for (const event of events) {
    const threadId = stringDetail(event.details, "thread_id") || `message-${event.id}`;
    groups.set(threadId, [...(groups.get(threadId) ?? []), event]);
  }
  const orderedThreads = [...groups.entries()]
    .map(([threadId, messages]) => ({
      threadId,
      messages: messages.sort((a, b) =>
        new Date(a.created_at).getTime() - new Date(b.created_at).getTime(),
      ),
    }))
    .sort((a, b) =>
      new Date(b.messages[b.messages.length - 1]?.created_at ?? 0).getTime()
      - new Date(a.messages[a.messages.length - 1]?.created_at ?? 0).getTime(),
    );
  const activeAccounts = accounts.filter((account) =>
    account.is_active && account.status === "verified" && account.scopes.includes(GMAIL_READONLY_SCOPE),
  );

  async function refreshConversation() {
    setSyncBusy(true);
    setError(null);
    setNotice(null);
    try {
      const result = await syncLeadGmailConversation(lead.id);
      setRevision((current) => current + 1);
      onConversationSynced();
      setNotice(result.messages_added
        ? `Imported ${result.messages_added} new email${result.messages_added === 1 ? "" : "s"} from ${result.threads_checked} thread${result.threads_checked === 1 ? "" : "s"}.`
        : `No new email found across ${result.threads_checked} thread${result.threads_checked === 1 ? "" : "s"}.`);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Gmail conversation could not be refreshed.");
    } finally {
      setSyncBusy(false);
    }
  }

  function replyTo(event: LeadPipelineEvent) {
    const details = event.details;
    const recipients = stringDetail(details, "recipients").split(",")[0].trim();
    const recipient = stringDetail(details, "direction") === "outbound"
      ? recipients
      : stringDetail(details, "sender");
    const originalSubject = stringDetail(details, "subject");
    setComposeSeed({
      recipient,
      subject: originalSubject && !/^re:/i.test(originalSubject) ? `Re: ${originalSubject}` : originalSubject,
      threadId: stringDetail(details, "thread_id") || undefined,
      inReplyTo: stringDetail(details, "rfc_message_id") || undefined,
    });
    setComposerRevision((current) => current + 1);
  }

  return <section className="lead-email-panel">
    <header className="lead-email-heading">
      <div><span className="eyebrow">Email conversation</span><h2>{lead.full_name}</h2><p>Messages stay linked to their Gmail mailbox. Refresh imports new messages and re-checks lead qualification.</p></div>
      <div className="lead-email-actions">
        <button className="secondary-button" type="button" onClick={() => {
          setComposeSeed({ recipient: lead.email || "" });
          setComposerRevision((current) => current + 1);
        }}>Compose</button>
        <button className="secondary-button" type="button" onClick={() => void refreshConversation()} disabled={syncBusy || activeAccounts.length === 0}>
          {syncBusy ? "Refreshing…" : "Refresh from Gmail"}
        </button>
      </div>
    </header>
    {accountsLoading && <p className="email-status">Loading connected Gmail accounts…</p>}
    {!accountsLoading && activeAccounts.length === 0 && <div className="email-empty-state"><strong>No verified Gmail account is available.</strong><span>Connect a Gmail mailbox in Integrations to view and refresh lead conversations.</span></div>}
    {error && <div className="notice" role="alert">{error}</div>}
    {notice && <div className="notice" role="status">{notice}</div>}
    {composeSeed && <EmailComposer
      key={composerRevision}
      accounts={accounts}
      canSend={canSend}
      leadId={lead.id}
      seed={composeSeed}
      onClose={() => setComposeSeed(null)}
      onSent={() => {
        setRevision((current) => current + 1);
        setComposeSeed(null);
      }}
    />}
    {loading && <p className="email-status">Loading lead email history…</p>}
    {!loading && orderedThreads.length === 0 && activeAccounts.length > 0 && <div className="email-empty-state"><strong>No email has been recorded for this lead.</strong><span>Use Refresh from Gmail to check connected inboxes, or compose a new message.</span></div>}
    <div className="lead-email-thread-list">
      {orderedThreads.map(({ threadId, messages }) => {
        const subject = stringDetail(messages[messages.length - 1]?.details ?? {}, "subject") || "(no subject)";
        return <section className="lead-email-thread" key={threadId}>
          <header><div><span className="eyebrow">Gmail thread</span><h3>{subject}</h3></div><span>{messages.length} {messages.length === 1 ? "email" : "emails"}</span></header>
          {messages.map((event) => {
            const details = event.details;
            const direction = stringDetail(details, "direction");
            const body = stringDetail(details, "body");
            const sender = stringDetail(details, "sender");
            const recipients = stringDetail(details, "recipients");
            const mailboxId = stringDetail(details, "gmail_connection_id");
            const mailboxName = accounts.find((account) => account.id === mailboxId)?.account_name;
            return <article className={`lead-email-message${direction === "outbound" ? " is-outbound" : ""}`} key={event.id}>
              <header><div><span className="eyebrow">{direction === "outbound" ? "Sent" : "Received"}</span></div>
                <time>{new Date(event.created_at).toLocaleString()}</time></header>
              <div className="lead-email-addresses"><span>From <b>{sender || "Unknown"}</b></span>{recipients && <span>To <b>{recipients}</b></span>}{mailboxName && <span>Mailbox <b>{mailboxName}</b></span>}</div>
              <p className="lead-email-body">{body || "No message text was stored."}</p>
              {canSend && <button className="button-quiet" type="button" onClick={() => replyTo(event)}>Reply</button>}
            </article>;
          })}
        </section>;
      })}
    </div>
  </section>;
}
