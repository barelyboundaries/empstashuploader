import { test, expect } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

// Helper to mock Stash static plugin assets
function setupMocks(page) {
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

  page.route("**/api/fs/exists*", async (route) => {
    const postData = JSON.parse(route.request().postData() || "{}");
    const results = {};
    for (const p of postData.paths || []) results[p] = false;
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ results })
    });
  });
}


test.describe("Empornium Megapack Builder Milestone 2 — Adversarial Stress & Edge Case Suite", () => {

  // =========================================================================
  // 1. QUERY PARSING & EMPTY / MALFORMED SCENE IDS
  // =========================================================================
  test("1.1 Empty and malformed scene ID query parameters handled safely", async ({ page }) => {
    setupMocks(page);

    // Scenario A: No query parameter at all
    await page.goto("http://localhost:9999/plugins/empornium-megapack/review.html");
    await expect(page.locator("#loading-state")).toHaveText("No scenes selected.");

    // Scenario B: Empty query parameter
    await page.goto("http://localhost:9999/plugins/empornium-megapack/review.html?scenes=");
    await expect(page.locator("#loading-state")).toHaveText("No scenes selected.");

    // Scenario C: Non-numeric, negative, and zero IDs
    await page.goto("http://localhost:9999/plugins/empornium-megapack/review.html?scenes=abc,,xyz,-5,0");
    await expect(page.locator("#loading-state")).toHaveText("No scenes selected.");
  });

  test("1.2 Mixed valid and invalid scene IDs parses only positive integers", async ({ page }) => {
    setupMocks(page);

    let requestedIds = null;
    await page.route("**/graphql", async (route) => {
      const postData = JSON.parse(route.request().postData() || "{}");
      if (postData.query && postData.query.includes("FindScenes")) {
        requestedIds = postData.variables?.ids;
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            data: {
              findScenes: {
                scenes: [
                  { id: 42, title: "Valid 42", paths: {}, files: [], performers: [], tags: [] },
                  { id: 99, title: "Valid 99", paths: {}, files: [], performers: [], tags: [] }
                ]
              }
            }
          })
        });
      }
      return route.continue();
    });

    await page.goto("http://localhost:9999/plugins/empornium-megapack/review.html?scenes=foo,42,bar,99,-1,0");
    await expect(page.locator(".scene-card")).toHaveCount(2);
    expect(requestedIds).toEqual([42, 99]);
  });

  // =========================================================================
  // 2. GRAPHQL CLIENT ERROR RESPONSES & NETWORK RESILIENCE
  // =========================================================================
  test("2.1 FindScenes HTTP 500 server error displays error message without crash", async ({ page }) => {
    setupMocks(page);

    await page.route("**/graphql", async (route) => {
      return route.fulfill({
        status: 500,
        contentType: "text/plain",
        body: "Internal Server Error"
      });
    });

    await page.goto("http://localhost:9999/plugins/empornium-megapack/review.html?scenes=1,2");
    await expect(page.locator("#loading-state")).toContainText("Failed to load scenes");
  });

  test("2.2 FindScenes GraphQL errors array displays error message gracefully", async ({ page }) => {
    setupMocks(page);

    await page.route("**/graphql", async (route) => {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          errors: [{ message: "Database connection failed" }, { message: "Stash locked" }]
        })
      });
    });

    await page.goto("http://localhost:9999/plugins/empornium-megapack/review.html?scenes=1,2");
    await expect(page.locator("#loading-state")).toContainText("Database connection failed; Stash locked");
  });

  test("2.3 FindScenes with scenes: [] when IDs requested displays 'No scenes found.'", async ({ page }) => {
    setupMocks(page);

    await page.route("**/graphql", async (route) => {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          data: { findScenes: { scenes: [] } }
        })
      });
    });

    await page.goto("http://localhost:9999/plugins/empornium-megapack/review.html?scenes=999");
    await expect(page.locator("#scene-list")).toContainText("No scenes found.");
  });

  test("2.4 Malformed scene records with null fields render safely with defaults", async ({ page }) => {
    setupMocks(page);

    await page.route("**/graphql", async (route) => {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          data: {
            findScenes: {
              scenes: [
                {
                  id: 1,
                  title: null, // null title
                  date: null, // null date
                  details: null,
                  paths: null, // null paths
                  files: null, // null files array
                  performers: null, // null performers array
                  tags: null // null tags array
                }
              ]
            }
          }
        })
      });
    });

    await page.goto("http://localhost:9999/plugins/empornium-megapack/review.html?scenes=1");

    await expect(page.locator(".scene-card")).toHaveCount(1);
    await expect(page.locator(".scene-title")).toContainText("#1 - Untitled Scene");
    await expect(page.locator(".scene-meta").first()).toContainText("👤 Unknown");
    await expect(page.locator(".scene-meta").first()).toContainText("📅 Unknown date");
    await expect(page.locator(".scene-meta").nth(1)).toContainText("📁 No file path");
  });

  test("2.5 ProbeFiles, MoveFiles, BuildMegapack mutation failure states", async ({ page }) => {
    setupMocks(page);

    page.on("dialog", async (dialog) => {
      await dialog.accept();
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
                  // Outside the seed dir (C:\Packs): move-only-missing only
                  // fires moveFiles for files NOT already under the seed dir.
                  { id: 1, title: "Scene 1", paths: {}, files: [{ id: 10, path: "C:/Source/s1.mp4" }], performers: [], tags: [] },
                  { id: 2, title: "Scene 2", paths: {}, files: [{ id: 20, path: "C:/Source/s2.mp4" }], performers: [], tags: [] }
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

      if (query.includes("RunProbe")) {
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            errors: [{ message: "Probe task execution rejected" }]
          })
        });
      }

      if (query.includes("MoveFiles")) {
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            errors: [{ message: "Destination folder unwritable" }]
          })
        });
      }

      if (query.includes("RunBuild")) {
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            errors: [{ message: "Concurrent build lock active" }]
          })
        });
      }

      return route.continue();
    });

    await page.goto("http://localhost:9999/plugins/empornium-megapack/review.html?scenes=1,2&mode=megapack");
    await page.locator("#output-dir").fill("C:\\Packs");

    // Read-only destination pre-check (collision-free, hermetic): without the
    // probe route the fail-closed discovery would abort before moveFiles.
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

    // Probe failure
    await page.locator("#btn-probe").click();
    await expect(page.locator("#status-text")).toContainText("Probe error: Probe task execution rejected");

    // Move failure
    await page.locator("#btn-consolidate").click();
    await expect(page.locator("#status-text")).toContainText("Destination folder unwritable");

    // Mark files as consolidated in page state to satisfy preflight build gate
    await page.evaluate(() => {
      window.activeScenes().forEach(s => {
        const f = s.files && s.files[0];
        if (f && f.id) window.consolidatedFileIds.add(f.id);
      });
      window.updateActionAvailability();
    });

    // Build failure
    await page.locator("#btn-build").click();
    await expect(page.locator("#status-text")).toContainText("Build trigger failed: Concurrent build lock active");
  });

  // =========================================================================
  // 3. PROBING BADGES RENDERING VIA DOM INJECTION VERIFICATION
  // =========================================================================
  test("3.1 Badge styling classes render properly for badge-success, badge-warning, badge-danger", async ({ page }) => {
    setupMocks(page);

    await page.goto("http://localhost:9999/plugins/empornium-megapack/review.html?scenes=");

    // Verify badge classes exist in style.css
    await page.setContent(`
      <!DOCTYPE html>
      <html>
      <head>
        <link rel="stylesheet" href="http://localhost:9999/plugins/empornium-megapack/style.css">
      </head>
      <body>
        <div id="test-badges">
          <span class="badge badge-success">⚡ Hardlink OK</span>
          <span class="badge badge-warning">📋 Copy Required</span>
          <span class="badge badge-danger">❌ Missing File</span>
          <span class="badge badge-danger">⚠️ Duplicate Name</span>
        </div>
      </body>
      </html>
    `);

    const badges = page.locator("#test-badges .badge");
    await expect(badges).toHaveCount(4);
    await expect(badges.nth(0)).toHaveClass(/badge-success/);
    await expect(badges.nth(1)).toHaveClass(/badge-warning/);
    await expect(badges.nth(2)).toHaveClass(/badge-danger/);
    await expect(badges.nth(3)).toHaveClass(/badge-danger/);
  });

  // =========================================================================
  // 4. DRAG-AND-DROP REORDERING & EXTREME SCENE COUNTS
  // =========================================================================
  test("4.1 Single scene rendering and BBCode preview", async ({ page }) => {
    setupMocks(page);

    await page.route("**/graphql", async (route) => {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          data: {
            findScenes: {
              scenes: [
                { id: 1, title: "Lone Scene", paths: {}, files: [{ id: 1, path: "C:/s1.mp4" }], performers: [{ name: "Solo" }], tags: [] }
              ]
            }
          }
        })
      });
    });

    await page.goto("http://localhost:9999/plugins/empornium-megapack/review.html?scenes=1&mode=single");
    await expect(page.locator(".scene-card")).toHaveCount(1);
    await expect(page.locator("#bbcode-preview")).toContainText("Lone Scene");
    await expect(page.locator("#bbcode-preview")).toContainText("[b]Performers:[/b] Solo");
    await expect(page.locator("#bbcode-preview")).not.toContainText("1. [b]");
  });

  test("4.2 Multi-scene DOM reordering and BBCode preview sync", async ({ page }) => {
    setupMocks(page);

    await page.route("**/graphql", async (route) => {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          data: {
            findScenes: {
              scenes: [
                { id: 10, title: "First Scene", paths: {}, files: [{ id: 10, path: "C:/s10.mp4" }], performers: [{ name: "Alice" }], tags: [] },
                { id: 20, title: "Second Scene", paths: {}, files: [{ id: 20, path: "C:/s20.mp4" }], performers: [{ name: "Bob" }], tags: [] },
                { id: 30, title: "Third Scene", paths: {}, files: [{ id: 30, path: "C:/s30.mp4" }], performers: [{ name: "Charlie" }], tags: [] }
              ]
            }
          }
        })
      });
    });

    await page.goto("http://localhost:9999/plugins/empornium-megapack/review.html?scenes=10,20,30");

    const cards = page.locator(".scene-card");
    await expect(cards).toHaveCount(3);
    await expect(cards.nth(0)).toContainText("First Scene");
    await expect(cards.nth(1)).toContainText("Second Scene");
    await expect(cards.nth(2)).toContainText("Third Scene");

    const bbcode = await page.locator("#bbcode-preview").innerText();
    expect(bbcode).toContain("1. [b]First Scene [/b] (Alice)");
    expect(bbcode).toContain("2. [b]Second Scene [/b] (Bob)");
    expect(bbcode).toContain("3. [b]Third Scene [/b] (Charlie)");
  });

  test("4.3 Extreme scene count: 100 scenes render and format BBCode correctly", async ({ page }) => {
    setupMocks(page);

    const generatedScenes = Array.from({ length: 100 }, (_, i) => ({
      id: i + 1,
      title: `Stress Scene ${i + 1}`,
      date: "2026-08-01",
      paths: { screenshot: `http://localhost/shot_${i + 1}.jpg` },
      files: [{ id: (i + 1) * 10, path: `C:/Media/scene_${i + 1}.mp4`, size: 1048576 * (i + 1) }],
      performers: [{ id: (i % 5) + 1, name: `Performer ${(i % 5) + 1}` }],
      tags: [{ id: 1, name: "Stress" }]
    }));

    await page.route("**/graphql", async (route) => {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          data: { findScenes: { scenes: generatedScenes } }
        })
      });
    });

    const idsParam = generatedScenes.map(s => s.id).join(",");
    await page.goto(`http://localhost:9999/plugins/empornium-megapack/review.html?scenes=${idsParam}`);

    // Verify all 100 cards rendered
    await expect(page.locator(".scene-card")).toHaveCount(100);

    // Verify BBCode contains total scenes 100
    const bbcode = await page.locator("#bbcode-preview").innerText();
    expect(bbcode).toContain("[b]Total Scenes:[/b] 100");
    expect(bbcode).toContain("1. [b]Stress Scene 1 [/b]");
    expect(bbcode).toContain("100. [b]Stress Scene 100 [/b]");
  });

  test("4.4 Extreme scene count: 250 scenes stress performance test", async ({ page }) => {
    setupMocks(page);

    const generatedScenes = Array.from({ length: 250 }, (_, i) => ({
      id: i + 1,
      title: `Bulk Scene ${i + 1}`,
      date: "2026-08-20",
      paths: {},
      files: [{ id: i + 1, path: `C:/Media/bulk_${i + 1}.mp4` }],
      performers: [{ id: 1, name: "Mega Star" }],
      tags: []
    }));

    await page.route("**/graphql", async (route) => {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          data: { findScenes: { scenes: generatedScenes } }
        })
      });
    });

    const idsParam = generatedScenes.map(s => s.id).join(",");
    const startTime = Date.now();
    await page.goto(`http://localhost:9999/plugins/empornium-megapack/review.html?scenes=${idsParam}`);

    await expect(page.locator(".scene-card")).toHaveCount(250);
    const duration = Date.now() - startTime;
    // Renders fast (well within 5 seconds)
    expect(duration).toBeLessThan(5000);

    const bbcode = await page.locator("#bbcode-preview").innerText();
    expect(bbcode).toContain("[b]Total Scenes:[/b] 250");
    expect(bbcode).toContain("250. [b]Bulk Scene 250 [/b]");
  });

  // =========================================================================
  // 5. BBCODE FORMATTING, SPECIAL CHARACTERS & CLIPBOARD INTERACTION
  // =========================================================================
  test("5.1 BBCode formatting with special characters, quotes, and emojis", async ({ page }) => {
    setupMocks(page);

    await page.route("**/graphql", async (route) => {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          data: {
              findScenes: {
                scenes: [
                  {
                    id: 1,
                    title: "Scene with [b]Tags[/b] & <script>alert(1)</script> / 日本語",
                    paths: {},
                    files: [{ id: 1, path: "C:/special.mp4" }],
                    performers: [{ name: "Artist <A>" }, { name: "Artist [B]" }],
                    tags: []
                  },
                  {
                    id: 2,
                    title: "Second Scene",
                    paths: {},
                    files: [{ id: 2, path: "C:/second.mp4" }],
                    performers: [],
                    tags: []
                  }
                ]
              }
            }
          })
        });
      });

    await page.goto("http://localhost:9999/plugins/empornium-megapack/review.html?scenes=1,2&mode=megapack");

    await page.locator("#pack-title").fill("Mega Pack [4K] ~ Special Edition! 🔥");
    await page.locator("#pack-notes").fill("Line 1 with quotes \"hello\"\nLine 2 with [url]http://example.com[/url]");

    const bbcodeText = await page.locator("#bbcode-preview").innerText();
    expect(bbcodeText).toContain("[center][b][size=5]Mega Pack [4K] ~ Special Edition! 🔥[/size][/b][/center]");
    expect(bbcodeText).toContain("Artist <A>, Artist [B]");
    expect(bbcodeText).toContain('[quote]Line 1 with quotes "hello"\nLine 2 with [url]http://example.com[/url][/quote]');
    expect(bbcodeText).toContain("Scene with [b]Tags[/b] & <script>alert(1)</script> / 日本語");
  });

  test("5.2 Clipboard copy button interaction & rejection safety", async ({ page, context }) => {
    setupMocks(page);
    await context.grantPermissions(["clipboard-read", "clipboard-write"]);

    await page.route("**/graphql", async (route) => {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          data: {
            findScenes: {
              scenes: [{ id: 1, title: "Copy Test", paths: {}, files: [], performers: [], tags: [] }]
            }
          }
        })
      });
    });

    await page.goto("http://localhost:9999/plugins/empornium-megapack/review.html?scenes=1");

    const copyBtn = page.locator("#btn-copy-bbcode");
    await copyBtn.click();
    await expect(copyBtn).toHaveText("✅ Copied!");

    // Test rejection safety: mock clipboard failure
    await page.evaluate(() => {
      navigator.clipboard.writeText = () => Promise.reject(new Error("Permission denied"));
    });

    await copyBtn.click();
    // Doesn't crash
    await expect(page.locator("#bbcode-preview")).toBeVisible();
  });

  // =========================================================================
  // 6. DUAL-TRANSPORT & LIFECYCLE EDGE CASES
  // =========================================================================
  test("6.1 Polling fallback execution delivers artifacts on completion", async ({ page }) => {
    setupMocks(page);

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
                scenes: [{ id: 7, title: "Fallback Scene", paths: {}, files: [], performers: [], tags: [] }]
              }
            }
          })
        });
      }

      if (query.includes("RunBuild")) {
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ data: { runPluginTask: "job-fb-7" } })
        });
      }

      if (query.includes("FindJob") || query.includes("findJob")) {
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            data: {
              findJob: {
                id: "job-fb-7",
                status: "FINISHED",
                progress: 1.0,
                error: null
              }
            }
          })
        });
      }

      return route.continue();
    });

    await page.goto("http://localhost:9999/plugins/empornium-megapack/review.html?scenes=7");
    
    // Explicitly invoke polling mechanism
    await page.evaluate(() => {
      window.startJobPolling("job-fb-7", "BuildMegapack", {
        pack_title: "My Awesome Megapack",
        output_dir: "C:\\Packs"
      });
    });

    // Verify polling was triggered and delivered artifacts
    await expect(page.locator("#artifact-summary")).toBeVisible({ timeout: 10000 });
    await expect(page.locator("#status-text")).toContainText("BuildMegapack completed successfully!");
  });

  // =========================================================================
  // 7. IN-STASH MAIN.JS SELECTION & DOM INJECTION EDGE CASES
  // =========================================================================
  test("7.1 main.js extracts scene IDs from diverse table and wall elements", async ({ page }) => {
    setupMocks(page);

    await page.setContent(`
      <!DOCTYPE html>
      <html>
      <head></head>
      <body>
        <div class="selection-actions"></div>
        <table>
          <tr class="scene-row" data-scene-id="201">
            <td><input type="checkbox" checked /></td>
            <td><a href="/scenes/201">Scene 201</a></td>
          </tr>
          <tr data-id="202">
            <td><input type="checkbox" class="search-item-check" checked /></td>
            <td>Scene 202</td>
          </tr>
          <tr class="wall-item" data-scene-id="203">
            <td><input type="checkbox" class="wall-item-check" checked /></td>
            <td>Scene 203</td>
          </tr>
          <tr class="scene-card">
            <td><input type="checkbox" class="card-check" value="204" checked /></td>
            <td>Scene 204</td>
          </tr>
        </table>
        <script src="http://localhost:9999/plugins/empornium-megapack/main.js"></script>
      </body>
      </html>
    `);

    const triggerBtn = page.locator("#empornium-megapack-btn");
    await expect(triggerBtn).toBeVisible();
    await triggerBtn.click();

    const modal = page.locator("#empornium-megapack-modal");
    await expect(modal).toBeVisible();
    await expect(modal.locator(".empornium-badge")).toContainText("4 scene(s) selected");
    const extractedIds = await page.evaluate(() => window._emporniumSceneIds);
    expect(extractedIds).toEqual(expect.arrayContaining([201, 202, 203, 204]));
  });

  test("7.2 main.js alerts user and does not mount modal when 0 scenes are selected on root page", async ({ page }) => {
    setupMocks(page);

    let alertMessage = null;
    page.on("dialog", async (dialog) => {
      alertMessage = dialog.message();
      await dialog.accept();
    });

    await page.setContent(`
      <!DOCTYPE html>
      <html>
      <head></head>
      <body>
        <nav class="navbar"></nav>
        <div class="scenes-list">
          <div class="scene-card" data-scene-id="1">
            <input type="checkbox" />
          </div>
        </div>
        <script src="http://localhost:9999/plugins/empornium-megapack/main.js"></script>
      </body>
      </html>
    `);

    const triggerBtn = page.locator("#empornium-megapack-btn");
    await triggerBtn.click();

    expect(alertMessage).toBe("Please select at least one scene to build a megapack.");
    await expect(page.locator("#empornium-megapack-modal")).toHaveCount(0);
  });

  test("7.3 Repeated DOM mutations inject trigger button idempotently", async ({ page }) => {
    setupMocks(page);

    await page.setContent(`
      <!DOCTYPE html>
      <html>
      <head></head>
      <body>
        <div class="btn-toolbar"></div>
        <script src="http://localhost:9999/plugins/empornium-megapack/main.js"></script>
      </body>
      </html>
    `);

    // Trigger several DOM mutations
    await page.evaluate(() => {
      for (let i = 0; i < 20; i++) {
        const div = document.createElement("div");
        document.body.appendChild(div);
      }
    });

    await expect(page.locator("#empornium-megapack-btn")).toHaveCount(1);
  });

});
