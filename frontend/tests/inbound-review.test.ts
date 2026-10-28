import assert from "node:assert/strict";
import { test } from "node:test";
import { recentReviewBatch } from "../src/inboundReview.ts";

test("recent review uses the chosen limit and small request pages", () => {
  assert.deepEqual(recentReviewBatch(), { limit: 100, pageSize: 25 });
  assert.deepEqual(recentReviewBatch(7), { limit: 7, pageSize: 7 });
  assert.deepEqual(recentReviewBatch(5000), { limit: 5000, pageSize: 25 });
});

test("invalid review sizes cannot be submitted", () => {
  for (const value of [0, -1, 5001, 2.5, NaN, Infinity]) {
    assert.throws(() => recentReviewBatch(value), /whole number/);
  }
});
