import { test, expect } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

// Move-only-missing consolidation into the seed directory (todo 6 of
// staged-wizard-inplace-seed):
//
//   - The consolidation destination IS the seed-dir field value (#output-dir)
//     — no pack-title subfolder is appended.
//   - Files whose path is ALREADY under the seed dir (RECURSIVE containment,
//     not just direct children) are excluded from moves AND from collision
//     probing — they produce zero mutations.
//   - ONLY missing primaries are moved via Stash moveFiles
//     (destination_folder = seed dir), preserving the confirm discipline, the
//     destination-collision dialog, the sequential mutation discipline and
//     the basename-collision backstop.
//   - Post-run status reports moved / already-in-place / still-missing counts.
//
// Mocking discipline: NETWORK LAYER ONLY (page.route("**/graphql") + the
// backend :9941 probe endpoint), inspecting request postData — the same
// pattern as test_modal_integration.spec.mjs and
// test_destination_collision.spec.mjs. Mutation variables are asserted from
// the wire, never from DOM state.

const SEED = "D:\\Seed";

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

function scene(id, fileId, filePath, title) {
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

// Boots the review page with the given scenes and wires the full network
// mock: FindScenes -> scenes, FindDestinationCollisions -> collisionScenes,
// moveFiles/deleteFiles recorded (+ optionally failing), /api/fs/exists
// recorded + answered via probeExists. Everything is recorded so wire-level
// assertions are possible; nothing leaks to a real Stash on :9999.
async function bootHarness(page, {
  scenes,
  collisionScenes = [],
  probeExists = () => false,
  moveFilesError = null
}) {
  serveAssets(page);
  const wire = { moves: [], deletes: [], probes: [], collisionQueries: [], nativeDialogs: [] };

  await page.route("**/graphql", async (route) => {
    const postData = JSON.parse(route.request().postData() || "{}");
    const query = postData.query || "";
    if (query.includes("FindDestinationCollisions")) {
      wire.collisionQueries.push(postData.variables);
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ data: { findScenes: { scenes: collisionScenes } } })
      });
    }
    if (query.includes("FindScenes")) {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ data: { findScenes: { scenes } } })
      });
    }
    if (query.includes("deleteFiles")) {
      wire.deletes.push(postData.variables);
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ data: { deleteFiles: true } })
      });
    }
    if (query.includes("moveFiles")) {
      wire.moves.push(postData.variables);
      if (moveFilesError) {
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ errors: [{ message: moveFilesError }] })
        });
      }
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ data: { moveFiles: true } })
      });
    }
    // Record + fulfill anything else — never leak to a real Stash on :9999.
    return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ data: {} }) });
  });

  await page.route("**/api/fs/exists", async (route) => {
    const postData = JSON.parse(route.request().postData() || "{}");
    const paths = postData.paths || [];
    wire.probes.push(paths);
    const results = {};
    for (const p of paths) results[p] = probeExists(p);
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ results })
    });
  });

  page.on("dialog", async (dialog) => {
    wire.nativeDialogs.push({ type: dialog.type(), message: dialog.message() });
    await dialog.accept();
  });

  const sceneIds = scenes.map((s) => s.id).join(",");
  await page.goto(`http://localhost:9999/plugins/deepseek-megapack/review.html?scenes=${sceneIds}&mode=megapack`);
  await expect(page.locator(".scene-card")).toHaveCount(scenes.length);
  // The seed-dir field (currently #output-dir; todo 8 relabels it) drives the
  // consolidation destination — point it at the spec's seed dir.
  await page.locator("#output-dir").fill(SEED);
  await expect(page.locator("#btn-consolidate")).toBeEnabled();
  return { wire };
}

