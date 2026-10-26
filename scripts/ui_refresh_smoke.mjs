// Development-only, non-sending browser check. Uses the existing local sign-in.
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

const domain = process.env.REPLIT_DEV_DOMAIN;
assert(domain, "The development preview must be running.");
const origin = process.env.CORRAL_PREVIEW_ORIGIN || `https://${domain}:5000`;
const profile = await mkdtemp(join(tmpdir(), "corral-refresh-"));
const port = 9248;
const browser = spawn(process.env.CORRAL_BROWSER_PATH || "chromium", [
  "--headless", "--no-sandbox", "--disable-dev-shm-usage",
  `--remote-debugging-port=${port}`, `--user-data-dir=${profile}`, "about:blank",
], { stdio: "ignore" });
const pause = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
let socket;
try {
  let pages;
  for (let i = 0; i < 80; i++) {
    try { pages = await (await fetch(`http://127.0.0.1:${port}/json`)).json(); } catch {}
    if (pages?.length) break;
    await pause(100);
  }
  assert(pages?.length, "Browser debugger did not start.");
  socket = new WebSocket(pages[0].webSocketDebuggerUrl);
  await new Promise((resolve) => socket.addEventListener("open", resolve, { once: true }));
  let seq = 0;
  const pending = new Map();
  const errors = [];
  socket.addEventListener("message", ({ data }) => {
    const result = JSON.parse(String(data));
    if (result.method === "Runtime.exceptionThrown") errors.push(result.params.exceptionDetails.text);
    const job = pending.get(result.id);
    if (!job) return;
    pending.delete(result.id);
    result.error ? job.reject(new Error(result.error.message)) : job.resolve(result.result);
  });
  const cdp = (method, params = {}) => new Promise((resolve, reject) => {
    const id = ++seq;
    pending.set(id, { resolve, reject });
    socket.send(JSON.stringify({ id, method, params }));
  });
  const evaluate = async (expression) => {
    const result = await cdp("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true });
    assert(!result.exceptionDetails, JSON.stringify(result.exceptionDetails));
    return result.result.value;
  };
  const wait = async (expression) => {
    for (let i = 0; i < 150; i++) {
      if (await evaluate(expression)) return;
      await pause(100);
    }
    const diagnostic = await evaluate(`JSON.stringify({ title: document.title, text: document.body?.innerText.slice(0, 450) })`);
    throw new Error(`UI did not become ready: ${expression}\n${diagnostic}`);
  };
  const click = async (text) => assert(await evaluate(`(() => {
    const button = [...document.querySelectorAll("button, a")].find(el =>
      (el.textContent.trim() === ${JSON.stringify(text)} || el.getAttribute("aria-label") === ${JSON.stringify(text)})
      && el.getClientRects().length && !el.disabled);
    button?.click(); return Boolean(button);
  })()`), `Missing enabled button: ${text}`);
  const capture = async (name) => {
    const image = await cdp("Page.captureScreenshot", { format: "png" });
    await writeFile(join(tmpdir(), `corral-refresh-${name}.png`), Buffer.from(image.data, "base64"));
  };
  await cdp("Page.enable");
  await cdp("Runtime.enable");
  await cdp("Emulation.setDeviceMetricsOverride", { width: 1440, height: 1000, deviceScaleFactor: 1, mobile: false });
  await cdp("Page.navigate", { url: `${origin}/` });
  await wait(`Boolean([...document.querySelectorAll("button")].find(el => el.textContent.trim() === "Continue to local workspace"))`);
  await click("Continue to local workspace");
  await wait(`Boolean(document.querySelector('.v-sidebar'))`);
  for (let i = 0; i < 3; i++) {
    await evaluate(`(() => {
      const close = [...document.querySelectorAll("button")].find(el => /^(skip|skip tour|not now|maybe later)$/i.test(el.textContent.trim()) && el.getClientRects().length);
      close?.click();
    })()`);
    await pause(150);
  }
  await capture("start");
  await evaluate(`document.querySelector("[data-ask-corral]")?.click()`);
  await wait(`Boolean(document.querySelector(".corral-agent-window.is-open"))`);
  await wait(`Boolean(document.querySelector(".corral-workspace-chat"))`);
  const initialScope = await evaluate(`JSON.stringify([...document.querySelectorAll(".v-agent-scope button")].map(button => [button.textContent.trim(), button.getAttribute("aria-pressed")]))`);
  assert.deepEqual(JSON.parse(initialScope), [["Lead", "false"], ["Workspace", "true"]]);
  await capture("agent-workspace");

  await evaluate(`document.querySelector('.v-agent-scope button[aria-pressed="false"]')?.click()`);
  await wait(`Boolean(document.querySelector(".corral-agent-lead-option"))`);
  await evaluate(`document.querySelector(".corral-agent-lead-option")?.click()`);
  await wait(`Boolean(document.querySelector(".corral-agent-window .lead-agent"))`);
  const leadScope = await evaluate(`JSON.stringify([...document.querySelectorAll(".v-agent-scope button")].map(button => [button.textContent.trim(), button.getAttribute("aria-pressed")]))`);
  assert.deepEqual(JSON.parse(leadScope), [["Lead", "true"], ["Workspace", "false"]]);
  await capture("agent-lead");

  await evaluate(`document.querySelector('.v-agent-scope button[aria-pressed="false"]')?.click()`);
  await wait(`Boolean(document.querySelector('.corral-agent-mode:not([hidden]) .corral-workspace-chat'))`);
  await evaluate(`document.querySelector('[aria-label="Close Ask Corral"]')?.click()`);
  await wait(`!document.querySelector(".corral-agent-window.is-open")`);

  // Visit every main workspace page and check the page frame for horizontal overflow.
  const navigation = JSON.parse(await evaluate(`JSON.stringify([...document.querySelectorAll(".v-sidebar-nav a.v-navitem")].filter(item => !item.closest(".v-pin")).map(item => item.querySelector(".v-navitem-label")?.textContent.trim()).filter(Boolean))`));
  assert.ok(navigation.length >= 15, `Expected the full workspace navigation; found ${navigation.length} pages.`);
  const pageChecks = [];
  for (const label of navigation) {
    const found = await evaluate(`(() => {
      const item = [...document.querySelectorAll(".v-sidebar-nav a.v-navitem")]
        .find(link => !link.closest(".v-pin") && link.querySelector(".v-navitem-label")?.textContent.trim() === ${JSON.stringify(label)});
      item?.click();
      return Boolean(item);
    })()`);
    assert.ok(found, `Navigation entry should be clickable: ${label}`);
    await pause(650);
    const page = JSON.parse(await evaluate(`JSON.stringify({
      heading: document.querySelector("main h1")?.textContent.trim() || "",
      viewport: document.documentElement.clientWidth,
      documentWidth: document.documentElement.scrollWidth
    })`));
    assert.ok(page.heading, `Page should render a heading after opening ${label}.`);
    assert.ok(page.documentWidth <= page.viewport + 1, `${label} overflows the desktop page width.`);
    pageChecks.push([label, page.heading]);
  }
  await evaluate(`document.querySelector('button[aria-label="Hide sidebar"]')?.click()`);
  await wait(`document.querySelector(".v-app")?.dataset.sidebar === "hidden"`);
  const collapsedSidebar = JSON.parse(await evaluate(`JSON.stringify({
    sidebarWidth: document.querySelector(".v-sidebar")?.getBoundingClientRect().width ?? 0,
    viewport: document.documentElement.clientWidth,
    documentWidth: document.documentElement.scrollWidth,
    visibility: getComputedStyle(document.querySelector(".v-sidebar")).visibility,
    sidebarDisplay: getComputedStyle(document.querySelector(".v-sidebar")).display,
    appColumns: getComputedStyle(document.querySelector(".v-app")).gridTemplateColumns,
    labels: [...document.querySelectorAll(".v-navitem-label")].slice(0, 3).map(label => getComputedStyle(label).display)
  })`));
  console.log("Collapsed sidebar visual check:", collapsedSidebar);
  assert.ok(collapsedSidebar.sidebarWidth <= 80 && collapsedSidebar.visibility === "visible");
  assert.ok(collapsedSidebar.documentWidth <= collapsedSidebar.viewport + 1, "Collapsed sidebar must not cause horizontal overflow.");
  await evaluate(`document.querySelector('button[aria-label="Show sidebar"]')?.click()`);
  await wait(`document.querySelector(".v-app")?.dataset.sidebar === "shown"`);
  console.log(`Checked ${pageChecks.length} main pages and both Agent scopes. No page-level horizontal overflow at desktop.`);

  await cdp("Emulation.setDeviceMetricsOverride", { width: 390, height: 844, deviceScaleFactor: 1, mobile: true });
  await pause(500);
  await evaluate(`document.querySelector("[data-ask-corral]")?.click()`);
  await wait(`Boolean(document.querySelector(".corral-agent-window.is-open"))`);
  const mobileAgent = JSON.parse(await evaluate(`JSON.stringify({
    viewport: document.documentElement.clientWidth,
    documentWidth: document.documentElement.scrollWidth,
    panelLeft: document.querySelector(".corral-agent-window").getBoundingClientRect().left,
    panelRight: document.querySelector(".corral-agent-window").getBoundingClientRect().right
  })`));
  assert.ok(mobileAgent.panelLeft >= -1 && mobileAgent.panelRight <= mobileAgent.viewport + 1);
  assert.ok(mobileAgent.documentWidth <= mobileAgent.viewport + 1, "The Agent dock must fit the mobile viewport.");
  await capture("agent-mobile");
  await evaluate(`document.querySelector('[aria-label="Close Ask Corral"]')?.click()`);
  await wait(`!document.querySelector(".corral-agent-window.is-open")`);
  await cdp("Emulation.setDeviceMetricsOverride", { width: 1440, height: 1000, deviceScaleFactor: 1, mobile: false });
  await pause(300);

  await click("Pipeline");
  await wait(`Boolean(document.body.innerText.includes("Contract & SLA"))`);
  await capture("pipeline");
  const leadId = await evaluate(`fetch("/v1/leads?limit=1").then(r => r.json()).then(r => r.items[0]?.id)`);
  assert(leadId, "Demo workspace should contain a lead.");
  // Navigate through the app's own shell event. Do not change any saved lead.
  await evaluate(`window.dispatchEvent(new CustomEvent("corral:lead-action", { detail: { leadId: ${JSON.stringify(leadId)}, action: "deal" } }))`);
  await wait(`Boolean([...document.querySelectorAll("button")].find(el => /Build Quote|New Version/.test(el.textContent) && !el.disabled))`);
  await capture("deal");
  // Opening a new quote reserves its number; keep that one write browser-local.
  await evaluate(`(() => {
    const original = window.fetch;
    window.fetch = async (url, options) => {
      if (String(url).endsWith("/v1/deals/settings") && options?.method === "PUT") {
        const data = JSON.parse(options.body);
        return new Response(JSON.stringify({ ...data, version: data.version + 1 }), { status: 200, headers: { "Content-Type": "application/json" } });
      }
      return original(url, options);
    };
  })()`);
  const quoteButton = await evaluate(`document.body.innerText.includes("Build Quote") ? "Build Quote" : "New Version"`);
  await click(quoteButton);
  await wait(`Boolean(document.querySelector('[role="dialog"], dialog[open]'))`);
  await capture("quote");
  await cdp("Emulation.setDeviceMetricsOverride", { width: 390, height: 844, deviceScaleFactor: 1, mobile: true });
  await pause(400);
  await capture("mobile");
  assert.deepEqual(errors, [], "The UI must not throw browser exceptions.");
  console.log("Signed-in pipeline, Deal Desk, quote editor and mobile layout loaded without browser exceptions. No quote, email or call was sent.");
} finally {
  socket?.close();
  browser.kill("SIGTERM");
  await pause(200);
  await rm(profile, { recursive: true, force: true });
}
