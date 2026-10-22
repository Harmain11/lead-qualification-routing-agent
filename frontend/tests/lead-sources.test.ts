import assert from "node:assert/strict";
import test from "node:test";
import {
  leadSourceCategory,
  leadSourceDetailLabel,
  leadSourceDisplay,
  leadSourceLabel,
} from "../src/leadSources.ts";

test("manual lead records use the Manually added category", () => {
  assert.equal(leadSourceCategory("manual"), "manual");
  assert.equal(leadSourceCategory("website", "manual-control-center"), "manual");
  assert.equal(leadSourceLabel("manual"), "Manually added");
  assert.equal(leadSourceLabel("website", "manual-control-center"), "Manually added");
  assert.equal(leadSourceDisplay("website", "manual-control-center"), "Manually added");
  assert.equal(leadSourceDetailLabel("manual-control-center"), "Added in Corral");
});

test("web-form submissions remain categorized as Web form", () => {
  assert.equal(leadSourceCategory("website", "public-website-form"), "website");
  assert.equal(leadSourceLabel("website"), "Web form");
});
