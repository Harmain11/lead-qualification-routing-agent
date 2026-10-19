import assert from "node:assert/strict";
import { test } from "node:test";
import { pollInboundSync } from "../src/inboundSyncPolling.ts";
import type { CrmInboundSyncRun, CrmInboundSyncState } from "../src/types.ts";

const run = (status: string): CrmInboundSyncRun => ({
  id: "run-1",
  status,
  mode: "review_recent",
  created_at: "2026-10-19T10:00:01.000Z",
  last_error: status === "completed_with_errors" ? "AI timed out for one message." : null,
  records_fetched: 3,
  records_created: 1,
  records_updated: 0,
  records_unchanged: 0,
  records_skipped: 1,
  records_failed: status === "completed_with_errors" ? 1 : 0,
  records_processed: 3,
  records_succeeded: 1,
});

const state = (status: string): CrmInboundSyncState =>
  ({ status, last_error: status === "failed" ? "OpenAI credentials unavailable." : null }) as CrmInboundSyncState;

test("polls queued Gmail sync through running to a terminal status", async () => {
  let poll = 0;
  const messages: string[] = [];
  const result = await pollInboundSync({
    queuedAt: "2026-10-19T10:00:00.000Z",
    getRuns: async () => {
      poll += 1;
      return poll === 1 ? [] : [run(poll === 2 ? "running" : "completed_with_errors")];
    },
    getState: async () => state(poll === 1 ? "queued" : "running"),
    onProgress: (message) => messages.push(message),
    wait: async () => {},
  });
  assert.equal(result.status, "completed_with_errors");
  assert.equal(poll, 3);
  assert.match(messages[0], /queued/);
  assert.match(messages[1], /3 processed/);
  assert.match(messages[2], /1 failed/);
});

test("reports worker/configuration failure when no run was created", async () => {
  await assert.rejects(
    pollInboundSync({
      queuedAt: "2026-10-19T10:00:00.000Z",
      getRuns: async () => [],
      getState: async () => state("failed"),
      onProgress: () => {},
      wait: async () => {},
    }),
    /OpenAI credentials unavailable/,
  );
});
