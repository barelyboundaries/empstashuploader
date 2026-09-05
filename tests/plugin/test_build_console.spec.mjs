import { test, expect } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

// Change B Test Suite: Verbose Build Console Surface & Un-gated Copy Controls
// Invariant: Must mock /api/run/** to isolate sidecar and satisfy test_asset_parity.spec.mjs

const SEED_DIR = "C:\\Packs";
const SCRATCH_DIR = "C:\\Scratch";

function setupAssetRoutes(page) {
  page.route("**/plugin*/**/main.js*", async (route) => {
    return route.fulfill({
      status: 200,
      contentType: "application/javascript",
      body: fs.readFileSync(path.resolve("plugin/main.js"), "utf8")
    });
  });

  page.route("**/plugin*/**/style.css*", async (route) => {
    return route.fulfill({
      status: 200,
      contentType: "text/css",
      body: fs.readFileSync(path.resolve("plugin/style.css"), "utf8")
    });
  });

  page.route("**/plugin*/**/review.html*", async (route) => {
    return route.fulfill({
      status: 200,
      contentType: "text/html",
      body: fs.readFileSync(path.resolve("plugin/assets/review.html"), "utf8")
    });
  });

  page.route("**/*review.js*", async (route) => {
    return route.fulfill({
      status: 200,
      contentType: "application/javascript",
      body: fs.readFileSync(path.resolve("plugin/assets/review.js"), "utf8")
    });
  });

  // Suite Invariant: Isolate backend sidecar run store
  page.route("**/api/run/**", async (route) => route.abort("connectionrefused"));

  page.route("**/health", async (route) => {
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ ok: true, status: "connected", version: "0.2.0", scratch_dir: SCRATCH_DIR, announce_configured: true, hamster_configured: true })
    });
  });

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
}

function mockScene(id, fileId, filePath, title = `Scene ${id}`) {
  return {
    id,
    title,
    date: "2026-01-01",
    paths: { screenshot: "" },
    files: [{
      id: fileId,
      path: filePath,
      size: 5000000,
      height: 1080,
      width: 1920,
      duration: 600,
      video_codec: "h264",
      oshash: `oshash-${fileId}`
    }],
    performers: [{ id: `p${id}`, name: `Performer ${id}` }],
    tags: [{ id: `t${id}`, name: `Tag ${id}` }],
    studio: { name: "Studio A" }
  };
}

