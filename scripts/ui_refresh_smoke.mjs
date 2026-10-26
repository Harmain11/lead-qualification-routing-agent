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
  let pageTarget;
  for (let i = 0; i < 80; i++) {
    try {
      const targets = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
      pageTarget = targets.find((target) => target.type === "page" && target.url === "about:blank");
    } catch {}
    if (pageTarget) break;
    await pause(100);
  }
  assert(pageTarget, "Browser page target did not start.");
  socket = new WebSocket(pageTarget.webSocketDebuggerUrl);
  await new Promise((resolve) => socket.addEventListener("open", resolve, { once: true }));
  let seq = 0;
  const pending = new Map();
  const errors = [];
  const failedRequests = [];
  socket.addEventListener("message", ({ data }) => {
    const result = JSON.parse(String(data));
    if (result.method === "Runtime.exceptionThrown") errors.push(result.params.exceptionDetails.text);
    if (result.method === "Network.loadingFailed") {
      failedRequests.push({ requestId: result.params.requestId, error: result.params.errorText });
    }
    if (result.method === "Network.responseReceived" && result.params.response.status >= 400) {
      failedRequests.push({ url: result.params.response.url, status: result.params.response.status });
    }
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
    const diagnostic = await evaluate(`JSON.stringify({
      title: document.title,
      readyState: document.readyState,
      text: document.body?.innerText.slice(0, 450),
      html: document.body?.innerHTML.slice(0, 1400),
      scripts: [...document.scripts].map(script => script.src || "inline")
    })`);
    throw new Error(`UI did not become ready: ${expression}\n${diagnostic}\nRuntime exceptions: ${JSON.stringify(errors)}\nFailed requests: ${JSON.stringify(failedRequests)}`);
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
  await cdp("Network.enable");
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
  await pause(350);
  await wait(`Boolean(document.querySelector(".corral-workspace-chat"))`);
  const initialScope = await evaluate(`JSON.stringify([...document.querySelectorAll(".v-agent-scope button")].map(button => [button.textContent.trim(), button.getAttribute("aria-pressed")]))`);
  assert.deepEqual(JSON.parse(initialScope), [["Lead", "false"], ["Workspace", "true"]]);
  await capture("agent-workspace");
  const workspaceCapabilityCopy = JSON.parse(await evaluate(`JSON.stringify({
    knowledge: document.querySelector('[aria-label="Workspace document source status"]')?.innerText ?? "",
    audit: document.querySelector('[aria-label="Approval and audit guidance"]')?.innerText ?? "",
    prompts: [...document.querySelectorAll(".corral-workspace-prompts button")].map(button => button.textContent.trim())
  })`));
  assert.match(workspaceCapabilityCopy.knowledge, /Not connected/);
  assert.match(workspaceCapabilityCopy.knowledge, /won.t guess/);
  assert.match(workspaceCapabilityCopy.audit, /review/i);
  assert.match(workspaceCapabilityCopy.audit, /reversal/i);
  for (const prompt of [
    "Start a quote or proposal",
    "Use a sequence or playbook",
    "Coach the pipeline from recorded data",
    "Check workspace document sources",
    "Explain approvals and audit history",
  ]) {
    assert.ok(workspaceCapabilityCopy.prompts.includes(prompt), `Missing workspace prompt: ${prompt}`);
  }
  const originalAgentTheme = await evaluate(`document.documentElement.dataset.theme || "light"`);
  const verifyWorkspaceCards = async (theme) => {
    const cards = JSON.parse(await evaluate(`JSON.stringify(
      [...document.querySelectorAll(".corral-workspace-status-card")].map(card => ({
        background: getComputedStyle(card).backgroundColor,
        title: getComputedStyle(card.querySelector("strong")).color,
        body: getComputedStyle(card.querySelector("p")).color,
        width: card.getBoundingClientRect().width,
        right: card.getBoundingClientRect().right,
        viewportWidth: window.innerWidth
      }))
    )`));
    assert.equal(cards.length, 2, `${theme}: both workspace capability cards should render.`);
    const channels = value => value.match(/[\d.]+/g).map(Number).slice(0, 3);
    const luminance = value => channels(value).map(channel => {
      const normalized = channel / 255;
      return normalized <= 0.04045 ? normalized / 12.92 : ((normalized + 0.055) / 1.055) ** 2.4;
    }).reduce((sum, value, index) => sum + value * [0.2126, 0.7152, 0.0722][index], 0);
    const contrast = (foreground, background) => {
      const front = luminance(foreground);
      const back = luminance(background);
      return (Math.max(front, back) + 0.05) / (Math.min(front, back) + 0.05);
    };
    for (const card of cards) {
      assert.ok(card.width > 0 && card.right <= card.viewportWidth, `${theme}: capability card should fit the viewport.`);
      assert.ok(contrast(card.title, card.background) >= 4.5, `${theme}: card title contrast is too low.`);
      assert.ok(contrast(card.body, card.background) >= 4.5, `${theme}: card text contrast is too low.`);
    }
  };
  const agentCardFixtureMarkup = {
    workspace: `
      <div class="corral-workspace-message-group">
        <article class="corral-workspace-message is-assistant">
          <span>Corral</span>
          <p>For a quote, choose the lead you want to continue with.</p>
          <div class="agent-smoke-choice-host"></div>
        </article>
        <details class="corral-workspace-action" open aria-label="Review proposed action">
          <summary class="corral-workspace-action-heading">
            <strong>Review proposed action</strong>
            <p>Check the proposed details before approving.</p>
          </summary>
          <pre>Proposed draft content wraps safely when it reaches the edge of the card.</pre>
          <p class="corral-workspace-action-result" role="status">Draft is ready for review.</p>
          <div class="corral-workspace-action-buttons">
            <button class="primary-button" type="button">Approve and apply</button>
            <button class="secondary-button" type="button">Not now</button>
          </div>
        </details>
        <div class="corral-workspace-prompts">
          <button type="button">Start a quote or proposal</button>
        </div>
        <p class="corral-workspace-error" role="alert">Example error for the responsive review.</p>
      </div>`,
    lead: `
      <article class="lead-agent-message is-assistant">
        <span class="lead-agent-role">Corral</span>
        <p>For a quote, choose the lead you want to continue with.</p>
        <div class="agent-smoke-choice-host"></div>
        <details class="lead-agent-action" open>
          <summary class="lead-agent-action-title">
            <span aria-hidden="true">↳</span>
            <strong>Review proposed action</strong>
          </summary>
          <pre>Proposed draft content wraps safely when it reaches the edge of the card.</pre>
          <p class="lead-agent-notice" role="status">Draft is ready for review.</p>
          <p class="lead-agent-error" role="alert">Example error for the responsive review.</p>
          <div class="lead-agent-action-buttons">
            <button class="primary-button" type="button">Approve and apply</button>
            <button class="secondary-button" type="button">Not now</button>
          </div>
        </details>
        <div class="lead-agent-suggestions">
          <button type="button">Start a quote or proposal</button>
        </div>
      </article>`,
  };
  const mountAgentCardFixture = async (mode) => {
    await evaluate(`(async () => {
      const mode = ${JSON.stringify(mode)};
      const oldRoot = window.__corralAgentSmokeRoots?.[mode];
      if (oldRoot) oldRoot.unmount();
      document.querySelectorAll('[data-agent-smoke-fixture="' + mode + '"]').forEach(fixture => fixture.remove());
      const transcriptClass = mode === "workspace" ? ".corral-workspace-transcript" : ".lead-agent-transcript";
      const transcript = document.querySelector(".corral-agent-window " + transcriptClass);
      if (!transcript) throw new Error("Missing Agent transcript for " + mode);
      const fixture = document.createElement("div");
      fixture.dataset.agentSmokeFixture = mode;
      fixture.innerHTML = ${JSON.stringify(agentCardFixtureMarkup[mode])};
      transcript.append(fixture);
      const [reactModule, reactDomModule, { AgentLeadChoices }] = await Promise.all([
        import("/node_modules/.vite/deps/react.js"),
        import("/node_modules/.vite/deps/react-dom_client.js"),
        import("/src/components/AgentLeadChoices.tsx")
      ]);
      const React = reactModule.default ?? reactModule;
      const ReactDOM = reactDomModule.default ?? reactDomModule;
      const createRoot = reactDomModule.createRoot ?? ReactDOM.createRoot;
      const root = createRoot(fixture.querySelector(".agent-smoke-choice-host"));
      window.__corralAgentSmokeRoots ??= {};
      window.__corralAgentSmokeRoots[mode] = root;
      root.render(React.createElement(AgentLeadChoices, {
        leads: [
          { id: "smoke-kathi", name: "Kathi Sitek", company: "Mosaic Systems", suggested: true },
          { id: "smoke-les", name: "Les Lehman", company: "North Harbor Group", suggested: false }
        ],
        onSelect: leadId => { window.__corralAgentSmokeSelection = { mode, leadId }; }
      }));
      return true;
    })()`);
    await wait(`Boolean(document.querySelector(${JSON.stringify(`[data-agent-smoke-fixture="${mode}"] .corral-agent-choice`)}))`);

    const focusSelector = `[data-agent-smoke-fixture="${mode}"] .corral-agent-choice`;
    await cdp("DOM.enable");
    await cdp("CSS.enable");
    const documentNode = await cdp("DOM.getDocument", { depth: 0 });
    const focusNode = await cdp("DOM.querySelector", {
      nodeId: documentNode.root.nodeId,
      selector: focusSelector,
    });
    assert.ok(focusNode.nodeId, `${mode}: lead choice should exist for keyboard-focus styling.`);
    await cdp("CSS.forcePseudoState", { nodeId: focusNode.nodeId, pseudoClasses: ["focus-visible"] });
    const focus = JSON.parse(await evaluate(`JSON.stringify((() => {
      const target = document.querySelector(${JSON.stringify(focusSelector)});
      const style = target ? getComputedStyle(target) : null;
      return {
        isChoice: target?.tagName === "BUTTON",
        visibleFocus: Boolean(target?.matches(":focus-visible")),
        outlineWidth: parseFloat(style?.outlineWidth ?? "0"),
        outlineStyle: style?.outlineStyle ?? "none"
      };
    })())`));
    assert.equal(focus.isChoice, true, `${mode}: lead choices should use native buttons.`);
    assert.equal(focus.visibleFocus, true, `${mode}: lead choice should expose keyboard-visible focus.`);
    assert.ok(focus.outlineWidth >= 2 && focus.outlineStyle !== "none", `${mode}: lead choice focus ring should be visible.`);
    await cdp("CSS.forcePseudoState", { nodeId: focusNode.nodeId, pseudoClasses: [] });

    const selectedRowSelector = `[data-agent-smoke-fixture="${mode}"] .corral-agent-choice-list .corral-agent-choice:nth-of-type(2)`;
    await evaluate(`document.querySelector(${JSON.stringify(selectedRowSelector)})?.click()`);
    await wait(`document.querySelector(${JSON.stringify(`${selectedRowSelector}[aria-pressed="true"]`)})?.getAttribute("aria-pressed") === "true"`);
    const selection = JSON.parse(await evaluate(`JSON.stringify((() => {
      const fixture = document.querySelector('[data-agent-smoke-fixture="${mode}"]');
      return {
        pressed: [...fixture.querySelectorAll(".corral-agent-choice")].map(row => row.getAttribute("aria-pressed")),
        selectedId: window.__corralAgentSmokeSelection?.leadId
      };
    })())`));
    assert.deepEqual(selection.pressed, ["false", "true"], `${mode}: selecting one lead should expose the selected state without color alone.`);
    assert.equal(selection.selectedId, "smoke-les", `${mode}: selecting a lead should invoke the existing selection callback.`);
  };
  const verifyAgentCardFixture = async (mode, theme, device) => {
    const headingSelector = `[data-agent-smoke-fixture="${mode}"] .corral-agent-choice-heading`;
    if (!(await evaluate(`Boolean(document.querySelector(${JSON.stringify(headingSelector)}))`))) {
      await mountAgentCardFixture(mode);
    }
    const report = JSON.parse(await evaluate(`JSON.stringify((() => {
      const fixture = document.querySelector('[data-agent-smoke-fixture="${mode}"]');
      const group = fixture?.querySelector(".corral-agent-choice-list");
      const heading = fixture?.querySelector(".corral-agent-choice-heading");
      const rows = [...(group?.querySelectorAll(".corral-agent-choice") ?? [])];
      const rowBoxes = rows.map(row => row.getBoundingClientRect());
      const message = fixture?.querySelector(${JSON.stringify(mode === "workspace" ? ".corral-workspace-message.is-assistant > p" : ".lead-agent-message.is-assistant > p")});
      const action = fixture?.querySelector(${JSON.stringify(mode === "workspace" ? ".corral-workspace-action" : ".lead-agent-action")});
      const actionSummary = fixture?.querySelector(${JSON.stringify(mode === "workspace" ? ".corral-workspace-action-heading" : ".lead-agent-action-title")});
      const status = fixture?.querySelector('[role="status"]');
      const error = fixture?.querySelector('[role="alert"]');
      const prompt = fixture?.querySelector(${JSON.stringify(mode === "workspace" ? ".corral-workspace-prompts button" : ".lead-agent-suggestions button")});
      const actionButtons = [...(fixture?.querySelectorAll(${JSON.stringify(mode === "workspace" ? ".corral-workspace-action-buttons button" : ".lead-agent-action-buttons button")}) ?? [])];
      const overflowers = [...(fixture?.querySelectorAll("*") ?? [])].filter(element => {
        const style = getComputedStyle(element);
        return ["auto", "scroll"].includes(style.overflowY) && element.scrollHeight > element.clientHeight + 1;
      }).map(element => element.className || element.tagName.toLowerCase());
      const font = element => element ? parseFloat(getComputedStyle(element).fontSize) : 0;
      const style = element => element ? getComputedStyle(element) : null;
      const rect = element => {
        const box = element?.getBoundingClientRect();
        return box ? { width: box.width, height: box.height, right: box.right } : null;
      };
      return {
        heading: heading?.textContent.trim(),
        role: group?.getAttribute("role"),
        correctlyLabelled: group?.getAttribute("aria-labelledby") === heading?.id,
        rows: rows.map((row, index) => ({
          width: rowBoxes[index]?.width ?? 0,
          height: rowBoxes[index]?.height ?? 0,
          nameFont: font(row.querySelector("strong")),
          borderStyle: style(row).borderStyle,
          borderWidth: parseFloat(style(row).borderTopWidth),
          background: style(row).backgroundColor,
          pressed: row.getAttribute("aria-pressed")
        })),
        primaryBackground: style(fixture?.querySelector(".primary-button"))?.backgroundColor,
        messageFont: font(message),
        messageLineHeight: style(message)?.lineHeight,
        action: rect(action),
        actionPadding: action ? ["paddingTop", "paddingRight", "paddingBottom", "paddingLeft"].map(key => parseFloat(style(action)[key])) : [],
        summaryHeight: rect(actionSummary)?.height ?? 0,
        statusFont: font(status),
        errorFont: font(error),
        prompt: { font: font(prompt), height: rect(prompt)?.height ?? 0 },
        actionButtons: actionButtons.map(button => ({
          height: button.getBoundingClientRect().height,
          font: font(button)
        })),
        overflowers,
        fixture: rect(fixture),
        transcript: rect(fixture?.parentElement)
      };
    })())`));
    const label = `${mode}/${device.label}/${theme}`;
    assert.equal(report.heading, "Choose a lead", `${label}: lead choices should have a clear heading.`);
    assert.equal(report.role, "group", `${label}: lead choices should be an accessible button group.`);
    assert.equal(report.correctlyLabelled, true, `${label}: lead-choice group should be labelled by its heading.`);
    assert.equal(report.rows.length, 2, `${label}: both candidate leads should render.`);
    assert.ok(report.rows.every(row => row.width >= 44 && row.height >= 44), `${label}: all lead-choice rows must meet 44px touch targets.`);
    assert.ok(Math.max(...report.rows.map(row => row.width)) - Math.min(...report.rows.map(row => row.width)) <= 1, `${label}: choices must use consistent widths.`);
    assert.ok(report.rows.every(row => row.nameFont >= 16), `${label}: lead names must be at least 16px.`);
    assert.ok(report.rows.every(row => row.borderStyle === "solid" && row.borderWidth >= 1), `${label}: lead choices should be outlined rows.`);
    assert.ok(report.rows.every(row => row.background !== report.primaryBackground), `${label}: lead choices must not use the solid primary-button fill.`);
    assert.ok(report.messageFont >= 16 && report.messageLineHeight === "24px", `${label}: reply text should be 16px with 1.5 line height (${report.messageFont}px, ${report.messageLineHeight}).`);
    assert.ok(report.action?.width > 0 && report.action.right <= report.transcript.right + 1, `${label}: action-review card should fit the transcript.`);
    assert.ok(report.actionPadding.every(padding => padding >= 14), `${label}: action cards should have balanced padding.`);
    assert.ok(report.summaryHeight >= 44, `${label}: action review disclosure should meet touch-target height.`);
    assert.ok(report.statusFont >= 16 && report.errorFont >= 16, `${label}: approval and error states must remain readable.`);
    assert.ok(report.prompt.font >= 16 && report.prompt.height >= 44, `${label}: suggested prompt should be readable and easy to tap.`);
    assert.ok(report.actionButtons.every(button => button.height >= 44 && button.font >= 16), `${label}: approval buttons must meet touch and text-size targets.`);
    assert.deepEqual(report.overflowers, [], `${label}: cards must not create nested scroll regions.`);
  };
  await evaluate(`document.documentElement.dataset.theme = "light"`);
  await pause(100);
  await verifyWorkspaceCards("light");
  await capture("agent-workspace-light");
  await evaluate(`document.documentElement.dataset.theme = "dark"`);
  await pause(150);
  await verifyWorkspaceCards("dark");
  await capture("agent-workspace-dark");
  await evaluate(`document.documentElement.dataset.theme = ${JSON.stringify(originalAgentTheme)}`);
  await mountAgentCardFixture("workspace");

  await evaluate(`document.querySelector('.v-agent-scope button[aria-pressed="false"]')?.click()`);
  await wait(`Boolean(document.querySelector(".corral-agent-lead-option"))`);
  await pause(200);
  await evaluate(`document.querySelector(".corral-agent-lead-option")?.click()`);
  await wait(`Boolean(document.querySelector(".corral-agent-window .lead-agent"))`);
  await pause(450);
  const leadScope = await evaluate(`JSON.stringify([...document.querySelectorAll(".v-agent-scope button")].map(button => [button.textContent.trim(), button.getAttribute("aria-pressed")]))`);
  assert.deepEqual(JSON.parse(leadScope), [["Lead", "true"], ["Workspace", "false"]]);
  await mountAgentCardFixture("lead");
  await capture("agent-lead");

  await evaluate(`document.querySelector('.v-agent-scope button[aria-pressed="false"]')?.click()`);
  await wait(`Boolean(document.querySelector('.corral-agent-mode:not([hidden]) .corral-workspace-chat'))`);
  await evaluate(`document.querySelector('[aria-label="Close Ask Corral"]')?.click()`);
  await wait(`!document.querySelector(".corral-agent-window.is-open")`);

  // Visit every main workspace page and check the page frame for horizontal overflow.
  const navigation = JSON.parse(await evaluate(`JSON.stringify([...document.querySelectorAll(".v-sidebar-nav a.v-navitem")].filter(item => !item.closest(".v-pin")).map(item => item.querySelector(".v-navitem-label")?.textContent.trim()).filter(Boolean))`));
  assert.ok(navigation.length >= 15, `Expected the full workspace navigation; found ${navigation.length} pages.`);
  const pageChecks = [];
  let primaryActionsChecked = 0;
  const requestedVisualChecks = { records: 0, contacts: 0, settingsThemes: 0, emailBody: 0, integrationCards: 0 };
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
    if (label === "Contacts") await capture("contacts-page-v5");
    if (label === "Companies") await capture("companies-page-v5");
    const nameDecorations = JSON.parse(await evaluate(`JSON.stringify({
      record: [...document.querySelectorAll(".records-card-name")].find(element => element.getClientRects().length)
        ? getComputedStyle([...document.querySelectorAll(".records-card-name")].find(element => element.getClientRects().length)).textDecorationLine : null,
      contactRow: [...document.querySelectorAll(".contact-row")].find(element => element.getClientRects().length)
        ? getComputedStyle([...document.querySelectorAll(".contact-row")].find(element => element.getClientRects().length)).textDecorationLine : null,
      contactName: [...document.querySelectorAll(".contact-row strong")].find(element => element.getClientRects().length)
        ? getComputedStyle([...document.querySelectorAll(".contact-row strong")].find(element => element.getClientRects().length)).textDecorationLine : null
    })`));
    if (nameDecorations.record !== null) {
      assert.equal(nameDecorations.record, "none", `${label}: record names must not be underlined.`);
      requestedVisualChecks.records += 1;
      await capture("records-v5");
    }
    if (nameDecorations.contactRow !== null) {
      assert.equal(nameDecorations.contactRow, "none", `${label}: contact rows must not be underlined.`);
      assert.equal(nameDecorations.contactName, "none", `${label}: contact names must not be underlined.`);
      requestedVisualChecks.contacts += 1;
      await capture("contacts-v5");
    }
    if (label === "Contacts" && nameDecorations.contactRow === null) {
      const fixtureDecorations = JSON.parse(await evaluate(`(() => {
        const fixture = document.createElement("aside");
        fixture.className = "v5-name-style-fixture";
        fixture.innerHTML = '<button class="records-card-name" type="button">Sample company</button><div class="contact-row"><strong>Sample contact</strong></div>';
        Object.assign(fixture.style, {
          position: "fixed", left: "-10000px", top: "0", visibility: "hidden"
        });
        document.querySelector(".v-app")?.append(fixture);
        const record = fixture.querySelector(".records-card-name");
        record.focus();
        const contact = fixture.querySelector(".contact-row");
        const contactName = fixture.querySelector(".contact-row strong");
        const result = {
          record: getComputedStyle(record).textDecorationLine,
          contact: getComputedStyle(contact).textDecorationLine,
          contactName: getComputedStyle(contactName).textDecorationLine
        };
        fixture.remove();
        return JSON.stringify(result);
      })()`));
      assert.deepEqual(fixtureDecorations, { record: "none", contact: "none", contactName: "none" });
      requestedVisualChecks.records += 1;
      requestedVisualChecks.contacts += 1;
    }
    if (await evaluate(`Boolean(document.querySelector(".settings-category-tabs"))`)) {
      const measureTabs = async () => JSON.parse(await evaluate(`JSON.stringify(
        [...document.querySelectorAll(".settings-category-tabs [role='tab']")].map(tab => {
          const title = tab.children[1].getBoundingClientRect();
          const caption = tab.children[2].getBoundingClientRect();
          const style = getComputedStyle(tab);
          return {
            selected: tab.getAttribute("aria-selected"),
            display: style.display,
            color: getComputedStyle(tab.children[1]).color,
            background: style.backgroundColor,
            surface: getComputedStyle(tab.parentElement).backgroundColor,
            captionColor: getComputedStyle(tab.children[2]).color,
            titleLeft: title.left,
            captionLeft: caption.left,
            titleTop: title.top,
            captionTop: caption.top
          };
        })
      )`));
      const verifyTabs = (tabs, theme) => {
        assert.equal(tabs.length, 2, `${theme}: both settings tabs should render.`);
        const color = (value) => {
          const channels = value.match(/[\d.]+/g).map(Number);
          return [channels[0], channels[1], channels[2], channels.length > 3 ? channels[3] : 1];
        };
        const composite = (foreground, background) => {
          const front = color(foreground);
          const back = color(background);
          return front.slice(0, 3).map((channel, index) => channel * front[3] + back[index] * (1 - front[3]));
        };
        const luminance = (channels) => channels.map(channel => {
          const value = channel / 255;
          return value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4;
        }).reduce((sum, value, index) => sum + value * [0.2126, 0.7152, 0.0722][index], 0);
        const contrast = (foreground, background) => {
          const foregroundLuminance = luminance(color(foreground).slice(0, 3));
          const backgroundLuminance = luminance(composite(background, tabs[0].surface));
          return (Math.max(foregroundLuminance, backgroundLuminance) + 0.05) /
            (Math.min(foregroundLuminance, backgroundLuminance) + 0.05);
        };
        for (const tab of tabs) {
          assert.equal(tab.display, "grid", `${theme}: tab labels should use aligned rows.`);
          assert.ok(Math.abs(tab.titleLeft - tab.captionLeft) < 1, `${theme}: tab title and caption should align.`);
          assert.ok(tab.titleTop < tab.captionTop, `${theme}: tab title should be above its caption.`);
          assert.ok(contrast(tab.color, tab.background) >= 4.5, `${theme}: tab titles need readable contrast.`);
          assert.ok(contrast(tab.captionColor, tab.background) >= 4.5, `${theme}: tab captions need readable contrast.`);
        }
        assert.equal(tabs.filter(tab => tab.selected === "true").length, 1, `${theme}: exactly one category is selected.`);
      };
      await evaluate(`document.documentElement.dataset.theme = "light"`);
      await pause(100);
      verifyTabs(await measureTabs(), "light");
      await capture("settings-v5-light");
      await evaluate(`document.querySelector("#settings-user-tab")?.click()`);
      await wait(`document.querySelector("#settings-user-tab")?.getAttribute("aria-selected") === "true"`);
      await evaluate(`document.documentElement.dataset.theme = "dark"`);
      await pause(150);
      verifyTabs(await measureTabs(), "dark");
      await capture("settings-v5-dark");
      await evaluate(`document.documentElement.dataset.theme = "light"`);
      await evaluate(`document.querySelector("#settings-company-tab")?.click()`);
      requestedVisualChecks.settingsThemes += 1;
    }
    if (label === "Inbox") {
      const sampleEmail = [
        "You don't often get email from reese.maddox@ingenuity-systems.cloud. Learn why this is important [2] [EXTERNAL EMAIL] DMCU Security Advisory: This email was received from an external source. Do not open attachments, click links, or trust the content. If you are suspicious, contact I.T. Hello Kathi, I'm looking forward to a 10-minute Zoom call about a potential tech collaboration. Let me know a suitable time. Best Regards, Reese Maddox, Manager Customer Success. PS: If you want to opt out of our email list, reply with STOP. CONFIDENTIALITY NOTICE: This email is confidential and intended only for the named recipient. CONFIDENTIALITY NOTICE: This email is confidential and intended only for the named recipient. Links: ----- [1] https://aka.ms/security [2] https://aka.ms/sender-identification",
      ].join("\n");
      await evaluate(`(async () => {
        const [reactModule, reactDomModule, { ProviderEmailBody }] = await Promise.all([
          import("/node_modules/.vite/deps/react.js"),
          import("/node_modules/.vite/deps/react-dom_client.js"),
          import("/src/components/ProviderEmailBody.tsx")
        ]);
        const React = reactModule.default ?? reactModule;
        const ReactDOM = reactDomModule.default ?? reactDomModule;
        const createRoot = reactDomModule.createRoot ?? ReactDOM.createRoot;
        const host = document.createElement("section");
        host.className = "email-v5-smoke-fixture";
        Object.assign(host.style, {
          position: "fixed", zIndex: "999999", left: "50%", top: "50%",
          transform: "translate(-50%, -50%)", width: "min(780px, 90vw)",
          maxHeight: "80vh", overflow: "auto", padding: "20px",
          background: "var(--surface)", border: "1px solid var(--separator)",
          borderRadius: "16px", boxShadow: "0 20px 60px rgba(0,0,0,.25)"
        });
        const heading = document.createElement("h2");
        heading.textContent = "Provider email display check";
        host.append(heading);
        document.querySelector(".v-app")?.append(host);
        window.__corralEmailSmokeRoot = createRoot(host);
        window.__corralEmailSmokeRoot.render(React.createElement(ProviderEmailBody, { content: ${JSON.stringify(sampleEmail)} }));
        return Boolean(document.querySelector(".v-app"));
      })()`);
      await wait(`Boolean(document.querySelector(".email-v5-smoke-fixture .provider-email-body-main"))`);
      const emailCheck = JSON.parse(await evaluate(`JSON.stringify((() => {
        const fixture = document.querySelector(".email-v5-smoke-fixture");
        const body = fixture?.querySelector(".provider-email-body-main");
        const details = [...(fixture?.querySelectorAll(".provider-email-disclosure") ?? [])];
        return {
          text: body?.textContent ?? "",
          fontSize: body ? getComputedStyle(body).fontSize : "",
          fontWeight: body ? getComputedStyle(body).fontWeight : "",
          disclosures: details.length,
          collapsed: details.filter(item => !item.open).length
        };
      })())`));
      assert.match(emailCheck.text, /Hello Kathi/i);
      assert.match(emailCheck.text, /potential tech collaboration/i);
      assert.doesNotMatch(emailCheck.text, /external email|security advisory|unsubscribe|confidential/i);
      assert.equal(emailCheck.fontSize, "14px");
      assert.equal(emailCheck.fontWeight, "400");
      assert.ok(emailCheck.disclosures > 0);
      assert.equal(emailCheck.collapsed, emailCheck.disclosures);
      await capture("email-v5-body");
      await evaluate(`window.__corralEmailSmokeRoot?.unmount(); document.querySelector(".email-v5-smoke-fixture")?.remove()`);
      await evaluate(`(async () => {
        const [reactModule, reactDomModule, { GmailInquiryView }] = await Promise.all([
          import("/node_modules/.vite/deps/react.js"),
          import("/node_modules/.vite/deps/react-dom_client.js"),
          import("/src/components/GmailInquiryView.tsx")
        ]);
        const React = reactModule.default ?? reactModule;
        const ReactDOM = reactDomModule.default ?? reactDomModule;
        const createRoot = reactDomModule.createRoot ?? ReactDOM.createRoot;
        const host = document.createElement("section");
        host.className = "gmail-inquiry-v5-smoke-fixture";
        Object.assign(host.style, {
          position: "fixed", zIndex: "999999", left: "50%", top: "50%",
          transform: "translate(-50%, -50%)", width: "min(780px, 90vw)",
          maxHeight: "80vh", overflow: "auto", padding: "20px",
          background: "var(--surface)", border: "1px solid var(--separator)",
          borderRadius: "16px", boxShadow: "0 20px 60px rgba(0,0,0,.25)"
        });
        const heading = document.createElement("h2");
        heading.textContent = "Gmail inquiry display check";
        host.append(heading);
        document.querySelector(".v-app")?.append(host);
        const message = [
          "--- Gmail message ui-smoke ---",
          "From: Reese Maddox <reese.maddox@example.test>",
          "To: Kathi <kathi@example.test>",
          "Subject: Kathi Can I call you on Thursday? " + ${JSON.stringify(sampleEmail)}
        ].join(String.fromCharCode(10));
        window.__corralInquirySmokeRoot = createRoot(host);
        window.__corralInquirySmokeRoot.render(React.createElement(GmailInquiryView, {
          message, source: "gmail", leadEmail: "kathi@example.test"
        }));
        return true;
      })()`);
      await wait(`Boolean(document.querySelector(".gmail-inquiry-v5-smoke-fixture .gmail-inquiry-body-content"))`);
      const inquiryEmailCheck = JSON.parse(await evaluate(`JSON.stringify((() => {
        const fixture = document.querySelector(".gmail-inquiry-v5-smoke-fixture");
        const body = fixture?.querySelector(".gmail-inquiry-body-content");
        const paragraph = body?.querySelector("p");
        const details = [...(fixture?.querySelectorAll(".gmail-inquiry-disclosure") ?? [])];
        return {
          text: body?.textContent ?? "",
          subject: fixture?.querySelector(".gmail-inquiry-subject h4")?.textContent ?? "",
          fontSize: paragraph ? getComputedStyle(paragraph).fontSize : "",
          fontWeight: paragraph ? getComputedStyle(paragraph).fontWeight : "",
          disclosures: details.length,
          collapsed: details.filter(item => !item.open).length,
          emptyState: Boolean(fixture?.querySelector(".gmail-inquiry-empty"))
        };
      })())`));
      assert.match(inquiryEmailCheck.text, /Hello Kathi/i);
      assert.doesNotMatch(inquiryEmailCheck.text, /external email|security advisory|unsubscribe|confidential/i);
      assert.equal(inquiryEmailCheck.subject, "Kathi Can I call you on Thursday?");
      assert.equal(inquiryEmailCheck.fontSize, "14px");
      assert.equal(inquiryEmailCheck.fontWeight, "400");
      assert.ok(inquiryEmailCheck.disclosures > 0);
      assert.equal(inquiryEmailCheck.collapsed, inquiryEmailCheck.disclosures);
      assert.equal(inquiryEmailCheck.emptyState, false);
      await pause(200);
      await capture("gmail-inquiry-v5-body");
      await evaluate(`window.__corralInquirySmokeRoot?.unmount(); document.querySelector(".gmail-inquiry-v5-smoke-fixture")?.remove()`);
      requestedVisualChecks.emailBody += 1;
    }
    const primaryActions = JSON.parse(await evaluate(`JSON.stringify([...document.querySelectorAll(
      ".v-app .primary-button, .v-app .button-primary, .v-app .primary-action, .v-app .auth-submit, .v-app .a-submit, .v-app .ct-tour-button-primary, .v-app .records-add-button, .v-app .records-filter-close, .v-app .slack-routing-actions > button, .v-app .v-btn[data-kind='filled']"
    )].filter(button => button.getClientRects().length).map(button => {
      const style = getComputedStyle(button);
      return {
        label: button.innerText.trim(),
        className: button.className,
        disabled: button.disabled,
        color: style.color,
        background: style.backgroundColor,
        backgroundImage: style.backgroundImage,
        opacity: style.opacity
      };
    }))`));
    for (const action of primaryActions) {
      assert.equal(action.color, "rgb(255, 255, 255)", `${label}: primary action text must stay white (${action.label}).`);
      assert.ok(action.background !== "rgba(0, 0, 0, 0)", `${label}: primary action needs a solid readable fill (${JSON.stringify(action)}).`);
      assert.equal(action.opacity, "1", `${label}: primary action must not fade its label (${action.label}).`);
      primaryActionsChecked += 1;
    }
    if (label === "Integrations") {
      await cdp("Emulation.setDeviceMetricsOverride", { width: 810, height: 720, deviceScaleFactor: 1, mobile: false });
      await pause(250);
      await evaluate(`(() => {
        const link = [...document.querySelectorAll(".v-sidebar-nav a.v-navitem")]
          .find(item => item.querySelector(".v-navitem-label")?.textContent.trim() === "Integrations");
        document.activeElement?.blur();
        link?.focus();
        link?.parentElement?.dispatchEvent(new MouseEvent("mouseover", { bubbles: true, relatedTarget: null }));
      })()`);
      await evaluate(`(() => {
        const style = document.createElement("style");
        style.id = "ui-smoke-emulate-hover-flyout";
        style.textContent = "@media (hover: none) { .v-flyout { display: block !important; } }";
        document.head.append(style);
      })()`);
      await wait(`Boolean(document.querySelector(".v-flyout"))`);
      await pause(250);
      const flyoutBounds = JSON.parse(await evaluate(`(() => {
        const flyout = document.querySelector(".v-flyout");
        const rect = flyout?.getBoundingClientRect();
        return JSON.stringify({
          left: rect?.left ?? -1,
          right: rect?.right ?? Infinity,
          viewport: document.documentElement.clientWidth,
          display: flyout ? getComputedStyle(flyout).display : "missing",
          visibility: flyout ? getComputedStyle(flyout).visibility : "missing",
          opacity: flyout ? getComputedStyle(flyout).opacity : "0",
          hoverNone: matchMedia("(hover: none)").matches,
          pointerFine: matchMedia("(pointer: fine)").matches,
          overflowX: flyout ? getComputedStyle(flyout).overflowX : "",
          labels: flyout?.innerText ?? ""
        });
      })()`));
      assert.notEqual(flyoutBounds.display, "none", `Integration flyouts must be visible when opened: ${JSON.stringify(flyoutBounds)}`);
      assert.equal(flyoutBounds.visibility, "visible", "Integration flyouts must not be hidden.");
      assert.ok(Number(flyoutBounds.opacity) > 0, "Integration flyouts must be visible when opened.");
      assert.ok(flyoutBounds.left >= 0, "Integration flyouts must not be clipped by the left viewport edge.");
      assert.ok(flyoutBounds.right <= flyoutBounds.viewport + 1, "Integration flyouts must fit within the viewport.");
      assert.equal(flyoutBounds.overflowX, "visible", "Nested flyouts must not create a horizontal scrollbar.");
      assert.match(flyoutBounds.labels, /Integrations/);
      await evaluate(`(() => {
        const crm = [...document.querySelectorAll(".v-flyout-group > .v-flyout-item")]
          .find(button => button.textContent.trim() === "CRM");
        crm?.click();
        return Boolean(crm);
      })()`);
      await wait(`Boolean([...document.querySelectorAll(".v-flyout-group")].find(group => group.querySelector(".v-flyout-submenu")) && [...document.querySelectorAll(".v-flyout-submenu")].some(menu => getComputedStyle(menu).display !== "none"))`);
      const submenuBounds = JSON.parse(await evaluate(`(() => {
        const submenu = [...document.querySelectorAll(".v-flyout-submenu")].find(menu => getComputedStyle(menu).display !== "none");
        const group = submenu?.closest(".v-flyout-group");
        const flyout = document.querySelector(".v-flyout");
        const rect = submenu?.getBoundingClientRect();
        const groupRect = group?.getBoundingClientRect();
        const flyoutRect = flyout?.getBoundingClientRect();
        return JSON.stringify({
          left: rect?.left ?? -1,
          right: rect?.right ?? Infinity,
          top: rect?.top ?? -1,
          groupTop: groupRect?.top ?? -1,
          flyoutRight: flyoutRect?.right ?? Infinity,
          text: submenu?.innerText ?? "",
          viewport: document.documentElement.clientWidth
        });
      })()`));
      assert.ok(submenuBounds.left >= 0, "Integration submenus must not be clipped by the left viewport edge.");
      assert.ok(submenuBounds.left >= submenuBounds.flyoutRight + 4, "Integration submenus must open beside, not over, the main flyout.");
      assert.ok(Math.abs(submenuBounds.top - submenuBounds.groupTop) <= 1, "Integration submenus must align with the selected category.");
      assert.ok(submenuBounds.right <= submenuBounds.viewport + 1, "Integration submenus must fit within the viewport.");
      assert.match(submenuBounds.text, /Salesforce/);
      assert.match(submenuBounds.text, /HubSpot/);
      assert.match(submenuBounds.text, /GoHighLevel/);
      await pause(300);
      await capture("integration-flyout-v5");
      await evaluate(`document.querySelector("#ui-smoke-emulate-hover-flyout")?.remove()`);
      await evaluate(`document.querySelector('.v-sidebar-nav a.v-navitem[aria-label="Integrations"]')?.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }))`);
      await cdp("Emulation.setDeviceMetricsOverride", { width: 1440, height: 1000, deviceScaleFactor: 1, mobile: false });
      await pause(250);

      await evaluate(`(() => {
        const fixture = document.createElement("button");
        fixture.type = "button";
        fixture.className = "gmail-mailbox-add";
        fixture.textContent = "Add account";
        fixture.style.position = "fixed";
        fixture.style.left = "-10000px";
        document.querySelector(".v-app")?.append(fixture);
        const style = getComputedStyle(fixture);
        window.__gmailButtonStyleSmoke = {
          color: style.color,
          background: style.backgroundColor
        };
        fixture.remove();
      })()`);
      const gmailButtonStyle = JSON.parse(await evaluate(`JSON.stringify(window.__gmailButtonStyleSmoke)`));
      assert.equal(gmailButtonStyle.color, "rgb(255, 255, 255)", "Add account text must remain white.");
      assert.equal(gmailButtonStyle.background, "rgb(30, 107, 82)", "Add account must use the Corral green fill.");

      const inlineCardOpened = await evaluate(`(() => {
        const card = [...document.querySelectorAll(".integration-card")].find(item => !item.classList.contains("provider-slack"));
        const toggle = card?.querySelector(".provider-collapse-toggle");
        toggle?.click();
        return Boolean(card && toggle);
      })()`);
      assert.ok(inlineCardOpened, "An inline integration card should be expandable.");
      await wait(`Boolean([...document.querySelectorAll(".integration-card .provider-details")].some(details => !details.hidden))`);
      const integrationMetrics = JSON.parse(await evaluate(`JSON.stringify((() => {
        const details = [...document.querySelectorAll(".integration-card .provider-details")].find(item => !item.hidden);
        const card = details?.closest(".integration-card");
        const grid = card?.closest(".integration-grid");
        const style = details ? getComputedStyle(details) : null;
        return {
          gridColumn: card ? getComputedStyle(card).gridColumn : "",
          cardWidth: card?.getBoundingClientRect().width ?? 0,
          detailsWidth: details?.getBoundingClientRect().width ?? 0,
          maxHeight: style?.maxHeight ?? "none",
          overflowY: style?.overflowY ?? "",
          gridWidth: grid?.getBoundingClientRect().width ?? 0
        };
      })())`));
      assert.equal(integrationMetrics.gridColumn, "auto", "Expanded integration cards must remain in one grid cell.");
      assert.notEqual(integrationMetrics.maxHeight, "none", "Integration details need a bounded height.");
      assert.equal(integrationMetrics.overflowY, "auto", "Integration details should scroll inside their card.");
      assert.ok(integrationMetrics.detailsWidth <= integrationMetrics.cardWidth + 1, "Expanded details must stay inside their card.");
      assert.ok(integrationMetrics.cardWidth <= integrationMetrics.gridWidth + 1, "Expanded cards must stay inside the integration grid.");
      await capture("integration-v5-expanded");
      await evaluate(`document.querySelector(".integration-card .provider-details:not([hidden])")?.closest(".integration-card")?.querySelector(".provider-collapse-toggle")?.click()`);
      requestedVisualChecks.integrationCards += 1;
      const opened = await evaluate(`(() => {
        const toggle = document.querySelector(".integration-card.provider-slack .provider-collapse-toggle");
        toggle?.click();
        return Boolean(toggle);
      })()`);
      assert.ok(opened, "The Slack routing settings should be reachable from Integrations.");
      await wait(`Boolean(document.querySelector(".slack-connection-dialog .slack-routing-actions > button"))`);
      await pause(250);
      const slackAction = JSON.parse(await evaluate(`(() => {
        const button = document.querySelector(".slack-connection-dialog .slack-routing-actions > button");
        const style = getComputedStyle(button);
        return JSON.stringify({
          visible: Boolean(button.getClientRects().length),
          label: button.innerText.trim(),
          color: style.color,
          background: style.backgroundColor,
          opacity: style.opacity
        });
      })()`));
      assert.equal(slackAction.visible, true);
      assert.equal(slackAction.label, "Save Slack routing");
      assert.equal(slackAction.color, "rgb(255, 255, 255)");
      assert.notEqual(slackAction.background, "rgba(0, 0, 0, 0)");
      assert.equal(slackAction.opacity, "1");
      await evaluate(`document.querySelector(".slack-connection-dialog .slack-routing-actions > button")?.scrollIntoView({ block: "center" })`);
      await pause(200);
      const slackActionBounds = JSON.parse(await evaluate(`(() => {
        const rect = document.querySelector(".slack-connection-dialog .slack-routing-actions > button").getBoundingClientRect();
        return JSON.stringify({ top: rect.top, bottom: rect.bottom, viewport: window.innerHeight });
      })()`));
      assert.ok(slackActionBounds.top >= 0 && slackActionBounds.bottom <= slackActionBounds.viewport, "Save Slack routing should be fully reachable by scrolling its dialog.");
      await capture("slack-routing");
      await evaluate(`document.querySelector(".integration-card.provider-slack .provider-collapse-toggle")?.click()`);
      await wait(`!document.querySelector(".slack-connection-dialog")`);
    }
    pageChecks.push([label, page.heading]);
  }
  await evaluate(`document.querySelector('button[aria-label="Hide sidebar"]')?.click()`);
  await wait(`document.querySelector(".v-app")?.dataset.sidebar === "hidden"`);
  await pause(350);
  const collapsedSidebar = JSON.parse(await evaluate(`JSON.stringify({
    sidebarWidth: document.querySelector(".v-sidebar")?.getBoundingClientRect().width ?? 0,
    viewport: document.documentElement.clientWidth,
    documentWidth: document.documentElement.scrollWidth,
    visibility: getComputedStyle(document.querySelector(".v-sidebar")).visibility,
    sidebarDisplay: getComputedStyle(document.querySelector(".v-sidebar")).display,
    appColumns: getComputedStyle(document.querySelector(".v-app")).gridTemplateColumns,
    labels: [...document.querySelectorAll(".v-navitem-label")].slice(0, 3).map(label => getComputedStyle(label).display)
  })`));
  assert.ok(collapsedSidebar.sidebarWidth <= 80 && collapsedSidebar.visibility === "visible");
  assert.ok(collapsedSidebar.documentWidth <= collapsedSidebar.viewport + 1, "Collapsed sidebar must not cause horizontal overflow.");
  await evaluate(`document.querySelector('button[aria-label="Show sidebar"]')?.click()`);
  await wait(`document.querySelector(".v-app")?.dataset.sidebar === "shown"`);
  await pause(350);
  console.log(`Checked ${pageChecks.length} main pages, ${primaryActionsChecked} visible primary actions, both Agent scopes, and the collapsed sidebar. No page-level horizontal overflow at desktop.`);
  console.log(`V5 targeted checks: ${JSON.stringify(requestedVisualChecks)}.`);

  await cdp("Emulation.setDeviceMetricsOverride", { width: 320, height: 667, deviceScaleFactor: 1, mobile: true });
  await pause(500);
  await evaluate(`document.querySelector("[data-ask-corral]")?.click()`);
  await wait(`Boolean(document.querySelector(".corral-agent-window.is-open"))`);
  await pause(350);
  const mobileLayouts = [
    { label: "portrait-320", width: 320, height: 667 },
    { label: "portrait-375", width: 375, height: 812 },
    { label: "portrait-430", width: 430, height: 932 },
    { label: "landscape-667", width: 667, height: 375 },
  ];
  const inspectWorkspaceLayout = async (theme, device) => {
    await cdp("Emulation.setDeviceMetricsOverride", {
      width: device.width, height: device.height, deviceScaleFactor: 1, mobile: true,
    });
    await pause(220);
    const layout = JSON.parse(await evaluate(`JSON.stringify((() => {
      const panel = document.querySelector(".corral-agent-window");
      const transcript = panel?.querySelector(".corral-workspace-transcript");
      const composer = panel?.querySelector(".corral-workspace-composer");
      const rect = element => {
        const box = element?.getBoundingClientRect();
        return box ? { top: box.top, right: box.right, bottom: box.bottom, width: box.width, height: box.height } : null;
      };
      const visible = element => {
        const style = getComputedStyle(element);
        return style.display !== "none" && style.visibility !== "hidden" && element.getClientRects().length > 0;
      };
      const scrollRegions = [...panel.querySelectorAll("*")].filter(element => {
        const style = getComputedStyle(element);
        return visible(element) && ["auto", "scroll"].includes(style.overflowY) &&
          !element.matches("textarea,input,select,[contenteditable=true]") &&
          !element.closest(".corral-workspace-composer,.lead-agent-composer");
      }).map(element => typeof element.className === "string" ? element.className : element.tagName.toLowerCase());
      const buttons = [...panel.querySelectorAll("button")].filter(visible).map(button => ({
        label: button.innerText.trim(),
        width: button.getBoundingClientRect().width,
        height: button.getBoundingClientRect().height,
        fontSize: parseFloat(getComputedStyle(button).fontSize)
      }));
      const text = [...panel.querySelectorAll(".corral-workspace-transcript p,.corral-workspace-transcript small,.corral-workspace-message,.corral-workspace-composer label,.corral-workspace-composer-footer small,.corral-workspace-composer textarea")].filter(visible).map(element => parseFloat(getComputedStyle(element).fontSize));
      return {
        panel: rect(panel), transcript: rect(transcript), composer: rect(composer),
        panelPaddingBottom: parseFloat(getComputedStyle(panel).paddingBottom),
        viewportWidth: window.innerWidth, viewportClientWidth: document.documentElement.clientWidth,
        viewportHeight: window.innerHeight,
        visualWidth: window.visualViewport?.width ?? window.innerWidth,
        visualHeight: window.visualViewport?.height ?? window.innerHeight,
        visualTop: window.visualViewport?.offsetTop ?? 0,
        documentWidth: document.documentElement.scrollWidth,
        pageScrollLocked: getComputedStyle(document.documentElement).overflow === "hidden" &&
          getComputedStyle(document.body).overflow === "hidden",
        transcriptWidth: transcript?.clientWidth ?? 0,
        transcriptScrollWidth: transcript?.scrollWidth ?? 0,
        composerWidth: composer?.clientWidth ?? 0,
        composerScrollWidth: composer?.scrollWidth ?? 0,
        scrollRegions, buttons, text,
        guide: rect([...panel.querySelectorAll("button")].find(button => /guide/i.test(button.innerText))),
        close: rect(panel.querySelector('[aria-label="Close Ask Corral"]')),
        scope: [...panel.querySelectorAll(".v-agent-scope button")].map(rect),
        send: rect(composer?.querySelector('button[type="submit"]'))
      };
    })())`));
    assert.ok(layout.panel && layout.composer && layout.transcript, `${device.label}/${theme}: workspace regions should render.`);
    assert.ok(layout.panel.width <= layout.visualWidth + 1, `${device.label}/${theme}: panel should fit the visual viewport.`);
    assert.ok(layout.panel.right <= layout.viewportClientWidth + 1, `${device.label}/${theme}: panel must not extend beyond the usable viewport width.`);
    assert.ok(layout.panel.top >= layout.visualTop - 1, `${device.label}/${theme}: panel should start within the visible viewport.`);
    assert.ok(layout.panel.bottom <= layout.visualTop + layout.visualHeight + 1, `${device.label}/${theme}: panel should fit above the keyboard/safe area.`);
    assert.ok(layout.composer.bottom <= layout.panel.bottom - layout.panelPaddingBottom + 1, `${device.label}/${theme}: composer should remain above the safe-area inset.`);
    assert.ok(layout.transcript.bottom <= layout.composer.top + 1, `${device.label}/${theme}: transcript should end above the fixed composer.`);
    assert.equal(layout.documentWidth <= layout.viewportClientWidth + 1, true, `${device.label}/${theme}: no page-level horizontal overflow.`);
    assert.equal(layout.pageScrollLocked, true, `${device.label}/${theme}: the page behind the full-screen panel must not scroll.`);
    assert.ok(layout.transcriptScrollWidth <= layout.transcriptWidth + 1, `${device.label}/${theme}: conversation should not scroll horizontally.`);
    assert.ok(layout.composerScrollWidth <= layout.composerWidth + 1, `${device.label}/${theme}: composer should not scroll horizontally.`);
    assert.deepEqual(layout.scrollRegions, ["corral-workspace-transcript"], `${device.label}/${theme}: use one conversation scroller.`);
    assert.ok(layout.close.width >= 44 && layout.close.height >= 44, `${device.label}/${theme}: close target must be at least 44px.`);
    assert.ok(layout.guide?.width >= 44 && layout.guide?.height >= 44, `${device.label}/${theme}: Guide target must be at least 44px.`);
    assert.equal(layout.scope.length, 2, `${device.label}/${theme}: both scope controls should be present.`);
    assert.ok(layout.scope.every(target => target.width >= 44 && target.height >= 44), `${device.label}/${theme}: scope targets must be at least 44px.`);
    assert.ok(layout.buttons.every(button => button.width >= 44 && button.height >= 44), `${device.label}/${theme}: visible buttons must be at least 44px.`);
    assert.ok(layout.buttons.every(button => button.fontSize >= 16), `${device.label}/${theme}: button text must be at least 16px.`);
    assert.ok(layout.text.every(size => size >= 16), `${device.label}/${theme}: body and composer text must be at least 16px.`);
    assert.ok(layout.send?.width >= 44 && layout.send?.height >= 44, `${device.label}/${theme}: Send target must be at least 44px.`);
    await verifyWorkspaceCards(theme);
    await verifyAgentCardFixture("workspace", theme, device);
    await capture(`agent-${device.label}-${theme}`);
  };

  for (const theme of ["light", "dark"]) {
    await evaluate(`document.documentElement.dataset.theme = ${JSON.stringify(theme)}`);
    await pause(120);
    for (const device of mobileLayouts) await inspectWorkspaceLayout(theme, device);
  }

  // A shorter visual viewport stands in for the space occupied by the on-screen keyboard.
  for (const theme of ["light", "dark"]) {
    await evaluate(`document.documentElement.dataset.theme = ${JSON.stringify(theme)}`);
    await cdp("Emulation.setDeviceMetricsOverride", { width: 375, height: 360, deviceScaleFactor: 1, mobile: true });
    await pause(220);
    await evaluate(`document.querySelector("#corral-workspace-prompt")?.focus()`);
    await pause(180);
    const keyboardLayout = JSON.parse(await evaluate(`JSON.stringify((() => {
      const panel = document.querySelector(".corral-agent-window").getBoundingClientRect();
      const composer = document.querySelector(".corral-workspace-composer").getBoundingClientRect();
      const visual = window.visualViewport;
      return {
        panelBottom: panel.bottom,
        safeBottom: parseFloat(getComputedStyle(document.querySelector(".corral-agent-window")).paddingBottom),
        composerBottom: composer.bottom,
        visualBottom: (visual?.offsetTop ?? 0) + (visual?.height ?? window.innerHeight),
        inputFontSize: parseFloat(getComputedStyle(document.querySelector("#corral-workspace-prompt")).fontSize)
      };
    })())`));
    assert.ok(keyboardLayout.composerBottom <= keyboardLayout.panelBottom - keyboardLayout.safeBottom + 1, `${theme}: keyboard-height composer must fit above the safe area.`);
    assert.ok(keyboardLayout.composerBottom <= keyboardLayout.visualBottom + 1, `${theme}: keyboard-height composer must remain visible.`);
    assert.ok(keyboardLayout.inputFontSize >= 16, `${theme}: mobile input text must avoid iOS auto-zoom.`);
  }

  await cdp("Emulation.setDeviceMetricsOverride", { width: 320, height: 667, deviceScaleFactor: 1, mobile: true });
  await evaluate(`document.documentElement.dataset.theme = "dark"`);
  await evaluate(`document.querySelector(".v-agent-scope button[aria-pressed=\\"false\\"]")?.click()`);
  await pause(250);
  if (!(await evaluate(`Boolean(document.querySelector(".corral-agent-window .lead-agent")?.getClientRects().length)`))) {
    await wait(`Boolean(document.querySelector(".corral-agent-window .corral-agent-lead-option")?.getClientRects().length)`);
    await evaluate(`document.querySelector(".corral-agent-window .corral-agent-lead-option")?.click()`);
    await wait(`Boolean(document.querySelector(".corral-agent-window .lead-agent")?.getClientRects().length)`);
  }
  for (const theme of ["light", "dark"]) {
    await evaluate(`document.documentElement.dataset.theme = ${JSON.stringify(theme)}`);
    for (const device of mobileLayouts) {
      await cdp("Emulation.setDeviceMetricsOverride", {
        width: device.width, height: device.height, deviceScaleFactor: 1, mobile: true,
      });
      await pause(220);
      const leadLayout = JSON.parse(await evaluate(`JSON.stringify((() => {
        const panel = document.querySelector(".corral-agent-window");
        const transcript = panel.querySelector(".lead-agent-transcript");
        const composer = panel.querySelector(".lead-agent-composer");
        const rect = element => {
          const box = element?.getBoundingClientRect();
          return box ? { top: box.top, right: box.right, bottom: box.bottom, width: box.width, height: box.height } : null;
        };
        const visible = element => {
          const style = getComputedStyle(element);
          return style.display !== "none" && style.visibility !== "hidden" && element.getClientRects().length > 0;
        };
        const scrollRegions = [...panel.querySelectorAll("*")].filter(element => {
          const style = getComputedStyle(element);
          return visible(element) && ["auto", "scroll"].includes(style.overflowY) &&
            !element.matches("textarea,input,select,[contenteditable=true]") &&
            !element.closest(".lead-agent-composer,.corral-workspace-composer");
        }).map(element => typeof element.className === "string" ? element.className : element.tagName.toLowerCase());
        const text = [...panel.querySelectorAll(".lead-agent p,.lead-agent small,.lead-agent label,.lead-agent span,.lead-agent strong,.lead-agent b,.lead-agent dt,.lead-agent dd,.lead-agent summary,.lead-agent time,.lead-agent a")].filter(visible).map(element => parseFloat(getComputedStyle(element).fontSize));
        const buttons = [...panel.querySelectorAll("button")].filter(visible).map(button => ({
          width: button.getBoundingClientRect().width,
          height: button.getBoundingClientRect().height,
          fontSize: parseFloat(getComputedStyle(button).fontSize)
        }));
        const visual = window.visualViewport;
        return {
          panel: rect(panel), transcript: rect(transcript), composer: rect(composer),
          lead: rect(panel.querySelector(".lead-agent")),
          input: rect(composer.querySelector("textarea")),
          send: rect(composer.querySelector('button[type="submit"]')),
          panelPaddingBottom: parseFloat(getComputedStyle(panel).paddingBottom),
          visualBottom: (visual?.offsetTop ?? 0) + (visual?.height ?? window.innerHeight),
          visualWidth: visual?.width ?? window.innerWidth,
          viewportWidth: window.innerWidth,
          viewportClientWidth: document.documentElement.clientWidth,
          pageScrollLocked: getComputedStyle(document.documentElement).overflow === "hidden" &&
            getComputedStyle(document.body).overflow === "hidden",
          scrollRegions, text, buttons,
          leadClientWidth: panel.querySelector(".lead-agent").clientWidth,
          leadScrollWidth: panel.querySelector(".lead-agent").scrollWidth,
          transcriptWidth: transcript.clientWidth,
          transcriptScrollWidth: transcript.scrollWidth
        };
      })())`));
      assert.ok(leadLayout.panel && leadLayout.transcript && leadLayout.composer && leadLayout.input, `${device.label}/${theme}: Lead regions should render.`);
      assert.ok(leadLayout.composer.bottom <= leadLayout.panel.bottom - leadLayout.panelPaddingBottom + 1, `${device.label}/${theme}: Lead composer must stay above the safe area.`);
      assert.ok(leadLayout.composer.bottom <= leadLayout.visualBottom + 1, `${device.label}/${theme}: Lead composer must remain visible.`);
      assert.ok(leadLayout.transcript.bottom <= leadLayout.composer.top + 1, `${device.label}/${theme}: Lead transcript must end above the composer.`);
      assert.ok(leadLayout.panel.right <= leadLayout.visualWidth + 1, `${device.label}/${theme}: Lead panel must fit the visual viewport.`);
      assert.equal(leadLayout.pageScrollLocked, true, `${device.label}/${theme}: the page behind the Lead panel must not scroll.`);
      assert.ok(leadLayout.leadScrollWidth <= leadLayout.leadClientWidth + 1, `${device.label}/${theme}: Lead content must not overflow horizontally.`);
      assert.ok(leadLayout.transcriptScrollWidth <= leadLayout.transcriptWidth + 1, `${device.label}/${theme}: Lead transcript must not scroll horizontally.`);
      assert.deepEqual(leadLayout.scrollRegions, ["lead-agent-transcript"], `${device.label}/${theme}: Lead view must have one content scroller.`);
      assert.ok(leadLayout.input.height >= 44 && leadLayout.send?.height >= 44, `${device.label}/${theme}: Lead composer controls must be at least 44px.`);
      assert.ok(leadLayout.buttons.every(button => button.width >= 43.5 && button.height >= 43.5), `${device.label}/${theme}: Lead buttons must meet 44px touch-target size within subpixel rounding. ${JSON.stringify(leadLayout.buttons.filter(button => button.width < 43.5 || button.height < 43.5))}`);
      assert.ok(leadLayout.buttons.every(button => button.fontSize >= 16), `${device.label}/${theme}: Lead button labels must be at least 16px.`);
      assert.ok(leadLayout.text.every(size => size >= 16), `${device.label}/${theme}: Lead body text must be at least 16px.`);
      await verifyAgentCardFixture("lead", theme, device);
      if ((device.label === "portrait-320" && theme === "dark") || (device.label === "landscape-667" && theme === "light")) {
        await capture(`agent-lead-${device.label}-${theme}`);
      }
    }
  }
  for (const theme of ["light", "dark"]) {
    await evaluate(`document.documentElement.dataset.theme = ${JSON.stringify(theme)}`);
    await cdp("Emulation.setDeviceMetricsOverride", { width: 375, height: 360, deviceScaleFactor: 1, mobile: true });
    await pause(220);
    await evaluate(`document.querySelector(".corral-agent-window .lead-agent-composer textarea")?.focus()`);
    await pause(180);
    const leadKeyboardLayout = JSON.parse(await evaluate(`JSON.stringify((() => {
      const panelElement = document.querySelector(".corral-agent-window");
      const panel = panelElement.getBoundingClientRect();
      const lead = panelElement.querySelector(".lead-agent");
      const mode = lead?.closest(".corral-agent-mode");
      const composerElement = panelElement.querySelector(".lead-agent-composer");
      const composer = composerElement.getBoundingClientRect();
      const input = composerElement.querySelector("textarea");
      const visual = window.visualViewport;
      return {
        panelBottom: panel.bottom,
        composerBottom: composer.bottom,
        composerHeight: composer.height,
        composerDisplay: getComputedStyle(composerElement).display,
        leadDisplay: lead ? getComputedStyle(lead).display : "missing",
        leadModeHidden: mode?.hidden ?? null,
        activeScope: [...panelElement.querySelectorAll(".v-agent-scope button")].map(button => [button.textContent.trim(), button.getAttribute("aria-pressed")]),
        inputRect: input ? { top: input.getBoundingClientRect().top, bottom: input.getBoundingClientRect().bottom, height: input.getBoundingClientRect().height } : null,
        visualBottom: (visual?.offsetTop ?? 0) + (visual?.height ?? window.innerHeight),
        safeBottom: parseFloat(getComputedStyle(panelElement).paddingBottom),
        inputFontSize: parseFloat(getComputedStyle(input).fontSize),
        viewport: [window.innerWidth, window.innerHeight, visual?.width, visual?.height],
        pageScrollLocked: getComputedStyle(document.documentElement).overflow === "hidden" &&
          getComputedStyle(document.body).overflow === "hidden"
      };
    })())`));
    assert.ok(leadKeyboardLayout.composerBottom <= leadKeyboardLayout.panelBottom - leadKeyboardLayout.safeBottom + 1, `${theme}: keyboard-height Lead composer must fit above the safe area. ${JSON.stringify(leadKeyboardLayout)}`);
    assert.ok(leadKeyboardLayout.composerBottom <= leadKeyboardLayout.visualBottom + 1, `${theme}: keyboard-height Lead composer must remain visible.`);
    assert.ok(leadKeyboardLayout.inputFontSize >= 16, `${theme}: Lead input text must avoid iOS auto-zoom. ${JSON.stringify(leadKeyboardLayout)}`);
    assert.equal(leadKeyboardLayout.pageScrollLocked, true, `${theme}: keyboard-height Lead view must keep background scrolling locked.`);
  }
  await evaluate(`(() => {
    for (const root of Object.values(window.__corralAgentSmokeRoots ?? {})) root.unmount();
    document.querySelectorAll("[data-agent-smoke-fixture]").forEach(fixture => fixture.remove());
  })()`);
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
  await pause(450);
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
