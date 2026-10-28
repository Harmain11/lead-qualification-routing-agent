import assert from "node:assert/strict";
import { test } from "node:test";
import { leadDetailSections, parseLeadSection } from "../src/leadSections.ts";

test("lead navigation follows the requested workflow and preserves Follow up links", () => {
  assert.deepEqual(leadDetailSections.map(({ label }) => label), [
    "Overview", "Qualification", "Follow up", "Emails", "Calls", "Deal", "Activity", "Ask Corral",
  ]);
  assert.equal(parseLeadSection("follow-ups"), "follow-ups");
  assert.equal(parseLeadSection("closing"), "deal");
});
