import { test, expect } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

// Build gating + pre-flight = all pack files under the seed dir (todo 7 of
// staged-wizard-inplace-seed):
//
//   - Build is enabled iff every active scene's chosen primary file sits under
//     the seed-dir field value (#output-dir, RECURSIVE containment via T6's
//     isPathUnderSeed) and no duplicate collisions are unresolved. The old
//     "direct child of the pack-title subfolder" check is gone — a file nested
//     deeper under the seed dir is in place.
//   - When disabled, the Build button tooltip shows the count AND the exact
//     missing basenames; attempting a build surfaces the same list via
//     showStatus and dispatches nothing.
//   - buildMegapack's pre-flight re-verifies authoritatively via POST
//     /api/fs/exists (chunked ≤100 paths, fail-closed on non-200/network
//     error) and blocks with the exact missing list.
//   - The runPluginTask payload carries seed_dir (the #output-dir value) and
//     scratch_dir when a #scratch-dir input exists (todo 8 adds it; the seam
//     is proven here by injecting the input). Variables are asserted from the
//     intercepted mutation BODY, never from DOM state.
//
// Mocking discipline: NETWORK LAYER ONLY (page.route("**/graphql") + the
// backend :9941 probe endpoint) — same pattern as
// test_consolidate_move_only_missing.spec.mjs.

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
// mock: FindScenes -> scenes, RunBuild/runPluginTask recorded,
// /api/fs/exists recorded + answered via probeExists (or failed with
// probeStatus != 200). Nothing leaks to a real Stash on :9999 or a real
// backend on :9941.
async function bootHarness(page, {
  scenes,
  mode = "megapack",
  probeExists = () => true,
  probeStatus = 200
}) {
  serveAssets(page);
  const wire = { probes: [], builds: [] };

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
    if (query.includes("runPluginTask")) {
      wire.builds.push(postData.variables);
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ data: { runPluginTask: "job-build-gating-1" } })
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

  const sceneIds = scenes.map((s) => s.id).join(",");
  await page.goto(`http://localhost:9999/plugins/empornium-megapack/review.html?scenes=${sceneIds}&mode=${mode}`);
  await expect(page.locator(".scene-card")).toHaveCount(scenes.length);
  // The seed-dir field (currently #output-dir; todo 8 relabels it) drives the
  // build gate — point it at the spec's seed dir.
  await page.locator("#output-dir").fill(SEED);
  // todo 8: the #scratch-dir input is native markup now; fill it so payload
  // assertions are deterministic regardless of a live :9941 sidecar prefill.
  await page.locator("#scratch-dir").fill(SCRATCH);
  return { wire };
}

function payloadOf(buildVariables) {
  const payloadStr = buildVariables.args.find((a) => a.key === "payload")?.value?.str;
  return JSON.parse(payloadStr);
}

