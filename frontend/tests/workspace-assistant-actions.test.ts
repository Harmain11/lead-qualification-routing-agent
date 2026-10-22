import assert from "node:assert/strict";
import { after, before, test } from "node:test";
import { createServer, type ViteDevServer } from "vite";
import type { WorkspaceAssistantAction } from "../src/api.ts";

let vite: ViteDevServer;
let actions: typeof import("../src/components/workspaceAssistantActions");
const originalFetch = globalThis.fetch;
const originalDocument = Object.getOwnPropertyDescriptor(globalThis, "document");
const requests: Array<{ url: string; method: string; body: Record<string, unknown> }> = [];
const permissions = new Set(["leads.view", "leads.edit"]);
const targetId = "00000000-0000-0000-0000-000000000001";
const noNavigate = () => { throw new Error("Unexpected navigation"); };

before(async () => {
  Object.defineProperty(globalThis, "document", { configurable: true, value: { cookie: "" } });
  globalThis.fetch = async (url, options) => {
    requests.push({
      url: String(url), method: options?.method ?? "GET",
      body: JSON.parse(String(options?.body ?? "{}")),
    });
    return new Response(JSON.stringify({ id: targetId }), { status: 200 });
  };
  vite = await createServer({
    configFile: false, root: process.cwd(),
    server: { middlewareMode: true, hmr: false },
    optimizeDeps: { noDiscovery: true, include: [] },
    appType: "custom",
  });
  actions = await vite.ssrLoadModule("/src/components/workspaceAssistantActions.ts");
});

after(async () => {
  globalThis.fetch = originalFetch;
  if (originalDocument) Object.defineProperty(globalThis, "document", originalDocument);
  else Reflect.deleteProperty(globalThis, "document");
  await vite?.close();
});

function proposal(kind: WorkspaceAssistantAction["kind"], payload?: object): WorkspaceAssistantAction {
  return {
    kind, target_id: targetId, target_label: "Mina Chen", title: "Review changes", summary: "Review first",
    area: null, email: null, role: null, enabled: null,
    email_recipient: null, email_cc: null, email_bcc: null, email_subject: null, email_body: null,
    payload_json: payload ? JSON.stringify(payload) : null,
  };
}

test("permissions are required before any lead-write API call", async () => {
  requests.length = 0;
  const action = proposal("update_lead_profile", { phone: "+442079460000" });
  assert.equal(actions.canApplyWorkspaceAssistantAction(action, new Set(["leads.view"])), false);
  await assert.rejects(
    actions.applyWorkspaceAssistantAction(action, new Set(["leads.view"]), noNavigate, noNavigate),
    /leads.edit/,
  );
  assert.equal(requests.length, 0);
});

test("approved profile updates preserve reviewed values and explicit clearing", async () => {
  requests.length = 0;
  const result = await actions.applyWorkspaceAssistantAction(
    proposal("update_lead_profile", { phone: null, job_title: "Director" }),
    permissions, noNavigate, noNavigate,
  );
  assert.match(result, /Updated Mina Chen/);
  assert.equal(requests[0].url, `/v1/leads/${targetId}/profile`);
  assert.equal(requests[0].method, "PATCH");
  assert.deepEqual(requests[0].body, { phone: null, job_title: "Director" });
});

test("approved notes, progress and reevaluation call their existing lead APIs", async () => {
  for (const [kind, payload, suffix, method] of [
    ["add_lead_note", { note: "Requested a proposal" }, "notes", "PUT"],
    ["record_lead_progress", { stage: "responded", note: "Customer replied" }, "progress", "POST"],
    ["reevaluate_lead", undefined, "reevaluate", "POST"],
  ] as const) {
    requests.length = 0;
    await actions.applyWorkspaceAssistantAction(proposal(kind, payload), permissions, noNavigate, noNavigate);
    assert.equal(requests.length, 1);
    assert.equal(requests[0].method, method);
    assert.match(requests[0].url, new RegExp(suffix));
    if (kind === "add_lead_note") assert.equal(requests[0].body.body, "Requested a proposal");
    if (kind === "record_lead_progress") assert.deepEqual(requests[0].body, payload);
  }
});

test("opening a lead is read-only and sending remains in the email approval card", async () => {
  requests.length = 0;
  let opened = "";
  await actions.applyWorkspaceAssistantAction(proposal("open_lead"), permissions, noNavigate, (id) => { opened = id; });
  assert.equal(opened, targetId);
  await assert.rejects(
    actions.applyWorkspaceAssistantAction(proposal("send_email"), permissions, noNavigate, noNavigate),
    /approval card/,
  );
  assert.equal(requests.length, 0);
});

test("opening the Dialer requires dialer access and never places a call", async () => {
  const action = { ...proposal("open_module"), area: "dialer" as const };
  const allowed = new Set(["dialer.call"]);
  const denied = new Set<string>();
  let opened = "";

  assert.equal(actions.canOpenAssistantArea("dialer", allowed), true);
  assert.equal(actions.canOpenAssistantArea("dialer", denied), false);
  const result = await actions.applyWorkspaceAssistantAction(
    action,
    allowed,
    (area) => { opened = area; },
    noNavigate,
  );

  assert.equal(opened, "dialer");
  assert.equal(result, "Opened the Dialer. Corral has not placed a call.");
  assert.equal(requests.length, 0);
  await assert.rejects(
    actions.applyWorkspaceAssistantAction(action, denied, noNavigate, noNavigate),
    /access/,
  );
});
