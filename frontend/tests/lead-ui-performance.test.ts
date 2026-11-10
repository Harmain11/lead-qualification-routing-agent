import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { shareRequest } from "../src/views/emailRequestCache.ts";

const source = (path: string) => readFile(new URL(path, import.meta.url), "utf8");

test("email requests with the same cache key share one in-flight request", async () => {
  const requests = new Map<string, Promise<number>>();
  let calls = 0;
  let release: ((value: number) => void) | undefined;
  const load = () => {
    calls += 1;
    return new Promise<number>((resolve) => { release = resolve; });
  };

  const first = shareRequest(requests, "inbox", load);
  const second = shareRequest(requests, "inbox", load);

  assert.equal(calls, 1);
  assert.equal(first, second);
  release?.(7);
  assert.deepEqual(await Promise.all([first, second]), [7, 7]);
  assert.equal(requests.size, 0);
});

test("a failed shared email request can be retried", async () => {
  const requests = new Map<string, Promise<number>>();
  await assert.rejects(shareRequest(requests, "thread", async () => {
    throw new Error("temporary");
  }));
  assert.equal(requests.size, 0);
  assert.equal(await shareRequest(requests, "thread", async () => 9), 9);
});

test("lead actions expose re-analysis and Ask Corral uses readable user-message colors", async () => {
  const record = await source("../src/v3/screens/LeadRecord.tsx");
  const css = await source("../src/v3/refine.css");

  assert.match(record, /reevaluateLead/);
  assert.match(record, />\s*\{reanalyzing \? "Analyzing…" : "Re-analyze"\}\s*<\/Button>/);
  assert.match(css, /\.lead-agent-message\.is-user p\s*\{[^}]*color:\s*var\(--on-tint\)/s);
});

test("Inbox keeps shared requests alive across navigation and quietly forces current data", async () => {
  const inbox = await source("../src/views/EmailWorkspace.tsx");

  assert.match(inbox, /const mailboxRequests = new Map/);
  assert.match(inbox, /const threadRequests = new Map/);
  assert.match(inbox, /shareRequest\(mailboxRequests/);
  assert.match(inbox, /shareRequest\(threadRequests/);
  assert.match(inbox, /forceMailboxRefresh\.current = true;\s*backgroundRefresh\.current = true;/);
  assert.match(inbox, /onSent=\{\(\) => \{[\s\S]*?forceMailboxRefresh\.current = true;/);
});
