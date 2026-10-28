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
