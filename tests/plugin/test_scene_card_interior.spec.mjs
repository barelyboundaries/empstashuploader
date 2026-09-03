import { test, expect } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

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

function makeScene(id, title, opts = {}) {
  return {
    id,
    title,
    date: "2026-01-01",
    paths: { screenshot: "", preview: "" },
    files: opts.files || [{
      id: `file-${id}`,
      path: opts.path || `D:\\Seed\\scene_${id}.mp4`,
      size: 5000000,
      height: 1080,
      width: 1920,
      duration: 600,
      video_codec: "h264",
      oshash: `oshash-${id}`
    }],
    performers: [{ name: `Performer ${id}` }],
    tags: [],
    studio: null
  };
}

async function bootHarness(page, { scenes = null, sceneCount = 6, viewport = { width: 1400, height: 900 } } = {}) {
  await page.setViewportSize(viewport);
  serveAssets(page);

  const sceneList = scenes || [];
  if (!scenes) {
    const names = ["Alpha", "Beta", "Gamma", "Delta", "Epsilon", "Zeta", "Eta", "Theta"];
    for (let i = 1; i <= sceneCount; i++) {
      const name = names[i - 1] || `Scene ${i}`;
      sceneList.push(makeScene(String(i), `${name} Video Scene`));
    }
  }

  await page.route("**/graphql", async (route) => {
    const postData = JSON.parse(route.request().postData() || "{}");
    const query = postData.query || "";
    if (query.includes("FindScenes")) {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ data: { findScenes: { scenes: sceneList } } })
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

  await page.route("**/api/fs/exists", async (route) => {
    const postData = JSON.parse(route.request().postData() || "{}");
    const paths = postData.paths || [];
    const results = {};
    for (const p of paths) results[p] = true;
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ results })
    });
  });

  await page.route("**/health", async (route) => {
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ status: "connected", version: "0.2.0", scratch_dir: "D:\\Scratch" })
    });
  });

  await page.route("**/api/run/**", async (route) => {
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ status: "FINISHED" })
    });
  });

  const url = `http://localhost:9999/plugin/empornium-megapack/assets/review.html?scenes=${sceneList.map((s) => s.id).join(",")}`;
  await page.goto(url);
  await page.waitForSelector(".scene-card");
}

