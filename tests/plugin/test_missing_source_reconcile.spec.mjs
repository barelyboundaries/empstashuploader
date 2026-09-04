import { test, expect } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

// reconcileSourceFiles() — auto-recovery from a stale primary file record:
//
//   - buildMegapack()'s pre-flight probes EVERY file record Stash knows about
//     for each active scene (not just the chosen primary) via POST
//     /api/fs/exists. If the current primary is gone but a sibling file for
//     the same scene is confirmed present, it auto-relinks to it (preferring
//     one already under the seed dir) and the build proceeds with that path.
//   - If nothing for a scene exists anywhere, Build aborts naming the scene
//     (title + filename) instead of dispatching a doomed job that fails deep
//     in task.py's validate_pack_files_present.
//   - The affected scene card gets a "Source file missing" badge and
//     Build/Consolidate stay disabled until it's resolved.
//
// Mocking discipline: NETWORK LAYER ONLY (page.route("**/graphql") + the
// backend :9941 probe endpoint) — same pattern as
// test_build_gating_inplace.spec.mjs.

const SEED = "D:\\Seed";

function serveAssets(page) {
  page.route("**/plugin*/**/review.html*", async (route) => {
    const filePath = path.resolve("plugin/assets/review.html");
    return route.fulfill({ status: 200, contentType: "text/html", body: fs.readFileSync(filePath, "utf8") });
  });
  page.route("**/*review.js*", async (route) => {
    const filePath = path.resolve("plugin/assets/review.js");
    return route.fulfill({ status: 200, contentType: "application/javascript", body: fs.readFileSync(filePath, "utf8") });
  });
}

function file(id, filePath, overrides = {}) {
  return {
    id,
    path: filePath,
    size: 5000000,
    height: 1080,
    width: 1920,
    duration: 600,
    video_codec: "h264",
    oshash: `oshash-${id}`,
    ...overrides
  };
}

function scene(id, title, files) {
  return { id, title, date: "2026-01-01", paths: { screenshot: "" }, files, performers: [], tags: [], studio: null };
}

async function bootHarness(page, { scenes, probeExists = () => false, probeStatus = 200, mode = "megapack" }) {
  serveAssets(page);
  const wire = { probes: [], builds: [], moves: [] };

  await page.route("**/graphql", async (route) => {
    const postData = JSON.parse(route.request().postData() || "{}");
    const query = postData.query || "";
    if (query.includes("FindScenes")) {
      return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ data: { findScenes: { scenes } } }) });
    }
    if (query.includes("FindDestinationCollisions")) {
      return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ data: { findScenes: { scenes: [] } } }) });
    }
    if (query.includes("MoveFiles")) {
      wire.moves.push(postData.variables);
      return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ data: { moveFiles: true } }) });
    }
    if (query.includes("runPluginTask")) {
      wire.builds.push(postData.variables);
      return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ data: { runPluginTask: "job-reconcile-1" } }) });
    }
    return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ data: {} }) });
  });

  await page.route("**/api/fs/exists", async (route) => {
    if (probeStatus !== 200) {
      return route.fulfill({ status: probeStatus, contentType: "application/json", body: JSON.stringify({ error: "Probe failed" }) });
    }
    const postData = JSON.parse(route.request().postData() || "{}");
    const paths = postData.paths || [];
    wire.probes.push(paths);
    const results = {};
    for (const p of paths) results[p] = probeExists(p);
    return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ results }) });
  });

  const sceneIds = scenes.map((s) => s.id).join(",");
  await page.goto(`http://localhost:9999/plugins/empornium-megapack/review.html?scenes=${sceneIds}&mode=${mode}`);
  await expect(page.locator(".scene-card")).toHaveCount(scenes.length);
  await page.locator("#output-dir").fill(SEED);
  return { wire };
}

function buildPayloadOf(buildVariables) {
  const payloadStr = buildVariables.args.find((a) => a.key === "payload")?.value?.str;
  return JSON.parse(payloadStr);
}

