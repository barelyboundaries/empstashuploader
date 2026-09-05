import { test, expect } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

const HOST_PAGE = `<!doctype html><html><head><meta charset="utf-8"></head>
<body style="margin:0"><div style="height:3000px">stash page</div></body></html>`;

function readAsset(rel) {
  return fs.readFileSync(path.resolve(rel), "utf8");
}

async function mountModal(page, { viewport = { width: 1280, height: 720 } } = {}) {
  await page.setViewportSize(viewport);
  await page.route("**/host-page", (route) =>
    route.fulfill({ status: 200, contentType: "text/html", body: HOST_PAGE })
  );
  await page.goto("http://localhost:9999/host-page");

  const modalCss = readAsset("plugin/style.css");
  const reviewHtml = readAsset("plugin/assets/review.html");
  const reviewJs = readAsset("plugin/assets/review.js");

  await page.evaluate(
    ({ modalCss, reviewHtml }) => {
      const style = document.createElement("style");
      style.textContent = modalCss;
      document.head.appendChild(style);

      const overlay = document.createElement("div");
      overlay.className = "empornium-modal-overlay";
      overlay.id = "empornium-megapack-modal";
      const container = document.createElement("div");
      container.className = "empornium-modal-container";
      const header = document.createElement("div");
      header.className = "empornium-modal-header";
      header.innerHTML = `
        <div class="empornium-modal-title">
          <span class="empornium-logo">??</span>
          <span>Empornium Megapack Builder</span>
          <span class="empornium-badge">1 scene(s) selected</span>
          <span id="empornium-header-slot"></span>
        </div>
        <button class="empornium-modal-close" title="Close (Esc)">&times;</button>
      `;
      const body = document.createElement("div");
      body.className = "empornium-modal-body";
      container.appendChild(header);
      container.appendChild(body);
      overlay.appendChild(container);
      document.body.appendChild(overlay);

      const doc = new DOMParser().parseFromString(reviewHtml, "text/html");
      const reviewStyle = document.createElement("style");
      reviewStyle.textContent = doc.querySelector("style").textContent;
      document.head.appendChild(reviewStyle);
      body.innerHTML = doc.body.innerHTML;
    },
    { modalCss, reviewHtml }
  );

  await page.evaluate((js) => {
    const script = document.createElement("script");
    script.textContent = js;
    document.body.appendChild(script);
  }, reviewJs);
}

async function mountStandalone(page, { viewport = { width: 1280, height: 720 } } = {}) {
  await page.setViewportSize(viewport);
  await page.route("**/host-page", (route) =>
    route.fulfill({ status: 200, contentType: "text/html", body: HOST_PAGE })
  );
  await page.goto("http://localhost:9999/host-page");

  const reviewHtml = readAsset("plugin/assets/review.html");
  const reviewJs = readAsset("plugin/assets/review.js");

  await page.evaluate(
    ({ reviewHtml }) => {
      const doc = new DOMParser().parseFromString(reviewHtml, "text/html");
      const reviewStyle = document.createElement("style");
      reviewStyle.textContent = doc.querySelector("style").textContent;
      document.head.appendChild(reviewStyle);
      document.body.innerHTML = doc.body.innerHTML;
    },
    { reviewHtml }
  );

  await page.evaluate((js) => {
    const script = document.createElement("script");
    script.textContent = js;
    document.body.appendChild(script);
  }, reviewJs);
}

