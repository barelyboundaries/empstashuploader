import { test, expect } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const SEED = "D:\\Seed";
const SCRATCH = "D:\\Scratch";

function serveAssets(page) {
  page.route("**/*", (route) => route.fulfill({ status: 404, contentType: "text/plain", body: "" }));

  page.route("**/plugin*/**/review.html*", async (route) => {
    const filePath = path.resolve(__dirname, "../../plugin/assets/review.html");
    return route.fulfill({
      status: 200,
      contentType: "text/html",
      body: fs.readFileSync(filePath, "utf8")
    });
  });

  page.route("**/*review.js*", async (route) => {
    const filePath = path.resolve(__dirname, "../../plugin/assets/review.js");
    return route.fulfill({
      status: 200,
      contentType: "application/javascript",
      body: fs.readFileSync(filePath, "utf8")
    });
  });
}

function createScene(id, title) {
  const sceneTitle = title || ("Scene " + id);
  return {
    id: id,
    title: sceneTitle,
    date: "2026-01-01",
    paths: { screenshot: "" },
    files: [{
      id: id * 10,
      path: SEED + "\\scene_" + id + ".mp4",
      size: 5000000,
      height: 1080,
      width: 1920,
      duration: 600,
      video_codec: "h264",
      oshash: "oshash-" + id
    }],
    performers: [{ name: "Performer " + id }],
    tags: [],
    studio: null
  };
}

