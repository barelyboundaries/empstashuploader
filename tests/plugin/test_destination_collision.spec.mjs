import { test, expect } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

// Data-layer tests for destination-collision discovery (Todo 2 of
// consolidate-destination-collision): oshash selection in fetchScenesChunked,
// findDestinationCollisions, pathExistsBatch, nextFreeName.
//
// Mocking discipline: NETWORK LAYER ONLY (page.route("**/graphql") and route
// interception for the backend :9941 endpoint), inspecting postData — exactly
// as test_modal_integration.spec.mjs does. The closure-internal executeGraphQL
// is never stubbed; assertions inspect what goes over the wire, not call
// counts alone.

function serveAssets(page) {
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
// the page boots cleanly and the window-exported data-layer functions can be
// driven directly (established "Window exports for tests and integrations"
// block at the bottom of review.js).
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

test.describe("Empornium Review — destination collision data layer", () => {

  test("oshash is selected in BOTH fetchScenesChunked queries (batch + per-id fallback)", async ({ page }) => {
    serveAssets(page);
    const graphqlBodies = [];
    await page.route("**/graphql", async (route) => {
      const postData = JSON.parse(route.request().postData() || "{}");
      graphqlBodies.push(postData);
      if (postData.query && postData.query.includes("FindScenes") && !postData.query.includes("FindDestinationCollisions")) {
        // Force the schema-mismatch fallback path so the per-id FindScene
        // query is exercised too.
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            errors: [{ message: "Cannot query field \"findScenes\" on type \"Query\". (GRAPHQL_VALIDATION_FAILED)" }]
          })
        });
      }
      if (postData.query && postData.query.includes("FindScene(")) {
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            data: {
              findScene: {
                id: 7,
                title: "Fallback Scene",
                files: [{ id: 77, path: "C:/Media/fallback.mp4", size: 1, oshash: "abc123" }]
              }
            }
          })
        });
      }
      return route.continue();
    });

    await page.goto("http://localhost:9999/plugins/empornium-megapack/review.html?scenes=7");

    // No DOM wait here: the forced batch error makes the fallback return scene 7,
    // whose card renders asynchronously — a scene-card count assertion would race
    // it. The wire-body assertions below are deterministic: this evaluate-driven
    // fetchScenesChunked cycle pushes BOTH query bodies before it resolves.
    const result = await page.evaluate(() => window.fetchScenesChunked([7]));
    expect(result).toHaveLength(1);
    expect(result[0].id).toBe(7);

    const batchQuery = graphqlBodies.find((b) => b.query && b.query.includes("FindScenes"));
    const fallbackQuery = graphqlBodies.find((b) => b.query && b.query.includes("FindScene("));
    expect(batchQuery, "batch FindScenes query was sent").toBeTruthy();
    expect(fallbackQuery, "per-id FindScene fallback query was sent").toBeTruthy();
    // Assert on the wire bodies, not call counts.
    expect(batchQuery.query).toContain("oshash");
    expect(fallbackQuery.query).toContain("oshash");
  });

  test("findDestinationCollisions returns only true same-folder basename collisions, excluding own file id", async ({ page }) => {
    serveAssets(page);
    let collisionRequest = null;
    await page.route("**/graphql", async (route) => {
      const postData = JSON.parse(route.request().postData() || "{}");
      if (postData.query && postData.query.includes("FindDestinationCollisions")) {
        collisionRequest = postData;
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            data: {
              findScenes: {
                scenes: [
                  { id: 50, title: "True collision", files: [{ id: 9001, path: "C:\\Packs\\Foo.MP4", size: 10, duration: 60, height: 1080, width: 1920, video_codec: "h264", oshash: "deadbeef" }] },
                  { id: 51, title: "Sibling folder", files: [{ id: 9002, path: "C:\\Packs-old\\foo.mp4", size: 10, duration: 60, height: 1080, width: 1920, video_codec: "h264", oshash: "aa" }] },
                  { id: 52, title: "Subfolder", files: [{ id: 9003, path: "C:\\Packs\\sub\\foo.mp4", size: 10, duration: 60, height: 1080, width: 1920, video_codec: "h264", oshash: "bb" }] },
                  { id: 53, title: "Different basename", files: [{ id: 9004, path: "C:\\Packs\\bar.mp4", size: 10, duration: 60, height: 1080, width: 1920, video_codec: "h264", oshash: "cc" }] },
                  { id: 54, title: "Own file", files: [{ id: 999, path: "C:\\Source\\foo.mp4", size: 10, duration: 60, height: 1080, width: 1920, video_codec: "h264", oshash: "dd" }] }
                ]
              }
            }
          })
        });
      }
      if (postData.query && postData.query.includes("FindScenes")) {
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ data: { findScenes: { scenes: [] } } })
        });
      }
      return route.continue();
    });

    await openReviewPage(page);

    const fileItems = [{ id: 999, path: "C:\\Source\\foo.mp4", sceneId: 54, sceneTitle: "Own file" }];
    const collisions = await page.evaluate(
      ([items, dest]) => window.findDestinationCollisions(items, dest),
      [fileItems, "C:\\Packs\\"]
    );

    // Wire-level assertions: INCLUDES filter + destination variable + per_page cap.
    expect(collisionRequest).toBeTruthy();
    expect(collisionRequest.query).toContain("INCLUDES");
    expect(collisionRequest.query).toContain("per_page: 1000");
    expect(collisionRequest.variables.path).toBe("C:\\Packs\\");
    expect(collisionRequest.query).toContain("oshash");

    // Only the true same-folder, case-insensitive basename collision survives.
    expect(collisions).toHaveLength(1);
    expect(collisions[0].sceneId).toBe(50);
    expect(collisions[0].file.id).toBe(9001);
    expect(collisions[0].file.oshash).toBe("deadbeef");
  });

  test("findDestinationCollisions rejects when Stash returns an error", async ({ page }) => {
    serveAssets(page);
    await page.route("**/graphql", async (route) => {
      const postData = JSON.parse(route.request().postData() || "{}");
      if (postData.query && postData.query.includes("FindDestinationCollisions")) {
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ errors: [{ message: "boom" }] })
        });
      }
      if (postData.query && postData.query.includes("FindScenes")) {
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ data: { findScenes: { scenes: [] } } })
        });
      }
      return route.continue();
    });

    await openReviewPage(page);

    const outcome = await page.evaluate(([items, dest]) =>
      window.findDestinationCollisions(items, dest).then(
        () => "resolved",
        (err) => `rejected: ${err.message}`
      ),
      [[{ id: 1, path: "C:\\Source\\foo.mp4" }], "C:\\Packs\\"]
    );
    expect(outcome).toContain("rejected");
    expect(outcome).toContain("boom");
  });

  test("pathExistsBatch chunks requests at 100 paths and merges results", async ({ page }) => {
    serveAssets(page);
    await openReviewPage(page);

    const probeBodies = [];
    await page.route("**/api/fs/exists", async (route) => {
      const postData = JSON.parse(route.request().postData() || "{}");
      probeBodies.push(postData);
      const results = {};
      for (const p of postData.paths || []) results[p] = false;
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ results })
      });
    });

    const paths = Array.from({ length: 150 }, (_, i) => `C:\\Packs\\file${i}.mp4`);
    const results = await page.evaluate((p) => window.pathExistsBatch(p), paths);

    expect(Object.keys(results)).toHaveLength(150);
    expect(results["C:\\Packs\\file0.mp4"]).toBe(false);
    // Chunking observed on the wire: 100 + 50, never more than 100 per request.
    expect(probeBodies).toHaveLength(2);
    expect(probeBodies[0].paths).toHaveLength(100);
    expect(probeBodies[1].paths).toHaveLength(50);
  });

  test("pathExistsBatch rejects (fail-closed) when the probe returns non-200", async ({ page }) => {
    serveAssets(page);
    await openReviewPage(page);

    await page.route("**/api/fs/exists", async (route) => {
      return route.fulfill({ status: 500, contentType: "application/json", body: "{}" });
    });

    const outcome = await page.evaluate(() =>
      window.pathExistsBatch(["C:\\Packs\\foo.mp4"]).then(
        () => "resolved",
        (err) => `rejected: ${err.message}`
      )
    );
    expect(outcome).toContain("rejected");
  });

  test("nextFreeName skips occupied candidates: foo (1) exists -> foo (2)", async ({ page }) => {
    serveAssets(page);
    await openReviewPage(page);

    const probeBodies = [];
    await page.route("**/api/fs/exists", async (route) => {
      const postData = JSON.parse(route.request().postData() || "{}");
      probeBodies.push(postData);
      const results = {};
      for (const p of postData.paths || []) {
        results[p] = p === "C:\\Packs\\foo (1).mp4";
      }
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ results })
      });
    });

    const name = await page.evaluate(() => window.nextFreeName("C:\\Packs\\", "foo.mp4"));
    expect(name).toBe("C:\\Packs\\foo (2).mp4");

    // The first probed candidate is "stem (1).ext".
    expect(probeBodies.length).toBeGreaterThanOrEqual(1);
    expect(probeBodies[0].paths[0]).toBe("C:\\Packs\\foo (1).mp4");
  });

  test("nextFreeName aborts (fail-closed) when the probe returns non-200", async ({ page }) => {
    serveAssets(page);
    await openReviewPage(page);

    await page.route("**/api/fs/exists", async (route) => {
      return route.fulfill({ status: 503, contentType: "application/json", body: "{}" });
    });

    const outcome = await page.evaluate(() =>
      window.nextFreeName("C:\\Packs\\", "foo.mp4").then(
        () => "resolved",
        (err) => `rejected: ${err.message}`
      )
    );
    expect(outcome).toContain("rejected");
  });

  test("nextFreeName aborts (fail-closed) when the probe fails at the network layer", async ({ page }) => {
    serveAssets(page);
    await openReviewPage(page);

    await page.route("**/api/fs/exists", async (route) => route.abort());

    const outcome = await page.evaluate(() =>
      window.nextFreeName("C:\\Packs\\", "foo.mp4").then(
        () => "resolved",
        (err) => `rejected: ${err && err.message}`
      )
    );
    expect(outcome).toContain("rejected");
  });

});