test.describe("Fix 1: Build result panel scrolling & pointer-events", () => {
  test("build-console-result computes pointer-events: auto and wheel scrolls to copy row", async ({ page }) => {
    await mountModal(page, { viewport: { width: 1200, height: 700 } });

    // Populate build result with tall content
    await page.evaluate(() => {
      const longBBCode = Array.from({ length: 60 }, (_, i) => `[b]Line ${i + 1}[/b]: Description of item ${i + 1}`).join("\n");
      window.renderBuildResult({
        pack_title: "Test Megapack Title",
        tags: ["tag1", "tag2", "tag3"],
        torrent_path: "C:\\Torrents\\test.torrent",
        manifest_path: "C:\\Torrents\\manifest.json",
        submission_path: "C:\\Torrents\\sub.json",
        bbcode_path: "C:\\Torrents\\bbcode.txt",
        cover_url: "https://example.com/cover.jpg",
        bbcode: longBBCode
      });
    });

    const consoleResult = page.locator("#build-console-result");
    await expect(consoleResult).toBeVisible();

    // Assert computed pointer-events is auto
    const pointerEvents = await consoleResult.evaluate((el) => window.getComputedStyle(el).pointerEvents);
    expect(pointerEvents).toBe("auto");

    // Check that the result container overflows vertically
    const isOverflowing = await consoleResult.evaluate((el) => el.scrollHeight > el.clientHeight);
    expect(isOverflowing).toBe(true);

    // Hover over the result panel and perform mouse wheel scrolling
    const box = await consoleResult.boundingBox();
    expect(box).not.toBeNull();
    await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);

    const initialScrollTop = await consoleResult.evaluate((el) => el.scrollTop);
    await page.mouse.wheel(0, 800);
    await page.waitForTimeout(300);

    const scrolledScrollTop = await consoleResult.evaluate((el) => el.scrollTop);
    expect(scrolledScrollTop).toBeGreaterThan(initialScrollTop);

    // Scroll all the way to reach .result-copy-row
    const copyRow = page.locator(".result-copy-row");
    await copyRow.scrollIntoViewIfNeeded();
    await expect(copyRow).toBeVisible();

    // Verify copy buttons inside the copy row are visible and enabled
    const copyTitleBtn = page.locator("#btn-copy-title");
    const copyTagsBtn = page.locator("#btn-copy-tags");
    const copyTorrentBtn = page.locator("#btn-copy-torrent-path");
    const copyCoverBtn = page.locator("#btn-copy-cover-url");
    const copyAllBtn = page.locator("#btn-copy-all");

    await expect(copyTitleBtn).toBeVisible();
    await expect(copyTitleBtn).toBeEnabled();
    await expect(copyTagsBtn).toBeVisible();
    await expect(copyTagsBtn).toBeEnabled();
    await expect(copyTorrentBtn).toBeVisible();
    await expect(copyTorrentBtn).toBeEnabled();
    await expect(copyCoverBtn).toBeVisible();
    await expect(copyCoverBtn).toBeEnabled();
    await expect(copyAllBtn).toBeVisible();
    await expect(copyAllBtn).toBeEnabled();
  });
});

test.describe("Fix 2: Fold duplicated header into single modal title bar", () => {
  test("with #empornium-header-slot present: #sidecar-status, #btn-sidecar-stop and #btn-history end up in slot and #review-header is hidden", async ({ page }) => {
    await mountModal(page);

    const slot = page.locator("#empornium-header-slot");
    const reviewHeader = page.locator("#review-header");

    // Assert #review-header is hidden
    await expect(reviewHeader).toBeHidden();

    // Assert unique controls are relocated into #empornium-header-slot
    await expect(slot.locator("#sidecar-status")).toBeAttached();
    await expect(slot.locator("#btn-sidecar-stop")).toBeAttached();
    await expect(slot.locator("#btn-history")).toBeAttached();

    // Assert duplicates and modal close remain in #review-header
    await expect(reviewHeader.locator("#header-logo")).toBeAttached();
    await expect(reviewHeader.locator("#header-modal-title")).toBeAttached();
    await expect(reviewHeader.locator("#btn-header-close")).toBeAttached();

    // Assert duplicates are NOT in the slot
    await expect(slot.locator("#header-logo")).toHaveCount(0);
    await expect(slot.locator("#header-modal-title")).toHaveCount(0);
    await expect(slot.locator("#btn-header-close")).toHaveCount(0);
  });

  test("with no slot present (standalone): #review-header is untouched and still holds them", async ({ page }) => {
    await mountStandalone(page);

    const reviewHeader = page.locator("#review-header");
    await expect(reviewHeader).toBeVisible();

    // Assert all controls remain inside #review-header
    await expect(reviewHeader.locator("#sidecar-status")).toBeAttached();
    await expect(reviewHeader.locator("#btn-sidecar-stop")).toBeAttached();
    await expect(reviewHeader.locator("#btn-history")).toBeAttached();
    await expect(reviewHeader.locator("#header-logo")).toBeAttached();
    await expect(reviewHeader.locator("#header-modal-title")).toBeAttached();
    await expect(reviewHeader.locator("#btn-header-close")).toBeAttached();

    // No slot exists
    await expect(page.locator("#empornium-header-slot")).toHaveCount(0);
  });

  test("fold is idempotent across two consecutive initEmporniumReview calls", async ({ page }) => {
    await mountModal(page);

    const slot = page.locator("#empornium-header-slot");
    const reviewHeader = page.locator("#review-header");

    // First call to initEmporniumReview
    await page.evaluate(() => {
      window.initEmporniumReview([1], "token-1", "single");
    });

    await expect(reviewHeader).toBeHidden();
    await expect(slot.locator("#sidecar-status")).toHaveCount(1);
    await expect(slot.locator("#btn-sidecar-stop")).toHaveCount(1);
    await expect(slot.locator("#btn-history")).toHaveCount(1);

    // Second consecutive call to initEmporniumReview
    await page.evaluate(() => {
      window.initEmporniumReview([1], "token-1", "single");
    });

    await expect(reviewHeader).toBeHidden();
    await expect(slot.locator("#sidecar-status")).toHaveCount(1);
    await expect(slot.locator("#btn-sidecar-stop")).toHaveCount(1);
    await expect(slot.locator("#btn-history")).toHaveCount(1);
  });
});
