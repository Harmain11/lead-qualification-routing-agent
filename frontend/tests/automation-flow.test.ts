import assert from "node:assert/strict";
import { test } from "node:test";
import { flowProblems, insertStepAfter, orderSteps, removeStep } from "../src/automationFlow.ts";
import type { PlaybookNode } from "../src/operationsApi.ts";

const node = (id: string, type: PlaybookNode["type"], next: string | null, extra: Partial<PlaybookNode> = {}): PlaybookNode => ({
  id, type, next, otherwise: null, x: 0, y: 0, wait_hours: 24, condition: "hot", text: "", ...extra,
});
const label = (item: PlaybookNode) => item.id;

const linear = [
  node("end", "end", null),
  node("draft", "draft", "end"),
  node("start", "start", "wait"),
  node("wait", "wait", "draft"),
];

test("steps are listed in run order regardless of storage order", () => {
  assert.deepEqual(orderSteps(linear).ordered.map((item) => item.id), ["start", "wait", "draft", "end"]);
  assert.equal(orderSteps(linear).unreachable.length, 0);
  assert.deepEqual(flowProblems(linear, label), []);
});

test("branches list the yes path before the no path", () => {
  const branched = [
    node("start", "start", "check"),
    node("check", "condition", "call", { otherwise: "nurture" }),
    node("call", "coach", "end"),
    node("nurture", "draft", "end"),
    node("end", "end", null),
  ];
  assert.deepEqual(orderSteps(branched).ordered.map((item) => item.id), ["start", "check", "call", "end", "nurture"]);
  assert.deepEqual(flowProblems(branched, label), []);
});

test("insert and remove keep the flow connected", () => {
  const inserted = insertStepAfter(linear, "wait", node("note", "note", null, { text: "Check fit" }));
  assert.deepEqual(orderSteps(inserted).ordered.map((item) => item.id), ["start", "wait", "note", "draft", "end"]);
  const removed = removeStep(inserted, "wait");
  assert.deepEqual(orderSteps(removed).ordered.map((item) => item.id), ["start", "note", "draft", "end"]);
  assert.deepEqual(flowProblems(removed, label), []);
  assert.equal(removeStep(linear, "start"), linear);
});

test("problems are reported in plain language", () => {
  const broken = [
    node("start", "start", "a"),
    node("a", "note", "a"),
    node("orphan", "draft", "end"),
    node("end", "end", null),
  ];
  const problems = flowProblems(broken, label);
  assert.ok(problems.some((item) => item.includes("needs its note text")));
  assert.ok(problems.some((item) => item.includes("not connected")));
  assert.ok(problems.some((item) => item.includes("loops back")));
  assert.ok(flowProblems([node("start", "start", null)], label).some((item) => item.includes("end step")));
});