test.describe('Scene Card Interior Responsiveness (Brief B2)', () => {

  test('1. Long titles (60+ chars and unbroken strings) do not overflow column bounds', async ({ page }) => {
    const scenes = [
      makeScene("1", "A Very Long Scene Title That Contains More Than Sixty Characters In It Easily For Grid Column Testing"),
      makeScene("2", "SupercalifragilisticexpialidociousUnbrokenLongStringToTestWrapGuardWithoutAnySpacesAtAll"),
      makeScene("3", "Standard Alpha Scene Title")
    ];

    await bootHarness(page, { scenes, viewport: { width: 1400, height: 900 } });

    const overflowResults = await page.evaluate(() => {
      const cards = [...document.querySelectorAll(".scene-card")];
      return cards.map((c) => ({
        scrollWidth: c.scrollWidth,
        clientWidth: c.clientWidth,
        hasOverflow: c.scrollWidth > c.clientWidth
      }));
    });

    expect(overflowResults).toHaveLength(3);
    for (const result of overflowResults) {
      expect(result.hasOverflow).toBe(false);
      expect(result.scrollWidth).toBeLessThanOrEqual(result.clientWidth);
    }
  });

  test('2. .scene-title rendered height stays bounded under derived line-height threshold', async ({ page }) => {
    await bootHarness(page, { sceneCount: 6, viewport: { width: 1400, height: 900 } });

    const titleMetrics = await page.evaluate(() => {
      const titles = [...document.querySelectorAll(".scene-card .scene-title")];
      return titles.map((t) => {
        const style = window.getComputedStyle(t);
        const fontSize = parseFloat(style.fontSize) || 15.2;
        let lineHeight = parseFloat(style.lineHeight);
        if (isNaN(lineHeight)) lineHeight = fontSize * 1.3;
        const rect = t.getBoundingClientRect();
        return {
          height: rect.height,
          lineHeight,
          estimatedLines: rect.height / lineHeight
        };
      });
    });

    for (const m of titleMetrics) {
      // Typical titles must fit within 1-2 lines; regression to 4 lines (>= 4 * lineHeight) fails
      expect(m.height).toBeLessThan(m.lineHeight * 3);
      expect(m.estimatedLines).toBeLessThan(2.5);
    }
  });

  test('3. Worst-case duplicate card with ✓ Keep This, ✕ Remove, and multi-file picker fits within card bounds', async ({ page }) => {
    const scenes = [
      makeScene("1", "Duplicate Scene Alpha Multi Version", {
        path: "D:\\Seed\\dup_target.mp4",
        files: [
          { id: "f1", path: "D:\\Seed\\dup_target.mp4", size: 5000000, height: 1080, width: 1920, duration: 600, video_codec: "h264" },
          { id: "f2", path: "D:\\Seed\\dup_target_720p.mp4", size: 3000000, height: 720, width: 1280, duration: 600, video_codec: "h264" }
        ]
      }),
      makeScene("2", "Duplicate Scene Beta Single File Collision", {
        path: "D:\\Other\\dup_target.mp4",
        files: [
          { id: "f3", path: "D:\\Other\\dup_target.mp4", size: 5000000, height: 1080, width: 1920, duration: 600, video_codec: "h264" }
        ]
      }),
      makeScene("3", "Standard Non Conflicting Scene")
    ];

    await bootHarness(page, { scenes, viewport: { width: 1400, height: 900 } });

    // Verify card 1 has both Keep and Remove buttons and file select
    const card1 = page.locator(".scene-card").first();
    const keepBtn = card1.locator(".scene-keep-btn");
    const removeBtn = card1.locator(".scene-remove-btn");
    const fileSelect = card1.locator(".scene-file-select");

    await expect(keepBtn).toBeVisible();
    await expect(removeBtn).toBeVisible();
    await expect(fileSelect).toBeVisible();

    const fits = await page.evaluate(() => {
      const card = document.querySelector(".scene-card");
      const keep = card.querySelector(".scene-keep-btn");
      const remove = card.querySelector(".scene-remove-btn");
      const cr = card.getBoundingClientRect();
      const kr = keep.getBoundingClientRect();
      const rr = remove.getBoundingClientRect();

      return {
        cardOverflow: card.scrollWidth > card.clientWidth,
        keepWidth: kr.width,
        keepHeight: kr.height,
        keepInsideCard: kr.left >= cr.left - 1 && kr.right <= cr.right + 1 && kr.top >= cr.top - 1 && kr.bottom <= cr.bottom + 1,
        removeWidth: rr.width,
        removeHeight: rr.height,
        removeInsideCard: rr.left >= cr.left - 1 && rr.right <= cr.right + 1 && rr.top >= cr.top - 1 && rr.bottom <= cr.bottom + 1
      };
    });

    expect(fits.cardOverflow).toBe(false);
    expect(fits.keepWidth).toBeGreaterThan(0);
    expect(fits.keepHeight).toBeGreaterThan(0);
    expect(fits.keepInsideCard).toBe(true);

    expect(fits.removeWidth).toBeGreaterThan(0);
    expect(fits.removeHeight).toBeGreaterThan(0);
    expect(fits.removeInsideCard).toBe(true);
  });

  test('4. Single-column layout at narrow viewport keeps thumbnail beside text; 3-column wraps vertically', async ({ page }) => {
    // 1-column layout at narrow viewport (700px)
    await bootHarness(page, { sceneCount: 3, viewport: { width: 700, height: 900 } });

    const narrowLayout = await page.evaluate(() => {
      const card = document.querySelector(".scene-card");
      const thumb = card.querySelector(".scene-thumb");
      const info = card.querySelector(".scene-info");
      const tr = thumb.getBoundingClientRect();
      const ir = info.getBoundingClientRect();
      return {
        shareTopEdge: Math.abs(tr.top - ir.top) <= 2,
        thumbLeft: tr.left,
        infoLeft: ir.left
      };
    });

    // In single-column layout, thumb and info sit side-by-side sharing top edge
    expect(narrowLayout.shareTopEdge).toBe(true);
    expect(narrowLayout.infoLeft).toBeGreaterThan(narrowLayout.thumbLeft);

    // 3-column layout at 1400px viewport
    await page.setViewportSize({ width: 1400, height: 900 });
    await page.waitForTimeout(50);

    const wideLayout = await page.evaluate(() => {
      const card = document.querySelector(".scene-card");
      const thumb = card.querySelector(".scene-thumb");
      const info = card.querySelector(".scene-info");
      const tr = thumb.getBoundingClientRect();
      const ir = info.getBoundingClientRect();
      return {
        shareTopEdge: Math.abs(tr.top - ir.top) <= 2,
        infoBelowThumb: ir.top >= tr.bottom - 1
      };
    });

    // In 3-column layout, thumb and info do NOT share top edge; info wraps below thumb
    expect(wideLayout.shareTopEdge).toBe(false);
    expect(wideLayout.infoBelowThumb).toBe(true);
  });

  test('5. Total rendered height of scene list for fixed scene count is lower than four-line baseline', async ({ page }) => {
    // 6 scenes at 1400px (2 rows in 3-column grid)
    await bootHarness(page, { sceneCount: 6, viewport: { width: 1400, height: 900 } });

    const heightInfo = await page.evaluate(() => {
      const list = document.getElementById("scene-list");
      const cards = [...document.querySelectorAll(".scene-card")];
      return {
        listHeight: list.getBoundingClientRect().height,
        cardHeights: cards.map((c) => Math.round(c.getBoundingClientRect().height))
      };
    });

    // Baseline with 4-line title wrapping was ~542px (cards ~265px tall)
    // After interior fix, list height is ~491px (cards ~239px tall)
    expect(heightInfo.listHeight).toBeLessThan(520);
    for (const cardHeight of heightInfo.cardHeights) {
      expect(cardHeight).toBeLessThan(255);
    }
  });

});
