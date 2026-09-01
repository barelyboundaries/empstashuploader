import { test, expect } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

// The build's real output -- hosted image URLs and the final BBCode -- exists
// only on the backend. It reaches the browser on a EMPORNIUM_TASK_RESULT log
// line; without it the UI falls back to locally-composed data that has no
// image block at all.
const REMOTE_IMAGE = "https://hamsterimg.net/images/2026/08/28/preview.jpg";
const BACKEND_BBCODE =
  "[center][b][size=5]Anji & Honey [2160p][/size][/b][/center]\n\n" +
  `[url=${REMOTE_IMAGE}][img=200]${REMOTE_IMAGE}[/img][/url]`;

function serveAssets(page) {
  page.route("**/plugin*/**/main.js*", (route) =>
    route.fulfill({ status: 200, contentType: "application/javascript", body: fs.readFileSync(path.resolve("plugin/main.js"), "utf8") })
  );
  page.route("**/plugin*/**/style.css*", (route) =>
    route.fulfill({ status: 200, contentType: "text/css", body: fs.readFileSync(path.resolve("plugin/style.css"), "utf8") })
  );
  page.route("**/plugin*/**/review.html*", (route) =>
    route.fulfill({ status: 200, contentType: "text/html", body: fs.readFileSync(path.resolve("plugin/assets/review.html"), "utf8") })
  );
  page.route("**/*review.js*", (route) =>
    route.fulfill({ status: 200, contentType: "application/javascript", body: fs.readFileSync(path.resolve("plugin/assets/review.js"), "utf8") })
  );
}

// `emitResult` controls whether the backend's result sentinel appears in the
// log stream the UI reads after the job reports FINISHED.
function setupGraphQLMocks(page, { emitResult = true, imageCount = 1 } = {}) {
  serveAssets(page);

  // Build pre-flight (todo 7 of staged-wizard-inplace-seed): the authoritative
  // on-disk probe must succeed or the build is blocked fail-closed.
  page.route("**/api/fs/exists*", async (route) => {
    const postData = JSON.parse(route.request().postData() || "{}");
    const results = {};
    for (const p of postData.paths || []) results[p] = true;
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ results })
    });
  });

  page.route("**/graphql", async (route) => {
    const postData = JSON.parse(route.request().postData() || "{}");
    const query = postData.query || "";

    if (query.includes("FindScenes")) {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          data: {
            findScenes: {
              scenes: [
                {
                  id: 10,
                  title: "Anji & Honey",
                  date: "2026-08-01",
                  files: [{ id: 101, path: "C:/Packs/anji.mp4", size: 1048576, height: 2160, width: 3840, duration: 1057, video_codec: "h264" }],
                  performers: [{ id: "p1", name: "Auhneesh Nicole" }],
                  tags: [{ id: "t1", name: "Big Ass" }]
                }
              ]
            }
          }
        })
      });
    }

    if (query.includes("runPluginTask")) {
      return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ data: { runPluginTask: "job-result-1" } }) });
    }

    if (query.includes("findJob")) {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ data: { findJob: { id: "job-result-1", status: "FINISHED", progress: 1.0, error: null } } })
      });
    }

    if (query.includes("logs")) {
      const runId = await page.evaluate(() => window.__lastRunId || "");
      const messages = [];
      if (emitResult && runId) {
        messages.push({
          time: "2026-08-28T10:00:00Z",
          level: "Info",
          message:
            `EMPORNIUM_TASK_RESULT ${runId}: ` +
            JSON.stringify({
              status: "success",
              pack_title: "Anji & Honey",
              bbcode: BACKEND_BBCODE,
              uploaded_urls: [
                REMOTE_IMAGE,
                ...Array.from({ length: imageCount - 1 }, (_, i) => `${REMOTE_IMAGE}?n=${i}`)
              ],
              tracker_tags: ["big.ass", "pov", "h264"],
              torrent_path: "C:\Packs\Anji & Honey.torrent",
              preview_only: false,
              ready: true,
              preflight: {
                ready: true,
                checks: [{ id: "images_remote", label: "Preview Images", passed: true, detail: "All 1 preview image(s) hosted remotely" }]
              }
            })
        });
      }
      return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ data: { logs: messages } }) });
    }

    return route.continue();
  });
}

