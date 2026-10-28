import assert from "node:assert/strict";
import test from "node:test";
import { assessCallLeadPotential } from "../src/callLeadAssessment.ts";

test("call assessment distinguishes buying intent, opt-out, and uncertain content", () => {
  assert.equal(
    assessCallLeadPotential({ summary: "Asked for pricing and a demo" }).label,
    "Potential lead",
  );
  assert.equal(
    assessCallLeadPotential({ transcript: "Please remove me and do not call again." }).label,
    "Unlikely lead",
  );
  assert.equal(
    assessCallLeadPotential({ transcript: "They asked for a quote next week." }).label,
    "Potential lead",
  );
  assert.equal(
    assessCallLeadPotential({ transcript: "The office manager took a message." }).label,
    "Review manually",
  );
});

test("call assessment explains missing evidence without inventing a result", () => {
  assert.deepEqual(assessCallLeadPotential({ summary: null, transcript: "  " }), {
    label: "Needs review",
    detail: "No call summary or transcript is available to assess.",
  });
});
