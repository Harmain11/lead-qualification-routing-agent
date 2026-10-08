// Development-only UI check. Closing and notification requests are intercepted
// inside this browser; no lead is changed and no live Slack message is sent.
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

const origin = process.env.REPLIT_DEV_DOMAIN && `https://${process.env.REPLIT_DEV_DOMAIN}`;
assert(origin, "This check requires the development preview domain.");
const profile = await mkdtemp(join(tmpdir(), "corral-member-sales-"));
const port = 9242;
const browser = spawn("chromium", [
  "--headless", "--no-sandbox", "--disable-gpu",
  `--remote-debugging-port=${port}`, `--user-data-dir=${profile}`, "about:blank",
], { stdio: "ignore" });
const pause = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
let socket;
try {
  let target;
  for (let index = 0; index < 80; index++) {
    try {
      target = await (await fetch(`http://127.0.0.1:${port}/json/new?about:blank`, { method: "PUT" })).json();
      break;
    } catch { await pause(100); }
  }
  assert(target?.webSocketDebuggerUrl, "Chromium did not start.");
  socket = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((resolve) => socket.addEventListener("open", resolve, { once: true }));
  let sequence = 0;
  const requests = new Map();
  const errors = [];
  socket.addEventListener("message", ({ data }) => {
    const message = JSON.parse(String(data));
    if (message.method === "Runtime.exceptionThrown") errors.push(message.params.exceptionDetails.text);
    const request = requests.get(message.id);
    if (!request) return;
    requests.delete(message.id);
    if (message.error) request.reject(new Error(message.error.message));
    else request.resolve(message.result);
  });
  const cdp = (method, params = {}) => new Promise((resolve, reject) => {
    const id = ++sequence;
    requests.set(id, { resolve, reject });
    socket.send(JSON.stringify({ id, method, params }));
  });
  const evaluate = async (expression) => {
    const result = await cdp("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true });
    assert(!result.exceptionDetails, JSON.stringify(result.exceptionDetails));
    return result.result.value;
  };
  const wait = async (expression) => {
    for (let index = 0; index < 150; index++) {
      if (await evaluate(expression)) return;
      await pause(100);
    }
    throw new Error(`Timed out: ${expression}`);
  };
  const click = async (text) => assert(await evaluate(`(() => {
    const button = [...document.querySelectorAll("button")].find(item => item.textContent.trim() === ${JSON.stringify(text)});
    button?.click(); return Boolean(button);
  })()`), `Missing button: ${text}`);
  await cdp("Page.enable");
  await cdp("Runtime.enable");
  await cdp("Emulation.setDeviceMetricsOverride", {
    width: 1280, height: 900, deviceScaleFactor: 1, mobile: false,
  });
  await cdp("Page.addScriptToEvaluateOnNewDocument", { source: `
    window.__closingRequests = [];
    window.__retryRequests = 0;
    const ids = ["11111111-1111-4111-8111-111111111111", "22222222-2222-4222-8222-222222222222"];
    const members = ids.map((id, index) => ({
      user_id: id, display_name: index ? "Jordan" : "Morgan", role: "sales_rep", status: "active",
      assigned_leads: 4, qualified_leads: 3, closed_leads: 2, project_wins: 1, sale_wins: 1,
      untyped_wins: 0, lost_leads: 0, active_leads: 2, conversion_rate: 50,
      revenue: 1200, wins_without_value: 0, average_response_minutes: 1.5,
      summary: (index ? "Jordan" : "Morgan") + " has 4 leads, 2 wins, and 2 open leads."
    }));
    const originalFetch = window.fetch.bind(window);
    const json = (value) => new Response(JSON.stringify(value), {headers: {"Content-Type": "application/json"}});
    window.fetch = async (input, init) => {
      const path = new URL(typeof input === "string" ? input : input.url, location.origin).pathname;
      if (path === "/v1/analytics/reps") {
        const response = await originalFetch(input, init);
        const body = await response.json();
        return json({...body, members, unattributed_leads: 0});
      }
      if (path === "/v1/leads/closing-options") return json(members.map(member => ({...member, slack_linked: true})));
      if (path.endsWith("/progress") && init?.method === "POST") {
        const request = JSON.parse(init.body);
        window.__closingRequests.push(request);
        window.__closingLead = path.split("/")[3];
        window.__outcome = {
          pipeline_stage: "closed_won", conversion_type: request.conversion_type,
          credited_user_id: request.credited_user_id, credited_name: "Morgan",
          closed_revenue: request.closed_revenue, currency: "USD", closed_at: new Date().toISOString(),
          notifications: [
            {destination: "channel", status: "sent", error_message: null, attempts: 1},
            {destination: "direct_message", status: "not_configured", error_message: "Link the Slack member first.", attempts: 1}
          ]
        };
        return json({stage: request.stage, note: request.note, classification: "warm", score: 78, rationale: [], updated_at: new Date().toISOString(), outcome: window.__outcome});
      }
      if (path.endsWith("/congratulations/retry")) {
        window.__retryRequests++;
        window.__outcome.notifications.forEach(delivery => {delivery.status = "sent"; delivery.error_message = null;});
        return json(window.__outcome);
      }
      const response = await originalFetch(input, init);
      if (path === "/v1/leads/" + window.__closingLead) {
        return json({...await response.json(), outcome: window.__outcome});
      }
      return response;
    };
  ` });
  await cdp("Page.navigate", { url: origin });
  await wait(`document.body.innerText.includes("Continue to local workspace")`);
  await click("Continue to local workspace");
  await wait(`document.querySelector(".app-shell") !== null`);
  for (let index = 0; index < 8; index++) {
    await evaluate(`document.querySelectorAll('[role="dialog"] button').forEach(button => {
      if (["Not now", "Skip tour"].includes(button.textContent.trim())) button.click();
    })`);
    await pause(150);
  }
  await click("Analytics");
  await wait(`document.querySelector(".performance-comparison") !== null`);
  assert.equal(await evaluate(`document.querySelectorAll(".performance-comparison thead th").length`), 3);
  await evaluate(`document.querySelector(".performance-member-picker input").click()`);
  await wait(`document.querySelector(".performance-comparison") === null`);
  await evaluate(`document.querySelector(".performance-member-picker input").click()`);
  await wait(`document.querySelector(".performance-comparison") !== null`);
  await evaluate(`(() => { const select = document.querySelector(".performance-profile-heading select");
    select.value = select.options[1].value; select.dispatchEvent(new Event("change", {bubbles: true}));
    document.querySelector(".team-performance-panel").scrollIntoView(); })()`);
  await wait(`document.querySelector(".performance-profile h4").textContent === "Jordan"`);
  await evaluate(`document.querySelector(".team-performance-panel").scrollIntoView({behavior: "instant", block: "start"})`);
  let shot = await cdp("Page.captureScreenshot", { format: "png" });
  await writeFile("/tmp/corral-member-comparison-desktop.png", Buffer.from(shot.data, "base64"));
  await cdp("Emulation.setDeviceMetricsOverride", { width: 390, height: 844, deviceScaleFactor: 1, mobile: true });
  await evaluate(`document.querySelector(".team-performance-panel").scrollIntoView({behavior: "instant", block: "start"})`);
  assert(await evaluate(`document.documentElement.scrollWidth <= innerWidth + 1`), "Comparison overflowed the phone page.");
  shot = await cdp("Page.captureScreenshot", { format: "png" });
  await writeFile("/tmp/corral-member-comparison-mobile.png", Buffer.from(shot.data, "base64"));
  await cdp("Emulation.setDeviceMetricsOverride", { width: 1280, height: 900, deviceScaleFactor: 1, mobile: false });
  await click("Leads");
  await wait(`document.querySelector(".lead-table tbody tr") !== null`);
  await evaluate(`(() => { const row = document.querySelector(".lead-table tbody tr"); (row.querySelector("button") ?? row).click(); })()`);
  await wait(`document.querySelector(".lead-progress-form") !== null`);
  await evaluate(`(() => {
    const stage = document.querySelector(".lead-progress-form select");
    stage.value = "converted"; stage.dispatchEvent(new Event("change", {bubbles: true}));
  })()`);
  await wait(`document.querySelector(".lead-closing-fields") !== null`);
  const fillClosing = async (kind) => evaluate(`(() => {
    const form = document.querySelector(".lead-progress-form");
    const selects = form.querySelectorAll("select");
    selects[1].value = ${JSON.stringify(kind)}; selects[1].dispatchEvent(new Event("change", {bubbles: true}));
    selects[2].value = selects[2].options[1].value; selects[2].dispatchEvent(new Event("change", {bubbles: true}));
    const amount = form.querySelector('input[type="number"]');
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set.call(amount, "1234.50");
    amount.dispatchEvent(new Event("input", {bubbles: true}));
    const note = form.querySelector("textarea");
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value").set.call(note, "Confirmed successful conversion.");
    note.dispatchEvent(new Event("input", {bubbles: true}));
  })()`);
  await fillClosing("project");
  await pause(150);
  await evaluate(`document.querySelector(".lead-progress-form").requestSubmit()`);
  await wait(`window.__closingRequests.length === 1 && document.querySelector(".lead-outcome-receipt") !== null`);
  assert.equal(await evaluate(`window.__closingRequests[0].conversion_type`), "project");
  assert.equal(await evaluate(`window.__closingRequests[0].closed_revenue`), 1234.5);
  await click("Retry unsent congratulations");
  await wait(`window.__retryRequests === 1 && !document.body.innerText.includes("Retry unsent congratulations")`);
  await fillClosing("sale");
  await pause(150);
  await evaluate(`document.querySelector(".lead-progress-form").requestSubmit()`);
  await wait(`window.__closingRequests.length === 2`);
  assert.equal(await evaluate(`window.__closingRequests[1].conversion_type`), "sale");
  await evaluate(`document.querySelector(".lead-outcome-receipt").scrollIntoView({behavior: "instant", block: "start"})`);
  await cdp("Emulation.setDeviceMetricsOverride", { width: 390, height: 844, deviceScaleFactor: 1, mobile: true });
  assert(await evaluate(`document.documentElement.scrollWidth <= innerWidth + 1`), "Closing form overflowed the phone page.");
  shot = await cdp("Page.captureScreenshot", { format: "png" });
  await writeFile("/tmp/corral-member-win-mobile.png", Buffer.from(shot.data, "base64"));
  assert.deepEqual(errors, [], "The signed-in UI produced a runtime exception.");
  console.log("Signed-in desktop/phone checks passed: member comparison, individual summaries, Project/Sale closing, and retry UI. No live Slack messages sent.");
} finally {
  socket?.close();
  browser.kill("SIGTERM");
  await pause(150);
  await rm(profile, { recursive: true, force: true });
}
