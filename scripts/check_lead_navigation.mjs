import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
const require = createRequire(new URL("../frontend/package.json", import.meta.url));
const { build } = require("esbuild");
const React = require("react");
const { renderToStaticMarkup } = require("react-dom/server");
const storage = new Map();
globalThis.localStorage = {
  getItem: (key) => storage.get(key) ?? null,
  setItem: (key, value) => storage.set(key, value),
};
globalThis.window = { location: { href: "https://corral.test/?integration=gmail" }, localStorage };

async function load(path) {
  const result = await build({
    entryPoints: [fileURLToPath(new URL(path, import.meta.url))],
    bundle: true, write: false, platform: "node", format: "cjs",
    packages: "external", loader: { ".css": "empty" }, jsx: "automatic",
    define: { "import.meta.env": "{}" },
  });
  const module = { exports: {} };
  new Function("require", "module", "exports", result.outputFiles[0].text)(require, module, module.exports);
  return module.exports;
}
const { LeadDetail } = await load("../frontend/src/components/LeadDetail.tsx");
const { DashboardInsights } = await load("../frontend/src/components/DashboardInsights.tsx");
const { usePinnedLeadSections } = await load("../frontend/src/components/PinnedLeadNavigation.tsx");
const { parseLeadSection, leadSectionHref } = await load("../frontend/src/leadSections.ts");
const lead = {
  id: "11111111-1111-4111-8111-111111111111", source: "website",
  company_name: "Test company", full_name: "Test buyer", email: "buyer@example.test",
  message: "Verified original request", product_or_service: "Consulting",
  received_at: "2026-10-08T09:00:00Z", pipeline_stage: "new",
  analysis: null, qualification: null, routing: null, crm_sync: null,
};
for (const section of ["overview", "ai", "follow-ups", "closing", "history"]) {
  const html = renderToStaticMarkup(React.createElement(LeadDetail, {
    lead, activeSection: section, onSectionChange() {}, canEdit: true,
    canCrmSync: true, onTogglePin() {}, isSectionPinned: false,
  }));
  assert.equal((html.match(/role="tab"/g) || []).length, 5);
  assert.equal((html.match(/role="tabpanel"/g) || []).length, 5);
  assert.equal((html.match(/role="tabpanel"[^>]*hidden=""/g) || []).length, 4);
  assert.match(html, /Verified original request/);
  assert.match(html, /Canonical commercial profile/);
  assert.match(html, /lead-progress-form/);
  assert.match(html, /lead-section-pin/);
  assert.match(html, /AI-assessed buying stage/);
  assert.match(html, /lead-panel-history/);
}
const readonly = renderToStaticMarkup(React.createElement(LeadDetail, {
  lead, activeSection: "closing", onSectionChange() {}, canEdit: false,
}));
assert.doesNotMatch(readonly, /lead-progress-form|Delete lead|Edit title/);
assert.equal(parseLeadSection("invalid"), "overview");
assert.equal(parseLeadSection("history"), "history");
assert.equal(new URL(leadSectionHref(lead.id, "closing"), window.location.href).searchParams.get("integration"), null);

let controls;
function Harness({ user = "user-a", workspace = "workspace-a" }) {
  controls = usePinnedLeadSections(user, workspace);
  return null;
}
const renderPins = (props) => renderToStaticMarkup(React.createElement(Harness, props));
renderPins();
controls.toggle({ leadId: lead.id, company: lead.company_name, section: "closing" });
renderPins();
assert.equal(controls.pins.length, 1);
renderPins({ workspace: "workspace-b" });
assert.equal(controls.pins.length, 0, "Pins leaked across workspaces.");
renderPins({ user: "user-b" });
assert.equal(controls.pins.length, 0, "Pins leaked across users.");
renderPins();
controls.toggle(controls.pins[0]);
renderPins();
assert.equal(controls.pins.length, 0, "Unpin did not persist.");
storage.set("corral.lead.pins.v1:user-a:workspace-a", JSON.stringify([
  { leadId: lead.id, company: "Invalid section", section: "invalid" },
  { leadId: "invalid", company: "Invalid record", section: "history" },
]));
renderPins();
assert.equal(controls.pins.length, 0);

const visible = new Set(["revenue-trend", "source-qualification", "source-quality"]);
const channels = { channels: [
  { channel: "website", lead_volume: 12, qualification_rate: 50, average_score: 75 },
  { channel: "email", lead_volume: 6, qualification_rate: 25, average_score: 60 },
  { channel: "no_volume", lead_volume: 0, qualification_rate: 100, average_score: 100 },
] };
const revenue = { data_status: "manual", currency: "USD", over_time: [
  { period: "2026-10-01", value: 0 }, { period: "2026-10-08", value: 1200 },
] };
const chartHtml = renderToStaticMarkup(React.createElement(DashboardInsights, { channels, revenue, visible }));
assert.match(chartHtml, /50%/);
assert.match(chartHtml, /75\s*\/\s*100/);
assert.match(chartHtml, /12 leads/);
assert.doesNotMatch(chartHtml, /No Volume/);
assert.match(chartHtml, /Manual/);
assert.match(chartHtml, /\$1,200/);
assert.doesNotMatch(chartHtml, /NaN|Infinity/);
assert.match(renderToStaticMarkup(React.createElement(DashboardInsights, {
  channels: null, revenue: { ...revenue, data_status: "unavailable" }, visible,
})), /Revenue is unavailable/);
assert.equal(renderToStaticMarkup(React.createElement(DashboardInsights, { channels, revenue, visible: new Set() })), "");
console.log("Lead navigation checks passed: five accessible panels, preserved information/actions, read-only roles, safe direct links, scoped pin persistence/unpin, and actual chart figures/empty states.");
