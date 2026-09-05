import { test, expect } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

// Change A Specification: Stage 2 Locations Lockout, UI Invalidation,
// Fail-Closed Drift Protection, and ProbeFiles Badge Rendering.

const SEED = "C:\\Packs";
const SCRATCH = "C:\\Scratch";

function serveAssets(page) {
  // Static asset mocks
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

  // Backend sidecar isolation
  page.route("**/api/run/**", async (route) => route.abort("connectionrefused"));
  page.route("**/health", async (route) => {
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ ok: true, status: "connected", version: "0.2.0", scratch_dir: SCRATCH, announce_configured: true, hamster_configured: true })
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

async function bootHarness(page, {
  scenes = [
    scene(1, 101, `${SEED}\\scene1.mp4`, "Scene 1"),
    scene(2, 102, `${SEED}\\scene2.mp4`, "Scene 2")
  ],
  mode = "megapack"
} = {}) {
  serveAssets(page);
  const wire = { tasks: [], mutations: [], dirChecks: [] };

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
      wire.dirChecks.push(p);
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ data: { directory: { path: p } } })
      });
    }

    if (query.includes("runPluginTask")) {
      wire.tasks.push(postData.variables);
      const taskName = postData.variables?.task_name || "GenericTask";
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ data: { runPluginTask: `job-${taskName}-1` } })
      });
    }

    if (query.includes("MoveFiles") || query.includes("moveFiles")) {
      wire.mutations.push(postData.variables);
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ data: { moveFiles: true } })
      });
    }

    return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ data: {} }) });
  });

  const sceneIds = scenes.map((s) => s.id).join(",");
  await page.goto(`http://localhost:9999/plugins/empornium-megapack/review.html?scenes=${sceneIds}&mode=${mode}`);
  await expect(page.locator(".scene-card")).toHaveCount(scenes.length);

  // Setup defaults
  await page.locator("#pack-title").fill("My Staged Pack");
  await page.locator("#output-dir").fill(SEED);
  await page.locator("#scratch-dir").fill(SCRATCH);

  return { wire };
}

async function walkTo(page, targetStage) {
  for (let s = 1; s < targetStage; s++) {
    await page.locator("#btn-stage-next").click();
    await expect(page.locator(`#stage-item-${s + 1}`)).toHaveClass(/stage-current/);
  }
}

