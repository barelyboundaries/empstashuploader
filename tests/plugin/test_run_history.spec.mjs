import { test, expect } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

// Change C2 Test Suite: Frontend History View & Modal Safety
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

  page.route("**/health", async (route) => {
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ ok: true, status: "connected", version: "0.2.0", scratch_dir: SCRATCH_DIR })
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

  page.route("**/api/tags/vocabulary", async (route) => {
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ tags: ["brunette", "blonde", "hd.1080p", "studio.alpha"] })
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

async function setupStashGraphQL(page, scenes = [mockScene(1, 101, `${SEED_DIR}\\scene1.mp4`, "Scene 1")]) {
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

    return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ data: {} }) });
  });
}

test.describe("Milestone C2: Persistent Run History View & Modal Safety", () => {

  test("1. #btn-history in modal header opens #history-view and fetches GET /api/runs", async ({ page }) => {
    setupAssetRoutes(page);
    await setupStashGraphQL(page);

    let runsRequested = false;
    await page.route("**/api/runs*", async (route) => {
      runsRequested = true;
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify([
          {
            run_id: "run-test-001",
            created_at: 1725450000,
            status: "success",
            mode: "megapack",
            pack_title: "Test Megapack 1",
            title: "Test Megapack 1",
            scene_count: 3,
            torrent_path: "C:\\Packs\\test1.torrent"
          }
        ])
      });
    });

    // Suite invariant: isolate /api/run/**
    await page.route("**/api/run/**", async (route) => route.abort("connectionrefused"));

    await page.goto("http://localhost:9999/plugins/empornium-megapack/review.html?scenes=1&mode=megapack");
    await expect(page.locator(".scene-card")).toHaveCount(1);

    const historyBtn = page.locator("#btn-history");
    await expect(historyBtn).toBeVisible();

    const historyView = page.locator("#history-view");
    await expect(historyView).toBeHidden();

    // Click #btn-history -> opens #history-view
    await historyBtn.click();
    await expect(historyView).toBeVisible();
    expect(runsRequested).toBe(true);

    // Verify run list is populated
    await expect(page.locator("#history-runs-list .history-run-card")).toHaveCount(1);

    // Close history view via #btn-history-close
    const closeBtn = page.locator("#btn-history-close");
    await expect(closeBtn).toBeVisible();
    await closeBtn.click();
    await expect(historyView).toBeHidden();
  });

  test("2. History view displays run cards with badges and empty state when empty", async ({ page }) => {
    setupAssetRoutes(page);
    await setupStashGraphQL(page);

    let emptyResponse = false;
    await page.route("**/api/runs*", async (route) => {
      if (emptyResponse) {
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify([])
        });
      }
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify([
          {
            run_id: "run-alpha-001",
            created_at: 1725451200,
            status: "success",
            mode: "megapack",
            pack_title: "Alpha Pack 2026",
            title: "Alpha Pack 2026",
            scene_count: 8,
            torrent_path: "C:\\Torrents\\alpha.torrent"
          },
          {
            run_id: "run-beta-002",
            created_at: 1725450000,
            status: "failed",
            mode: "single",
            pack_title: "Beta Solo Scene",
            title: "Beta Solo Scene",
            scene_count: 1,
            torrent_path: ""
          }
        ])
      });
    });

    await page.route("**/api/run/**", async (route) => route.abort("connectionrefused"));

    await page.goto("http://localhost:9999/plugins/empornium-megapack/review.html?scenes=1&mode=megapack");
    await page.locator("#btn-history").click();

    // Verify 2 run cards
    const cards = page.locator("#history-runs-list .history-run-card");
    await expect(cards).toHaveCount(2);

    // Card 1 assertions: title, status badge, mode badge, scene count, actions
    const card1 = cards.nth(0);
    await expect(card1.locator(".history-run-title")).toContainText("Alpha Pack 2026");
    await expect(card1.locator(".history-badge-success")).toContainText("success");
    await expect(card1.locator(".history-badge-mode")).toContainText("megapack");
    await expect(card1.locator(".history-run-scenes")).toContainText("8 scene(s)");
    await expect(card1.locator(".btn-history-load")).toBeVisible();
    await expect(card1.locator(".btn-history-delete")).toBeVisible();

    // Card 2 assertions
    const card2 = cards.nth(1);
    await expect(card2.locator(".history-run-title")).toContainText("Beta Solo Scene");
    await expect(card2.locator(".history-badge-failed")).toContainText("failed");
    await expect(card2.locator(".history-badge-mode")).toContainText("single");
    await expect(card2.locator(".history-run-scenes")).toContainText("1 scene(s)");

    // Refresh with empty response to test empty state
    emptyResponse = true;
    await page.locator("#btn-history-refresh").click();
    await expect(page.locator("#history-empty")).toBeVisible();
    await expect(page.locator("#history-empty")).toContainText("No past runs found");
    await expect(cards).toHaveCount(0);
  });

  test("3. Selecting past run fetches GET /api/run/{run_id} and populates #build-console-result with active copy buttons", async ({ page, context }) => {
    await context.grantPermissions(["clipboard-read", "clipboard-write"]);
    setupAssetRoutes(page);
    await setupStashGraphQL(page);

    await page.route("**/api/runs*", async (route) => {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify([
          {
            run_id: "historical-run-42",
            created_at: 1725450000,
            status: "success",
            mode: "megapack",
            pack_title: "Historical Megapack Collection",
            title: "Historical Megapack Collection",
            scene_count: 4,
            torrent_path: "C:\\Torrents\\historical.torrent"
          }
        ])
      });
    });

    let runDetailFetched = false;
    await page.route("**/api/run/**", async (route) => {
      const url = route.request().url();
      if (url.includes("historical-run-42") && route.request().method() === "GET") {
        runDetailFetched = true;
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            found: true,
            result: {
              status: "success",
              task: "BuildMegapack",
              mode: "megapack",
              pack_title: "Historical Megapack Collection",
              title: "Historical Megapack Collection",
              torrent_path: "C:\\Torrents\\historical.torrent",
              manifest_path: "C:\\Torrents\\historical.manifest.json",
              cover_url: "https://hamsterimg.com/i/cover42.jpg",
              tracker_tags: ["brunette", "hd.1080p", "studio.alpha"],
              bbcode: "[b]Historical Megapack Collection[/b]\n[img]https://hamsterimg.com/i/cover42.jpg[/img]",
              uploaded_urls: ["https://hamsterimg.com/i/cover42.jpg"],
              scenes: [1, 2, 3, 4],
              scene_count: 4,
              ready: true
            }
          })
        });
      }
      return route.abort("connectionrefused");
    });

    await page.goto("http://localhost:9999/plugins/empornium-megapack/review.html?scenes=1&mode=megapack");
    await page.locator("#btn-history").click();
    await expect(page.locator("#history-view")).toBeVisible();

    // Click "View result" on the historical run
    const loadBtn = page.locator(".btn-history-load");
    await loadBtn.click();

    await expect(page.locator("#history-view")).toBeHidden();
    expect(runDetailFetched).toBe(true);

    const resultPanel = page.locator("#build-console-result");
    await expect(resultPanel).toBeVisible();

    // Verify fields populated
    await expect(page.locator("#handoff-title")).toContainText("Historical Megapack Collection");
    await expect(page.locator("#handoff-tags")).toContainText("brunette");
    await expect(page.locator("#handoff-torrent")).toContainText("historical.torrent");
    await expect(page.locator("#result-bbcode")).toHaveValue(/Historical Megapack Collection/);

    // Verify un-gated copy buttons are enabled
    const copyTitleBtn = page.locator("#btn-copy-title");
    const copyTagsBtn = page.locator("#btn-copy-tags");
    const copyPathBtn = page.locator("#btn-copy-torrent-path");
    const copyBbcodeBtn = page.locator("#btn-copy-bbcode");
    const copyCoverBtn = page.locator("#btn-copy-cover-url");
    const copyAllBtn = page.locator("#btn-copy-all");

    await expect(copyTitleBtn).toBeEnabled();
    await expect(copyTagsBtn).toBeEnabled();
    await expect(copyPathBtn).toBeEnabled();
    await expect(copyBbcodeBtn).toBeEnabled();
    await expect(copyCoverBtn).toBeEnabled();
    await expect(copyAllBtn).toBeEnabled();

    // Click copy button and assert clipboard text
    await copyTitleBtn.click();
    expect(await page.evaluate(() => navigator.clipboard.readText())).toBe("Historical Megapack Collection");

    await copyTagsBtn.click();
    expect(await page.evaluate(() => navigator.clipboard.readText())).toContain("brunette");

    await copyCoverBtn.click();
    expect(await page.evaluate(() => navigator.clipboard.readText())).toBe("https://hamsterimg.com/i/cover42.jpg");

    await copyAllBtn.click();
    expect(await page.evaluate(() => navigator.clipboard.readText())).toContain("Historical Megapack Collection");
  });

  test("4. Deleting a past run sends DELETE /api/run/{run_id} and refreshes history list", async ({ page }) => {
    setupAssetRoutes(page);
    await setupStashGraphQL(page);

    let runs = [
      {
        run_id: "run-to-delete-99",
        created_at: 1725450000,
        status: "success",
        mode: "megapack",
        pack_title: "Pack to be Deleted",
        title: "Pack to be Deleted",
        scene_count: 2,
        torrent_path: "C:\\Torrents\\delete.torrent"
      }
    ];

    await page.route("**/api/runs*", async (route) => {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(runs)
      });
    });

    let deleteRequested = false;
    await page.route("**/api/run/**", async (route) => {
      const url = route.request().url();
      if (url.includes("run-to-delete-99") && route.request().method() === "DELETE") {
        deleteRequested = true;
        runs = []; // Prune on server
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ status: "ok", run_id: "run-to-delete-99", deleted: true })
        });
      }
      return route.abort("connectionrefused");
    });

    await page.goto("http://localhost:9999/plugins/empornium-megapack/review.html?scenes=1&mode=megapack");
    await page.locator("#btn-history").click();
    await expect(page.locator(".history-run-card")).toHaveCount(1);

    // Accept confirm dialog when Delete is clicked
    page.on("dialog", async (dialog) => {
      expect(dialog.type()).toBe("confirm");
      await dialog.accept();
    });

    const deleteBtn = page.locator(".btn-history-delete");
    await deleteBtn.click();

    // List refreshes to empty state
    await expect(page.locator("#history-empty")).toBeVisible();
    await expect(page.locator(".history-run-card")).toHaveCount(0);
    expect(deleteRequested).toBe(true);
  });

  test("5. Empornium History button in Stash toolbar opens history modal directly without scene selection or token", async ({ page }) => {
    setupAssetRoutes(page);

    let tokenEndpointCalled = false;
    await page.route("**/api/token*", async (route) => {
      tokenEndpointCalled = true;
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ token: "should-not-be-called" })
      });
    });

    let runsFetched = false;
    await page.route("**/api/runs*", async (route) => {
      runsFetched = true;
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify([
          {
            run_id: "history-direct-001",
            created_at: 1725450000,
            status: "success",
            mode: "megapack",
            pack_title: "Direct History Run",
            title: "Direct History Run",
            scene_count: 5,
            torrent_path: "C:\\Torrents\\direct.torrent"
          }
        ])
      });
    });

    await page.route("**/api/run/**", async (route) => route.abort("connectionrefused"));

    // Provide mock Stash toolbar DOM with NO scenes selected
    await page.setContent(`
      <!DOCTYPE html>
      <html>
      <head>
        <link rel="stylesheet" href="http://localhost:9999/plugins/empornium-megapack/style.css">
      </head>
      <body>
        <div class="btn-toolbar">
          <button class="btn btn-primary">Other Stash Tool</button>
        </div>
        <script src="http://localhost:9999/plugins/empornium-megapack/main.js"></script>
      </body>
      </html>
    `);

    // Verify both toolbar buttons were injected
    const uploaderBtn = page.locator("#empornium-megapack-btn");
    const historyBtn = page.locator("#empornium-history-btn");

    await expect(uploaderBtn).toBeVisible();
    await expect(historyBtn).toBeVisible();
    await expect(historyBtn).toContainText("Empornium History");

    // Click Empornium History button -> opens modal directly
    await historyBtn.click();

    const modal = page.locator("#empornium-megapack-modal");
    await expect(modal).toBeVisible();

    // Verify token was NOT requested
    expect(tokenEndpointCalled).toBe(false);

    // Verify history view is immediately opened inside modal and fetched runs
    const historyView = page.locator("#history-view");
    await expect(historyView).toBeVisible();
    await expect(page.locator(".history-run-card")).toHaveCount(1);
    expect(runsFetched).toBe(true);
  });

  test("6. Modal lifecycle safety: backdrop click and Escape key prompt confirmation when busy or with undismissed result", async ({ page }) => {
    setupAssetRoutes(page);
    await setupStashGraphQL(page);

    await page.route("**/api/runs*", async (route) => {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify([])
      });
    });

    await page.route("**/api/run/**", async (route) => route.abort("connectionrefused"));

    // Provide mock Stash DOM with 1 scene selected and load main.js
    await page.setContent(`
      <!DOCTYPE html>
      <html>
      <head>
        <link rel="stylesheet" href="http://localhost:9999/plugins/empornium-megapack/style.css">
      </head>
      <body>
        <div class="btn-toolbar"></div>
        <div class="scenes-list">
          <div class="scene-card" data-scene-id="1">
            <input type="checkbox" checked />
          </div>
        </div>
        <script src="http://localhost:9999/plugins/empornium-megapack/main.js"></script>
      </body>
      </html>
    `);

    const uploaderBtn = page.locator("#empornium-megapack-btn");
    await expect(uploaderBtn).toBeVisible();
    await uploaderBtn.click();

    const modal = page.locator("#empornium-megapack-modal");
    await expect(modal).toBeVisible();
    await expect(modal.locator("#stage-bar")).toBeVisible();

    // 6a. When idle and no result open: _emporniumCanClose() is true -> closes immediately without prompt
    let dialogPrompted = false;
    page.on("dialog", async (dialog) => {
      dialogPrompted = true;
      await dialog.accept();
    });

    // Escape key closes modal cleanly
    await page.keyboard.press("Escape");
    await expect(modal).toHaveCount(0);
    expect(dialogPrompted).toBe(false);

    // 6b. Reopen modal and set uiBusy = true -> backdrop click and Escape require confirmation
    await uploaderBtn.click();
    await expect(modal).toBeVisible();
    await expect(modal.locator("#stage-bar")).toBeVisible();

    await page.evaluate(() => {
      window.setUiBusy(true, "build");
    });

    // Test cancelling confirmation leaves modal open
    let dismissNext = true;
    page.removeAllListeners("dialog");
    page.on("dialog", async (dialog) => {
      dialogPrompted = true;
      expect(dialog.message()).toContain("will be in History");
      if (dismissNext) {
        await dialog.dismiss();
      } else {
        await dialog.accept();
      }
    });

    // Backdrop click with dismiss -> modal stays open
    dialogPrompted = false;
    dismissNext = true;
    await page.click("#empornium-megapack-modal", { position: { x: 5, y: 5 } });
    expect(dialogPrompted).toBe(true);
    await expect(modal).toBeVisible();

    // Escape key with dismiss -> modal stays open
    dialogPrompted = false;
    dismissNext = true;
    await page.keyboard.press("Escape");
    expect(dialogPrompted).toBe(true);
    await expect(modal).toBeVisible();

    // Escape key with accept -> modal closes
    dialogPrompted = false;
    dismissNext = false;
    await page.keyboard.press("Escape");
    expect(dialogPrompted).toBe(true);
    await expect(modal).toHaveCount(0);

    // 6c. Modal safety with completed build result open
    await uploaderBtn.click();
    await expect(modal).toBeVisible();
    await expect(modal.locator("#stage-bar")).toBeVisible();

    await page.evaluate(() => {
      window.setUiBusy(false);
      window.renderBuildResult({
        status: "success",
        pack_title: "Completed Pack Safety Test",
        torrent_path: "C:\\safety.torrent",
        bbcode: "[b]Completed[/b]",
        ready: true
      });
    });

    // Backdrop click with dismiss -> modal stays open
    dialogPrompted = false;
    dismissNext = true;
    await page.click("#empornium-megapack-modal", { position: { x: 5, y: 5 } });
    expect(dialogPrompted).toBe(true);
    await expect(modal).toBeVisible();

    // Header close button (.empornium-modal-close) remains UNCONDITIONAL -> closes immediately without prompt
    dialogPrompted = false;
    const headerCloseBtn = modal.locator(".empornium-modal-close");
    await headerCloseBtn.click();
    await expect(modal).toHaveCount(0);
    expect(dialogPrompted).toBe(false);
  });

});