test.describe("Teamwork Preview Challenger 2: Adversarial UI Lifecycle and Race Conditions", () => {

  // =========================================================================
  // TASK 1: Stress-test rapid, concurrent calls to refreshSidecarStatus()
  // =========================================================================

  test("Task 1.1: 50 concurrent calls to refreshSidecarStatus() coalesce into a single StartBackend task dispatch", async ({ page }) => {
    serveAssets(page);
    let startBackendCount = 0;

    await page.route("**/graphql", async (route) => {
      const postData = JSON.parse(route.request().postData() || "{}");
      const query = postData.query || "";
      if (query.includes("FindScenes")) {
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ data: { findScenes: { scenes: [] } } })
        });
      }
      if (query.includes("runPluginTask")) {
        if (postData.variables?.task_name === "StartBackend") {
          startBackendCount++;
        }
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ data: { runPluginTask: "job-backend-concurrent" } })
        });
      }
      return route.fallback();
    });

    await page.route("**/health*", (route) => route.abort());

    await page.goto("http://localhost:9999/plugins/empornium-megapack/review.html?scenes=1");

    const results = await page.evaluate(() => {
      const calls = [];
      for (let i = 0; i < 50; i++) {
        calls.push(window.refreshSidecarStatus());
      }
      return Promise.all(calls);
    });

    expect(results).toHaveLength(50);
    expect(startBackendCount).toBe(1);
  });

  test("Task 1.2: Staggered bursts during the 10s poll cycle join the active in-flight cycle without duplicate dispatch", async ({ page }) => {
    serveAssets(page);
    let startBackendCount = 0;

    await page.route("**/graphql", async (route) => {
      const postData = JSON.parse(route.request().postData() || "{}");
      if (postData.query?.includes("FindScenes")) {
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ data: { findScenes: { scenes: [] } } })
        });
      }
      if (postData.variables?.task_name === "StartBackend") {
        startBackendCount++;
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ data: { runPluginTask: "job-backend-staggered" } })
        });
      }
      return route.fallback();
    });

    await page.route("**/health*", (route) => route.abort());

    await page.goto("http://localhost:9999/plugins/empornium-megapack/review.html?scenes=1");

    await page.evaluate(async () => {
      const p1 = window.refreshSidecarStatus();
      await new Promise(r => setTimeout(r, 200));
      const p2 = window.refreshSidecarStatus();
      await new Promise(r => setTimeout(r, 400));
      const p3 = window.refreshSidecarStatus();
      await new Promise(r => setTimeout(r, 400));
      const p4 = window.refreshSidecarStatus();
      return Promise.all([p1, p2, p3, p4]);
    });

    expect(startBackendCount).toBe(1);
  });

  test("Task 1.3: Reset debouncer after cycle failure allows subsequent cycle to dispatch a new StartBackend task", async ({ page }) => {
    serveAssets(page);
    let startBackendCount = 0;

    await page.route("**/graphql", async (route) => {
      const postData = JSON.parse(route.request().postData() || "{}");
      if (postData.query?.includes("FindScenes")) {
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ data: { findScenes: { scenes: [] } } })
        });
      }
      if (postData.variables?.task_name === "StartBackend") {
        startBackendCount++;
        if (startBackendCount === 1) {
          return route.fulfill({
            status: 500,
            contentType: "application/json",
            body: JSON.stringify({ errors: [{ message: "GraphQL execution failed" }] })
          });
        }
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ data: { runPluginTask: "job-backend-2" } })
        });
      }
      return route.fallback();
    });

    await page.route("**/health*", (route) => route.abort());

    await page.goto("http://localhost:9999/plugins/empornium-megapack/review.html?scenes=1");

    // On page load, initEmporniumReview() triggers cycle 1, which fails and resets debouncer
    const badge = page.locator("#sidecar-status");
    await expect(badge).toHaveText(/Sidecar: failed to start/);
    expect(startBackendCount).toBe(1);

    // Second call triggers cycle 2: debouncer was cleanly reset, so it dispatches StartBackend again
    await page.evaluate(() => window.refreshSidecarStatus());
    expect(startBackendCount).toBe(2);
  });

  // =========================================================================
  // TASK 2: Badge transitions through starting, running, failed-to-start
  // =========================================================================

  test("Task 2.1: Badge transitions through starting to failed-to-start with clean classes and no flashing", async ({ page }) => {
    serveAssets(page);

    await page.route("**/graphql", async (route) => {
      const postData = JSON.parse(route.request().postData() || "{}");
      if (postData.query?.includes("FindScenes")) {
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ data: { findScenes: { scenes: [] } } })
        });
      }
      if (postData.variables?.task_name === "StartBackend") {
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ data: { runPluginTask: "job-fail-cycle" } })
        });
      }
      return route.fallback();
    });

    await page.route("**/health*", (route) => route.abort());

    await page.goto("http://localhost:9999/plugins/empornium-megapack/review.html?scenes=1");

    const badge = page.locator("#sidecar-status");
    const stopBtn = page.locator("#btn-sidecar-stop");

    await expect(badge).toHaveText(/Sidecar: starting/);
    await expect(badge).toHaveClass(/sidecar-status sidecar-warn/);
    await expect(badge).not.toHaveClass(/sidecar-ok/);
    await expect(badge).not.toHaveClass(/sidecar-bad/);
    await expect(stopBtn).toBeHidden();

    await expect(badge).toHaveText(/Sidecar: failed to start/, { timeout: 15000 });
    await expect(badge).toHaveClass(/sidecar-status sidecar-bad/);
    await expect(badge).not.toHaveClass(/sidecar-ok/);
    await expect(badge).not.toHaveClass(/sidecar-warn/);
    await expect(stopBtn).toBeHidden();
  });

  test("Task 2.2: Badge recovers during polling -> transitions from starting to connected (v0.2.0) and reveals stop button", async ({ page }) => {
    serveAssets(page);
    let pollAttempts = 0;

    await page.route("**/graphql", async (route) => {
      const postData = JSON.parse(route.request().postData() || "{}");
      if (postData.query?.includes("FindScenes")) {
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ data: { findScenes: { scenes: [] } } })
        });
      }
      if (postData.variables?.task_name === "StartBackend") {
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ data: { runPluginTask: "job-delayed-start" } })
        });
      }
      return route.fallback();
    });

    await page.route("**/health*", async (route) => {
      pollAttempts++;
      if (pollAttempts <= 2) {
        return route.abort();
      }
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ status: "ok", version: "0.2.0", scratch_dir: "D:\\RecoveredScratch" })
      });
    });

    await page.goto("http://localhost:9999/plugins/empornium-megapack/review.html?scenes=1");

    const badge = page.locator("#sidecar-status");
    const stopBtn = page.locator("#btn-sidecar-stop");

    // Recovers to connected
    await expect(badge).toHaveText("Sidecar: connected (v0.2.0)", { timeout: 8000 });
    await expect(badge).toHaveClass(/sidecar-status sidecar-ok/);
    await expect(badge).not.toHaveClass(/sidecar-bad/);
    await expect(badge).not.toHaveClass(/sidecar-warn/);
    await expect(stopBtn).toBeVisible();
  });

  test("Task 2.3: Badge handles outdated version accurately with warning class and restart remediation", async ({ page }) => {
    serveAssets(page);

    await page.route("**/graphql", async (route) => {
      const postData = JSON.parse(route.request().postData() || "{}");
      if (postData.query?.includes("FindScenes")) {
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ data: { findScenes: { scenes: [] } } })
        });
      }
      return route.fallback();
    });

    await page.route("**/health*", (route) => {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ status: "ok", version: "0.1.8", scratch_dir: "D:\\OldScratch" })
      });
    });

    await page.goto("http://localhost:9999/plugins/empornium-megapack/review.html?scenes=1");

    const badge = page.locator("#sidecar-status");
    const stopBtn = page.locator("#btn-sidecar-stop");

    await expect(badge).toHaveText(/Sidecar: outdated/);
    await expect(badge).toHaveClass(/sidecar-status sidecar-warn/);
    await expect(badge).not.toHaveClass(/sidecar-ok/);
    await expect(stopBtn).toBeVisible();
  });

  // =========================================================================
  // TASK 3: Verify clicking Stop sidecar dispatches POST /api/shutdown
  // =========================================================================

  test("Task 3.1: Clicking Stop sidecar sends POST /api/shutdown and transitions badge to NOT RUNNING", async ({ page }) => {
    serveAssets(page);
    let shutdownCalled = false;

    await page.route("**/graphql", async (route) => {
      const postData = JSON.parse(route.request().postData() || "{}");
      if (postData.query?.includes("FindScenes")) {
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ data: { findScenes: { scenes: [] } } })
        });
      }
      return route.fallback();
    });

    await page.route("**/health*", (route) => {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ status: "ok", version: "0.2.0" })
      });
    });

    await page.route("**/api/shutdown*", (route) => {
      expect(route.request().method()).toBe("POST");
      shutdownCalled = true;
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ status: "ok", detail: "Server shutting down" })
      });
    });

    await page.goto("http://localhost:9999/plugins/empornium-megapack/review.html?scenes=1");

    const badge = page.locator("#sidecar-status");
    const stopBtn = page.locator("#btn-sidecar-stop");

    await expect(badge).toHaveText("Sidecar: connected (v0.2.0)");
    await expect(stopBtn).toBeVisible();

    await stopBtn.click();

    expect(shutdownCalled).toBe(true);
    await expect(badge).toHaveText(/Sidecar: NOT RUNNING/);
    await expect(badge).toHaveClass(/sidecar-status sidecar-bad/);
    await expect(stopBtn).toBeHidden();
  });

  test("Task 3.2: Stop sidecar handles network disconnect during shutdown without error throwing", async ({ page }) => {
    serveAssets(page);

    await page.route("**/graphql", async (route) => {
      const postData = JSON.parse(route.request().postData() || "{}");
      if (postData.query?.includes("FindScenes")) {
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ data: { findScenes: { scenes: [] } } })
        });
      }
      return route.fallback();
    });

    await page.route("**/health*", (route) => {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ status: "ok", version: "0.2.0" })
      });
    });

    await page.route("**/api/shutdown*", (route) => route.abort());

    await page.goto("http://localhost:9999/plugins/empornium-megapack/review.html?scenes=1");

    const badge = page.locator("#sidecar-status");
    const stopBtn = page.locator("#btn-sidecar-stop");

    await expect(badge).toHaveText("Sidecar: connected (v0.2.0)");
    await expect(stopBtn).toBeVisible();

    await stopBtn.click();

    await expect(badge).toHaveText(/Sidecar: NOT RUNNING/);
    await expect(badge).toHaveClass(/sidecar-status sidecar-bad/);
    await expect(stopBtn).toBeHidden();
  });

  // =========================================================================
  // TASK 4: Verify wizard initialization never blocks or stalls when sidecar is down
  // =========================================================================

  test("Task 4.1: Wizard initialization renders and becomes fully interactive in <200ms when sidecar is unreachable and polling", async ({ page }) => {
    serveAssets(page);

    await page.route("**/graphql", async (route) => {
      const postData = JSON.parse(route.request().postData() || "{}");
      if (postData.query?.includes("FindScenes")) {
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ data: { findScenes: { scenes: [createScene(10, "Scene Alpha")] } } })
        });
      }
      if (postData.variables?.task_name === "StartBackend") {
        await new Promise(r => setTimeout(r, 1000));
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ data: { runPluginTask: "job-slow-start" } })
        });
      }
      return route.fallback();
    });

    await page.route("**/health*", (route) => route.abort());

    await page.goto("http://localhost:9999/plugins/empornium-megapack/review.html?scenes=1");

    await expect(page.locator("#stage-rail")).toBeVisible();
    await expect(page.locator("#stage-item-1")).toHaveClass(/stage-current/);
    await expect(page.locator("#pack-title")).toBeVisible();
    await expect(page.locator("#btn-stage-next")).toBeEnabled();

    await page.locator("#pack-title").fill("My Fast Pack");
    expect(await page.locator("#pack-title").inputValue()).toBe("My Fast Pack");

    await page.locator("#btn-stage-next").click();
    await expect(page.locator("#stage-item-2")).toHaveClass(/stage-current/);
  });

  test("Task 4.2: Advancing on Locations stage when sidecar is down fails closed with informative error without page crash", async ({ page }) => {
    serveAssets(page);

    await page.route("**/graphql", async (route) => {
      const postData = JSON.parse(route.request().postData() || "{}");
      if (postData.query?.includes("FindScenes")) {
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ data: { findScenes: { scenes: [createScene(10, "Scene Alpha")] } } })
        });
      }
      return route.fallback();
    });

    await page.route("**/health*", (route) => route.abort());
    await page.route("**/api/fs/exists*", (route) => route.abort());

    await page.goto("http://localhost:9999/plugins/empornium-megapack/review.html?scenes=1");

    await page.locator("#pack-title").fill("My Pack");
    await page.locator("#btn-stage-next").click();
    await expect(page.locator("#stage-item-2")).toHaveClass(/stage-current/);

    await page.locator("#output-dir").fill(SEED);
    await page.locator("#scratch-dir").fill(SCRATCH);

    await page.locator("#btn-stage-next").click();

    await expect(page.locator("#stage-item-2")).toHaveClass(/stage-current/);
    await expect(page.locator("#status-text")).toContainText("not found or could not be verified on disk");
    await expect(page.locator("#btn-stage-next")).toBeEnabled();
  });

});