test.describe("Change A: Stage 2 Locations Lockout & State Invalidation", () => {

  // Criterion 1: Advancing past stage 2 sets readOnly=true, .locked, aria-readonly="true", disables browse buttons
  test("1. Advancing past Stage 2 locks #output-dir and #scratch-dir, adds .locked and aria-readonly, and disables browse buttons", async ({ page }) => {
    await bootHarness(page);

    // Currently at Stage 1
    await expect(page.locator("#stage-item-1")).toHaveClass(/stage-current/);

    // Advance 1 -> 2
    await page.locator("#btn-stage-next").click();
    await expect(page.locator("#stage-item-2")).toHaveClass(/stage-current/);

    // Verify inputs at Stage 2 are initially editable and unlocked
    const outputDir = page.locator("#output-dir");
    const scratchDir = page.locator("#scratch-dir");
    const browseDir = page.locator("#btn-browse-dir");
    const browseScratch = page.locator("#btn-browse-scratch");
    const unlockBtn = page.locator("#btn-unlock-locations");
    const notice = page.locator("#locations-locked-notice");

    expect(await outputDir.getAttribute("readonly")).toBeNull();
    await expect(outputDir).not.toHaveClass(/locked/);
    await expect(browseDir).toBeEnabled();
    await expect(browseScratch).toBeEnabled();
    await expect(unlockBtn).toBeHidden();
    await expect(notice).toBeHidden();

    // Advance 2 -> 3 (Locations check passes)
    await page.locator("#btn-stage-next").click();
    await expect(page.locator("#stage-item-3")).toHaveClass(/stage-current/);

    // Verify locked state on #output-dir and #scratch-dir
    await expect(outputDir).toHaveAttribute("readonly", "");
    await expect(outputDir).toHaveAttribute("aria-readonly", "true");
    await expect(outputDir).toHaveClass(/locked/);
    await expect(outputDir).not.toBeDisabled();

    await expect(scratchDir).toHaveAttribute("readonly", "");
    await expect(scratchDir).toHaveAttribute("aria-readonly", "true");
    await expect(scratchDir).toHaveClass(/locked/);
    await expect(scratchDir).not.toBeDisabled();

    // Verify browse buttons are disabled
    await expect(browseDir).toBeDisabled();
    await expect(browseScratch).toBeDisabled();
  });

  // Criterion 2: Cycling UI busy state leaves stage 2 inputs locked
  test("2. Cycling UI busy state (true -> false) leaves stage 2 inputs locked and browse buttons disabled", async ({ page }) => {
    await bootHarness(page);
    await walkTo(page, 3); // Advance to Stage 3 (Locations locked)

    const outputDir = page.locator("#output-dir");
    const scratchDir = page.locator("#scratch-dir");
    const browseDir = page.locator("#btn-browse-dir");
    const browseScratch = page.locator("#btn-browse-scratch");

    // Cycle UI busy to true
    await page.evaluate(() => window.setUiBusy(true, "mutation", "TestCycle"));
    await expect(page.locator("#busy-banner")).toBeVisible();
    await expect(outputDir).toBeDisabled(); // Disabled during active busy

    // Cycle UI busy back to false
    await page.evaluate(() => window.setUiBusy(false));
    await expect(page.locator("#busy-banner")).toBeHidden();

    // Verify Stage 2 inputs remain locked, NOT re-enabled for editing
    await expect(outputDir).toHaveAttribute("readonly", "");
    await expect(outputDir).toHaveAttribute("aria-readonly", "true");
    await expect(outputDir).toHaveClass(/locked/);
    await expect(outputDir).not.toBeDisabled();

    await expect(scratchDir).toHaveAttribute("readonly", "");
    await expect(scratchDir).toHaveAttribute("aria-readonly", "true");
    await expect(scratchDir).toHaveClass(/locked/);
    await expect(scratchDir).not.toBeDisabled();

    // Browse buttons must remain disabled
    await expect(browseDir).toBeDisabled();
    await expect(browseScratch).toBeDisabled();
  });

  // Criterion 3: Navigating back to stage 2 maintains lock, showing #btn-unlock-locations and #locations-locked-notice
  test("3. Navigating back to Stage 2 maintains lock, showing #btn-unlock-locations and #locations-locked-notice", async ({ page }) => {
    await bootHarness(page);
    await walkTo(page, 3);

    // Return to Stage 2 via Back button
    await page.locator("#btn-stage-back").click();
    await expect(page.locator("#stage-item-2")).toHaveClass(/stage-current/);

    // Verify lock notice and unlock button are displayed
    await expect(page.locator("#locations-locked-notice")).toBeVisible();
    await expect(page.locator("#btn-unlock-locations")).toBeVisible();

    // Inputs remain locked
    await expect(page.locator("#output-dir")).toHaveClass(/locked/);
    await expect(page.locator("#output-dir")).toHaveAttribute("readonly", "");
    await expect(page.locator("#scratch-dir")).toHaveClass(/locked/);
    await expect(page.locator("#scratch-dir")).toHaveAttribute("readonly", "");

    // Browse buttons remain disabled
    await expect(page.locator("#btn-browse-dir")).toBeDisabled();
    await expect(page.locator("#btn-browse-scratch")).toBeDisabled();
  });

  // Criterion 4A: Dismissing the unlock confirmation prompt keeps locations locked
  test("4A. Dismissing the unlock confirmation prompt keeps locations locked", async ({ page }) => {
    await bootHarness(page);
    await walkTo(page, 3);
    await page.locator("#btn-stage-back").click();

    // Intercept confirm dialog and dismiss (Cancel)
    page.once("dialog", async (dialog) => {
      expect(dialog.type()).toBe("confirm");
      await dialog.dismiss();
    });

    await page.locator("#btn-unlock-locations").click();

    // Lock remains active
    await expect(page.locator("#output-dir")).toHaveClass(/locked/);
    await expect(page.locator("#locations-locked-notice")).toBeVisible();
    await expect(page.locator("#btn-unlock-locations")).toBeVisible();
    await expect(page.locator("#btn-browse-dir")).toBeDisabled();
  });

  // Criterion 4B: Confirming unlock resets lock, clears probe/consolidation state, resets maxStageReached to 2, and updates UI
  test("4B. Confirming unlock resets lock, clears probe/consolidation state, resets maxStageReached to 2, and updates UI", async ({ page }) => {
    await bootHarness(page);
    await walkTo(page, 4); // Walk all the way to Stage 4 (maxStageReached = 4)

    // Populate downstream state before unlock
    await page.evaluate(() => {
      window.consolidatedFileIds.add(101);
      const summary = document.getElementById("artifact-summary");
      if (summary) summary.style.display = "block";
    });

    // Return to Stage 2
    await page.locator("#stage-item-2").click();
    await expect(page.locator("#stage-item-2")).toHaveClass(/stage-current/);

    // Intercept confirm dialog and accept
    page.once("dialog", async (dialog) => {
      await dialog.accept();
    });

    await page.locator("#btn-unlock-locations").click();

    // Lock is reset
    const outputDir = page.locator("#output-dir");
    const scratchDir = page.locator("#scratch-dir");
    expect(await outputDir.getAttribute("readonly")).toBeNull();
    await expect(outputDir).not.toHaveClass(/locked/);
    expect(await scratchDir.getAttribute("readonly")).toBeNull();
    await expect(scratchDir).not.toHaveClass(/locked/);

    // Browse buttons enabled
    await expect(page.locator("#btn-browse-dir")).toBeEnabled();
    await expect(page.locator("#btn-browse-scratch")).toBeEnabled();

    // Notice and unlock button hidden
    await expect(page.locator("#locations-locked-notice")).toBeHidden();
    await expect(page.locator("#btn-unlock-locations")).toBeHidden();

    // Downstream state invalidated
    const state = await page.evaluate(() => ({
      currentStage: window.getWizardStage?.().currentStage,
      maxStageReached: window.getWizardStage?.().maxStageReached,
      consolidatedCount: window.consolidatedFileIds.size
    }));
    expect(state.currentStage).toBe(2);
    expect(state.maxStageReached).toBe(2);
    expect(state.consolidatedCount).toBe(0);

    // Stages 3 and 4 are no longer marked reached
    await expect(page.locator("#stage-item-3")).not.toHaveClass(/reached/);
    await expect(page.locator("#stage-item-4")).not.toHaveClass(/reached/);
    await expect(page.locator("#artifact-summary")).toBeHidden();
  });

  // Criterion 5A: Tampering with DOM input while locked produces a drift error and aborts consolidation
  test("5A. Tampering with DOM input while locked produces a drift error, leaves busyAwaitingJob false, and aborts consolidation", async ({ page }) => {
    const { wire } = await bootHarness(page);
    await walkTo(page, 4); // Stage 4: Actions

    // Tamper with #output-dir in the DOM while locked
    await page.evaluate(() => {
      const input = document.getElementById("output-dir");
      input.value = "C:\\DriftedPath";
    });

    // Attempt Consolidate
    await page.locator("#btn-consolidate").click();

    // Verification: status shows drift error
    const statusText = page.locator("#status-text");
    await expect(statusText).toContainText(/drift/i);

    // UI cleanly unlocked (busyAwaitingJob was false, finally executed setUiBusy(false))
    await expect(page.locator("#busy-banner")).toBeHidden();
    await expect(page.locator("#btn-consolidate")).toBeEnabled();

    // Zero GraphQL MoveFiles mutations dispatched
    expect(wire.mutations).toHaveLength(0);
  });

  // Criterion 5B: Tampering with DOM input while locked produces a drift error and aborts build
  test("5B. Tampering with DOM input while locked produces a drift error, leaves busyAwaitingJob false, and aborts build", async ({ page }) => {
    const { wire } = await bootHarness(page);
    await walkTo(page, 4); // Stage 4: Actions

    // Tamper with #scratch-dir in the DOM while locked
    await page.evaluate(() => {
      const input = document.getElementById("scratch-dir");
      input.value = "C:\\DriftedScratch";
    });

    // Attempt Build
    await page.locator("#btn-build").click();

    // Verification: status shows drift error
    const statusText = page.locator("#status-text");
    await expect(statusText).toContainText(/drift/i);

    // UI cleanly unlocked
    await expect(page.locator("#busy-banner")).toBeHidden();
    await expect(page.locator("#btn-build")).toBeEnabled();

    // Zero build tasks dispatched
    const buildTasks = wire.tasks.filter((t) => t.task_name === "BuildMegapack" || t.task_name === "BuildSingleScene");
    expect(buildTasks).toHaveLength(0);
  });

  // Criterion 6: probeResultsMap populates from ProbeFiles payload and scene badges render
  test("6. probeResultsMap populates from ProbeFiles payload and renders capability badges on scene cards", async ({ page }) => {
    const testScenes = [
      scene(1, 101, `${SEED}\\scene1.mp4`, "Hardlink Scene"),
      scene(2, 102, `${SEED}\\scene2.mp4`, "Copy Scene"),
      scene(3, 103, `${SEED}\\scene3.mp4`, "Duplicate Name Scene"),
      scene(4, 104, `${SEED}\\scene4.mp4`, "Missing Scene")
    ];
    await bootHarness(page, { scenes: testScenes });
    await walkTo(page, 4);

    // Initially cards do not have probe badges
    const cards = page.locator(".scene-card");
    await expect(cards).toHaveCount(4);
    await expect(cards.nth(0).locator(".badge:has-text('Hardlink OK')")).toBeHidden();

    // Dispatch completion of ProbeFiles task via window.onTaskComplete
    const probePayload = {
      status: "success",
      task: "ProbeFiles",
      target_dir: SEED,
      files: [
        { scene_id: 1, path: `${SEED}\\scene1.mp4`, exists: true, can_hardlink: true, is_duplicate_name: false },
        { scene_id: 2, path: `${SEED}\\scene2.mp4`, exists: true, can_hardlink: false, is_duplicate_name: false },
        { scene_id: 3, path: `${SEED}\\scene3.mp4`, exists: true, can_hardlink: false, is_duplicate_name: true },
        { scene_id: 4, path: `${SEED}\\scene4.mp4`, exists: false, can_hardlink: false, is_duplicate_name: false }
      ]
    };

    await page.evaluate((payload) => {
      window.onTaskComplete("ProbeFiles", payload);
    }, probePayload);

    // Verify badges rendered on each scene card
    // Scene 1: Hardlink OK
    await expect(cards.nth(0).locator(".badge-success")).toContainText("⚡ Hardlink OK");
    // Scene 2: Copy Required
    await expect(cards.nth(1).locator(".badge-warning")).toContainText("📋 Copy Required");
    // Scene 3: Duplicate Name
    await expect(cards.nth(2).locator(".badge-danger")).toContainText("⚠️ Duplicate Name");
    // Scene 4: Missing File
    await expect(cards.nth(3).locator(".badge-danger")).toContainText("❌ Missing File");

    // Invalidate state via unlock at Stage 2 and verify badges are cleared
    await page.locator("#stage-item-2").click();
    page.once("dialog", (d) => d.accept());
    await page.locator("#btn-unlock-locations").click();

    // Verify badges cleared upon unlock
    await expect(cards.nth(0).locator(".badge:has-text('Hardlink OK')")).toBeHidden();
    await expect(cards.nth(1).locator(".badge:has-text('Copy Required')")).toBeHidden();
  });
});
