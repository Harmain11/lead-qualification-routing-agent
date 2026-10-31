import { useCallback, useEffect, useRef, useState } from "react";
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
import { dedupeGmailEvents, latestGmailReplyTarget } from "../utils/gmailEvents";
import { Button, Monogram } from "../v3/ui/primitives";
import { Icon } from "../v3/ui/Icon";

const GMAIL_READONLY_SCOPE = "https://www.googleapis.com/auth/gmail.readonly";
const GMAIL_SEND_SCOPE = "https://www.googleapis.com/auth/gmail.send";

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
  hideComposeButton = false,
}: {
  lead: Lead;
  active: boolean;
  canView: boolean;
  canSend: boolean;
  onConversationSynced: () => void;
  onAskAgent?: (prompt?: string) => void;
  hideComposeButton?: boolean;
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
  const syncInFlight = useRef(false);
  const onConversationSyncedRef = useRef(onConversationSynced);
  onConversationSyncedRef.current = onConversationSynced;

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
  const replyTarget = latestGmailReplyTarget(events, lead.email, accounts.filter((account) =>
    account.is_active && account.status === "verified" && account.scopes.includes(GMAIL_SEND_SCOPE),
  ).map((account) => account.id));

  const refreshConversation = useCallback(async (manual = true) => {
    if (syncInFlight.current) return;
    syncInFlight.current = true;
    if (manual) {
      setSyncBusy(true);
      setError(null);
      setNotice(null);
    }
    try {
      const result = await syncLeadGmailConversation(lead.id);
      setRevision((current) => current + 1);
      onConversationSyncedRef.current();
      if (manual || result.messages_added || result.profile_updated_fields?.length) {
        const message = result.messages_added
          ? `Imported ${result.messages_added} new email${result.messages_added === 1 ? "" : "s"} from ${result.threads_checked} thread${result.threads_checked === 1 ? "" : "s"}.`
          : `No new email found across ${result.threads_checked} thread${result.threads_checked === 1 ? "" : "s"}.`;
        setNotice(`${message}${result.profile_updated_fields?.length ? ` Updated ${result.profile_updated_fields.join(" and ")} from the lead’s own message.` : ""}`);
      }
    } catch (reason) {
      if (manual) setError(reason instanceof Error ? reason.message : "Gmail conversation could not be refreshed.");
    } finally {
      syncInFlight.current = false;
      if (manual) setSyncBusy(false);
    }
  }, [lead.id]);

  useEffect(() => {
    if (!active || !canView || activeAccounts.length === 0) return;
    const timer = window.setInterval(() => {
      if (document.visibilityState === "visible") void refreshConversation(false);
    }, 60_000);
    return () => window.clearInterval(timer);
  }, [active, canView, activeAccounts.length, refreshConversation]);

  if (!canView) return null;

  function replyTo(event: LeadPipelineEvent) {
    const details = event.details;
    const threadId = threadIdForEvent(event);
    const connectionId = stringDetail(details, "gmail_connection_id");
    const recipients = stringDetail(details, "recipients")
      .split(",")
      .map((address) => address.trim())
      .filter(Boolean);
    const recipient = stringDetail(details, "direction") === "outbound"
      ? recipients.find((address) => address.toLowerCase() === lead.email?.trim().toLowerCase()) || recipients[0] || ""
      : stringDetail(details, "sender");
    const originalSubject = stringDetail(details, "subject");
    setComposeSeed({
      recipient,
      subject: originalSubject && !/^re:/i.test(originalSubject) ? `Re: ${originalSubject}` : originalSubject,
      threadId: threadId || undefined,
      inReplyTo: stringDetail(details, "rfc_message_id") || undefined,
      connectionId: connectionId || undefined,
    });
    setComposerRevision((current) => current + 1);
  }

  const lastEvent = orderedThreads[0]?.messages[orderedThreads[0].messages.length - 1];
  const lastDirection = lastEvent ? stringDetail(lastEvent.details, "direction") : "";
  const lastTime = lastEvent ? new Date(lastEvent.created_at) : null;
  const status = !lastEvent
    ? { tone: "neutral", text: "No emails yet. Write the first one when you are ready." }
    : lastDirection === "outbound"
      ? { tone: "attention", text: `You sent the last email ${relative(lastTime!)}. No reply yet.` }
      : { tone: "tint", text: `${lead.full_name.split(" ")[0] || "The lead"} replied ${relative(lastTime!)}. Your move.` };
  const totalMessages = orderedThreads.reduce((sum, thread) => sum + thread.messages.length, 0);
  const mailboxEmail = (id: string) => (accounts.find((account) => account.id === id)?.account_name ?? "").toLowerCase();

  function startCompose(seed: GmailComposerSeed) {
    setComposeSeed(seed);
    setComposerRevision((current) => current + 1);
    window.requestAnimationFrame(() => document.getElementById("lead-mail-composer")?.scrollIntoView({ block: "nearest", behavior: "smooth" }));
  }
  function replyAll(event: LeadPipelineEvent) {
    const details = event.details;
    const inbound = stringDetail(details, "direction") !== "outbound";
    const own = mailboxEmail(stringDetail(details, "gmail_connection_id"));
    const everyone = [stringDetail(details, "sender"), ...stringDetail(details, "recipients").split(",")]
      .map((address) => address.trim())
      .filter(Boolean);
    const primary = inbound ? stringDetail(details, "sender") : (lead.email?.trim() || "");
    const cc = everyone.filter((address) => {
      const lower = address.toLowerCase();
      return lower !== primary.toLowerCase() && !(own && lower.includes(own));
    });
    replyTo(event);
    setComposeSeed((seed) => (seed ? { ...seed, recipient: primary || seed.recipient, cc: cc.join(", ") } : seed));
  }
  function forward(event: LeadPipelineEvent) {
    const details = event.details;
    const subject = stringDetail(details, "subject");
    const quoted = plainText(stringDetail(details, "body"));
    startCompose({
      recipient: "",
      subject: /^fwd?:/i.test(subject) ? subject : `Fwd: ${subject}`,
      body: `\n\n---------- Forwarded message ----------\nFrom: ${stringDetail(details, "sender")}\nTo: ${stringDetail(details, "recipients")}\nDate: ${new Date(event.created_at).toLocaleString()}\nSubject: ${subject}\n\n${quoted}`,
      connectionId: stringDetail(details, "gmail_connection_id") || undefined,
    });
  }
  const askCorral = (prompt: string) => onAskAgent?.(prompt);
  const classification = lead.qualification?.classification.toLowerCase();
  const disqualified = Boolean(lead.qualification?.hard_disqualified) || ["disqualify", "disqualified", "hard_disqualified"].includes(classification || "");
  const needsReview = Boolean(lead.routing?.manual_review || lead.analysis?.requires_manual_review);
  const blocked = disqualified
    ? "This lead is disqualified. Review the evidence and update its qualification before reaching out."
    : needsReview ? "This lead needs a person to review it first. Record verified context in Pipeline, then re-check it." : null;

  return <section className="lead-email-panel lm">
    <header className="lm-head">
      <div>
        <h2>Emails with {lead.full_name || "this lead"}</h2>
        <p>{lead.email ? lead.email : "No email address on file"}{totalMessages ? ` · ${totalMessages} message${totalMessages === 1 ? "" : "s"} in ${orderedThreads.length} thread${orderedThreads.length === 1 ? "" : "s"}` : ""}</p>
      </div>
      <div className="lm-head-actions">
        <Button kind="gray" size="sm" icon="refresh" onClick={() => void refreshConversation()} disabled={syncBusy || activeAccounts.length === 0}>{syncBusy ? "Refreshing…" : "Refresh"}</Button>
        {!hideComposeButton && canSend && <Button kind="filled" size="sm" icon="edit" disabled={Boolean(blocked)} title={blocked ?? undefined} onClick={() => startCompose({ recipient: lead.email?.trim() || "", purpose: "follow_up", ...(replyTarget ?? {}) })}>{replyTarget ? "Reply" : "New email"}</Button>}
      </div>
    </header>

    {blocked && <div className="lm-status" data-tone="attention"><Icon name="alert" size={16} /><span><b>Outreach paused.</b> {blocked}</span></div>}
    <div className="lm-status" data-tone={status.tone}>
      <Icon name={status.tone === "tint" ? "mail" : status.tone === "attention" ? "clock" : "note"} size={16} />
      <span>{status.text}</span>
      {activeAccounts.length > 0 && <small>Checks Gmail every minute while this tab is open</small>}
    </div>

    {onAskAgent && (
      <div className="lm-ai" role="group" aria-label="Ask Corral about this conversation">
        <span className="lm-ai-label"><Icon name="sparkle" size={14} />Ask Corral</span>
        <button type="button" onClick={() => askCorral(`Summarize my email conversation with ${lead.full_name} and tell me where things stand.`)} disabled={!totalMessages}>Summarize thread</button>
        <button type="button" onClick={() => askCorral(`Draft a reply to the latest email from ${lead.full_name}. Use the whole thread for context and keep it short.`)} disabled={!totalMessages || !canSend}>Suggest a reply</button>
        <button type="button" onClick={() => askCorral(`Propose three meeting times next week for ${lead.full_name} and draft the email offering them.`)} disabled={!canSend}>Propose meeting times</button>
        <button type="button" onClick={() => askCorral(`What questions or commitments in my emails with ${lead.full_name} still need an answer?`)} disabled={!totalMessages}>Open questions</button>
      </div>
    )}

    {!accountsLoading && !loading && !error && activeAccounts.length === 0 && orderedThreads.length === 0 && <LeadEmailEmptyState
      title="No verified Gmail mailbox"
      description="Connect a verified Gmail mailbox in Integrations to refresh and view conversations for this lead."
    />}
    {error && <div className="notice" role="alert">{error}</div>}
    {notice && <div className="notice" role="status">{notice}</div>}
    {composeSeed && <div id="lead-mail-composer" className="lm-composer"><EmailComposer
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
    /></div>}
    <MeetingOfferDrafts
      leadId={lead.id}
      leadEmail={lead.email}
      active={active}
      canSend={canSend}
      accounts={accounts}
      onSent={() => {
        setRevision((current) => current + 1);
        onConversationSynced();
      }}
    />
    {!accountsLoading && loading && <p className="email-status lead-email-status" role="status">Loading this lead’s email history…</p>}
    {!accountsLoading && !loading && !error && orderedThreads.length === 0 && activeAccounts.length > 0 && <LeadEmailEmptyState
      title="No conversation linked yet"
      description="Refresh Gmail to check connected inboxes, or write a new email to start this conversation."
    />}
    <div className="lm-threads">
      {!loading && orderedThreads.map(({ threadId, messages }, threadIndex) => {
        const subject = stringDetail(messages[messages.length - 1]?.details ?? {}, "subject") || "(no subject)";
        const latest = messages[messages.length - 1];
        const canReply = canSend && messages.some((message) => stringDetail(message.details, "rfc_message_id"));
        return <details className="lm-thread" key={threadId} open={threadIndex === 0 || undefined}>
          <summary>
            <span className="lm-thread-title"><strong>{subject}</strong><small>{messages.length} {messages.length === 1 ? "email" : "emails"} · last {latest ? new Date(latest.created_at).toLocaleDateString(undefined, { month: "short", day: "numeric" }) : ""}</small></span>
            <span className="lm-chevron" aria-hidden="true" />
          </summary>
          <ol className="lm-messages">{messages.map((event, index) => {
            const details = event.details;
            const direction = stringDetail(details, "direction");
            const outbound = direction === "outbound";
            const body = stringDetail(details, "body");
            const sender = stringDetail(details, "sender");
            const recipients = stringDetail(details, "recipients");
            const mailboxName = accounts.find((account) => account.id === stringDetail(details, "gmail_connection_id"))?.account_name;
            const isLast = index === messages.length - 1;
            const who = outbound ? "You" : (sender.replace(/<.*>/, "").trim() || sender || "Lead");
            return <li key={event.id} className="lm-message" data-outbound={outbound || undefined}>
              <details open={isLast || undefined}>
                <summary>
                  <Monogram text={outbound ? (mailboxName || "You") : who} size={32} />
                  <span className="lm-from"><b>{who}</b><small>{outbound ? `to ${recipients || lead.email || "lead"}` : sender.match(/<(.+)>/)?.[1] ?? sender}</small></span>
                  <span className="lm-snippet">{plainText(body).replace(/\s+/g, " ").slice(0, 110)}</span>
                  <time dateTime={event.created_at}>{new Date(event.created_at).toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })}</time>
                </summary>
                <div className="lm-body">
                  <div className="lm-addresses"><span>From <b>{sender || "Unknown"}</b></span>{recipients && <span>To <b>{recipients}</b></span>}{mailboxName && <span>Mailbox <b>{mailboxName}</b></span>}</div>
                  <ProviderEmailBody content={body} />
                  {canSend && <div className="lm-actions">
                    {canReply && stringDetail(details, "gmail_connection_id") && <Button kind="gray" size="sm" icon="reply" onClick={() => replyTo(event)}>Reply</Button>}
                    {canReply && stringDetail(details, "gmail_connection_id") && <Button kind="gray" size="sm" icon="replyAll" onClick={() => replyAll(event)}>Reply all</Button>}
                    <Button kind="gray" size="sm" icon="forward" onClick={() => forward(event)}>Forward</Button>
                    {onAskAgent && <Button kind="plain" size="sm" icon="sparkle" onClick={() => askCorral(`Draft a reply to this email from ${who} (subject "${stringDetail(details, "subject")}"). Use the whole thread for context.`)}>Draft with Corral</Button>}
                  </div>}
                </div>
              </details>
            </li>;
          })}</ol>
        </details>;
      })}
    </div>
  </section>;
}

function plainText(value: string) {
  return value.replace(/<style[\s\S]*?<\/style>/gi, "").replace(/<br\s*\/?>/gi, "\n").replace(/<\/p>/gi, "\n\n").replace(/<[^>]+>/g, "").replace(/&nbsp;/g, " ").replace(/&amp;/g, "&").replace(/&lt;/g, "<").replace(/&gt;/g, ">").trim();
}

function relative(date: Date) {
  const minutes = Math.round((Date.now() - date.getTime()) / 60_000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} hour${hours === 1 ? "" : "s"} ago`;
  const days = Math.round(hours / 24);
  return `${days} day${days === 1 ? "" : "s"} ago`;
}