test.describe("updateActionAvailability — in-place build gating", () => {

  test("one file outside the seed dir -> Build disabled, tooltip + attempted build show the EXACT filename, nothing dispatches", async ({ page }) => {
    const scenes = [
      scene(10, 100, "D:\\Seed\\present.mp4", "Present"),
      scene(20, 200, "E:\\Elsewhere\\missing_one.mp4", "Missing One")
    ];
    const { wire } = await bootHarness(page, { scenes });

    const buildBtn = page.locator("#btn-build");
    await expect(buildBtn).toBeDisabled();
    // Tooltip: count AND the exact missing basename.
    const tooltip = await buildBtn.getAttribute("title");
    expect(tooltip).toContain("1 file(s) missing from the seed directory");
    expect(tooltip).toContain("missing_one.mp4");

    // The missing-list computation is exported for todo 8's stage gating.
    const missing = await page.evaluate(() => window.computeMissingSeedFiles());
    expect(missing.seedDir).toBe(SEED);
    expect(missing.missing).toHaveLength(1);
    expect(missing.missing[0].name).toBe("missing_one.mp4");
    expect(missing.missing[0].path).toBe("E:\\Elsewhere\\missing_one.mp4");

    // Attempting the build surfaces the exact list via showStatus and
    // dispatches nothing.
    await page.evaluate(() => window.buildMegapack());
    await expect(page.locator("#status-text")).toContainText("Build aborted");
    await expect(page.locator("#status-text")).toContainText("missing_one.mp4");
    expect(wire.builds).toHaveLength(0);
    expect(wire.probes).toHaveLength(0);
  });

  test("nested file under the seed dir is in place — the direct-child check is gone", async ({ page }) => {
    const scenes = [
      scene(10, 100, "D:\\Seed\\a.mp4", "Direct Child"),
      scene(20, 200, "D:\\Seed\\sub\\nested.mp4", "Nested Child")
    ];
    const { wire } = await bootHarness(page, { scenes });

    await expect(page.locator("#btn-build")).toBeEnabled();
    await expect(page.locator("#btn-build")).toHaveAttribute(
      "title",
      "Build megapack torrent, contact sheets, and BBCode"
    );
    // Gating is client-side only — no probe until a build is attempted.
    expect(wire.probes).toHaveLength(0);
  });

  test("unresolved duplicate collisions still disable Build with the collision reason", async ({ page }) => {
    // Two scenes sharing one basename -> duplicate group -> collision reason
    // takes precedence over the missing-file list.
    const scenes = [
      { ...scene(10, 100, "D:\\Seed\\dup.mp4", "Dup A") },
      { ...scene(20, 200, "D:\\Seed2\\dup.mp4", "Dup B") }
    ];
    const { wire } = await bootHarness(page, { scenes });

    const buildBtn = page.locator("#btn-build");
    await expect(buildBtn).toBeDisabled();
    await expect(buildBtn).toHaveAttribute(
      "title",
      "1 filename collision must be resolved first"
    );
    expect(wire.builds).toHaveLength(0);
  });

  test("inline reason element shows the blocking reason when Build is disabled and is hidden when Build is enabled", async ({ page }) => {
    const scenes = [
      scene(10, 100, "D:\\Seed\\a.mp4", "Scene A"),
      scene(20, 200, "E:\\Elsewhere\\b.mp4", "Scene B")
    ];
    await bootHarness(page, { scenes });

    const buildBtn = page.locator("#btn-build");
    const reasonEl = page.locator("#action-disabled-reason");

    // Disabled initially because Scene B is outside seed dir
    await expect(buildBtn).toBeDisabled();
    await expect(reasonEl).toBeVisible();
    const disabledTitle = await buildBtn.getAttribute("title");
    await expect(reasonEl).toHaveText(disabledTitle);
    await expect(reasonEl).toHaveAttribute("role", "status");
    await expect(reasonEl).toHaveAttribute("aria-live", "polite");

    // Removing Scene B leaves only Scene A (under seed dir) -> now only 1 scene, but in Megapack mode that requires 2 scenes
    // Let's replace Scene B with a scene under seed dir by pointing output-dir to root or loading 2 scenes under seed dir
    await page.locator("#output-dir").fill("D:\\");
    await expect(buildBtn).toBeDisabled();
    await expect(reasonEl).toBeVisible();

    // Now set seed dir containing both files
    await page.route("**/graphql", async (route) => {
      const postData = JSON.parse(route.request().postData() || "{}");
      if (postData.query?.includes("FindScenes")) {
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            data: {
              findScenes: {
                scenes: [
                  scene(10, 100, "D:\\Seed\\a.mp4", "Scene A"),
                  scene(20, 200, "D:\\Seed\\b.mp4", "Scene B")
                ]
              }
            }
          })
        });
      }
      return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ data: {} }) });
    });
    await page.evaluate(() => window.loadScenes([10, 20]));
    await page.locator("#output-dir").fill("D:\\Seed");

    await expect(buildBtn).toBeEnabled();
    await expect(reasonEl).toBeHidden();
  });
});

test.describe("buildMegapack pre-flight — authoritative /api/fs/exists check", () => {

  test("all primaries under the seed dir but one mocked missing on disk -> build blocked with the exact filename", async ({ page }) => {
    const scenes = [
      scene(10, 100, "D:\\Seed\\on_disk.mp4", "On Disk"),
      scene(20, 200, "D:\\Seed\\vanished.mp4", "Vanished")
    ];
    const { wire } = await bootHarness(page, {
      scenes,
      probeExists: (p) => !p.endsWith("vanished.mp4")
    });

    await expect(page.locator("#btn-build")).toBeEnabled();

    await page.evaluate(() => window.buildMegapack());
    await expect(page.locator("#status-text")).toContainText("Build aborted");
    await expect(page.locator("#status-text")).toContainText("vanished.mp4");
    expect(wire.builds).toHaveLength(0);
    // The probe covered the primaries the client-side gate considers present.
    expect(wire.probes).toHaveLength(1);
    expect(wire.probes[0].sort()).toEqual(["D:\\Seed\\on_disk.mp4", "D:\\Seed\\vanished.mp4"].sort());
  });

  test("probe endpoint returns non-200 -> fail-closed: build blocked, nothing dispatches", async ({ page }) => {
    const scenes = [scene(10, 100, "D:\\Seed\\a.mp4", "A")];
    const { wire } = await bootHarness(page, { scenes, probeStatus: 500 });

    await expect(page.locator("#btn-build")).toBeEnabled();

    await page.evaluate(() => window.buildMegapack());
    await expect(page.locator("#status-text")).toContainText("Build aborted");
    await expect(page.locator("#status-text")).toContainText("filesystem check failed");
    expect(wire.builds).toHaveLength(0);
  });

  test("probe requests are chunked at <=100 paths per request", async ({ page }) => {
    // 150 scenes -> 2 probe requests (100 + 50), all present -> build fires.
    const scenes = [];
    for (let i = 1; i <= 150; i++) {
      scenes.push(scene(i, 1000 + i, `D:\\Seed\\scene_${String(i).padStart(3, "0")}.mp4`, `Scene ${i}`));
    }
    const { wire } = await bootHarness(page, { scenes });

    await expect(page.locator("#btn-build")).toBeEnabled();
    await page.locator("#btn-build").click();

    // trackJobProgress overwrites the transient "Starting..." status with the
    // queued state once the mutation resolves — wait for that terminal text.
    await expect(page.locator("#status-text")).toContainText("queued (Job ID:");
    expect(wire.builds).toHaveLength(1);
    expect(wire.probes).toHaveLength(2);
    expect(wire.probes[0].length).toBeLessThanOrEqual(100);
    expect(wire.probes[1].length).toBeLessThanOrEqual(100);
    expect(wire.probes[0].length + wire.probes[1].length).toBe(150);
  });
});

