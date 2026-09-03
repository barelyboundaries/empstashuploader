import { test, expect } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

// Brief F: Stop StartBackend piling up in the Stash job queue
// Coverage for:
// 1. Sidecar down and a StartBackend already READY in the queue -> no second dispatch.
// 2. Sidecar down and the queue clear -> dispatches exactly once.
// 3. Queue blocked by an unrelated RUNNING job -> badge reflects "queued/waiting", and does not claim failed.
// 4. Several refreshSidecarStatus() calls spanning the poll window produce at most one queued dispatch.
// 5. The queue query failing -> falls back to dispatching (fail safe), not to silence.
// 6. Sidecar healthy -> no queue query and no dispatch at all.

function serveAssets(page) {
  // Catch-all FIRST
  page.route("**/*", (route) => route.fulfill({ status: 404, contentType: "text/plain", body: "" }));
  // Isolate sidecar api/run route per test harness invariant
  page.route("**/api/run/**", (route) => route.abort());

  page.route("**/plugin*/**/review.html*", async (route) => {
    const filePath = path.resolve("plugin/assets/review.html");
    return route.fulfill({
      status: 200,
      contentType: "text/html",
      body: fs.readFileSync(filePath, "utf8")
    });
  });

  page.route("**/*review.js*", async (route) => {
    const filePath = path.resolve("plugin/assets/review.js");
    return route.fulfill({
      status: 200,
      contentType: "application/javascript",
      body: fs.readFileSync(filePath, "utf8")
    });
  });
}

