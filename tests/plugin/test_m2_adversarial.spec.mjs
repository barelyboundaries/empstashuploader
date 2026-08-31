import { test, expect } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

function setupMocks(page) {
  // Stash static plugin assets
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


test.describe("DeepSeek Megapack Frontend - Adversarial Stress & Verification Suite", () => {

  test("ADV-1: DOM Injection across all layout containers & dynamic container appearance", async ({ page }) => {
    setupMocks(page);

    const containers = [
      { name: "btn-toolbar", html: `<div class="btn-toolbar"><button>Default Action</button></div>` },
      { name: "selection-actions", html: `<div class="selection-actions"><span>Selected</span></div>` },
      { name: "filter-container", html: `<div class="filter-container"><input type="search" /></div>` },
      { name: "nav.navbar", html: `<nav class="navbar"><a class="navbar-brand">Stash</a></nav>` },
    ];

    for (const c of containers) {
      await page.route("http://localhost:9999/scenes", (route) => {
        return route.fulfill({
          status: 200,
          contentType: "text/html",
          body: `
            <!DOCTYPE html>
            <html>
            <head><link rel="stylesheet" href="http://localhost:9999/plugins/deepseek-megapack/style.css"></head>
            <body>
              <div id="wrapper">${c.html}</div>
              <script src="http://localhost:9999/plugins/deepseek-megapack/main.js"></script>
            </body>
            </html>
          `
        });
      });

      await page.goto("http://localhost:9999/scenes");
      const btn = page.locator("#deepseek-megapack-btn");
      await expect(btn).toBeVisible();
      await expect(btn).toHaveCount(1);
    }

    // Dynamic Container Insertion (delayed DOM mount after script load)
    await page.route("http://localhost:9999/dynamic", (route) => {
      return route.fulfill({
        status: 200,
        contentType: "text/html",
        body: `
          <!DOCTYPE html>
          <html>
          <head><link rel="stylesheet" href="http://localhost:9999/plugins/deepseek-megapack/style.css"></head>
          <body>
            <div id="dynamic-host"></div>
            <script src="http://localhost:9999/plugins/deepseek-megapack/main.js"></script>
          </body>
          </html>
        `
      });
    });

    await page.goto("http://localhost:9999/dynamic");

    // Initially no button because no matching container exists
    await expect(page.locator("#deepseek-megapack-btn")).toHaveCount(0);

    // Dynamically insert .btn-toolbar into #dynamic-host
    await page.evaluate(() => {
      const tb = document.createElement("div");
      tb.className = "btn-toolbar";
      tb.innerHTML = `<button class="btn btn-info">Dynamic Action</button>`;
      document.getElementById("dynamic-host").appendChild(tb);
    });

    // MutationObserver should detect and inject button immediately
    const dynBtn = page.locator("#deepseek-megapack-btn");
    await expect(dynBtn).toBeVisible({ timeout: 2000 });
    await expect(dynBtn).toHaveCount(1);

    // Multiple candidate containers in DOM -> ensure ONLY ONE button is created
    await page.evaluate(() => {
      const nav = document.createElement("nav");
      nav.className = "navbar";
      document.body.appendChild(nav);
      const sel = document.createElement("div");
      sel.className = "selection-actions";
      document.body.appendChild(sel);
    });

    await page.waitForTimeout(100);
    await expect(page.locator("#deepseek-megapack-btn")).toHaveCount(1);
  });

  test("ADV-2: Mutation churn & rapid click spamming resilience", async ({ page }) => {
    setupMocks(page);

    await page.route("http://localhost:9999/churn", (route) => {
      return route.fulfill({
        status: 200,
        contentType: "text/html",
        body: `
          <!DOCTYPE html>
          <html>
          <head><link rel="stylesheet" href="http://localhost:9999/plugins/deepseek-megapack/style.css"></head>
          <body>
            <div id="app-root">
              <div class="btn-toolbar"></div>
              <div class="scene-card" data-scene-id="123">
                <input type="checkbox" checked />
              </div>
            </div>
            <script src="http://localhost:9999/plugins/deepseek-megapack/main.js"></script>
          </body>
          </html>
        `
      });
    });

    await page.goto("http://localhost:9999/churn");

    // Simulate 40 rapid SPA view mutations / container re-creations
    await page.evaluate(() => {
      const root = document.getElementById("app-root");
      for (let i = 0; i < 40; i++) {
        const tb = root.querySelector(".btn-toolbar");
        if (tb) tb.remove();
        const newTb = document.createElement("div");
        newTb.className = "btn-toolbar";
        root.appendChild(newTb);
      }
    });

    await page.waitForTimeout(150);
    const triggerBtn = page.locator("#deepseek-megapack-btn");
    await expect(triggerBtn).toBeVisible();
    await expect(triggerBtn).toHaveCount(1);

    // Rapid click spamming: click 15 times in rapid succession
    for (let i = 0; i < 15; i++) {
      await triggerBtn.click({ force: true });
    }

    // Only 1 modal overlay should exist in DOM
    const modal = page.locator("#deepseek-megapack-modal");
    await expect(modal).toBeVisible();
    await expect(modal).toHaveCount(1);

    // Close and reopen 10 times to verify no memory / DOM leak
    for (let i = 0; i < 10; i++) {
      await page.keyboard.press("Escape");
      await expect(modal).toHaveCount(0);
      await triggerBtn.click();
      await expect(modal).toHaveCount(1);
    }
    await page.keyboard.press("Escape");
    await expect(modal).toHaveCount(0);
  });

  test("ADV-3: Scene ID extraction matrix across diverse Stash DOM structures & fallbacks", async ({ page }) => {
    setupMocks(page);

    await page.route("http://localhost:9999/selection-test", (route) => {
      return route.fulfill({
        status: 200,
        contentType: "text/html",
        body: `
          <!DOCTYPE html>
          <html>
          <head><link rel="stylesheet" href="http://localhost:9999/plugins/deepseek-megapack/style.css"></head>
          <body>
            <div class="btn-toolbar"></div>
            <div id="test-grid">
              <!-- 1. Checkbox with explicit numeric value -->
              <div class="card">
                <input type="checkbox" class="card-check" value="101" checked />
              </div>

              <!-- 2. Scene card with data-scene-id and checkbox with default value='on' -->
              <div class="scene-card" data-scene-id="102">
                <input type="checkbox" value="on" checked />
              </div>

              <!-- 3. Table row with data-id -->
              <table>
                <tr class="scene-row" data-id="103">
                  <td><input type="checkbox" checked /></td>
                </tr>
                <!-- 4. Table row with anchor link /scenes/104 -->
                <tr>
                  <td>
                    <input type="checkbox" checked />
                    <a href="/scenes/104">Scene 104</a>
                  </td>
                </tr>
              </table>

              <!-- 5. Wall item with search-item-check and anchor link -->
              <div class="wall-item">
                <input type="checkbox" class="search-item-check" checked />
                <a href="/scenes/105/edit">Scene 105</a>
              </div>

              <!-- 6. Unchecked item (should NOT be included) -->
              <div class="scene-card" data-scene-id="999">
                <input type="checkbox" />
              </div>

              <!-- 7. Duplicate selection referencing scene 101 (should be deduplicated) -->
              <div class="scene-card" data-scene-id="101">
                <input type="checkbox" checked />
              </div>
            </div>
            <script src="http://localhost:9999/plugins/deepseek-megapack/main.js"></script>
          </body>
          </html>
        `
      });
    });

    await page.goto("http://localhost:9999/selection-test");

    const triggerBtn = page.locator("#deepseek-megapack-btn");
    await triggerBtn.click();

    const modal = page.locator("#deepseek-megapack-modal");
    await expect(modal).toBeVisible();
    await expect(modal.locator(".deepseek-badge")).toContainText("5 scene(s) selected");
    const extractedIds = await page.evaluate(() => window._deepseekSceneIds);
    expect(extractedIds).toEqual(expect.arrayContaining([101, 102, 103, 104, 105]));


    await page.keyboard.press("Escape");

    // Single scene URL fallback test: /scenes/777
    await page.route("http://localhost:9999/scenes/777", (route) => {
      return route.fulfill({
        status: 200,
        contentType: "text/html",
        body: `
          <!DOCTYPE html>
          <html>
          <head><link rel="stylesheet" href="http://localhost:9999/plugins/deepseek-megapack/style.css"></head>
          <body>
            <div class="btn-toolbar"></div>
            <div class="scene-header">Single Scene View 777</div>
            <script src="http://localhost:9999/plugins/deepseek-megapack/main.js"></script>
          </body>
          </html>
        `
      });
    });

    await page.goto("http://localhost:9999/scenes/777");
    await page.locator("#deepseek-megapack-btn").click();
    await expect(page.locator("#deepseek-megapack-modal")).toBeVisible();
    const fallbackIds = await page.evaluate(() => window._deepseekSceneIds);
    expect(fallbackIds).toEqual([777]);



    // Zero selection alert handling test: on /scenes with no checkboxes checked
    await page.route("http://localhost:9999/scenes-empty", (route) => {
      return route.fulfill({
        status: 200,
        contentType: "text/html",
        body: `
          <!DOCTYPE html>
          <html>
          <head><link rel="stylesheet" href="http://localhost:9999/plugins/deepseek-megapack/style.css"></head>
          <body>
            <div class="btn-toolbar"></div>
            <div>No selection</div>
            <script src="http://localhost:9999/plugins/deepseek-megapack/main.js"></script>
          </body>
          </html>
        `
      });
    });

    let alertMessage = null;
    page.once("dialog", async (dialog) => {
      alertMessage = dialog.message();
      await dialog.accept();
    });

    await page.goto("http://localhost:9999/scenes-empty");
    await page.locator("#deepseek-megapack-btn").click();
    expect(alertMessage).toBe("Please select at least one scene to build a megapack.");
    await expect(page.locator("#deepseek-megapack-modal")).toHaveCount(0);
  });

  test("ADV-4: Modal Backdrop Click, Header Close & Cross-Frame postMessage", async ({ page }) => {
    setupMocks(page);

    // Route GraphQL FindScenes for the iframe
    await page.route("**/graphql", async (route) => {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          data: {
            findScenes: {
              scenes: [{ id: 1, title: "Test Scene", paths: {}, files: [], performers: [], tags: [] }]
            }
          }
        })
      });
    });

    await page.route("http://localhost:9999/modal-lifecycle", (route) => {
      return route.fulfill({
        status: 200,
        contentType: "text/html",
        body: `
          <!DOCTYPE html>
          <html>
          <head><link rel="stylesheet" href="http://localhost:9999/plugins/deepseek-megapack/style.css"></head>
          <body>
            <div class="btn-toolbar"></div>
            <div class="scene-card" data-scene-id="1">
              <input type="checkbox" checked />
            </div>
            <script src="http://localhost:9999/plugins/deepseek-megapack/main.js"></script>
          </body>
          </html>
        `
      });
    });

    await page.goto("http://localhost:9999/modal-lifecycle");

    const triggerBtn = page.locator("#deepseek-megapack-btn");
    await triggerBtn.click();

    const overlay = page.locator("#deepseek-megapack-modal");
    const container = page.locator(".deepseek-modal-container");
    await expect(overlay).toBeVisible();

    // 1. Click inside modal container -> modal should REMAIN OPEN
    await container.click({ position: { x: 20, y: 20 } });
    await expect(overlay).toBeVisible();

    // 2. Click outside container on backdrop -> modal should CLOSE
    await overlay.click({ position: { x: 5, y: 5 } });
    await expect(overlay).toHaveCount(0);

    // 3. Re-open and verify cross-frame close from inside iframe via btn-header-close
    await triggerBtn.click();
    const headerCloseBtn = overlay.locator("#btn-header-close");
    await expect(headerCloseBtn).toBeVisible({ timeout: 5000 });
    await headerCloseBtn.click();

    // Verify modal overlay was removed
    await expect(overlay).toHaveCount(0);
  });

  test("ADV-5: Scene metadata edge cases, Drag-and-Drop reordering & BBCode synchronization", async ({ page }) => {
    setupMocks(page);

    // Mock scenes with varied/null/empty attributes
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
                    id: 10,
                    title: "First Scene (Normal)",
                    date: "2026-03-01",
                    paths: { screenshot: "http://localhost:9999/shot1.jpg" },
                    files: [{ id: 100, path: "C:/Media/Scene1.mp4", size: 1000 }],
                    performers: [{ id: 1, name: "Performer One" }],
                    tags: [{ id: 1, name: "VR" }]
                  },
                  {
                    id: 20,
                    title: null, // Null title fallback
                    date: null,  // Null date fallback
                    paths: null, // Null paths fallback
                    files: [],   // Missing files fallback
                    performers: [], // Empty performers
                    tags: []        // Empty tags
                  },
                  {
                    id: 30,
                    title: "Third Scene (Multi Performer)",
                    date: "2026-03-03",
                    paths: { preview: "http://localhost:9999/prev3.mp4" },
                    files: [{ id: 300, path: "C:/Media/Scene3.mp4", size: 3000 }],
                    performers: [{ id: 1, name: "Performer One" }, { id: 2, name: "Performer Two" }],
                    tags: [{ id: 2, name: "HD" }]
                  }
                ]
              }
            }
          })
        });
      }
      return route.continue();
    });

    await page.goto("http://localhost:9999/plugins/deepseek-megapack/review.html?scenes=10,20,30");

    const cards = page.locator(".scene-card");
    await expect(cards).toHaveCount(3);
    await expect(cards.nth(0)).toContainText("First Scene (Normal)");
    await expect(cards.nth(1)).toContainText("Untitled Scene");
    await expect(cards.nth(1)).toContainText("👤 Unknown");
    await expect(cards.nth(1)).toContainText("📅 Unknown date");
    await expect(cards.nth(1)).toContainText("📁 No file path");
    await expect(cards.nth(2)).toContainText("Third Scene (Multi Performer)");

    // Verify initial BBCode structure
    const bbcode = page.locator("#bbcode-preview");
    await expect(bbcode).toContainText("1. [b]First Scene (Normal) [/b] (Performer One)");
    await expect(bbcode).toContainText("2. [b]Scene [/b]");
    await expect(bbcode).toContainText("3. [b]Third Scene (Multi Performer) [/b] (Performer One, Performer Two)");

    // Simulate drag and drop reordering: Drag Scene 3 (index 2) before Scene 1 (index 0)
    await page.evaluate(() => {
      const container = document.getElementById("scene-list");
      const cardList = [...container.querySelectorAll(".scene-card")];
      // Move card 2 before card 0
      container.insertBefore(cardList[2], cardList[0]);
      // Trigger dragend to invoke reorderScenes()
      cardList[2].dispatchEvent(new Event("dragend"));
    });

    // Check that BBCode preview updated order immediately: Scene 3 is now #1
    await expect(bbcode).toContainText("1. [b]Third Scene (Multi Performer) [/b] (Performer One, Performer Two)");
    await expect(bbcode).toContainText("2. [b]First Scene (Normal) [/b] (Performer One)");
    await expect(bbcode).toContainText("3. [b]Scene [/b]");
  });

  test("ADV-6: WebSocket Disconnect / Error & Seamless HTTP Polling Fallback", async ({ page }) => {
    setupMocks(page);

    let pollAttempts = 0;

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
            data: {
              findScenes: {
                scenes: [
                  { id: 55, title: "Resilience Test Scene 1", paths: {}, files: [{ id: 555, path: "C:/Packs/My Awesome Megapack/s1.mp4" }], performers: [], tags: [] },
                  { id: 56, title: "Resilience Test Scene 2", paths: {}, files: [{ id: 556, path: "C:/Packs/My Awesome Megapack/s2.mp4" }], performers: [], tags: [] }
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
          body: JSON.stringify({ data: { runPluginTask: "job-resilience-555" } })
        });
      }

      if (query.includes("FindJob") || query.includes("findJob")) {
        pollAttempts++;
        const progress = pollAttempts < 3 ? 0.33 * pollAttempts : 1.0;
        const status = pollAttempts < 3 ? "RUNNING" : "FINISHED";
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            data: {
              findJob: {
                id: "job-resilience-555",
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

    // Mock WebSocket to simulate immediate connection failure (onerror)
    await page.addInitScript(() => {
      class FailingWebSocket {
        constructor(url, protocols) {
          window.__wsConstructed = true;
          setTimeout(() => {
            if (this.onerror) {
              this.onerror(new Event("error"));
            }
          }, 20);
        }
        send() {}
        close() {}
      }
      window.WebSocket = FailingWebSocket;
    });

    await page.goto("http://localhost:9999/plugins/deepseek-megapack/review.html?scenes=55,56&mode=megapack");
    // Seed-dir field starts EMPTY (no machine-path default) — set it so the
    // build gate passes against the C:\Packs fixtures. The pack title also
    // starts EMPTY; fill it so the artifact panel names the torrent as before.
    await page.locator("#output-dir").fill("C:\\Packs");
    await page.locator("#pack-title").fill("My Awesome Megapack");

    // Click build button
    await page.locator("#btn-build").click();

    // Verify WebSocket was attempted and failed
    await expect.poll(() => page.evaluate(() => window.__wsConstructed)).toBe(true);

    // Verify HTTP polling took over automatically and completed the job
    const summaryBox = page.locator("#artifact-summary");
    await expect(summaryBox).toBeVisible({ timeout: 8000 });
    await expect(page.locator("#status-text")).toContainText("BuildMegapack completed successfully!");
    await expect(page.locator("#artifact-details")).toContainText("My Awesome Megapack.torrent");
  });

  test("ADV-7: MoveFiles confirmation cancellation vs execution", async ({ page }) => {
    setupMocks(page);

    let moveFilesInvoked = false;

    // Read-only destination pre-check mocks (collision-free): without these
    // the discovery query/probe fall through to the network and hit a real
    // Stash on :9999, aborting the consolidation before moveFiles.
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

      if (query.includes("FindScenes")) {
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            data: {
              findScenes: {
                scenes: [
                  { id: 1, title: "Scene One", paths: {}, files: [{ id: 101, path: "C:/Media/s1.mp4" }], performers: [], tags: [] },
                  { id: 2, title: "Scene Two", paths: {}, files: [{ id: 102, path: "C:/Media/s2.mp4" }], performers: [], tags: [] }
                ]
              }
            }
          })
        });
      }

      if (query.includes("FindDestinationCollisions")) {
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ data: { findScenes: { scenes: [] } } })
        });
      }

      if (query.includes("MoveFiles") || query.includes("moveFiles")) {
        moveFilesInvoked = true;
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ data: { moveFiles: true } })
        });
      }

      return route.fallback();
    });

    // 1. Test Dialog Dismiss (User clicks "Cancel")
    page.once("dialog", async (dialog) => {
      // Consolidation destination = the seed-dir field value (in-place
      // seeding) — no pack-title subfolder.
      expect(dialog.message()).toContain("Move/consolidate 2 files into C:\\Packs?");
      await dialog.dismiss();
    });

    await page.goto("http://localhost:9999/plugins/deepseek-megapack/review.html?scenes=1,2&mode=megapack");
    // Seed-dir field starts EMPTY — set it so the confirm dialog names C:\Packs.
    await page.locator("#output-dir").fill("C:\\Packs");
    await page.locator("#btn-consolidate").click();
    // Wait for the flow to reach its cancel terminal before asserting no
    // mutation fired (the pre-check runs async before the confirm).
    await expect(page.locator("#status-text")).toContainText("Consolidation cancelled");
    expect(moveFilesInvoked).toBe(false);

    // 2. Test Dialog Accept (User clicks "OK")
    page.once("dialog", async (dialog) => {
      await dialog.accept();
    });

    await page.locator("#btn-consolidate").click();
    // The pre-check runs async before moveFiles — poll for the mutation.
    await expect.poll(() => moveFilesInvoked).toBe(true);
    await expect(page.locator("#status-text")).toContainText("Files moved successfully!");
  });

  test("ADV-8: GraphQL Server Error Handling during Task Dispatch", async ({ page }) => {
    setupMocks(page);

    // Build pre-flight (todo 7 of staged-wizard-inplace-seed): the
    // authoritative on-disk probe must succeed or the build is blocked
    // fail-closed before dispatch. Fixture moved under the seed dir
    // (C:\Packs) — the old C:/Media path is now gated as missing.
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
            data: {
              findScenes: {
                scenes: [{ id: 1, title: "Error Scene", paths: {}, files: [{ id: 101, path: "C:/Packs/s1.mp4" }], performers: [], tags: [] }]
              }
            }
          })
        });
      }

      if (query.includes("RunBuild")) {
        // Return GraphQL Error payload
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            errors: [{ message: "Stash Plugin Service Unavailable: Task execution denied" }]
          })
        });
      }

      return route.continue();
    });

    await page.goto("http://localhost:9999/plugins/deepseek-megapack/review.html?scenes=1");
    // Seed-dir field starts EMPTY — set it so the build gate passes.
    await page.locator("#output-dir").fill("C:\\Packs");
    await page.locator("#btn-build").click();

    // Verify error banner is displayed with danger color
    const statusText = page.locator("#status-text");
    await expect(statusText).toBeVisible();
    await expect(statusText).toContainText("Build trigger failed: Stash Plugin Service Unavailable: Task execution denied");
  });

});
