import { test, expect } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';
import { execSync } from 'node:child_process';


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
}

test.describe("DeepSeek Megapack - 66 Scenes POST+Token Transport & Chunking Flow", () => {

  test("1. 66 scenes selected -> POST /api/token -> IDs travel by token, never in a URL", async ({ page }) => {
    setupMocks(page);

    let postedTokenPayload = null;

    // Intercept token creation POST
    await page.route("**/api/token*", async (route) => {
      if (route.request().method() === "POST") {
        postedTokenPayload = JSON.parse(route.request().postData() || "{}");
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ token: "tok-66-test-uuid-abcdef123456" })
        });
      }
      return route.continue();
    });

    // Build 66 checkboxes in DOM
    const checkboxesHtml = Array.from({ length: 66 }, (_, i) => `
      <div class="scene-card" data-scene-id="${i + 1}">
        <input type="checkbox" class="card-check" checked value="${i + 1}" />
        <span>Scene ${i + 1}</span>
      </div>
    `).join("\n");

    await page.route("http://localhost:9999/test-scenes-66*", async (route) => {
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
            <div class="btn-toolbar"></div>
            <div class="scenes-list">
              ${checkboxesHtml}
            </div>
            <script src="http://localhost:9999/plugins/empornium-megapack/main.js"></script>
          </body>
          </html>
        `
      });
    });

    // Record every request URL so we can prove no scene IDs were smuggled
    // through a query string. The token transport exists precisely because 66
    // IDs in a URL exceed browser length limits.
    const requestUrls = [];
    page.on("request", (req) => {
      requestUrls.push(req.url());
    });

    await page.goto("http://localhost:9999/test-scenes-66");


    const triggerBtn = page.locator("#empornium-megapack-btn");
    await expect(triggerBtn).toBeVisible();
    await triggerBtn.click();

    // Verify POST was called with all 66 scene IDs
    expect(postedTokenPayload).toBeTruthy();
    expect(postedTokenPayload.sceneIds).toHaveLength(66);
    expect(postedTokenPayload.sceneIds[0]).toBe(1);
    expect(postedTokenPayload.sceneIds[65]).toBe(66);

    const modal = page.locator("#empornium-megapack-modal");
    await expect(modal).toBeVisible();
    await expect(modal.locator(".empornium-badge")).toContainText("66 scene(s) selected");

    // The review layer must RESOLVE the 66 IDs by GETting the token back.
    // Asserting window._emporniumToken instead would be near-worthless: main.js
    // sets it before handing the token to initEmporniumReview, so it stays
    // populated even if the review layer never receives or uses the token.
    await expect
      .poll(() => requestUrls.some((u) => u.includes("/api/token/tok-66-test-uuid-abcdef123456")))
      .toBe(true);

    // No request may carry scene IDs in its query string -- this is the
    // property the token transport guarantees, and the reason it exists.
    const leaked = requestUrls.filter((u) => u.includes("scenes="));
    expect(leaked).toEqual([]);
  });



  test("2. Review loads 66 scenes via 3 GraphQL batches (25, 25, 16) and renders 66 cards", async ({ page }) => {
    setupMocks(page);

    const graphqlBatches = [];

    // Mock GET /api/token
    await page.route("**/api/token/tok-66-active*", async (route) => {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ sceneIds: Array.from({ length: 66 }, (_, i) => i + 1) })
      });
    });

    // Mock chunked GraphQL FindScenes
    await page.route("**/graphql", async (route) => {
      const postData = JSON.parse(route.request().postData() || "{}");
      if (postData.query && postData.query.includes("FindScenes")) {
        const ids = postData.variables?.ids || [];
        graphqlBatches.push(ids);

        const returnedScenes = ids.map((id) => ({
          id: id,
          title: `Scene #${id} Mega`,
          date: "2026-04-01",
          paths: { screenshot: `http://localhost:9999/shots/${id}.jpg` },
          files: [{ id: id * 10, path: `C:/Media/scene_${id}.mp4`, size: 5000000 + id }],
          performers: [{ id: 1, name: "Star Performer" }],
          tags: [{ id: 1, name: "HD" }],
          studio: { id: 1, name: "MegaStudio" }
        }));

        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            data: {
              findScenes: {
                scenes: returnedScenes
              }
            }
          })
        });
      }
      return route.continue();
    });

    await page.goto("http://localhost:9999/plugins/empornium-megapack/review.html?token=tok-66-active");

    // Assert that 3 batches were sent (25, 25, 16)
    await expect.poll(() => graphqlBatches.length).toBe(3);
    expect(graphqlBatches[0]).toHaveLength(25);
    expect(graphqlBatches[1]).toHaveLength(25);
    expect(graphqlBatches[2]).toHaveLength(16);

    // Assert all 66 scene cards are rendered in DOM
    const sceneCards = page.locator(".scene-card");
    await expect(sceneCards).toHaveCount(66);
    await expect(sceneCards.first()).toContainText("Scene #1 Mega");
    await expect(sceneCards.last()).toContainText("Scene #66 Mega");

    // Assert BBCode reflects 66 scenes
    const bbcode = page.locator("#bbcode-preview");
    await expect(bbcode).toContainText("[b]Total Scenes:[/b] 66");
    await expect(bbcode).toContainText("66. [b]Scene #66 Mega [/b]");
  });


  test("3. Legacy ?scenes=1,2 query parameter backwards compatibility still works", async ({ page }) => {
    setupMocks(page);

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
                  { id: 1, title: "Legacy Scene 1", files: [], performers: [], tags: [] },
                  { id: 2, title: "Legacy Scene 2", files: [], performers: [], tags: [] }
                ]
              }
            }
          })
        });
      }
      return route.continue();
    });

    await page.goto("http://localhost:9999/plugins/empornium-megapack/review.html?scenes=1,2");

    const cards = page.locator(".scene-card");
    await expect(cards).toHaveCount(2);
    await expect(cards.first()).toContainText("Legacy Scene 1");
    await expect(cards.nth(1)).toContainText("Legacy Scene 2");
  });

  test("4. Token 404 / resolution failure shows actionable retry error UI", async ({ page }) => {
    setupMocks(page);

    // Mock 404 for invalid token
    await page.route("**/api/token/missing-token-xyz*", async (route) => {
      return route.fulfill({
        status: 404,
        contentType: "application/json",
        body: JSON.stringify({ detail: "Token not found or expired" })
      });
    });

    await page.goto("http://localhost:9999/plugins/empornium-megapack/review.html?token=missing-token-xyz");

    const loadingState = page.locator("#loading-state");
    await expect(loadingState).toContainText("Failed to load scenes");
    await expect(loadingState).toContainText("missing-token-xyz");

    const retryBtn = loadingState.locator("#btn-retry-load");
    await expect(retryBtn).toBeVisible();
    await expect(retryBtn).toContainText("Retry");
  });

  test("5. empornium-megapack.yml CSP validation (localhost/127.0.0.1:9941, no wildcards)", async () => {
    const ymlPath = path.resolve("plugin/empornium-megapack.yml");
    const ymlContent = fs.readFileSync(ymlPath, "utf8");

    // Parse via Python yaml module
    const pythonOut = execSync(
      `python -c "import yaml, json; print(json.dumps(yaml.safe_load(open(r'${ymlPath}'))))"`,
      { encoding: "utf8" }
    );
    const doc = JSON.parse(pythonOut);

    expect(doc).toBeTruthy();
    expect(doc.ui).toBeTruthy();
    expect(doc.ui.csp).toBeTruthy();
    expect(doc.ui.csp["connect-src"]).toBeTruthy();

    const connectSrc = doc.ui.csp["connect-src"];
    expect(connectSrc).toContain("http://127.0.0.1:9941");
    expect(connectSrc).toContain("http://localhost:9941");

    // Guardrail: Ensure no wildcard * in csp section or yml
    const cspJson = JSON.stringify(doc.ui.csp);
    expect(cspJson).not.toContain("*");
    expect(ymlContent).not.toContain("*");
  });

  test("6. Blocked iframe contentDocument fallback UI in main.js", async ({ page }) => {
    setupMocks(page);

    // Mock token creation to succeed
    await page.route("**/api/token*", async (route) => {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ token: "tok-fallback-test" })
      });
    });

    // Provide a mock Stash page
    await page.setContent(`
      <!DOCTYPE html>
      <html>
      <head>
        <link rel="stylesheet" href="http://localhost:9999/plugins/empornium-megapack/style.css">
      </head>
      <body>
        <div class="btn-toolbar"></div>
        <div class="scenes-list">
          <div class="scene-card" data-scene-id="10">
            <input type="checkbox" class="card-check" checked value="10" />
            <span>Scene 10</span>
          </div>
        </div>
        <script src="http://localhost:9999/plugins/empornium-megapack/main.js"></script>
      </body>
      </html>
    `);

    // Mock failure fetching review.html
    await page.route("**/review.html*", async (route) => {
      return route.fulfill({ status: 500, body: "Server Error" });
    });

    const triggerBtn = page.locator("#empornium-megapack-btn");
    await expect(triggerBtn).toBeVisible();
    await triggerBtn.click();

    const modal = page.locator("#empornium-megapack-modal");
    await expect(modal).toBeVisible();

    const fallback = modal.locator(".empornium-blocked-fallback");
    await expect(fallback).toBeVisible();
    await expect(fallback).toContainText("Content Loading Blocked");
    await expect(fallback.locator(".empornium-retry-btn")).toBeVisible();
  });

});

