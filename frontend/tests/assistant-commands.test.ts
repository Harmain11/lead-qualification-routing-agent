import assert from "node:assert/strict";
import { test } from "node:test";
import { parseAssistantCommand } from "../src/components/assistantCommands.ts";

test("plain navigation runs as an instant command", () => {
  assert.deepEqual(parseAssistantCommand("open pipeline"), { kind: "navigate", area: "pipeline" });
  assert.deepEqual(parseAssistantCommand("Go to the inbox."), { kind: "navigate", area: "email" });
  assert.deepEqual(parseAssistantCommand("please take me to call history"), { kind: "navigate", area: "dialer" });
  assert.deepEqual(parseAssistantCommand("Show me my calendar page"), { kind: "navigate", area: "calendar" });
  assert.deepEqual(parseAssistantCommand("Companies"), { kind: "navigate", area: "accounts" });
  assert.deepEqual(parseAssistantCommand("open research & coaching"), { kind: "navigate", area: "intelligence" });
});

test("closing and new chats are instant", () => {
  assert.deepEqual(parseAssistantCommand("close"), { kind: "close" });
  assert.deepEqual(parseAssistantCommand("Close the assistant"), { kind: "close" });
  assert.deepEqual(parseAssistantCommand("new chat"), { kind: "new_chat" });
  assert.deepEqual(parseAssistantCommand("start a new conversation"), { kind: "new_chat" });
});

test("requests that need judgement still go to the agent", () => {
  assert.equal(parseAssistantCommand("show me leads that went cold this week"), null);
  assert.equal(parseAssistantCommand("open lead Jane Doe"), null);
  assert.equal(parseAssistantCommand("close the deal with Acme"), null);
  assert.equal(parseAssistantCommand("draft a follow up email"), null);
  assert.equal(parseAssistantCommand(""), null);
});
