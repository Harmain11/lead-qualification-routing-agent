import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test, { before, after } from "node:test";
import { createServer, type ViteDevServer } from "vite";
import type { Quote } from "../src/v3/deals/dealStore.ts";

let vite: ViteDevServer;
let quoteTotals: typeof import("../src/v3/deals/dealStore.ts").quoteTotals;
let stageFromBackend: typeof import("../src/v3/deals/dealStore.ts").stageFromBackend;
let viewDeal: typeof import("../src/v3/deals/dealStore.ts").viewDeal;
let blankDeal: typeof import("../src/v3/deals/dealStore.ts").blankDeal;
before(async () => {
  vite = await createServer({ configFile: false, optimizeDeps: { noDiscovery: true, include: [] }, server: { middlewareMode: true, ws: false }, appType: "custom" });
  ({ quoteTotals, stageFromBackend, viewDeal, blankDeal } = await vite.ssrLoadModule("/src/v3/deals/dealStore.ts"));
});
after(async () => { await vite?.close(); });

test("quote totals round line discounts, recurring terms, global discount and tax to cents", () => {
  const totals = quoteTotals({
    termMonths: 3, discountPct: 10, taxPct: 7.5,
    lines: [
      { id: "one", name: "One", description: "", unit: "item", unitPrice: 10.05, quantity: 3, discountPct: 10, billing: "one_time" },
      { id: "monthly", name: "Monthly", description: "", unit: "month", unitPrice: 0.15, quantity: 1, discountPct: 10, billing: "monthly" },
    ],
  });
  assert.equal(totals.subtotal, 24.80);
  assert.equal(totals.tax, 1.86);
  assert.equal(totals.contractValue, 26.66);
});

const source = (path: string) => readFile(new URL(path, import.meta.url), "utf8");

const lead = (overrides: Record<string, unknown> = {}) =>
  ({
    id: "lead-1",
    received_at: new Date(Date.now() - 3 * 86_400_000).toISOString(),
    budget: null,
    outcome: null,
    routing: null,
    ...overrides,
  }) as never;

test("quote totals: line discounts, quote discount, monthly term and tax", () => {
  const quote = {
    lines: [
      { id: "a", name: "Build", description: "", quantity: 1, unitPrice: 10000, billing: "one_time", unit: "project", discountPct: 10 },
      { id: "b", name: "Retainer", description: "", quantity: 1, unitPrice: 1000, billing: "monthly", unit: "month", discountPct: 0 },
    ],
    discountPct: 0,
    taxPct: 10,
    termMonths: 6,
  } as unknown as Quote;
  const totals = quoteTotals(quote);
  assert.equal(totals.oneTime, 9000);
  assert.equal(totals.monthly, 1000);
  assert.equal(totals.subtotal, 15000);
  assert.equal(totals.contractValue, 16500);
});

test("API sales stages map onto deal desk columns", () => {
  assert.equal(stageFromBackend("new_lead"), "new");
  assert.equal(stageFromBackend("follow_up_sent"), "contacted");
  assert.equal(stageFromBackend("meeting_scheduled"), "engaged");
  assert.equal(stageFromBackend("proposal_sent"), "quoted");
  assert.equal(stageFromBackend("closed_won"), "won");
  assert.equal(stageFromBackend("nurture"), null);
});

test("a recorded outcome always wins over a saved commercial stage", () => {
  const deal = { ...blankDeal("lead-1"), stage: "negotiation" as const };
  const won = viewDeal(lead({ outcome: { pipeline_stage: "closed_won", closed_revenue: 48000, closed_at: null } }), deal, "proposal_sent");
  assert.equal(won.stage, "won");
  assert.equal(won.stageSource, "outcome");
  assert.equal(won.value, 48000);
  const open = viewDeal(lead(), deal, "proposal_sent");
  assert.equal(open.stage, "negotiation");
  assert.equal(open.stageSource, "workspace");
  const budgetOnly = viewDeal(lead({ budget: 20000 }), blankDeal("lead-1"), "proposal_sent");
  assert.equal(budgetOnly.value, null);
  assert.equal(budgetOnly.weighted, null);
  const recorded = viewDeal(lead({ outcome: { pipeline_stage: "proposal", opportunity_value: 20000, currency: "USD" } }), blankDeal("lead-1"), "proposal_sent", 0.25);
  assert.equal(recorded.stage, "quoted");
  assert.equal(recorded.value, 20000);
  assert.equal(recorded.weighted, 20000 * 0.25);
});

test("stage moves the API records still require a written comment and use the progress endpoint", async () => {
  const sheet = await source("../src/v3/deals/StageMoveSheet.tsx");
  assert.match(sheet, /updateLeadProgress\(lead\.id/);
  assert.match(sheet, /needsNote && note\.trim\(\)\.length < 3/);
  assert.match(sheet, /This lead is closed/);
  // Commercial-only stages use the workspace deal record.
  assert.match(sheet, /dealActions\.setStage\(lead\.id, target, note\.trim\(\) \|\| undefined\)/);
});

test("pipeline moves are gated by edit permission and always confirm", async () => {
  const board = await source("../src/v3/screens/PipelineBoard.tsx");
  assert.match(board, /if \(!canEdit \|\| view\.stage === target\) return;\s*setMove/);
  assert.match(board, /draggable=\{canEdit\}/);
  const app = await source("../src/App.tsx");
  assert.match(app, /canEdit=\{can\(workspacePermissions, "leads\.edit"\)\}/);
});

test("outreach stays paused for review holds even when Email is requested from elsewhere", async () => {
  const record = await source("../src/v3/screens/LeadRecord.tsx");
  assert.match(record, /\{open && !blocked && historyLoaded && !historyFailed && \(/);
});
