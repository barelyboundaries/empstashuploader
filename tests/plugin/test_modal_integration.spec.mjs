import { test, expect } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

// Helper to mock Stash static plugin assets and GraphQL/WS
function setupMocks(page) {
  // 1. Mock Stash static asset routing for review.html, main.js, style.css
  page.route("**/plugin*/**/main.js*", async (route) => {
    const filePath = path.resolve("plugin/main.js");
    return route.fulfill({
      status: 200,
      contentType: "application/javascript",
      body: fs.readFileSync(filePath, "utf8")
    });
  });

  page.route("**/plugin*/**/style.css*", async (route) => {
    const filePath = path.resolve("plugin/style.css");
    return route.fulfill({
      status: 200,
      contentType: "text/css",
      body: fs.readFileSync(filePath, "utf8")
    });
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


test.describe("Empornium Megapack Builder Frontend - Full Integration Suite", () => {

  test("1. DOM Injection & Modal Opening / Closing (main.js)", async ({ page }) => {
    setupMocks(page);

    // Provide a mock Stash scenes page DOM
    await page.setContent(`
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
          <div class="scene-card" data-scene-id="103">
            <input type="checkbox" />
            <span>Scene 103 (unchecked)</span>
          </div>
        </div>
        <script src="http://localhost:9999/plugins/empornium-megapack/main.js"></script>
      </body>
      </html>
    `);

    // Verify trigger button was injected
    const triggerBtn = page.locator("#empornium-megapack-btn");
    await expect(triggerBtn).toBeVisible();
    await expect(triggerBtn).toContainText("Empornium Uploader");

    // Click trigger button -> opens modal overlay
    await triggerBtn.click();

    const modal = page.locator("#empornium-megapack-modal");
    await expect(modal).toBeVisible();
    await expect(modal.locator(".empornium-badge")).toContainText("2 scene(s) selected");

    // Test Close via &times; button
    const closeBtn = modal.locator(".empornium-modal-close");
    await closeBtn.click();
    await expect(modal).toHaveCount(0);

    // Reopen modal and test Escape key close
    await triggerBtn.click();
    await expect(page.locator("#empornium-megapack-modal")).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(page.locator("#empornium-megapack-modal")).toHaveCount(0);

    // Reopen modal and test postMessage EMPORNIUM_CLOSE_MODAL
    await triggerBtn.click();
    await expect(page.locator("#empornium-megapack-modal")).toBeVisible();
    await page.evaluate(() => {
      window.postMessage({ type: "EMPORNIUM_CLOSE_MODAL" }, "*");
    });
    await expect(page.locator("#empornium-megapack-modal")).toHaveCount(0);
  });

  test("2. Scene loading, reordering, BBCode preview, and clipboard copy (review.html)", async ({ page, context }) => {
    setupMocks(page);

    // Grant clipboard permissions
    await context.grantPermissions(["clipboard-read", "clipboard-write"]);

    // Intercept GraphQL FindScenes
    await page.route("**/graphql", async (route) => {
      const postData = JSON.parse(route.request().postData() || "{}");
      if (postData.query && postData.query.includes("FindScenes")) {
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            data: {
              findScenes: {
                scenes: [
                  {
                    id: 1,
                    title: "Scene Alpha",
                    date: "2026-01-10",
                    paths: { screenshot: "http://localhost:9999/screenshot/1.jpg" },
                    files: [{ id: 11, path: "C:/Media/alpha.mp4", size: 5000000 }],
                    performers: [{ id: 1, name: "Alice Wonderland" }],
                    tags: [{ id: 1, name: "4K" }]
                  },
                  {
                    id: 2,
                    title: "Scene Beta",
                    date: "2026-01-11",
                    paths: { screenshot: "http://localhost:9999/screenshot/2.jpg" },
                    files: [{ id: 22, path: "C:/Media/beta.mp4", size: 6000000 }],
                    performers: [{ id: 2, name: "Bob Builder" }],
                    tags: [{ id: 2, name: "60fps" }]
                  }
                ]
              }
            }
          })
        });
      }
      return route.continue();
    });

    await page.goto("http://localhost:9999/plugins/empornium-megapack/review.html?scenes=1,2");

    // Check rendered scene cards
    await expect(page.locator(".scene-card")).toHaveCount(2);
    await expect(page.locator(".scene-card").first()).toContainText("Scene Alpha");
    await expect(page.locator(".scene-card").nth(1)).toContainText("Scene Beta");

    // Check initial BBCode. The pack title starts EMPTY now (no placeholder
    // default), so set the title the old default used to provide.
    await page.locator("#pack-title").fill("My Awesome Megapack");
    const bbcodeBox = page.locator("#bbcode-preview");
    await expect(bbcodeBox).toHaveValue(/My Awesome Megapack/);
    await expect(bbcodeBox).toHaveValue(/Alice Wonderland, Bob Builder/);
    await expect(bbcodeBox).toHaveValue(/\[b\]Total Scenes:\[\/b\] 2/);
    await expect(bbcodeBox).toHaveValue(/1\. \[b\]Scene Alpha \[\/b\]/);
    await expect(bbcodeBox).toHaveValue(/2\. \[b\]Scene Beta \[\/b\]/);

    // Change title and notes
    await page.locator("#pack-title").fill("Custom Megapack Title");
    await page.locator("#pack-notes").fill("Special release edition");
    await expect(bbcodeBox).toHaveValue(/Custom Megapack Title/);
    await expect(bbcodeBox).toHaveValue(/\[quote\]Special release edition\[\/quote\]/);

    // Test BBCode Copy button
    const copyBtn = page.locator("#btn-copy-bbcode");
    await copyBtn.click();
    await expect(copyBtn).toContainText("Copied!");
  });

  test("3. GraphQL mutation execution (ProbeFiles, MoveFiles, BuildMegapack)", async ({ page }) => {
    setupMocks(page);

    let probeCalledWith = null;
    let moveFilesCalledWith = null;
    let buildCalledWith = null;

    // Handle dialog for confirm() in consolidateFiles
    page.on("dialog", async (dialog) => {
      await dialog.accept();
    });

    // Destination-collision pre-check (read-only): no collisions, nothing
    // exists on disk. Without these the discovery query/probe fall through
    // to route.continue() and leak to a real Stash on :9999.
    await page.route("**/api/fs/exists*", async (route) => {
      const postData = JSON.parse(route.request().postData() || "{}");
      const results = {};
      for (const p of postData.paths || []) results[p] = false;
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ results })
      });
    });

    await page.route("**/graphql", async (route) => {
      const postData = JSON.parse(route.request().postData() || "{}");
      const query = postData.query || "";

      if (query.includes("FindDestinationCollisions")) {
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ data: { findScenes: { scenes: [] } } })
        });
      }

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
                    title: "Scene Ten",
                    date: "2026-02-01",
                    paths: { screenshot: "http://localhost:9999/shot.jpg" },
                    files: [{ id: 100, path: "C:/Media/ten.mp4", size: 10000, height: 1080, width: 1920, duration: 1800, video_codec: "h264" }],
                    performers: [{ id: 5, name: "Performer 5" }],
                    tags: [{ id: 8, name: "Tag 8" }],
                    studio: { id: 2, name: "Studio Ten" }
                  }
                ]
              }
            }
          })
        });
      }

      if (query.includes("RunProbe") || (query.includes("runPluginTask") && postData.variables?.task_name === "ProbeFiles")) {
        probeCalledWith = postData.variables;
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            data: { runPluginTask: "job-probe-99" }
          })
        });
      }

      if (query.includes("MoveFiles") || query.includes("moveFiles")) {
        moveFilesCalledWith = postData.variables;
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            data: { moveFiles: true }
          })
        });
      }

      if (query.includes("RunBuild") || (query.includes("runPluginTask") && postData.variables?.task_name === "BuildMegapack")) {
        buildCalledWith = postData.variables;
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            data: { runPluginTask: "job-build-88" }
          })
        });
      }

      return route.continue();
    });

    await page.goto("http://localhost:9999/plugins/empornium-megapack/review.html?scenes=10&mode=megapack");
    // Seed-dir field starts EMPTY (no machine-path default) — set it explicitly.
    await page.locator("#output-dir").fill("C:\\Packs");

    // 1. Probe Files
    await page.locator("#btn-probe").click();
    expect(probeCalledWith).toBeTruthy();
    expect(probeCalledWith.task_name).toBe("ProbeFiles");
    expect(probeCalledWith.plugin_id).toBe("empornium-megapack");
    await expect(page.locator("#status-text")).toContainText("Task ProbeFiles queued (Job ID: job-probe-99)");

    // 2. Consolidate Files (MoveFiles) — the flow now runs the read-only
    // destination pre-check first, so poll for the mutation on the wire.
    await page.locator("#btn-consolidate").click();
    await expect.poll(() => moveFilesCalledWith).toBeTruthy();
    expect(moveFilesCalledWith.input.ids).toEqual([100]);
    // Consolidation destination = the seed-dir field value (no pack-title
    // subfolder — in-place seeding, todo 6 of staged-wizard-inplace-seed).
    expect(moveFilesCalledWith.input.destination_folder).toBe("C:\\Packs");

    // 3. Build Megapack
    await page.locator("#btn-build").click();
    expect(buildCalledWith).toBeTruthy();
    expect(buildCalledWith.task_name).toBe("BuildMegapack");
    expect(buildCalledWith.plugin_id).toBe("empornium-megapack");

    // Assert that dispatched payload carries height, duration, date, studio, video_codec, and upload_previews
    const payloadArg = buildCalledWith.args.find(a => a.key === "payload");
    expect(payloadArg).toBeTruthy();
    const parsedPayload = JSON.parse(payloadArg.value.str);
    expect(parsedPayload.upload_previews).toBe(false);
    expect(parsedPayload.scenes).toHaveLength(1);
    const scenePayload = parsedPayload.scenes[0];
    expect(scenePayload.height).toBe(1080);
    expect(scenePayload.width).toBe(1920);
    expect(scenePayload.duration).toBe(1800);
    expect(scenePayload.video_codec).toBe("h264");
    expect(scenePayload.date).toBe("2026-02-01");
    expect(scenePayload.studio).toBe("Studio Ten");

    // 4. Test checking upload_previews checkbox
    await page.locator("#opt-upload-previews").check();
    await page.locator("#btn-build").click();
    const payloadArg2 = buildCalledWith.args.find(a => a.key === "payload");
    const parsedPayload2 = JSON.parse(payloadArg2.value.str);
    expect(parsedPayload2.upload_previews).toBe(true);

    await expect(page.locator("#status-text")).toContainText("Task BuildMegapack queued (Job ID: job-build-88)");
  });

  test("4. Polling Job Progress and Artifact Delivery", async ({ page }) => {
    setupMocks(page);

    // Build pre-flight (todo 7 of staged-wizard-inplace-seed): the
    // authoritative on-disk probe must succeed or the build is blocked
    // fail-closed before dispatch.
    await page.route("**/api/fs/exists*", async (route) => {
      const postData = JSON.parse(route.request().postData() || "{}");
      const results = {};
      for (const p of postData.paths || []) results[p] = true;
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ results })
      });
    });

    // Deterministic WebSocket (same addInitScript pattern as test 5, minus
    // the auto-responses): the app auto-subscribes over WS after the build
    // dispatch, and without this mock that subscription reaches the REAL
    // Stash WS on :9999 — its payloads for the unknown job id can race the
    // HTTP polling below and flip artifact rendering into the not-ready
    // branch. Silent mock: opens, records sends, never answers, so the
    // FindJob polling alone drives the job to FINISHED.
    await page.addInitScript(() => {
      class MockWebSocket {
        constructor(url, protocols) {
          this.url = url;
          this.protocols = protocols;
          window.__mockWsInstance = this;
          setTimeout(() => {
            if (this.onopen) this.onopen();
          }, 10);
        }
        send(data) {
          window.__mockWsSent = window.__mockWsSent || [];
          window.__mockWsSent.push(JSON.parse(data));
        }
        close() {
          if (this.onclose) this.onclose();
        }
      }
      window.WebSocket = MockWebSocket;
    });

    let pollCount = 0;

    await page.route("**/api/run/*", async (route) => {
      if (route.request().method() === "GET") {
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            found: true,
            result: {
              status: "success",
              pack_title: "My Awesome Megapack",
              torrent_path: "C:\\Packs\\My Awesome Megapack.torrent",
              manifest_path: "C:\\Packs\\My Awesome Megapack_manifest.json",
              submission_path: "C:\\Packs\\My Awesome Megapack_submission.json",
              bbcode_path: "C:\\Packs\\My Awesome Megapack_bbcode.txt",
              upload_previews: false,
              preview_only: true,
              ready: true,
              tracker_tags: ["scene.one"],
              preflight: {
                ready: true,
                checks: [
                  { id: "images_remote", label: "Preview Images", passed: true, detail: "All remote" },
                  { id: "tracker_tags", label: "Tracker Tags", passed: true, detail: "Tags valid" },
                  { id: "category", label: "Category", passed: true, is_info: true, detail: "Category selected" },
                  { id: "torrent_valid", label: "Torrent File", passed: true, detail: "Valid torrent" },
                  { id: "payload_files", label: "Media Files Verification", passed: true, detail: "Files exist" },
                  { id: "root_name", label: "Torrent Root Name", passed: true, detail: "Matches title" }
                ]
              }
            }
          })
        });
      }
      return route.fallback();
    });

    await page.route("**/graphql", async (route) => {
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
                    id: 1,
                    title: "Scene One",
                    date: "2026-01-01",
                    paths: {},
                    files: [{ id: 1, path: "C:/Packs/s1.mp4", size: 1024 }],
                    performers: [],
                    tags: []
                  }
                ]
              }
            }
          })
        });
      }

      if (query.includes("RunBuild")) {
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            data: { runPluginTask: "job-build-777" }
          })
        });
      }

      if (query.includes("FindJob") || query.includes("findJob")) {
        pollCount++;
        const progress = pollCount === 1 ? 0.45 : 1.0;
        const status = pollCount === 1 ? "RUNNING" : "FINISHED";
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            data: {
              findJob: {
                id: "job-build-777",
                status: status,
                progress: progress,
                error: null
              }
            }
          })
        });
      }

      return route.continue();
    });

    await page.goto("http://localhost:9999/plugins/empornium-megapack/review.html?scenes=1");

    // Seed-dir field starts EMPTY — set it so the build gate passes.
    await page.locator("#output-dir").fill("C:\\Packs");

    // Click build button
    await page.locator("#btn-build").click();

    // Trigger polling manually or let polling run
    await page.evaluate(() => {
      window.startJobPolling("job-build-777", "BuildMegapack", {
        pack_title: "My Awesome Megapack",
        output_dir: "C:\\Packs"
      });
    });

    // Wait for completion and check artifact summary
    const summaryBox = page.locator("#artifact-summary");
    await expect(summaryBox).toBeVisible({ timeout: 5000 });
    await expect(page.locator("#artifact-details")).toContainText("C:\\Packs\\My Awesome Megapack.torrent");
    await expect(page.locator("#artifact-details")).toContainText("C:\\Packs\\My Awesome Megapack_manifest.json");
    await expect(page.locator("#status-text")).toContainText("completed successfully!");
  });

  test("5. WebSocket Job Progress & Task Failure handling", async ({ page }) => {
    setupMocks(page);

    // Build pre-flight (todo 7 of staged-wizard-inplace-seed): the
    // authoritative on-disk probe must succeed or the build is blocked
    // fail-closed before dispatch.
    await page.route("**/api/fs/exists*", async (route) => {
      const postData = JSON.parse(route.request().postData() || "{}");
      const results = {};
      for (const p of postData.paths || []) results[p] = true;
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ results })
      });
    });

    await page.route("**/graphql", async (route) => {
      const postData = JSON.parse(route.request().postData() || "{}");
      const query = postData.query || "";

      if (query.includes("FindScenes")) {
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            data: { findScenes: { scenes: [{ id: 99, title: "Scene Fail", paths: {}, files: [{ id: 999, path: "C:/Packs/My Awesome Megapack/fail.mp4" }], performers: [], tags: [] }] } }
          })
        });
      }

      if (query.includes("RunBuild")) {
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ data: { runPluginTask: "job-fail-99" } })
        });
      }
      return route.continue();
    });

    // Mock WebSocket in the browser before navigation
    await page.addInitScript(() => {
      class MockWebSocket {
        constructor(url, protocols) {
          this.url = url;
          this.protocols = protocols;
          window.__mockWsInstance = this;
          setTimeout(() => {
            if (this.onopen) this.onopen();
          }, 10);
        }
        send(data) {
          window.__mockWsSent = window.__mockWsSent || [];
          window.__mockWsSent.push(JSON.parse(data));
        }
        close() {
          if (this.onclose) this.onclose();
        }
      }
      window.WebSocket = MockWebSocket;
    });

    await page.goto("http://localhost:9999/plugins/empornium-megapack/review.html?scenes=99&mode=megapack");
    // Seed-dir field starts EMPTY — set it so the build gate passes.
    await page.locator("#output-dir").fill("C:\\Packs");
    await page.locator("#btn-build").click();

    // Verify WebSocket subscription was initiated
    await expect.poll(() => page.evaluate(() => (window.__mockWsSent || []).length)).toBeGreaterThanOrEqual(2);

    // Simulate progress message via WebSocket
    await page.evaluate(() => {
      const ws = window.__mockWsInstance;
      ws.onmessage({
        data: JSON.stringify({
          type: "next",
          payload: {
            data: {
              jobsSubscribe: {
                job: {
                  id: "job-fail-99",
                  status: "RUNNING",
                  progress: 0.65,
                  error: null
                }
              }
            }
          }
        })
      });
    });

    await expect(page.locator("#status-text")).toContainText("Running BuildMegapack: 65%");
    await expect(page.locator("#progress-bar")).toHaveAttribute("style", /width:\s*65%/);

    // Simulate failure message via WebSocket
    await page.evaluate(() => {
      const ws = window.__mockWsInstance;
      ws.onmessage({
        data: JSON.stringify({
          type: "next",
          payload: {
            data: {
              jobsSubscribe: {
                job: {
                  id: "job-fail-99",
                  status: "FAILED",
                  progress: 0.65,
                  error: "vcsi binary not found"
                }
              }
            }
          }
        })
      });
    });

    await expect(page.locator("#status-text")).toContainText("Task BuildMegapack FAILED: vcsi binary not found");
  });

});