test.describe("runPluginTask payload — seed_dir + scratch_dir", () => {

  test("happy path: payload body (from the wire) contains seed_dir = the #output-dir value", async ({ page }) => {
    const scenes = [
      scene(10, 100, "D:\\Seed\\a.mp4", "A"),
      scene(20, 200, "D:\\Seed\\sub\\b.mp4", "B")
    ];
    const { wire } = await bootHarness(page, { scenes });

    await page.locator("#btn-build").click();
    await expect(page.locator("#status-text")).toContainText("queued (Job ID:");

    expect(wire.builds).toHaveLength(1);
    expect(wire.builds[0].task_name).toBe("BuildMegapack");
    const payload = payloadOf(wire.builds[0]);
    expect(payload.seed_dir).toBe(SEED);
    // OLD (todo 7): no #scratch-dir input existed -> the key was omitted.
    // NEW (todo 8): the input is native markup and the harness fills it, so
    // the value flows into the payload.
    expect(payload.scratch_dir).toBe(SCRATCH);
    // OLD (todo 7): payload.output_dir mirrored the seed dir.
    // NEW (todo 8): output_dir is dropped from the UI payload; task.py's
    // legacy fallback covers old payloads only.
    expect(payload.output_dir).toBeUndefined();
  });

  test("scratch_dir omission contract: an empty #scratch-dir keeps the key out of the payload", async ({ page }) => {
    // OLD (todo 7): the seam was proven by injecting a #scratch-dir input
    // because todo 8 had not added it yet. NEW (todo 8): the input is native
    // markup, so the contract worth pinning is the omit-when-empty fallback
    // (task.py's legacy path relies on the key's absence).
    const scenes = [scene(10, 100, "D:\\Seed\\a.mp4", "A")];
    const { wire } = await bootHarness(page, { scenes });

    await page.locator("#scratch-dir").fill("");

    await page.locator("#btn-build").click();
    await expect(page.locator("#status-text")).toContainText("queued (Job ID:");

    const payload = payloadOf(wire.builds[0]);
    expect(payload.seed_dir).toBe(SEED);
    expect(payload.scratch_dir).toBeUndefined();
  });

  test("single-scene mode parity: file under the seed dir builds; payload carries seed_dir", async ({ page }) => {
    const scenes = [scene(10, 100, "D:\\Seed\\solo.mp4", "Solo")];
    const { wire } = await bootHarness(page, { scenes, mode: "single" });

    const buildBtn = page.locator("#btn-build");
    await expect(buildBtn).toBeEnabled();
    await buildBtn.click();
    await expect(page.locator("#status-text")).toContainText("queued (Job ID:");

    expect(wire.builds).toHaveLength(1);
    expect(wire.builds[0].task_name).toBe("BuildSingleScene");
    const payload = payloadOf(wire.builds[0]);
    expect(payload.seed_dir).toBe(SEED);
    expect(payload.single_scene).toBe(true);
  });

  test("single-scene mode parity: file outside the seed dir disables Build with the exact filename", async ({ page }) => {
    const scenes = [scene(10, 100, "Z:\\RawStorage\\solo.mp4", "Solo")];
    await bootHarness(page, { scenes, mode: "single" });

    const buildBtn = page.locator("#btn-build");
    await expect(buildBtn).toBeDisabled();
    const tooltip = await buildBtn.getAttribute("title");
    expect(tooltip).toContain("1 file(s) missing from the seed directory");
    expect(tooltip).toContain("solo.mp4");
  });
});
