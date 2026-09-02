import { test, expect } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

// Diagnostics-layer tests for sidecar probe classification + status badge
// (Todo 5 of consolidate-sidecar-404): the four classification paths in
// pathExistsBatch/classifyProbeFailure (404 → outdated, network → not
// reachable, other-HTTP → status+URL, malformed body → malformed) and the
// three refreshSidecarStatus badge states (NOT RUNNING / connected / outdated).
//
// Mocking discipline: NETWORK LAYER ONLY (page.route), exactly as
// test_destination_collision.spec.mjs does. The sidecar itself never needs to
// run: Playwright intercepts 127.0.0.1:9941 / localhost:9941 before the
// network. A catch-all route is registered FIRST (lowest precedence —
// Playwright matches most-recently-registered first) so no request can ever
// leak to a real live sidecar or to the non-running dev server; this keeps the
// badge tests deterministic even while a real sidecar answers on :9941.

const A_PATH = ["C:\\Packs\\a.mp4"];

function serveAssets(page) {
  // Catch-all FIRST: everything not explicitly routed below gets an empty 404.
  // Prevents any accidental leak to the live sidecar (:9941) or favicon noise.
  page.route("**/*", (route) => route.fulfill({ status: 404, contentType: "text/plain", body: "" }));

  page.route("**/graphql", async (route) => {
    const postData = JSON.parse(route.request().postData() || "{}");
    if (postData.query && postData.query.includes("runPluginTask")) {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ data: { runPluginTask: "job-default-1" } })
      });
    }
    return route.fallback();
  });

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

// Loads the review page with the initial FindScenes returning no scenes, so
// the page boots cleanly and the window-exported functions can be driven
// directly. Must be called AFTER the test-specific routes so those take
// precedence (Playwright: last registered wins).
async function openReviewPage(page) {
  await page.route("**/graphql", async (route) => {
    const postData = JSON.parse(route.request().postData() || "{}");
    if (postData.query && postData.query.includes("FindScenes")) {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ data: { findScenes: { scenes: [] } } })
      });
    }
    // Defer to the test-specific handler registered earlier; never leak to a
    // real Stash instance that may be listening on :9999.
    return route.fallback();
  });
  await page.goto("http://localhost:9999/plugins/empornium-megapack/review.html?scenes=1");
  await expect(page.locator(".scene-card")).toHaveCount(0);
}

