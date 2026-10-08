// Render the real component to check known, unknown and unavailable AI states.
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { createRequire } from "node:module";
import { pathToFileURL } from "node:url";
import ts from "../frontend/node_modules/typescript/lib/typescript.js";

const require = createRequire(new URL("../frontend/package.json", import.meta.url));
const React = require("react");
const { renderToStaticMarkup } = require("react-dom/server");
const source = await readFile(new URL("../frontend/src/components/AiStageAssessment.tsx", import.meta.url), "utf8");
let output = ts.transpileModule(source, {
  compilerOptions: { jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 },
}).outputText;
output = output.replace(/(["'])react\/jsx-runtime\1/g, JSON.stringify(pathToFileURL(require.resolve("react/jsx-runtime")).href));
const { AiStageAssessment } = await import(`data:text/javascript;base64,${Buffer.from(output).toString("base64")}`);
const render = (analysis) => renderToStaticMarkup(React.createElement(AiStageAssessment, {analysis}));
const analysis = {
  status: "completed", requires_manual_review: false, overall_confidence: 0.99,
  context: {buying_stage: {
    value: "evaluation", information_kind: "inferred", confidence: 0.62,
    evidence: [{source_field: "message", excerpt: "We are evaluating options. <script>not executable</script>"}],
  }},
  summary: {buying_signal: "The buyer is comparing options."},
  recommended_action: "Arrange a discovery call.", created_at: "2026-10-08T09:00:00Z",
};
let html = render(analysis);
assert.match(html, />Evaluation</);
assert.match(html, /Stage confidence: 62%/);
assert.doesNotMatch(html, /Stage confidence: 99%/);
assert.match(html, /AI estimate/);
assert.match(html, /Why AI thinks this/);
assert.match(html, /&lt;script&gt;not executable&lt;\/script&gt;/);
assert.match(html, /Arrange a discovery call/);
assert.match(html, /not a confirmed win or loss/);
html = render({...analysis, context: {buying_stage: {...analysis.context.buying_stage, information_kind: "explicit"}}});
assert.match(html, /Explicitly stated/);
html = render({...analysis, status: "manual_review"});
assert.match(html, /Human review required/);
html = render({...analysis, context: {buying_stage: {...analysis.context.buying_stage, value: "unknown", information_kind: "unknown", confidence: 0}}});
assert.match(html, /Not enough evidence/);
assert.doesNotMatch(html, /Stage confidence:/);
html = render({...analysis, status: "provider_error"});
assert.match(html, /AI assessment unavailable/);
assert.doesNotMatch(html, />Evaluation</);
assert.match(render(null), /AI assessment unavailable/);
assert.doesNotThrow(() => render({...analysis, created_at: "invalid"}));
console.log("AI stage checks passed: estimated/explicit stages, stage-specific confidence, safe evidence, review warnings, unknown and unavailable states.");
