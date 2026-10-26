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