test.describe("reconcileSourceFiles — auto-relink to a sibling file on disk", () => {

  test("stale primary + existing sibling under the seed dir -> auto-relinks and Build dispatches with the sibling's path", async ({ page }) => {
    const scenes = [
      scene(10, "Renamed Scene", [
        file(100, `${SEED}\\stale-original-name.mp4`),
        file(101, `${SEED}\\renamed-deduped-name.mp4`, { size: 4800000 })
      ])
    ];
    // Megapack mode requires >=2 scenes to enable the radio, but a single
    // active scene still builds fine via activeScenes(); keep it simple with
    // a second, already-fine scene so mode stays "megapack" without collisions.
    scenes.push(scene(20, "Untouched Scene", [file(200, `${SEED}\\untouched.mp4`)]));

    const { wire } = await bootHarness(page, {
      scenes,
      probeExists: (p) => p !== `${SEED}\\stale-original-name.mp4`
    });

    await expect(page.locator('.scene-card[data-scene-id="10"] .badge-danger')).toHaveCount(0);

    await page.evaluate(() => window.buildMegapack());
    // The relink notice is transient — buildMegapack immediately proceeds to
    // dispatch, and trackJobProgress overwrites #status-text with the queued
    // message. The real assertion is the wire-level one below: the build
    // payload must carry the sibling's path, not the stale primary's.
    await expect(page.locator("#status-text")).toContainText("Task BuildMegapack queued");

    expect(wire.builds).toHaveLength(1);
    const payload = buildPayloadOf(wire.builds[0]);
    const relinkedScene = payload.scenes.find((s) => s.id === 10);
    expect(relinkedScene.path).toBe(`${SEED}\\renamed-deduped-name.mp4`);
  });

  test("stale primary with NO file anywhere on disk -> Build blocked by scene name, badge shown, Consolidate disabled too", async ({ page }) => {
    const scenes = [
      scene(30, "Gone Scene", [file(300, `${SEED}\\gone.mp4`)]),
      scene(40, "Fine Scene", [file(400, `${SEED}\\fine.mp4`)])
    ];
    const { wire } = await bootHarness(page, {
      scenes,
      probeExists: (p) => p === `${SEED}\\fine.mp4`
    });

    await page.evaluate(() => window.buildMegapack());
    await expect(page.locator("#status-text")).toContainText("Build aborted");
    await expect(page.locator("#status-text")).toContainText("Gone Scene (gone.mp4)");
    expect(wire.builds).toHaveLength(0);

    const badge = page.locator('.scene-card[data-scene-id="30"] .badge-danger');
    await expect(badge).toContainText("Source file missing");
    await expect(page.locator('.scene-card[data-scene-id="40"] .badge-danger')).toHaveCount(0);

    const buildBtn = page.locator("#btn-build");
    const consolidateBtn = page.locator("#btn-consolidate");
    await expect(buildBtn).toBeDisabled();
    await expect(consolidateBtn).toBeDisabled();
    await expect(consolidateBtn).toHaveAttribute("title", /no file on disk anywhere/);
  });

  test("everything already under the seed dir and present -> zero relinks, build dispatches unmodified", async ({ page }) => {
    const scenes = [
      scene(50, "Simple Scene", [file(500, `${SEED}\\simple.mp4`)])
    ];
    scenes.push(scene(60, "Second Scene", [file(600, `${SEED}\\second.mp4`)]));
    const { wire } = await bootHarness(page, { scenes, probeExists: () => true });

    await page.evaluate(() => window.buildMegapack());
    expect(wire.builds).toHaveLength(1);
    // No relink/missing banner text on the plain happy path.
    const status = await page.locator("#status-text").innerText();
    expect(status).not.toContain("Auto-relinked");
    expect(status).not.toContain("Build aborted");
  });

  test("stale primary + existing sibling OUTSIDE the seed dir -> Build blocked naming scene, pointing to Consolidate; no dispatch", async ({ page }) => {
    const OUTSIDE = "E:\\Outside";
    const scenes = [
      scene(70, "Outside Sibling Scene", [
        file(700, `${SEED}\\stale-in-seed.mp4`),
        file(701, `${OUTSIDE}\\surviving-outside.mp4`)
      ]),
      scene(80, "Fine Scene", [file(800, `${SEED}\\fine.mp4`)])
    ];

    const { wire } = await bootHarness(page, {
      scenes,
      probeExists: (p) => p === `${OUTSIDE}\\surviving-outside.mp4` || p === `${SEED}\\fine.mp4`
    });

    await page.evaluate(() => window.buildMegapack());

    // Build does NOT dispatch
    expect(wire.builds).toHaveLength(0);

    const statusText = await page.locator("#status-text").innerText();
    expect(statusText).toContain("Build aborted");
    expect(statusText).toContain("Outside Sibling Scene");
    expect(statusText).toContain("surviving-outside.mp4");
    expect(statusText).toContain("run Consolidate to move them");
  });

  test("Consolidate on a scene whose primary is gone but a sibling exists -> move list carries the sibling, not the stale path", async ({ page }) => {
    page.on("dialog", async (dialog) => { await dialog.accept(); });
    const OUTSIDE = "E:\\Outside";
    const scenes = [
      scene(90, "Move Sibling Scene", [
        file(900, `${OUTSIDE}\\stale-primary.mp4`),
        file(901, `${OUTSIDE}\\surviving-sibling.mp4`)
      ]),
      scene(91, "In Place Scene", [file(910, `${SEED}\\in-place.mp4`)])
    ];

    const { wire } = await bootHarness(page, {
      scenes,
      probeExists: (p) => p === `${OUTSIDE}\\surviving-sibling.mp4` || p === `${SEED}\\in-place.mp4`
    });

    await page.locator("#btn-consolidate").click();

    await expect(page.locator("#status-text")).toContainText("Files moved successfully!");
    expect(wire.moves).toHaveLength(1);
    expect(wire.moves[0].input.ids).toEqual([901]);
    expect(wire.moves[0].input.destination_folder).toBe(SEED);
  });

  test("Consolidate on a scene with no file anywhere -> blocked with a clear reason, no move attempted", async ({ page }) => {
    const OUTSIDE = "E:\\Outside";
    const scenes = [
      scene(92, "Missing Everywhere Scene", [file(920, `${OUTSIDE}\\missing.mp4`)]),
      scene(93, "Second Scene", [file(930, `${OUTSIDE}\\fine.mp4`)])
    ];

    const { wire } = await bootHarness(page, {
      scenes,
      probeExists: (p) => p === `${OUTSIDE}\\fine.mp4`
    });

    await page.locator("#btn-consolidate").click();

    expect(wire.moves).toHaveLength(0);
    const status = page.locator("#status-text");
    await expect(status).toContainText("Consolidation aborted");
    await expect(status).toContainText("Missing Everywhere Scene (missing.mp4)");
    await expect(status).toContainText("no file on disk anywhere");

    const badge = page.locator('.scene-card[data-scene-id="92"] .badge-danger');
    await expect(badge).toContainText("Source file missing");
  });

  test("Consolidate when sidecar probe fails (500) -> aborts fail-closed, no move attempted", async ({ page }) => {
    const OUTSIDE = "E:\\Outside";
    const scenes = [
      scene(94, "Probe Error Scene", [file(940, `${OUTSIDE}\\scene.mp4`)]),
      scene(95, "Second Scene", [file(950, `${OUTSIDE}\\second.mp4`)])
    ];

    const { wire } = await bootHarness(page, {
      scenes,
      probeStatus: 500
    });

    await page.locator("#btn-consolidate").click();

    expect(wire.moves).toHaveLength(0);
    const status = page.locator("#status-text");
    await expect(status).toContainText("Consolidation aborted");
    await expect(status).toContainText("filesystem check failed");
  });
});
