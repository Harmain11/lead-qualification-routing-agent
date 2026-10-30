import assert from "node:assert/strict";
import { test } from "node:test";
import { callOutcome, formatCallDuration } from "../src/callOutcome.ts";

test("call outcomes match the backend vocabulary", () => {
  assert.equal(callOutcome("completed", "outbound", 95), "answered");
  assert.equal(callOutcome("no-answer", "inbound", null), "missed");
  assert.equal(callOutcome("no-answer", "outbound", null), "no_answer");
  assert.equal(callOutcome("completed", "inbound", 0), "missed");
  assert.equal(callOutcome("ringing", "outbound", null), "in_progress");
  assert.equal(callOutcome("canceled", "inbound", null), "failed");
});

test("durations read naturally", () => {
  assert.equal(formatCallDuration(null), "—");
  assert.equal(formatCallDuration(42), "42s");
  assert.equal(formatCallDuration(95), "1m 35s");
});