async function bootReviewHarness(page, {
  scenes = [
    mockScene(1, 101, `${SEED_DIR}\\scene1.mp4`, "Scene 1"),
    mockScene(2, 102, `${SEED_DIR}\\scene2.mp4`, "Scene 2")
  ],
  mode = "megapack",
  mockWs = true
} = {}) {
  setupAssetRoutes(page);
  const state = {
    dispatchedTasks: [],
    dispatchedRunId: null,
    moveMutations: []
  };

  await page.route("**/graphql", async (route) => {
    const postData = JSON.parse(route.request().postData() || "{}");
    const query = postData.query || "";

    if (query.includes("FindScenes")) {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ data: { findScenes: { scenes } } })
      });
    }

    if (query.includes("StageDirCheck")) {
      const p = postData.variables?.path || "";
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ data: { directory: { path: p } } })
      });
    }

    if (query.includes("runPluginTask")) {
      state.dispatchedTasks.push(postData.variables);
      const args = postData.variables?.args || [];
      for (const arg of args) {
        if (arg?.key === "payload") {
          const raw = arg?.value?.str ?? arg?.value;
          const payload = typeof raw === "string" ? JSON.parse(raw) : raw;
          if (payload?.run_id) state.dispatchedRunId = payload.run_id;
        }
      }
      const taskName = postData.variables?.task_name || "BuildMegapack";
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ data: { runPluginTask: `job-${taskName}-1` } })
      });
    }

    if (query.includes("MoveFiles") || query.includes("moveFiles")) {
      state.moveMutations.push(postData.variables);
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ data: { moveFiles: true } })
      });
    }

    if (query.includes("findJob")) {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          data: {
            findJob: {
              id: "job-build-1",
              status: "RUNNING",
              progress: 0.5,
              error: null
            }
          }
        })
      });
    }

    if (query.includes("logs") || query.includes("Logs")) {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ data: { logs: [] } })
      });
    }

    return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ data: {} }) });
  });

  if (mockWs) {
    await page.addInitScript(() => {
      class MockWebSocket {
        constructor(url, protocols) {
          this.url = url;
          this.protocols = protocols;
          this.sent = [];
          window.__mockWsInstance = this;
          window.__mockWsProtocols = protocols;
          window.__mockWsSent = this.sent;
          setTimeout(() => {
            if (this.onopen) this.onopen();
          }, 10);
        }
        send(data) {
          try {
            this.sent.push(JSON.parse(data));
          } catch (_) {
            this.sent.push(data);
          }
        }
        close() {
          if (this.onclose) this.onclose();
        }
      }
      window.WebSocket = MockWebSocket;
    });
  }

  const sceneIds = scenes.map(s => s.id).join(",");
  await page.goto(`http://localhost:9999/plugins/empornium-megapack/review.html?scenes=${sceneIds}&mode=${mode}`);
  await expect(page.locator(".scene-card")).toHaveCount(scenes.length);

  await page.locator("#pack-title").fill("Build Console Test Pack");
  await page.locator("#output-dir").fill(SEED_DIR);
  await page.locator("#scratch-dir").fill(SCRATCH_DIR);

  return state;
}

// Helpers to push WebSocket events into review.js
async function deliverWsJobProgress(page, { jobId = "job-BuildMegapack-1", status = "RUNNING", progress = 0.5, error = null } = {}) {
  await page.evaluate(({ jobId, status, progress, error }) => {
    const ws = window.__mockWsInstance;
    if (!ws || !ws.onmessage) return;
    ws.onmessage({
      data: JSON.stringify({
        type: "next",
        payload: {
          data: {
            jobsSubscribe: {
              job: { id: jobId, status, progress, error }
            }
          }
        }
      })
    });
  }, { jobId, status, progress, error });
}

async function deliverWsLogs(page, logEntries = []) {
  await page.evaluate((entries) => {
    const ws = window.__mockWsInstance;
    if (!ws || !ws.onmessage) return;
    ws.onmessage({
      data: JSON.stringify({
        type: "next",
        payload: {
          data: {
            loggingSubscribe: entries
          }
        }
      })
    });
  }, logEntries);
}

