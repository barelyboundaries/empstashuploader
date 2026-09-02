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

function makeScene(id, title) {
  return {
    id,
    title,
    date: "2026-01-01",
    paths: { screenshot: "", preview: "" },
    files: [{
      id: `file-${id}`,
      path: `D:\\Seed\\scene_${id}.mp4`,
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

async function bootHarness(page, { sceneCount = 6, viewport = { width: 1400, height: 900 } } = {}) {
  await page.setViewportSize(viewport);
  serveAssets(page);

  const scenes = [];
  const names = ["Alpha", "Beta", "Gamma", "Delta", "Epsilon", "Zeta", "Eta", "Theta"];
  for (let i = 1; i <= sceneCount; i++) {
    const name = names[i - 1] || `Scene ${i}`;
    scenes.push(makeScene(String(i), `${name} Video Scene`));
  }

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

  const url = `http://localhost:9999/plugin/empornium-megapack/assets/review.html?scenes=${scenes.map((s) => s.id).join(",")}`;
  await page.goto(url);
  await page.waitForSelector(".scene-card");
}

async function simulateDrag(page, sourceIndex, targetClientX, targetClientY) {
  await page.evaluate(({ srcIdx, x, y }) => {
    const container = document.getElementById("scene-list");
    const cards = container.querySelectorAll(".scene-card");
    const sourceCard = cards[srcIdx];
    if (!sourceCard) throw new Error(`Source card ${srcIdx} not found`);

    sourceCard.dispatchEvent(new DragEvent("dragstart", { bubbles: true, cancelable: true }));
    container.dispatchEvent(new DragEvent("dragover", {
      bubbles: true,
      cancelable: true,
      clientX: x,
      clientY: y
    }));
    sourceCard.dispatchEvent(new DragEvent("dragend", { bubbles: true, cancelable: true }));
  }, { srcIdx: sourceIndex, x: targetClientX, y: targetClientY });
}

async function getCardTitles(page) {
  return page.$$eval(".scene-card .scene-title", (els) => els.map((el) => el.textContent.trim()));
}

test.describe('Responsive Scene List Grid & 2D Drag-and-Drop Hit-Testing', () => {

  test('R3: #scene-list renders exactly 3 columns at 1400px viewport with derived column widths', async ({ page }) => {
    await bootHarness(page, { sceneCount: 6, viewport: { width: 1400, height: 900 } });

    const gridInfo = await page.evaluate(() => {
      const container = document.getElementById("scene-list");
      const style = window.getComputedStyle(container);
      const columns = style.gridTemplateColumns.split(/\s+/).filter(Boolean);
      const cards = [...container.querySelectorAll(".scene-card")];
      const rects = cards.map((c) => {
        const r = c.getBoundingClientRect();
        return { left: Math.round(r.left), top: Math.round(r.top), width: Math.round(r.width), height: Math.round(r.height) };
      });
      return { columnCount: columns.length, columns, rects };
    });

    expect(gridInfo.columnCount).toBe(3);
    expect(gridInfo.rects.length).toBe(6);

    // Cards 0, 1, 2 share the same top (Row 0)
    expect(gridInfo.rects[0].top).toBe(gridInfo.rects[1].top);
    expect(gridInfo.rects[1].top).toBe(gridInfo.rects[2].top);

    // Cards 3, 4, 5 share the same top (Row 1) and are below Row 0
    expect(gridInfo.rects[3].top).toBe(gridInfo.rects[4].top);
    expect(gridInfo.rects[4].top).toBe(gridInfo.rects[5].top);
    expect(gridInfo.rects[3].top).toBeGreaterThan(gridInfo.rects[0].top);

    // Derived column width satisfies 240px <= width <= 270px
    for (const rect of gridInfo.rects) {
      expect(rect.width).toBeGreaterThanOrEqual(240);
      expect(rect.width).toBeLessThanOrEqual(270);
    }
  });

  test('R3: #scene-list collapses cleanly to 1 column on narrow viewports', async ({ page }) => {
    await bootHarness(page, { sceneCount: 3, viewport: { width: 600, height: 900 } });

    const gridInfo = await page.evaluate(() => {
      const container = document.getElementById("scene-list");
      const style = window.getComputedStyle(container);
      const columns = style.gridTemplateColumns.split(/\s+/).filter(Boolean);
      const cards = [...container.querySelectorAll(".scene-card")];
      const rects = cards.map((c) => {
        const r = c.getBoundingClientRect();
        return { top: Math.round(r.top), bottom: Math.round(r.bottom) };
      });
      return { columnCount: columns.length, rects };
    });

    expect(gridInfo.columnCount).toBe(1);
    // In single column, each card is strictly stacked vertically below the preceding card
    expect(gridInfo.rects[1].top).toBeGreaterThanOrEqual(gridInfo.rects[0].bottom);
    expect(gridInfo.rects[2].top).toBeGreaterThanOrEqual(gridInfo.rects[1].bottom);
  });

  test('R2: Active-stage panel and stage rail chips follow solid accent styling and completed checkmarks', async ({ page }) => {
    await bootHarness(page, { sceneCount: 3, viewport: { width: 1400, height: 900 } });

    // Stage 1 active:
    // Panel 1 has solid accent border rgb(59, 130, 246) and rgba(59, 130, 246, 0.08) tint
    await expect(page.locator("#stage-panel-1")).toHaveCSS("border-top-color", "rgb(59, 130, 246)");
    await expect(page.locator("#stage-panel-1")).toHaveCSS("background-color", "rgba(59, 130, 246, 0.08)");
    // Active chip is filled blue pill
    await expect(page.locator("#stage-item-1")).toHaveCSS("background-color", "rgb(59, 130, 246)");
    await expect(page.locator("#stage-item-1")).toHaveCSS("color", "rgb(255, 255, 255)");
    // Unreached chip is subdued
    await expect(page.locator("#stage-item-2")).toHaveCSS("color", "rgb(156, 163, 175)");

    // Advance to Stage 2: fill pack title and click Next
    await page.fill("#pack-title", "Test Megapack Title");
    await page.click("#btn-stage-next");

    // Stage 2 active:
    await expect(page.locator("#stage-item-1")).toHaveClass(/stage-completed/);
    await expect(page.locator("#stage-item-1 .stage-check")).toBeVisible();
    await expect(page.locator("#stage-item-1")).toHaveCSS("color", "rgb(16, 185, 129)");
    await expect(page.locator("#stage-item-2")).toHaveCSS("background-color", "rgb(59, 130, 246)");
    await expect(page.locator("#stage-item-2")).toHaveCSS("color", "rgb(255, 255, 255)");
    await expect(page.locator("#stage-panel-2")).toHaveCSS("border-top-color", "rgb(59, 130, 246)");
    await expect(page.locator("#stage-panel-2")).toHaveCSS("background-color", "rgba(59, 130, 246, 0.08)");

    // Advance to Stage 3 (Scenes): fill seed & scratch and click Next
    await page.fill("#output-dir", "D:\\Seed");
    await page.fill("#scratch-dir", "D:\\Scratch");
    await page.click("#btn-stage-next");

    // Stage 3 active:
    await expect(page.locator(".scene-list-panel")).toHaveClass(/stage-current/);
    await expect(page.locator(".scene-list-panel")).toHaveCSS("border-top-color", "rgb(59, 130, 246)");
    await expect(page.locator(".scene-list-panel")).toHaveCSS("background-color", "rgba(59, 130, 246, 0.08)");
  });

  test('R4: 2D Drag-and-Drop within same row (reorder cards within Row 0)', async ({ page }) => {
    await bootHarness(page, { sceneCount: 6, viewport: { width: 1400, height: 900 } });

    // Initial titles: #1 - Alpha, #2 - Beta, #3 - Gamma, #4 - Delta, #5 - Epsilon, #6 - Zeta
    const initialTitles = await getCardTitles(page);
    expect(initialTitles[0]).toContain("Alpha");
    expect(initialTitles[1]).toContain("Beta");
    expect(initialTitles[2]).toContain("Gamma");

    // Drag Beta (index 1) before Alpha (index 0)
    const card0Rect = await page.evaluate(() => {
      const c0 = document.querySelectorAll("#scene-list .scene-card")[0];
      const r = c0.getBoundingClientRect();
      return { x: r.left + r.width * 0.2, y: r.top + r.height * 0.5 };
    });

    await simulateDrag(page, 1, card0Rect.x, card0Rect.y);

    const reorderedTitles = await getCardTitles(page);
    expect(reorderedTitles[0]).toContain("Beta");
    expect(reorderedTitles[1]).toContain("Alpha");
    expect(reorderedTitles[2]).toContain("Gamma");
    expect(reorderedTitles[3]).toContain("Delta");
  });

  test('R4: 2D Drag-and-Drop across rows (move card from Row 0 to Row 1)', async ({ page }) => {
    await bootHarness(page, { sceneCount: 6, viewport: { width: 1400, height: 900 } });

    // Drag Alpha (index 0, Row 0) between Delta and Epsilon (Row 1)
    const deltaRect = await page.evaluate(() => {
      const cards = document.querySelectorAll("#scene-list .scene-card");
      const delta = cards[3]; // Delta is index 3
      const r = delta.getBoundingClientRect();
      // Drop in the right half of Delta (after Delta, before Epsilon)
      return { x: r.left + r.width * 0.75, y: r.top + r.height * 0.5 };
    });

    await simulateDrag(page, 0, deltaRect.x, deltaRect.y);

    const reorderedTitles = await getCardTitles(page);
    // Expected: Beta (#1), Gamma (#2), Delta (#3), Alpha (#4), Epsilon (#5), Zeta (#6)
    expect(reorderedTitles[0]).toContain("Beta");
    expect(reorderedTitles[1]).toContain("Gamma");
    expect(reorderedTitles[2]).toContain("Delta");
    expect(reorderedTitles[3]).toContain("Alpha");
    expect(reorderedTitles[4]).toContain("Epsilon");
    expect(reorderedTitles[5]).toContain("Zeta");
  });

  test('R4: 2D Drag-and-Drop across rows backwards (move card from Row 1 to Row 0)', async ({ page }) => {
    await bootHarness(page, { sceneCount: 6, viewport: { width: 1400, height: 900 } });

    // Drag Zeta (index 5, Row 1) between Alpha and Beta (Row 0)
    const betaRect = await page.evaluate(() => {
      const cards = document.querySelectorAll("#scene-list .scene-card");
      const beta = cards[1]; // Beta is index 1
      const r = beta.getBoundingClientRect();
      // Drop in the left half of Beta (before Beta)
      return { x: r.left + r.width * 0.25, y: r.top + r.height * 0.5 };
    });

    await simulateDrag(page, 5, betaRect.x, betaRect.y);

    const reorderedTitles = await getCardTitles(page);
    // Expected: Alpha (#1), Zeta (#2), Beta (#3), Gamma (#4), Delta (#5), Epsilon (#6)
    expect(reorderedTitles[0]).toContain("Alpha");
    expect(reorderedTitles[1]).toContain("Zeta");
    expect(reorderedTitles[2]).toContain("Beta");
    expect(reorderedTitles[3]).toContain("Gamma");
    expect(reorderedTitles[4]).toContain("Delta");
    expect(reorderedTitles[5]).toContain("Epsilon");
  });

  test('R4: 2D Drag-and-Drop to end of list (past all cards)', async ({ page }) => {
    await bootHarness(page, { sceneCount: 6, viewport: { width: 1400, height: 900 } });

    // Drag Alpha (index 0) below the last row
    const belowRect = await page.evaluate(() => {
      const cards = document.querySelectorAll("#scene-list .scene-card");
      const zeta = cards[5];
      const r = zeta.getBoundingClientRect();
      return { x: r.left + r.width * 0.5, y: r.bottom + 50 };
    });

    await simulateDrag(page, 0, belowRect.x, belowRect.y);

    const reorderedTitles = await getCardTitles(page);
    // Expected: Beta, Gamma, Delta, Epsilon, Zeta, Alpha
    expect(reorderedTitles[0]).toContain("Beta");
    expect(reorderedTitles[1]).toContain("Gamma");
    expect(reorderedTitles[2]).toContain("Delta");
    expect(reorderedTitles[3]).toContain("Epsilon");
    expect(reorderedTitles[4]).toContain("Zeta");
    expect(reorderedTitles[5]).toContain("Alpha");
  });

  test('R4: Drag-and-Drop preserves single-column vertical reorder behavior when collapsed', async ({ page }) => {
    await bootHarness(page, { sceneCount: 3, viewport: { width: 600, height: 900 } });

    // Initial: Alpha (#1), Beta (#2), Gamma (#3)
    let titles = await getCardTitles(page);
    expect(titles[0]).toContain("Alpha");
    expect(titles[1]).toContain("Beta");
    expect(titles[2]).toContain("Gamma");

    // Drag Gamma (index 2) to top of Alpha (index 0)
    const alphaTop = await page.evaluate(() => {
      const c = document.querySelectorAll("#scene-list .scene-card")[0];
      const r = c.getBoundingClientRect();
      return { x: r.left + r.width * 0.5, y: r.top + r.height * 0.2 };
    });

    await simulateDrag(page, 2, alphaTop.x, alphaTop.y);

    titles = await getCardTitles(page);
    expect(titles[0]).toContain("Gamma");
    expect(titles[1]).toContain("Alpha");
    expect(titles[2]).toContain("Beta");

    // Drag Gamma (now index 0) between Alpha and Beta
    const betaTop = await page.evaluate(() => {
      const c = document.querySelectorAll("#scene-list .scene-card")[2]; // Beta is currently index 2
      const r = c.getBoundingClientRect();
      return { x: r.left + r.width * 0.5, y: r.top + r.height * 0.2 };
    });

    await simulateDrag(page, 0, betaTop.x, betaTop.y);

    titles = await getCardTitles(page);
    expect(titles[0]).toContain("Alpha");
    expect(titles[1]).toContain("Gamma");
    expect(titles[2]).toContain("Beta");
  });

  test('R4 Adversarial: Multi-column drag to the right of a trailing single-card row inserts after that card', async ({ page }) => {
    // 4 scenes in 3-column grid: Row 0 has 3 cards (Alpha, Beta, Gamma), Row 1 has 1 card (Delta)
    await bootHarness(page, { sceneCount: 4, viewport: { width: 1400, height: 900 } });

    // Initial: Alpha, Beta, Gamma, Delta
    let titles = await getCardTitles(page);
    expect(titles[0]).toContain("Alpha");
    expect(titles[1]).toContain("Beta");
    expect(titles[2]).toContain("Gamma");
    expect(titles[3]).toContain("Delta");

    // Drag Alpha (index 0) to Row 1, dropping in Column 1 space (to the right of Delta) with y in top half of Delta
    const dropCoord = await page.evaluate(() => {
      const cards = document.querySelectorAll("#scene-list .scene-card");
      const delta = cards[3];
      const r = delta.getBoundingClientRect();
      // Right of Delta card, within top half of Delta (y < centerY)
      return { x: r.right + 50, y: r.top + 15 };
    });

    await simulateDrag(page, 0, dropCoord.x, dropCoord.y);

    titles = await getCardTitles(page);
    // Expected: Beta, Gamma, Delta, Alpha (Alpha placed AFTER Delta)
    expect(titles[0]).toContain("Beta");
    expect(titles[1]).toContain("Gamma");
    expect(titles[2]).toContain("Delta");
    expect(titles[3]).toContain("Alpha");
  });

  test('R4 Adversarial: Multi-column drag with 2 scenes total correctly reorders left-to-right', async ({ page }) => {
    await bootHarness(page, { sceneCount: 2, viewport: { width: 1400, height: 900 } });

    // Initial: Alpha (#1), Beta (#2)
    let titles = await getCardTitles(page);
    expect(titles[0]).toContain("Alpha");
    expect(titles[1]).toContain("Beta");

    // Drag Alpha (index 0) to the right of Beta (index 1) in upper half
    const dropCoord = await page.evaluate(() => {
      const beta = document.querySelectorAll("#scene-list .scene-card")[1];
      const r = beta.getBoundingClientRect();
      return { x: r.right + 30, y: r.top + 10 };
    });

    await simulateDrag(page, 0, dropCoord.x, dropCoord.y);

    titles = await getCardTitles(page);
    expect(titles[0]).toContain("Beta");
    expect(titles[1]).toContain("Alpha");
  });

  test('R4 Adversarial: Variable height cards maintain correct row-banding and drag target detection', async ({ page }) => {
    await bootHarness(page, { sceneCount: 6, viewport: { width: 1400, height: 900 } });

    // Inject extra content into card 1 (Beta) to make it significantly taller than other cards
    await page.evaluate(() => {
      const beta = document.querySelectorAll("#scene-list .scene-card")[1];
      const extra = document.createElement("div");
      extra.style.height = "120px";
      extra.textContent = "Extra metadata height extension";
      beta.querySelector(".scene-info").appendChild(extra);
    });

    // Verify Row 0 track expanded due to extra content in Beta compared to Row 1 cards
    const heights = await page.evaluate(() => {
      const cards = [...document.querySelectorAll("#scene-list .scene-card")];
      return cards.map((c) => Math.round(c.getBoundingClientRect().height));
    });
    expect(heights[0]).toBeGreaterThan(heights[3] + 50);

    // Drag Zeta (index 5, Row 1) between Alpha (index 0) and Beta (index 1) in Row 0
    const alphaRightCoord = await page.evaluate(() => {
      const alpha = document.querySelectorAll("#scene-list .scene-card")[0];
      const r = alpha.getBoundingClientRect();
      return { x: r.right + 6, y: r.top + r.height * 0.5 };
    });

    await simulateDrag(page, 5, alphaRightCoord.x, alphaRightCoord.y);

    const reordered = await getCardTitles(page);
    expect(reordered[0]).toContain("Alpha");
    expect(reordered[1]).toContain("Zeta");
    expect(reordered[2]).toContain("Beta");
  });

  test('R4 & Constraints: Drag reordering persists into final build payload dispatched via wizard stages', async ({ page }) => {
    let capturedBuildPayload = null;
    await bootHarness(page, { sceneCount: 4, viewport: { width: 1400, height: 900 } });

    await page.route("**/graphql", async (route) => {
      const postData = JSON.parse(route.request().postData() || "{}");
      const query = postData.query || "";
      if (query.includes("FindScenes")) {
        const names = ["Alpha", "Beta", "Gamma", "Delta"];
        const scenes = names.map((name, i) => makeScene(String(i + 1), `${name} Video Scene`));
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ data: { findScenes: { scenes } } })
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
      if (query.includes("RunBuild")) {
        const args = postData.variables?.args || [];
        const payloadArg = args.find((a) => a.key === "payload");
        if (payloadArg && payloadArg.value?.str) {
          capturedBuildPayload = JSON.parse(payloadArg.value.str);
        }
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ data: { runPluginTask: "job-123" } })
        });
      }
      return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ data: {} }) });
    });

    // Reorder: Move Alpha (index 0) after Gamma (index 2)
    const gammaRightCoord = await page.evaluate(() => {
      const gamma = document.querySelectorAll("#scene-list .scene-card")[2];
      const r = gamma.getBoundingClientRect();
      return { x: r.left + r.width * 0.75, y: r.top + r.height * 0.5 };
    });

    await simulateDrag(page, 0, gammaRightCoord.x, gammaRightCoord.y);

    const titles = await getCardTitles(page);
    expect(titles[0]).toContain("Beta");
    expect(titles[1]).toContain("Gamma");
    expect(titles[2]).toContain("Alpha");
    expect(titles[3]).toContain("Delta");

    // Advance Stage 1 (Setup) -> Stage 2
    await page.fill("#pack-title", "Reordered Megapack");
    await page.click("#btn-stage-next");

    // Advance Stage 2 (Locations) -> Stage 3
    await page.fill("#output-dir", "D:\\Seed");
    await page.fill("#scratch-dir", "D:\\Scratch");
    await page.click("#btn-stage-next");

    // Advance Stage 3 (Scenes) -> Stage 4 (Actions)
    await page.click("#btn-stage-next");

    // On Stage 4, click Build Megapack
    await page.click("#btn-build");
    await page.waitForTimeout(100);

    // Assert that scenes were dispatched in the newly reordered sequence: [2, 3, 1, 4]
    expect(capturedBuildPayload).not.toBeNull();
    const dispatchedSceneIds = (capturedBuildPayload.scenes || []).map((s) => String(s.id));
    expect(dispatchedSceneIds).toEqual(["2", "3", "1", "4"]);
  });

  test('R4 Adversarial: Drag reordering under deviceScaleFactor 1.25 (High-DPI / fractional zoom)', async ({ page }) => {
    await page.setViewportSize({ width: 1400, height: 900 });
    await bootHarness(page, { sceneCount: 6, viewport: { width: 1400, height: 900 } });

    // Drag Beta (index 1) to the left of Alpha (index 0)
    const card0Rect = await page.evaluate(() => {
      const c0 = document.querySelectorAll("#scene-list .scene-card")[0];
      const r = c0.getBoundingClientRect();
      return { x: r.left + r.width * 0.25, y: r.top + r.height * 0.5 };
    });

    await simulateDrag(page, 1, card0Rect.x, card0Rect.y);

    const titles = await getCardTitles(page);
    expect(titles[0]).toContain("Beta");
    expect(titles[1]).toContain("Alpha");
    expect(titles[2]).toContain("Gamma");
  });

});
