import { test, expect } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

const REMOTE_COVER = "https://hamsterimg.net/images/pasted_cover_test.jpg";
const FULL_CHUNKED_BBCODE =
  "[center][b][size=5]Chunked BBCode Test [2160p][/size][/b][/center]\n\n" +
  "[url=https://hamsterimg.net/scene1.jpg][img=200]https://hamsterimg.net/scene1.jpg[/img][/url]\n" +
  "[quote]Special Unicode: ★★★★★ — Performer 桜井[/quote]";

function serveAssets(page) {
  // REMOTE_COVER must actually resolve for the thumbnail <img> to get a
  // bounding box: left as a live request the fixture 404s and toBeVisible
  // degrades into a broken-image-rendering coin flip. Fulfill it locally.
  page.route(REMOTE_COVER, (route) =>
    route.fulfill({
      status: 200,
      contentType: "image/png",
      body: Buffer.from(
        "iVBORw0KGgoAAAANSUhEUgAAAAoAAAAKCAYAAACNMs+9AAAAFUlEQVR42mP8z8BQz0AEYBxVSF+FABJADveWkH6oAAAAAElFTkSuQmCC",
        "base64"
      ),
    })
  );
  page.route("**/plugin*/**/main.js*", (route) =>
    route.fulfill({ status: 200, contentType: "application/javascript", body: fs.readFileSync(path.resolve("plugin/main.js"), "utf8") })
  );
  page.route("**/plugin*/**/style.css*", (route) =>
    route.fulfill({ status: 200, contentType: "text/css", body: fs.readFileSync(path.resolve("plugin/style.css"), "utf8") })
  );
  page.route("**/plugin*/**/review.html*", (route) =>
    route.fulfill({ status: 200, contentType: "text/html", body: fs.readFileSync(path.resolve("plugin/assets/review.html"), "utf8") })
  );
  page.route("**/*review.js*", (route) =>
    route.fulfill({ status: 200, contentType: "application/javascript", body: fs.readFileSync(path.resolve("plugin/assets/review.js"), "utf8") })
  );
}

function chunkBase64(str, chunkSize = 40) {
  const b64 = Buffer.from(str, "utf8").toString("base64");
  const chunks = [];
  for (let i = 0; i < b64.length; i += chunkSize) {
    chunks.push(b64.slice(i, i + chunkSize));
  }
  return chunks;
}

async function captureRunId(page) {
  await page.addInitScript(() => {
    const orig = window.fetch;
    window.fetch = function (...args) {
      try {
        const init = args[1] || {};
        const parsed = JSON.parse(String(init.body || "{}"));
        const args_ = parsed?.variables?.args || [];
        for (const arg of args_) {
          if (arg?.key !== "payload") continue;
          const raw = arg?.value?.str ?? arg?.value;
          const payload = typeof raw === "string" ? JSON.parse(raw) : raw;
          if (payload?.run_id) window.__lastRunId = payload.run_id;
        }
      } catch (_) {}
      return orig.apply(this, args);
    };
  });
}

function setupGraphQLMocks(page, { bbcodeMode = "chunked_full", lastRecordedTask = {} } = {}) {
  serveAssets(page);

  // Build pre-flight (todo 7 of staged-wizard-inplace-seed): the authoritative
  // on-disk probe must succeed or the build is blocked fail-closed.
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

  page.route("**/graphql", async (route) => {
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
                {
                  id: 10,
                  title: "Anji & Honey",
                  date: "2026-08-01",
                  files: [{ id: 101, path: "C:/Packs/anji.mp4", size: 1048576, height: 2160, width: 3840, duration: 1057, video_codec: "h264" }],
                  performers: [{ id: "p1", name: "Auhneesh Nicole" }],
                  tags: [{ id: "t1", name: "Big Ass" }]
                }
              ]
            }
          }
        })
      });
    }

    if (query.includes("runPluginTask")) {
      lastRecordedTask.task_name = postData?.variables?.task_name;
      lastRecordedTask.args = postData?.variables?.args;
      return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ data: { runPluginTask: "job-defect-1" } }) });
    }

    if (query.includes("findJob")) {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ data: { findJob: { id: "job-defect-1", status: "FINISHED", progress: 1.0, error: null } } })
      });
    }

    if (query.includes("logs")) {
      const runId = await page.evaluate(() => window.__lastRunId || "");
      const messages = [];
      if (lastRecordedTask.task_name === "UploadCoverImage") {
        messages.push({
          time: "2026-08-30T10:00:00Z",
          level: "Info",
          message: `DEEPSEEK_TASK_RESULT ${runId}: ` + JSON.stringify({
            status: "success",
            task: "UploadCoverImage",
            run_id: runId,
            cover_url: REMOTE_COVER,
            local_path: "C:/staging/pasted_covers/cover.jpg"
          })
        });
      } else if (runId) {
        messages.push({
          time: "2026-08-30T10:00:00Z",
          level: "Info",
          message:
            `DEEPSEEK_TASK_RESULT ${runId}: ` +
            JSON.stringify({
              status: "success",
              pack_title: "Anji & Honey",
              bbcode_truncated: true,
              uploaded_urls: ["https://hamsterimg.net/scene1.jpg"],
              tracker_tags: ["big.ass", "pov"],
              torrent_path: "C:\\Packs\\Anji & Honey.torrent",
              preview_only: false,
              ready: true,
              preflight: {
                ready: true,
                checks: [{ id: "images_remote", label: "Preview Images", passed: true, detail: "All 1 preview image(s) hosted remotely" }]
              }
            })
        });

        const chunks = chunkBase64(FULL_CHUNKED_BBCODE, 30);
        const total = chunks.length;
        if (bbcodeMode === "chunked_full") {
          chunks.forEach((chunk, i) => {
            messages.push({
              time: "2026-08-30T10:00:01Z",
              level: "Info",
              message: `DEEPSEEK_TASK_BBCODE ${runId} ${i + 1}/${total}: ${chunk}`
            });
          });
        } else if (bbcodeMode === "chunked_missing_one") {
          // Omit the second chunk
          chunks.slice(0, 1).forEach((chunk, i) => {
            messages.push({
              time: "2026-08-30T10:00:01Z",
              level: "Info",
              message: `DEEPSEEK_TASK_BBCODE ${runId} ${i + 1}/${total}: ${chunk}`
            });
          });
        }
      }
      return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ data: { logs: messages } }) });
    }

    return route.continue();
  });
}

