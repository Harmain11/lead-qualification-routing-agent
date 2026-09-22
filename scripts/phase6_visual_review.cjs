const { chromium } = require("playwright-core");

(async () => {
  const browser = await chromium.launch({ headless: true });
  const errors = [];
  const responses = [];
  const views = {};
  const inspect = async (page, name) => {
    page.on("console", (message) => {
      if (message.type() === "error") errors.push(`${name} console: ${message.text()}`);
    });
    page.on("pageerror", (error) => errors.push(`${name} page: ${error.message}`));
    page.on("requestfailed", (request) => {
      errors.push(`${name} request: ${request.url()} ${request.failure()?.errorText}`);
    });
    page.on("response", (response) => {
      if (response.url().includes("/v1/")) {
        responses.push(`${name} ${response.status()} ${response.url()}`);
      }
    });
    await page.goto("http://host.docker.internal:5173", { waitUntil: "networkidle" });
    await page.waitForFunction(() =>
      document.querySelector(".dashboard-view")
      || Array.from(document.querySelectorAll("button")).some((button) =>
        button.textContent?.includes("Continue to seeded demo workspace"),
      ),
    );
    const demoSession = page.getByRole("button", { name: "Continue to seeded demo workspace" });
    if (await demoSession.count()) {
      if (name === "desktop") {
        await page.screenshot({ path: "/tmp/ui-run/phase65-auth-desktop.png", fullPage: true });
      }
      await demoSession.click();
      await page.getByRole("heading", { name: /Revenue intelligence/i }).waitFor();
    }
    await page.waitForTimeout(8000);
    const geometry = await page.evaluate(() => ({
      viewport: document.documentElement.clientWidth,
      documentWidth: document.documentElement.scrollWidth,
      bodyWidth: document.body.scrollWidth,
      h1: document.querySelector("h1")?.textContent,
      metricCards: document.querySelectorAll(".metric-card").length,
      networkControls: document.querySelectorAll(".network-accessible button").length,
      networkLabels: document.querySelectorAll(".network-node-labels button").length,
      activityEvents: document.querySelectorAll(".activity-compact details").length,
      canvasCount: document.querySelectorAll("canvas").length,
      bodyText: document.body.innerText.slice(0, 500),
    }));
    return geometry;
  };

  const desktop = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  const desktopGeometry = await inspect(desktop, "desktop");
  await desktop.screenshot({ path: "/tmp/ui-run/phase6-dashboard-desktop.png", fullPage: true });
  for (const viewName of [
    "Leads",
    "Pipeline",
    "Agents / AI Activity",
    "Analytics",
    "Manual Review",
    "Integrations",
    "Settings",
  ]) {
    await desktop.getByRole("button").filter({ hasText: viewName }).first().click();
    await desktop.waitForTimeout(700);
    views[viewName] = await desktop.locator("main h1").first().textContent();
    if (viewName === "Leads" && await desktop.locator(".lead-table tbody tr").count()) {
      await desktop.locator(".lead-table tbody tr").first().click();
      await desktop.locator(".lead-story").waitFor();
      views["Lead detail"] = await desktop.locator(".lead-story h2").first().textContent();
    }
    if (viewName === "Analytics") {
      await desktop.screenshot({ path: "/tmp/ui-run/phase6-analytics-desktop.png", fullPage: true });
    }
  }

  const mobile = await browser.newPage({ viewport: { width: 390, height: 844 }, isMobile: true });
  const mobileGeometry = await inspect(mobile, "mobile");
  await mobile.screenshot({ path: "/tmp/ui-run/phase6-dashboard-mobile.png", fullPage: true });

  const laptop = await browser.newPage({ viewport: { width: 1180, height: 820 } });
  const laptopGeometry = await inspect(laptop, "laptop");
  await laptop.screenshot({ path: "/tmp/ui-run/phase65-dashboard-laptop.png", fullPage: true });

  const tablet = await browser.newPage({ viewport: { width: 820, height: 1000 }, isMobile: true });
  const tabletGeometry = await inspect(tablet, "tablet");
  await tablet.screenshot({ path: "/tmp/ui-run/phase65-dashboard-tablet.png", fullPage: true });

  console.log(JSON.stringify({ desktopGeometry, laptopGeometry, tabletGeometry, mobileGeometry, views, responses, errors }));
  await browser.close();
  if (errors.length) process.exitCode = 1;
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
