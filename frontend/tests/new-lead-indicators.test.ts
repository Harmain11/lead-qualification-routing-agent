import assert from "node:assert/strict";
import test from "node:test";
import {
  emptyNewLeadIndicatorSnapshot,
  markLeadSeen,
  newlyUnseenLeadIds,
  observeNewLeads,
} from "../src/newLeadIndicators.ts";

test("initial observation establishes a baseline without marking existing leads new", async () => {
  const result = await observeNewLeads(emptyNewLeadIndicatorSnapshot, [
    { id: "older", created_at: "2026-10-01T10:00:00Z" },
    { id: "latest", created_at: "2026-10-05T10:00:00Z" },
  ]);

  assert.deepEqual(result.unseenIds, []);
  assert.equal(result.cursorTime, Date.parse("2026-10-05T10:00:00Z"));
});

test("all leads newer than the prior cursor are marked unseen regardless of list order", async () => {
  const baseline = await observeNewLeads(emptyNewLeadIndicatorSnapshot, [
    { id: "existing", created_at: "2026-10-01T10:00:00Z" },
  ]);
  const result = await observeNewLeads(baseline, [
    { id: "newest", created_at: "2026-10-03T10:00:00Z" },
    { id: "newer", created_at: "2026-10-02T10:00:00Z" },
    { id: "existing", created_at: "2026-10-01T10:00:00Z" },
  ]);

  assert.deepEqual(result.unseenIds, ["newest", "newer"]);
});

test("a lead at the same timestamp is recognized and opening it clears only that lead", async () => {
  const baseline = await observeNewLeads(emptyNewLeadIndicatorSnapshot, [
    { id: "existing", created_at: "2026-10-01T10:00:00Z" },
  ]);
  const observed = await observeNewLeads(baseline, [
    { id: "existing", created_at: "2026-10-01T10:00:00Z" },
    { id: "same-time-new", created_at: "2026-10-01T10:00:00Z" },
  ]);
  const cleared = markLeadSeen(observed, "same-time-new");

  assert.deepEqual(observed.unseenIds, ["same-time-new"]);
  assert.deepEqual(cleared.unseenIds, []);
  assert.equal(cleared.cursorTime, baseline.cursorTime);
});

test("opening a new lead before the next list refresh does not make it new again", async () => {
  const baseline = await observeNewLeads(emptyNewLeadIndicatorSnapshot, [
    { id: "existing", created_at: "2026-10-01T10:00:00Z" },
  ]);
  const createdAt = "2026-10-02T10:00:00Z";
  const opened = markLeadSeen(baseline, "opened-first", createdAt);
  const observed = await observeNewLeads(opened, [
    { id: "opened-first", created_at: createdAt },
    { id: "existing", created_at: "2026-10-01T10:00:00Z" },
  ]);

  assert.deepEqual(observed.unseenIds, []);
  assert.equal(observed.cursorTime, Date.parse(createdAt));
});

test("legacy stored snapshots without opened-lead tracking remain readable", async () => {
  const baseline = await observeNewLeads(emptyNewLeadIndicatorSnapshot, [
    { id: "existing", created_at: "2026-10-01T10:00:00Z" },
  ]);
  const legacy = { ...baseline } as Partial<typeof baseline>;
  delete legacy.openedLeads;
  const result = await observeNewLeads(
    legacy as typeof baseline,
    [{ id: "new", created_at: "2026-10-02T10:00:00Z" }],
  );

  assert.deepEqual(result.unseenIds, ["new"]);
});

test("only newly unseen lead IDs trigger arrival notifications", async () => {
  const baseline = await observeNewLeads(emptyNewLeadIndicatorSnapshot, [
    { id: "existing", created_at: "2026-10-01T10:00:00Z" },
  ]);
  const arrived = await observeNewLeads(baseline, [
    { id: "new", created_at: "2026-10-02T10:00:00Z" },
    { id: "existing", created_at: "2026-10-01T10:00:00Z" },
  ]);
  const repeated = await observeNewLeads(arrived, [
    { id: "new", created_at: "2026-10-02T10:00:00Z" },
    { id: "existing", created_at: "2026-10-01T10:00:00Z" },
  ]);

  assert.deepEqual(newlyUnseenLeadIds(baseline, arrived), ["new"]);
  assert.deepEqual(newlyUnseenLeadIds(arrived, repeated), []);
});

