import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { operationState } from "../src/components/operationState.ts";
import { componentTotals } from "../src/components/record/scoreModel.ts";

// Safeguards for the v3 interface: the new design must not weaken approval gates,
// permission checks, scoring semantics or the "never claim completion" rule.
const source = (path: string) => readFile(new URL(path, import.meta.url), "utf8");

test("a fresh proposal never reads as completed", () => {
  for (const kind of ["update_profile", "add_note", "record_progress", "sync_crm", "delete_lead", "reevaluate_lead", "refresh_email"]) {
    const state = operationState(kind, {});
    assert.equal(state.key, "approval", kind);
    assert.equal(state.label, "Awaiting your approval");
  }
  assert.equal(operationState("open_email", {}).key, "suggested");
  assert.equal(operationState("draft_email", {}).label, "Suggested · opens a draft");
});

test("drafts and the dialer are never presented as sent or called", () => {
  assert.equal(operationState("draft_email", { done: true }).label, "Draft open · not sent");
  assert.equal(operationState("send_follow_up", { done: true }).label, "Draft open · not sent");
  assert.equal(operationState("open_dialer", { done: true }).label, "Dialer opened · no call placed");
});

test("running, failed and cancelled states take precedence correctly", () => {
  assert.equal(operationState("sync_crm", { busy: true }).key, "running");
  assert.equal(operationState("sync_crm", { error: "CRM write failed" }).key, "failed");
  assert.equal(operationState("delete_lead", { cancelled: true }).label, "Cancelled · nothing changed");
  assert.equal(operationState("add_note", { done: true }).label, "Completed");
});

test("score story reads the recorded breakdown without recalculating", () => {
  const totals = componentTotals({
    component_scores: { intent_urgency: 25, engagement_quality: 20 },
    score_breakdown: [
      { component: "intent_urgency", key: "urgency", points: 15, max_points: 15, reason: "", evidence: [] },
      { component: "intent_urgency", key: "buying_stage", points: 0, max_points: 10, reason: "", evidence: [] },
      { component: "intent_urgency", key: "budget_signal", points: 5, max_points: 5, reason: "", evidence: [] },
      { component: "intent_urgency", key: "specific_request", points: 5, max_points: 5, reason: "", evidence: [] },
      { component: "engagement_quality", key: "message_detail", points: 6, max_points: 10, reason: "", evidence: [] },
      { component: "engagement_quality", key: "contact_completeness", points: 4, max_points: 5, reason: "", evidence: [] },
      { component: "engagement_quality", key: "pain_clarity", points: 5, max_points: 5, reason: "", evidence: [] },
      { component: "engagement_quality", key: "channel_context", points: 5, max_points: 5, reason: "", evidence: [] },
    ],
  });
  assert.deepEqual(totals.map((item) => [item.key, item.points, item.max]), [
    ["intent_urgency", 25, 35],
    ["engagement_quality", 20, 25],
  ]);
  assert.ok(!totals.some((item) => item.key === "icp_fit"), "no ICP component is invented without a published ICP");
});

test("records without a breakdown fall back to the documented 40/35/25 maxima", () => {
  const totals = componentTotals({ component_scores: { icp_fit: 30, intent_urgency: 20, engagement_quality: 10 }, score_breakdown: [] });
  assert.deepEqual(totals.map((item) => item.max), [40, 35, 25]);
});

test("permission fallback order includes the calendar and checks view permissions", async () => {
  const app = await source("../src/App.tsx");
  assert.match(app, /const FALLBACK_ORDER: View\[\] = \[\s*"dashboard", "leads", "email", "calendar", "contacts", "accounts", "pipeline", "agents",\s*"analytics", "review", "integrations", "dialer", "team", "settings",\s*\];/);
  assert.match(app, /FALLBACK_ORDER\.find\(\(item\) => hasViewPermission\(item, permissions\)\)/);
  assert.match(app, /const visibleNavigation = navigation\.filter\(\(item\) =>\s*hasViewPermission\(item\.id, workspacePermissions\),?\s*\);/);
  for (const [view, permission] of [["dashboard", "analytics.view"], ["leads", "leads.view"], ["calendar", "leads.view"], ["review", "manual_review.view"], ["dialer", "dialer.call"], ["compliance", "workspace.manage"]]) {
    assert.match(app, new RegExp(`${view}: "${permission.replace(".", "\\.")}"`), view);
  }
});

