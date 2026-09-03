// Captures the README screenshot set for docs/screenshots/.
//
// Everything is mocked at the NETWORK layer (same discipline as the Playwright
// specs): the review UI is served straight off disk and every Stash GraphQL
// call, sidecar endpoint, and job result is answered from the synthetic
// fixtures below. Nothing here touches a real Stash, a real backend, or any
// real library content -- the scenes are invented placeholders so the shots are
// safe to publish.
//
// Usage: node scripts/capture_readme_screenshots.mjs

import { chromium } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

const OUT_DIR = path.resolve("docs/screenshots");
const SEED = "D:\\Media\\Packs";
const SCRATCH = "D:\\Media\\Scratch";
const PACK_TITLE = "Studio Showcase Collection Vol. 1";

// Flat slate placeholder standing in for the Stash thumbnail.
const THUMB =
  "data:image/svg+xml;utf8," +
  encodeURIComponent(
    '<svg xmlns="http://www.w3.org/2000/svg" width="320" height="180">' +
      '<rect width="320" height="180" fill="#1f2937"/>' +
      '<rect x="0.5" y="0.5" width="319" height="179" fill="none" stroke="#374151"/>' +
      '<circle cx="160" cy="90" r="26" fill="none" stroke="#4b5563" stroke-width="3"/>' +
      '<path d="M152 78 l22 12 -22 12 z" fill="#4b5563"/>' +
    '</svg>'
  );

const SCENES = [
  ["Opening Set", "Ravenna Vale", "Northlight Studios", 2412, 1920, 1080],
  ["Rooftop Session", "Ravenna Vale", "Northlight Studios", 1980, 1920, 1080],
  ["Studio Interview", "Marisol Quinn", "Northlight Studios", 1145, 1920, 1080],
  ["Evening Shoot", "Marisol Quinn", "Northlight Studios", 2760, 3840, 2160],
  ["Backstage Reel", "Ivy Sandoval", "Northlight Studios", 1520, 1920, 1080],
  ["Closing Set", "Ivy Sandoval", "Northlight Studios", 2205, 3840, 2160],
].map(([title, performer, studio, duration, width, height], i) => ({
  id: 100 + i,
  title,
  date: `2026-0${(i % 9) + 1}-1${i}`,
  paths: { screenshot: THUMB },
  files: [{
    id: 200 + i,
    path: `${SEED}\\${title.replace(/ /g, "_")}.mp4`,
    size: 1024 * 1024 * (820 + i * 137),
    width,
    height,
    duration,
    video_codec: "h264",
    oshash: `oshash-${200 + i}`,
  }],
  performers: [{ id: `p${i}`, name: performer }],
  tags: [{ id: "t1", name: "Feature" }, { id: "t2", name: height >= 2160 ? "2160p" : "1080p" }],
  studio: { id: "s1", name: studio },
}));

const BUILD_RESULT = {
  found: true,
  result: {
    status: "success",
    pack_title: PACK_TITLE,
    torrent_path: `${SEED}\\${PACK_TITLE}.torrent`,
    manifest_path: `${SCRATCH}\\${PACK_TITLE}_manifest.json`,
    submission_path: `${SCRATCH}\\${PACK_TITLE}_submission.json`,
    bbcode_path: `${SCRATCH}\\${PACK_TITLE}_bbcode.txt`,
    upload_previews: true,
    preview_only: false,
    ready: true,
    tracker_tags: ["1080p", "2160p", "feature", "ivy.sandoval", "marisol.quinn", "northlight.studios", "ravenna.vale"],
    uploaded_urls: [
      "https://hamsterimg.net/images/opening-set-sheet.jpg",
      "https://hamsterimg.net/images/rooftop-session-sheet.jpg",
      "https://hamsterimg.net/images/studio-interview-sheet.jpg",
      "https://hamsterimg.net/images/evening-shoot-sheet.jpg",
      "https://hamsterimg.net/images/backstage-reel-sheet.jpg",
      "https://hamsterimg.net/images/closing-set-sheet.jpg",
    ],
    preflight: {
      ready: true,
      checks: [
        { id: "images_remote", label: "Preview Images", passed: true, detail: "6 contact sheets hosted remotely" },
        { id: "presentation_size", label: "Presentation Size", passed: true, detail: "4.1 MB of 23 MB budget" },
        { id: "tracker_tags", label: "Tracker Tags", passed: true, detail: "7 valid tags" },
        { id: "category", label: "Category", passed: true, is_info: true, detail: "Category — you select this on the upload form." },
        { id: "torrent_valid", label: "Torrent File (torf)", passed: true, detail: "Valid torrent, 6 files, 11.4 GB" },
        { id: "payload_files", label: "Media Files Verification", passed: true, detail: "All 6 files exist on disk" },
        { id: "root_name", label: "Torrent Root Name", passed: true, detail: "Root folder matches pack title" },
      ],
    },
  },
};

let capturedRunId = "run-readme-shot";

const BBCODE = [
  "[center][b][size=5]Studio Showcase Collection Vol. 1[/size][/b][/center]",
  "",
  "[b]Performers:[/b] Ravenna Vale, Marisol Quinn, Ivy Sandoval",
  "[b]Studio:[/b] Northlight Studios",
  "[b]Scenes:[/b] 6   [b]Total:[/b] 11.4 GB   [b]Runtime:[/b] 3h 20m",
  "",
  "[hr]",
  "",
  "[b]1. Opening Set[/b] - 1080p h264 - 40m 12s",
  "[img]https://hamsterimg.net/images/opening-set-sheet.jpg[/img]",
  "",
  "[b]2. Rooftop Session[/b] - 1080p h264 - 33m 0s",
  "[img]https://hamsterimg.net/images/rooftop-session-sheet.jpg[/img]",
].join("\n");

