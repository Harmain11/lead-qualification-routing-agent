// Run against the development preview only:
// node scripts/salesforce_setup_smoke.mjs
// Uses the existing local-workspace sign-in; provider calls and installer tabs
// are intercepted only inside this test browser. No package is installed.
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

const origin = process.env.REPLIT_DEV_DOMAIN
  ? `https://${process.env.REPLIT_DEV_DOMAIN}` : null;
assert(origin, "REPLIT_DEV_DOMAIN is required; this test must not run against production.");
const profile = await mkdtemp(join(tmpdir(), "corral-salesforce-smoke-"));
const port = 9237;
const chrome = spawn("chromium", [
  "--headless", "--no-sandbox", "--disable-gpu",
  `--remote-debugging-port=${port}`, `--user-data-dir=${profile}`, "about:blank",
], { stdio: "ignore" });
let socket;
const pause = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
try {
  let target;
  for (let attempt = 0; attempt < 100; attempt++) {
    try {
      target = await (await fetch(`http://127.0.0.1:${port}/json/new?about:blank`, {
        method: "PUT",
      })).json();
      break;
    } catch { await pause(100); }
  }
  assert(target?.webSocketDebuggerUrl, "Chromium did not start.");
  socket = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((resolve, reject) => {
    socket.addEventListener("open", resolve, { once: true });
    socket.addEventListener("error", reject, { once: true });
  });
  let sequence = 0;
  const requests = new Map();
  const runtimeErrors = [];
  socket.addEventListener("message", ({ data }) => {
    const result = JSON.parse(String(data));
    if (result.method === "Runtime.exceptionThrown") {
      runtimeErrors.push(result.params.exceptionDetails.text);
    }
    const pending = requests.get(result.id);
    if (!pending) return;
    requests.delete(result.id);
    if (result.error) pending.reject(new Error(result.error.message));
    else pending.resolve(result.result);
  });
  const cdp = (method, params = {}) => new Promise((resolve, reject) => {
    const id = ++sequence;
    requests.set(id, { resolve, reject });
    socket.send(JSON.stringify({ id, method, params }));
  });
  const evaluate = async (expression) => {
    const result = await cdp("Runtime.evaluate", {
      expression, returnByValue: true, awaitPromise: true,
    });
    assert(!result.exceptionDetails, JSON.stringify(result.exceptionDetails));
    return result.result.value;
  };
  const wait = async (expression) => {
    for (let attempt = 0; attempt < 150; attempt++) {
      if (await evaluate(expression)) return;
      await pause(100);
    }
    throw new Error(`Timed out: ${expression}`);
  };
  const click = async (selector, text) => {
    assert(await evaluate(`(() => {
      const element = [...document.querySelectorAll(${JSON.stringify(selector)})]
        .find(item => item.textContent.trim() === ${JSON.stringify(text)});
      if (!element) return false;
      element.click(); return true;
    })()`), `Missing control: ${text}`);
  };
  await cdp("Runtime.enable");
  await cdp("Page.enable");
  await cdp("Emulation.setDeviceMetricsOverride", {
    width: 1280, height: 900, deviceScaleFactor: 1, mobile: false,
  });
  await cdp("Page.addScriptToEvaluateOnNewDocument", { source: `
    window.__installerTabs = [];
    window.open = (url, target, features) => {
      window.__installerTabs.push({url, target, features}); return null;
    };
    window.__oauthAttempts = {salesforce: 0, hubspot: 0};
    const realFetch = window.fetch.bind(window);
    window.fetch = async (input, init) => {
      const url = typeof input === "string" ? input : input.url;
      for (const provider of ["salesforce", "hubspot"]) {
        if (url.includes("/v1/integrations/" + provider + "/connect")) {
          const attempt = ++window.__oauthAttempts[provider];
          const ok = provider === "salesforce" && attempt > 1;
          return new Response(JSON.stringify(ok
            ? {authorization_url: "/__salesforce_oauth_smoke__"}
            : {detail: "Smoke-test provider unavailable"}), {
            status: ok ? 200 : 503, headers: {"Content-Type": "application/json"}
          });
        }
      }
      return realFetch(input, init);
    };
  ` });
  await cdp("Page.navigate", { url: origin });
  await wait(`document.body.innerText.includes("Continue to local workspace")`);
  await click("button", "Continue to local workspace");
  await wait(`document.querySelector(".app-shell") !== null`);
  // Dismiss real, optional first-run dialogs through their normal controls.
  for (let attempt = 0; attempt < 8; attempt++) {
    await evaluate(`(() => {
      const button = [...document.querySelectorAll('[role="dialog"] button')]
        .find(item => ["Not now", "Skip tour"].includes(item.textContent.trim()));
      button?.click();
    })()`);
    await pause(200);
  }
  await click("button", "Integrations");
  await wait(`document.querySelector(".provider-salesforce") !== null`);
  const salesforceButton = () => evaluate(`(() => {
    const button = [...document.querySelectorAll(".provider-salesforce .card-actions button")]
      .find(item => ["Connect", "Reconnect"].includes(item.textContent.trim()));
    button?.click(); return Boolean(button);
  })()`);
  assert(await salesforceButton());
  await wait(`document.querySelector(".salesforce-guide") !== null`);
  assert.equal(await evaluate("window.__oauthAttempts.salesforce"), 0);
  const installer = await evaluate(`(() => {
    const link = document.querySelector(".salesforce-package-link");
    return {href:link.href, target:link.target, rel:link.rel};
  })()`);
  assert.equal(installer.href,
    "https://login.salesforce.com/packaging/installPackage.apexp?p0=04taj000000j95Z");
  assert.equal(installer.target, "_blank");
  assert.match(installer.rel, /noopener/);
  assert.equal(await evaluate("window.__installerTabs.length"), 0,
    "Connect must wait for an explicit click on the package installer link.");
  assert(await evaluate(`document.querySelector(".salesforce-guide-footer .primary-button").disabled`));
  await pause(300); // Allow the modal's 220ms entrance transition to settle.
  const desktopShot = await cdp("Page.captureScreenshot", { format: "png" });
  await writeFile("/tmp/corral-salesforce-guide-desktop.png", Buffer.from(desktopShot.data, "base64"));
  await evaluate(`document.querySelector(".salesforce-installed-check input").click()`);
  await wait(`!document.querySelector(".salesforce-guide-footer .primary-button").disabled`);
  await evaluate(`document.querySelector(".salesforce-guide-footer .primary-button").focus()`);
  await cdp("Input.dispatchKeyEvent", { type: "keyDown", key: "Tab", code: "Tab" });
  assert(await evaluate(`document.activeElement === document.querySelector(".salesforce-guide-close")`));
  await click(".salesforce-guide button", "Cancel");
  await wait(`!document.querySelector(".salesforce-guide")`);
  assert.equal(await evaluate("window.__oauthAttempts.salesforce"), 0);
  assert(await salesforceButton());
  await wait(`document.querySelector(".salesforce-guide") !== null`);
  assert(await evaluate(`!document.querySelector(".salesforce-installed-check input").checked`));
  await cdp("Emulation.setDeviceMetricsOverride", {
    width: 390, height: 844, deviceScaleFactor: 1, mobile: true,
  });
  await evaluate(`document.documentElement.dataset.theme = "dark"`);
  await pause(300); // Let responsive layout and theme styles settle before measuring.
  const geometry = await evaluate(`(() => {
    const bounds = document.querySelector(".salesforce-guide").getBoundingClientRect();
    return {left:bounds.left, right:bounds.right, top:bounds.top, bottom:bounds.bottom,
      viewportWidth:innerWidth, viewportHeight:innerHeight};
  })()`);
  assert(geometry.left >= -1 && geometry.right <= geometry.viewportWidth + 1, JSON.stringify(geometry));
  assert(geometry.top >= -1 && geometry.bottom <= geometry.viewportHeight + 1, JSON.stringify(geometry));
  const screenshot = await cdp("Page.captureScreenshot", { format: "png" });
  await writeFile("/tmp/corral-salesforce-guide-mobile.png", Buffer.from(screenshot.data, "base64"));
  await evaluate(`document.querySelector(".salesforce-installed-check input").click()`);
  await click(".salesforce-guide button", "Continue to Salesforce");
  await wait(`document.querySelector(".salesforce-guide-error") !== null`);
  assert.equal(await evaluate("window.__oauthAttempts.salesforce"), 1);
  assert.match(await evaluate(`document.querySelector(".salesforce-guide-error").textContent`),
    /Smoke-test provider unavailable/);
  assert(await evaluate(`!document.querySelector(".salesforce-guide-footer .primary-button").disabled`));
  await click(".salesforce-guide button", "Cancel");
  await wait(`!document.querySelector(".salesforce-guide")`);
  assert(await evaluate(`(() => {
    const button = [...document.querySelectorAll(".provider-hubspot .card-actions button")]
      .find(item => ["Connect", "Reconnect"].includes(item.textContent.trim()));
    button?.click(); return Boolean(button);
  })()`));
  await wait(`window.__oauthAttempts.hubspot === 1`);
  assert.equal(await evaluate(`document.querySelector(".salesforce-guide") !== null`), false);
  assert.equal(await evaluate("window.__installerTabs.length"), 0,
    "The guide must not open Salesforce automatically on Connect or Reconnect.");
  assert(await salesforceButton());
  await wait(`document.querySelector(".salesforce-guide") !== null`);
  await evaluate(`document.querySelector(".salesforce-installed-check input").click()`);
  await click(".salesforce-guide button", "Continue to Salesforce");
  await wait(`location.pathname === "/__salesforce_oauth_smoke__"`);
  assert.deepEqual(runtimeErrors, []);
  console.log("PASS: install URL, OAuth gate, cancellation/reset, focus trap, mobile/dark layout, error/retry, unrelated provider and authorization redirect.");
} finally {
  socket?.close();
  const exited = new Promise((resolve) => chrome.once("exit", resolve));
  chrome.kill("SIGTERM");
  await Promise.race([exited, pause(2000)]);
  if (chrome.exitCode === null) chrome.kill("SIGKILL");
  await rm(profile, { recursive: true, force: true, maxRetries: 3, retryDelay: 100 });
}
