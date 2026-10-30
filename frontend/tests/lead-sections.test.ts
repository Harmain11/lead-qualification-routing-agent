import assert from "node:assert/strict";
import { test } from "node:test";
import { leadDetailSections, parseLeadSection } from "../src/leadSections.ts";

test("lead navigation follows the workflow, with Pipeline in place of Follow up", () => {
  assert.deepEqual(leadDetailSections.map(({ label }) => label), [
    "Overview", "Qualification", "Pipeline", "Emails", "Calls", "Deal", "Activity", "Ask Corral",
  ]);
  assert.equal(parseLeadSection("pipeline"), "pipeline");
  // Old Follow up links and pinned tabs land on Pipeline.
  assert.equal(parseLeadSection("follow-ups"), "pipeline");
  assert.equal(parseLeadSection("closing"), "deal");
});
