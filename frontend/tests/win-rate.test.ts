import assert from "node:assert/strict";
import { after, before, test } from "node:test";
import { createServer, type ViteDevServer } from "vite";

let vite: ViteDevServer;
let module: typeof import("../src/v3/deals/useWorkspaceLeads");
const originalDocument = Object.getOwnPropertyDescriptor(globalThis, "document");

before(async () => {
  Object.defineProperty(globalThis, "document", { configurable: true, value: { cookie: "" } });
  vite = await createServer({
    configFile: false, root: process.cwd(),
    server: { middlewareMode: true, hmr: false },
    optimizeDeps: { noDiscovery: true, include: [] },
    appType: "custom",
  });
  module = await vite.ssrLoadModule("/src/v3/deals/useWorkspaceLeads.ts");
});

after(async () => {
  if (originalDocument) Object.defineProperty(globalThis, "document", originalDocument);
  else Reflect.deleteProperty(globalThis, "document");
  await vite?.close();
});

const lead = (stage: string | null) => ({ outcome: stage ? { pipeline_stage: stage } : null }) as never;

test("a forecast needs enough closed deals to be a rate", () => {
  // Two wins and no losses is not a 100% win rate.
  assert.equal(module.observedWinRate([lead("closed_won"), lead("closed_won"), lead("quoted")]), null);
  assert.equal(module.observedWinRate([]), null);
});

test("win rate is won over closed once the sample is large enough", () => {
  const leads = [
    lead("closed_won"), lead("closed_won"), lead("closed_lost"), lead("closed_lost"), lead("closed_lost"),
    lead("quoted"), lead(null),
  ];
  assert.equal(module.observedWinRate(leads), 0.4);
});
