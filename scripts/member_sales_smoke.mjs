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
    const diagnostic = await evaluate(`JSON.stringify({
      slackConfigured: window.__slackConfigured,
      routing: [...document.querySelectorAll(".slack-routing-guide")].map(item => item.innerText),
      page: document.body?.innerText.slice(-2500)
    })`);
    throw new Error(`Timed out: ${expression}\n${diagnostic}`);
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
    window.__routing = {default_connection_id: null, rules: []};
    window.__slackConfigured = false;
    window.__routingSaves = [];
    // Exercise embedded browsers without secure-context randomUUID support.
    Object.defineProperty(crypto, "randomUUID", {value: undefined, configurable: true});
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
      if (path === "/v1/integrations/slack/notification-config") {
        if (init?.method === "PUT") {
          const request = JSON.parse(init.body);
          window.__routingSaves.push(request);
          window.__routing = {...request, workspace_id: "00000000-0000-0000-0000-000000000006", updated_at: new Date().toISOString()};
        }
        return json(window.__routing);
      }
      if (path === "/v1/integrations") {
        const response = await originalFetch(input, init);
        const cards = await response.json();
        return json(cards.map(card => {
          if (card.provider !== "slack") return card;
          const connection = window.__slackConfigured ? {
            id: "33333333-3333-4333-8333-333333333333",
            provider_type: "slack", account_name: "UI test Slack", auth_type: "oauth2",
            status: "verified", is_active: true, scopes: [], credential_state: "configured",
            metadata: {slack_team_id: "TTEST", slack_channel_id: "CTEST", slack_channel_name: "team"},
          } : null;
          return {...card, connection, connections: connection ? [connection] : []};
        }));
      }
      if (path.startsWith("/v1/auth/")) {
        const response = await originalFetch(input, init);
        try {
          const body = await response.clone().json();
          if (Array.isArray(body.workspaces)) {
            window.__session = body;
            if (!window.__legacyDashboardPreferenceSeeded) {
              const userId = body.user?.id || "local";
              localStorage.setItem("corral.dashboard.widgets.v1:" + userId, '["lead-intelligence-network"]');
              window.__legacyDashboardPreferenceSeeded = true;
            }
          }
        } catch {}
        return response;
      }
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
  await wait(`document.body?.innerText.includes("Continue to local workspace")`);
  await click("Continue to local workspace");
  await wait(`document.querySelector(".app-shell") !== null`);
  for (let index = 0; index < 8; index++) {
    await evaluate(`document.querySelectorAll('[role="dialog"] button').forEach(button => {
      if (["Not now", "Skip tour"].includes(button.textContent.trim())) button.click();
    })`);
    await pause(150);
  }
  await wait(`document.querySelector(".dashboard-view") !== null`);
  assert(await evaluate(`!document.querySelector(".intelligence-network")
    && document.querySelector(".data-panel h2") !== null`), "The obsolete topology card remains or the real dashboard panels are missing.");
  assert(await evaluate(`(() => {
    const preference = JSON.parse(localStorage.getItem("corral.dashboard.widgets.v1:" + (window.__session.user?.id || "local")) || "[]");
    return !preference.includes("lead-intelligence-network")
      && preference.includes("pipeline-pulse")
      && preference.includes("attention-queue");
  })()`), "A previously saved topology-only dashboard was not migrated to useful panels.");
  const dashboardShot = await cdp("Page.captureScreenshot", {format: "png"});
  await writeFile("/tmp/corral-dashboard-clean.png", Buffer.from(dashboardShot.data, "base64"));
  await evaluate(`document.querySelector(".workspace-trigger").click()`);
  await wait(`document.querySelector(".workspace-chooser[open]") !== null`);
  assert(await evaluate(`(() => {
    const dialog = document.querySelector(".workspace-chooser").getBoundingClientRect();
    return dialog.width >= innerWidth - 1 && dialog.height >= innerHeight - 1
      && window.__session.workspaces.every(workspace => [...document.querySelectorAll(".workspace-option strong")].some(item => item.textContent === workspace.name));
  })()`), "Workspace names were missing or the chooser was not full page.");
  let workspaceShot = await cdp("Page.captureScreenshot", {format: "png"});
  await writeFile("/tmp/corral-workspaces-desktop.png", Buffer.from(workspaceShot.data, "base64"));
  await cdp("Emulation.setDeviceMetricsOverride", {width: 390, height: 844, deviceScaleFactor: 1, mobile: true});
  assert(await evaluate(`document.documentElement.scrollWidth <= innerWidth + 1`), "Workspace chooser overflowed the phone.");
  workspaceShot = await cdp("Page.captureScreenshot", {format: "png"});
  await writeFile("/tmp/corral-workspaces-mobile.png", Buffer.from(workspaceShot.data, "base64"));
  await cdp("Emulation.setDeviceMetricsOverride", {width: 1280, height: 900, deviceScaleFactor: 1, mobile: false});
  await click("Back to Corral");
  // Test an actual switch using this development-only browser session, then
  // return to its original workspace. No production session is touched.
  const originalWorkspace = await evaluate(`window.__session.current_workspace_id`);
  const otherWorkspace = await evaluate(`window.__session.workspaces.find(item => item.id !== window.__session.current_workspace_id)?.id`);
  if (otherWorkspace) {
    await evaluate(`document.querySelector(".workspace-trigger").click()`);
    await wait(`document.querySelector(".workspace-chooser[open]") !== null`);
    await evaluate(`document.querySelectorAll(".workspace-option")[window.__session.workspaces.findIndex(item => item.id === ${JSON.stringify(otherWorkspace)})].click()`);
    await wait(`window.__session.current_workspace_id === ${JSON.stringify(otherWorkspace)} && !document.querySelector(".workspace-chooser[open]")`);
    await evaluate(`document.querySelector(".workspace-trigger").click()`);
    await wait(`document.querySelector(".workspace-chooser[open]") !== null`);
    await evaluate(`document.querySelectorAll(".workspace-option")[window.__session.workspaces.findIndex(item => item.id === ${JSON.stringify(originalWorkspace)})].click()`);
    await wait(`window.__session.current_workspace_id === ${JSON.stringify(originalWorkspace)} && !document.querySelector(".workspace-chooser[open]")`);
  }
  await click("Integrations");
  await wait(`document.querySelector('[aria-label="Expand Slack details"]') !== null`);
  await evaluate(`document.querySelector('[aria-label="Expand Slack details"]').click()`);
  await wait(`document.querySelector(".slack-rules-toolbar") !== null`);
  await click("+ Add rule");
  await wait(`document.querySelector('.slack-routing-guide [role="alert"]')?.textContent.includes("Connect and verify Slack")`);
  assert.equal(await evaluate(`document.querySelectorAll(".slack-routing-rule").length`), 0);
  await evaluate(`window.__slackConfigured = true`);
  await click("Analytics");
  await wait(`document.querySelector(".team-performance-panel") !== null`);
  await click("Integrations");
  await wait(`document.querySelector('[aria-label="Expand Slack details"]') !== null`);
  await evaluate(`document.querySelector('[aria-label="Expand Slack details"]').click()`);
  await wait(`document.querySelector(".slack-routing-health")?.textContent.includes("1 verified")`);
  await click("+ Add rule");
  await wait(`document.querySelectorAll(".slack-routing-rule").length === 1`);
  assert(await evaluate(`document.querySelector('.slack-routing-rule input') === document.activeElement`), "The new Slack rule was not brought into focus.");
  await click("Save Slack routing");
  await wait(`window.__routingSaves.length === 1`);
  await wait(`document.querySelector('.notice[role="status"]')?.textContent.includes("Slack notification routing saved")`);
  assert.match(await evaluate(`window.__routingSaves[0].rules[0].id`), /^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/);
  await click("Analytics");
  await wait(`document.querySelector(".team-performance-panel") !== null`);
  await click("Integrations");
  await wait(`document.querySelector('[aria-label="Expand Slack details"]') !== null`);
  await evaluate(`document.querySelector('[aria-label="Expand Slack details"]').click()`);
  await wait(`document.querySelectorAll(".slack-routing-rule").length === 1`);
  await evaluate(`document.querySelector(".slack-routing-rule").scrollIntoView({behavior: "instant", block: "start"})`);
  const routingShot = await cdp("Page.captureScreenshot", {format: "png"});
  await writeFile("/tmp/corral-slack-rules.png", Buffer.from(routingShot.data, "base64"));
  await evaluate(`document.querySelector(".slack-routing-rule button").click()`);
  await click("Save Slack routing");
  await wait(`window.__routingSaves.length === 2 && window.__routingSaves[1].rules.length === 0`);
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
  await wait(`document.querySelector(".ai-stage-assessment") !== null`);
  const assessedStage = await evaluate(`document.querySelector(".ai-stage-value").textContent`);
  assert(await evaluate(`document.querySelector(".ai-stage-assessment").textContent.includes("AI-assessed buying stage")`));
  if (await evaluate(`Boolean(document.querySelector(".ai-stage-reasoning"))`)) {
    await evaluate(`document.querySelector(".ai-stage-reasoning summary").click()`);
    assert(await evaluate(`document.querySelector(".ai-stage-reasoning").open`), "The AI reasoning did not open.");
    await evaluate(`document.querySelector(".ai-stage-reasoning summary").click()`);
  }
  assert(await evaluate(`(() => {
    const drawer = document.querySelector(".lead-drawer").getBoundingClientRect();
    const editor = document.querySelector(".lead-progress-form").getBoundingClientRect();
    return drawer.width >= innerWidth - 1 && drawer.height >= innerHeight - 1 && editor.top < innerHeight;
  })()`), "The lead detail was not full page or closing/comments were still buried.");
  let leadShot = await cdp("Page.captureScreenshot", {format: "png"});
  await writeFile("/tmp/corral-lead-closing-desktop.png", Buffer.from(leadShot.data, "base64"));
  await evaluate(`(() => {
    const stage = document.querySelector(".lead-progress-form select");
    stage.value = "converted"; stage.dispatchEvent(new Event("change", {bubbles: true}));
  })()`);
  await wait(`document.querySelector(".lead-closing-fields") !== null`);
  assert.equal(await evaluate(`document.querySelector(".ai-stage-value").textContent`), assessedStage, "Choosing a manual closing stage changed the AI assessment.");
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
  await cdp("Emulation.setPageScaleFactor", {pageScaleFactor: 1});
  await pause(200);
  const phoneViewport = await evaluate(`({
    innerWidth, innerHeight, scale: visualViewport.scale,
    drawerHeight: document.querySelector(".lead-drawer").getBoundingClientRect().height,
    backdropHeight: document.querySelector(".lead-drawer-backdrop").getBoundingClientRect().height
  })`);
  assert.equal(phoneViewport.innerWidth, 390);
  assert.equal(phoneViewport.innerHeight, 844);
  assert.equal(phoneViewport.scale, 1);
  assert.equal(phoneViewport.drawerHeight, 844);
  assert.equal(phoneViewport.backdropHeight, 844);
  assert(await evaluate(`document.documentElement.scrollWidth <= innerWidth + 1`), "Closing form overflowed the phone page.");
  shot = await cdp("Page.captureScreenshot", { format: "png" });
  await writeFile("/tmp/corral-member-win-mobile.png", Buffer.from(shot.data, "base64"));
  await evaluate(`document.querySelector(".ai-stage-assessment").scrollIntoView({behavior: "instant", block: "start"})`);
  shot = await cdp("Page.captureScreenshot", {format: "png"});
  await writeFile("/tmp/corral-ai-stage-mobile.png", Buffer.from(shot.data, "base64"));
  assert.deepEqual(errors, [], "The signed-in UI produced a runtime exception.");
  console.log("Signed-in desktop/phone checks passed: full-page workspace names, full-page lead details, visible comments/closing, embedded-browser Slack rule add/save/reload/remove, member comparison, Project/Sale outcomes, and retry UI. No live Slack messages sent; rule writes and closing requests were intercepted in this test browser.");
} finally {
  socket?.close();
  browser.kill("SIGTERM");
  await pause(150);
  await rm(profile, { recursive: true, force: true });
}
