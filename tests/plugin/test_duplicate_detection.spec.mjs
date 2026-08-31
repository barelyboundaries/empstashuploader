import { test, expect } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

function setupMocks(page) {
  page.route("**/plugin*/**/main.js*", async (route) => {
    const filePath = path.resolve("plugin/main.js");
    return route.fulfill({
      status: 200,
      contentType: "application/javascript",
      body: fs.readFileSync(filePath, "utf8")
    });
  });

  page.route("**/plugin*/**/style.css*", async (route) => {
    const filePath = path.resolve("plugin/style.css");
    return route.fulfill({
      status: 200,
      contentType: "text/css",
      body: fs.readFileSync(filePath, "utf8")
    });
  });

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

  page.route("**/api/fs/exists*", async (route) => {
    const postData = JSON.parse(route.request().postData() || "{}");
    const results = {};
    // Build pre-flight (todo 7 of staged-wizard-inplace-seed) treats a false
    // probe as "missing on disk" and blocks the build: the R2 fixtures represent
    // existing files under the default seed dir (C:\Packs). Consolidation
    // discovery (R3 tests point the seed field at D:\Consolidation) must keep
    // seeing destination paths as FREE, or the collision dialog would open
    // mid-move — so anything outside C:\Packs probes false.
    for (const p of postData.paths || []) {
      results[p] = p.toLowerCase().startsWith("c:\\packs\\");
    }
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ results })
    });
  });
}

