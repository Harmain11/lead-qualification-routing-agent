import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
const require = createRequire(new URL("../frontend/package.json", import.meta.url));
const { build } = require("esbuild");
const React = require("react");
const { renderToStaticMarkup } = require("react-dom/server");
async function load(path) {
  const output = await build({
    entryPoints: [fileURLToPath(new URL(path, import.meta.url))],
    bundle: true, write: false, platform: "node", format: "cjs",
    packages: "external", loader: { ".css": "empty" }, jsx: "automatic",
  });
  const module = { exports: {} };
  new Function("require", "module", "exports", output.outputFiles[0].text)(require, module, module.exports);
  return module.exports;
}
const { Bars } = await load("../frontend/src/components/Charts.tsx");
const absolute = renderToStaticMarkup(React.createElement(Bars, {
  rows: [{ label: "A", value: 24 }, { label: "B", value: 15 }, { label: "C", value: 0 }],
  scaleMaximum: 100,
}));
for (const scale of ["0.24", "0.15", "0"]) assert(absolute.includes(`--bar-scale:${scale}`));
const relative = renderToStaticMarkup(React.createElement(Bars, {
  rows: [{ label: "Count A", value: 24 }, { label: "Count B", value: 12 }],
}));
assert(relative.includes("--bar-scale:1"));
assert(relative.includes("--bar-scale:0.5"));
const { DashboardInsights } = await load("../frontend/src/components/DashboardInsights.tsx");
const charts = renderToStaticMarkup(React.createElement(DashboardInsights, {
  channels: { channels: [
    { channel: "website", lead_volume: 2, qualified_leads: 1, scored_leads: 2, qualification_rate: 50, average_score: 24 },
    { channel: "offline", lead_volume: 1, qualified_leads: 0, scored_leads: 0, qualification_rate: 0, average_score: 0 },
  ] }, revenue: null, visible: new Set(["source-qualification", "source-quality"]),
}));
assert(charts.includes("--bar-scale:0.24"));
assert(charts.includes("--bar-scale:0.5"));
assert(charts.includes("1 qualified / 2 leads"));
assert(charts.includes("2 scored / 2 leads"));
assert(!charts.includes("0/100"), "An unscored source was shown as a genuine zero score.");
const { appPages, pageFromSearch, sectionFromSearch } = await load("../frontend/src/appNavigation.ts");
for (const page of appPages) assert.equal(pageFromSearch(`?view=${page}`), page);
assert.equal(pageFromSearch("?view=invalid"), "dashboard");
assert.equal(pageFromSearch("?integration=gmail"), "integrations");
assert.equal(pageFromSearch("?view=analytics&integration=gmail"), "analytics");
assert.equal(sectionFromSearch("?section=email-templates", "settings"), "email-templates");
assert.equal(sectionFromSearch("?section=invalid", "settings"), "");
console.log("Fixed score/percentage scales, real count labels, unscored-source handling and refresh-route parsing passed.");
