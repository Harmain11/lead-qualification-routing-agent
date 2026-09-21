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
    await page.waitForTimeout(8000);
    const geometry = await page.evaluate(() => ({
      viewport: document.documentElement.clientWidth,
      documentWidth: document.documentElement.scrollWidth,
      bodyWidth: document.body.scrollWidth,
      h1: document.querySelector("h1")?.textContent,
      metricCards: document.querySelectorAll(".metric-card").length,
      flowNodes: document.querySelectorAll(".flow-node").length,
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

  console.log(JSON.stringify({ desktopGeometry, mobileGeometry, views, responses, errors }));
  await browser.close();
  if (errors.length) process.exitCode = 1;
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
