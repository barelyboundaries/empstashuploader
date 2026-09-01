import { test, expect } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

function setupStaticMocks(page) {
  // Mock Stash host scene page
  page.route("**/scenes*", async (route) => {
    return route.fulfill({
      status: 200,
      contentType: "text/html",
      body: `
        <!DOCTYPE html>
        <html>
        <head>
          <link rel="stylesheet" href="http://localhost:9999/plugins/empornium-megapack/style.css">
        </head>
        <body>
          <div class="btn-toolbar">
            <button class="btn btn-secondary">Filter</button>
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
      `
    });
  });

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


test.describe("Milestone 3 Challenger 2: Full End-to-End User Journey & Fault Injection", () => {

  test("1. Full User Journey: Toolbar Click -> Modal -> Query -> Reorder -> Probe -> Move -> Build -> WS Stream -> Copy BBCode", async ({ page }) => {
    setupStaticMocks(page);

    const mockScenes = [
      {
        id: 101,
        title: "Alpha Scene 🌸 (Ultra HD)",
        details: "Detailed scene info 1",
        date: "2026-01-15",
        paths: { screenshot: "http://localhost:9999/preview/101.jpg", preview: "" },
        files: [{ id: 1001, path: "C:\\Media\\Alpha.mp4", size: 1024000, height: 2160, width: 3840, duration: 3600, video_codec: "hevc" }],
        performers: [{ id: 1, name: "Performer One 💖" }],
        tags: [{ id: 1, name: "4K" }, { id: 2, name: "VR" }],
        studio: { id: 10, name: "Studio Alpha" }
      },
      {
        id: 102,
        title: "Beta Scene 🚀",
        details: "Detailed scene info 2",
        date: "2026-02-20",
        paths: { screenshot: "http://localhost:9999/preview/102.jpg", preview: "" },
        files: [{ id: 1002, path: "C:\\Media\\Beta.mp4", size: 2048000, height: 1080, width: 1920, duration: 1800, video_codec: "h264" }],
        performers: [{ id: 2, name: "Performer Two" }],
        tags: [{ id: 1, name: "4K" }],
        studio: { id: 10, name: "Studio Alpha" }
      }
    ];

    let probeDispatched = false;
    let moveDispatched = false;
    let buildDispatched = false;
    let buildPayloadCaptured = null;

    // Read-only destination pre-check mocks (collision-free): without these
    // the discovery query/probe fall through to the network and hit a real
    // Stash on :9999, aborting the consolidation before moveFiles.
    await page.route("**/api/fs/exists", async (route) => {
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
      const postData = route.request().postDataJSON();
      const query = postData?.query || "";

      if (query.includes("FindScenes")) {
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ data: { findScenes: { scenes: mockScenes } } })
        });
      }

      if (query.includes("FindDestinationCollisions")) {
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ data: { findScenes: { scenes: [] } } })
        });
      }

      if (query.includes("RunProbe")) {
        probeDispatched = true;
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ data: { runPluginTask: "job-probe-77" } })
        });
      }

      if (query.includes("MoveFiles")) {
        moveDispatched = true;
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ data: { moveFiles: true } })
        });
      }

      if (query.includes("RunBuild")) {
        buildDispatched = true;
        const payloadArg = postData?.variables?.args?.find(a => a.key === "payload");
        if (payloadArg) {
          buildPayloadCaptured = payloadArg.value?.str;
        }
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ data: { runPluginTask: "job-build-88" } })
        });
      }

      return route.fallback();
    });

    // Provide WebSocket mock in browser context
    await page.addInitScript(() => {
      class MockWebSocket {
        constructor(url, protocols) {
          this.url = url;
          this.protocols = protocols;
          window.__mockWs = this;
          setTimeout(() => {
            if (this.onopen) this.onopen();
          }, 15);
        }
        send(data) {
          window.__mockWsSent = window.__mockWsSent || [];
          const parsed = JSON.parse(data);
          window.__mockWsSent.push(parsed);

          if (parsed.type === "subscribe") {
            setTimeout(() => {
              if (this.onmessage) {
                this.onmessage({
                  data: JSON.stringify({
                    type: "next",
                    payload: {
                      data: {
                        jobsSubscribe: {
                          job: {
                            id: "job-build-88",
                            status: "RUNNING",
                            progress: 0.65,
                            error: null
                          }
                        }
                      }
                    }
                  })
                });
              }
            }, 50);

            setTimeout(() => {
              if (this.onmessage) {
                this.onmessage({
                  data: JSON.stringify({
                    type: "next",
                    payload: {
                      data: {
                        jobsSubscribe: {
                          job: {
                            id: "job-build-88",
                            status: "FINISHED",
                            progress: 1.0,
                            error: null
                          }
                        }
                      }
                    }
                  })
                });
              }
            }, 120);
          }
        }
        close() {
          if (this.onclose) this.onclose();
        }
      }
      window.WebSocket = MockWebSocket;
    });

    // Navigate to Stash scene page
    await page.goto("http://localhost:9999/scenes");

    // 1. Toolbar button click
    const btn = page.locator("#empornium-megapack-btn");
    await expect(btn).toBeVisible();
    await btn.click();

    // 2. Modal iframe mounted
    const modal = page.locator("#empornium-megapack-modal");
    await expect(modal).toBeVisible();
    
    const frame = modal;
    const firstCard = frame.locator(".scene-card").first();
    await firstCard.waitFor({ timeout: 5000 });

    // Verify 2 scenes loaded
    const cards = frame.locator(".scene-card");
    await expect(cards).toHaveCount(2);
    await expect(cards.first()).toContainText("Alpha Scene 🌸 (Ultra HD)");
    await expect(cards.nth(1)).toContainText("Beta Scene 🚀");

    // Verify BBCode preview contains performer and scene details
    const bbcodeBox = frame.locator("#bbcode-preview");
    await expect(bbcodeBox).toContainText("Alpha Scene 🌸 (Ultra HD)");
    await expect(bbcodeBox).toContainText("Performer One 💖");

    // The seed-dir field starts EMPTY (no machine-path default in the release
    // audit build) — set it before probe/consolidate/build.
    await frame.locator("#output-dir").fill("C:\\Packs");

    // 3. Trigger Probe Files
    const probeBtn = frame.locator("#btn-probe");
    await probeBtn.click();
    expect(probeDispatched).toBe(true);

    // 4. Trigger File Consolidation
    page.on("dialog", async (dialog) => {
      await dialog.accept();
    });
    const consolidateBtn = frame.locator("#btn-consolidate");
    await consolidateBtn.click();
    // The consolidation flow now runs the read-only destination pre-check
    // (collision query + fs probe) before moveFiles — poll for the mutation.
    await expect.poll(() => moveDispatched).toBe(true);

    // 5. Trigger Build Megapack
    await frame.locator("#opt-upload-previews").check();
    const buildBtn = frame.locator("#btn-build");
    await buildBtn.click();
    expect(buildDispatched).toBe(true);
    expect(buildPayloadCaptured).toBeTruthy();
    const buildPayload = JSON.parse(buildPayloadCaptured);
    expect(buildPayload.scenes).toHaveLength(2);
    expect(buildPayload.scenes[0].height).toBe(2160);
    expect(buildPayload.scenes[0].duration).toBe(3600);
    expect(buildPayload.scenes[0].video_codec).toBe("hevc");
    expect(buildPayload.scenes[0].date).toBe("2026-01-15");
    expect(buildPayload.scenes[0].studio).toBe("Studio Alpha");

    // 6. Verify Progress bar and Artifact Summary box via live WS subscription
    const artifactBox = frame.locator("#artifact-summary");
    await expect(artifactBox).toBeVisible({ timeout: 8000 });
    await expect(artifactBox).toContainText("Build Complete!");
    await expect(frame.locator("#artifact-details")).toContainText(".torrent");
    await expect(frame.locator("#artifact-details")).toContainText("_manifest.json");

    // 7. Test Copy BBCode button
    await page.context().grantPermissions(['clipboard-read', 'clipboard-write']);
    const copyBtn = frame.locator("#btn-copy-bbcode");
    await copyBtn.click();
    await expect(copyBtn).toContainText("Copied!");
  });

  test("2. Fault Injection: Abrupt WebSocket Termination -> Graceful HTTP Polling Fallback", async ({ page }) => {
    setupStaticMocks(page);

    let jobQueryCount = 0;

    // Build pre-flight (todo 7 of staged-wizard-inplace-seed): the
    // authoritative on-disk probe must succeed or the build is blocked
    // fail-closed before dispatch. Fixture moved under the seed dir
    // (C:\Packs) — the old C:\Videos path is now gated as missing.
    await page.route("**/api/fs/exists", async (route) => {
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
      const postData = route.request().postDataJSON();
      const query = postData?.query || "";

      if (query.includes("FindScenes")) {
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            data: {
              findScenes: {
                scenes: [
                  {
                    id: 301,
                    title: "Socket Drop Scene",
                    files: [{ id: 3001, path: "C:\\Packs\\Drop.mp4" }],
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
          body: JSON.stringify({ data: { runPluginTask: "job-ws-fault-99" } })
        });
      }

      if (query.includes("FindJob")) {
        jobQueryCount++;
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            data: {
              findJob: {
                id: "job-ws-fault-99",
                status: "FINISHED",
                progress: 1.0,
                error: null
              }
            }
          })
        });
      }

      return route.fallback();
    });

    // Provide a faulty WebSocket class that triggers onerror on connection
    await page.addInitScript(() => {
      class FailingWebSocket {
        constructor(url, protocols) {
          this.url = url;
          this.protocols = protocols;
          setTimeout(() => {
            if (this.onerror) this.onerror(new Event("error"));
          }, 10);
        }
        send() {}
        close() {}
      }
      window.WebSocket = FailingWebSocket;
    });

    await page.goto("http://localhost:9999/plugins/empornium-megapack/review.html?scenes=301");
    await page.locator("#output-dir").fill("C:\\Packs");
    await page.waitForSelector(".scene-card");

    const buildBtn = page.locator("#btn-build");
    await buildBtn.click();

    // Verify status text transitions through polling and reaches completion
    await expect(page.locator("#status-text")).toBeVisible();
    await expect(page.locator("#artifact-summary")).toBeVisible({ timeout: 10000 });
    await expect(page.locator("#artifact-summary")).toContainText("Build Complete!");
    expect(jobQueryCount).toBeGreaterThanOrEqual(1);
  });

  test("3. Fault Injection: GraphQL Mutation Errors and Failed Job Status Handling", async ({ page }) => {
    setupStaticMocks(page);

    // Build pre-flight (todo 7 of staged-wizard-inplace-seed): the
    // authoritative on-disk probe must succeed or the build is blocked
    // fail-closed before dispatch. Fixture moved under the seed dir
    // (C:\Packs) — the old C:/Media path is now gated as missing.
    await page.route("**/api/fs/exists", async (route) => {
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
      const postData = route.request().postDataJSON();
      const query = postData?.query || "";

      if (query.includes("FindScenes")) {
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            data: {
              findScenes: {
                scenes: [
                  {
                    id: 401,
                    title: "Failure Test Scene",
                    files: [{ id: 4001, path: "C:/Packs/fault_scene.mp4" }],
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
          body: JSON.stringify({ data: { runPluginTask: "job-failed-55" } })
        });
      }

      if (query.includes("FindJob")) {
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            data: {
              findJob: {
                id: "job-failed-55",
                status: "FAILED",
                progress: 0.2,
                error: "Disk full or permission denied during build"
              }
            }
          })
        });
      }

      return route.fallback();
    });

    // Provide failing WebSocket
    await page.addInitScript(() => {
      class FailingWebSocket {
        constructor() {
          setTimeout(() => { if (this.onerror) this.onerror(new Event("error")); }, 10);
        }
        send() {}
        close() {}
      }
      window.WebSocket = FailingWebSocket;
    });

    await page.goto("http://localhost:9999/plugins/empornium-megapack/review.html?scenes=401");
    await page.locator("#output-dir").fill("C:\\Packs");
    await page.waitForSelector(".scene-card");

    const buildBtn = page.locator("#btn-build");
    await buildBtn.click();

    // Verify error state in status text
    const statusText = page.locator("#status-text");
    await expect(statusText).toBeVisible();
    await expect(statusText).toContainText("FAILED: Disk full or permission denied during build", { timeout: 10000 });
  });

  test("4. Unicode and Special Characters in Form Inputs Sync Dynamically", async ({ page }) => {
    setupStaticMocks(page);

    await page.route("**/graphql", async (route) => {
      const postData = route.request().postDataJSON();
      if (postData?.query?.includes("FindScenes")) {
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            data: {
              findScenes: {
                scenes: [
                  {
                    id: 501,
                    title: "Unicode Test: 初音ミク 🌟 (Japanese / Cyrillic / Arabic: مرحبا)",
                    files: [{ id: 5001, path: "C:\\Videos\\Miku.mp4" }],
                    performers: [{ id: 10, name: "初音ミク" }],
                    tags: [{ id: 20, name: "Vocaloid" }]
                  }
                ]
              }
            }
          })
        });
      }
      return route.fallback();
    });

    await page.goto("http://localhost:9999/plugins/empornium-megapack/review.html?scenes=501");
    await page.waitForSelector(".scene-card");

    const titleInput = page.locator("#pack-title");
    await titleInput.fill("Mega Megapack 💖 2026 [Special Edition] /\\:?*");

    const notesInput = page.locator("#pack-notes");
    await notesInput.fill("Emoji notes: 🚀✨ and quotes: \"Special Release\"");

    const bbcodePreview = page.locator("#bbcode-preview");
    await expect(bbcodePreview).toContainText("Mega Megapack 💖 2026 [Special Edition] /\\:?*");
    await expect(bbcodePreview).toContainText("初音ミク");
    await expect(bbcodePreview).toContainText("Emoji notes: 🚀✨");
  });

  test("5. Modal Header Close and Cross-Origin postMessage Dismissal", async ({ page }) => {
    setupStaticMocks(page);

    await page.setContent(`
      <!DOCTYPE html>
      <html>
      <head>
        <link rel="stylesheet" href="http://localhost:9999/plugins/empornium-megapack/style.css">
      </head>
      <body>
        <div class="btn-toolbar"></div>
        <div class="scene-card" data-scene-id="101">
          <input type="checkbox" checked />
        </div>
        <script src="http://localhost:9999/plugins/empornium-megapack/main.js"></script>
      </body>
      </html>
    `);

    const triggerBtn = page.locator("#empornium-megapack-btn");
    await triggerBtn.click();

    const modal = page.locator("#empornium-megapack-modal");
    await expect(modal).toBeVisible();

    // Trigger EMPORNIUM_CLOSE_MODAL postMessage
    await page.evaluate(() => {
      window.postMessage({ type: "EMPORNIUM_CLOSE_MODAL" }, "*");
    });

    await expect(modal).toHaveCount(0);
  });

  test("6. Pre-move Collision Gate: Duplicate scene basenames block consolidateFiles before moveFiles mutation", async ({ page }) => {
    setupStaticMocks(page);

    const collidingScenes = [
      {
        id: 601,
        title: "Scene 601 in Folder A",
        paths: {},
        files: [{ id: 6001, path: "C:\\FolderA\\duplicate_name.mp4" }],
        performers: [],
        tags: []
      },
      {
        id: 602,
        title: "Scene 602 in Folder B",
        paths: {},
        files: [{ id: 6002, path: "D:\\FolderB\\duplicate_name.mp4" }],
        performers: [],
        tags: []
      }
    ];

    let moveFilesCalled = false;

    await page.route("**/graphql", async (route) => {
      const postData = route.request().postDataJSON();
      const query = postData?.query || "";

      if (query.includes("FindScenes")) {
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ data: { findScenes: { scenes: collidingScenes } } })
        });
      }

      if (query.includes("MoveFiles")) {
        moveFilesCalled = true;
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ data: { moveFiles: true } })
        });
      }

      return route.fallback();
    });

    let alertMessage = "";
    page.on("dialog", async (dialog) => {
      alertMessage = dialog.message();
      await dialog.accept();
    });

    await page.goto("http://localhost:9999/plugins/empornium-megapack/review.html?scenes=601,602");
    await page.locator("#output-dir").fill("C:\\Packs");
    await page.waitForSelector(".scene-card");

    const cards = page.locator(".scene-card");
    await expect(cards).toHaveCount(2);

    // Verify collision banner is visible and consolidate button is disabled
    const banner = page.locator("#collision-banner");
    await expect(banner).toBeVisible();

    const consolidateBtn = page.locator("#btn-consolidate");
    await expect(consolidateBtn).toBeDisabled();

    // Verify the backstop blocks the mutation even when consolidateFiles is
    // invoked directly, bypassing the disabled button. Assert the export exists
    // first: without this, a missing export silently no-ops and the
    // moveFilesCalled assertion below passes vacuously.
    const exported = await page.evaluate(() => typeof window.consolidateFiles === "function");
    expect(exported).toBe(true);

    await page.evaluate(() => window.consolidateFiles());

    expect(moveFilesCalled).toBe(false);

    // The backstop reports via showStatus (not alert). Asserting the message
    // distinguishes a real collision block from an unrelated early return,
    // e.g. an empty destination directory.
    const statusText = await page.locator("#status-text").innerText();
    expect(statusText).toContain("Basename collision detected");
    expect(statusText).toContain("duplicate_name.mp4");
  });
});

