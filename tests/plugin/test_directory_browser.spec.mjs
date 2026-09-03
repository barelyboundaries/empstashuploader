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
}

test.describe("Empornium Megapack Builder - Server Filesystem Directory Browser", () => {

  test("1. 'Browse...' button is present adjacent to Destination Directory input", async ({ page }) => {
    setupMocks(page);

    await page.route("**/graphql", async (route) => {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ data: { findScenes: { scenes: [] } } })
      });
    });

    await page.goto("http://localhost:9999/plugins/empornium-megapack/review.html");

    const outputDirInput = page.locator("#output-dir");
    const browseBtn = page.locator("#btn-browse-dir");

    await expect(outputDirInput).toBeVisible();
    await expect(outputDirInput).toHaveValue("");
    await expect(browseBtn).toBeVisible();
    await expect(browseBtn).toContainText("Browse...");
  });

  test("2. Clicking 'Browse...' opens modal and loads directory listing via GraphQL", async ({ page }) => {
    setupMocks(page);

    let queriedPath = null;
    await page.route("**/graphql", async (route) => {
      const postData = JSON.parse(route.request().postData() || "{}");
      if (postData.query?.includes("Directory")) {
        queriedPath = postData.variables?.path;
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            data: {
              directory: {
                path: "C:\\Packs",
                parent: "C:\\",
                directories: ["C:\\Packs\\Action", "C:\\Packs\\Comedy", "C:\\Packs\\Drama"]
              }
            }
          })
        });
      }
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ data: { findScenes: { scenes: [] } } })
      });
    });

    await page.goto("http://localhost:9999/plugins/empornium-megapack/review.html");
    // Seed-dir field starts EMPTY (no machine-path default) — set it explicitly.
    await page.locator("#output-dir").fill("C:\\Packs");

    const modal = page.locator("#dir-browser-modal");
    await expect(modal).toBeHidden();

    // Click Browse...
    await page.locator("#btn-browse-dir").click();
    await expect(modal).toBeVisible();

    expect(queriedPath).toBe("C:\\Packs");

    // Verify current path input in modal toolbar
    const currentPathInput = page.locator("#dir-current-path");
    await expect(currentPathInput).toHaveValue("C:\\Packs");

    // Verify directory entries rendered
    const entries = page.locator(".dir-entry");
    await expect(entries).toHaveCount(3);
    await expect(entries.nth(0)).toContainText("Action");
    await expect(entries.nth(1)).toContainText("Comedy");
    await expect(entries.nth(2)).toContainText("Drama");

    // Selected display shows current path
    const selectedDisplay = page.locator("#dir-selected-display");
    await expect(selectedDisplay).toContainText("C:\\Packs");
  });

  test("3. Single-click selects directory, double-click navigates into subfolder", async ({ page }) => {
    setupMocks(page);

    const history = [];
    await page.route("**/graphql", async (route) => {
      const postData = JSON.parse(route.request().postData() || "{}");
      if (postData.query?.includes("Directory")) {
        const p = postData.variables?.path;
        history.push(p);
        if (p === "C:\\Packs\\Action") {
          return route.fulfill({
            status: 200,
            contentType: "application/json",
            body: JSON.stringify({
              data: {
                directory: {
                  path: "C:\\Packs\\Action",
                  parent: "C:\\Packs",
                  directories: ["C:\\Packs\\Action\\SubFolder1"]
                }
              }
            })
          });
        }
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            data: {
              directory: {
                path: "C:\\Packs",
                parent: "C:\\",
                directories: ["C:\\Packs\\Action", "C:\\Packs\\Comedy"]
              }
            }
          })
        });
      }
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ data: { findScenes: { scenes: [] } } })
      });
    });

    await page.goto("http://localhost:9999/plugins/empornium-megapack/review.html");
    await page.locator("#btn-browse-dir").click();

    const actionEntry = page.locator(".dir-entry").filter({ hasText: "Action" });
    await expect(actionEntry).toBeVisible();

    // Single click selects and updates display
    await actionEntry.click();
    await expect(actionEntry).toHaveClass(/selected/);
    await expect(page.locator("#dir-selected-display")).toHaveText("C:\\Packs\\Action");

    // Double click navigates into Action
    await actionEntry.dblclick();

    await expect(page.locator("#dir-current-path")).toHaveValue("C:\\Packs\\Action");
    const subEntries = page.locator(".dir-entry");
    await expect(subEntries).toHaveCount(1);
    await expect(subEntries.first()).toContainText("SubFolder1");
  });

  test("4. Navigation up to parent folder and drives button work correctly", async ({ page }) => {
    setupMocks(page);

    await page.route("**/graphql", async (route) => {
      const postData = JSON.parse(route.request().postData() || "{}");
      if (postData.query?.includes("Directory")) {
        const p = postData.variables?.path;
        if (p === "C:\\Packs\\Action") {
          return route.fulfill({
            status: 200,
            contentType: "application/json",
            body: JSON.stringify({
              data: {
                directory: {
                  path: "C:\\Packs\\Action",
                  parent: "C:\\Packs",
                  directories: []
                }
              }
            })
          });
        }
        if (p === "C:\\Packs") {
          return route.fulfill({
            status: 200,
            contentType: "application/json",
            body: JSON.stringify({
              data: {
                directory: {
                  path: "C:\\Packs",
                  parent: "C:\\",
                  directories: ["C:\\Packs\\Action"]
                }
              }
            })
          });
        }
        if (p === null || p === "") {
          return route.fulfill({
            status: 200,
            contentType: "application/json",
            body: JSON.stringify({
              data: {
                directory: {
                  path: "",
                  parent: null,
                  directories: ["C:\\", "D:\\"]
                }
              }
            })
          });
        }
      }
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ data: { findScenes: { scenes: [] } } })
      });
    });

    await page.goto("http://localhost:9999/plugins/empornium-megapack/review.html");
    await page.locator("#output-dir").fill("C:\\Packs\\Action");
    await page.locator("#btn-browse-dir").click();

    await expect(page.locator("#dir-current-path")).toHaveValue("C:\\Packs\\Action");

    // Click Up -> goes to C:\Packs
    await page.locator("#btn-dir-up").click();
    await expect(page.locator("#dir-current-path")).toHaveValue("C:\\Packs");

    // Click Drives -> goes to root drive list
    await page.locator("#btn-dir-roots").click();
    const driveEntries = page.locator(".dir-entry");
    await expect(driveEntries).toHaveCount(2);
    await expect(driveEntries.nth(0)).toContainText("C:\\");
    await expect(driveEntries.nth(1)).toContainText("D:\\");
  });

  test("5. Cancelling or closing modal does not alter Destination Directory input", async ({ page }) => {
    setupMocks(page);

    await page.route("**/graphql", async (route) => {
      const postData = JSON.parse(route.request().postData() || "{}");
      if (postData.query?.includes("Directory")) {
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            data: {
              directory: {
                path: "C:\\Packs",
                parent: "C:\\",
                directories: ["C:\\Packs\\DifferentFolder"]
              }
            }
          })
        });
      }
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ data: { findScenes: { scenes: [] } } })
      });
    });

    await page.goto("http://localhost:9999/plugins/empornium-megapack/review.html");
    await page.locator("#output-dir").fill("C:\\Packs");
    const outputDir = page.locator("#output-dir");
    await expect(outputDir).toHaveValue("C:\\Packs");

    // Open browser, select DifferentFolder, but cancel
    await page.locator("#btn-browse-dir").click();
    await page.locator(".dir-entry").filter({ hasText: "DifferentFolder" }).click();
    await page.locator("#btn-cancel-dir").click();

    // Verify modal is closed and input was NOT modified
    await expect(page.locator("#dir-browser-modal")).toBeHidden();
    await expect(outputDir).toHaveValue("C:\\Packs");

    // Open again, press Escape
    await page.locator("#btn-browse-dir").click();
    await expect(page.locator("#dir-browser-modal")).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(page.locator("#dir-browser-modal")).toBeHidden();
    await expect(outputDir).toHaveValue("C:\\Packs");
  });

  test("6. Confirming folder selection updates Destination Directory input with absolute path", async ({ page }) => {
    setupMocks(page);

    await page.route("**/graphql", async (route) => {
      const postData = JSON.parse(route.request().postData() || "{}");
      if (postData.query?.includes("Directory")) {
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            data: {
              directory: {
                path: "D:\\StashMedia\\Megapacks",
                parent: "D:\\StashMedia",
                directories: ["D:\\StashMedia\\Megapacks\\TargetPack"]
              }
            }
          })
        });
      }
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ data: { findScenes: { scenes: [] } } })
      });
    });

    await page.goto("http://localhost:9999/plugins/empornium-megapack/review.html");
    const outputDir = page.locator("#output-dir");
    await outputDir.fill("D:\\StashMedia\\Megapacks");

    await page.locator("#btn-browse-dir").click();
    await page.locator(".dir-entry").filter({ hasText: "TargetPack" }).click();
    await page.locator("#btn-select-dir").click();

    await expect(page.locator("#dir-browser-modal")).toBeHidden();
    await expect(outputDir).toHaveValue("D:\\StashMedia\\Megapacks\\TargetPack");
  });

  test("7. Selected directory path works seamlessly with 'Probe Filesystem' and 'Consolidate Files'", async ({ page }) => {
    setupMocks(page);

    page.on("dialog", async (dialog) => {
      await dialog.accept();
    });

    let probePayload = null;
    let moveFilesInput = null;

    // Read-only destination pre-check mock (collision-free): without the
    // probe route the consolidation aborts before moveFiles (the probe
    // endpoint is fail-closed and every real candidate is unreachable).
    await page.route("**/api/fs/exists", async (route) => {
      const postData = JSON.parse(route.request().postData() || "{}");
      const results = {};
      for (const p of postData.paths || []) results[p] = p.startsWith("E:\\TargetStorage") ? false : true;
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ results })
      });
    });

    await page.route("**/graphql", async (route) => {
      const postData = JSON.parse(route.request().postData() || "{}");
      const query = postData.query || "";

      if (query.includes("FindDestinationCollisions")) {
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ data: { findScenes: { scenes: [] } } })
        });
      }

      if (query.includes("Directory")) {
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            data: {
              directory: {
                path: "E:\\TargetStorage",
                parent: "E:\\",
                directories: ["E:\\TargetStorage\\MegapackFolder"]
              }
            }
          })
        });
      }

      if (query.includes("findScene") || query.includes("FindScenes")) {
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            data: {
              findScenes: {
                scenes: [
                  {
                    id: 101,
                    title: "Scene 101",
                    paths: {},
                    files: [{ id: 501, path: "C:/Source/scene1.mp4", size: 1000 }],
                    performers: [],
                    tags: []
                  },
                  {
                    id: 102,
                    title: "Scene 102",
                    paths: {},
                    files: [{ id: 502, path: "C:/Source/scene2.mp4", size: 1000 }],
                    performers: [],
                    tags: []
                  }
                ]
              }
            }
          })
        });
      }

      if (query.includes("RunProbe")) {
        const args = postData.variables?.args || [];
        const payloadArg = args.find((a) => a.key === "payload");
        probePayload = JSON.parse(payloadArg?.value?.str || "{}");
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ data: { runPluginTask: "job-probe-1" } })
        });
      }

      if (query.includes("MoveFiles")) {
        moveFilesInput = postData.variables?.input;
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ data: { moveFiles: true } })
        });
      }

      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ data: {} })
      });
    });

    await page.goto("http://localhost:9999/plugins/empornium-megapack/review.html?scenes=101,102&mode=megapack");
    // Pack title starts EMPTY — set it so the probe target keeps its
    // <seed>/<sanitized title> subfolder.
    await page.locator("#pack-title").fill("My Awesome Megapack");

    // 1. Browse and select E:\TargetStorage\MegapackFolder
    await page.locator("#output-dir").fill("E:\\TargetStorage");
    await page.locator("#btn-browse-dir").click();
    await page.locator(".dir-entry").filter({ hasText: "MegapackFolder" }).click();
    await page.locator("#btn-select-dir").click();

    await expect(page.locator("#output-dir")).toHaveValue("E:\\TargetStorage\\MegapackFolder");

    // 2. Click Probe Filesystem -> verify probePayload.target_dir
    await page.locator("#btn-probe").click();
    await expect.poll(() => probePayload).toBeTruthy();
    expect(probePayload.target_dir).toBe("E:\\TargetStorage\\MegapackFolder\\My Awesome Megapack");

    // 3. Click Consolidate Files -> verify moveFilesInput.destination_folder
    await page.locator("#btn-consolidate").click();
    await expect.poll(() => moveFilesInput).toBeTruthy();
    // Consolidation destination = the seed-dir field value (no pack-title
    // subfolder — in-place seeding). Probe target_dir above still appends the
    // pack subfolder until todo 8 re-points probeFiles.
    expect(moveFilesInput.destination_folder).toBe("E:\\TargetStorage\\MegapackFolder");
  });

  test("8. Error resilience: Handles server error gracefully with fallback button", async ({ page }) => {
    setupMocks(page);

    let queryCount = 0;
    await page.route("**/graphql", async (route) => {
      const postData = JSON.parse(route.request().postData() || "{}");
      if (postData.query?.includes("Directory")) {
        queryCount++;
        if (queryCount === 1) {
          return route.fulfill({
            status: 200,
            contentType: "application/json",
            body: JSON.stringify({
              errors: [{ message: "Path 'X:\\NonExistent' does not exist" }]
            })
          });
        }
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            data: {
              directory: {
                path: "",
                parent: null,
                directories: ["C:\\", "D:\\"]
              }
            }
          })
        });
      }
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ data: { findScenes: { scenes: [] } } })
      });
    });

    await page.goto("http://localhost:9999/plugins/empornium-megapack/review.html");
    await page.locator("#output-dir").fill("X:\\NonExistent");
    await page.locator("#btn-browse-dir").click();

    // Verify error status and fallback button
    await expect(page.locator("#dir-browser-status")).toBeVisible();
    await expect(page.locator("#dir-browser-status")).toContainText("Path 'X:\\NonExistent' does not exist");

    const fallbackBtn = page.locator("#btn-dir-fallback-root");
    await expect(fallbackBtn).toBeVisible();

    // Click fallback button -> loads drive roots
    await fallbackBtn.click();
    const driveEntries = page.locator(".dir-entry");
    await expect(driveEntries).toHaveCount(2);
    await expect(driveEntries.first()).toContainText("C:\\");
  });

  test("9. Keyboard Accessibility: Space selects row, Enter navigates into folder, Escape dismisses", async ({ page }) => {
    setupMocks(page);

    const queriedPaths = [];
    await page.route("**/graphql", async (route) => {
      const postData = JSON.parse(route.request().postData() || "{}");
      if (postData.query?.includes("Directory")) {
        const p = postData.variables?.path;
        queriedPaths.push(p);
        if (p === "C:\\Packs\\Action") {
          return route.fulfill({
            status: 200,
            contentType: "application/json",
            body: JSON.stringify({
              data: {
                directory: {
                  path: "C:\\Packs\\Action",
                  parent: "C:\\Packs",
                  directories: ["C:\\Packs\\Action\\Sub1"]
                }
              }
            })
          });
        }
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            data: {
              directory: {
                path: "C:\\Packs",
                parent: "C:\\",
                directories: ["C:\\Packs\\Action", "C:\\Packs\\Comedy"]
              }
            }
          })
        });
      }
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ data: { findScenes: { scenes: [] } } })
      });
    });

    await page.goto("http://localhost:9999/plugins/empornium-megapack/review.html");
    await page.locator("#btn-browse-dir").click();
    await expect(page.locator("#dir-browser-modal")).toBeVisible();

    const comedyEntry = page.locator(".dir-entry").filter({ hasText: "Comedy" });
    await comedyEntry.press("Space");
    await expect(comedyEntry).toHaveClass(/selected/);
    await expect(page.locator("#dir-selected-display")).toHaveText("C:\\Packs\\Comedy");

    const actionEntry = page.locator(".dir-entry").filter({ hasText: "Action" });
    await actionEntry.press("Enter");

    await expect(page.locator("#dir-current-path")).toHaveValue("C:\\Packs\\Action");
    const subEntries = page.locator(".dir-entry");
    await expect(subEntries).toHaveCount(1);
    await expect(subEntries.first()).toContainText("Sub1");
  });

  test("10. Special characters, brackets, and ampersands in folder names are safely escaped and selectable", async ({ page }) => {
    setupMocks(page);

    const specialName = 'C:\\Packs\\Special & <Tags> [1080p] "Best"';
    await page.route("**/graphql", async (route) => {
      const postData = JSON.parse(route.request().postData() || "{}");
      if (postData.query?.includes("Directory")) {
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            data: {
              directory: {
                path: "C:\\Packs",
                parent: "C:\\",
                directories: [specialName]
              }
            }
          })
        });
      }
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ data: { findScenes: { scenes: [] } } })
      });
    });

    await page.goto("http://localhost:9999/plugins/empornium-megapack/review.html");
    await page.locator("#btn-browse-dir").click();

    const specialEntry = page.locator(".dir-entry").first();
    await expect(specialEntry).toBeVisible();
    await expect(specialEntry).toContainText('Special & <Tags> [1080p] "Best"');

    await specialEntry.click();
    await expect(page.locator("#dir-selected-display")).toHaveText(specialName);

    await page.locator("#btn-select-dir").click();
    await expect(page.locator("#dir-browser-modal")).toBeHidden();
    await expect(page.locator("#output-dir")).toHaveValue(specialName);
  });

  test("11. Empty directory states display clean placeholder and allow selecting current empty folder", async ({ page }) => {
    setupMocks(page);

    await page.route("**/graphql", async (route) => {
      const postData = JSON.parse(route.request().postData() || "{}");
      if (postData.query?.includes("Directory")) {
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            data: {
              directory: {
                path: "D:\\EmptyStashPack",
                parent: "D:\\",
                directories: []
              }
            }
          })
        });
      }
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ data: { findScenes: { scenes: [] } } })
      });
    });

    await page.goto("http://localhost:9999/plugins/empornium-megapack/review.html");
    await page.locator("#output-dir").fill("D:\\EmptyStashPack");
    await page.locator("#btn-browse-dir").click();

    await expect(page.locator("#dir-browser-list")).toContainText("Empty directory or no subdirectories found");
    await expect(page.locator("#dir-selected-display")).toHaveText("D:\\EmptyStashPack");

    await page.locator("#btn-select-dir").click();
    await expect(page.locator("#dir-browser-modal")).toBeHidden();
    await expect(page.locator("#output-dir")).toHaveValue("D:\\EmptyStashPack");
  });

});
