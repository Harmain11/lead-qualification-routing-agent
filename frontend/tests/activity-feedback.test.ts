import assert from "node:assert/strict";
import test from "node:test";
import { createServer } from "vite";

test("lead updates do not display the customer's inquiry as feedback", async () => {
  const vite = await createServer({ configFile: false, optimizeDeps: { noDiscovery: true, include: [] }, server: { middlewareMode: true, ws: false }, appType: "custom" });
  try {
    const { mutationStatusMessage } = await vite.ssrLoadModule("/src/activity.ts");
    assert.deepEqual(mutationStatusMessage("/v1/leads/123/owner", { id: "123", message: "Private customer email" }), { message: "Saved.", kind: "success" });
    assert.deepEqual(mutationStatusMessage("/v1/jobs", { status: "running", message: "Processing" }), { message: "Processing", kind: "info" });
  } finally {
    await vite.close();
  }
});