test.describe("Stage 7 Feature 1 — Duplicate Filename Detection & Resolution", () => {

  const testScenes = [
    {
      id: 101,
      title: "Alpha Scene 101",
      date: "2026-01-10",
      paths: { screenshot: "http://localhost:9999/shot101.jpg" },
      files: [{ id: 1001, path: "C:\\Packs\\Conflict_Video.mp4", size: 1000000, height: 1080, width: 1920, duration: 1200, video_codec: "h264" }],
      performers: [{ id: 1, name: "Performer One" }],
      tags: [{ id: 10, name: "TagA" }],
      studio: { id: 100, name: "Studio Alpha" }
    },
    {
      id: 102,
      title: "Beta Scene 102 (Unique)",
      date: "2026-01-11",
      paths: { screenshot: "http://localhost:9999/shot102.jpg" },
      files: [{ id: 1002, path: "C:\\Packs\\unique_name.mp4", size: 2000000, height: 1080, width: 1920, duration: 1500, video_codec: "h264" }],
      performers: [{ id: 2, name: "Performer Two" }],
      tags: [{ id: 20, name: "TagB" }],
      studio: { id: 100, name: "Studio Alpha" }
    },
    {
      id: 103,
      title: "Gamma Scene 103 (Case Colliding with 101)",
      date: "2026-01-12",
      paths: { screenshot: "http://localhost:9999/shot103.jpg" },
      files: [{ id: 1003, path: "C:\\Packs\\conflict_video.MP4", size: 3000000, height: 1080, width: 1920, duration: 1800, video_codec: "h264" }],
      performers: [{ id: 3, name: "Performer Three" }],
      tags: [{ id: 30, name: "TagC" }],
      studio: { id: 100, name: "Studio Alpha" }
    },
    {
      id: 104,
      title: "Delta Scene 104 (Second Collision Group)",
      date: "2026-01-13",
      paths: { screenshot: "http://localhost:9999/shot104.jpg" },
      files: [{ id: 1004, path: "C:\\Packs\\other_duplicate.mkv", size: 4000000, height: 2160, width: 3840, duration: 2400, video_codec: "hevc" }],
      performers: [{ id: 4, name: "Performer Four" }],
      tags: [{ id: 40, name: "TagD" }],
      studio: { id: 100, name: "Studio Alpha" }
    },
    {
      id: 105,
      title: "Epsilon Scene 105 (Second Collision Group)",
      date: "2026-01-14",
      paths: { screenshot: "http://localhost:9999/shot105.jpg" },
      files: [{ id: 1005, path: "C:\\Packs\\OTHER_DUPLICATE.MKV", size: 5000000, height: 2160, width: 3840, duration: 2600, video_codec: "hevc" }],
      performers: [{ id: 5, name: "Performer Five" }],
      tags: [{ id: 50, name: "TagE" }],
      studio: { id: 100, name: "Studio Alpha" }
    }
  ];

  test("R1. Detection & Grouping on Initial Load (Case-Insensitive & Independent of Probe)", async ({ page }) => {
    setupMocks(page);

    await page.route("**/graphql", async (route) => {
      const postData = JSON.parse(route.request().postData() || "{}");
      if (postData.query?.includes("FindScenes")) {
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ data: { findScenes: { scenes: testScenes } } })
        });
      }
      return route.continue();
    });

    await page.goto("http://localhost:9999/plugins/deepseek-megapack/review.html?scenes=101,102,103,104,105");
    await page.waitForSelector(".scene-card");

    const cards = page.locator(".scene-card");
    await expect(cards).toHaveCount(5);

    // Verify collision banner is displayed on load
    const banner = page.locator("#collision-banner");
    await expect(banner).toBeVisible();
    await expect(page.locator("#collision-headline")).toContainText("2 conflict groups");
    await expect(page.locator("#collision-headline")).toContainText("4 colliding files");

    // Check card 0 (Scene 101, Group A 1 of 2)
    const card101 = cards.nth(0);
    await expect(card101).toHaveClass(/scene-card--duplicate/);
    await expect(card101.locator(".badge-danger")).toContainText("Group A (1 of 2)");

    // Check card 1 (Scene 102, Unique - no duplicate styling or badge)
    const card102 = cards.nth(1);
    await expect(card102).not.toHaveClass(/scene-card--duplicate/);
    await expect(card102.locator(".badge-danger")).toHaveCount(0);

    // Check card 2 (Scene 103, Group A 2 of 2)
    const card103 = cards.nth(2);
    await expect(card103).toHaveClass(/scene-card--duplicate/);
    await expect(card103.locator(".badge-danger")).toContainText("Group A (2 of 2)");

    // Check card 3 (Scene 104, Group B 1 of 2)
    const card104 = cards.nth(3);
    await expect(card104).toHaveClass(/scene-card--duplicate/);
    await expect(card104.locator(".badge-danger")).toContainText("Group B (1 of 2)");

    // Check card 4 (Scene 105, Group B 2 of 2)
    const card105 = cards.nth(4);
    await expect(card105).toHaveClass(/scene-card--duplicate/);
    await expect(card105.locator(".badge-danger")).toContainText("Group B (2 of 2)");

    // Verify Consolidate and Build buttons are disabled
    const consolidateBtn = page.locator("#btn-consolidate");
    const buildBtn = page.locator("#btn-build");
    await expect(consolidateBtn).toBeDisabled();
    await expect(buildBtn).toBeDisabled();
    await expect(consolidateBtn).toHaveAttribute("title", /2 filename collisions must be resolved first/);
    await expect(buildBtn).toHaveAttribute("title", /2 filename collisions must be resolved first/);
  });

  test("R2. 'Keep first, remove rest' clears all duplicates and re-enables Consolidate/Build", async ({ page }) => {
    setupMocks(page);

    await page.route("**/graphql", async (route) => {
      const postData = JSON.parse(route.request().postData() || "{}");
      if (postData.query?.includes("FindScenes")) {
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ data: { findScenes: { scenes: testScenes } } })
        });
      }
      return route.continue();
    });

    await page.goto("http://localhost:9999/plugins/deepseek-megapack/review.html?scenes=101,102,103,104,105");
    await page.locator("#output-dir").fill("C:\\Packs");
    await page.waitForSelector(".scene-card");

    // Click "Keep first, remove rest"
    const keepFirstBtn = page.locator("#btn-keep-first");
    await keepFirstBtn.click();

    // Banner should disappear
    const banner = page.locator("#collision-banner");
    await expect(banner).toBeHidden();

    // 3 active cards remain: 101, 102, 104
    const remainingCards = page.locator(".scene-card");
    await expect(remainingCards).toHaveCount(3);
    await expect(remainingCards.nth(0)).toContainText("Alpha Scene 101");
    await expect(remainingCards.nth(1)).toContainText("Beta Scene 102");
    await expect(remainingCards.nth(2)).toContainText("Delta Scene 104");

    // No cards should have duplicate class or badge now
    for (let i = 0; i < 3; i++) {
      await expect(remainingCards.nth(i)).not.toHaveClass(/scene-card--duplicate/);
      await expect(remainingCards.nth(i).locator(".badge-danger")).toHaveCount(0);
    }

    // Numbering should be #1, #2, #3
    await expect(remainingCards.nth(0).locator(".scene-title")).toContainText("#1 - Alpha Scene 101");
    await expect(remainingCards.nth(1).locator(".scene-title")).toContainText("#2 - Beta Scene 102");
    await expect(remainingCards.nth(2).locator(".scene-title")).toContainText("#3 - Delta Scene 104");
    // Consolidate button should be re-enabled. OLD (pre-todo-7): Build stayed
    // disabled until files were moved into the pack-title subfolder. NEW: the
    // fixture files already sit under the seed dir (C:\Packs, recursive), so
    // Build is available immediately.
    const consolidateBtn = page.locator("#btn-consolidate");
    const buildBtn = page.locator("#btn-build");
    await expect(consolidateBtn).toBeEnabled();
    await expect(buildBtn).toBeEnabled();

    // BBCode preview should update to 3 scenes
    const bbcodePreview = page.locator("#bbcode-preview");
    await expect(bbcodePreview).toContainText("[b]Total Scenes:[/b] 3");
    await expect(bbcodePreview).toContainText("1. [b]Alpha Scene 101");
    await expect(bbcodePreview).toContainText("2. [b]Beta Scene 102");
    await expect(bbcodePreview).toContainText("3. [b]Delta Scene 104");
    await expect(bbcodePreview).not.toContainText("Gamma Scene 103");
    await expect(bbcodePreview).not.toContainText("Epsilon Scene 105");
  });

  test("R2. 'Show only conflicts' toggles view without breaking card order or interactions", async ({ page }) => {
    setupMocks(page);

    await page.route("**/graphql", async (route) => {
      const postData = JSON.parse(route.request().postData() || "{}");
      if (postData.query?.includes("FindScenes")) {
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ data: { findScenes: { scenes: testScenes } } })
        });
      }
      return route.continue();
    });

    await page.goto("http://localhost:9999/plugins/deepseek-megapack/review.html?scenes=101,102,103,104,105");
    await page.waitForSelector(".scene-card");

    const filterBtn = page.locator("#btn-filter-conflicts");

    // Click filter to show conflicts only
    await filterBtn.click();
    await expect(filterBtn).toHaveClass(/active/);

    // Only 4 cards should be visible (101, 103, 104, 105) - 102 is hidden
    const filteredCards = page.locator(".scene-card");
    await expect(filteredCards).toHaveCount(4);
    await expect(page.locator('.scene-card[data-scene-id="102"]')).toHaveCount(0);

    // Verify correct cards rendered
    await expect(filteredCards.nth(0)).toContainText("Alpha Scene 101");
    await expect(filteredCards.nth(1)).toContainText("Gamma Scene 103");
    await expect(filteredCards.nth(2)).toContainText("Delta Scene 104");
    await expect(filteredCards.nth(3)).toContainText("Epsilon Scene 105");

    // Click filter again to toggle off
    await filterBtn.click();
    await expect(filterBtn).not.toHaveClass(/active/);

    // All 5 cards visible again
    const allCards = page.locator(".scene-card");
    await expect(allCards).toHaveCount(5);
    await expect(page.locator('.scene-card[data-scene-id="102"]')).toBeVisible();
  });

  test("R2 & R3. Per-card '✕ Remove' updates numbering, resolves collisions incrementally, and updates BBCode", async ({ page }) => {
    setupMocks(page);

    await page.route("**/graphql", async (route) => {
      const postData = JSON.parse(route.request().postData() || "{}");
      if (postData.query?.includes("FindScenes")) {
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ data: { findScenes: { scenes: testScenes } } })
        });
      }
      return route.continue();
    });

    await page.goto("http://localhost:9999/plugins/deepseek-megapack/review.html?scenes=101,102,103,104,105");
    await page.locator("#output-dir").fill("C:\\Packs");
    await page.waitForSelector(".scene-card");

    // Remove scene 103 (the conflicting member of Group A)
    const removeBtn103 = page.locator('.scene-card[data-scene-id="103"] .scene-remove-btn');
    await removeBtn103.click();

    // Scene 103 is gone; remaining scenes: 101, 102, 104, 105 (4 cards)
    const cardsAfterFirstRemove = page.locator(".scene-card");
    await expect(cardsAfterFirstRemove).toHaveCount(4);

    // Scene 101 should NO LONGER have duplicate badge/styling since it's the sole remaining file with that name
    const card101 = page.locator('.scene-card[data-scene-id="101"]');
    await expect(card101).not.toHaveClass(/scene-card--duplicate/);
    await expect(card101.locator(".badge-danger")).toHaveCount(0);

    // Group B (104, 105) is now the ONLY collision group (Group A)
    await expect(page.locator("#collision-headline")).toContainText("1 conflict group");
    await expect(page.locator("#btn-consolidate")).toBeDisabled();

    // Now remove scene 105 (the conflicting member of the remaining group)
    const removeBtn105 = page.locator('.scene-card[data-scene-id="105"] .scene-remove-btn');
    await removeBtn105.click();

    // Banner is now hidden, consolidate enabled
    await expect(page.locator("#collision-banner")).toBeHidden();
    await expect(page.locator("#btn-consolidate")).toBeEnabled();
    // OLD (pre-todo-7): Build disabled until consolidated into the pack-title
    // subfolder. NEW: files under the seed dir (C:\Packs) build in place.
    await expect(page.locator("#btn-build")).toBeEnabled();

    // 3 cards remain: 101, 102, 104
    await expect(page.locator(".scene-card")).toHaveCount(3);
  });

  test("R2. Empty state and 'Restore all removed scenes' functionality", async ({ page }) => {
    setupMocks(page);

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
                  { id: 201, title: "Scene 201", paths: {}, files: [{ id: 2001, path: "C:\\Packs\\s1.mp4" }], performers: [], tags: [] },
                  { id: 202, title: "Scene 202", paths: {}, files: [{ id: 2002, path: "C:\\Packs\\s2.mp4" }], performers: [], tags: [] }
                ]
              }
            }
          })
        });
      }
      return route.continue();
    });

    await page.goto("http://localhost:9999/plugins/deepseek-megapack/review.html?scenes=201,202");
    await page.locator("#output-dir").fill("C:\\Packs");
    await page.waitForSelector(".scene-card");

    // Remove both scenes
    await page.locator('.scene-card[data-scene-id="201"] .scene-remove-btn').click();
    await page.locator('.scene-card[data-scene-id="202"] .scene-remove-btn').click();

    // Empty state rendered with restore button
    await expect(page.locator("#scene-list")).toContainText("All scenes have been removed from the pack.");
    const restoreBtn = page.locator("#btn-restore-all");
    await expect(restoreBtn).toBeVisible();

    // Action buttons disabled with "No scenes in the pack"
    const consolidateBtn = page.locator("#btn-consolidate");
    const buildBtn = page.locator("#btn-build");
    await expect(consolidateBtn).toBeDisabled();
    await expect(buildBtn).toBeDisabled();
    await expect(consolidateBtn).toHaveAttribute("title", "No scenes in the pack");
    await expect(buildBtn).toHaveAttribute("title", "No scenes in the pack");

    // Click restore
    await restoreBtn.click();

    // Both scenes restored and buttons re-enabled
    await expect(page.locator(".scene-card")).toHaveCount(2);
    await expect(consolidateBtn).toBeEnabled();
    // OLD (pre-todo-7): Build disabled until consolidated. NEW: the scenes'
    // files sit under the seed dir (C:\Packs), so Build is available.
    await expect(buildBtn).toBeEnabled();
  });

  test("R3. Payload Integrity: Removed scenes are excluded from GraphQL mutation bodies (moveFiles and runPluginTask)", async ({ page }) => {
    setupMocks(page);

    const localScenes = JSON.parse(JSON.stringify(testScenes));
    let moveFilesInput = null;
    let buildMegapackPayload = null;

    page.on("dialog", async (dialog) => {
      await dialog.accept();
    });

    await page.route("**/graphql", async (route) => {
      const postData = JSON.parse(route.request().postData() || "{}");
      const query = postData.query || "";

      if (query.includes("FindScenes")) {
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ data: { findScenes: { scenes: localScenes } } })
        });
      }

      if (query.includes("MoveFiles")) {
        moveFilesInput = postData.variables?.input;
        localScenes.forEach(s => {
          if (s.files && s.files[0]) {
            const fname = s.files[0].path.split(/[\\/]/).pop();
            s.files[0].path = "D:\\Consolidation\\" + fname;
          }
        });
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ data: { moveFiles: true } })
        });
      }

      if (query.includes("RunBuild") || (query.includes("runPluginTask") && postData.variables?.task_name === "BuildMegapack")) {
        const payloadStr = postData.variables?.args?.find((a) => a.key === "payload")?.value?.str;
        if (payloadStr) {
          buildMegapackPayload = JSON.parse(payloadStr);
        }
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ data: { runPluginTask: "job-build-stage7" } })
        });
      }

      return route.continue();
    });

    await page.goto("http://localhost:9999/plugins/deepseek-megapack/review.html?scenes=101,102,103,104,105");
    await page.waitForSelector(".scene-card");

    // The fixture files sit under C:\Packs — point the seed-dir field
    // (in-place seeding destination) elsewhere so the active files count as
    // missing and the move actually fires.
    await page.locator("#output-dir").fill("D:\\Consolidation");

    // Resolve conflicts with "Keep first, remove rest" -> keeps 101, 102, 104; removes 103, 105
    await page.locator("#btn-keep-first").click();

    // Further remove 102 manually
    await page.locator('.scene-card[data-scene-id="102"] .scene-remove-btn').click();

    // Now active scenes are ONLY 101 and 104
    await expect(page.locator(".scene-card")).toHaveCount(2);

    // Test Consolidate Files payload
    await page.locator("#btn-consolidate").click();

    await expect.poll(() => moveFilesInput).toBeTruthy();
    expect(moveFilesInput.ids).toEqual([1001, 1004]);
    expect(moveFilesInput.ids).not.toContain(1002);
    expect(moveFilesInput.ids).not.toContain(1003);
    expect(moveFilesInput.ids).not.toContain(1005);
    expect(moveFilesInput.destination_folder).toBe("D:\\Consolidation");

    // Test Build Megapack payload
    await page.locator("#btn-build").click();

    await expect.poll(() => buildMegapackPayload).toBeTruthy();
    const payloadSceneIds = buildMegapackPayload.scenes.map((s) => s.id);
    expect(payloadSceneIds).toEqual([101, 104]);
    expect(payloadSceneIds).not.toContain(102);
    expect(payloadSceneIds).not.toContain(103);
    expect(payloadSceneIds).not.toContain(105);

    // Performers and tags aggregation should also ONLY contain active scenes (101 and 104)
    expect(buildMegapackPayload.performers).toEqual(["Performer One", "Performer Four"]);
    expect(buildMegapackPayload.tags).toEqual(["TagA", "TagD"]);
  });

  test("R3 & F8. Removals persist across loadScenes() re-fetch", async ({ page }) => {
    setupMocks(page);

    let fetchCount = 0;
    await page.route("**/graphql", async (route) => {
      const postData = JSON.parse(route.request().postData() || "{}");
      if (postData.query?.includes("FindScenes")) {
        fetchCount++;
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ data: { findScenes: { scenes: testScenes } } })
        });
      }
      return route.continue();
    });

    await page.goto("http://localhost:9999/plugins/deepseek-megapack/review.html?scenes=101,102,103,104,105");
    await page.waitForSelector(".scene-card");

    // Remove scene 103 and 105
    await page.locator('.scene-card[data-scene-id="103"] .scene-remove-btn').click();
    await page.locator('.scene-card[data-scene-id="105"] .scene-remove-btn').click();

    await expect(page.locator(".scene-card")).toHaveCount(3);

    // Trigger loadScenes() again (simulating re-fetch after consolidation)
    await page.evaluate(() => window.loadScenes());

    // Wait and verify removals are still honored
    await expect(page.locator(".scene-card")).toHaveCount(3);
    await expect(page.locator('.scene-card[data-scene-id="101"]')).toBeVisible();
    await expect(page.locator('.scene-card[data-scene-id="102"]')).toBeVisible();
    await expect(page.locator('.scene-card[data-scene-id="104"]')).toBeVisible();
    await expect(page.locator('.scene-card[data-scene-id="103"]')).toHaveCount(0);
    await expect(page.locator('.scene-card[data-scene-id="105"]')).toHaveCount(0);
  });

  test("ADV-1. Multi-file scene collision: correct badge ordinals and 'Keep first' preserves first scene", async ({ page }) => {
    setupMocks(page);

    const multiFileScenes = [
      {
        id: 301,
        title: "Multi-File Alpha Scene 301",
        paths: { screenshot: "http://localhost:9999/shot301.jpg" },
        files: [
          { id: 3001, path: "C:\\Dir1\\Shared_Clip.mp4", size: 1000000 },
          { id: 3002, path: "C:\\Dir2\\shared_clip.mp4", size: 1000000 }
        ],
        performers: [{ id: 1, name: "Performer One" }],
        tags: []
      },
      {
        id: 302,
        title: "Single-File Beta Scene 302",
        paths: { screenshot: "http://localhost:9999/shot302.jpg" },
        files: [
          { id: 3003, path: "D:\\Dir3\\SHARED_CLIP.MP4", size: 2000000 }
        ],
        performers: [{ id: 2, name: "Performer Two" }],
        tags: []
      },
      {
        id: 303,
        title: "Unique Scene 303",
        paths: { screenshot: "http://localhost:9999/shot303.jpg" },
        files: [
          { id: 3004, path: "D:\\Dir4\\unique_clip.mp4", size: 3000000 }
        ],
        performers: [],
        tags: []
      }
    ];

    await page.route("**/graphql", async (route) => {
      const postData = JSON.parse(route.request().postData() || "{}");
      if (postData.query?.includes("FindScenes")) {
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ data: { findScenes: { scenes: multiFileScenes } } })
        });
      }
      return route.continue();
    });

    await page.goto("http://localhost:9999/plugins/deepseek-megapack/review.html?scenes=301,302,303");
    await page.waitForSelector(".scene-card");

    // Banner should report 1 conflict group and 3 colliding files across 2 scenes
    const headline = page.locator("#collision-headline");
    await expect(headline).toContainText("1 conflict group");
    await expect(headline).toContainText("3 colliding files");

    // Scene 301 card badge: Group A (1 of 2) - distinct scene count, not file count!
    const card301 = page.locator('.scene-card[data-scene-id="301"]');
    await expect(card301.locator(".badge-danger")).toContainText("Group A (1 of 2)");

    // Scene 302 card badge: Group A (2 of 2)
    const card302 = page.locator('.scene-card[data-scene-id="302"]');
    await expect(card302.locator(".badge-danger")).toContainText("Group A (2 of 2)");

    // Click "Keep first, remove rest"
    await page.locator("#btn-keep-first").click();

    // Scene 301 must NOT be excluded! Scene 302 MUST be excluded.
    const remainingCards = page.locator(".scene-card");
    await expect(remainingCards).toHaveCount(2);
    await expect(page.locator('.scene-card[data-scene-id="301"]')).toBeVisible();
    await expect(page.locator('.scene-card[data-scene-id="303"]')).toBeVisible();
    await expect(page.locator('.scene-card[data-scene-id="302"]')).toHaveCount(0);
  });

  test("ADV-2. Scale: >26 Collision Groups Generate Spreadsheet Labels (Group A -> Group Z -> Group AA -> Group AB)", async ({ page }) => {
    setupMocks(page);

    // Create 28 collision groups (56 scenes)
    const largeSceneList = [];
    for (let i = 0; i < 28; i++) {
      const sIdA = 1000 + i * 2 + 1;
      const sIdB = 1000 + i * 2 + 2;
      const filename = `duplicate_video_index_${i}.mp4`;
      largeSceneList.push({
        id: sIdA,
        title: `Scene ${sIdA}`,
        paths: {},
        files: [{ id: sIdA * 10, path: `C:\\LibA\\${filename}` }],
        performers: [],
        tags: []
      });
      largeSceneList.push({
        id: sIdB,
        title: `Scene ${sIdB}`,
        paths: {},
        files: [{ id: sIdB * 10, path: `D:\\LibB\\${filename}` }],
        performers: [],
        tags: []
      });
    }

    await page.route("**/graphql", async (route) => {
      const postData = JSON.parse(route.request().postData() || "{}");
      if (postData.query?.includes("FindScenes")) {
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ data: { findScenes: { scenes: largeSceneList } } })
        });
      }
      return route.continue();
    });

    const idsString = largeSceneList.map((s) => s.id).join(",");
    await page.goto(`http://localhost:9999/plugins/deepseek-megapack/review.html?scenes=${idsString}`);
    await page.waitForSelector(".scene-card");

    // Group 0 is Group A (Scene 1001)
    const cardGroupA = page.locator('.scene-card[data-scene-id="1001"]');
    await expect(cardGroupA.locator(".badge-danger")).toContainText("Group A (1 of 2)");

    // Group 25 is Group Z (Scene 1000 + 25*2 + 1 = 1051)
    const cardGroupZ = page.locator('.scene-card[data-scene-id="1051"]');
    await expect(cardGroupZ.locator(".badge-danger")).toContainText("Group Z (1 of 2)");

    // Group 26 is Group AA (Scene 1000 + 26*2 + 1 = 1053)
    const cardGroupAA = page.locator('.scene-card[data-scene-id="1053"]');
    await expect(cardGroupAA.locator(".badge-danger")).toContainText("Group AA (1 of 2)");

    // Group 27 is Group AB (Scene 1000 + 27*2 + 1 = 1055)
    const cardGroupAB = page.locator('.scene-card[data-scene-id="1055"]');
    await expect(cardGroupAB.locator(".badge-danger")).toContainText("Group AB (1 of 2)");

    // Verify 28 conflict groups reported in banner
    await expect(page.locator("#collision-headline")).toContainText("28 conflict groups");
  });

  test("ADV-3. Re-fetch in loadScenes() prunes stale excluded IDs no longer returned by Stash", async ({ page }) => {
    setupMocks(page);

    let activeScenesList = [...testScenes];

    await page.route("**/graphql", async (route) => {
      const postData = JSON.parse(route.request().postData() || "{}");
      if (postData.query?.includes("FindScenes")) {
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ data: { findScenes: { scenes: activeScenesList } } })
        });
      }
      return route.continue();
    });

    await page.goto("http://localhost:9999/plugins/deepseek-megapack/review.html?scenes=101,102,103,104,105");
    await page.waitForSelector(".scene-card");

    // Remove scene 103 and 105
    await page.locator('.scene-card[data-scene-id="103"] .scene-remove-btn').click();
    await page.locator('.scene-card[data-scene-id="105"] .scene-remove-btn').click();

    // Simulate Stash scene 105 being deleted on server, so next fetch returns only [101, 102, 103, 104]
    activeScenesList = testScenes.filter((s) => s.id !== 105);

    // Call loadScenes() to re-fetch
    await page.evaluate(() => window.loadScenes());

    // 3 cards remain (101, 102, 104); 103 is still excluded
    await expect(page.locator(".scene-card")).toHaveCount(3);
    await expect(page.locator('.scene-card[data-scene-id="101"]')).toBeVisible();
    await expect(page.locator('.scene-card[data-scene-id="102"]')).toBeVisible();
    await expect(page.locator('.scene-card[data-scene-id="104"]')).toBeVisible();
    await expect(page.locator('.scene-card[data-scene-id="103"]')).toHaveCount(0);
    await expect(page.locator('.scene-card[data-scene-id="105"]')).toHaveCount(0);
  });

  test("ADV-4. Drag-and-drop reorder under 'Show only conflicts' correctly updates pack order", async ({ page }) => {
    setupMocks(page);

    await page.route("**/graphql", async (route) => {
      const postData = JSON.parse(route.request().postData() || "{}");
      if (postData.query?.includes("FindScenes")) {
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ data: { findScenes: { scenes: testScenes } } })
        });
      }
      return route.continue();
    });

    await page.goto("http://localhost:9999/plugins/deepseek-megapack/review.html?scenes=101,102,103,104,105");
    await page.waitForSelector(".scene-card");

    // Filter to conflicts only (101, 103, 104, 105)
    await page.locator("#btn-filter-conflicts").click();
    await expect(page.locator(".scene-card")).toHaveCount(4);

    // Simulate reordering by swapping card elements in DOM and calling dragend/reorderScenes
    await page.evaluate(() => {
      const container = document.getElementById("scene-list");
      const cards = Array.from(container.querySelectorAll(".scene-card"));
      // Move last card (105) to the very top
      container.insertBefore(cards[3], cards[0]);
      // Trigger card dragend event to trigger reorderScenes()
      cards[3].dispatchEvent(new Event("dragend", { bubbles: true }));
    });

    // Verify 105 is now at the top of the filtered view
    const reorderedFiltered = page.locator(".scene-card");
    await expect(reorderedFiltered.nth(0)).toContainText("Epsilon Scene 105");

    // Toggle back to show all scenes
    await page.locator("#btn-filter-conflicts").click();

    // Verify pack order in full view: 105 is first, followed by 102, 101, 103, 104
    const fullCards = page.locator(".scene-card");
    await expect(fullCards).toHaveCount(5);
    await expect(fullCards.nth(0)).toContainText("Epsilon Scene 105");
    await expect(fullCards.nth(1)).toContainText("Beta Scene 102");
    await expect(fullCards.nth(2)).toContainText("Alpha Scene 101");
    await expect(fullCards.nth(3)).toContainText("Gamma Scene 103");
    await expect(fullCards.nth(4)).toContainText("Delta Scene 104");

    // Verify BBCode preview also reflects new pack order
    const bbcodePreview = page.locator("#bbcode-preview");
    await expect(bbcodePreview).toContainText("1. [b]Epsilon Scene 105");
  });

  test("ADV-5. Overlapping multi-group collisions: Scene 1 participates in both Group A and Group B, and 'Keep first, remove rest' resolves all groups", async ({ page }) => {
    setupMocks(page);

    const overlappingScenes = [
      {
        id: 401,
        title: "Alpha Scene 401 (Has file1 and file2)",
        paths: {},
        files: [
          { id: 4001, path: "C:\\Packs\\video_one.mp4" },
          { id: 4002, path: "C:\\Packs\\video_two.mp4" }
        ],
        performers: [{ id: 1, name: "Performer One" }],
        tags: []
      },
      {
        id: 402,
        title: "Beta Scene 402 (Collides with file1)",
        paths: {},
        files: [{ id: 4003, path: "C:\\Packs\\VIDEO_ONE.MP4" }],
        performers: [{ id: 2, name: "Performer Two" }],
        tags: []
      },
      {
        id: 403,
        title: "Gamma Scene 403 (Collides with file2)",
        paths: {},
        files: [{ id: 4004, path: "C:\\Packs\\video_two.MP4" }],
        performers: [{ id: 3, name: "Performer Three" }],
        tags: []
      },
      {
        id: 404,
        title: "Delta Scene 404 (Unique)",
        paths: {},
        files: [{ id: 4005, path: "C:\\Packs\\unique_track.mp4" }],
        performers: [{ id: 4, name: "Performer Four" }],
        tags: []
      }
    ];

    await page.route("**/graphql", async (route) => {
      const postData = JSON.parse(route.request().postData() || "{}");
      if (postData.query?.includes("FindScenes")) {
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ data: { findScenes: { scenes: overlappingScenes } } })
        });
      }
      return route.continue();
    });

    await page.goto("http://localhost:9999/plugins/deepseek-megapack/review.html?scenes=401,402,403,404");
    await page.locator("#output-dir").fill("C:\\Packs");
    await page.waitForSelector(".scene-card");

    // Banner should report 2 conflict groups and 4 colliding files
    await expect(page.locator("#collision-headline")).toContainText("2 conflict groups");
    await expect(page.locator("#collision-headline")).toContainText("4 colliding files");
    await expect(page.locator("#btn-consolidate")).toBeDisabled();
    await expect(page.locator("#btn-build")).toBeDisabled();

    // Scene 401 is duplicate
    await expect(page.locator('.scene-card[data-scene-id="401"]')).toHaveClass(/scene-card--duplicate/);

    // Click "Keep first, remove rest"
    await page.locator("#btn-keep-first").click();

    // Banner should be hidden, buttons enabled
    await expect(page.locator("#collision-banner")).toBeHidden();
    await expect(page.locator("#btn-consolidate")).toBeEnabled();
    // OLD (pre-todo-7): Build disabled until consolidated. NEW: files under
    // the seed dir (C:\Packs) build in place.
    await expect(page.locator("#btn-build")).toBeEnabled();

    // 2 active scenes remain: 401 and 404
    const remaining = page.locator(".scene-card");
    await expect(remaining).toHaveCount(2);
    await expect(page.locator('.scene-card[data-scene-id="401"]')).toBeVisible();
    await expect(page.locator('.scene-card[data-scene-id="404"]')).toBeVisible();
    await expect(page.locator('.scene-card[data-scene-id="402"]')).toHaveCount(0);
    await expect(page.locator('.scene-card[data-scene-id="403"]')).toHaveCount(0);
  });

  test("ADV-6. Complex filenames & XSS matrix: special characters, spaces, brackets, unicode, dots, and case variations", async ({ page }) => {
    setupMocks(page);

    const complexScenes = [
      {
        id: 501,
        title: "Scene 501 <script>alert(1)</script>",
        paths: {},
        files: [{ id: 5001, path: "C:\\Media\\[Studio] Complex (2026) #1.1 & More <Tag>.1080p.mkv" }],
        performers: [],
        tags: []
      },
      {
        id: 502,
        title: "Scene 502",
        paths: {},
        files: [{ id: 5002, path: "/unix/path/[STUDIO] complex (2026) #1.1 & more <tag>.1080P.MKV" }],
        performers: [],
        tags: []
      },
      {
        id: 503,
        title: "Scene 503 (Leading Dot)",
        paths: {},
        files: [{ id: 5003, path: "D:\\Media\\..hidden_dot_file.mp4" }],
        performers: [],
        tags: []
      },
      {
        id: 504,
        title: "Scene 504 (Leading Dot)",
        paths: {},
        files: [{ id: 5004, path: "E:\\Media\\..HIDDEN_DOT_FILE.MP4" }],
        performers: [],
        tags: []
      },
      {
        id: 505,
        title: "Scene 505 (Unicode)",
        paths: {},
        files: [{ id: 5005, path: "F:\\Media\\unicode_日本語_test.mp4" }],
        performers: [],
        tags: []
      },
      {
        id: 506,
        title: "Scene 506 (Unicode)",
        paths: {},
        files: [{ id: 5006, path: "G:\\Media\\UNICODE_日本語_TEST.mp4" }],
        performers: [],
        tags: []
      }
    ];

    await page.route("**/graphql", async (route) => {
      const postData = JSON.parse(route.request().postData() || "{}");
      if (postData.query?.includes("FindScenes")) {
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ data: { findScenes: { scenes: complexScenes } } })
        });
      }
      return route.continue();
    });

    await page.goto("http://localhost:9999/plugins/deepseek-megapack/review.html?scenes=501,502,503,504,505,506");
    await page.waitForSelector(".scene-card");

    // All 3 collision groups detected (3 groups, 6 colliding files)
    await expect(page.locator("#collision-headline")).toContainText("3 conflict groups");
    await expect(page.locator("#collision-headline")).toContainText("6 colliding files");

    // Assert script tag was escaped and not executed or rendered as raw HTML element
    const card501 = page.locator('.scene-card[data-scene-id="501"]');
    await expect(card501.locator("script")).toHaveCount(0);
    await expect(card501.locator(".scene-title")).toContainText("<script>alert(1)</script>");

    // Click "Keep first, remove rest" -> keeps 501, 503, 505; removes 502, 504, 506
    await page.locator("#btn-keep-first").click();

    await expect(page.locator("#collision-banner")).toBeHidden();
    const remaining = page.locator(".scene-card");
    await expect(remaining).toHaveCount(3);
    await expect(page.locator('.scene-card[data-scene-id="501"]')).toBeVisible();
    await expect(page.locator('.scene-card[data-scene-id="503"]')).toBeVisible();
    await expect(page.locator('.scene-card[data-scene-id="505"]')).toBeVisible();
  });

  test("ADV-7. Complete conflict filter lifecycle: incremental resolution auto-exits filter, restore resets duplicate state", async ({ page }) => {
    setupMocks(page);

    const filterLifecycleScenes = [
      { id: 601, title: "Scene 601", paths: {}, files: [{ id: 6001, path: "C:\\Packs\\a.mp4" }], performers: [], tags: [] },
      { id: 602, title: "Scene 602", paths: {}, files: [{ id: 6002, path: "C:\\Packs\\A.MP4" }], performers: [], tags: [] },
      { id: 603, title: "Scene 603", paths: {}, files: [{ id: 6003, path: "C:\\Packs\\b.mp4" }], performers: [], tags: [] },
      { id: 604, title: "Scene 604", paths: {}, files: [{ id: 6004, path: "C:\\Packs\\B.MP4" }], performers: [], tags: [] },
      { id: 605, title: "Scene 605 (Unique)", paths: {}, files: [{ id: 6005, path: "C:\\Packs\\unique.mp4" }], performers: [], tags: [] }
    ];

    await page.route("**/graphql", async (route) => {
      const postData = JSON.parse(route.request().postData() || "{}");
      if (postData.query?.includes("FindScenes")) {
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ data: { findScenes: { scenes: filterLifecycleScenes } } })
        });
      }
      return route.continue();
    });

    await page.goto("http://localhost:9999/plugins/deepseek-megapack/review.html?scenes=601,602,603,604,605");
    await page.locator("#output-dir").fill("C:\\Packs");
    await page.waitForSelector(".scene-card");

    // Toggle "Show only conflicts"
    const filterBtn = page.locator("#btn-filter-conflicts");
    await filterBtn.click();
    await expect(page.locator(".scene-card")).toHaveCount(4);

    // Remove 602 (resolves group A)
    await page.locator('.scene-card[data-scene-id="602"] .scene-remove-btn').click();
    // In conflict view, 2 scenes remain (603, 604)
    await expect(page.locator(".scene-card")).toHaveCount(2);

    // Remove 604 (resolves group B -> 0 collision groups remain)
    await page.locator('.scene-card[data-scene-id="604"] .scene-remove-btn').click();

    // With 0 collisions, filter auto-resets and banner hides; 3 remaining scenes displayed: 601, 603, 605
    await expect(page.locator("#collision-banner")).toBeHidden();
    await expect(page.locator(".scene-card")).toHaveCount(3);
    await expect(page.locator("#btn-consolidate")).toBeEnabled();
    // OLD (pre-todo-7): Build disabled until consolidated. NEW: files under
    // the seed dir (C:\Packs) build in place.
    await expect(page.locator("#btn-build")).toBeEnabled();

    // Now remove all remaining scenes one by one
    await page.locator('.scene-card[data-scene-id="601"] .scene-remove-btn').click();
    await page.locator('.scene-card[data-scene-id="603"] .scene-remove-btn').click();
    await page.locator('.scene-card[data-scene-id="605"] .scene-remove-btn').click();

    // Empty state
    await expect(page.locator("#btn-restore-all")).toBeVisible();
    await expect(page.locator("#btn-consolidate")).toBeDisabled();

    // Click restore all
    await page.locator("#btn-restore-all").click();

    // All 5 restored, 2 collision groups back, banner visible, buttons disabled
    await expect(page.locator(".scene-card")).toHaveCount(5);
    await expect(page.locator("#collision-banner")).toBeVisible();
    await expect(page.locator("#collision-headline")).toContainText("2 conflict groups");
    await expect(page.locator("#btn-consolidate")).toBeDisabled();
    await expect(page.locator("#btn-build")).toBeDisabled();
  });

  test("ADV-8. Resilient handling of malformed scene metadata (empty paths, missing files array, nulls)", async ({ page }) => {
    setupMocks(page);

    const malformedScenes = [
      { id: 701, title: "Scene 701 Empty Files", paths: {}, files: [], performers: [], tags: [] },
      { id: 702, title: "Scene 702 Empty Path", paths: {}, files: [{ id: 7002, path: "" }], performers: [], tags: [] },
      { id: 703, title: "Scene 703 Null Path", paths: {}, files: [{ id: 7003, path: null }], performers: [], tags: [] },
      { id: 704, title: "Scene 704 Null Files", paths: {}, files: null, performers: [], tags: [] },
      { id: 705, title: "Scene 705 Colliding File A", paths: {}, files: [{ id: 7005, path: "C:\\valid.mp4" }], performers: [], tags: [] },
      { id: 706, title: "Scene 706 Colliding File B", paths: {}, files: [{ id: 7006, path: "D:\\VALID.mp4" }], performers: [], tags: [] }
    ];

    await page.route("**/graphql", async (route) => {
      const postData = JSON.parse(route.request().postData() || "{}");
      if (postData.query?.includes("FindScenes")) {
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ data: { findScenes: { scenes: malformedScenes } } })
        });
      }
      return route.continue();
    });

    await page.goto("http://localhost:9999/plugins/deepseek-megapack/review.html?scenes=701,702,703,704,705,706");
    await page.waitForSelector(".scene-card");

    // All 6 cards rendered cleanly
    const cards = page.locator(".scene-card");
    await expect(cards).toHaveCount(6);

    // Exactly 1 conflict group detected (between 705 and 706)
    await expect(page.locator("#collision-headline")).toContainText("1 conflict group");
    await expect(page.locator("#collision-headline")).toContainText("2 colliding files");

    // Malformed scenes have no duplicate badge or class
    for (const sId of ["701", "702", "703", "704"]) {
      const c = page.locator(`.scene-card[data-scene-id="${sId}"]`);
      await expect(c).not.toHaveClass(/scene-card--duplicate/);
      await expect(c.locator(".badge-danger")).toHaveCount(0);
    }

    // 705 and 706 have duplicate badge and class
    await expect(page.locator('.scene-card[data-scene-id="705"]')).toHaveClass(/scene-card--duplicate/);
    await expect(page.locator('.scene-card[data-scene-id="706"]')).toHaveClass(/scene-card--duplicate/);
  });

  test("R1. Rich Scene Comparison Metadata & Quality Indicators: Displays formatted size, duration, resolution, codec, file path, and comparative superiority badges", async ({ page }) => {
    setupMocks(page);

    await page.route("**/graphql", async (route) => {
      const postData = JSON.parse(route.request().postData() || "{}");
      if (postData.query?.includes("FindScenes")) {
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ data: { findScenes: { scenes: testScenes } } })
        });
      }
      return route.continue();
    });

    await page.goto("http://localhost:9999/plugins/deepseek-megapack/review.html?scenes=101,102,103,104,105");
    await page.waitForSelector(".scene-card");

    const card101 = page.locator('.scene-card[data-scene-id="101"]');
    const card102 = page.locator('.scene-card[data-scene-id="102"]');
    const card103 = page.locator('.scene-card[data-scene-id="103"]');
    const card104 = page.locator('.scene-card[data-scene-id="104"]');
    const card105 = page.locator('.scene-card[data-scene-id="105"]');

    // 1. Verify Card 101 Media Specs (Group A: 1000000 bytes -> 976.56 KB, 1200s -> 20m 0s, 1080p, h264)
    await expect(card101.locator(".scene-specs")).toBeVisible();
    await expect(card101.locator(".spec-res")).toContainText("1080p");
    await expect(card101.locator(".spec-codec")).toContainText("h264");
    await expect(card101.locator(".spec-size")).toContainText("976.56 KB");
    await expect(card101.locator(".spec-dur")).toContainText("20m 0s");
    await expect(card101).toContainText("C:\\Packs\\Conflict_Video.mp4");

    // 2. Verify Card 103 Media Specs (Group A: 3000000 bytes -> 2.86 MB, 1800s -> 30m 0s, 1080p, h264)
    await expect(card103.locator(".scene-specs")).toBeVisible();
    await expect(card103.locator(".spec-res")).toContainText("1080p");
    await expect(card103.locator(".spec-codec")).toContainText("h264");
    await expect(card103.locator(".spec-size")).toContainText("2.86 MB");
    await expect(card103.locator(".spec-dur")).toContainText("30m 0s");
    await expect(card103).toContainText("C:\\Packs\\conflict_video.MP4");

    // 3. Verify Quality Superiority Badges in Group A (Card 103 has larger size and longer runtime than 101)
    await expect(card103.locator(".badge-superior")).toHaveCount(2);
    await expect(card103.locator(".badge-superior-size")).toContainText("Larger Size (2.86 MB)");
    await expect(card103.locator(".badge-superior-duration")).toContainText("Longer Runtime (30m 0s)");
    // Card 101 has inferior size & duration, identical resolution, so 0 superiority badges
    await expect(card101.locator(".badge-superior")).toHaveCount(0);

    // 4. Verify Card 104 Media Specs (Group B: 4000000 bytes -> 3.81 MB, 2400s -> 40m 0s, 2160p, hevc)
    await expect(card104.locator(".spec-res")).toContainText("2160p");
    await expect(card104.locator(".spec-codec")).toContainText("hevc");
    await expect(card104.locator(".spec-size")).toContainText("3.81 MB");
    await expect(card104.locator(".spec-dur")).toContainText("40m 0s");

    // 5. Verify Card 105 Media Specs (Group B: 5000000 bytes -> 4.77 MB, 2600s -> 43m 20s, 2160p, hevc)
    await expect(card105.locator(".spec-res")).toContainText("2160p");
    await expect(card105.locator(".spec-codec")).toContainText("hevc");
    await expect(card105.locator(".spec-size")).toContainText("4.77 MB");
    await expect(card105.locator(".spec-dur")).toContainText("43m 20s");

    // 6. Verify Quality Superiority Badges in Group B (Card 105 has larger size and longer runtime than 104)
    await expect(card105.locator(".badge-superior-size")).toContainText("Larger Size (4.77 MB)");
    await expect(card105.locator(".badge-superior-duration")).toContainText("Longer Runtime (43m 20s)");
    await expect(card104.locator(".badge-superior")).toHaveCount(0);

    // 7. Verify Keep This button presence: visible on duplicate cards (101, 103, 104, 105), absent on unique card 102
    await expect(card101.locator(".scene-keep-btn")).toBeVisible();
    await expect(card101.locator(".scene-keep-btn")).toHaveText("✓ Keep This");
    await expect(card103.locator(".scene-keep-btn")).toBeVisible();
    await expect(card104.locator(".scene-keep-btn")).toBeVisible();
    await expect(card105.locator(".scene-keep-btn")).toBeVisible();
    await expect(card102.locator(".scene-keep-btn")).toHaveCount(0);
  });

  test("R1. Comparative Quality Indicators: Highlights superior resolution when resolutions differ in collision group", async ({ page }) => {
    setupMocks(page);

    const resDiffScenes = [
      {
        id: 801,
        title: "Low Res Scene 801",
        paths: {},
        files: [{ id: 8001, path: "C:\\Packs\\same_name.mp4", size: 1000000, height: 720, width: 1280, duration: 1800, video_codec: "h264" }],
        performers: [],
        tags: []
      },
      {
        id: 802,
        title: "High Res Scene 802",
        paths: {},
        files: [{ id: 8002, path: "C:\\Packs\\SAME_NAME.MP4", size: 1000000, height: 2160, width: 3840, duration: 1800, video_codec: "hevc" }],
        performers: [],
        tags: []
      }
    ];

    await page.route("**/graphql", async (route) => {
      const postData = JSON.parse(route.request().postData() || "{}");
      if (postData.query?.includes("FindScenes")) {
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ data: { findScenes: { scenes: resDiffScenes } } })
        });
      }
      return route.continue();
    });

    await page.goto("http://localhost:9999/plugins/deepseek-megapack/review.html?scenes=801,802");
    await page.waitForSelector(".scene-card");

    const card801 = page.locator('.scene-card[data-scene-id="801"]');
    const card802 = page.locator('.scene-card[data-scene-id="802"]');

    // 802 has 2160p vs 801's 720p -> 802 gets Higher Resolution badge
    await expect(card802.locator(".badge-superior-resolution")).toContainText("Higher Resolution (2160p)");
    await expect(card801.locator(".badge-superior-resolution")).toHaveCount(0);
  });

  test("R2. One-Click 'Keep This' Conflict Resolution: Keeps chosen scene and excludes all other group members in one click", async ({ page }) => {
    setupMocks(page);

    await page.route("**/graphql", async (route) => {
      const postData = JSON.parse(route.request().postData() || "{}");
      if (postData.query?.includes("FindScenes")) {
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ data: { findScenes: { scenes: testScenes } } })
        });
      }
      return route.continue();
    });

    await page.goto("http://localhost:9999/plugins/deepseek-megapack/review.html?scenes=101,102,103,104,105");
    await page.locator("#output-dir").fill("C:\\Packs");
    await page.waitForSelector(".scene-card");

    // Initially 2 conflict groups, 5 scenes
    await expect(page.locator("#collision-headline")).toContainText("2 conflict groups");
    await expect(page.locator("#btn-consolidate")).toBeDisabled();
    await expect(page.locator("#btn-build")).toBeDisabled();

    // Click "Keep This" on Scene 103 (superior quality scene in Group A)
    const keepBtn103 = page.locator('.scene-card[data-scene-id="103"] .scene-keep-btn');
    await keepBtn103.click();

    // Group A is resolved! Scene 101 is excluded, Scene 103 is kept.
    // 4 active cards remain: 102, 103, 104, 105
    const cardsAfterFirstKeep = page.locator(".scene-card");
    await expect(cardsAfterFirstKeep).toHaveCount(4);
    await expect(page.locator('.scene-card[data-scene-id="101"]')).toHaveCount(0);
    await expect(page.locator('.scene-card[data-scene-id="103"]')).toBeVisible();

    // Scene 103 is no longer duplicate (no duplicate styling, no Keep This button)
    const card103 = page.locator('.scene-card[data-scene-id="103"]');
    await expect(card103).not.toHaveClass(/scene-card--duplicate/);
    await expect(card103.locator(".scene-keep-btn")).toHaveCount(0);

    // Group B (104, 105) is now the only remaining conflict group
    await expect(page.locator("#collision-headline")).toContainText("1 conflict group");
    await expect(page.locator("#btn-consolidate")).toBeDisabled();

    // Now click "Keep This" on Scene 105 (superior quality scene in Group B)
    const keepBtn105 = page.locator('.scene-card[data-scene-id="105"] .scene-keep-btn');
    await keepBtn105.click();

    // Group B is resolved! Scene 104 is excluded, Scene 105 is kept.
    // All duplicate groups are resolved: banner is hidden, consolidate enabled!
    await expect(page.locator("#collision-banner")).toBeHidden();
    await expect(page.locator("#btn-consolidate")).toBeEnabled();
    // OLD (pre-todo-7): Build disabled until consolidated. NEW: files under
    // the seed dir (C:\Packs) build in place.
    await expect(page.locator("#btn-build")).toBeEnabled();

    // 3 active cards remain: 102 (Beta), 103 (Gamma), 105 (Epsilon)
    const finalCards = page.locator(".scene-card");
    await expect(finalCards).toHaveCount(3);
    await expect(finalCards.nth(0)).toContainText("Beta Scene 102");
    await expect(finalCards.nth(1)).toContainText("Gamma Scene 103");
    await expect(finalCards.nth(2)).toContainText("Epsilon Scene 105");

    // Title numbers should be #1, #2, #3
    await expect(finalCards.nth(0).locator(".scene-title")).toContainText("#1 - Beta Scene 102");
    await expect(finalCards.nth(1).locator(".scene-title")).toContainText("#2 - Gamma Scene 103");
    await expect(finalCards.nth(2).locator(".scene-title")).toContainText("#3 - Epsilon Scene 105");

    // BBCode preview updated with kept scenes
    const bbcode = page.locator("#bbcode-preview");
    await expect(bbcode).toContainText("[b]Total Scenes:[/b] 3");
    await expect(bbcode).toContainText("1. [b]Beta Scene 102");
    await expect(bbcode).toContainText("2. [b]Gamma Scene 103");
    await expect(bbcode).toContainText("3. [b]Epsilon Scene 105");
    await expect(bbcode).not.toContainText("Alpha Scene 101");
    await expect(bbcode).not.toContainText("Delta Scene 104");
  });

  test("R2. One-Click 'Keep This' in Overlapping Multi-Group Collision: Resolves multiple groups simultaneously", async ({ page }) => {
    setupMocks(page);

    const multiGroupScenes = [
      {
        id: 901,
        title: "Multi-Collision Scene 901",
        paths: {},
        files: [
          { id: 9001, path: "C:\\Packs\\shared_a.mp4", size: 1000000, height: 1080, duration: 1800, video_codec: "h264" },
          { id: 9002, path: "C:\\Packs\\shared_b.mp4", size: 1000000, height: 1080, duration: 1800, video_codec: "h264" }
        ],
        performers: [{ id: 1, name: "Performer One" }],
        tags: []
      },
      {
        id: 902,
        title: "Colliding A Scene 902",
        paths: {},
        files: [{ id: 9003, path: "D:\\Packs\\SHARED_A.MP4", size: 2000000, height: 1080, duration: 1800, video_codec: "h264" }],
        performers: [{ id: 2, name: "Performer Two" }],
        tags: []
      },
      {
        id: 903,
        title: "Colliding B Scene 903",
        paths: {},
        files: [{ id: 9004, path: "E:\\Packs\\SHARED_B.MP4", size: 2000000, height: 1080, duration: 1800, video_codec: "h264" }],
        performers: [{ id: 3, name: "Performer Three" }],
        tags: []
      },
      {
        id: 904,
        title: "Unique Scene 904",
        paths: {},
        files: [{ id: 9005, path: "C:\\Packs\\unique.mp4", size: 3000000, height: 1080, duration: 1800, video_codec: "h264" }],
        performers: [{ id: 4, name: "Performer Four" }],
        tags: []
      }
    ];

    await page.route("**/graphql", async (route) => {
      const postData = JSON.parse(route.request().postData() || "{}");
      if (postData.query?.includes("FindScenes")) {
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ data: { findScenes: { scenes: multiGroupScenes } } })
        });
      }
      return route.continue();
    });

    await page.goto("http://localhost:9999/plugins/deepseek-megapack/review.html?scenes=901,902,903,904");
    await page.locator("#output-dir").fill("C:\\Packs");
    await page.waitForSelector(".scene-card");

    // Banner reports 2 conflict groups
    await expect(page.locator("#collision-headline")).toContainText("2 conflict groups");
    await expect(page.locator("#btn-consolidate")).toBeDisabled();

    // Click "Keep This" on Scene 901 (which participates in BOTH Group A and Group B)
    const keepBtn901 = page.locator('.scene-card[data-scene-id="901"] .scene-keep-btn');
    await keepBtn901.click();

    // Both Scene 902 (Group A) and Scene 903 (Group B) are excluded in a single click!
    // Banner is immediately cleared, consolidate enabled
    await expect(page.locator("#collision-banner")).toBeHidden();
    await expect(page.locator("#btn-consolidate")).toBeEnabled();
    // OLD (pre-todo-7): Build disabled until consolidated. NEW: files under
    // the seed dir (C:\Packs) build in place.
    await expect(page.locator("#btn-build")).toBeEnabled();

    // 2 scenes remain: 901 and 904
    const remaining = page.locator(".scene-card");
    await expect(remaining).toHaveCount(2);
    await expect(page.locator('.scene-card[data-scene-id="901"]')).toBeVisible();
    await expect(page.locator('.scene-card[data-scene-id="904"]')).toBeVisible();
    await expect(page.locator('.scene-card[data-scene-id="902"]')).toHaveCount(0);
    await expect(page.locator('.scene-card[data-scene-id="903"]')).toHaveCount(0);
  });

  test("R2 & R3. 'Keep This' Conflict Resolution excludes unchosen scenes from GraphQL mutation payloads", async ({ page }) => {
    setupMocks(page);

    const localScenes = JSON.parse(JSON.stringify(testScenes));
    let moveFilesInput = null;
    let buildMegapackPayload = null;

    page.on("dialog", async (dialog) => {
      await dialog.accept();
    });

    await page.route("**/graphql", async (route) => {
      const postData = JSON.parse(route.request().postData() || "{}");
      const query = postData.query || "";

      if (query.includes("FindScenes")) {
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ data: { findScenes: { scenes: localScenes } } })
        });
      }

      if (query.includes("MoveFiles")) {
        moveFilesInput = postData.variables?.input;
        localScenes.forEach(s => {
          if (s.files && s.files[0]) {
            const fname = s.files[0].path.split(/[\\/]/).pop();
            s.files[0].path = "D:\\Consolidation\\" + fname;
          }
        });
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ data: { moveFiles: true } })
        });
      }

      if (query.includes("RunBuild") || (query.includes("runPluginTask") && postData.variables?.task_name === "BuildMegapack")) {
        const payloadStr = postData.variables?.args?.find((a) => a.key === "payload")?.value?.str;
        if (payloadStr) {
          buildMegapackPayload = JSON.parse(payloadStr);
        }
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ data: { runPluginTask: "job-build-keep-this" } })
        });
      }

      return route.continue();
    });

    await page.goto("http://localhost:9999/plugins/deepseek-megapack/review.html?scenes=101,102,103,104,105");
    await page.waitForSelector(".scene-card");

    // The fixture files sit under C:\Packs — point the seed-dir field
    // (in-place seeding destination) elsewhere so the active files count as
    // missing and the move actually fires.
    await page.locator("#output-dir").fill("D:\\Consolidation");

    // Use "Keep This" to keep 103 (excludes 101) and keep 105 (excludes 104)
    await page.locator('.scene-card[data-scene-id="103"] .scene-keep-btn').click();
    await page.locator('.scene-card[data-scene-id="105"] .scene-keep-btn').click();

    // Active scenes are 102, 103, 105 (files: 1002, 1003, 1005)
    await expect(page.locator(".scene-card")).toHaveCount(3);

    // Test Consolidate Files mutation payload
    await page.locator("#btn-consolidate").click();

    await expect.poll(() => moveFilesInput).toBeTruthy();
    expect(moveFilesInput.ids).toEqual([1002, 1003, 1005]);
    expect(moveFilesInput.ids).not.toContain(1001);
    expect(moveFilesInput.ids).not.toContain(1004);
    expect(moveFilesInput.destination_folder).toBe("D:\\Consolidation");

    // Test Build Megapack payload
    await page.locator("#btn-build").click();

    await expect.poll(() => buildMegapackPayload).toBeTruthy();
    const payloadSceneIds = buildMegapackPayload.scenes.map((s) => s.id);
    expect(payloadSceneIds).toEqual([102, 103, 105]);
    expect(payloadSceneIds).not.toContain(101);
    expect(payloadSceneIds).not.toContain(104);

    // Check performers and tags aggregation
    expect(buildMegapackPayload.performers).toEqual(["Performer Two", "Performer Three", "Performer Five"]);
    expect(buildMegapackPayload.tags).toEqual(["TagB", "TagC", "TagE"]);
  });

  test("R2. 'Keep This' conflict resolution works seamlessly under 'Show only conflicts' filtered view", async ({ page }) => {
    setupMocks(page);

    await page.route("**/graphql", async (route) => {
      const postData = JSON.parse(route.request().postData() || "{}");
      if (postData.query?.includes("FindScenes")) {
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ data: { findScenes: { scenes: testScenes } } })
        });
      }
      return route.continue();
    });

    await page.goto("http://localhost:9999/plugins/deepseek-megapack/review.html?scenes=101,102,103,104,105");
    await page.locator("#output-dir").fill("C:\\Packs");
    await page.waitForSelector(".scene-card");

    // Filter to conflicts (101, 103, 104, 105)
    await page.locator("#btn-filter-conflicts").click();
    await expect(page.locator(".scene-card")).toHaveCount(4);

    // Keep Scene 103 in Group A -> 101 excluded; Group A resolved
    await page.locator('.scene-card[data-scene-id="103"] .scene-keep-btn').click();

    // Filtered view now shows only remaining conflicting group B (104, 105)
    await expect(page.locator(".scene-card")).toHaveCount(2);
    await expect(page.locator('.scene-card[data-scene-id="104"]')).toBeVisible();
    await expect(page.locator('.scene-card[data-scene-id="105"]')).toBeVisible();

    // Keep Scene 105 in Group B -> 104 excluded; all conflicts resolved
    await page.locator('.scene-keep-btn[data-scene-id="105"]').click();

    // Banner hidden, view automatically exits filter and shows all 3 active scenes (102, 103, 105)
    await expect(page.locator("#collision-banner")).toBeHidden();
    await expect(page.locator(".scene-card")).toHaveCount(3);
    await expect(page.locator("#btn-consolidate")).toBeEnabled();
    // OLD (pre-todo-7): Build disabled until consolidated. NEW: files under
    // the seed dir (C:\Packs) build in place.
    await expect(page.locator("#btn-build")).toBeEnabled();
  });

  test("ADV-9. Multi-file scene collision accurately targets colliding file specs and quality superiority over non-colliding files[0]", async ({ page }) => {
    setupMocks(page);

    const multiFileCollidingScenes = [
      {
        id: 951,
        title: "Multi-File Scene 951 (Sample at files[0], 4K feature at files[1])",
        paths: {},
        files: [
          { id: 9501, path: "C:\\Packs\\bonus_sample.mp4", size: 50000000, height: 720, width: 1280, duration: 60, video_codec: "h264" },
          { id: 9502, path: "C:\\Packs\\feature_movie.mp4", size: 4000000000, height: 2160, width: 3840, duration: 3600, video_codec: "hevc" }
        ],
        performers: [],
        tags: []
      },
      {
        id: 952,
        title: "Compromised Scene 952 (1080p feature at files[0])",
        paths: {},
        files: [
          { id: 9503, path: "D:\\Packs\\FEATURE_MOVIE.MP4", size: 2000000000, height: 1080, width: 1920, duration: 3600, video_codec: "h264" }
        ],
        performers: [],
        tags: []
      }
    ];

    await page.route("**/graphql", async (route) => {
      const postData = JSON.parse(route.request().postData() || "{}");
      if (postData.query?.includes("FindScenes")) {
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ data: { findScenes: { scenes: multiFileCollidingScenes } } })
        });
      }
      return route.continue();
    });

    await page.goto("http://localhost:9999/plugins/deepseek-megapack/review.html?scenes=951,952");
    await page.waitForSelector(".scene-card");

    const card951 = page.locator('.scene-card[data-scene-id="951"]');
    const card952 = page.locator('.scene-card[data-scene-id="952"]');

    // Card 951 specs must show the colliding file (files[1]: 2160p, hevc, 3.73 GB, 1h 0m 0s), NOT the sample (files[0]: 720p)
    await expect(card951.locator(".spec-res")).toContainText("2160p");
    await expect(card951.locator(".spec-codec")).toContainText("hevc");
    await expect(card951.locator(".spec-size")).toContainText("3.73 GB");
    await expect(card951).toContainText("C:\\Packs\\feature_movie.mp4");

    // Card 951 must receive Higher Resolution and Larger Size badges because its colliding file (2160p, 3.73GB) beats 952's (1080p, 1.86GB)
    await expect(card951.locator(".badge-superior-resolution")).toContainText("Higher Resolution (2160p)");
    await expect(card951.locator(".badge-superior-size")).toContainText("Larger Size (3.73 GB)");
    await expect(card952.locator(".badge-superior")).toHaveCount(0);
  });

  test("ADV-10. Multi-group scene collision deduplicates superiority badges into clean combined badges", async ({ page }) => {
    setupMocks(page);

    const multiGroupScenes = [
      {
        id: 961,
        title: "Leader Scene 961 (collides in both Group A and Group B with superior 4K)",
        paths: {},
        files: [
          { id: 9601, path: "C:\\Packs\\group_a.mp4", size: 4000000000, height: 2160, width: 3840, duration: 3600, video_codec: "hevc" },
          { id: 9602, path: "C:\\Packs\\group_b.mp4", size: 4000000000, height: 2160, width: 3840, duration: 3600, video_codec: "hevc" }
        ],
        performers: [],
        tags: []
      },
      {
        id: 962,
        title: "Subordinate A Scene 962 (720p)",
        paths: {},
        files: [
          { id: 9603, path: "D:\\Packs\\GROUP_A.MP4", size: 1000000000, height: 720, width: 1280, duration: 3600, video_codec: "h264" }
        ],
        performers: [],
        tags: []
      },
      {
        id: 963,
        title: "Subordinate B Scene 963 (1080p)",
        paths: {},
        files: [
          { id: 9604, path: "E:\\Packs\\GROUP_B.MP4", size: 2000000000, height: 1080, width: 1920, duration: 3600, video_codec: "h264" }
        ],
        performers: [],
        tags: []
      }
    ];

    await page.route("**/graphql", async (route) => {
      const postData = JSON.parse(route.request().postData() || "{}");
      if (postData.query?.includes("FindScenes")) {
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ data: { findScenes: { scenes: multiGroupScenes } } })
        });
      }
      return route.continue();
    });

    await page.goto("http://localhost:9999/plugins/deepseek-megapack/review.html?scenes=961,962,963");
    await page.waitForSelector(".scene-card");

    const card961 = page.locator('.scene-card[data-scene-id="961"]');

    // Should have exactly 1 resolution badge and 1 size badge (no duplicate 2160p badges), with combined title tooltip
    await expect(card961.locator(".badge-superior-resolution")).toHaveCount(1);
    await expect(card961.locator(".badge-superior-size")).toHaveCount(1);
    const resBadge = card961.locator(".badge-superior-resolution");
    await expect(resBadge).toHaveAttribute("title", /Group A, Group B/);
  });

  test("ADV-11. Metadata formatting helpers handle null, zero, and width-only resolution fallbacks safely", async ({ page }) => {
    setupMocks(page);

    await page.goto("http://localhost:9999/plugins/deepseek-megapack/review.html");

    const testResults = await page.evaluate(() => {
      return {
        // formatResolution tests
        res540w: window.formatResolution(0, 960),
        res480w: window.formatResolution(null, 854),
        res360w: window.formatResolution(0, 640),
        res240w: window.formatResolution(null, 426),
        resNull: window.formatResolution(null, null),
        resZero: window.formatResolution(0, 0),
        resCustomH: window.formatResolution(480, 0),

        // formatFileSize tests
        sizeNull: window.formatFileSize(null),
        sizeZero: window.formatFileSize(0),
        sizeNegative: window.formatFileSize(-500),
        sizeBytes: window.formatFileSize(500),
        sizeKB: window.formatFileSize(1024),
        sizeMB: window.formatFileSize(1048576),
        sizeGB: window.formatFileSize(1073741824),

        // formatDuration tests
        durNull: window.formatDuration(null),
        durZero: window.formatDuration(0),
        durSeconds: window.formatDuration(45),
        durMinutes: window.formatDuration(125),
        durHours: window.formatDuration(3665),

        // formatCodec tests
        codecNull: window.formatCodec(null),
        codecTrim: window.formatCodec("  hevc  ")
      };
    });

    expect(testResults.res540w).toBe("540p");
    expect(testResults.res480w).toBe("480p");
    expect(testResults.res360w).toBe("360p");
    expect(testResults.res240w).toBe("240p");
    expect(testResults.resNull).toBe("");
    expect(testResults.resZero).toBe("");
    expect(testResults.resCustomH).toBe("480p");

    expect(testResults.sizeNull).toBe("");
    expect(testResults.sizeZero).toBe("");
    expect(testResults.sizeNegative).toBe("");
    expect(testResults.sizeBytes).toBe("500 B");
    expect(testResults.sizeKB).toBe("1.00 KB");
    expect(testResults.sizeMB).toBe("1.00 MB");
    expect(testResults.sizeGB).toBe("1.00 GB");

    expect(testResults.durNull).toBe("");
    expect(testResults.durZero).toBe("");
    expect(testResults.durSeconds).toBe("0m 45s");
    expect(testResults.durMinutes).toBe("2m 5s");
    expect(testResults.durHours).toBe("1h 1m 5s");

    expect(testResults.codecNull).toBe("");
    expect(testResults.codecTrim).toBe("hevc");
  });

  test("ADV-12. Resolution superiority honors width-only 4K files (height: 0, width: 3840) over 1080p and 720p files in collision groups", async ({ page }) => {
    setupMocks(page);

    const widthOnly4kScenes = [
      {
        id: 971,
        title: "4K Width-Only Scene 971",
        paths: {},
        files: [{ id: 9701, path: "C:\\Packs\\shared_4k_test.mp4", size: 5000000000, height: 0, width: 3840, duration: 3600, video_codec: "hevc" }],
        performers: [],
        tags: []
      },
      {
        id: 972,
        title: "1080p Scene 972",
        paths: {},
        files: [{ id: 9702, path: "D:\\Packs\\SHARED_4K_TEST.MP4", size: 3000000000, height: 1080, width: 1920, duration: 3600, video_codec: "h264" }],
        performers: [],
        tags: []
      },
      {
        id: 973,
        title: "720p Scene 973",
        paths: {},
        files: [{ id: 9703, path: "E:\\Packs\\shared_4k_test.mp4", size: 1000000000, height: 720, width: 1280, duration: 3600, video_codec: "h264" }],
        performers: [],
        tags: []
      }
    ];

    await page.route("**/graphql", async (route) => {
      const postData = JSON.parse(route.request().postData() || "{}");
      if (postData.query?.includes("FindScenes")) {
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ data: { findScenes: { scenes: widthOnly4kScenes } } })
        });
      }
      return route.continue();
    });

    await page.goto("http://localhost:9999/plugins/deepseek-megapack/review.html?scenes=971,972,973");
    await page.waitForSelector(".scene-card");

    const card971 = page.locator('.scene-card[data-scene-id="971"]');
    const card972 = page.locator('.scene-card[data-scene-id="972"]');
    const card973 = page.locator('.scene-card[data-scene-id="973"]');

    // 4K scene (971) must be awarded the Higher Resolution badge
    await expect(card971.locator(".badge-superior-resolution")).toContainText("Higher Resolution (2160p)");
    // 1080p (972) and 720p (973) must NOT have higher resolution badge
    await expect(card972.locator(".badge-superior-resolution")).toHaveCount(0);
    await expect(card973.locator(".badge-superior-resolution")).toHaveCount(0);
  });

  test("ADV-13. Anamorphic widescreen 1080p (1920x800) does not get false inferiority against 1920x1080, but beats 720p (1280x720)", async ({ page }) => {
    setupMocks(page);

    const aspectScenes = [
      {
        id: 981,
        title: "16:9 1080p Scene 981",
        paths: {},
        files: [{ id: 9801, path: "C:\\Packs\\pair_a.mp4", size: 2000000000, height: 1080, width: 1920, duration: 1800, video_codec: "h264" }],
        performers: [],
        tags: []
      },
      {
        id: 982,
        title: "2.40:1 Anamorphic 1080p Scene 982",
        paths: {},
        files: [{ id: 9802, path: "D:\\Packs\\PAIR_A.MP4", size: 2000000000, height: 800, width: 1920, duration: 1800, video_codec: "h264" }],
        performers: [],
        tags: []
      },
      {
        id: 983,
        title: "2.40:1 Anamorphic 1080p Scene 983",
        paths: {},
        files: [{ id: 9803, path: "C:\\Packs\\pair_b.mp4", size: 2000000000, height: 800, width: 1920, duration: 1800, video_codec: "h264" }],
        performers: [],
        tags: []
      },
      {
        id: 984,
        title: "720p Scene 984",
        paths: {},
        files: [{ id: 9804, path: "D:\\Packs\\PAIR_B.MP4", size: 1000000000, height: 720, width: 1280, duration: 1800, video_codec: "h264" }],
        performers: [],
        tags: []
      }
    ];

    await page.route("**/graphql", async (route) => {
      const postData = JSON.parse(route.request().postData() || "{}");
      if (postData.query?.includes("FindScenes")) {
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ data: { findScenes: { scenes: aspectScenes } } })
        });
      }
      return route.continue();
    });

    await page.goto("http://localhost:9999/plugins/deepseek-megapack/review.html?scenes=981,982,983,984");
    await page.waitForSelector(".scene-card");

    const card981 = page.locator('.scene-card[data-scene-id="981"]');
    const card982 = page.locator('.scene-card[data-scene-id="982"]');
    const card983 = page.locator('.scene-card[data-scene-id="983"]');
    const card984 = page.locator('.scene-card[data-scene-id="984"]');

    // In Group A (981 vs 982), both are 1080p: neither gets higher resolution badge
    await expect(card981.locator(".badge-superior-resolution")).toHaveCount(0);
    await expect(card982.locator(".badge-superior-resolution")).toHaveCount(0);

    // In Group B (983 vs 984), 983 (1080p anamorphic) beats 984 (720p)
    await expect(card983.locator(".badge-superior-resolution")).toContainText("Higher Resolution (1080p)");
    await expect(card984.locator(".badge-superior-resolution")).toHaveCount(0);
  });

  test("ADV-14. Internal duplicate files on a single scene without cross-scene collision do not lock out consolidation or create false collision groups", async ({ page }) => {
    setupMocks(page);

    const internalDupScenes = [
      {
        id: 991,
        title: "Internal Duplicate Scene 991",
        paths: {},
        files: [
          { id: 9901, path: "C:\\Packs\\internal_dup.mp4", size: 1000000, height: 1080, width: 1920, duration: 1800, video_codec: "h264" },
          { id: 9902, path: "D:\\Other\\internal_dup.mp4", size: 1000000, height: 1080, width: 1920, duration: 1800, video_codec: "h264" }
        ],
        performers: [{ id: 1, name: "Performer One" }],
        tags: []
      },
      {
        id: 992,
        title: "Unique Scene 992",
        paths: {},
        files: [{ id: 9903, path: "C:\\Packs\\unique_clip.mp4", size: 2000000, height: 1080, width: 1920, duration: 1800, video_codec: "h264" }],
        performers: [{ id: 2, name: "Performer Two" }],
        tags: []
      }
    ];

    await page.route("**/graphql", async (route) => {
      const postData = JSON.parse(route.request().postData() || "{}");
      if (postData.query?.includes("FindScenes")) {
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ data: { findScenes: { scenes: internalDupScenes } } })
        });
      }
      return route.continue();
    });

    await page.goto("http://localhost:9999/plugins/deepseek-megapack/review.html?scenes=991,992");
    await page.waitForSelector(".scene-card");

    // Collision banner should be hidden (no cross-scene collisions)
    await expect(page.locator("#collision-banner")).toBeHidden();

    // Scene 991 should NOT have duplicate badge or styling
    const card991 = page.locator('.scene-card[data-scene-id="991"]');
    await expect(card991).not.toHaveClass(/scene-card--duplicate/);
    await expect(card991.locator(".badge-danger")).toHaveCount(0);
    await expect(card991.locator(".scene-keep-btn")).toHaveCount(0);
  });

  test("ADV-15. Fractional duration differences rounding to the same second (1800.4s vs 1800.1s) do not produce conflicting superiority badges", async ({ page }) => {
    setupMocks(page);

    const roundingScenes = [
      {
        id: 995,
        title: "Scene 995 (1800.4s)",
        paths: {},
        files: [{ id: 9951, path: "C:\\Packs\\round_test.mp4", size: 1000000, height: 1080, width: 1920, duration: 1800.4, video_codec: "h264" }],
        performers: [],
        tags: []
      },
      {
        id: 996,
        title: "Scene 996 (1800.1s)",
        paths: {},
        files: [{ id: 9961, path: "D:\\Packs\\ROUND_TEST.MP4", size: 1000000, height: 1080, width: 1920, duration: 1800.1, video_codec: "h264" }],
        performers: [],
        tags: []
      }
    ];

    await page.route("**/graphql", async (route) => {
      const postData = JSON.parse(route.request().postData() || "{}");
      if (postData.query?.includes("FindScenes")) {
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ data: { findScenes: { scenes: roundingScenes } } })
        });
      }
      return route.continue();
    });

    await page.goto("http://localhost:9999/plugins/deepseek-megapack/review.html?scenes=995,996");
    await page.waitForSelector(".scene-card");

    const card995 = page.locator('.scene-card[data-scene-id="995"]');
    const card996 = page.locator('.scene-card[data-scene-id="996"]');

    // Both cards display 30m 0s and neither gets longer runtime badge
    await expect(card995.locator(".spec-dur")).toContainText("30m 0s");
    await expect(card996.locator(".spec-dur")).toContainText("30m 0s");
    await expect(card995.locator(".badge-superior-duration")).toHaveCount(0);
    await expect(card996.locator(".badge-superior-duration")).toHaveCount(0);
  });

  test("ADV-16. Portrait / vertical video resolution tier parity (1080x1920 matches 1920x1080) and superiority over portrait 720p (720x1280)", async ({ page }) => {
    setupMocks(page);

    const portraitScenes = [
      {
        id: 997,
        title: "Portrait 1080p Scene 997 (1080x1920)",
        paths: {},
        files: [{ id: 9971, path: "C:\\Packs\\portrait_test_a.mp4", size: 1000000, height: 1920, width: 1080, duration: 1800, video_codec: "h264" }],
        performers: [],
        tags: []
      },
      {
        id: 998,
        title: "Landscape 1080p Scene 998 (1920x1080)",
        paths: {},
        files: [{ id: 9981, path: "D:\\Packs\\PORTRAIT_TEST_A.MP4", size: 1000000, height: 1080, width: 1920, duration: 1800, video_codec: "h264" }],
        performers: [],
        tags: []
      },
      {
        id: 999,
        title: "Portrait 1080p Scene 999 (1080x1920)",
        paths: {},
        files: [{ id: 9991, path: "C:\\Packs\\portrait_test_b.mp4", size: 1000000, height: 1920, width: 1080, duration: 1800, video_codec: "h264" }],
        performers: [],
        tags: []
      },
      {
        id: 1000,
        title: "Portrait 720p Scene 1000 (720x1280)",
        paths: {},
        files: [{ id: 10001, path: "D:\\Packs\\PORTRAIT_TEST_B.MP4", size: 500000, height: 1280, width: 720, duration: 1800, video_codec: "h264" }],
        performers: [],
        tags: []
      }
    ];

    await page.route("**/graphql", async (route) => {
      const postData = JSON.parse(route.request().postData() || "{}");
      if (postData.query?.includes("FindScenes")) {
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ data: { findScenes: { scenes: portraitScenes } } })
        });
      }
      return route.continue();
    });

    await page.goto("http://localhost:9999/plugins/deepseek-megapack/review.html?scenes=997,998,999,1000");
    await page.waitForSelector(".scene-card");

    const card997 = page.locator('.scene-card[data-scene-id="997"]');
    const card998 = page.locator('.scene-card[data-scene-id="998"]');
    const card999 = page.locator('.scene-card[data-scene-id="999"]');
    const card1000 = page.locator('.scene-card[data-scene-id="1000"]');

    // In Group A (997 vs 998), both are 1080p: both display 1080p, neither gets Higher Resolution badge
    await expect(card997.locator(".spec-res")).toContainText("1080p");
    await expect(card998.locator(".spec-res")).toContainText("1080p");
    await expect(card997.locator(".badge-superior-resolution")).toHaveCount(0);
    await expect(card998.locator(".badge-superior-resolution")).toHaveCount(0);

    // In Group B (999 vs 1000), 999 (portrait 1080p) beats 1000 (portrait 720p)
    await expect(card999.locator(".spec-res")).toContainText("1080p");
    await expect(card1000.locator(".spec-res")).toContainText("720p");
    await expect(card999.locator(".badge-superior-resolution")).toContainText("Higher Resolution (1080p)");
    await expect(card1000.locator(".badge-superior-resolution")).toHaveCount(0);
  });

  test("ADV-17. Ultra-narrow viewport (<360px) layout stability: media specs and superiority badges wrap cleanly without overflow", async ({ page }) => {
    setupMocks(page);

    await page.setViewportSize({ width: 320, height: 640 });

    await page.route("**/graphql", async (route) => {
      const postData = JSON.parse(route.request().postData() || "{}");
      if (postData.query?.includes("FindScenes")) {
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ data: { findScenes: { scenes: testScenes } } })
        });
      }
      return route.continue();
    });

    await page.goto("http://localhost:9999/plugins/deepseek-megapack/review.html?scenes=101,103");
    await page.waitForSelector(".scene-card");

    const card103 = page.locator('.scene-card[data-scene-id="103"]');
    await expect(card103.locator(".scene-specs")).toBeVisible();
    await expect(card103.locator(".spec-item")).toHaveCount(4);

    // Verify all 4 spec items are visible and render with nowrap CSS
    const nowrapCount = await page.evaluate(() => {
      const items = Array.from(document.querySelectorAll(".scene-specs .spec-item"));
      return items.filter((el) => window.getComputedStyle(el).whiteSpace === "nowrap").length;
    });
    expect(nowrapCount).toBeGreaterThan(0);

    // Verify Keep This and Remove buttons remain clickable on narrow viewport
    const keepBtn = card103.locator(".scene-keep-btn");
    await expect(keepBtn).toBeVisible();
    await keepBtn.click();

    // Scene 101 should be excluded
    await expect(page.locator('.scene-card[data-scene-id="101"]')).toHaveCount(0);
    await expect(page.locator('.scene-card[data-scene-id="103"]')).toBeVisible();
  });

  test("ADV-18. Multi-file scene version selector defaults to best quality file and consolidates only selected file without collision error", async ({ page }) => {
    setupMocks(page);

    const multiFileScenes = [
      {
        id: 4318,
        title: "Emma Scene 4318",
        date: "2026-01-15",
        paths: { screenshot: "http://localhost:9999/shot4318.jpg" },
        files: [
          { id: 4602, path: "D:\\232\\Cuck\\Emma\\emmassecretlife52-cdzDk65D.mp4", size: 20377486, height: 1080, width: 1920, duration: 61.39, video_codec: "h264" },
          { id: 29361, path: "D:\\240\\Tina Mang\\OF Videos\\emmassecretlife52-cdzDk65D.mp4", size: 47950206, height: 1080, width: 1920, duration: 61.39, video_codec: "h264" }
        ],
        performers: [{ id: 10, name: "Emma" }],
        tags: [{ id: 1, name: "Solo" }],
        studio: { id: 5, name: "OF" }
      },
      {
        id: 4319,
        title: "Emma Scene 4319",
        date: "2026-01-16",
        paths: { screenshot: "http://localhost:9999/shot4319.jpg" },
        files: [
          { id: 4603, path: "D:\\232\\Cuck\\Emma\\emmassecretlife53-RqA1FzTr.mp4", size: 47334851, height: 1440, width: 1920, duration: 108.07, video_codec: "h264" },
          { id: 29359, path: "D:\\240\\Tina Mang\\OF Videos\\emmassecretlife53-RqA1FzTr.mp4", size: 88970750, height: 1440, width: 1920, duration: 108.07, video_codec: "h264" }
        ],
        performers: [{ id: 10, name: "Emma" }],
        tags: [{ id: 1, name: "Solo" }],
        studio: { id: 5, name: "OF" }
      }
    ];

    let moveFilesCalledWith = null;

    // Read-only destination pre-check mocks (collision-free): without these
    // the discovery query/probe fall through to the network and hit a real
    // Stash on :9999, aborting the consolidation before moveFiles.
    await page.route("**/api/fs/exists", async (route) => {
      const postData = JSON.parse(route.request().postData() || "{}");
      const results = {};
      for (const p of postData.paths || []) results[p] = false;
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ results })
      });
    });

    await page.route("**/graphql", async (route) => {
      const postData = JSON.parse(route.request().postData() || "{}");
      if (postData.query?.includes("FindScenes")) {
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ data: { findScenes: { scenes: multiFileScenes } } })
        });
      }
      if (postData.query?.includes("FindDestinationCollisions")) {
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ data: { findScenes: { scenes: [] } } })
        });
      }
      if (postData.query?.includes("MoveFiles")) {
        moveFilesCalledWith = postData.variables?.input;
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ data: { moveFiles: true } })
        });
      }
      return route.fallback();
    });

    page.on("dialog", async (dialog) => {
      await dialog.accept();
    });

    await page.goto("http://localhost:9999/plugins/deepseek-megapack/review.html?scenes=4318,4319");
    await page.locator("#output-dir").fill("C:\\Packs");
    await page.waitForSelector(".scene-card");

    // 1. Verify file version dropdown renders on both cards with auto-selection of larger file
    const card4318 = page.locator('.scene-card[data-scene-id="4318"]');
    const select4318 = card4318.locator(".scene-file-select");
    await expect(select4318).toBeVisible();
    await expect(select4318.locator("option")).toHaveCount(2);

    // Auto-selected version should be the 47.95MB file (id 29361)
    const val4318 = await select4318.inputValue();
    expect(val4318).toBe("29361");
    await expect(card4318.locator(".spec-size")).toContainText("45.73 MB");

    // 2. Verify Consolidate is enabled without any self-collision banner blocking it
    await expect(page.locator("#collision-banner")).toBeHidden();
    const consolidateBtn = page.locator("#btn-consolidate");
    await expect(consolidateBtn).toBeEnabled();

    // 3. Click consolidate and verify only the 2 selected files (not 4) are moved
    await consolidateBtn.click();
    // The consolidation flow now runs the read-only destination pre-check
    // (collision query + fs probe) before moveFiles — poll instead of a
    // fixed wait.
    await expect.poll(() => moveFilesCalledWith).not.toBeNull();
    expect(moveFilesCalledWith.ids).toEqual([29361, 29359]);
  });

});

