import assert from "node:assert/strict";
import { test } from "node:test";
import { latestGmailReplyTarget, newestFirstGmailEvents, newestFirstGmailThread } from "../src/utils/gmailEvents.ts";
import type { GmailThreadMessage, LeadPipelineEvent } from "../src/types.ts";

function message(id: string, createdAt: string, details: Record<string, unknown>): LeadPipelineEvent {
  return { id, stage: "gmail_message", status: "received", details, created_at: createdAt };
}

test("orders a lead email thread newest first without mutating the original list", () => {
  const oldest = message("oldest", "2026-10-20T08:00:00Z", {});
  const newest = message("newest", "2026-10-20T10:00:00Z", {});
  const input = [oldest, newest];

  assert.deepEqual(newestFirstGmailEvents(input).map((event) => event.id), ["newest", "oldest"]);
  assert.deepEqual(input.map((event) => event.id), ["oldest", "newest"]);
});

test("orders mailbox conversation messages newest first", () => {
  const createMailboxMessage = (messageId: string, sentAt: string): GmailThreadMessage => ({
    thread_id: "thread",
    sender: "lead@example.test",
    sender_name: "Lead",
    recipients: "sales@example.test",
    subject: "Conversation",
    rfc_message_id: `<${messageId}@example.test>`,
    direction: "inbound",
    body: "Message body",
    message_id: messageId,
    sent_at: sentAt,
  });
  const oldest = createMailboxMessage("oldest", "2026-10-20T08:00:00Z");
  const newest = createMailboxMessage("newest", "2026-10-20T10:00:00Z");

  assert.deepEqual(
    newestFirstGmailThread([oldest, newest]).map((message) => message.message_id),
    ["newest", "oldest"],
  );
});

test("replies to the latest lead conversation using its original mailbox and RFC reference", () => {
  const events = [
    message("1", "2026-10-27T10:00:00Z", {
      message_id: "gmail-1", thread_id: "thread-a", gmail_connection_id: "mailbox-a",
      sender: "lead@example.com", direction: "inbound", subject: "Quick meeting",
      rfc_message_id: "<first@example.com>",
    }),
    message("2", "2026-10-28T10:00:00Z", {
      message_id: "gmail-2", thread_id: "thread-a", gmail_connection_id: "mailbox-a",
      sender: "us@example.com", recipients: "lead@example.com", direction: "outbound",
      subject: "Re: Quick meeting", rfc_message_id: "<second@example.com>",
    }),
    message("3", "2026-10-29T10:00:00Z", {
      message_id: "gmail-3", thread_id: "thread-a", gmail_connection_id: "mailbox-a",
      sender: "us@example.com", recipients: "lead@example.com", direction: "outbound",
      subject: "Re: Quick meeting", rfc_message_id: "",
    }),
    message("4", "2026-10-30T10:00:00Z", {
      message_id: "gmail-4", thread_id: "other-thread", gmail_connection_id: "mailbox-b",
      sender: "other@example.com", direction: "inbound", subject: "Unrelated",
      rfc_message_id: "<other@example.com>",
    }),
  ];
  assert.deepEqual(latestGmailReplyTarget(events, "lead@example.com", ["mailbox-a", "mailbox-b"]), {
    threadId: "thread-a", connectionId: "mailbox-a", subject: "Re: Quick meeting",
    inReplyTo: "<second@example.com>",
  });
  assert.equal(latestGmailReplyTarget(events, "lead@example.com", ["mailbox-b"]), null);
});
