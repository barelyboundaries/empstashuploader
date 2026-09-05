import { test, expect } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

// Wizard stage strip (todo 8 of staged-wizard-inplace-seed):
//
//   - The review UI presents four gated stages — 1. Setup, 2. Locations,
//     3. Scenes, 4. Actions — via a compact rail at the top of the page.
//   - Next validates the CURRENT stage before advancing:
//       Setup:     pack title non-empty.
//       Locations: seed dir + scratch dir non-empty AND both exist.
//                  Existence is verified fail-closed against Stash's
//                  `directory` query (StageDirCheck) — NOT /api/fs/exists,
//                  which is os.path.isfile()-only (main.py fs_exists) and
//                  therefore reports false for EVERY directory. Any GraphQL
//                  error, null payload, or empty path blocks the stage.
//       Scenes:    >= 1 active scene and no unresolved duplicate collisions.
//     A failed gate shows the reason via showStatus(..., true), scrolls the
//     failing stage into view, and does NOT advance.
//   - Back is always free (except on stage 1). Rail click-to-jump works only
//     for already-reached stages; forward movement requires Next validation.
//   - Stage state is plain JS (no storage): initEmporniumReview starts at 1.
//   - GET /health scratch_dir prefills #scratch-dir once at init, never
//     clobbering user input.
//   - The runPluginTask payload (intercepted from the WIRE, not the DOM)
//     carries seed_dir + scratch_dir; output_dir is dropped (todo 8).
//
// Mocking discipline: NETWORK LAYER ONLY (page.route("**/graphql") + the
// backend :9941 endpoints) — same pattern as test_build_gating_inplace.spec.mjs.

const SEED = "D:\\Seed";
const SCRATCH = "D:\\Scratch";

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
// mock: FindScenes -> scenes, StageDirCheck -> per dirExists, RunBuild/
// runPluginTask recorded, /api/fs/exists recorded + answered via probeExists,
// /health -> scratch_dir when healthScratch is set (with an optional delay so
// prefill-vs-user-input races are deterministic). Nothing leaks to a real
// Stash on :9999 or a real backend on :9941.
async function bootHarness(page, {
  scenes,
  mode = "megapack",
  probeExists = () => true,
  probeStatus = 200,
  dirExists = () => true,
  healthScratch = null,
  healthDelayMs = 0,
  fillDirs = true
} = {}) {
  serveAssets(page);
  const wire = { probes: [], builds: [], dirChecks: [], startBackend: null };

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
      if (dirExists(p)) {
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ data: { directory: { path: p } } })
        });
      }
      // Fail-closed shape: a GraphQL error must block the stage, never pass it.
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ errors: [{ message: `directory not found: ${p}` }] })
      });
    }
    if (query.includes("runPluginTask")) {
      if (postData.variables?.task_name === "StartBackend") {
        wire.startBackend = postData.variables;
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ data: { runPluginTask: "job-start-backend-1" } })
        });
      }
      wire.builds.push(postData.variables);
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ data: { runPluginTask: "job-wizard-stages-1" } })
      });
    }
    // Record + fulfill anything else — never leak to a real Stash on :9999.
    return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ data: {} }) });
  });

  await page.route("**/api/fs/exists", async (route) => {
    if (probeStatus !== 200) {
      return route.fulfill({
        status: probeStatus,
        contentType: "application/json",
        body: JSON.stringify({ error: "probe failed (simulated)" })
      });
    }
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

  await page.route("**/health", async (route) => {
    if (healthScratch === null) {
      // Simulate an unreachable sidecar: init must survive this silently.
      return route.abort("connectionrefused");
    }
    if (healthDelayMs > 0) {
      await new Promise((resolve) => setTimeout(resolve, healthDelayMs));
    }
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        status: "ok",
        track: "Empornium Megapack Builder",
        version: "0.2.0",
        output_dir: "C:\\Downloads\\Megapacks",
        scratch_dir: healthScratch,
        hamster_configured: true,
        announce_configured: true
      })
    });
  });

  const sceneIds = scenes.map((s) => s.id).join(",");
  await page.goto(`http://localhost:9999/plugins/empornium-megapack/review.html?scenes=${sceneIds}&mode=${mode}`);
  await expect(page.locator(".scene-card")).toHaveCount(scenes.length);
  // The seed/scratch fields drive the stage gates — point them at the spec's
  // paths (the health prefill may also have filled scratch; fill() wins).
  if (fillDirs) {
    await page.locator("#output-dir").fill(SEED);
    await page.locator("#scratch-dir").fill(SCRATCH);
  }
  return { wire };
}

function payloadOf(buildVariables) {
  const payloadStr = buildVariables.args.find((a) => a.key === "payload")?.value?.str;
  return JSON.parse(payloadStr);
}