test("bulk actions keep their confirmations and permission gates", async () => {
  const leads = await source("../src/v3/screens/LeadsScreen.tsx");
  assert.match(leads, /window\.confirm\(`Delete \$\{selectedIds\.size\} selected lead/);
  assert.match(leads, /window\.confirm\(`Transfer or update \$\{selectedIds\.size\} selected leads/);
  assert.match(leads, /\{canCrmSync && \(/);
  assert.match(leads, /\{canEdit && <Button size="sm" kind="destructive"/);
  assert.match(leads, /\{canExport && <Button/);
  assert.match(leads, /\{canEdit && <Button kind="filled" icon="plus"/);
  assert.match(leads, /canManageRouting && lead\.routing \?/);
});

test("the lead record keeps delete confirmation, CRM gating and enrolment gating", async () => {
  const record = await source("../src/v3/screens/LeadRecord.tsx");
  assert.match(record, /window\.confirm\("Delete this lead from the workspace\?/);
  assert.match(record, /\{canEdit && \(\s*<>\s*<MenuItem icon="edit"/);
  assert.match(record, /\{canEnrollAutomations && \(/);
  assert.match(record, /\{canUseAgent && \(/);
  assert.match(record, /canCrmSync && providers\.length > 0/);
  assert.match(record, /CRM writes only happen when you request them/);
  // Outreach stays paused for disqualified or review-held leads.
  assert.match(record, /disabled=\{Boolean\(blocked\)\}/);
});

test("delivery actions are attached to their rows and keep action-specific gates", async () => {
  const record = await source("../src/v3/screens/LeadRecord.tsx");
  const api = await source("../src/api.ts");
  const delivery = record.slice(record.indexOf("function Delivery"), record.indexOf("Follow-up email"));
  assert.match(delivery, /canManageRouting && notificationRetryable/);
  assert.match(delivery, /retryLeadNotification\(lead\.id\)/);
  assert.match(delivery, /canEdit && canViewEmail && !confirmationSent/);
  assert.match(delivery, /disabled=\{Boolean\(emailBlockReason\)\}/);
  assert.match(delivery, /onClick=\{onComposeEmail\}/);
  assert.match(delivery, /canCrmSync && providers\.length > 0/);
  assert.match(api, /\/v1\/leads\/\$\{leadId\}\/notifications\/retry/);
});

test("lead details can be edited from the action menu without changing CRM data", async () => {
  const record = await source("../src/v3/screens/LeadRecord.tsx");
  assert.match(record, /Edit lead details/);
  assert.match(record, /updateLeadProfile\(lead\.id, updates\)/);
  assert.match(record, /Company name/);
  assert.match(record, /Product or service/);
  assert.match(record, /qualification refreshed\. No CRM was changed/);
});

test("lead Calls tab is history-only while the Dialer keeps its call flow", async () => {
  const record = await source("../src/v3/screens/LeadRecord.tsx");
  const calls = await source("../src/components/CallsPanel.tsx");
  const dialer = await source("../src/views/DialerView.tsx");
  const review = await source("../src/components/CallReviewDetails.tsx");
  const css = await source("../src/views/DialerView.css");
  const callSection = record.slice(record.indexOf("/* ------------------------------------------------------------- Calls */"), record.indexOf("/* ---------------------------------------------------------- Overview */"));

  assert.match(callSection, /historyOnly/);
  assert.doesNotMatch(callSection, /Open Phone|Calls placed here/);
  assert.match(calls, /historyOnly \? "Call history & analysis"/);
  assert.match(calls, /Load transcript analysis/);
  assert.match(calls, /<CallReviewDetails/);
  assert.match(calls, /!historyOnly && canCall/);
  assert.match(dialer, /canCall=\{canCallLeads\}/);
  assert.match(review, /className="call-transcript-text"/);
  assert.match(css, /\.call-transcript-text[^}]*white-space:pre-wrap; overflow-wrap:anywhere;/);
});

test("Ask Corral is only offered to members who may execute agent actions", async () => {
  const app = await source("../src/App.tsx");
  assert.match(app, /\{can\(workspacePermissions, "agents\.execute"\) && \(\s*<button\s+type="button"\s+className="v-ask"/);
  assert.match(app, /\{can\(workspacePermissions, "agents\.execute"\) && \(\s*<CorralAgentDock/);
});

test("v3 never draws outlines around buttons or cards", async () => {
  const css = (await Promise.all(["foundation", "components", "shell", "screens"].map((name) => source(`../src/v3/${name}.css`)))).join("\n");
  assert.match(css, /--shadow-card: none;/);
  assert.match(css, /--shadow-control: none;/);
  assert.doesNotMatch(css, /\.v-btn[^{]*\{[^}]*border:\s*1px/);
  assert.doesNotMatch(css, /\.v-card[^{]*\{[^}]*border:\s*1px/);
});