test.describe("isPathUnderSeed — recursive seed-dir containment helper", () => {
  // Edge cases pinned per todo 6: recursive containment (not just direct
  // child), segment-boundary prefix (the sibling trap), Windows
  // case-insensitivity, mixed separators, trailing separators, drive root.
  const cases = [
    ["D:\\Seed\\a.mp4", "D:\\Seed", true, "direct child"],
    ["D:\\Seed\\sub\\a.mp4", "D:\\Seed", true, "nested (recursive containment)"],
    ["D:\\Seed\\sub\\deeper\\a.mp4", "D:\\Seed", true, "deeply nested"],
    ["D:\\Seed2\\a.mp4", "D:\\Seed", false, "sibling trap: Seed2 is not under Seed"],
    ["D:\\Media2\\file.mp4", "D:\\Media", false, "sibling trap: Media2 is not under Media"],
    ["d:\\seed\\a.mp4", "D:\\SEED", true, "case-insensitive (Windows)"],
    ["D:/Seed/a.mp4", "D:\\Seed", true, "mixed separators (child forward slashes)"],
    ["D:\\Seed\\a.mp4", "D:/Seed", true, "mixed separators (seed forward slashes)"],
    ["D:\\Seed\\a.mp4", "D:\\Seed\\", true, "trailing separator on seed"],
    ["D:\\Seed\\a.mp4", "D:\\Seed\\\\", true, "multiple trailing separators on seed"],
    ["D:\\Seed\\", "D:\\Seed", true, "equality counts as under"],
    ["C:\\file.mp4", "C:\\", true, "drive root seed"],
    ["C:\\Other\\file.mp4", "C:\\", true, "everything on the drive is under the drive root"],
    ["", "D:\\Seed", false, "empty child path"],
    ["a.mp4", "D:\\Seed", false, "relative child path"],
    ["D:\\Seed\\a.mp4", "", false, "empty seed dir"],
    ["D:\\Seed\\a.mp4", "D:\\Other", false, "unrelated tree"]
  ];

  test("helper is exported and classifies every edge case", async ({ page }) => {
    serveAssets(page);
    await page.route("**/graphql", async (route) => {
      const postData = JSON.parse(route.request().postData() || "{}");
      if (postData.query && postData.query.includes("FindScenes")) {
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ data: { findScenes: { scenes: [] } } })
        });
      }
      return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ data: {} }) });
    });
    await page.goto("http://localhost:9999/plugins/deepseek-megapack/review.html?scenes=1");
    await expect(page.locator(".scene-card")).toHaveCount(0);

    expect(await page.evaluate(() => typeof window.isPathUnderSeed)).toBe("function");

    const failures = [];
    for (const [child, seed, expected, label] of cases) {
      const actual = await page.evaluate(
        ([c, s]) => window.isPathUnderSeed(c, s),
        [child, seed]
      );
      if (actual !== expected) {
        failures.push(`isPathUnderSeed(${JSON.stringify(child)}, ${JSON.stringify(seed)}) = ${actual}, expected ${expected} (${label})`);
      }
    }
    expect(failures, failures.join("\n")).toEqual([]);
  });
});