// ---------------------------------------------------------------------------
// Todo 3: destination-collision resolution DIALOG.
//
// The dialog is opened through the window-exported test seam
// `window.openDestinationCollisionDialog(collisions)` (same export pattern as
// the Todo-2 data functions) so Wave 3's executor can reuse it. It must ONLY
// record choices — every test below also asserts that no fetch/GraphQL
// mutation leaves the page while the dialog is up.
//
// Native confirm() discipline: Playwright auto-dismisses window.confirm(),
// which would silently cancel confirm-gated paths, so every dialog test
// registers a page.on("dialog") handler that RECORDS the native dialogs.
// ---------------------------------------------------------------------------

const DIALOG_INCOMING_FILE = {
  id: 999,
  path: "C:\\Source\\foo.mp4",
  size: 600000000, // 572.20 MB, bitrate 8.00 Mbps over 600s
  duration: 600,
  height: 1080,
  width: 1920,
  video_codec: "h264",
  oshash: "oshash-identical"
};

const DIALOG_EXISTING_FILE = {
  id: 9001,
  path: "C:\\Packs\\foo.mp4",
  size: 500000000, // 476.84 MB, bitrate 6.78 Mbps over 590s
  duration: 590,
  height: 1080,
  width: 1920,
  video_codec: "h264",
  oshash: "oshash-identical"
};

function dialogCollision(overrides = {}) {
  return {
    incomingFile: { ...DIALOG_INCOMING_FILE },
    incomingSceneId: 54,
    incomingSceneTitle: "Incoming scene",
    existingFile: { ...DIALOG_EXISTING_FILE },
    existingSceneId: 50,
    existingSceneTitle: "Existing scene",
    existingPath: "C:\\Packs\\foo.mp4",
    ...overrides
  };
}

// Boots the review page from the backend origin (port 9941) so the
// port-9941 branch of the Stash-origin derivation is exercised.
async function openReviewPageOnBackendPort(page) {
  await page.route("http://127.0.0.1:9941/review.html*", async (route) => {
    const filePath = path.resolve("plugin/assets/review.html");
    return route.fulfill({
      status: 200,
      contentType: "text/html",
      body: fs.readFileSync(filePath, "utf8")
    });
  });
  await page.route("**/graphql", async (route) => {
    const postData = JSON.parse(route.request().postData() || "{}");
    if (postData.query && postData.query.includes("FindScenes")) {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ data: { findScenes: { scenes: [] } } })
      });
    }
    return route.fallback();
  });
  await page.goto("http://127.0.0.1:9941/review.html?scenes=1");
  await expect(page.locator(".scene-card")).toHaveCount(0);
}

