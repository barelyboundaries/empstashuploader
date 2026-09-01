import { test, expect } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

/**
 * main.js mounts the review UI by injecting review.html's <body> markup into
 * .empornium-modal-body and its <style> block into the host document's <head>.
 * There is no iframe.
 *
 * That means review.html's own `body { display:flex; height:100vh }` rule never
 * applies to the injected markup, so any layout that depends on it must be
 * reproduced by the modal CSS. Loading review.html standalone hides this class
 * of bug entirely -- these tests deliberately reproduce the real mount.
 */

const HOST_PAGE = `<!doctype html><html><head><meta charset="utf-8"></head>
<body style="margin:0"><div style="height:3000px">stash page behind the modal</div></body></html>`;

function readAsset(rel) {
  return fs.readFileSync(path.resolve(rel), "utf8");
}

async function mountLikeMainJs(page, { viewport = { width: 1419, height: 856 } } = {}) {
  await page.setViewportSize(viewport);
  await page.route("**/host-page", (route) =>
    route.fulfill({ status: 200, contentType: "text/html", body: HOST_PAGE })
  );
  await page.goto("http://localhost:9999/host-page");

  const modalCss = readAsset("plugin/style.css");
  const reviewHtml = readAsset("plugin/assets/review.html");

  await page.evaluate(
    ({ modalCss, reviewHtml }) => {
      const style = document.createElement("style");
      style.textContent = modalCss;
      document.head.appendChild(style);

      // Same DOM shape main.js builds.
      const overlay = document.createElement("div");
      overlay.className = "empornium-modal-overlay";
      overlay.id = "empornium-megapack-modal";
      const container = document.createElement("div");
      container.className = "empornium-modal-container";
      const header = document.createElement("div");
      header.className = "empornium-modal-header";
      header.innerHTML = '<div class="empornium-modal-title">Empornium Megapack Builder</div>';
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
}

/** Fill the left panel with enough cards to guarantee vertical overflow. */
async function stuffSceneList(page, count = 70) {
  await page.evaluate((n) => {
    const list = document.getElementById("scene-list");
    list.innerHTML = "";
    for (let i = 0; i < n; i++) {
      const card = document.createElement("div");
      card.className = "scene-card";
      card.style.height = "90px";
      card.textContent = `Scene ${i + 1}`;
      list.appendChild(card);
    }
  }, count);
}

test.describe("Review UI mounted the way main.js mounts it", () => {
  test("the modal never grows past the viewport", async ({ page }) => {
    await mountLikeMainJs(page);
    await stuffSceneList(page);

    const fits = await page.evaluate(() => {
      const rect = document.querySelector(".empornium-modal-container").getBoundingClientRect();
      return { bottom: rect.bottom, viewport: window.innerHeight };
    });

    expect(fits.bottom).toBeLessThanOrEqual(fits.viewport + 1);
  });

  test("the scene list scrolls instead of spilling past the modal edge", async ({ page }) => {
    await mountLikeMainJs(page);
    await stuffSceneList(page);

    const panel = await page.evaluate(() => {
      const el = document.querySelector(".scene-list-panel");
      const before = el.scrollTop;
      el.scrollTop = el.scrollHeight;
      return { overflows: el.scrollHeight > el.clientHeight, scrolled: el.scrollTop > before };
    });

    expect(panel.overflows).toBe(true);
    expect(panel.scrolled).toBe(true);
  });

  test("the options panel scrolls to reach content below the fold", async ({ page }) => {
    await mountLikeMainJs(page);
    await page.evaluate(() => {
      // Approximate a completed build: a tall summary plus the BBCode preview.
      const box = document.getElementById("artifact-summary");
      box.style.display = "flex";
      box.innerHTML = '<div style="height:600px">summary</div>';
    });

    const panel = await page.evaluate(() => {
      const el = document.querySelector(".options-panel");
      const before = el.scrollTop;
      el.scrollTop = el.scrollHeight;
      return { overflows: el.scrollHeight > el.clientHeight, scrolled: el.scrollTop > before };
    });

    expect(panel.overflows).toBe(true);
    expect(panel.scrolled).toBe(true);
  });

  test("a wheel gesture over the panel actually scrolls it", async ({ page }) => {
    await mountLikeMainJs(page);
    await stuffSceneList(page);

    const box = await page.locator(".scene-list-panel").boundingBox();
    await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
    const before = await page.evaluate(() => document.querySelector(".scene-list-panel").scrollTop);
    await page.mouse.wheel(0, 800);
    await page.waitForTimeout(300);
    const after = await page.evaluate(() => document.querySelector(".scene-list-panel").scrollTop);

    expect(after).toBeGreaterThan(before);
  });

  test("the layout is bounded by the modal, not by its own content", async ({ page }) => {
    await mountLikeMainJs(page);
    await stuffSceneList(page, 200);

    const m = await page.evaluate(() => {
      const layout = document.querySelector(".main-layout");
      const modalBody = document.querySelector(".empornium-modal-body");
      return {
        layoutHeight: layout.getBoundingClientRect().height,
        modalBodyHeight: modalBody.getBoundingClientRect().height
      };
    });

    // 200 cards would be ~18000px tall if the layout sized itself to content.
    expect(m.layoutHeight).toBeLessThanOrEqual(m.modalBodyHeight + 1);
  });
});