function stageItem(page, n) {
  return page.locator(`#stage-item-${n}`);
}

async function expectCurrentStage(page, n) {
  await expect(stageItem(page, n)).toHaveClass(/stage-current/);
}

// Walks forward 1 -> n by clicking Next, waiting for each advance (the
// Locations hop is async: it verifies both directories first).
async function walkTo(page, n) {
  if (n > 1) {
    // The pack title starts EMPTY (no placeholder default since the release
    // audit) and the Setup gate blocks Next while it is empty.
    await page.locator("#pack-title").fill("My Staged Pack");
  }
  for (let s = 1; s < n; s++) {
    await page.locator("#btn-stage-next").click();
    await expectCurrentStage(page, s + 1);
  }
}

test.describe("wizard stage rail — rendering", () => {

  test("four stages render with Setup current; Back disabled; forward stages not yet reached; init survives a failed /health fetch", async ({ page }) => {
    const scenes = [scene(10, 100, `${SEED}\\a.mp4`, "A")];
    await bootHarness(page, { scenes }); // healthScratch null -> /health aborted

    await expect(page.locator("#stage-rail")).toBeVisible();
    const labels = await page.locator("#stage-rail .stage-label").allInnerTexts();
    expect(labels).toEqual(["Setup", "Locations", "Scenes", "Actions"]);

    await expectCurrentStage(page, 1);
    await expect(stageItem(page, 1)).toHaveClass(/stage-current/);
    for (const n of [2, 3, 4]) {
      await expect(stageItem(page, n)).not.toHaveClass(/reached/);
    }
    await expect(page.locator("#btn-stage-back")).toBeDisabled();
    await expect(page.locator("#btn-stage-next")).toBeEnabled();

    const state = await page.evaluate(() => window.getWizardStage());
    expect(state).toEqual({ currentStage: 1, maxStageReached: 1 });
  });
});

test.describe("wizard stage gates — Next validates before advancing", () => {

  test("Setup: empty pack title blocks Next with the reason shown; filling it advances to Locations", async ({ page }) => {
    const scenes = [scene(10, 100, `${SEED}\\a.mp4`, "A")];
    await bootHarness(page, { scenes });

    await page.locator("#pack-title").fill("");
    await page.locator("#btn-stage-next").click();

    await expect(page.locator("#status-text")).toContainText("Pack title is empty");
    await expectCurrentStage(page, 1);
    // Error styling: showStatus(..., true) paints the reason var(--danger).
    const color = await page.locator("#status-text").evaluate((el) => getComputedStyle(el).color);
    expect(color).not.toBe("");

    await page.locator("#pack-title").fill("My Staged Pack");
    await page.locator("#btn-stage-next").click();
    await expectCurrentStage(page, 2);
  });

  test("Locations: empty scratch dir blocks; a dir failing the existence check blocks fail-closed; valid dirs advance to Scenes", async ({ page }) => {
    const scenes = [scene(10, 100, `${SEED}\\a.mp4`, "A")];
    // Only the seed dir "exists" — the scratch path fails the check.
    const { wire } = await bootHarness(page, { scenes, dirExists: (p) => p === SEED });
    await walkTo(page, 2);

    // Empty scratch dir -> blocked with the reason.
    await page.locator("#scratch-dir").fill("");
    await page.locator("#btn-stage-next").click();
    await expect(page.locator("#status-text")).toContainText("Scratch directory is empty");
    await expectCurrentStage(page, 2);

    // Non-empty but nonexistent -> fail-closed block naming the path.
    await page.locator("#scratch-dir").fill("D:\\Does\\Not\\Exist");
    await page.locator("#btn-stage-next").click();
    await expect(page.locator("#status-text")).toContainText("not found or could not be verified");
    await expect(page.locator("#status-text")).toContainText("D:\\Does\\Not\\Exist");
    await expectCurrentStage(page, 2);

    // Both dirs verifiable -> advance. Both paths were actually checked.
    await page.locator("#scratch-dir").fill(SEED);
    await page.locator("#btn-stage-next").click();
    await expectCurrentStage(page, 3);
    expect(wire.dirChecks).toContain(SEED);
    expect(wire.dirChecks).toContain("D:\\Does\\Not\\Exist");
  });

  test("Locations: empty seed dir blocks with the reason shown", async ({ page }) => {
    const scenes = [scene(10, 100, `${SEED}\\a.mp4`, "A")];
    await bootHarness(page, { scenes });
    await walkTo(page, 2);

    await page.locator("#output-dir").fill("");
    await page.locator("#btn-stage-next").click();
    await expect(page.locator("#status-text")).toContainText("Seed directory is empty");
    await expectCurrentStage(page, 2);
  });

  test("Scenes: unresolved duplicate collisions block Next with the reason; resolving them advances to Actions", async ({ page }) => {
    const scenes = [
      scene(10, 100, `${SEED}\\dup.mp4`, "Dup A"),
      scene(20, 200, "E:\\Other\\dup.mp4", "Dup B")
    ];
    await bootHarness(page, { scenes });
    await walkTo(page, 3);

    await page.locator("#btn-stage-next").click();
    await expect(page.locator("#status-text")).toContainText("unresolved filename collision");
    await expectCurrentStage(page, 3);

    // Resolve via the established control: keep the first, remove the rest.
    await page.locator("#btn-keep-first").click();
    await page.locator("#btn-stage-next").click();
    await expectCurrentStage(page, 4);
  });

  test("Scenes: no active scenes blocks Next with the reason shown", async ({ page }) => {
    const scenes = [scene(10, 100, `${SEED}\\a.mp4`, "A")];
    await bootHarness(page, { scenes });
    await walkTo(page, 3);

    await page.evaluate(() => window.removeSceneFromPack(10));
    await page.locator("#btn-stage-next").click();
    await expect(page.locator("#status-text")).toContainText("No active scenes");
    await expectCurrentStage(page, 3);
  });
});

