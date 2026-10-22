import assert from "node:assert/strict";
import test from "node:test";
import { isFollowUpEmailDraftRequest } from "../src/components/leadAssistantIntents.ts";

test("recognizes direct requests to draft follow-up email", () => {
  for (const request of [
    "I want to write a follow up email",
    "Draft a follow-up email for this lead",
    "Please compose an email",
    "Write and send a follow-up email",
  ]) {
    assert.equal(isFollowUpEmailDraftRequest(request), true, request);
  }
});

test("does not redirect questions about existing email to the composer", () => {
  for (const question of [
    "What did we say in the last email?",
    "Summarize the follow-up emails from last month",
    "Does the lead have an email address?",
  ]) {
    assert.equal(isFollowUpEmailDraftRequest(question), false, question);
  }
});
