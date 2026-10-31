import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { parseCommaSeparatedList } from "../src/textEntry.ts";

const readSource = (path: string) =>
  readFile(new URL(path, import.meta.url), "utf8");

test("ICP list preserves raw typing and paste until blur", async () => {
  const source = await readSource("../src/views/IcpView.tsx");
  assert.match(source, /onChange=\{\(event\) => setDraft\(event\.target\.value\)\}/);
  assert.match(source, /onBlur=\{\(\) => \{ const normalized = parseCommaSeparatedList\(draft\)/);
  assert.deepEqual(parseCommaSeparatedList("AI, sales, R&D!"), ["AI", "sales", "R&D!"]);
  assert.deepEqual(parseCommaSeparatedList(" North America,  SaaS "), [
    "North America",
    "SaaS",
  ]);
});

test("representative free-form fields keep the exact typed value", async () => {
  const records = await readSource("../src/views/RecordsView.tsx");
  const lead = await readSource("../src/v3/screens/LeadsScreen.tsx");
  const detail = await readSource("../src/v3/screens/LeadRecord.tsx");
  const composer = await readSource("../src/components/EmailComposer.tsx");
  const assistantChat = await readSource("../src/components/WorkspaceAssistantChat.tsx");

  assert.match(records, /value=\{form\.name\}[\s\S]*?onChange=\{\((?:event|e)\) =>\s*setForm\(\{ \.\.\.form, name: (?:event|e)\.target\.value \}\)/);
  assert.match(lead, /onChange=\{\(event\) => set\("message", event\.target\.value\)\}/);
  assert.match(detail, /<EmailComposer/);
  assert.match(composer, /contentEditable=\{!busy\}[\s\S]*?onInput=\{syncBody\}/);
  assert.match(composer, /setBody\(editor\.innerText\.replace\(/);
  assert.match(assistantChat, /contentEditable=\{message\.actionStatus !== "sending"[\s\S]*?onInput=\{\(event\) => updateEmailBody/);
  assert.match(assistantChat, /const body = editor\.innerText\.replace\(/);
});
