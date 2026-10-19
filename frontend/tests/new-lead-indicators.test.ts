import assert from "node:assert/strict";
import test from "node:test";
import {
  emptyNewLeadIndicatorSnapshot,
  markLeadSeen,
  observeNewLeads,
} from "../src/newLeadIndicators.ts";

test("initial observation establishes a baseline without marking existing leads new", () => {
  const result = observeNewLeads(emptyNewLeadIndicatorSnapshot, [
    { id: "older", created_at: "2026-10-01T10:00:00Z" },
    { id: "latest", created_at: "2026-10-05T10:00:00Z" },
  ]);

  assert.deepEqual(result.unseenIds, []);
  assert.equal(result.cursorTime, Date.parse("2026-10-05T10:00:00Z"));
});

test("all leads newer than the prior cursor are marked unseen regardless of list order", () => {
  const baseline = observeNewLeads(emptyNewLeadIndicatorSnapshot, [
    { id: "existing", created_at: "2026-10-01T10:00:00Z" },
  ]);
  const result = observeNewLeads(baseline, [
    { id: "newest", created_at: "2026-10-03T10:00:00Z" },
    { id: "newer", created_at: "2026-10-02T10:00:00Z" },
    { id: "existing", created_at: "2026-10-01T10:00:00Z" },
  ]);

  assert.deepEqual(result.unseenIds, ["newest", "newer"]);
});

test("a lead at the same timestamp is recognized and opening it clears only that lead", () => {
  const baseline = observeNewLeads(emptyNewLeadIndicatorSnapshot, [
    { id: "existing", created_at: "2026-10-01T10:00:00Z" },
  ]);
  const observed = observeNewLeads(baseline, [
    { id: "existing", created_at: "2026-10-01T10:00:00Z" },
    { id: "same-time-new", created_at: "2026-10-01T10:00:00Z" },
  ]);
  const cleared = markLeadSeen(observed, "same-time-new");

  assert.deepEqual(observed.unseenIds, ["same-time-new"]);
  assert.deepEqual(cleared.unseenIds, []);
  assert.equal(cleared.cursorTime, baseline.cursorTime);
});

test("opening a new lead before the next list refresh does not make it new again", () => {
  const baseline = observeNewLeads(emptyNewLeadIndicatorSnapshot, [
    { id: "existing", created_at: "2026-10-01T10:00:00Z" },
  ]);
  const createdAt = "2026-10-02T10:00:00Z";
  const opened = markLeadSeen(baseline, "opened-first", createdAt);
  const observed = observeNewLeads(opened, [
    { id: "opened-first", created_at: createdAt },
    { id: "existing", created_at: "2026-10-01T10:00:00Z" },
  ]);

  assert.deepEqual(observed.unseenIds, []);
  assert.equal(observed.cursorTime, Date.parse(createdAt));
});

test("legacy stored snapshots without opened-lead tracking remain readable", () => {
  const baseline = observeNewLeads(emptyNewLeadIndicatorSnapshot, [
    { id: "existing", created_at: "2026-10-01T10:00:00Z" },
  ]);
  const legacy = { ...baseline } as Partial<typeof baseline>;
  delete legacy.openedLeads;
  const result = observeNewLeads(
    legacy as typeof baseline,
    [{ id: "new", created_at: "2026-10-02T10:00:00Z" }],
  );

  assert.deepEqual(result.unseenIds, ["new"]);
});
