import { test, expect } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

function getMockPageHtml() {
  return `
    <!DOCTYPE html>
    <html>
    <head>
      <link rel="stylesheet" href="http://localhost:9999/plugins/empornium-megapack/style.css">
    </head>
    <body>
      <div class="btn-toolbar">
        <button class="btn btn-primary">Other Action</button>
      </div>
      <div class="scenes-list">
        <div class="scene-card" data-scene-id="101">
          <input type="checkbox" checked />
          <span>Scene 101</span>
        </div>
        <div class="scene-card" data-scene-id="102">
          <input type="checkbox" checked />
          <span>Scene 102</span>
        </div>
      </div>
      <script src="http://localhost:9999/plugins/empornium-megapack/main.js"></script>
    </body>
    </html>
  `;
}

test.describe("Stale-Asset Cache Busting & Versioned Lifecycle", () => {
  test("1. Modal open with a CHANGED buildStamp forces a re-fetch of review.js even though window.initEmporniumReview is already defined", async ({ page }) => {
    let currentStamp = "1.0.0-commitA";
    const jsRequests = [];
    const htmlRequests = [];

    await page.route("**/plugin/*/assets/version.json*", async (route) => {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ buildStamp: currentStamp }),
      });
    });

    await page.route("**/plugin*/**/main.js*", async (route) => {
      const filePath = path.resolve("plugin/main.js");
      return route.fulfill({
        status: 200,
        contentType: "application/javascript",
        body: fs.readFileSync(filePath, "utf8"),
      });
    });

    await page.route("**/plugin*/**/style.css*", async (route) => {
      const filePath = path.resolve("plugin/style.css");
      return route.fulfill({
        status: 200,
        contentType: "text/css",
        body: fs.readFileSync(filePath, "utf8"),
      });
    });

    await page.route("**/plugin*/**/review.html*", async (route) => {
      htmlRequests.push(route.request().url());
      const filePath = path.resolve("plugin/assets/review.html");
      return route.fulfill({
        status: 200,
        contentType: "text/html",
        body: fs.readFileSync(filePath, "utf8"),
      });
    });

    await page.route("**/*review.js*", async (route) => {
      jsRequests.push(route.request().url());
      const filePath = path.resolve("plugin/assets/review.js");
      return route.fulfill({
        status: 200,
        contentType: "application/javascript",
        body: fs.readFileSync(filePath, "utf8"),
      });
    });

    await page.setContent(getMockPageHtml());

    // 1. Initial modal open with commitA
    const triggerBtn = page.locator("#empornium-megapack-btn");
    await expect(triggerBtn).toBeVisible();
    await triggerBtn.click();

    const modal = page.locator("#empornium-megapack-modal");
    await expect(modal).toBeVisible();
    await expect(page.locator("#scene-list")).toBeVisible();

    expect(jsRequests.length).toBe(1);
    expect(jsRequests[0]).toContain("?v=1.0.0-commitA");
    expect(htmlRequests.length).toBe(1);
    expect(htmlRequests[0]).toContain("?v=1.0.0-commitA");

    const assetVersionA = await page.evaluate(() => window.__emporniumAssetVersion);
    expect(assetVersionA).toBe("1.0.0-commitA");

    // Close the modal
    const closeBtn = modal.locator(".empornium-modal-close");
    await closeBtn.click();
    await expect(modal).toHaveCount(0);

    // Verify window.initEmporniumReview is already defined in the page context
    const hasInitFn = await page.evaluate(() => typeof window.initEmporniumReview === "function");
    expect(hasInitFn).toBe(true);

    // 2. Change build stamp to simulate a new deployment
    currentStamp = "1.0.0-commitB";

    // Reopen modal -> must re-fetch review.js with commitB even though window.initEmporniumReview was present
    await triggerBtn.click();
    await expect(page.locator("#empornium-megapack-modal")).toBeVisible();
    await expect(page.locator("#scene-list")).toBeVisible();

    expect(jsRequests.length).toBe(2);
    expect(jsRequests[1]).toContain("?v=1.0.0-commitB");
    expect(htmlRequests.length).toBe(2);
    expect(htmlRequests[1]).toContain("?v=1.0.0-commitB");

    const assetVersionB = await page.evaluate(() => window.__emporniumAssetVersion);
    expect(assetVersionB).toBe("1.0.0-commitB");
  });

  test("2. Modal open with an UNCHANGED buildStamp does NOT re-fetch review.js", async ({ page }) => {
    const fixedStamp = "1.0.0-staticStamp";
    const jsRequests = [];

    await page.route("**/plugin/*/assets/version.json*", async (route) => {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ buildStamp: fixedStamp }),
      });
    });

    await page.route("**/plugin*/**/main.js*", async (route) => {
      const filePath = path.resolve("plugin/main.js");
      return route.fulfill({
        status: 200,
        contentType: "application/javascript",
        body: fs.readFileSync(filePath, "utf8"),
      });
    });

    await page.route("**/plugin*/**/style.css*", async (route) => {
      const filePath = path.resolve("plugin/style.css");
      return route.fulfill({
        status: 200,
        contentType: "text/css",
        body: fs.readFileSync(filePath, "utf8"),
      });
    });

    await page.route("**/plugin*/**/review.html*", async (route) => {
      const filePath = path.resolve("plugin/assets/review.html");
      return route.fulfill({
        status: 200,
        contentType: "text/html",
        body: fs.readFileSync(filePath, "utf8"),
      });
    });

    await page.route("**/*review.js*", async (route) => {
      jsRequests.push(route.request().url());
      const filePath = path.resolve("plugin/assets/review.js");
      return route.fulfill({
        status: 200,
        contentType: "application/javascript",
        body: fs.readFileSync(filePath, "utf8"),
      });
    });

    await page.setContent(getMockPageHtml());

    const triggerBtn = page.locator("#empornium-megapack-btn");
    await expect(triggerBtn).toBeVisible();

    // First open
    await triggerBtn.click();
    const modal = page.locator("#empornium-megapack-modal");
    await expect(modal).toBeVisible();
    await expect(page.locator("#scene-list")).toBeVisible();
    expect(jsRequests.length).toBe(1);

    // Close modal
    await modal.locator(".empornium-modal-close").click();
    await expect(modal).toHaveCount(0);

    // Second open with unchanged buildStamp
    await triggerBtn.click();
    await expect(page.locator("#empornium-megapack-modal")).toBeVisible();
    await expect(page.locator("#scene-list")).toBeVisible();

    // Must NOT re-fetch review.js
    expect(jsRequests.length).toBe(1);
  });

  test("3. version.json unreachable falls back to old reuse-if-present behavior and modal still opens", async ({ page }) => {
    const jsRequests = [];
    const htmlRequests = [];

    await page.route("**/plugin/*/assets/version.json*", async (route) => {
      return route.fulfill({
        status: 404,
        contentType: "application/json",
        body: JSON.stringify({ error: "Not Found" }),
      });
    });

    await page.route("**/plugin*/**/main.js*", async (route) => {
      const filePath = path.resolve("plugin/main.js");
      return route.fulfill({
        status: 200,
        contentType: "application/javascript",
        body: fs.readFileSync(filePath, "utf8"),
      });
    });

    await page.route("**/plugin*/**/style.css*", async (route) => {
      const filePath = path.resolve("plugin/style.css");
      return route.fulfill({
        status: 200,
        contentType: "text/css",
        body: fs.readFileSync(filePath, "utf8"),
      });
    });

    await page.route("**/plugin*/**/review.html*", async (route) => {
      htmlRequests.push(route.request().url());
      const filePath = path.resolve("plugin/assets/review.html");
      return route.fulfill({
        status: 200,
        contentType: "text/html",
        body: fs.readFileSync(filePath, "utf8"),
      });
    });

    await page.route("**/*review.js*", async (route) => {
      jsRequests.push(route.request().url());
      const filePath = path.resolve("plugin/assets/review.js");
      return route.fulfill({
        status: 200,
        contentType: "application/javascript",
        body: fs.readFileSync(filePath, "utf8"),
      });
    });

    await page.setContent(getMockPageHtml());

    const triggerBtn = page.locator("#empornium-megapack-btn");
    await expect(triggerBtn).toBeVisible();

    // 1. First open: version.json is 404, modal must still open and fetch unversioned URLs
    await triggerBtn.click();
    const modal = page.locator("#empornium-megapack-modal");
    await expect(modal).toBeVisible();
    await expect(page.locator("#scene-list")).toBeVisible();

    expect(jsRequests.length).toBe(1);
    expect(jsRequests[0]).not.toContain("?v=");
    expect(htmlRequests.length).toBe(1);
    expect(htmlRequests[0]).not.toContain("?v=");

    // Close modal
    await modal.locator(".empornium-modal-close").click();
    await expect(modal).toHaveCount(0);

    // 2. Second open: version.json still unreachable, reuses existing initEmporniumReview
    await triggerBtn.click();
    await expect(page.locator("#empornium-megapack-modal")).toBeVisible();
    await expect(page.locator("#scene-list")).toBeVisible();

    // review.js was reused; no second fetch occurred
    expect(jsRequests.length).toBe(1);
  });

  test("4. window.__emporniumTeardown is invoked before a re-injection", async ({ page }) => {
    let currentStamp = "1.0.0-release1";

    await page.route("**/plugin/*/assets/version.json*", async (route) => {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ buildStamp: currentStamp }),
      });
    });

    await page.route("**/plugin*/**/main.js*", async (route) => {
      const filePath = path.resolve("plugin/main.js");
      return route.fulfill({
        status: 200,
        contentType: "application/javascript",
        body: fs.readFileSync(filePath, "utf8"),
      });
    });

    await page.route("**/plugin*/**/style.css*", async (route) => {
      const filePath = path.resolve("plugin/style.css");
      return route.fulfill({
        status: 200,
        contentType: "text/css",
        body: fs.readFileSync(filePath, "utf8"),
      });
    });

    await page.route("**/plugin*/**/review.html*", async (route) => {
      const filePath = path.resolve("plugin/assets/review.html");
      return route.fulfill({
        status: 200,
        contentType: "text/html",
        body: fs.readFileSync(filePath, "utf8"),
      });
    });

    await page.route("**/*review.js*", async (route) => {
      const filePath = path.resolve("plugin/assets/review.js");
      return route.fulfill({
        status: 200,
        contentType: "application/javascript",
        body: fs.readFileSync(filePath, "utf8"),
      });
    });

    await page.setContent(getMockPageHtml());

    const triggerBtn = page.locator("#empornium-megapack-btn");
    await expect(triggerBtn).toBeVisible();

    // First open
    await triggerBtn.click();
    const modal = page.locator("#empornium-megapack-modal");
    await expect(modal).toBeVisible();
    await expect(page.locator("#scene-list")).toBeVisible();

    // Verify teardown function is registered
    const teardownExists = await page.evaluate(() => typeof window.__emporniumTeardown === "function");
    expect(teardownExists).toBe(true);

    // Spy on teardown and record when it's called
    await page.evaluate(() => {
      window.__teardownCallCount = 0;
      const originalTeardown = window.__emporniumTeardown;
      window.__emporniumTeardown = function () {
        window.__teardownCallCount++;
        window.__teardownCalledAt = Date.now();
        return originalTeardown ? originalTeardown() : undefined;
      };
    });

    // Close modal
    await modal.locator(".empornium-modal-close").click();
    await expect(modal).toHaveCount(0);

    // Update build stamp to trigger re-injection
    currentStamp = "1.0.0-release2";

    // Reopen modal
    await triggerBtn.click();
    await expect(page.locator("#empornium-megapack-modal")).toBeVisible();
    await expect(page.locator("#scene-list")).toBeVisible();

    // Assert that __emporniumTeardown was invoked
    const callCount = await page.evaluate(() => window.__teardownCallCount);
    expect(callCount).toBe(1);
  });

  test("5. window.__emporniumTeardown defensive execution does not throw", async ({ page }) => {
    await page.route("**/plugin/*/assets/version.json*", async (route) => {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ buildStamp: "1.0.0" }),
      });
    });

    await page.route("**/plugin*/**/main.js*", async (route) => {
      const filePath = path.resolve("plugin/main.js");
      return route.fulfill({
        status: 200,
        contentType: "application/javascript",
        body: fs.readFileSync(filePath, "utf8"),
      });
    });

    await page.route("**/plugin*/**/style.css*", async (route) => {
      const filePath = path.resolve("plugin/style.css");
      return route.fulfill({
        status: 200,
        contentType: "text/css",
        body: fs.readFileSync(filePath, "utf8"),
      });
    });

    await page.route("**/plugin*/**/review.html*", async (route) => {
      const filePath = path.resolve("plugin/assets/review.html");
      return route.fulfill({
        status: 200,
        contentType: "text/html",
        body: fs.readFileSync(filePath, "utf8"),
      });
    });

    await page.route("**/*review.js*", async (route) => {
      const filePath = path.resolve("plugin/assets/review.js");
      return route.fulfill({
        status: 200,
        contentType: "application/javascript",
        body: fs.readFileSync(filePath, "utf8"),
      });
    });

    await page.setContent(getMockPageHtml());

    const triggerBtn = page.locator("#empornium-megapack-btn");
    await triggerBtn.click();
    await expect(page.locator("#empornium-megapack-modal")).toBeVisible();
    await expect(page.locator("#scene-list")).toBeVisible();

    // Calling __emporniumTeardown multiple times with mock socket / timers should not throw
    const noThrow = await page.evaluate(() => {
      try {
        window.__emporniumTeardown();
        window.__emporniumTeardown();
        return true;
      } catch (e) {
        return false;
      }
    });

    expect(noThrow).toBe(true);
  });
});
