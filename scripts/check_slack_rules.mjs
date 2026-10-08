// Pure frontend rule creation checks: no provider calls or database writes.
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import ts from "../frontend/node_modules/typescript/lib/typescript.js";

const source = await readFile(new URL("../frontend/src/slackRouting.ts", import.meta.url), "utf8");
let output = ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 },
}).outputText;
const idSource = await readFile(new URL("../frontend/src/clientIds.ts", import.meta.url), "utf8");
const idOutput = ts.transpileModule(idSource, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 },
}).outputText;
const idUrl = `data:text/javascript;base64,${Buffer.from(idOutput).toString("base64")}`;
output = output.replace(/(["'])\.\/clientIds\1/g, JSON.stringify(idUrl));
const { createClientId: newSlackRuleId } = await import(idUrl);
const { createSlackRule } = await import(`data:text/javascript;base64,${Buffer.from(output).toString("base64")}`);
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
assert.match(newSlackRuleId(), uuid);
assert.match(newSlackRuleId({ getRandomValues: (bytes) => crypto.getRandomValues(bytes) }), uuid);
assert.throws(() => newSlackRuleId({}), /supported browser/);
assert.throws(() => createSlackRule([], null, 0), /Connect and verify Slack/);
const first = { id: "first", status: "verified", is_active: true, metadata: { slack_channel_id: "C123" } };
const second = { ...first, id: "second", metadata: { slack_channel_id: " C456 " } };
const rule = createSlackRule([first, second], "second", 2);
assert.equal(rule.connection_id, "second");
assert.equal(rule.channel_id, "C456");
assert.equal(rule.name, "Rule 3");
assert.match(rule.id, uuid);
assert.deepEqual(rule.sources, []);
assert.deepEqual(rule.qualification_bands, []);
assert.equal(rule.destination_type, "channel");
assert.equal(rule.weight, 1);
assert.equal(createSlackRule([first], "stale", 0).connection_id, "first");
assert.throws(() => createSlackRule([{ ...first, is_active: false }], null, 0), /verify Slack/);
assert.throws(() => createSlackRule([{ ...first, status: "connected" }], null, 0), /verify Slack/);
assert.notEqual(createSlackRule([first], null, 0).id, createSlackRule([first], null, 1).id);
console.log("Slack rule creation checks passed, including embedded-browser UUID support and connection prerequisites.");