test.describe("consolidateFiles — move-only-missing into the seed dir", () => {

  test("mixed selection fires moveFiles ONLY for outside files; in-place files produce zero mutations", async ({ page }) => {
    const scenes = [
      scene(10, 100, "D:\\Seed\\already_there.mp4", "In Place Direct"),      // direct child of seed
      scene(20, 200, "D:\\Seed\\nested\\deep.mp4", "In Place Nested"),       // RECURSIVELY under seed
      scene(30, 300, "E:\\Elsewhere\\missing_one.mp4", "Missing One"),       // outside seed
      scene(40, 400, "C:\\Other\\missing_two.mp4", "Missing Two")            // outside seed
    ];
    const { wire } = await bootHarness(page, { scenes });

    await page.locator("#btn-consolidate").click();

    await expect(page.locator("#status-text")).toContainText("Files moved successfully!");

    // Exactly ONE batched move, carrying ONLY the two outside files.
    expect(wire.moves).toHaveLength(1);
    expect(wire.moves[0].input.ids).toEqual([300, 400]);
    expect(wire.moves[0].input.ids).not.toContain(100);
    expect(wire.moves[0].input.ids).not.toContain(200);
    // destination_folder = the seed-dir field value — no pack-title subfolder.
    expect(wire.moves[0].input.destination_folder).toBe(SEED);
    expect(wire.moves[0].input.destination_basename).toBeUndefined();
    expect(wire.deletes).toHaveLength(0);

    // The in-place files were never collision-probed: the discovery probe
    // covers only the moved files' destination paths.
    const probed = wire.probes.flat();
    expect(probed).toEqual([`${SEED}\\missing_one.mp4`, `${SEED}\\missing_two.mp4`]);

    // The overall confirm names the seed dir and only the moved count.
    expect(wire.nativeDialogs).toHaveLength(1);
    expect(wire.nativeDialogs[0].type).toBe("confirm");
    expect(wire.nativeDialogs[0].message).toBe(`Move/consolidate 2 files into ${SEED}?`);

    // Post-run bookkeeping: every primary is accounted for.
    const ids = await page.evaluate(() => Array.from(window.consolidatedFileIds));
    expect(ids).toEqual(expect.arrayContaining([100, 200, 300, 400]));

    // Post-run status reports moved / already-in-place / still-missing.
    const status = await page.locator("#status-text").innerText();
    expect(status).toContain("2 moved");
    expect(status).toContain("2 already in place");
    expect(status).toContain("0 still missing");
  });

  test("all files already under the seed dir (incl. nested) -> ZERO mutations, no confirm, no discovery", async ({ page }) => {
    const scenes = [
      scene(10, 100, "D:\\Seed\\a.mp4", "Direct Child"),
      scene(20, 200, "D:\\Seed\\sub\\b.mp4", "Nested Child")
    ];
    const { wire } = await bootHarness(page, { scenes });

    await page.locator("#btn-consolidate").click();

    await expect(page.locator("#status-text")).toContainText("already in");

    expect(wire.moves).toHaveLength(0);
    expect(wire.deletes).toHaveLength(0);
    expect(wire.collisionQueries).toHaveLength(0);
    expect(wire.probes).toHaveLength(0);
    await expect(page.locator("#dest-collision-modal")).toBeHidden();

    const ids = await page.evaluate(() => Array.from(window.consolidatedFileIds));
    expect(ids).toEqual(expect.arrayContaining([100, 200]));
  });

  test("destination-collision dialog still opens for an outside file colliding at the seed root; in-place file is not probed", async ({ page }) => {
    const scenes = [
      scene(10, 100, "D:\\Seed\\inplace.mp4", "In Place"),
      scene(20, 200, "E:\\Src\\collide.mp4", "Incoming Collision")
    ];
    const collisionScenes = [
      { id: 50, title: "Existing At Seed Root", files: [{ id: 9001, path: "D:\\Seed\\collide.mp4", size: 4000000, duration: 590, height: 1080, width: 1920, video_codec: "h264", oshash: "existing-9001" }] }
    ];
    const { wire } = await bootHarness(page, {
      scenes,
      collisionScenes,
      probeExists: (p) => p.endsWith("collide.mp4")
    });

    await page.locator("#btn-consolidate").click();

    // The dialog opens for the destination collision...
    const modal = page.locator("#dest-collision-modal");
    await expect(modal).toBeVisible();
    await expect(modal.locator(".dest-collision-card")).toHaveCount(1);

    // ...the discovery query targeted the SEED dir...
    expect(wire.collisionQueries).toHaveLength(1);
    expect(wire.collisionQueries[0].path).toBe(SEED);

    // ...and only the MOVED file's destination path was probed — the
    // in-place file needed no collision check against its own location.
    expect(wire.probes.flat()).toEqual([`${SEED}\\collide.mp4`]);

    // Default resolution: Keep existing -> no mutation for that file.
    await page.click("#btn-confirm-dest-collision");
    await expect(page.locator("#status-text")).toContainText("Build ignores them and excludes them from the torrent");

    expect(wire.moves).toHaveLength(0);
    expect(wire.deletes).toHaveLength(0);
  });

  test("moveFiles error -> status reports moved/failed counts and what remains missing", async ({ page }) => {
    const scenes = [
      scene(10, 100, "D:\\Seed\\inplace.mp4", "In Place"),
      scene(20, 200, "E:\\Src\\fail.mp4", "Fails To Move")
    ];
    const { wire } = await bootHarness(page, {
      scenes,
      moveFilesError: "move failed (simulated)"
    });

    await page.locator("#btn-consolidate").click();

    await expect(page.locator("#status-text")).toContainText("Consolidation stopped");

    expect(wire.moves).toHaveLength(1);
    expect(wire.moves[0].input.ids).toEqual([200]);
    expect(wire.moves[0].input.destination_folder).toBe(SEED);

    const status = await page.locator("#status-text").innerText();
    expect(status).toContain("0 of 1 file(s) moved");
    expect(status).toContain("1 file(s) not moved");
    expect(status).toContain("Still missing: fail.mp4");

    // The in-place file is truthfully accounted for even though the run failed.
    const ids = await page.evaluate(() => Array.from(window.consolidatedFileIds));
    expect(ids).toEqual([100]);
  });
});
