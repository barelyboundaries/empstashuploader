import { test, expect } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

function serveAssets(page) {
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

function setupGraphQLMocks(page) {
  serveAssets(page);

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
                  title: "Presentation Test Scene",
                  date: "2026-08-01",
                  files: [{ id: 101, path: "C:/Packs/scene.mp4", size: 1048576, height: 2160, width: 3840, duration: 1057, video_codec: "h264" }],
                  performers: [{ id: "p1", name: "Alice" }],
                  tags: [{ id: "t1", name: "Test" }]
                }
              ]
            }
          }
        })
      });
    }

    return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ data: {} }) });
  });
}

test.describe("Presentation size indicator and preflight checks", () => {
  test("renders presentation_size preflight check and shows error styling when total exceeds 25 MiB cap", async ({ page }) => {
    setupGraphQLMocks(page);

    await page.goto("http://localhost:9999/plugins/empornium-megapack/review.html?scenes=10&mode=megapack");
    await expect(page.locator("#loading-state")).toBeHidden({ timeout: 5000 });

    const overSizeBytes = 26.5 * 1024 * 1024; // 26.5 MiB
    await page.evaluate((bytes) => {
      window.onTaskComplete("BuildMegapack", {
        pack_title: "Over Budget Megapack",
        output_dir: "C:\\Packs",
        upload_previews: true,
        preview_only: false,
        ready: false,
        presentation_bytes: bytes,
        preflight: {
          ready: false,
          checks: [
            { id: "images_remote", label: "Preview Images", passed: true, detail: "All remote" },
            { id: "tracker_tags", label: "Tracker Tags", passed: true, detail: "Tags valid" },
            { id: "category", label: "Category", passed: true, is_info: true, detail: "Category selected" },
            { id: "torrent_valid", label: "Torrent File (torf)", passed: true, detail: "Valid torrent" },
            { id: "payload_files", label: "Media Files Verification", passed: true, detail: "Files exist" },
            { id: "root_name", label: "Torrent Root Name", passed: true, is_warning: false, is_info: false, detail: "Matches title" },
            {
              id: "presentation_size",
              label: "Presentation Size",
              passed: false,
              detail: "26.50 MiB of 21.93 MiB budget (Empornium cap 25.00 MiB)"
            }
          ]
        }
      });
    }, overSizeBytes);

    const summaryBox = page.locator("#artifact-summary");
    await expect(summaryBox).toBeVisible({ timeout: 5000 });

    // Verify presentation size check failed row in checklist
    const checkRow = page.locator("#check-presentation_size");
    await expect(checkRow).toBeVisible();
    await expect(checkRow).toContainText("❌");
    await expect(checkRow).toContainText("Presentation Size");
    await expect(checkRow).toContainText("26.50 MiB");

    // Verify presentation-size-line element under BBCode preview
    const sizeLine = page.locator("#presentation-size-line");
    await expect(sizeLine).toBeVisible();
    await expect(sizeLine).toContainText("Presentation Size: 26.5 MiB / 25 MiB");
    await expect(sizeLine).toHaveClass(/badge-danger/);
  });

  test("renders presentation size indicator with normal styling when under cap", async ({ page }) => {
    setupGraphQLMocks(page);

    await page.goto("http://localhost:9999/plugins/empornium-megapack/review.html?scenes=10&mode=megapack");
    await expect(page.locator("#loading-state")).toBeHidden({ timeout: 5000 });

    const underSizeBytes = 2.1 * 1024 * 1024; // 2.1 MiB
    await page.evaluate((bytes) => {
      window.onTaskComplete("BuildMegapack", {
        pack_title: "Within Budget Megapack",
        output_dir: "C:\\Packs",
        upload_previews: true,
        preview_only: false,
        ready: true,
        presentation_bytes: bytes,
        preflight: {
          ready: true,
          checks: [
            { id: "images_remote", label: "Preview Images", passed: true, detail: "All remote" },
            { id: "tracker_tags", label: "Tracker Tags", passed: true, detail: "Tags valid" },
            { id: "category", label: "Category", passed: true, is_info: true, detail: "Category selected" },
            { id: "torrent_valid", label: "Torrent File (torf)", passed: true, detail: "Valid torrent" },
            { id: "payload_files", label: "Media Files Verification", passed: true, detail: "Files exist" },
            { id: "root_name", label: "Torrent Root Name", passed: true, is_warning: false, is_info: false, detail: "Matches title" },
            {
              id: "presentation_size",
              label: "Presentation Size",
              passed: true,
              detail: "2.10 MiB of 21.93 MiB budget (Empornium cap 25.00 MiB)"
            }
          ]
        }
      });
    }, underSizeBytes);

    const summaryBox = page.locator("#artifact-summary");
    await expect(summaryBox).toBeVisible({ timeout: 5000 });

    // Verify presentation size check passed row in checklist
    const checkRow = page.locator("#check-presentation_size");
    await expect(checkRow).toBeVisible();
    await expect(checkRow).toContainText("✅");
    await expect(checkRow).toContainText("Presentation Size");
    await expect(checkRow).toContainText("2.10 MiB");

    // Verify presentation-size-line element under BBCode preview
    const sizeLine = page.locator("#presentation-size-line");
    await expect(sizeLine).toBeVisible();
    await expect(sizeLine).toContainText("Presentation Size: 2.1 MiB / 25 MiB");
    await expect(sizeLine).not.toHaveClass(/badge-danger/);
  });
});