test.describe("Empornium Review — destination collision dialog", () => {

  test("renders per-collision comparison with all six data fields, identical-content badge, and Stash scene links (port-9999 origin branch)", async ({ page }) => {
    serveAssets(page);
    const nativeDialogs = [];
    page.on("dialog", async (dialog) => {
      nativeDialogs.push({ type: dialog.type(), message: dialog.message() });
      await dialog.dismiss();
    });
    await openReviewPage(page);

    const dialogPromise = page.evaluate(
      (items) => window.openDestinationCollisionDialog(items),
      [dialogCollision()]
    );

    const modal = page.locator("#dest-collision-modal");
    await expect(modal).toBeVisible();

    const card = modal.locator(".dest-collision-card");
    await expect(card).toHaveCount(1);

    // Two columns: Incoming / Already in destination.
    await expect(card.locator(".dest-collision-col--incoming .dest-collision-col-header")).toHaveText("Incoming");
    await expect(card.locator(".dest-collision-col--existing .dest-collision-col-header")).toHaveText("Already in destination");

    // All six data fields on BOTH sides: filename, size, duration, resolution, codec, bitrate.
    const incomingCol = card.locator(".dest-collision-col--incoming");
    const existingCol = card.locator(".dest-collision-col--existing");
    await expect(incomingCol.locator("[data-field='filename']")).toHaveText("foo.mp4");
    await expect(existingCol.locator("[data-field='filename']")).toHaveText("foo.mp4");
    for (const field of ["size", "duration", "resolution", "codec", "bitrate"]) {
      await expect(incomingCol.locator(`[data-field='${field}']`)).toBeVisible();
      await expect(existingCol.locator(`[data-field='${field}']`)).toBeVisible();
    }
    // Exact formatted values (helpers formatFileSize/formatDuration/formatResolution/formatCodec).
    await expect(incomingCol.locator("[data-field='size']")).toContainText("572.20 MB");
    await expect(existingCol.locator("[data-field='size']")).toContainText("476.84 MB");
    await expect(incomingCol.locator("[data-field='duration']")).toContainText("10m 0s");
    await expect(existingCol.locator("[data-field='duration']")).toContainText("9m 50s");
    await expect(incomingCol.locator("[data-field='resolution']")).toContainText("1080p");
    await expect(existingCol.locator("[data-field='resolution']")).toContainText("1080p");
    await expect(incomingCol.locator("[data-field='codec']")).toContainText("h264");
    await expect(existingCol.locator("[data-field='codec']")).toContainText("h264");
    // Computed bitrate Mbps = size*8/duration/1e6.
    await expect(incomingCol.locator("[data-field='bitrate']")).toContainText("8.00 Mbps");
    await expect(existingCol.locator("[data-field='bitrate']")).toContainText("6.78 Mbps");

    // Identical-content badge (both oshash values non-empty and equal).
    await expect(card.locator(".dest-collision-identical")).toHaveText("✔ identical content");

    // Both scene links: correct ids AND the full origin (port 9999 page →
    // window.location.origin is the Stash UI itself).
    const incomingLink = incomingCol.locator("a.dest-collision-scene-link");
    const existingLink = existingCol.locator("a.dest-collision-scene-link");
    await expect(incomingLink).toHaveAttribute("href", "http://localhost:9999/scenes/54");
    await expect(existingLink).toHaveAttribute("href", "http://localhost:9999/scenes/50");
    await expect(incomingLink).toHaveText("Open scene in Stash");
    await expect(existingLink).toHaveText("Open scene in Stash");

    // Close cleanly.
    await page.click("#btn-cancel-dest-collision");
    await expect(modal).toBeHidden();
    expect(await dialogPromise).toBeNull();
    expect(nativeDialogs).toHaveLength(0);
  });

  test("scene links use http://<hostname>:9999 when the page is served by the backend on port 9941", async ({ page }) => {
    serveAssets(page);
    await openReviewPageOnBackendPort(page);

    const dialogPromise = page.evaluate(
      (items) => window.openDestinationCollisionDialog(items),
      [dialogCollision()]
    );

    const card = page.locator("#dest-collision-modal .dest-collision-card");
    await expect(card).toHaveCount(1);
    // location.origin on :9941 is the FastAPI server, NOT Stash — the links
    // must point at the Stash UI on :9999 instead.
    await expect(card.locator(".dest-collision-col--incoming a.dest-collision-scene-link")).toHaveAttribute("href", "http://127.0.0.1:9999/scenes/54");
    await expect(card.locator(".dest-collision-col--existing a.dest-collision-scene-link")).toHaveAttribute("href", "http://127.0.0.1:9999/scenes/50");

    await page.click("#btn-cancel-dest-collision");
    expect(await dialogPromise).toBeNull();
  });

  test("each resolution choice shows its consequence text", async ({ page }) => {
    serveAssets(page);
    await openReviewPage(page);

    const dialogPromise = page.evaluate(
      (items) => window.openDestinationCollisionDialog(items),
      [dialogCollision()]
    );
    const card = page.locator("#dest-collision-modal .dest-collision-card");
    await expect(card).toHaveCount(1);

    await expect(card.locator(".dest-collision-choice--keep .dest-collision-consequence")).toHaveText(
      "the scene stays unconsolidated; Build stays disabled until it is removed or resolved"
    );
    await expect(card.locator(".dest-collision-choice--replace .dest-collision-consequence")).toHaveText(
      "deletes the existing file from disk and Stash; the emptied scene remains in Stash"
    );
    await expect(card.locator(".dest-collision-choice--keepboth .dest-collision-consequence")).toHaveText(
      "the old copy stays in the destination; Build ignores it and keeps it out of the torrent — the file stays on disk until you remove it"
    );

    await page.click("#btn-cancel-dest-collision");
    expect(await dialogPromise).toBeNull();
  });

  test("Keep-both is disabled while #opt-dest-rename is unchecked (with tooltip) and enabled when checked", async ({ page }) => {
    serveAssets(page);
    await openReviewPage(page);

    const dialogPromise = page.evaluate(
      (items) => window.openDestinationCollisionDialog(items),
      [dialogCollision()]
    );
    const modal = page.locator("#dest-collision-modal");
    await expect(modal).toBeVisible();

    // Opt-in checkbox DEFAULT UNCHECKED.
    const renameBox = page.locator("#opt-dest-rename");
    await expect(renameBox).not.toBeChecked();

    const keepbothRadio = modal.locator(".dest-collision-card").first().locator("input[value='keepboth']");
    await expect(keepbothRadio).toBeDisabled();
    // Tooltip present while disabled.
    await expect(modal.locator(".dest-collision-card").first().locator(".dest-collision-choice--keepboth")).toHaveAttribute(
      "title",
      /renam/i
    );

    await renameBox.check();
    await expect(keepbothRadio).toBeEnabled();

    await page.click("#btn-cancel-dest-collision");
    expect(await dialogPromise).toBeNull();
  });

  test("two-collision batch renders two cards with independent choices; confirm resolves the recorded choices without any network call", async ({ page }) => {
    serveAssets(page);
    const wire = { graphql: [], probes: [] };
    await page.route("**/graphql", async (route) => {
      const postData = JSON.parse(route.request().postData() || "{}");
      wire.graphql.push(postData);
      if (postData.query && postData.query.includes("FindScenes")) {
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ data: { findScenes: { scenes: [] } } })
        });
      }
      // Record + fulfill anything else — never leak to a real Stash.
      return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ data: {} }) });
    });
    await page.route("**/api/fs/exists", async (route) => {
      wire.probes.push(route.request().postData());
      return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ results: {} }) });
    });
    await page.goto("http://localhost:9999/plugins/empornium-megapack/review.html?scenes=1");
    await expect(page.locator(".scene-card")).toHaveCount(0);

    const collisions = [
      dialogCollision({
        incomingFile: { ...DIALOG_INCOMING_FILE, path: "C:\\Source\\alpha.mp4", oshash: "same-oshash" },
        existingFile: { ...DIALOG_EXISTING_FILE, path: "C:\\Packs\\alpha.mp4", oshash: "same-oshash" },
        existingPath: "C:\\Packs\\alpha.mp4"
      }),
      dialogCollision({
        incomingFile: { ...DIALOG_INCOMING_FILE, id: 998, path: "C:\\Source\\beta.mp4", oshash: "incoming-hash" },
        existingFile: { ...DIALOG_EXISTING_FILE, path: "C:\\Packs\\beta.mp4", oshash: "different-hash" },
        existingPath: "C:\\Packs\\beta.mp4"
      })
    ];

    const dialogPromise = page.evaluate((items) => window.openDestinationCollisionDialog(items), collisions);
    const modal = page.locator("#dest-collision-modal");
    await expect(modal).toBeVisible();

    const cards = modal.locator(".dest-collision-card");
    await expect(cards).toHaveCount(2);

    // Only the identical-oshash pair carries the badge.
    await expect(modal.locator(".dest-collision-identical")).toHaveCount(1);

    // Independent choices: Replace on card 1, Keep-both on card 2.
    await cards.first().locator("input[value='replace']").check();
    await page.locator("#opt-dest-rename").check();
    await cards.nth(1).locator("input[value='keepboth']").check();

    await page.click("#btn-confirm-dest-collision");
    const choices = await dialogPromise;

    expect(choices).toHaveLength(2);
    expect(choices[0].choice).toBe("replace");
    expect(choices[0].incomingFileId).toBe(999);
    expect(choices[0].existingFileId).toBe(9001);
    expect(choices[0].existingSceneId).toBe(50);
    expect(choices[1].choice).toBe("keepboth");
    expect(choices[1].incomingFileId).toBe(998);

    // The dialog records choices only — nothing went over the wire.
    const mutationBodies = wire.graphql.filter((b) => /mutation/i.test(b.query || ""));
    expect(mutationBodies).toHaveLength(0);
    expect(wire.probes).toHaveLength(0);
    await expect(modal).toBeHidden();
  });

  test("unknown/foreign existing file renders the unknown-file branch with only Keep-both/abort", async ({ page }) => {
    serveAssets(page);
    await openReviewPage(page);

    const foreign = dialogCollision({
      existingFile: null,
      existingSceneId: null,
      existingSceneTitle: null
    });

    const dialogPromise = page.evaluate((items) => window.openDestinationCollisionDialog(items), [foreign]);
    const modal = page.locator("#dest-collision-modal");
    await expect(modal).toBeVisible();

    const card = modal.locator(".dest-collision-card").first();
    await expect(card.locator(".dest-collision-unknown")).toHaveText("Unknown file (not in the Stash library)");

    // No Replace option for a foreign file; only Keep-both (gated) / abort.
    await expect(card.locator("input[value='replace']")).toHaveCount(0);
    await expect(card.locator("input[value='keep']")).toHaveCount(0);

    // Keep-both still gated by the opt-in checkbox; Confirm stays disabled
    // while the card has no selectable choice.
    const keepbothRadio = card.locator("input[value='keepboth']");
    await expect(keepbothRadio).toBeDisabled();
    const confirmBtn = page.locator("#btn-confirm-dest-collision");
    await expect(confirmBtn).toBeDisabled();

    await page.locator("#opt-dest-rename").check();
    await expect(keepbothRadio).toBeEnabled();
    await expect(keepbothRadio).toBeChecked();
    await expect(confirmBtn).toBeEnabled();

    // Incoming side still links to its scene; the unknown side has no link.
    await expect(card.locator(".dest-collision-col--incoming a.dest-collision-scene-link")).toHaveAttribute(
      "href",
      "http://localhost:9999/scenes/54"
    );
    await expect(card.locator(".dest-collision-col--existing a.dest-collision-scene-link")).toHaveCount(0);

    await page.click("#btn-confirm-dest-collision");
    const choices = await dialogPromise;
    expect(choices).toHaveLength(1);
    expect(choices[0].choice).toBe("keepboth");
    expect(choices[0].existingFileId).toBeNull();
  });

  test("case-insensitive collision Scene.MP4 vs scene.mp4 is flagged by the data layer and rendered by the dialog", async ({ page }) => {
    serveAssets(page);
    await page.route("**/graphql", async (route) => {
      const postData = JSON.parse(route.request().postData() || "{}");
      if (postData.query && postData.query.includes("FindDestinationCollisions")) {
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            data: {
              findScenes: {
                scenes: [
                  { id: 50, title: "Existing uppercase", files: [{ id: 9001, path: "C:\\Packs\\Scene.MP4", size: 500000000, duration: 590, height: 1080, width: 1920, video_codec: "h264", oshash: "zz" }] }
                ]
              }
            }
          })
        });
      }
      if (postData.query && postData.query.includes("FindScenes")) {
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ data: { findScenes: { scenes: [] } } })
        });
      }
      return route.fallback();
    });
    await openReviewPage(page);

    const collisions = await page.evaluate(
      ([items, dest]) => window.findDestinationCollisions(items, dest),
      [[{ id: 999, path: "C:\\Source\\scene.mp4", sceneId: 54, sceneTitle: "Incoming scene" }], "C:\\Packs\\"]
    );
    expect(collisions).toHaveLength(1);

    const dialogModel = collisions.map((col) => ({
      incomingFile: { id: 999, path: "C:\\Source\\scene.mp4", size: 600000000, duration: 600, height: 1080, width: 1920, video_codec: "h264", oshash: "" },
      incomingSceneId: 54,
      incomingSceneTitle: "Incoming scene",
      existingFile: col.file,
      existingSceneId: col.sceneId,
      existingSceneTitle: col.sceneTitle,
      existingPath: col.file.path
    }));

    const dialogPromise = page.evaluate((items) => window.openDestinationCollisionDialog(items), dialogModel);
    const card = page.locator("#dest-collision-modal .dest-collision-card").first();
    await expect(card).toBeVisible();
    await expect(card.locator(".dest-collision-col--incoming [data-field='filename']")).toHaveText("scene.mp4");
    await expect(card.locator(".dest-collision-col--existing [data-field='filename']")).toHaveText("Scene.MP4");

    await page.click("#btn-cancel-dest-collision");
    expect(await dialogPromise).toBeNull();
  });

  test("dialog cancels cleanly with Esc and performs no fetch/GraphQL mutations", async ({ page }) => {
    serveAssets(page);
    const nativeDialogs = [];
    page.on("dialog", async (dialog) => {
      nativeDialogs.push({ type: dialog.type(), message: dialog.message() });
      await dialog.dismiss();
    });

    const wire = { graphql: [], probes: [] };
    await page.route("**/graphql", async (route) => {
      const postData = JSON.parse(route.request().postData() || "{}");
      wire.graphql.push(postData);
      if (postData.query && postData.query.includes("FindScenes")) {
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ data: { findScenes: { scenes: [] } } })
        });
      }
      // Record + fulfill — never leak to a real Stash on :9999.
      return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ data: {} }) });
    });
    await page.route("**/api/fs/exists", async (route) => {
      wire.probes.push(JSON.parse(route.request().postData() || "{}"));
      return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ results: {} }) });
    });

    await page.goto("http://localhost:9999/plugins/empornium-megapack/review.html?scenes=1");
    await expect(page.locator(".scene-card")).toHaveCount(0);

    const dialogPromise = page.evaluate(
      (items) => window.openDestinationCollisionDialog(items),
      [dialogCollision()]
    );
    const modal = page.locator("#dest-collision-modal");
    await expect(modal).toBeVisible();

    // Keyboard: focus lands inside the dialog and Esc cancels it.
    await page.keyboard.press("Tab");
    const focusInside = await page.evaluate(() => {
      const modal = document.getElementById("dest-collision-modal");
      return modal.contains(document.activeElement);
    });
    expect(focusInside).toBe(true);

    await page.keyboard.press("Escape");
    expect(await dialogPromise).toBeNull();
    await expect(modal).toBeHidden();

    // No mutation of any kind was fired by opening + cancelling the dialog.
    const mutationBodies = wire.graphql.filter((b) => /mutation/i.test(b.query || ""));
    expect(mutationBodies).toHaveLength(0);
    expect(wire.probes).toHaveLength(0);
    // No native confirm() was raised either.
    expect(nativeDialogs).toHaveLength(0);
  });

});

