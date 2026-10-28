import type { LeadPipelineEvent } from "../types";

function detail(details: Record<string, unknown>, key: string): string {
  const value = details[key];
  return typeof value === "string" ? value.trim() : "";
}

function firstAddress(value: string): string {
  const match = value.match(/<([^>]+)>/);
  return (match?.[1] ?? value.split(/[;,]/, 1)[0] ?? "").trim().toLowerCase();
}

function mailboxForEvent(event: LeadPipelineEvent): string {
  const details = event.details;
  const explicitMailbox = detail(details, "gmail_mailbox_email");
  if (explicitMailbox) return firstAddress(explicitMailbox);

  const direction = detail(details, "direction") || event.status;
  const mailbox = direction.toLowerCase() === "outbound"
    ? detail(details, "sender")
    : detail(details, "recipients");
  return firstAddress(mailbox);
}

export function dedupeGmailEvents(
  events: LeadPipelineEvent[],
): LeadPipelineEvent[] {
  const seen = new Set<string>();
  return events.filter((event) => {
    const messageId = detail(event.details, "message_id");
    if (!messageId) return true;

    const mailbox = mailboxForEvent(event);
    const identity = `${mailbox || "unknown-mailbox"}:${messageId}`;
    if (seen.has(identity)) return false;
    seen.add(identity);
    return true;
  });
}

export type GmailReplyTarget = {
  threadId: string;
  inReplyTo?: string;
  connectionId: string;
  subject: string;
};

export function latestGmailReplyTarget(
  events: LeadPipelineEvent[],
  leadEmail: string | null,
  sendableConnectionIds: string[],
): GmailReplyTarget | null {
  const address = firstAddress(leadEmail ?? "");
  if (!address) return null;
  const allowed = new Set(sendableConnectionIds);
  const messages = dedupeGmailEvents(events)
    .filter((event) => {
      if (event.stage !== "gmail_message") return false;
      const details = event.details;
      const connectionId = detail(details, "gmail_connection_id");
      const threadId = detail(details, "thread_id") || detail(details, "gmail_thread_id");
      const direction = detail(details, "direction");
      const counterparty = direction === "outbound"
        ? detail(details, "recipients")
        : detail(details, "sender");
      return allowed.has(connectionId) && Boolean(threadId)
        && firstAddress(counterparty) === address;
    })
    .sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at));
  const latest = messages[0];
  if (!latest) return null;
  const threadId = detail(latest.details, "thread_id") || detail(latest.details, "gmail_thread_id");
  const connectionId = detail(latest.details, "gmail_connection_id");
  const lastWithRfcId = messages.find((event) =>
    (detail(event.details, "thread_id") || detail(event.details, "gmail_thread_id")) === threadId
    && detail(event.details, "gmail_connection_id") === connectionId
    && detail(event.details, "rfc_message_id"),
  );
  const originalSubject = detail(latest.details, "subject");
  if (!lastWithRfcId || !originalSubject) return null;
  return {
    threadId,
    connectionId,
    subject: originalSubject && !/^re:/i.test(originalSubject)
      ? `Re: ${originalSubject}` : originalSubject,
    inReplyTo: detail(lastWithRfcId.details, "rfc_message_id"),
  };
}