// EMPORNIUM_TASK_BBCODE <runId> <i>/<total>: <base64 chunk>
function bbcodeChunks(runId) {
  const b64 = Buffer.from(BBCODE, "utf8").toString("base64");
  const size = 180;
  const parts = [];
  for (let i = 0; i < b64.length; i += size) parts.push(b64.slice(i, i + size));
  return parts.map((chunk, i) => ({
    time: new Date().toISOString(),
    level: "INFO",
    message: `EMPORNIUM_TASK_BBCODE ${runId} ${i + 1}/${parts.length}: ${chunk}`,
  }));
}

function buildLogs(runId) {
  return [
    { time: new Date().toISOString(), level: "INFO", message: "Building megapack..." },
    ...bbcodeChunks(runId),
    {
      time: new Date().toISOString(),
      level: "INFO",
      message: `EMPORNIUM_TASK_RESULT ${runId}: ${JSON.stringify(BUILD_RESULT.result)}`,
    },
  ];
}

function serveAssets(page) {
  const files = [
    ["**/plugin*/**/review.html*", "plugin/assets/review.html", "text/html"],
    ["**/*review.js*", "plugin/assets/review.js", "application/javascript"],
    ["**/plugin*/**/main.js*", "plugin/main.js", "application/javascript"],
    ["**/plugin*/**/style.css*", "plugin/style.css", "text/css"],
  ];
  for (const [glob, file, type] of files) {
    page.route(glob, (route) =>
      route.fulfill({ status: 200, contentType: type, body: fs.readFileSync(path.resolve(file), "utf8") }));
  }
}

async function boot(page) {
  serveAssets(page);

  await page.route("**/api/fs/exists*", async (route) => {
    const body = JSON.parse(route.request().postData() || "{}");
    const results = {};
    for (const p of body.paths || []) results[p] = true;
    return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ results }) });
  });

  await page.route("**/api/run/*", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(BUILD_RESULT) }));

  await page.route("**/health", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        status: "ok",
        track: "Empornium Megapack Builder",
        version: "0.2.0", // must equal EXPECTED_SIDECAR_VERSION in review.js
        output_dir: SEED,
        scratch_dir: SCRATCH,
        hamster_configured: true,
      }),
    }));

  await page.route("**/graphql", async (route) => {
    const body = JSON.parse(route.request().postData() || "{}");
    const query = body.query || "";
    const json = (data) =>
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ data }) });

    // Must precede the generic FindScenes branch: the destination-collision
    // probe is also a findScenes query, and answering it with the pack's own
    // scenes would report every file as colliding with itself.
    if (query.includes("FindDestinationCollisions")) return json({ findScenes: { scenes: [] } });
    if (query.includes("FindScenes")) return json({ findScenes: { scenes: SCENES } });
    if (query.includes("StageDirCheck")) return json({ directory: { path: body.variables?.path || "" } });
    if (query.includes("runPluginTask")) {
      // The UI mints a run_id per dispatch and then looks for log sentinels
      // addressed to it, so the sentinels below have to use the same value.
      const payloadArg = (body.variables?.args || []).find((a) => a.key === "payload");
      try {
        capturedRunId = JSON.parse(payloadArg?.value?.str || "{}").run_id || capturedRunId;
      } catch { /* leave the previous value */ }
      return json({ runPluginTask: "job-readme-shot" });
    }
    // Stash's job API does not expose plugin stdout, so the backend publishes
    // its result -- and the rendered BBCode, base64 and chunked -- as log lines.
    if (query.includes("logs")) return json({ logs: buildLogs(capturedRunId) });
    if (query.includes("FindJob") || query.includes("findJob"))
      return json({ findJob: { id: "job-readme-shot", status: "FINISHED", progress: 1.0, error: null } });
    return json({});
  });

  const ids = SCENES.map((s) => s.id).join(",");
  await page.goto(`http://localhost:9999/plugins/empornium-megapack/review.html?scenes=${ids}&mode=megapack`);
  await page.locator(".scene-card").first().waitFor({ timeout: 15000 });
}

async function shot(page, name) {
  await page.waitForTimeout(400);
  await page.screenshot({ path: path.join(OUT_DIR, name) });
  console.log("wrote", name);
}

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 2 });

fs.mkdirSync(OUT_DIR, { recursive: true });
await boot(page);

// Stage 1 — Setup
await page.locator("#pack-title").fill(PACK_TITLE);
await page.locator("#pack-notes").fill("Six scenes, remastered sources, contact sheet per scene.");
await page.locator("#opt-upload-previews").check();
await shot(page, "01-setup.png");

// Stage 2 — Locations
await page.locator("#btn-stage-next").click();
await page.locator("#output-dir").fill(SEED);
await page.locator("#scratch-dir").fill(SCRATCH);
await shot(page, "02-locations.png");

// Stage 3 — Scenes
await page.locator("#btn-stage-next").click();
await page.locator("#stage-item-3.stage-current").waitFor({ timeout: 10000 });
await shot(page, "03-scenes.png");

// Stage 4 — Actions
await page.locator("#btn-stage-next").click();
await page.locator("#stage-item-4.stage-current").waitFor({ timeout: 10000 });
await shot(page, "04-actions.png");

// Build complete — the handoff summary and pre-flight checklist.
await page.locator("#btn-build").click();
await page.locator("#artifact-summary").waitFor({ state: "visible", timeout: 15000 });
// The summary renders below the fold of the options panel; bring the whole
// pre-flight checklist into frame so the hero shot shows the actual result.
await page.locator("#artifact-summary").scrollIntoViewIfNeeded();
await page.waitForTimeout(800);
await shot(page, "05-build-complete.png");

await browser.close();