// ---------------------------------------------------------------------------
// Todo 4: consolidation EXECUTION ENGINE.
//
// Drives the real flow end to end: scenes load from the mocked FindScenes,
// #btn-consolidate is clicked, and the wire log records every GraphQL call
// and filesystem probe IN ORDER so sequencing assertions (deleteFiles before
// moveFiles, rename probe after the batched move) run against actual wire
// traffic — never against stubbed functions.
//
// Native confirm() discipline: the harness registers page.on("dialog") that
// RECORDS then ACCEPTS every native dialog (Playwright would auto-dismiss
// and silently cancel the confirm-gated paths).
// ---------------------------------------------------------------------------

function execScene(id, fileId, filePath, title) {
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
    performers: [],
    tags: [],
    studio: null
  };
}

// Stash returns Scene.files primary-first, and refuses deleteFiles on a
// scene's primary file. By default the colliding file is the scene's only file
// and is therefore PRIMARY (the common real-world shape) — pass
// {primary: false} to put a decoy ahead of it so Replace is legitimately
// available, which is the only case Stash actually permits.
function existingCollisionScene(sceneId, fileId, filePath, title, opts = {}) {
  const collidingFile = {
    id: fileId,
    path: filePath,
    size: 4000000,
    duration: 590,
    height: 1080,
    width: 1920,
    video_codec: "h264",
    oshash: `existing-${fileId}`
  };
  const files = opts.primary === false
    ? [{ ...collidingFile, id: fileId + 500000, path: `${filePath}.primary.mp4`, oshash: `primary-${fileId}` }, collidingFile]
    : [collidingFile];
  return {
    id: sceneId,
    title,
    files
  };
}