// The run_id is generated client-side, so the log mock has to learn it from
// the page before it can address the sentinel to this run.
async function captureRunId(page) {
  await page.addInitScript(() => {
    const orig = window.fetch;
    window.fetch = function (...args) {
      try {
        const init = args[1] || {};
        const parsed = JSON.parse(String(init.body || "{}"));
        const args_ = parsed?.variables?.args || [];
        for (const arg of args_) {
          if (arg?.key !== "payload") continue;
          const raw = arg?.value?.str ?? arg?.value;
          const payload = typeof raw === "string" ? JSON.parse(raw) : raw;
          if (payload?.run_id) window.__lastRunId = payload.run_id;
        }
      } catch (_) {}
      return orig.apply(this, args);
    };
  });
}

async function runBuild(page) {
  await page.goto("http://localhost:9999/plugins/empornium-megapack/review.html?scenes=10&mode=single");
  await expect(page.locator("#loading-state")).toBeHidden({ timeout: 5000 });
  await page.locator("#output-dir").fill("C:\\Packs");
  await page.locator("#pack-title").fill("Anji & Honey");
  await page.locator("#btn-build").click();
  await expect(page.locator("#artifact-summary")).toBeVisible({ timeout: 8000 });
}

test.describe("Build result sentinel reaches the handoff panel", () => {
  test("hosted preview URL is listed and linked after a successful build", async ({ page }) => {
    await captureRunId(page);
    setupGraphQLMocks(page);
    await runBuild(page);

    const link = page.locator("#handoff-image-urls a");
    await expect(link).toHaveCount(1);
    await expect(link).toHaveAttribute("href", REMOTE_IMAGE);
  });

  test("BBCode preview is replaced with the backend's copy, image block included", async ({ page }) => {
    await captureRunId(page);
    setupGraphQLMocks(page);
    await runBuild(page);

    const preview = page.locator("#bbcode-preview");
    await expect(preview).toContainText(`[img=200]${REMOTE_IMAGE}[/img]`);
  });

  test("backend pre-flight results are shown rather than the client-side fallback", async ({ page }) => {
    await captureRunId(page);
    setupGraphQLMocks(page);
    await runBuild(page);

    await expect(page.locator("#check-images_remote")).toContainText("All 1 preview image(s) hosted remotely");
  });

  test("a large gallery stays collapsed so the checklist is not pushed off-panel", async ({ page }) => {
    // Regression: 14 raw URLs rendered inline made the summary ~666px tall and
    // pushed the pre-flight checklist and upload button below the fold.
    await captureRunId(page);
    setupGraphQLMocks(page, { imageCount: 14 });
    await runBuild(page);

    await expect(page.locator("#handoff-image-urls li")).toHaveCount(14);
    // details/summary starts closed, so the URLs take one line, not fourteen.
    await expect(page.locator("details.handoff-images")).not.toHaveAttribute("open", /.*/);
    await expect(page.locator("#handoff-image-urls")).toBeHidden();

    const summaryFits = await page.evaluate(() => {
      const box = document.getElementById("artifact-summary");
      return box.getBoundingClientRect().height <= window.innerHeight * 0.6 + 1;
    });
    expect(summaryFits).toBe(true);
  });

  test("the URL list opens on demand", async ({ page }) => {
    await captureRunId(page);
    setupGraphQLMocks(page, { imageCount: 14 });
    await runBuild(page);

    await page.locator("details.handoff-images > summary").click();

    await expect(page.locator("#handoff-image-urls")).toBeVisible();
    await expect(page.locator("#handoff-image-urls a").first()).toHaveAttribute("href", REMOTE_IMAGE);
  });

  test("the summary box can scroll to its own overflow", async ({ page }) => {
    await captureRunId(page);
    setupGraphQLMocks(page, { imageCount: 14 });
    await runBuild(page);

    const scrollable = await page.evaluate(() => {
      const panel = document.querySelector(".options-panel");
      return panel.scrollHeight > panel.clientHeight;
    });
    expect(scrollable).toBe(true);

    const moved = await page.evaluate(() => {
      const panel = document.querySelector(".options-panel");
      const before = panel.scrollTop;
      panel.scrollTop = panel.scrollHeight;
      return panel.scrollTop > before;
    });
    expect(moved).toBe(true);
  });

  test("panel still renders when no result sentinel is found", async ({ page }) => {
    await captureRunId(page);
    setupGraphQLMocks(page, { emitResult: false });
    await runBuild(page);

    await expect(page.locator("#handoff-status-header")).toBeVisible();
    await expect(page.locator("#handoff-image-urls")).toHaveCount(0);
  });
});
