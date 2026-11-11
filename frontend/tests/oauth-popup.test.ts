import assert from "node:assert/strict";
import test from "node:test";

import { shouldCloseOAuthPopupCallback } from "../src/oauthPopup.ts";

test("closes a successful integration callback only when it is an OAuth popup", () => {
  const callback = new URLSearchParams({ integration: "gmail", status: "connected" });
  assert.equal(shouldCloseOAuthPopupCallback(callback, "corral-oauth-gmail-123", false), true);
  assert.equal(shouldCloseOAuthPopupCallback(callback, "", true), true);
  assert.equal(shouldCloseOAuthPopupCallback(callback, "", false), false);
});

test("does not close unrelated windows or incomplete callback results", () => {
  assert.equal(
    shouldCloseOAuthPopupCallback(new URLSearchParams({ integration: "gmail" }), "corral-oauth-gmail-123", true),
    false,
  );
  assert.equal(
    shouldCloseOAuthPopupCallback(new URLSearchParams({ integration: "gmail", status: "cancelled" }), "corral-oauth-gmail-123", true),
    false,
  );
});