// Boots the review page with the given scenes and wires the full network
// mock: FindScenes -> scenes, FindDestinationCollisions -> collisionScenes,
// moveFiles/deleteFiles mutations recorded + fulfilled, /api/fs/exists
// recorded + answered via probeExists (or failed via probeFail). Everything
// is recorded into one ordered wire.log so sequence assertions are possible.
async function bootExecutionHarness(page, {
  scenes,
  collisionScenes = [],
  probeExists = () => false,
  probeFail = null,
  deleteFilesError = null
}) {
  serveAssets(page);
  const wire = { log: [] };

  await page.route("**/graphql", async (route) => {
    const postData = JSON.parse(route.request().postData() || "{}");
    const query = postData.query || "";
    if (query.includes("FindDestinationCollisions")) {
      wire.log.push({ kind: "FindDestinationCollisions" });
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ data: { findScenes: { scenes: collisionScenes } } })
      });
    }
    if (query.includes("FindScenes")) {
      wire.log.push({ kind: "FindScenes" });
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ data: { findScenes: { scenes } } })
      });
    }
    if (query.includes("deleteFiles")) {
      wire.log.push({ kind: "deleteFiles", variables: postData.variables, query });
      if (deleteFilesError) {
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ errors: [{ message: deleteFilesError }] })
        });
      }
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ data: { deleteFiles: true } })
      });
    }
    if (query.includes("moveFiles")) {
      wire.log.push({ kind: "moveFiles", variables: postData.variables, query });
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ data: { moveFiles: true } })
      });
    }
    // Record + fulfill anything else — never leak to a real Stash on :9999.
    wire.log.push({ kind: "other-graphql", query });
    return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ data: {} }) });
  });

  await page.route("**/api/fs/exists", async (route) => {
    const postData = JSON.parse(route.request().postData() || "{}");
    const paths = postData.paths || [];
    wire.log.push({ kind: "probe", paths });
    if (probeFail && probeFail(paths)) {
      return route.fulfill({ status: 500, contentType: "application/json", body: "{}" });
    }
    const results = {};
    for (const p of paths) results[p] = probeExists(p);
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ results })
    });
  });

  const nativeDialogs = [];
  page.on("dialog", async (dialog) => {
    nativeDialogs.push({ type: dialog.type(), message: dialog.message() });
    await dialog.accept();
  });

  const sceneIds = scenes.map((s) => s.id).join(",");
  await page.goto(`http://localhost:9999/plugins/empornium-megapack/review.html?scenes=${sceneIds}&mode=megapack`);
  // Seed-dir field starts EMPTY (no machine-path default) — set it so the
  // consolidation destination matches the C:\Packs fixtures below.
  await page.locator("#output-dir").fill("C:\\Packs");
  await expect(page.locator(".scene-card")).toHaveCount(scenes.length);
  await expect(page.locator("#btn-consolidate")).toBeEnabled();
  return { wire, nativeDialogs };
}

