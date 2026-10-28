import assert from "node:assert/strict";
import test from "node:test";
import { getLeadTalkingPoints } from "../src/leadActionPlan.ts";

test("preserves configured lead talking points", () => {
  const points = ["Address the stated pain: slow handoffs."];
  assert.deepEqual(
    getLeadTalkingPoints({
      manual_review: true,
      routing_reason: "Manual review",
      action_plan: { talking_points: points },
    }),
    points,
  );
});

test("supplies evidence-review prompts when a manual-review plan has no talking points", () => {
  const points = getLeadTalkingPoints({
    manual_review: true,
    routing_reason: "Company name field is missing or contains a placeholder value.",
    action_plan: { talking_points: [] },
  });

  assert.equal(points.length, 2);
  assert.match(points[0], /company name/i);
  assert.match(points[1], /confirmed facts from unknown details/i);
});

test("does not invent talking points for a completed plan", () => {
  assert.deepEqual(
    getLeadTalkingPoints({
      manual_review: false,
      routing_reason: "Qualified lead.",
      action_plan: { talking_points: [] },
    }),
    [],
  );
  assert.deepEqual(getLeadTalkingPoints(null), []);
});