test.describe("Playwright Build Console Specifications (Change B)", () => {

  // Specification 1: Console overlay opens on Build dispatch, showing elapsed time, progress bar, phases
  test("1. Build dispatch opens console overlay, starts elapsed timer, and tracks progress phases", async ({ page }) => {
    await bootReviewHarness(page);
    const consoleOverlay = page.locator("#build-console");
    await expect(consoleOverlay).toBeHidden();

    // Dispatch Build directly
    await page.locator("#btn-build").click();

    // Overlay must open immediately
    await expect(consoleOverlay).toBeVisible();
    await expect(page.locator("#build-console-title")).toContainText(/Building|Megapack/i);

    // Elapsed timer starts ticking
    const elapsedEl = page.locator("#build-console-elapsed");
    await expect(elapsedEl).toBeVisible();
    await expect(elapsedEl).toHaveText(/0:0[0-9]/);

    // Progress bar initializes
    const progressBar = page.locator("#build-console-bar");
    await expect(progressBar).toBeVisible();

    // Phases checklist initialized
    const phasesContainer = page.locator("#build-console-phases");
    await expect(phasesContainer).toBeVisible();

    // Progress to contact sheets phase (0.25)
    await deliverWsJobProgress(page, { progress: 0.25 });
    // Progress bar width reflects update
    await expect.poll(async () => {
      const width = await progressBar.evaluate(el => el.style.width);
      return parseFloat(width) >= 20;
    }).toBe(true);

    // Minimize button collapses overlay
    const minBtn = page.locator("#btn-build-console-minimize");
    await expect(minBtn).toBeVisible();
    await minBtn.click();
    await expect(consoleOverlay).toBeHidden();
  });

  // Specification 2: Console overlay opens on Probe dispatch
  test("2. Probe dispatch opens console overlay and initializes probe progress view", async ({ page }) => {
    await bootReviewHarness(page);
    const consoleOverlay = page.locator("#build-console");
    await expect(consoleOverlay).toBeHidden();

    // Click Probe
    await page.locator("#btn-probe").click();

    // Overlay opens with probe context
    await expect(consoleOverlay).toBeVisible();
    await expect(page.locator("#build-console-title")).toContainText(/Probe|Probing/i);
    await expect(page.locator("#build-console-elapsed")).toBeVisible();
    await expect(page.locator("#build-console-bar")).toBeVisible();
  });

  // Specification 3: Consolidate opens console and logs move actions via appendConsoleLog()
  test("3. Consolidate opens console and logs move actions directly via appendConsoleLog()", async ({ page }) => {
    // Scene files outside destination seed folder to trigger moves
    const scenes = [
      mockScene(1, 101, "D:\\OtherDrive\\scene1.mp4", "Scene 1"),
      mockScene(2, 102, "D:\\OtherDrive\\scene2.mp4", "Scene 2")
    ];
    await bootReviewHarness(page, { scenes });

    // In test 3, destination files in SEED_DIR do not exist yet (no collisions)
    await page.route("**/api/fs/exists*", async (route) => {
      const postData = JSON.parse(route.request().postData() || "{}");
      const results = {};
      for (const p of postData.paths || []) {
        results[p] = p.startsWith(SEED_DIR) ? false : true;
      }
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ results })
      });
    });

    // Handle consolidation confirm dialog
    page.on("dialog", dialog => dialog.accept());

    let finishMove;
    const moveHoldPromise = new Promise(resolve => { finishMove = resolve; });

    await page.route("**/graphql", async (route) => {
      const postData = JSON.parse(route.request().postData() || "{}");
      const query = postData.query || "";
      if (query.includes("MoveFiles") || query.includes("moveFiles")) {
        await moveHoldPromise;
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ data: { moveFiles: true } })
        });
      }
      return route.fallback();
    });

    await page.locator("#btn-consolidate").click();

    const consoleOverlay = page.locator("#build-console");
    await expect(consoleOverlay).toBeVisible();
    await expect(page.locator("#build-console-title")).toContainText(/Consolidat/i);

    // Check log pane contents populated by appendConsoleLog()
    const logPane = page.locator("#build-console-log");
    await expect(logPane).toBeVisible();
    await expect(logPane).toContainText(/Moving|Consolidat/i);
    await expect(logPane).toContainText(/scene1\.mp4|scene2\.mp4|files/i);

    if (finishMove) finishMove();
  });

  // Specification 4: Active run lines appear; mismatched run lines are ignored
  test("4. Active run lines appear in log stream; mismatched run IDs and unrelated lines are ignored", async ({ page }) => {
    const state = await bootReviewHarness(page);
    await page.locator("#btn-build").click();

    await expect.poll(() => state.dispatchedRunId).not.toBeNull();
    const activeRunId = state.dispatchedRunId;

    // Send 3 lines: 1 active run, 1 rogue run, 1 unrelated Stash log
    await deliverWsLogs(page, [
      {
        time: "2026-09-04T12:00:00Z",
        level: "Info",
        message: `[emp:${activeRunId}] Generating contact sheets for scene 1...`
      },
      {
        time: "2026-09-04T12:00:01Z",
        level: "Info",
        message: `[emp:rogue-other-run-999] Rogue task output from concurrent job`
      },
      {
        time: "2026-09-04T12:00:02Z",
        level: "Info",
        message: `Database cleanup finished`
      }
    ]);

    const logPane = page.locator("#build-console-log");
    await expect(logPane).toContainText(`Generating contact sheets for scene 1...`);
    // Mismatched and un-tagged lines must NOT appear in console
    const logText = await logPane.innerText();
    expect(logText).not.toContain("Rogue task output from concurrent job");
    expect(logText).not.toContain("Database cleanup finished");
  });

  // Specification 5: Sentinel BBCode payloads are collapsed to one-liners
  test("5. Sentinel BBCode payloads are collapsed into single-line summaries", async ({ page }) => {
    const state = await bootReviewHarness(page);
    await page.locator("#btn-build").click();

    await expect.poll(() => state.dispatchedRunId).not.toBeNull();
    const activeRunId = state.dispatchedRunId;

    const massiveChunkPayload = "W2NlbnRlcl1bYl1b...".repeat(200); // Simulated massive payload
    await deliverWsLogs(page, [
      {
        time: "2026-09-04T12:00:05Z",
        level: "Info",
        message: `EMPORNIUM_TASK_BBCODE ${activeRunId}: 1/2:${massiveChunkPayload}`
      }
    ]);

    const logPane = page.locator("#build-console-log");
    // Assert massive payload is NOT printed verbatim
    const logText = await logPane.innerText();
    expect(logText).not.toContain(massiveChunkPayload);
    // Assert collapsed indicator is present
    await expect(logPane).toContainText(/BBCODE|chunk/i);
  });

  // Specification 6: Log auto-scrolls with scroll detach detection and "jump to latest" button
  test("6. Log pane auto-scrolls, detaches when scrolled up showing jump button, and re-attaches on click", async ({ page }) => {
    const state = await bootReviewHarness(page);
    await page.locator("#btn-build").click();

    await expect.poll(() => state.dispatchedRunId).not.toBeNull();
    const activeRunId = state.dispatchedRunId;

    // Emit 60 log lines to force overflow
    const lotsOfLogs = Array.from({ length: 60 }, (_, i) => ({
      time: "2026-09-04T12:00:00Z",
      level: "Info",
      message: `[emp:${activeRunId}] Processing batch item ${i + 1} of 60...`
    }));
    await deliverWsLogs(page, lotsOfLogs);

    const logPane = page.locator("#build-console-log");
    const jumpBtn = page.locator("#btn-console-scroll-bottom, #btn-console-jump-latest");

    // Initially auto-scrolled to bottom, jump button hidden
    await expect(jumpBtn).toBeHidden();
    const isAtBottom = await logPane.evaluate(el => Math.abs(el.scrollHeight - el.clientHeight - el.scrollTop) < 10);
    expect(isAtBottom).toBe(true);

    // Scroll up manually
    await logPane.evaluate(el => {
      el.scrollTop = 0;
      el.dispatchEvent(new Event("scroll"));
    });

    // Jump to latest button appears
    await expect(jumpBtn).toBeVisible();

    // Deliver another line while detached; verify view does NOT force-jump to bottom
    await deliverWsLogs(page, [{
      time: "2026-09-04T12:00:10Z",
      level: "Info",
      message: `[emp:${activeRunId}] New event arriving while detached`
    }]);

    const detachedScrollTop = await logPane.evaluate(el => el.scrollTop);
    expect(detachedScrollTop).toBeLessThan(50);

    // Click jump button: scrolls back to bottom and hides button
    await jumpBtn.click();
    const reattachedAtBottom = await logPane.evaluate(el => Math.abs(el.scrollHeight - el.clientHeight - el.scrollTop) < 10);
    expect(reattachedAtBottom).toBe(true);
    await expect(jumpBtn).toBeHidden();
  });

  // Specification 7: WebSocket failure fallback notice
  test("7. WebSocket failure triggers polling fallback and shows fallback notice", async ({ page }) => {
    // Disable mock WS to induce connection failure / watchdog timeout
    await bootReviewHarness(page, { mockWs: false });

    await page.locator("#btn-build").click();

    // Wait for watchdog / error fallback notice in console
    const logPane = page.locator("#build-console-log");
    await expect(logPane).toContainText(
      "Live log unavailable — falling back to progress polling. Check the Stash log for detail.",
      { timeout: 8000 }
    );
  });

  // Specification 8: Completed build reveals #build-console-result with final BBCode and copy controls
  test("8. Completed build reveals #build-console-result with final BBCode and copy controls", async ({ page, context }) => {
    await context.grantPermissions(["clipboard-read", "clipboard-write"]);
    const state = await bootReviewHarness(page);

    await page.locator("#btn-build").click();

    await expect.poll(() => state.dispatchedRunId).not.toBeNull();
    const activeRunId = state.dispatchedRunId;

    const RESULT_BBCODE = "[center][b][size=5]Final Console Megapack[/size][/b][/center]\n[img]https://hamsterimg.net/images/cover.jpg[/img]";
    const COVER_URL = "https://hamsterimg.net/images/cover.jpg";
    const TORRENT_PATH = "C:\\Packs\\Final Console Megapack.torrent";

    // Emit result sentinel and FINISHED job update
    await deliverWsLogs(page, [{
      time: "2026-09-04T12:05:00Z",
      level: "Info",
      message: `EMPORNIUM_TASK_RESULT ${activeRunId}: ` + JSON.stringify({
        status: "success",
        pack_title: "Final Console Megapack",
        bbcode: RESULT_BBCODE,
        tracker_tags: ["megapack", "feature", "1080p"],
        torrent_path: TORRENT_PATH,
        cover_url: COVER_URL,
        ready: true,
        preflight: {
          ready: true,
          checks: [{ id: "images_remote", label: "Remote Images", passed: true, detail: "All images remote" }]
        }
      })
    }]);

    await deliverWsJobProgress(page, { status: "FINISHED", progress: 1.0 });

    // Console remains open and reveals result view
    const consoleOverlay = page.locator("#build-console");
    await expect(consoleOverlay).toBeVisible();

    const resultView = page.locator("#build-console-result");
    await expect(resultView).toBeVisible();

    // Verify copy controls are present in result view
    const btnCopyTitle = page.locator("#btn-copy-title");
    const btnCopyTags = page.locator("#btn-copy-tags");
    const btnCopyTorrent = page.locator("#btn-copy-torrent-path");
    const btnCopyBbcode = page.locator("#btn-copy-bbcode");
    const btnCopyCover = page.locator("#btn-copy-cover-url");

    await expect(btnCopyTitle).toBeVisible();
    await expect(btnCopyTags).toBeVisible();
    await expect(btnCopyTorrent).toBeVisible();
    await expect(btnCopyBbcode).toBeVisible();
    await expect(btnCopyCover).toBeVisible();

    // Test copy cover URL
    await btnCopyCover.click();
    const copiedCover = await page.evaluate(() => navigator.clipboard.readText());
    expect(copiedCover).toBe(COVER_URL);

    // Stage 4 pointer link exists
    const artifactPointer = page.locator("#artifact-summary");
    await expect(artifactPointer).toContainText(/Build complete|Open result/i);
  });

  // Specification 9: Un-gated plain-text copy buttons work even on preflight failure
  test("9. Un-gated copy buttons (Title, Tags, BBCode, Cover URL) remain enabled on preflight failure", async ({ page, context }) => {
    await context.grantPermissions(["clipboard-read", "clipboard-write"]);
    const state = await bootReviewHarness(page);

    await page.locator("#btn-build").click();

    await expect.poll(() => state.dispatchedRunId).not.toBeNull();
    const activeRunId = state.dispatchedRunId;

    const UNREADY_BBCODE = "[b]Local file preview[/b]\nfile:///C:/Media/preview.jpg";
    const COVER_URL = "https://hamsterimg.net/images/unready_cover.jpg";

    // Emit result sentinel with preflight failure (ready: false, no torrent_path)
    await deliverWsLogs(page, [{
      time: "2026-09-04T12:05:00Z",
      level: "Info",
      message: `EMPORNIUM_TASK_RESULT ${activeRunId}: ` + JSON.stringify({
        status: "success",
        pack_title: "Unready Preflight Pack",
        bbcode: UNREADY_BBCODE,
        tracker_tags: ["tag.alpha", "tag.beta"],
        torrent_path: null,
        cover_url: COVER_URL,
        ready: false,
        preflight: {
          ready: false,
          checks: [{ id: "images_remote", label: "Remote Images", passed: false, detail: "Local file:/// URLs present" }]
        }
      })
    }]);

    await deliverWsJobProgress(page, { status: "FINISHED", progress: 1.0 });

    const btnCopyTitle = page.locator("#btn-copy-title");
    const btnCopyTags = page.locator("#btn-copy-tags");
    const btnCopyBbcode = page.locator("#btn-copy-bbcode");
    const btnCopyCover = page.locator("#btn-copy-cover-url");
    const btnCopyTorrent = page.locator("#btn-copy-torrent-path");

    // UN-GATED controls MUST remain ENABLED despite preflight failure
    await expect(btnCopyTitle).toBeEnabled();
    await expect(btnCopyTags).toBeEnabled();
    await expect(btnCopyBbcode).toBeEnabled();
    await expect(btnCopyCover).toBeEnabled();

    // Torrent path is missing, so it remains disabled
    await expect(btnCopyTorrent).toBeDisabled();

    // Verify copy functionality
    await btnCopyTitle.click();
    expect(await page.evaluate(() => navigator.clipboard.readText())).toBe("Unready Preflight Pack");

    await btnCopyTags.click();
    expect(await page.evaluate(() => navigator.clipboard.readText())).toContain("tag.alpha");

    await btnCopyBbcode.click();
    const copiedBbcode = await page.evaluate(() => navigator.clipboard.readText());
    expect(copiedBbcode.replace(/\r\n/g, "\n")).toBe(UNREADY_BBCODE);

    await btnCopyCover.click();
    expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(COVER_URL);
  });

  // Specification 10: Copy all button combines title, tags, and BBCode into clipboard
  test("10. Copy all button combines title, tags, and BBCode into clipboard", async ({ page, context }) => {
    await context.grantPermissions(["clipboard-read", "clipboard-write"]);
    const state = await bootReviewHarness(page);

    await page.locator("#btn-build").click();

    await expect.poll(() => state.dispatchedRunId).not.toBeNull();
    const activeRunId = state.dispatchedRunId;

    await deliverWsLogs(page, [{
      time: "2026-09-04T12:05:00Z",
      level: "Info",
      message: `EMPORNIUM_TASK_RESULT ${activeRunId}: ` + JSON.stringify({
        status: "success",
        pack_title: "Combined Pack",
        bbcode: "[b]All In One Content[/b]",
        tracker_tags: ["tag.one", "tag.two"],
        torrent_path: "C:\\Packs\\combined.torrent",
        ready: true,
        preflight: { ready: true, checks: [] }
      })
    }]);
    await deliverWsJobProgress(page, { status: "FINISHED", progress: 1.0 });

    const btnCopyAll = page.locator("#btn-copy-all");
    await expect(btnCopyAll).toBeVisible();
    await btnCopyAll.click();

    const copiedAll = await page.evaluate(() => navigator.clipboard.readText());
    expect(copiedAll).toContain("Combined Pack");
    expect(copiedAll).toContain("tag.one");
    expect(copiedAll).toContain("[b]All In One Content[/b]");
  });

  // Specification 11: Completion-time log collapse and manual toggle control
  test("11. Log collapses on completion and manual toggle expands/collapses with aria-expanded", async ({ page }) => {
    const state = await bootReviewHarness(page);

    await page.locator("#btn-build").click();
    await expect.poll(() => state.dispatchedRunId).not.toBeNull();
    const activeRunId = state.dispatchedRunId;

    const logEl = page.locator("#build-console-log");
    const toggleBtn = page.locator("#btn-console-toggle-log");
    const resultView = page.locator("#build-console-result");

    // Deliver multiple log lines while build is in progress
    const runningLogs = [];
    for (let i = 1; i <= 20; i++) {
      runningLogs.push({
        time: `2026-09-04T12:00:${i.toString().padStart(2, "0")}Z`,
        level: "Info",
        message: `[emp:${activeRunId}] Processing step ${i} of megapack build...`
      });
    }
    await deliverWsLogs(page, runningLogs);
    await deliverWsJobProgress(page, { status: "RUNNING", progress: 0.5 });

    // Assert: log is NOT collapsed while build is running, and toggle button is hidden
    await expect(logEl).toBeVisible();
    await expect(logEl).not.toHaveClass(/collapsed/);
    await expect(toggleBtn).toBeHidden();
    await expect(resultView).toBeHidden();

    // Finish build with result sentinel
    await deliverWsLogs(page, [{
      time: "2026-09-04T12:01:00Z",
      level: "Info",
      message: `EMPORNIUM_TASK_RESULT ${activeRunId}: ` + JSON.stringify({
        status: "success",
        pack_title: "Collapse Test Pack",
        bbcode: "[b]Completed Pack[/b]",
        tracker_tags: ["test.tag"],
        torrent_path: "C:\\Packs\\test.torrent",
        ready: true,
        preflight: { ready: true, checks: [] }
      })
    }]);
    await deliverWsJobProgress(page, { status: "FINISHED", progress: 1.0 });

    // Assert: result panel appears
    await expect(resultView).toBeVisible();

    // Assert: log IS collapsed once result panel appears
    await expect(logEl).toHaveClass(/collapsed/);

    // Assert: toggle button is visible, shows collapsed label, and has aria-expanded="false"
    await expect(toggleBtn).toBeVisible();
    await expect(toggleBtn).toHaveText("▸ Show full log");
    await expect(toggleBtn).toHaveAttribute("aria-expanded", "false");

    // Assert: log is scrolled to the bottom
    const isScrolledToBottom = await page.evaluate(() => {
      const el = document.getElementById("build-console-log");
      if (!el) return false;
      return el.scrollTop + el.clientHeight >= el.scrollHeight - 5;
    });
    expect(isScrolledToBottom).toBe(true);

    // Click toggle to expand log
    await toggleBtn.click();
    await expect(logEl).not.toHaveClass(/collapsed/);
    await expect(toggleBtn).toHaveText("▾ Collapse log");
    await expect(toggleBtn).toHaveAttribute("aria-expanded", "true");

    // Deliver a subsequent log append
    await deliverWsLogs(page, [{
      time: "2026-09-04T12:02:00Z",
      level: "Info",
      message: `[emp:${activeRunId}] Late post-build log line appended`
    }]);

    // Assert: manual expand is NOT undone by subsequent log append
    await expect(logEl).not.toHaveClass(/collapsed/);
    await expect(toggleBtn).toHaveText("▾ Collapse log");
    await expect(toggleBtn).toHaveAttribute("aria-expanded", "true");

    // Click toggle to re-collapse log
    await toggleBtn.click();
    await expect(logEl).toHaveClass(/collapsed/);
    await expect(toggleBtn).toHaveText("▸ Show full log");
    await expect(toggleBtn).toHaveAttribute("aria-expanded", "false");
  });
});