test.describe("Defect Fixes UX: Chunked BBCode and Pasted Cover Image", () => {
  test("a mocked log stream carrying chunked sentinel lines results in #bbcode-preview holding the full text", async ({ page }) => {
    await captureRunId(page);
    setupGraphQLMocks(page, { bbcodeMode: "chunked_full" });

    await page.goto("http://localhost:9999/plugins/empornium-megapack/review.html?scenes=10&mode=single");
    await page.locator("#output-dir").fill("C:\\Packs");
    await expect(page.locator("#loading-state")).toBeHidden({ timeout: 5000 });
    await page.locator("#pack-title").fill("Anji & Honey");
    await page.locator("#btn-build").click();
    await expect(page.locator("#artifact-summary")).toBeVisible({ timeout: 8000 });

    const bbcode = await page.locator("#bbcode-preview").innerText();
    expect(bbcode).toBe(FULL_CHUNKED_BBCODE);
    await expect(page.locator("#bbcode-warning")).toBeHidden();
  });

  test("a stream missing one chunk leaves the preview unchanged and shows the provisional warning", async ({ page }) => {
    await captureRunId(page);
    setupGraphQLMocks(page, { bbcodeMode: "chunked_missing_one" });

    await page.goto("http://localhost:9999/plugins/empornium-megapack/review.html?scenes=10&mode=single");
    await page.locator("#output-dir").fill("C:\\Packs");
    await expect(page.locator("#loading-state")).toBeHidden({ timeout: 5000 });
    await page.locator("#pack-title").fill("Anji & Honey");
    await page.locator("#btn-build").click();
    await expect(page.locator("#artifact-summary")).toBeVisible({ timeout: 8000 });

    const preview = page.locator("#bbcode-preview");
    // Should NOT contain the full chunked text because it failed to reassemble
    await expect(preview).not.toContainText("Special Unicode: ★★★★★");
    // Warning banner should be visible
    await expect(page.locator("#bbcode-warning")).toBeVisible();
    await expect(page.locator("#bbcode-warning")).toContainText("provisional");
  });

  test("pasting a synthetic image into #cover-paste-zone issues UploadCoverImage mutation and renders thumbnail", async ({ page }) => {
    const recorded = {};
    await captureRunId(page);
    setupGraphQLMocks(page, { lastRecordedTask: recorded });

    await page.goto("http://localhost:9999/plugins/empornium-megapack/review.html?scenes=10&mode=single");
    await expect(page.locator("#loading-state")).toBeHidden({ timeout: 5000 });

    // Synthesize paste event with 10x10 PNG
    const pngBase64 = "iVBORw0KGgoAAAANSUhEUgAAAAoAAAAKCAYAAACNMs+9AAAAFUlEQVR42mP8z8BQz0AEYBxVSF+FABJADveWkH6oAAAAAElFTkSuQmCC";
    await page.evaluate((b64) => {
      const byteCharacters = atob(b64);
      const byteNumbers = new Array(byteCharacters.length);
      for (let i = 0; i < byteCharacters.length; i++) {
        byteNumbers[i] = byteCharacters.charCodeAt(i);
      }
      const byteArray = new Uint8Array(byteNumbers);
      const blob = new Blob([byteArray], { type: "image/png" });
      const file = new File([blob], "synthetic_cover.png", { type: "image/png" });

      const dt = new DataTransfer();
      dt.items.add(file);

      const pasteEvent = new ClipboardEvent("paste", {
        bubbles: true,
        cancelable: true,
        clipboardData: dt
      });

      const zone = document.getElementById("cover-paste-zone");
      zone.dispatchEvent(pasteEvent);
    }, pngBase64);

    // Wait for the thumbnail to render from remote URL
    const previewImg = page.locator("#cover-preview");
    await expect(previewImg).toBeVisible({ timeout: 8000 });
    await expect(previewImg).toHaveAttribute("src", REMOTE_COVER);
    await expect(page.locator("#btn-remove-cover")).toBeVisible();

    // Verify task mutation was dispatched with UploadCoverImage
    expect(recorded.task_name).toBe("UploadCoverImage");
    const payloadArg = recorded.args?.find((a) => a.key === "payload");
    const parsedPayload = JSON.parse(payloadArg?.value?.str || "{}");
    expect(parsedPayload.image_b64).toBeTruthy();
    expect(parsedPayload.filename).toBe("synthetic_cover.png");
  });
});