test.describe("wizard stage navigation — Back and click-to-jump", () => {

  test("Back always returns: 4 -> 3 -> 2 -> 1, then Back is disabled on stage 1", async ({ page }) => {
    const scenes = [scene(10, 100, `${SEED}\\a.mp4`, "A")];
    await bootHarness(page, { scenes });
    await walkTo(page, 4);

    for (const n of [3, 2, 1]) {
      await page.locator("#btn-stage-back").click();
      await expectCurrentStage(page, n);
    }
    await expect(page.locator("#btn-stage-back")).toBeDisabled();
  });

  test("rail click-to-jump works only for already-reached stages; forward movement requires Next", async ({ page }) => {
    const scenes = [scene(10, 100, `${SEED}\\a.mp4`, "A")];
    await bootHarness(page, { scenes });
    await walkTo(page, 3); // stages 1-3 reached

    await stageItem(page, 1).click();
    await expectCurrentStage(page, 1);

    // Stage 4 was never reached -> clicking it must NOT jump forward.
    await stageItem(page, 4).click();
    await expectCurrentStage(page, 1);

    // Forward again only through Next validation.
    await page.locator("#btn-stage-next").click();
    await expectCurrentStage(page, 2);
  });
});

test.describe("wizard scratch-dir prefill from GET /health", () => {

  test("empty #scratch-dir is prefilled from the /health scratch_dir field", async ({ page }) => {
    const scenes = [scene(10, 100, `${SEED}\\a.mp4`, "A")];
    // fillDirs=false leaves the field empty so the init-time prefill owns it.
    await bootHarness(page, { scenes, healthScratch: "E:\\HealthScratch", fillDirs: false });
    await expect(page.locator("#scratch-dir")).toHaveValue("E:\\HealthScratch");
  });

  test("prefill never clobbers user input (delayed /health response loses the race)", async ({ page }) => {
    const scenes = [scene(10, 100, `${SEED}\\a.mp4`, "A")];
    await bootHarness(page, { scenes, healthScratch: "E:\\HealthScratch", healthDelayMs: 800, fillDirs: false });

    // Fill BEFORE the delayed health response lands.
    await page.locator("#scratch-dir").fill("D:\\ManualChoice");
    await page.waitForTimeout(1200);
    await expect(page.locator("#scratch-dir")).toHaveValue("D:\\ManualChoice");
  });
});

test.describe("wizard end-to-end — build payload from the wire", () => {

  test("walking all four stages then Build dispatches runPluginTask with seed_dir + scratch_dir (output_dir dropped)", async ({ page }) => {
    const scenes = [
      scene(10, 100, `${SEED}\\a.mp4`, "A"),
      scene(20, 200, `${SEED}\\sub\\b.mp4`, "B")
    ];
    const { wire } = await bootHarness(page, { scenes });

    await page.locator("#pack-title").fill("My Staged Pack");
    await walkTo(page, 4);
    await page.locator("#btn-build").click();
    await expect(page.locator("#status-text")).toContainText("queued (Job ID:");

    expect(wire.builds).toHaveLength(1);
    expect(wire.builds[0].task_name).toBe("BuildMegapack");
    const payload = payloadOf(wire.builds[0]);
    expect(payload.seed_dir).toBe(SEED);
    expect(payload.scratch_dir).toBe(SCRATCH);
    // todo 8 drops output_dir from the UI payload; task.py's legacy fallback
    // covers old payloads only.
    expect(payload.output_dir).toBeUndefined();
  });
});