test.describe("Empornium Review — sidecar probe diagnostics + status badge", () => {

  // --- pathExistsBatch classification tiers (todo 3) -----------------------

  test("pathExistsBatch: unreachable sidecar (network) -> 'not reachable' + start_backend.ps1 remediation", async ({ page }) => {
    serveAssets(page);
    await openReviewPage(page);

    // Abort BOTH loopback candidates (127.0.0.1:9941 + localhost:9941).
    await page.route("**/api/fs/exists*", (route) => route.abort());

    // Network tier: joined URL list + start_backend.ps1 remediation.
    await expect(page.evaluate((p) => window.pathExistsBatch(p), A_PATH))
      .rejects.toThrow(/not reachable[\s\S]*start_backend\.ps1/);
  });

  test("pathExistsBatch: HTTP 404 -> 'outdated' message naming the first loopback candidate", async ({ page }) => {
    serveAssets(page);
    await openReviewPage(page);

    await page.route("**/api/fs/exists*", (route) =>
      route.fulfill({
        status: 404,
        contentType: "application/json",
        body: JSON.stringify({ detail: "Not Found" })
      })
    );

    // 404 tier: candidate-1's URL appears explicitly, then the outdated claim.
    // (Message shape: "answered 404 at <url> — the running sidecar is
    // outdated …" — URL before "outdated", hence the ordered pattern.)
    await expect(page.evaluate((p) => window.pathExistsBatch(p), A_PATH))
      .rejects.toThrow(/404 at http:\/\/127\.0\.0\.1:9941\/api\/fs\/exists[\s\S]*outdated/);
  });

  test("pathExistsBatch: other HTTP status (500) -> 'HTTP 500' + failing URL", async ({ page }) => {
    serveAssets(page);
    await openReviewPage(page);

    await page.route("**/api/fs/exists*", (route) =>
      route.fulfill({ status: 500, contentType: "application/json", body: "boom" })
    );

    // Other-HTTP tier: exact status text and a :9941 exists URL.
    await expect(page.evaluate((p) => window.pathExistsBatch(p), A_PATH))
      .rejects.toThrow(/HTTP 500[\s\S]*9941\/api\/fs\/exists/);
  });

  test("pathExistsBatch: regression guard — valid empty results object resolves (fail-closed only on failure)", async ({ page }) => {
    serveAssets(page);
    await openReviewPage(page);

    // Valid shape, zero entries: the happy path must still resolve.
    await page.route("**/api/fs/exists*", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ results: {} })
      })
    );

    const outcome = await page.evaluate((p) => window.pathExistsBatch(p), A_PATH);
    // Resolves (never rejects) and returns the merged results map object.
    expect(outcome).not.toBeNull();
    expect(typeof outcome).toBe("object");
    expect(Array.isArray(outcome)).toBe(false);
    expect(Object.keys(outcome)).toHaveLength(0);
  });

  test("pathExistsBatch: malformed 200 body -> 'malformed' classification, NOT 'not reachable'", async ({ page }) => {
    serveAssets(page);
    await openReviewPage(page);

    // Non-JSON body on an OK response: json() throws inside its own
    // try/catch, so the attempt is "malformed", never "network".
    await page.route("**/api/fs/exists*", (route) =>
      route.fulfill({ status: 200, contentType: "application/json", body: "abcdef" })
    );

    await expect(page.evaluate((p) => window.pathExistsBatch(p), A_PATH))
      .rejects.toThrow(/malformed response/);
    // Proves the json()/fetch() split: the malformed tier must not be
    // misclassified as the network tier.
    await expect(page.evaluate((p) => window.pathExistsBatch(p), A_PATH))
      .rejects.toThrow(/^(?!.*not reachable)/);
  });

  // --- refreshSidecarStatus badge states & stop affordance -------------------

  test("badge: /health unreachable -> transitions through starting to failed-to-start, swallowed silently (no console/page errors)", async ({ page }) => {
    const consoleErrors = [];
    const pageErrors = [];
    page.on("console", (msg) => {
      if (msg.type() === "error") consoleErrors.push(msg.text());
    });
    page.on("pageerror", (err) => pageErrors.push(err.message));

    serveAssets(page);
    // Registered BEFORE goto so the boot-time refreshSidecarStatus() call is intercepted
    await page.route("**/health*", (route) => route.abort());
    await openReviewPage(page);

    const badge = page.locator("#sidecar-status");
    const stopBtn = page.locator("#btn-sidecar-stop");

    await expect(badge).toHaveClass(/sidecar-warn|sidecar-bad/);
    await expect(stopBtn).toBeHidden();

    // When polling timeout expires or task finishes with unreachable health, settles on failed-to-start
    await expect(badge).toHaveText("Sidecar: failed to start — run start_backend.ps1", { timeout: 15000 });
    await expect(badge).toHaveClass(/sidecar-bad/);
    await expect(stopBtn).toBeHidden();

    expect(pageErrors, "no uncaught page errors").toEqual([]);
    const appConsoleErrors = consoleErrors.filter(
      (text) => !text.startsWith("Failed to load resource:")
    );
    expect(appConsoleErrors, "no app-level console errors").toEqual([]);
  });

  test("badge: /health ok with expected version 0.2.0 -> connected, stop button visible", async ({ page }) => {
    serveAssets(page);
    await page.route("**/health*", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ status: "ok", version: "0.2.0" })
      })
    );
    await openReviewPage(page);

    await page.evaluate(() => window.refreshSidecarStatus());

    const badge = page.locator("#sidecar-status");
    await expect(badge).toHaveText("Sidecar: connected (v0.2.0)");
    await expect(badge).toHaveClass(/sidecar-ok/);

    const stopBtn = page.locator("#btn-sidecar-stop");
    await expect(stopBtn).toBeVisible();
  });

  test("badge: /health ok with old version 0.1.9 -> outdated + restart remediation, stop button visible", async ({ page }) => {
    serveAssets(page);
    await page.route("**/health*", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ status: "ok", version: "0.1.9" })
      })
    );
    await openReviewPage(page);

    await page.evaluate(() => window.refreshSidecarStatus());

    const badge = page.locator("#sidecar-status");
    await expect(badge).toHaveText(
      "Sidecar: outdated (v0.1.9, expected 0.2.0) — restart via start_backend.ps1"
    );
    await expect(badge).toHaveClass(/sidecar-warn/);

    const stopBtn = page.locator("#btn-sidecar-stop");
    await expect(stopBtn).toBeVisible();
  });

  test("stop button: clicking #btn-sidecar-stop dispatches POST /api/shutdown and transitions badge to NOT RUNNING", async ({ page }) => {
    serveAssets(page);
    let shutdownCalls = 0;
    await page.route("**/health*", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ status: "ok", version: "0.2.0" })
      })
    );
    await page.route("**/api/shutdown*", (route) => {
      shutdownCalls++;
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ status: "ok", detail: "Server shutting down" })
      });
    });

    await openReviewPage(page);
    await page.evaluate(() => window.refreshSidecarStatus());

    const badge = page.locator("#sidecar-status");
    const stopBtn = page.locator("#btn-sidecar-stop");

    await expect(badge).toHaveText("Sidecar: connected (v0.2.0)");
    await expect(stopBtn).toBeVisible();

    await stopBtn.click();

    expect(shutdownCalls).toBeGreaterThanOrEqual(1);
    await expect(badge).toHaveText("Sidecar: NOT RUNNING — run start_backend.ps1");
    await expect(badge).toHaveClass(/sidecar-bad/);
    await expect(stopBtn).toBeHidden();
  });

  test("badge recovery: initially unreachable -> starting -> recovers during polling -> connected", async ({ page }) => {
    serveAssets(page);
    let backendStarted = false;
    const taskDispatches = [];

    await page.route("**/graphql", async (route) => {
      const postData = JSON.parse(route.request().postData() || "{}");
      if (postData.query && postData.query.includes("FindScenes")) {
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ data: { findScenes: { scenes: [] } } })
        });
      }
      if (postData.query && postData.query.includes("runPluginTask")) {
        taskDispatches.push(postData.variables);
        if (postData.variables?.task_name === "StartBackend") {
          backendStarted = true;
        }
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ data: { runPluginTask: "job-start-1" } })
        });
      }
      return route.fallback();
    });

    await page.route("**/health*", async (route) => {
      if (!backendStarted) {
        return route.abort();
      }
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ status: "ok", version: "0.2.0", scratch_dir: "C:\\RecoveredScratch" })
      });
    });

    await openReviewPage(page);

    const badge = page.locator("#sidecar-status");
    const stopBtn = page.locator("#btn-sidecar-stop");

    await expect(badge).toHaveText("Sidecar: connected (v0.2.0)", { timeout: 8000 });
    await expect(badge).toHaveClass(/sidecar-ok/);
    await expect(stopBtn).toBeVisible();

    expect(taskDispatches.some((v) => v?.task_name === "StartBackend")).toBe(true);
  });

  test("refreshSidecarStatus debouncing: multiple concurrent calls do not dispatch duplicate StartBackend tasks", async ({ page }) => {
    serveAssets(page);
    let startBackendDispatches = 0;

    await page.route("**/graphql", async (route) => {
      const postData = JSON.parse(route.request().postData() || "{}");
      if (postData.query && postData.query.includes("FindScenes")) {
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ data: { findScenes: { scenes: [] } } })
        });
      }
      if (postData.query && postData.query.includes("runPluginTask")) {
        if (postData.variables?.task_name === "StartBackend") {
          startBackendDispatches++;
        }
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ data: { runPluginTask: "job-start-1" } })
        });
      }
      return route.fallback();
    });

    await page.route("**/health*", (route) => route.abort());

    await openReviewPage(page);

    // Call refreshSidecarStatus 3 times concurrently
    await page.evaluate(() => {
      return Promise.all([
        window.refreshSidecarStatus(),
        window.refreshSidecarStatus(),
        window.refreshSidecarStatus()
      ]);
    });

    // Exactly one StartBackend dispatch should have happened
    expect(startBackendDispatches).toBe(1);
  });

});
