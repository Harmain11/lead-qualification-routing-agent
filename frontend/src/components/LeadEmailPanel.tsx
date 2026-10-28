import { useEffect, useState } from "react";
import {
  getLeadPipelineHistory,
  listIntegrations,
  syncLeadGmailConversation,
} from "../api";
import type { GmailComposerSeed } from "./EmailComposer";
import { EmailComposer } from "./EmailComposer";
import { MeetingOfferDrafts } from "./MeetingOfferDrafts";
import { ProviderEmailBody } from "./ProviderEmailBody";
import type { Lead, LeadPipelineEvent, ProviderConnection } from "../types";
import { dedupeGmailEvents } from "../utils/gmailEvents";

const GMAIL_READONLY_SCOPE = "https://www.googleapis.com/auth/gmail.readonly";

function stringDetail(details: Record<string, unknown>, key: string): string {
  const value = details[key];
  return typeof value === "string" ? value : "";
}

function threadIdForEvent(event: LeadPipelineEvent): string {
  return (
    stringDetail(event.details, "thread_id") ||
    stringDetail(event.details, "gmail_thread_id")
  );
}

function LeadEmailEmptyState({
  title,
  description,
}: {
  title: string;
  description: string;
}) {
  return <div className="email-empty-state lead-email-empty-state" role="status">
    <span className="lead-email-empty-icon" aria-hidden="true">
      <svg viewBox="0 0 24 24" fill="none">
        <path d="M3.75 6.75h16.5v10.5H3.75z" />
        <path d="m4.5 7.5 7.5 6 7.5-6" />
      </svg>
    </span>
    <div className="lead-email-empty-copy">
      <strong>{title}</strong>
      <span>{description}</span>
    </div>
  </div>;
}

export function LeadEmailPanel({
  lead,
  active,
  canView,
  canSend,
  onConversationSynced,
  onAskAgent,
}: {
  lead: Lead;
  active: boolean;
  canView: boolean;
  canSend: boolean;
  onConversationSynced: () => void;
  onAskAgent?: () => void;
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
    setEvents([]);
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
  for (const event of dedupeGmailEvents(events)) {
    const threadId = threadIdForEvent(event) || `message-${event.id}`;
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
      threadId: threadIdForEvent(event) || undefined,
      inReplyTo: stringDetail(details, "rfc_message_id") || undefined,
    });
    setComposerRevision((current) => current + 1);
  }

  return <section className="lead-email-panel">
    <header className="lead-email-heading">
      <div><span className="eyebrow">Email conversation</span><h2>{lead.full_name}</h2><p>Review this lead’s Gmail history or start a new conversation.</p></div>
      <div className="lead-email-actions">
        {onAskAgent && <button className="secondary-button" type="button" onClick={onAskAgent}>Ask Corral</button>}
        <button className="primary-button" type="button" onClick={() => {
          setComposeSeed({ recipient: lead.email?.trim() || "" });
          setComposerRevision((current) => current + 1);
        }}>Compose</button>
        <button className="secondary-button" type="button" onClick={() => void refreshConversation()} disabled={syncBusy || activeAccounts.length === 0}>
          {syncBusy ? "Refreshing…" : "Refresh from Gmail"}
        </button>
      </div>
    </header>
    {accountsLoading && <p className="email-status lead-email-status" role="status">Checking connected Gmail accounts…</p>}
    {!accountsLoading && !loading && !error && activeAccounts.length === 0 && orderedThreads.length === 0 && <LeadEmailEmptyState
      title="No verified Gmail mailbox"
      description="Connect a verified Gmail mailbox in Integrations to refresh and view conversations for this lead."
    />}
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
    <MeetingOfferDrafts
      leadId={lead.id}
      leadEmail={lead.email}
      active={active}
      canSend={canSend}
      onSent={() => {
        setRevision((current) => current + 1);
        onConversationSynced();
      }}
    />
    {!accountsLoading && loading && <p className="email-status lead-email-status" role="status">Loading this lead’s email history…</p>}
    {!accountsLoading && !loading && !error && orderedThreads.length === 0 && activeAccounts.length > 0 && <LeadEmailEmptyState
      title="No conversation linked yet"
      description="Refresh Gmail to check connected inboxes, or compose a new email to start this conversation."
    />}
    <div className="lead-email-thread-list">
      {!loading && orderedThreads.map(({ threadId, messages }) => {
        const subject = stringDetail(messages[messages.length - 1]?.details ?? {}, "subject") || "(no subject)";
        return <details className="lead-email-thread" key={threadId}>
          <summary>
            <span className="lead-email-thread-heading"><span className="eyebrow">Gmail thread</span><strong>{subject}</strong></span>
            <span className="lead-email-thread-summary-meta">{messages.length} {messages.length === 1 ? "email" : "emails"}</span>
          </summary>
          <div className="lead-email-thread-messages">{messages.map((event) => {
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
              <ProviderEmailBody content={body} />
              {canSend && <button className="button-quiet" type="button" onClick={() => replyTo(event)}>Reply</button>}
            </article>;
          })}</div>
        </details>;
      })}
    </div>
  </section>;
}
