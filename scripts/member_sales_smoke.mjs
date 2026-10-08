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
const browser = spawn(process.env.CORRAL_BROWSER_PATH || "chromium", [
  "--headless", "--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage",
  `--remote-debugging-port=${port}`, `--user-data-dir=${profile}`, "about:blank",
], { stdio: ["ignore", "ignore", "pipe"] });
let browserDiagnostic = "";
browser.stderr.on("data", (data) => { browserDiagnostic = (browserDiagnostic + String(data)).slice(-2000); });
browser.on("error", (error) => { browserDiagnostic = error.message; });
const pause = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
let socket;
try {
  let target;
  for (let index = 0; index < 300; index++) {
    try {
      target = await (await fetch(`http://127.0.0.1:${port}/json/new?about:blank`, { method: "PUT" })).json();
      break;
    } catch { await pause(100); }
  }
  assert(target?.webSocketDebuggerUrl, `Chromium did not start (exit ${browser.exitCode}). ${browserDiagnostic}`);
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
  const reloadPage = async () => {
    const loaded = new Promise((resolve) => {
      const listener = ({ data }) => {
        if (JSON.parse(String(data)).method !== "Page.loadEventFired") return;
        socket.removeEventListener("message", listener);
        resolve();
      };
      socket.addEventListener("message", listener);
    });
    await cdp("Page.reload");
    await loaded;
  };
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
  assert(await evaluate(`["Revenue trend", "Qualification rate by source", "Lead quality by source"].every(
    title => [...document.querySelectorAll("h2")].some(heading => heading.textContent === title)
  )`), "New dashboard insights are missing.");
  assert(await evaluate(`[...document.querySelectorAll(".dashboard-insights .bar-row")].every(row => {
    const value = parseFloat(row.querySelector("b").textContent);
    return Math.abs(Number(row.querySelector("i span").style.getPropertyValue("--bar-scale")) - value / 100) < .00001;
  })`), "Score/percentage bars are not using an absolute 0–100 scale.");
  await evaluate(`window.scrollTo({top: document.querySelector(".dashboard-insights").getBoundingClientRect().top + scrollY - 100, behavior:"instant"})`);
  let insightsShot = await cdp("Page.captureScreenshot", {format: "png"});
  await writeFile("/tmp/corral-dashboard-insights-desktop.png", Buffer.from(insightsShot.data, "base64"));
  await cdp("Emulation.setDeviceMetricsOverride", {width: 390, height: 844, deviceScaleFactor: 1, mobile: true});
  await wait(`document.querySelector(".app-shell")?.classList.contains("nav-collapsed")`);
  await pause(300);
  await evaluate(`window.scrollTo({top: document.querySelector(".dashboard-insights").getBoundingClientRect().top + scrollY - 100, behavior:"instant"})`);
  assert(await evaluate(`document.documentElement.scrollWidth <= innerWidth + 1`), "Dashboard charts overflowed the phone.");
  insightsShot = await cdp("Page.captureScreenshot", {format: "png"});
  await writeFile("/tmp/corral-dashboard-insights-mobile.png", Buffer.from(insightsShot.data, "base64"));
  await cdp("Emulation.setDeviceMetricsOverride", {width: 1280, height: 900, deviceScaleFactor: 1, mobile: false});
  if (process.argv.includes("--insights-only")) {
    assert.deepEqual(errors, []);
    console.log("Dashboard insight snapshots and phone layout checks passed.");
  } else {
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
  if (!process.argv.includes("--skip-refresh")) for (const [label, page] of [["Analytics", "analytics"], ["Integrations", "integrations"], ["Settings", "settings"], ["Users", "team"], ["Contacts", "contacts"], ["Pipeline", "pipeline"], ["Agents / AI Activity", "agents"], ["Manual Review", "review"]]) {
    await click(label);
    await wait(`new URLSearchParams(location.search).get("view") === ${JSON.stringify(page)}`);
    await reloadPage();
    await wait(`Boolean(document.querySelector(".app-shell") && document.querySelector('[data-navigation-id="${page}"].is-active'))`);
    assert.equal(await evaluate(`new URLSearchParams(location.search).get("view")`), page, `${label} returned to Dashboard after refresh.`);
  }
  await evaluate(`document.querySelector(".mini-rail-expand")?.click()`);
  await wait(`Boolean(document.querySelector(".app-shell.nav-expanded"))`);
  await pause(350);
  const menuPoint = await evaluate(`(() => { const r=document.querySelector('.side-rail [data-navigation-id="leads"]').getBoundingClientRect(); return {x:r.left+20,y:r.top+r.height/2}; })()`);
  await cdp("Input.dispatchMouseEvent", {type:"mouseMoved", ...menuPoint});
  await wait(`!document.querySelector('[data-navigation-owner="leads"][data-navigation-mode="expanded"]').hidden`);
  assert(await evaluate(`(() => {
    const popup = document.querySelector('[data-navigation-owner="leads"][data-navigation-mode="expanded"]');
    const box = popup.getBoundingClientRect();
    return box.left >= document.querySelector(".side-rail").getBoundingClientRect().right
      && box.top >= 0 && box.bottom <= innerHeight
      && [...popup.querySelectorAll("button")].every(button => {
        const r=button.getBoundingClientRect();
        return button.contains(document.elementFromPoint(r.left+r.width/2,r.top+r.height/2));
      });
  })()`), "Navigation flyout is clipped, offscreen or covered.");
  await writeFile("/tmp/corral-navigation-desktop.png", Buffer.from((await cdp("Page.captureScreenshot", {format:"png"})).data, "base64"));
  assert.equal(await evaluate(`document.querySelectorAll('[data-navigation-owner="leads"][data-navigation-mode="expanded"] .navigation-menu-child').length`), 5);
  await evaluate(`[...document.querySelectorAll('[data-navigation-owner="leads"][data-navigation-mode="expanded"] .navigation-menu-child')].find(button => button.textContent.trim()==="AI & qualification").click()`);
  await wait(`document.querySelector(".lead-table") && new URLSearchParams(location.search).get("lead_tab")==="ai"`);
  await wait(`document.querySelector('[data-lead-subpage="ai"]') !== null`);
  await wait(`Boolean(document.querySelector(".lead-table tbody tr"))`);
  await evaluate(`(() => {const row=document.querySelector(".lead-table tbody tr"); (row.querySelector("button") ?? row).click();})()`);
  await wait(`document.querySelector(".lead-story")?.dataset.activeSection==="ai"`);
  assert(await evaluate(`[...document.querySelectorAll(".lead-drawer .primary-button")].every(button => getComputedStyle(button).backgroundImage==="none")`), "Lead action buttons still use gradients.");
  await writeFile("/tmp/corral-lead-ai-desktop.png", Buffer.from((await cdp("Page.captureScreenshot", {format:"png"})).data, "base64"));
  await evaluate(`document.querySelector(".lead-drawer-close").click()`);
  await wait(`!document.querySelector(".lead-drawer")`);
  await evaluate(`(() => { const button=document.querySelector('.side-rail [data-navigation-id="leads"] .navigation-menu-trigger'); button.focus(); button.dispatchEvent(new KeyboardEvent("keydown",{key:"ArrowDown",bubbles:true})); })()`);
  await wait(`document.activeElement?.classList.contains("navigation-menu-child")`);
  await evaluate(`document.activeElement.dispatchEvent(new KeyboardEvent("keydown",{key:"Escape",bubbles:true}))`);
  await wait(`document.querySelector('[data-navigation-owner="leads"][data-navigation-mode="expanded"]').hidden`);
  await cdp("Emulation.setDeviceMetricsOverride", {width:1000,height:620,deviceScaleFactor:1,mobile:false});
  await pause(450);
  assert(await evaluate(`(() => {
    const nav=document.querySelector(".side-rail > nav").getBoundingClientRect();
    const foot=document.querySelector(".rail-foot").getBoundingClientRect();
    return nav.bottom <= foot.top && foot.bottom <= innerHeight && nav.height > 100;
  })()`), "Sidebar footer overlaps navigation at a short desktop height.");
  await evaluate(`document.querySelector(".side-rail > nav").scrollTop=1000`);
  await writeFile("/tmp/corral-sidebar-short-desktop.png", Buffer.from((await cdp("Page.captureScreenshot", {format:"png"})).data, "base64"));
  await cdp("Emulation.setDeviceMetricsOverride", {width:390,height:844,deviceScaleFactor:1,mobile:true});
  await pause(350);
  await evaluate(`document.querySelector(".bar-leading button").click()`);
  await wait(`Boolean(document.querySelector(".app-shell.nav-expanded"))`);
  await evaluate(`(() => {const button=document.querySelector('.side-rail [data-navigation-id="leads"] .navigation-menu-disclosure'); button.focus(); button.click();})()`);
  await wait(`!document.querySelector('[data-navigation-owner="leads"][data-navigation-mode="expanded"]').hidden`);
  assert(await evaluate(`(() => {
    const popup=document.querySelector('[data-navigation-owner="leads"][data-navigation-mode="expanded"]');
    return getComputedStyle(popup).position==="static" && popup.getBoundingClientRect().width>100;
  })()`), "Phone submenu is not readable inline.");
  await pause(350);
  await writeFile("/tmp/corral-navigation-mobile.png", Buffer.from((await cdp("Page.captureScreenshot", {format:"png"})).data, "base64"));
  await evaluate(`document.querySelector('.side-rail [data-navigation-id="leads"] .navigation-menu-disclosure').click()`);
  await wait(`document.querySelector('[data-navigation-owner="leads"][data-navigation-mode="expanded"]').hidden`);
  await cdp("Emulation.setDeviceMetricsOverride", {width:1280,height:900,deviceScaleFactor:1,mobile:false});
  for (const [view, section, selector] of [
    ["analytics","lead-trajectory",".analytics-grid > *"],
    ["settings","email-templates",'.settings-email-templates'],
    ["integrations","gmail",'.integration-card'],
    ["leads","ai",'[data-lead-subpage="ai"]'],
    ["leads","closing",'[data-lead-subpage="closing"]'],
    ["leads","follow-ups",'[data-lead-subpage="follow-ups"]'],
    ["leads","history",'[data-lead-subpage="history"]'],
  ]) {
    const query = view==="leads" ? "lead_tab" : "section";
    await cdp("Page.navigate",{url: `${origin}/?view=${view}&${query}=${section}`});
    await wait(`document.querySelector(${JSON.stringify(selector)}) !== null`);
    if(view==="leads") {
      await wait(`document.querySelector(".lead-subpage-table tbody tr") !== null && document.querySelector("[data-lead-subpage]").getAttribute("aria-busy")==="false"`);
      assert.equal(await evaluate(`getComputedStyle(document.querySelector(".lead-subpage-table tbody td")).padding`),"14px 10px","Subpage row spacing differs from the main Leads table.");
      assert.equal(await evaluate(`getComputedStyle(document.querySelector(".lead-row-identity b")).fontSize`),"14px","Subpage name typography differs from the main Leads table.");
      assert(await evaluate(`[...document.querySelectorAll(".lead-subpage-table td:last-child button")].every(button => {
        const rect=button.getBoundingClientRect(), panel=document.querySelector(".lead-browser").getBoundingClientRect();
        return rect.left>=panel.left && rect.right<=panel.right && button.scrollWidth<=button.clientWidth+1;
      })`),"Subpage action button is clipped.");
      await writeFile(`/tmp/corral-subpage-${section}-desktop.png`,Buffer.from((await cdp("Page.captureScreenshot",{format:"png"})).data,"base64"));
    }
    if(view==="analytics" || view==="integrations") assert.equal(await evaluate(`document.querySelectorAll(${JSON.stringify(selector)}).length`),1,"Subtab did not render a dedicated page.");
    if(view==="settings") assert.equal(await evaluate(`[...document.querySelectorAll(".settings-grid > *")].filter(node=>getComputedStyle(node).display!=="none").length`),1,"Settings subtab contains unrelated panels.");
    await reloadPage();
    await wait(`document.querySelector(${JSON.stringify(selector)}) !== null`);
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
  const chooseLeadTab = async (section) => {
    await evaluate(`document.getElementById("lead-tab-" + ${JSON.stringify(section)}).click()`);
    await wait(`document.querySelector(".lead-story")?.dataset.activeSection === ${JSON.stringify(section)}`);
  };
  assert.equal(await evaluate(`document.querySelectorAll(".lead-detail-tabs [role='tab']").length`), 5);
  for (const section of ["ai", "follow-ups", "closing", "history", "overview"]) {
    await chooseLeadTab(section);
    assert.equal(await evaluate(`document.querySelectorAll(".lead-story [role='tabpanel']:not([hidden])").length`), 1, "More than one lead section is visible.");
    assert.equal(await evaluate(`new URLSearchParams(location.search).get("lead_tab")`), section);
  }
  await chooseLeadTab("ai");
  await evaluate(`document.getElementById("lead-tab-ai").focus(); document.getElementById("lead-tab-ai").dispatchEvent(new KeyboardEvent("keydown", {key:"ArrowRight", bubbles:true}))`);
  await wait(`document.querySelector(".lead-story")?.dataset.activeSection === "follow-ups"`);
  await chooseLeadTab("closing");
  await evaluate(`document.querySelector(".lead-section-pin").click()`);
  await wait(`document.querySelector(".lead-section-pin")?.getAttribute("aria-pressed") === "true"`);
  await evaluate(`document.querySelector(".lead-drawer-close").click()`);
  await wait(`document.querySelector(".lead-drawer") === null`);
  await evaluate(`document.querySelector(".pinned-lead-row a").click()`);
  await wait(`document.querySelector(".lead-story")?.dataset.activeSection === "closing"`);
  const reloaded = new Promise((resolve) => {
    const listener = ({ data }) => {
      if (JSON.parse(String(data)).method !== "Page.loadEventFired") return;
      socket.removeEventListener("message", listener);
      resolve();
    };
    socket.addEventListener("message", listener);
  });
  await cdp("Page.reload");
  await reloaded;
  await wait(`document.querySelector(".lead-story")?.dataset.activeSection === "closing"`);
  assert.equal(await evaluate(`document.querySelector(".lead-section-pin").getAttribute("aria-pressed")`), "true", "Pin did not survive a refresh/deep link.");
  await chooseLeadTab("history");
  await evaluate(`history.back()`);
  await wait(`document.querySelector(".lead-story")?.dataset.activeSection === "closing"`);
  await evaluate(`history.forward()`);
  await wait(`document.querySelector(".lead-story")?.dataset.activeSection === "history"`);
  await chooseLeadTab("closing");
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
  await chooseLeadTab("ai");
  await chooseLeadTab("closing");
  assert.equal(await evaluate(`document.querySelector(".lead-progress-form textarea").value`), "Confirmed successful conversion.", "Switching sections discarded an unsaved comment.");
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
  await evaluate(`document.querySelector(".lead-section-pin").click()`);
  await wait(`document.querySelector(".lead-section-pin")?.getAttribute("aria-pressed") === "false"`);
  const auditRoutes = ["dashboard", "leads", "contacts", "pipeline", "agents", "analytics", "integrations", "team", "settings", "review"];
  for (const [width, height, mobile] of [[1280,900,false],[390,844,true]]) {
    await cdp("Emulation.setDeviceMetricsOverride",{width,height,deviceScaleFactor:1,mobile});
    for (const view of auditRoutes) {
      await cdp("Page.navigate",{url:`${origin}/?view=${view}`});
      await wait(`Boolean(document.querySelector('[data-navigation-id="${view}"].is-active')) && Boolean(document.querySelector(".view-transition h1"))`);
      await wait(`Boolean(document.querySelector(".product-footer")) && !document.querySelector(".product-footer").textContent.includes("Generated —") && !document.querySelector(".view-transition [aria-busy='true']")`);
      await pause(650);
      if(view==="leads") await wait(`document.querySelector(".lead-table tbody tr") !== null`);
      if(view==="leads") assert.equal(await evaluate(`getComputedStyle(document.querySelector(".lead-row-identity")).display`),"grid","Name and company must remain on separate lines.");
      assert(await evaluate(`document.documentElement.scrollWidth <= innerWidth + 2`), `${view} overflows the ${mobile?"phone":"desktop"} viewport.`);
      await writeFile(`/tmp/corral-audit-${view}-${mobile?"mobile":"desktop"}.png`,Buffer.from((await cdp("Page.captureScreenshot",{format:"png"})).data,"base64"));
    }
  }
  for (const section of ["ai","follow-ups","closing","history"]) {
    await cdp("Page.navigate",{url:`${origin}/?view=leads&lead_tab=${section}`});
    await wait(`document.querySelector('[data-lead-subpage="${section}"][aria-busy="false"] .lead-subpage-table tbody tr') !== null`);
    assert(await evaluate(`[...document.querySelectorAll(".lead-subpage-table td:last-child button")].every(button => {
      const rect=button.getBoundingClientRect(), panel=document.querySelector(".lead-browser").getBoundingClientRect();
      return rect.left>=panel.left && rect.right<=panel.right && button.scrollWidth<=button.clientWidth+1;
    })`),`${section} has a clipped phone action button.`);
    await writeFile(`/tmp/corral-subpage-${section}-mobile.png`,Buffer.from((await cdp("Page.captureScreenshot",{format:"png"})).data,"base64"));
  }
  assert.deepEqual(errors, [], "The signed-in UI produced a runtime exception.");
  console.log("Signed-in desktop/phone checks passed: five lead tabs, keyboard/direct-link/back/forward navigation, draft preservation, pin/reopen/refresh/unpin, dashboard insights without mobile overflow, full-page workspace and lead details, Slack rules, member comparison, Project/Sale outcomes, and retry UI. No live Slack messages sent; rule writes and closing requests were intercepted in this test browser.");
  }
} finally {
  socket?.close();
  browser.kill("SIGTERM");
  await pause(150);
  await rm(profile, { recursive: true, force: true });
}
