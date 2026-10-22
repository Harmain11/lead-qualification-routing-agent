import assert from "node:assert/strict";
import { test } from "node:test";
import { assistantConversation } from "../src/assistantConversation.ts";

test("includes reviewed email edits and successful send outcome on the next turn", () => {
  const [message] = assistantConversation([{
    role: "assistant", content: "Please review this draft",
    action: { kind: "send_email", email_cc: "team@example.test", email_bcc: "private@example.test", email_subject: "Edited subject" },
    actionStatus: "applied", actionResult: "Email sent from Sales to customer@example.test.",
  }]);
  assert.match(message.content, /"state":"applied"/);
  assert.match(message.content, /Email sent from Sales/);
  assert.match(message.content, /private@example\.test/);
  assert.match(message.content, /Edited subject/);
});

test("keeps unapproved, dismissed and failed actions separate from success", () => {
  for (const status of ["awaiting_approval", "dismissed", "sending"]) {
    const [message] = assistantConversation([{
      role: "assistant", content: "Review", action: { kind: "update_lead_profile" },
      actionStatus: status,
    }]);
    assert.match(message.content, new RegExp(`"state":"${status}"`));
    assert.doesNotMatch(message.content, /"state":"applied"/);
  }
  const [failed] = assistantConversation([{
    role: "assistant", content: "Review", action: { kind: "add_lead_note" }, error: "Save failed",
  }]);
  assert.match(failed.content, /Save failed/);
  assert.doesNotMatch(failed.content, /"state":"applied"/);
});

test("includes completed lead actions and respects request history bounds", () => {
  const result = assistantConversation(Array.from({ length: 20 }, () => ({
    role: "assistant" as const, content: "x".repeat(3000), done: true,
    action: { kind: "add_note", note: "n".repeat(5000) },
  })));
  assert.equal(result.length, 16);
  assert.ok(result.every(({ content }) => content.length <= 3000));
  assert.match(result[0].content, /"state":"applied"/);
  assert.match(result[0].content, /details truncated/);
});