test.describe("Brief F — StartBackend queue flood prevention & truthful status badge", () => {

  test("1. Sidecar down and a StartBackend already READY in the queue -> no second dispatch", async ({ page }) => {
    serveAssets(page);
    await page.route("**/health*", (route) => route.abort());

    const runPluginTaskCalls = [];
    let jobQueueCalls = 0;

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
      if (query.includes("JobQueue")) {
        jobQueueCalls++;
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            data: {
              jobQueue: [
                {
                  id: "job-existing-sb",
                  status: "READY",
                  description: "Running plugin task: StartBackend",
                  startTime: null
                }
              ]
            }
          })
        });
      }
      if (query.includes("runPluginTask")) {
        runPluginTaskCalls.push(postData);
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ data: { runPluginTask: "job-unexpected" } })
        });
      }
      return route.fallback();
    });

    await page.goto("http://localhost:9999/plugins/empornium-megapack/review.html?scenes=1");

    const badge = page.locator("#sidecar-status");
    await expect(badge).toHaveText(/Sidecar: start queued/);
    await expect(badge).toHaveClass(/sidecar-status sidecar-warn/);

    // Call refreshSidecarStatus explicitly to verify subsequent refresh skips dispatch
    await page.evaluate(() => window.refreshSidecarStatus());

    expect(jobQueueCalls).toBeGreaterThanOrEqual(1);
    expect(runPluginTaskCalls).toHaveLength(0);
  });

  test("2. Sidecar down and the queue clear -> dispatches exactly once", async ({ page }) => {
    serveAssets(page);
    await page.route("**/health*", (route) => route.abort());

    const runPluginTaskCalls = [];
    let jobQueueCalls = 0;

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
      if (query.includes("JobQueue")) {
        jobQueueCalls++;
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ data: { jobQueue: [] } })
        });
      }
      if (query.includes("runPluginTask")) {
        if (postData.variables?.task_name === "StartBackend") {
          runPluginTaskCalls.push(postData);
        }
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ data: { runPluginTask: "job-new-sb" } })
        });
      }
      return route.fallback();
    });

    await page.goto("http://localhost:9999/plugins/empornium-megapack/review.html?scenes=1");

    const badge = page.locator("#sidecar-status");
    await expect(badge).toHaveText(/Sidecar: starting/);

    expect(jobQueueCalls).toBeGreaterThanOrEqual(1);
    expect(runPluginTaskCalls).toHaveLength(1);
    expect(runPluginTaskCalls[0].variables.plugin_id).toBe("empornium-megapack");
    expect(runPluginTaskCalls[0].variables.task_name).toBe("StartBackend");
  });

  test("3. Queue blocked by an unrelated RUNNING job -> badge reflects queued/waiting, not failed, after poll window expires", async ({ page }) => {
    serveAssets(page);
    await page.route("**/health*", (route) => route.abort());

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
      if (query.includes("JobQueue")) {
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            data: {
              jobQueue: [
                {
                  id: "job-329",
                  status: "RUNNING",
                  description: "Running plugin task: BuildMegapack",
                  startTime: "2026-09-03T02:09:30Z"
                },
                {
                  id: "job-330",
                  status: "READY",
                  description: "Running plugin task: StartBackend",
                  startTime: null
                }
              ]
            }
          })
        });
      }
      return route.fallback();
    });

    await page.goto("http://localhost:9999/plugins/empornium-megapack/review.html?scenes=1");

    const badge = page.locator("#sidecar-status");
    // Initially reflects queued and waiting on BuildMegapack
    await expect(badge).toHaveText(/Sidecar: start queued — waiting on BuildMegapack/);
    await expect(badge).toHaveClass(/sidecar-status sidecar-warn/);

    // Wait for the 10-second poll window to expire
    await page.waitForTimeout(11000);

    // After poll window expires, badge MUST STILL reflect queued/waiting and NOT claim failure
    await expect(badge).toHaveText(/Sidecar: start queued — waiting on BuildMegapack/);
    await expect(badge).toHaveClass(/sidecar-status sidecar-warn/);
    await expect(badge).not.toHaveText(/failed to start/);
    await expect(badge).not.toHaveClass(/sidecar-bad/);
  });

  test("4. Several refreshSidecarStatus() calls spanning the poll window produce at most one queued dispatch", async ({ page }) => {
    test.setTimeout(35000);
    serveAssets(page);
    await page.route("**/health*", (route) => route.abort());

    let dispatchCount = 0;
    let mockQueue = [];

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
      if (query.includes("JobQueue")) {
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ data: { jobQueue: mockQueue } })
        });
      }
      if (query.includes("runPluginTask")) {
        if (postData.variables?.task_name === "StartBackend") {
          dispatchCount++;
          mockQueue = [
            {
              id: "job-sb-1",
              status: "READY",
              description: "Running plugin task: StartBackend",
              startTime: null
            }
          ];
        }
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ data: { runPluginTask: "job-sb-1" } })
        });
      }
      return route.fallback();
    });

    // Call 1 dispatched on page load via initEmporniumReview()
    await page.goto("http://localhost:9999/plugins/empornium-megapack/review.html?scenes=1");

    // Call 2: concurrent call joining the in-flight poll window
    const p2 = page.evaluate(() => window.refreshSidecarStatus());

    // Await the in-flight poll window to expire (~10s)
    await p2;

    // Call 3: executed after the poll window has expired; sees StartBackend in queue and skips dispatch
    await page.evaluate(() => window.refreshSidecarStatus());

    // Call 4: additional check to ensure no queue pileup
    await page.evaluate(() => window.refreshSidecarStatus());

    expect(dispatchCount).toBe(1);
  });

  test("5. The queue query failing -> falls back to dispatching (fail safe), not to silence", async ({ page }) => {
    serveAssets(page);
    await page.route("**/health*", (route) => route.abort());

    let dispatchCount = 0;

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
      if (query.includes("JobQueue")) {
        return route.fulfill({
          status: 500,
          contentType: "application/json",
          body: JSON.stringify({ errors: [{ message: "Internal server error querying jobQueue" }] })
        });
      }
      if (query.includes("runPluginTask")) {
        if (postData.variables?.task_name === "StartBackend") {
          dispatchCount++;
        }
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ data: { runPluginTask: "job-fail-safe-1" } })
        });
      }
      return route.fallback();
    });

    await page.goto("http://localhost:9999/plugins/empornium-megapack/review.html?scenes=1");

    const badge = page.locator("#sidecar-status");
    await expect(badge).toHaveText(/Sidecar: starting/);

    // Must have fallen back to dispatching StartBackend
    expect(dispatchCount).toBe(1);
  });

  test("6. Sidecar healthy -> no queue query and no dispatch at all", async ({ page }) => {
    serveAssets(page);
    await page.route("**/health*", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ status: "ok", version: "0.2.0" })
      })
    );

    let jobQueueCalls = 0;
    let runPluginTaskCalls = 0;

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
      if (query.includes("JobQueue")) {
        jobQueueCalls++;
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ data: { jobQueue: [] } })
        });
      }
      if (query.includes("runPluginTask")) {
        runPluginTaskCalls++;
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ data: { runPluginTask: "job-unexpected" } })
        });
      }
      return route.fallback();
    });

    await page.goto("http://localhost:9999/plugins/empornium-megapack/review.html?scenes=1");

    const badge = page.locator("#sidecar-status");
    await expect(badge).toHaveText("Sidecar: connected (v0.2.0)");
    await expect(badge).toHaveClass(/sidecar-status sidecar-ok/);

    // Explicitly call refreshSidecarStatus to test user/code refresh on healthy sidecar
    await page.evaluate(() => window.refreshSidecarStatus());

    expect(jobQueueCalls).toBe(0);
    expect(runPluginTaskCalls).toBe(0);
  });

});
