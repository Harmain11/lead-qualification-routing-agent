import assert from "node:assert/strict";
import test from "node:test";
import { isDirectPhoneCallRequest } from "../src/components/workspaceAssistantIntents.ts";

test("recognizes requests to place a phone call", () => {
  for (const request of [
    "Please call Kathi Sitek",
    "please call kathi sitek",
    "Can you call this lead?",
    "I want to call the prospect",
    "Could you initiate a call through the dialer?",
    "Call",
  ]) {
    assert.equal(isDirectPhoneCallRequest(request), true, request);
  }
});

test("does not mistake call-history questions for call requests", () => {
  for (const question of [
    "Summarize recent calls and meetings",
    "Find the latest call transcript",
    "Call history from last month",
    "Did we call Kathi last week?",
  ]) {
    assert.equal(isDirectPhoneCallRequest(question), false, question);
  }
});
