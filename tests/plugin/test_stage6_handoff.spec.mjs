import { test, expect } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

test.describe("Stage 6 — Handoff Quality & Manual Upload Preparation", () => {
  function setupGraphQLMocks(page, isPreviewOnly = true) {
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

    // Build pre-flight (todo 7 of staged-wizard-inplace-seed): the
    // authoritative on-disk probe must succeed or the build is blocked
    // fail-closed before dispatch.
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


    page.route("**/api/run/*", async (route) => {
      if (route.request().method() === "GET") {
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            found: true,
            result: {
              status: "success",
              pack_title: isPreviewOnly ? "Preview Only Megapack" : "Ready Megapack",
              torrent_path: isPreviewOnly ? "C:\\Packs\\Preview Only Megapack.torrent" : "C:\\Packs\\Ready Megapack.torrent",
              manifest_path: isPreviewOnly ? "C:\\Packs\\Preview Only Megapack_manifest.json" : "C:\\Packs\\Ready Megapack_manifest.json",
              submission_path: isPreviewOnly ? "C:\\Packs\\Preview Only Megapack_submission.json" : "C:\\Packs\\Ready Megapack_submission.json",
              bbcode_path: isPreviewOnly ? "C:\\Packs\\Preview Only Megapack_bbcode.txt" : "C:\\Packs\\Ready Megapack_bbcode.txt",
              upload_previews: !isPreviewOnly,
              preview_only: isPreviewOnly,
              ready: !isPreviewOnly,
              tracker_tags: ["1080p", "feature", "star.performer"],
              uploaded_urls: isPreviewOnly ? [] : ["https://hamsterimg.net/images/preview.jpg"],
              preflight: {
                ready: !isPreviewOnly,
                checks: isPreviewOnly
                  ? [
                      { id: "images_remote", label: "Preview Images", passed: false, detail: "Contains local file:/// preview" },
                      { id: "tracker_tags", label: "Tracker Tags", passed: true, detail: "3 valid tags" },
                      { id: "category", label: "Category", passed: true, is_info: true, detail: "Category — you select this on the upload form." },
                      { id: "torrent_valid", label: "Torrent File", passed: true, detail: "Valid torrent" },
                      { id: "payload_files", label: "Media Files Verification", passed: true, detail: "All files exist on disk" },
                      { id: "root_name", label: "Torrent Root Name", passed: true, detail: "Root folder matches pack title" }
                    ]
                  : [
                      { id: "images_remote", label: "Preview Images", passed: true, detail: "All remote on HamsterImg" },
                      { id: "tracker_tags", label: "Tracker Tags", passed: true, detail: "Tracker Tags valid" },
                      { id: "category", label: "Category", passed: true, is_info: true, detail: "Category — you select this on the upload form." },
                      { id: "torrent_valid", label: "Torrent File", passed: true, detail: "Valid torrent" },
                      { id: "payload_files", label: "Media Files Verification", passed: true, detail: "All files exist on disk" },
                      { id: "root_name", label: "Torrent Root Name", passed: true, detail: "Root folder matches pack title" }
                    ]
              }
            }
          })
        });
      }
      return route.fallback();
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
                    title: "Test Scene 1",
                    date: "2026-03-01",
                    files: [{ id: 101, path: "C:/Packs/Preview Only Megapack/s1.mp4", size: 1024 * 1024, height: 1080, width: 1920, duration: 1800, video_codec: "h264" }],
                    performers: [{ id: "p1", name: "Star Performer" }],
                    tags: [{ id: "t1", name: "Feature" }, { id: "t2", name: "1080p" }]
                  }
                ]
              }
            }
          })
        });
      }

      if (query.includes("RunBuild") || query.includes("runPluginTask")) {
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            data: { runPluginTask: "job-stage6-123" }
          })
        });
      }

      if (query.includes("FindJob") || query.includes("findJob")) {
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            data: {
              findJob: {
                id: "job-stage6-123",
                status: "FINISHED",
                progress: 1.0,
                error: null
              }
            }
          })
        });
      }

      return route.continue();
    });
  }

  test("R3 & R1: When preview_only is True (upload disabled), copy affordances are HARD-BLOCKED with clear explanation", async ({ page }) => {
    // Grant clipboard permissions
    await page.context().grantPermissions(["clipboard-read", "clipboard-write"]);
    setupGraphQLMocks(page, true);

    await page.goto("http://localhost:9999/plugins/empornium-megapack/review.html?scenes=10&mode=megapack");
    await page.locator("#output-dir").fill("C:\\Packs");
    await expect(page.locator("#loading-state")).toBeHidden({ timeout: 5000 });

    // Set pack title
    await page.locator("#pack-title").fill("Preview Only Megapack");

    // Click Build Megapack
    await page.locator("#btn-build").click();

    const summaryBox = page.locator("#artifact-summary");
    await expect(summaryBox).toBeVisible({ timeout: 5000 });

    // 1. Verify gate alert is visible with explanation and remedy
    const gateAlert = page.locator("#preview-gate-alert");
    await expect(gateAlert).toBeVisible();
    await expect(gateAlert).toContainText("Pack Not Ready for Upload");
    await expect(gateAlert).toContainText("local file:/// URLs");
    await expect(gateAlert).toContainText("Remedy: Enable preview upload");

    // 2. Verify static Category reminder line (R2 / 6b)
    const catReminder = page.locator("#category-reminder");
    await expect(catReminder).toBeVisible();
    await expect(catReminder).toContainText("Category — you select this on the upload form.");

    // 3. Verify that Description BBCode, Title, and Tags copy buttons are UN-GATED (plain text copies remain enabled)
    const btnCopyBbcode = page.locator("#btn-copy-bbcode");
    const btnCopyTitle = page.locator("#btn-copy-title");
    const btnCopyTags = page.locator("#btn-copy-tags");
    const btnCopyTorrent = page.locator("#btn-copy-torrent-path");

    await expect(btnCopyBbcode).toBeEnabled();
    await expect(btnCopyTitle).toBeEnabled();
    await expect(btnCopyTags).toBeEnabled();
    await expect(btnCopyTorrent).toBeEnabled();

    // 4. Verify rendered handoff details (R1 / 6a)
    await expect(page.locator("#handoff-title")).toContainText("Preview Only Megapack");
    // Sorted, dot-normalized, and now including performers/studio -- the
    // fallback mirrors the backend's merge_tags instead of scene tags alone.
    await expect(page.locator("#handoff-tags")).toContainText("1080p feature star.performer");
    await expect(page.locator("#handoff-torrent")).toContainText("C:\\Packs\\Preview Only Megapack.torrent");
    await expect(page.locator("#handoff-images")).toContainText("local file:/// preview");

    // 5. When site_url is empty, upload button is ABSENT from DOM (Stage 6 cleanup)
    await expect(page.locator("#btn-open-upload")).toHaveCount(0);
  });

  test("R1, R2, R3, R5: When preview_only is False and site_url is configured, copy affordances and upload link are ENABLED and functional", async ({ page }) => {
    // Grant clipboard permissions
    await page.context().grantPermissions(["clipboard-read", "clipboard-write"]);
    setupGraphQLMocks(page, false);

    await page.goto("http://localhost:9999/plugins/empornium-megapack/review.html?scenes=10&mode=megapack");
    await expect(page.locator("#loading-state")).toBeHidden({ timeout: 5000 });

    // Set pack title
    await page.locator("#pack-title").fill("Ready Megapack");

    // Check upload previews checkbox
    const uploadCheckbox = page.locator("#opt-upload-previews");
    await uploadCheckbox.check();
    await expect(uploadCheckbox).toBeChecked();

    // Trigger onTaskComplete with configured site_url
    await page.evaluate(() => {
      window.onTaskComplete("BuildMegapack", {
        pack_title: "Ready Megapack",
        output_dir: "C:\\Packs",
        torrent_path: "C:\\Packs\\Ready Megapack.torrent",
        manifest_path: "C:\\Packs\\Ready Megapack_manifest.json",
        submission_path: "C:\\Packs\\Ready Megapack_submission.json",
        bbcode_path: "C:\\Packs\\Ready Megapack_bbcode.txt",
        upload_previews: true,
        preview_only: false,
        ready: true,
        site_url: "https://www.empornium.sx",
        tracker_tags: ["feature", "1080p", "star.performer"],
        preflight: {
          ready: true,
          checks: [
            { id: "images_remote", label: "Preview Images", passed: true, detail: "All remote on HamsterImg" },
            { id: "tracker_tags", label: "Tracker Tags", passed: true, detail: "Tracker Tags valid" },
            { id: "category", label: "Category", passed: true, is_info: true, detail: "Category — you select this on the upload form." },
            { id: "torrent_valid", label: "Torrent File", passed: true, detail: "Valid torrent" },
            { id: "payload_files", label: "Media Files Verification", passed: true, detail: "All files exist on disk" },
            { id: "root_name", label: "Torrent Root Name", passed: true, detail: "Root folder matches pack title" }
          ]
        }
      });
    });

    const summaryBox = page.locator("#artifact-summary");
    await expect(summaryBox).toBeVisible({ timeout: 5000 });

    // 1. Verify gate alert is hidden and status header indicates ready
    const gateAlert = page.locator("#preview-gate-alert");
    await expect(gateAlert).toBeHidden();
    await expect(page.locator("#handoff-status-header")).toContainText("Ready for Manual Upload");

    // 2. Verify static Category reminder line (R2 / 6b)
    const catReminder = page.locator("#category-reminder");
    await expect(catReminder).toBeVisible();
    await expect(catReminder).toContainText("Category — you select this on the upload form.");

    // 3. Verify that ALL copy buttons are ENABLED
    const btnCopyBbcode = page.locator("#btn-copy-bbcode");
    const btnCopyTitle = page.locator("#btn-copy-title");
    const btnCopyTags = page.locator("#btn-copy-tags");
    const btnCopyTorrent = page.locator("#btn-copy-torrent-path");

    await expect(btnCopyBbcode).toBeEnabled();
    await expect(btnCopyTitle).toBeEnabled();
    await expect(btnCopyTags).toBeEnabled();
    await expect(btnCopyTorrent).toBeEnabled();

    // 4. Test clicking each copy button and observing feedback
    await btnCopyTitle.click();
    await expect(btnCopyTitle).toContainText("✅ Copied!");

    await btnCopyTags.click();
    await expect(btnCopyTags).toContainText("✅ Copied!");

    await btnCopyTorrent.click();
    await expect(btnCopyTorrent).toContainText("✅ Copied!");

    await btnCopyBbcode.click();
    await expect(btnCopyBbcode).toContainText("✅ Copied!");

    // 5. Verify image summary indicates remote upload
    await expect(page.locator("#handoff-images")).toContainText("all remote on HamsterImg");

    // 6. Verify Pre-Flight Checklist rendered (R4 / 6d)
    const preflightList = page.locator("#preflight-checklist");
    await expect(preflightList).toBeVisible();
    await expect(page.locator("#check-images_remote")).toContainText("Preview Images");
    await expect(page.locator("#check-tracker_tags")).toContainText("Tracker Tags");
    await expect(page.locator("#check-category")).toContainText("Category");
    await expect(page.locator("#check-torrent_valid")).toContainText("Torrent File");
    await expect(page.locator("#check-payload_files")).toContainText("Media Files Verification");
    await expect(page.locator("#check-root_name")).toContainText("Torrent Root Name");

    // 7. Verify Open Empornium Upload Link (R5 / 6e) configured correctly
    const btnUpload = page.locator("#btn-open-upload");
    await expect(btnUpload).toBeVisible();
    await expect(btnUpload).toHaveAttribute("href", "https://www.empornium.sx/upload.php");
    await expect(btnUpload).toHaveAttribute("target", "_blank");
    await expect(btnUpload).toHaveAttribute("rel", "noopener noreferrer");
    await btnUpload.click();
  });

  test("R4 & R5: Specific pre-flight failures disable upload link and itemize failures actionable", async ({ page }) => {
    setupGraphQLMocks(page, true);

    await page.goto("http://localhost:9999/plugins/empornium-megapack/review.html?scenes=10&mode=megapack");
    await expect(page.locator("#loading-state")).toBeHidden({ timeout: 5000 });

    // Set pack title
    await page.locator("#pack-title").fill("Failed Preflight Megapack");

    // Trigger onTaskComplete with custom failed preflight checklist and configured site_url
    await page.evaluate(() => {
      window.onTaskComplete("BuildMegapack", {
        pack_title: "Failed Preflight Megapack",
        output_dir: "C:\\Packs",
        upload_previews: false,
        preview_only: true,
        ready: false,
        site_url: "https://www.empornium.sx",
        preflight: {
          ready: false,
          checks: [
            { id: "images_remote", label: "Preview Images", passed: false, detail: "Contains local file:/// URLs" },
            { id: "tracker_tags", label: "Tracker Tags", passed: true, detail: "3 valid tags" },
            { id: "category", label: "Category", passed: true, is_info: true, detail: "User selects on upload form" },
            { id: "torrent_valid", label: "Torrent File (torf)", passed: false, detail: "private flag is False" },
            { id: "payload_files", label: "Media Files Verification", passed: false, detail: "Missing 1 file(s) on disk" },
            { id: "root_name", label: "Torrent Root Name", passed: true, is_warning: true, detail: "Root folder differs from pack title" }
          ]
        }
      });
    });

    const summaryBox = page.locator("#artifact-summary");
    await expect(summaryBox).toBeVisible({ timeout: 5000 });

    // Verify itemized failures
    await expect(page.locator("#check-images_remote")).toContainText("❌");
    await expect(page.locator("#check-torrent_valid")).toContainText("❌");
    await expect(page.locator("#check-torrent_valid")).toContainText("private flag is False");
    await expect(page.locator("#check-payload_files")).toContainText("❌");
    await expect(page.locator("#check-payload_files")).toContainText("Missing 1 file(s)");
    await expect(page.locator("#check-root_name")).toContainText("⚠️");

    // Verify upload button is present but disabled / styled inert
    const btnUpload = page.locator("#btn-open-upload");
    await expect(btnUpload).toBeVisible();
    await expect(btnUpload).toHaveCSS("pointer-events", "none");
    await expect(btnUpload).toHaveCSS("opacity", "0.45");

    // Plain text copy buttons remain enabled even on preflight failure
    await expect(page.locator("#btn-copy-bbcode")).toBeEnabled();
    await expect(page.locator("#btn-copy-title")).toBeEnabled();
    await expect(page.locator("#btn-copy-tags")).toBeEnabled();
  });
});