test("a complete lead snapshot clears unread arrivals for deleted leads", async () => {
  const baseline = await observeNewLeads(emptyNewLeadIndicatorSnapshot, [
    { id: "existing", created_at: "2026-10-01T10:00:00Z" },
  ]);
  const arrived = await observeNewLeads(baseline, [
    { id: "deleted", created_at: "2026-10-02T10:00:00Z" },
    { id: "existing", created_at: "2026-10-01T10:00:00Z" },
  ]);
  const partialPage = await observeNewLeads(
    arrived,
    [{ id: "existing", created_at: "2026-10-01T10:00:00Z" }],
    { completeSnapshot: false },
  );
  const completeSnapshot = await observeNewLeads(partialPage, []);

  assert.deepEqual(partialPage.unseenIds, ["deleted"]);
  assert.deepEqual(completeSnapshot.unseenIds, []);
});

test("clearing a deleted arrival keeps repeat-import suppression", async () => {
  const baseline = await observeNewLeads(emptyNewLeadIndicatorSnapshot, [
    { id: "existing", created_at: "2026-10-01T10:00:00Z" },
  ]);
  const arrived = await observeNewLeads(baseline, [
    {
      id: "deleted",
      email: "prospect@example.com",
      created_at: "2026-10-02T10:00:00Z",
    },
    { id: "existing", created_at: "2026-10-01T10:00:00Z" },
  ]);
  const cleared = await observeNewLeads(arrived, [
    { id: "existing", created_at: "2026-10-01T10:00:00Z" },
  ]);
  const reimported = await observeNewLeads(cleared, [
    {
      id: "new-id",
      email: "prospect@example.com",
      created_at: "2026-10-03T10:00:00Z",
    },
    { id: "existing", created_at: "2026-10-01T10:00:00Z" },
  ]);

  assert.deepEqual(cleared.unseenIds, []);
  assert.deepEqual(reimported.unseenIds, []);
});

test("reimporting a deleted email lead suppresses its duplicate arrival", async () => {
  const baseline = await observeNewLeads(emptyNewLeadIndicatorSnapshot, [
    { id: "existing", created_at: "2026-10-01T10:00:00Z" },
  ]);
  const arrived = await observeNewLeads(baseline, [
    {
      id: "gmail-first",
      email: "prospect@example.com",
      created_at: "2026-10-02T10:00:00Z",
    },
    { id: "existing", created_at: "2026-10-01T10:00:00Z" },
  ]);
  const reimported = await observeNewLeads(arrived, [
    {
      id: "gmail-reimport",
      email: "  PROSPECT@example.com ",
      created_at: "2026-10-03T10:00:00Z",
    },
    { id: "existing", created_at: "2026-10-01T10:00:00Z" },
  ]);

  assert.deepEqual(arrived.unseenIds, ["gmail-first"]);
  assert.deepEqual(reimported.unseenIds, []);
  assert.deepEqual(newlyUnseenLeadIds(arrived, reimported), []);
  assert.equal(
    JSON.stringify(reimported).includes("prospect@example.com"),
    false,
  );
});

test("opening a new email lead before refresh still suppresses a later reimport", async () => {
  const baseline = await observeNewLeads(emptyNewLeadIndicatorSnapshot, [
    { id: "existing", created_at: "2026-10-01T10:00:00Z" },
  ]);
  const createdAt = "2026-10-02T10:00:00Z";
  const opened = markLeadSeen(baseline, "opened-first", createdAt);
  const observed = await observeNewLeads(opened, [
    {
      id: "opened-first",
      email: "prospect@example.com",
      created_at: createdAt,
    },
    { id: "existing", created_at: "2026-10-01T10:00:00Z" },
  ]);
  const reimported = await observeNewLeads(observed, [
    {
      id: "reimported",
      email: "prospect@example.com",
      created_at: "2026-10-03T10:00:00Z",
    },
    { id: "existing", created_at: "2026-10-01T10:00:00Z" },
  ]);

  assert.deepEqual(observed.unseenIds, []);
  assert.deepEqual(reimported.unseenIds, []);
});