test.describe("Empornium Review — consolidation execution engine (destination-collision-aware)", () => {

  test("(a) no collisions -> exactly one batched moveFiles; existing confirm() gate preserved", async ({ page }) => {
    const scenes = [
      execScene(1, 11, "C:\\Source\\a.mp4", "Scene A"),
      execScene(2, 22, "C:\\Source\\b.mp4", "Scene B")
    ];
    const { wire, nativeDialogs } = await bootExecutionHarness(page, { scenes });

    await page.locator("#btn-consolidate").click();
    await expect(page.locator("#status-text")).toContainText("Files moved successfully!");

    const moves = wire.log.filter((e) => e.kind === "moveFiles");
    expect(moves).toHaveLength(1);
    expect(moves[0].variables.input.ids).toEqual([11, 22]);
    expect(moves[0].variables.input.destination_folder).toBe("C:\\Packs");
    expect(moves[0].query).toContain("moveFiles");
    expect(moves[0].variables.input.destination_basename).toBeUndefined();
    expect(wire.log.some((e) => e.kind === "deleteFiles")).toBe(false);

    // Discovery ran (read-only) but no collision dialog appeared.
    expect(wire.log.some((e) => e.kind === "FindDestinationCollisions")).toBe(true);
    await expect(page.locator("#dest-collision-modal")).toBeHidden();

    // Exactly one native dialog: the pre-existing overall confirm gate.
    expect(nativeDialogs).toHaveLength(1);
    expect(nativeDialogs[0].type).toBe("confirm");
    expect(nativeDialogs[0].message).toBe("Move/consolidate 2 files into C:\\Packs?");

    const ids = await page.evaluate(() => Array.from(window.consolidatedFileIds));
    expect(ids).toEqual(expect.arrayContaining([11, 22]));
  });

  test("(b) re-consolidating an already-in-place selection opens NO dialog and fires ZERO mutations (move-only-missing)", async ({ page }) => {
    const scenes = [
      execScene(1, 11, "C:\\Packs\\My Awesome Megapack\\a.mp4", "Scene A"),
      execScene(2, 22, "C:\\Packs\\My Awesome Megapack\\b.mp4", "Scene B")
    ];
    const { wire, nativeDialogs } = await bootExecutionHarness(page, { scenes });

    await page.locator("#btn-consolidate").click();
    await expect(page.locator("#status-text")).toContainText("already in");

    // Both files sit RECURSIVELY under the seed dir (nested in the old pack
    // subfolder): move-only-missing moves nothing, probes nothing, and there
    // is no move to confirm.
    const moves = wire.log.filter((e) => e.kind === "moveFiles");
    expect(moves).toHaveLength(0);
    expect(wire.log.some((e) => e.kind === "FindDestinationCollisions")).toBe(false);
    expect(wire.log.some((e) => e.kind === "probe")).toBe(false);
    expect(wire.log.some((e) => e.kind === "deleteFiles")).toBe(false);
    await expect(page.locator("#dest-collision-modal")).toBeHidden();
    expect(nativeDialogs).toHaveLength(0); // no move -> no confirm gate

    const ids = await page.evaluate(() => Array.from(window.consolidatedFileIds));
    expect(ids).toEqual(expect.arrayContaining([11, 22]));
  });

  test("(c) Keep-existing -> no mutation for that file, others move, persistent leftover warning shown", async ({ page }) => {
    const scenes = [
      execScene(1, 11, "C:\\Source\\a.mp4", "Scene A"),
      execScene(2, 22, "C:\\Source\\b.mp4", "Scene B"),
      execScene(3, 33, "C:\\Source\\c.mp4", "Scene C")
    ];
    const collisionScenes = [existingCollisionScene(50, 9001, "C:\\Packs\\b.mp4", "Existing B")];
    const { wire } = await bootExecutionHarness(page, {
      scenes,
      collisionScenes,
      probeExists: (p) => p.endsWith("b.mp4")
    });

    await page.locator("#btn-consolidate").click();
    const modal = page.locator("#dest-collision-modal");
    await expect(modal).toBeVisible();
    await expect(modal.locator(".dest-collision-card")).toHaveCount(1);
    // Keep existing is the default radio — confirm as-is.
    await page.click("#btn-confirm-dest-collision");

    await expect(page.locator("#status-text")).toContainText("Build ignores them and excludes them from the torrent");

    const moves = wire.log.filter((e) => e.kind === "moveFiles");
    expect(moves).toHaveLength(1);
    expect(moves[0].variables.input.ids).toEqual([11, 33]); // b (22) never attempted
    expect(wire.log.some((e) => e.kind === "deleteFiles")).toBe(false);

    const ids = await page.evaluate(() => Array.from(window.consolidatedFileIds));
    expect(ids).toContain(11);
    expect(ids).toContain(33);
    expect(ids).not.toContain(22);

    // Persistent warning names the leftover destination file.
    const status = await page.locator("#status-text").innerText();
    expect(status).toContain("C:\\Packs\\b.mp4");
    expect(status).toContain("Build ignores them and excludes them from the torrent");
  });

  test("(d) Replace -> deleteFiles(existing id) BEFORE moveFiles(incoming id), inside the explicit confirm flow", async ({ page }) => {
    const scenes = [execScene(1, 11, "C:\\Source\\foo.mp4", "Incoming Scene")];
    // Replace is only legal on a NON-primary file — Stash refuses to delete a
    // scene's primary file.
    const collisionScenes = [existingCollisionScene(50, 9001, "C:\\Packs\\foo.mp4", "Existing Scene", { primary: false })];
    const { wire, nativeDialogs } = await bootExecutionHarness(page, {
      scenes,
      collisionScenes,
      probeExists: (p) => p.endsWith("foo.mp4")
    });

    await page.locator("#btn-consolidate").click();
    const modal = page.locator("#dest-collision-modal");
    await expect(modal).toBeVisible();
    await modal.locator(".dest-collision-card").first().locator("input[value='replace']").check();
    await page.click("#btn-confirm-dest-collision");

    await expect(page.locator("#status-text")).toContainText("Files moved successfully!");

    const deleteCalls = wire.log.filter((e) => e.kind === "deleteFiles");
    const moveCalls = wire.log.filter((e) => e.kind === "moveFiles");
    expect(deleteCalls).toHaveLength(1);
    expect(deleteCalls[0].variables.ids).toEqual([9001]);
    // The REAL schema mutation (disk + DB) — never the DB-only destroyFiles.
    expect(deleteCalls[0].query).toContain("deleteFiles");
    expect(deleteCalls[0].query).not.toContain("destroyFiles");
    expect(moveCalls).toHaveLength(1);
    expect(moveCalls[0].variables.input.ids).toEqual([11]);
    // Wire order: deleteFiles strictly before the incoming file's moveFiles.
    expect(wire.log.findIndex((e) => e.kind === "deleteFiles"))
      .toBeLessThan(wire.log.findIndex((e) => e.kind === "moveFiles"));

    // Explicit confirm named the file and the owning scene.
    const replaceConfirm = nativeDialogs.find((d) => d.message.includes("Replace existing file"));
    expect(replaceConfirm).toBeTruthy();
    expect(replaceConfirm.message).toContain("foo.mp4");
    expect(replaceConfirm.message).toContain("Existing Scene");

    const ids = await page.evaluate(() => Array.from(window.consolidatedFileIds));
    expect(ids).toContain(11);
  });

  test("(e) Keep-both with checkbox -> one moveFiles per renamed file with destination_basename computed AFTER the batched move, old->new confirm first", async ({ page }) => {
    const scenes = [
      execScene(1, 11, "C:\\Source\\a.mp4", "Scene A"),
      execScene(2, 22, "C:\\Source\\b.mp4", "Scene B")
    ];
    const collisionScenes = [existingCollisionScene(50, 9001, "C:\\Packs\\b.mp4", "Existing B")];
    const { wire, nativeDialogs } = await bootExecutionHarness(page, {
      scenes,
      collisionScenes,
      probeExists: (p) => p.endsWith("b.mp4")
    });

    await page.locator("#btn-consolidate").click();
    const modal = page.locator("#dest-collision-modal");
    await expect(modal).toBeVisible();
    await page.locator("#opt-dest-rename").check();
    await modal.locator(".dest-collision-card").first().locator("input[value='keepboth']").check();
    await page.click("#btn-confirm-dest-collision");

    await expect(page.locator("#status-text")).toContainText("Build ignores them and excludes them from the torrent");

    const moveCalls = wire.log.filter((e) => e.kind === "moveFiles");
    expect(moveCalls).toHaveLength(2);
    // (1) batched move of the collision-free file first, no basename override.
    expect(moveCalls[0].variables.input.ids).toEqual([11]);
    expect(moveCalls[0].variables.input.destination_basename).toBeUndefined();
    // (2) rename move for the keep-both file: basename-only destination_basename.
    expect(moveCalls[1].variables.input.ids).toEqual([22]);
    expect(moveCalls[1].variables.input.destination_basename).toBe("b (1).mp4");
    expect(moveCalls[1].variables.input.destination_folder).toBe("C:\\Packs");

    // The rename probe happened AFTER the batched move and BEFORE the rename mutation.
    const batchIdx = wire.log.findIndex((e) => e.kind === "moveFiles");
    const renameProbeIdx = wire.log.findIndex((e) => e.kind === "probe" && e.paths.some((p) => p.includes("b (1)")));
    const renameIdx = wire.log.findIndex((e) => e.kind === "moveFiles" && e.variables.input.destination_basename);
    expect(renameProbeIdx).toBeGreaterThan(batchIdx);
    expect(renameIdx).toBeGreaterThan(renameProbeIdx);

    // ONE old->new confirm preceded the renames, listing both names.
    const renameConfirm = nativeDialogs.find((d) => d.message.includes("Rename and keep both?"));
    expect(renameConfirm).toBeTruthy();
    expect(renameConfirm.message).toContain("b.mp4");
    expect(renameConfirm.message).toContain("b (1).mp4");

    const ids = await page.evaluate(() => Array.from(window.consolidatedFileIds));
    expect(ids).toEqual(expect.arrayContaining([11, 22]));
  });

  test("(f) rename checkbox OFF -> Keep-both impossible, no destination_basename mutation issued", async ({ page }) => {
    const scenes = [
      execScene(1, 11, "C:\\Source\\a.mp4", "Scene A"),
      execScene(2, 22, "C:\\Source\\b.mp4", "Scene B")
    ];
    const collisionScenes = [existingCollisionScene(50, 9001, "C:\\Packs\\b.mp4", "Existing B")];
    const { wire } = await bootExecutionHarness(page, {
      scenes,
      collisionScenes,
      probeExists: (p) => p.endsWith("b.mp4")
    });

    await page.locator("#btn-consolidate").click();
    const modal = page.locator("#dest-collision-modal");
    await expect(modal).toBeVisible();
    // Checkbox OFF (default) -> the Keep-both radio is disabled: impossible.
    const keepbothRadio = modal.locator(".dest-collision-card").first().locator("input[value='keepboth']");
    await expect(keepbothRadio).toBeDisabled();
    // Default Keep-existing stands.
    await page.click("#btn-confirm-dest-collision");

    await expect(page.locator("#status-text")).toContainText("Build ignores them and excludes them from the torrent");

    const moves = wire.log.filter((e) => e.kind === "moveFiles");
    expect(moves).toHaveLength(1);
    expect(moves[0].variables.input.ids).toEqual([11]);
    expect(moves[0].variables.input.destination_basename).toBeUndefined();
    expect(wire.log.some((e) => e.kind === "deleteFiles")).toBe(false);
    const ids = await page.evaluate(() => Array.from(window.consolidatedFileIds));
    expect(ids).not.toContain(22);
  });

  test("(g) first-error stop -> later files not attempted, status shows moved/failed counts", async ({ page }) => {
    const scenes = [
      execScene(1, 11, "C:\\Source\\a.mp4", "Scene A"),
      execScene(2, 22, "C:\\Source\\b.mp4", "Scene B"),
      execScene(3, 33, "C:\\Source\\c.mp4", "Scene C")
    ];
    const collisionScenes = [
      existingCollisionScene(50, 9001, "C:\\Packs\\b.mp4", "Existing B", { primary: false }),
      existingCollisionScene(51, 9002, "C:\\Packs\\c.mp4", "Existing C", { primary: false })
    ];
    const { wire } = await bootExecutionHarness(page, {
      scenes,
      collisionScenes,
      probeExists: (p) => p.endsWith("b.mp4") || p.endsWith("c.mp4"),
      deleteFilesError: "delete failed (simulated)"
    });

    await page.locator("#btn-consolidate").click();
    const modal = page.locator("#dest-collision-modal");
    await expect(modal).toBeVisible();
    const cards = modal.locator(".dest-collision-card");
    await expect(cards).toHaveCount(2);
    await cards.nth(0).locator("input[value='replace']").check();
    await cards.nth(1).locator("input[value='replace']").check();
    await page.click("#btn-confirm-dest-collision");

    await expect(page.locator("#status-text")).toContainText("Consolidation stopped");

    // First replace's deleteFiles failed -> the second replace was never attempted.
    const deleteCalls = wire.log.filter((e) => e.kind === "deleteFiles");
    expect(deleteCalls).toHaveLength(1);
    expect(deleteCalls[0].variables.ids).toEqual([9001]);
    // Only the batched move (file 11) succeeded.
    const moveCalls = wire.log.filter((e) => e.kind === "moveFiles");
    expect(moveCalls).toHaveLength(1);
    expect(moveCalls[0].variables.input.ids).toEqual([11]);

    const status = await page.locator("#status-text").innerText();
    expect(status).toContain("1 of 3 file(s) moved");
    expect(status).toContain("2 file(s) not moved");
    const ids = await page.evaluate(() => Array.from(window.consolidatedFileIds));
    expect(ids).toEqual([11]);
  });

  test("(h) probe 500 during rename -> rename path aborts, no destination_basename mutation issued", async ({ page }) => {
    const scenes = [
      execScene(1, 11, "C:\\Source\\a.mp4", "Scene A"),
      execScene(2, 22, "C:\\Source\\b.mp4", "Scene B")
    ];
    // Filesystem-only collision: Stash knows nothing about the existing file.
    const { wire } = await bootExecutionHarness(page, {
      scenes,
      collisionScenes: [],
      probeExists: (p) => p.endsWith("b.mp4"),
      probeFail: (paths) => paths.some((p) => p.includes(" (1)"))
    });

    await page.locator("#btn-consolidate").click();
    const modal = page.locator("#dest-collision-modal");
    await expect(modal).toBeVisible();
    await expect(modal.locator(".dest-collision-unknown")).toHaveCount(1);
    await page.locator("#opt-dest-rename").check();
    await modal.locator(".dest-collision-card").first().locator("input[value='keepboth']").check();
    await page.click("#btn-confirm-dest-collision");

    await expect(page.locator("#status-text")).toContainText("Consolidation stopped");

    // Fail-closed: the rename probe 500 aborted before any rename mutation.
    const renameMoves = wire.log.filter((e) => e.kind === "moveFiles" && e.variables.input.destination_basename);
    expect(renameMoves).toHaveLength(0);
    const moveCalls = wire.log.filter((e) => e.kind === "moveFiles");
    expect(moveCalls).toHaveLength(1); // only the batched move happened
    expect(moveCalls[0].variables.input.ids).toEqual([11]);

    const status = await page.locator("#status-text").innerText();
    expect(status).toContain("1 of 2 file(s) moved");
    expect(status).toContain("1 file(s) not moved");
  });

  // ---------------------------------------------------------------------
  // Regression cases from a real consolidation failure: Stash rejected
  // deleteFiles with "cannot delete primary file <path>" after 26 of 30 files
  // had already moved. Two distinct defects — Replace was offered for a file
  // Stash will never delete, and two files of the SAME scene were presented as
  // a foreign collision.
  // ---------------------------------------------------------------------

  // Scene 1 owns BOTH files: the 1080p copy already sitting in the destination
  // (primary, files[0]) and the 2160p copy at the source. getPrimaryFile()
  // selects by resolution, so the source copy is the incoming file.
  function sameSceneFixture() {
    return {
      id: 1,
      title: "Deduplicated Scene",
      date: "2026-01-01",
      paths: { screenshot: "" },
      files: [
        { id: 900, path: "C:\\Packs\\dup.mp4", size: 20377486, height: 1080, width: 1920, duration: 600, video_codec: "h264", oshash: "same-900" },
        { id: 901, path: "C:\\Source\\dup.mp4", size: 47950206, height: 2160, width: 3840, duration: 600, video_codec: "h264", oshash: "same-901" }
      ],
      performers: [],
      tags: [],
      studio: null
    };
  }

  test("(i) same-scene sibling in the destination -> 'use the copy already there', no Replace offered, zero mutations", async ({ page }) => {
    const scene = sameSceneFixture();
    const { wire } = await bootExecutionHarness(page, {
      scenes: [scene],
      collisionScenes: [scene],
      probeExists: (p) => p.endsWith("dup.mp4")
    });

    await page.locator("#btn-consolidate").click();
    const modal = page.locator("#dest-collision-modal");
    await expect(modal).toBeVisible();
    const card = modal.locator(".dest-collision-card").first();

    // Presented as a same-scene case, not a foreign collision.
    await expect(card).toContainText("This scene already has a file in the destination");
    await expect(card.locator("input[value='useexisting']")).toBeChecked();
    // Replace and Keep-existing are meaningless here and must not be offered.
    await expect(card.locator("input[value='replace']")).toHaveCount(0);
    await expect(card.locator("input[value='keep']")).toHaveCount(0);

    await page.click("#btn-confirm-dest-collision");
    await expect(page.locator("#status-text")).toContainText("copy already in the destination");

    // Nothing was moved, renamed, or deleted.
    expect(wire.log.some((e) => e.kind === "moveFiles")).toBe(false);
    expect(wire.log.some((e) => e.kind === "deleteFiles")).toBe(false);

    // The pack now points at the copy that is already in place: the scene card
    // renders the destination path, not the source one.
    // (The card also lists the scene's other file in its version picker, so
    // assert the SELECTED version rather than the card text.)
    const sceneCard = page.locator(".scene-card").first();
    await expect(sceneCard).toContainText("C:\\Packs\\dup.mp4");
    await expect(sceneCard.locator(".scene-file-select")).toHaveValue("900");
  });

  test("(j) existing file is its scene's PRIMARY -> Replace is disabled with the Stash reason", async ({ page }) => {
    const scenes = [execScene(1, 11, "C:\\Source\\foo.mp4", "Incoming Scene")];
    // Default helper: colliding file is the scene's only file, hence primary.
    const collisionScenes = [existingCollisionScene(50, 9001, "C:\\Packs\\foo.mp4", "Existing Scene")];
    await bootExecutionHarness(page, {
      scenes,
      collisionScenes,
      probeExists: (p) => p.endsWith("foo.mp4")
    });

    await page.locator("#btn-consolidate").click();
    const card = page.locator("#dest-collision-modal .dest-collision-card").first();
    await expect(card).toBeVisible();

    const replaceRadio = card.locator("input[value='replace']");
    await expect(replaceRadio).toBeDisabled();
    await expect(card).toContainText("Stash refuses to delete a scene's primary file");
    // The safe resolutions remain available.
    await expect(card.locator("input[value='keep']")).toBeEnabled();
  });

  test("(k) forged Replace on a primary file aborts BEFORE any mutation (no half-consolidated pack)", async ({ page }) => {
    const scenes = [
      execScene(1, 11, "C:\\Source\\a.mp4", "Scene A"),
      execScene(2, 22, "C:\\Source\\foo.mp4", "Scene B")
    ];
    const collisionScenes = [existingCollisionScene(50, 9001, "C:\\Packs\\foo.mp4", "Existing Scene")];
    const { wire } = await bootExecutionHarness(page, {
      scenes,
      collisionScenes,
      probeExists: (p) => p.endsWith("foo.mp4")
    });

    await page.locator("#btn-consolidate").click();
    const card = page.locator("#dest-collision-modal .dest-collision-card").first();
    await expect(card).toBeVisible();

    // Simulate a stale/forged choice by re-enabling the disabled control.
    await card.locator("input[value='replace']").evaluate((el) => {
      el.disabled = false;
      el.checked = true;
    });
    await page.click("#btn-confirm-dest-collision");

    await expect(page.locator("#status-text")).toContainText("aborted before any changes");

    // The critical property: scene A must NOT have been batch-moved first.
    expect(wire.log.some((e) => e.kind === "moveFiles")).toBe(false);
    expect(wire.log.some((e) => e.kind === "deleteFiles")).toBe(false);
    const ids = await page.evaluate(() => Array.from(window.consolidatedFileIds));
    expect(ids).toEqual([]);
  });

});
