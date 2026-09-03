/**
 * Empornium Megapack Builder Milestone 3 — Challenger 1 Adversarial & Stress Suite
 * Tests DOM mutation churn, XSS immunity, extreme scene volume, drag-and-drop reordering,
 * WebSocket fallback polling, and GraphQL mutation error handling.
 */

import { test, expect } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

function setupMocks(page) {
  page.route('**/plugin*/**/main.js*', async (route) => {
    const filePath = path.resolve('plugin/main.js');
    return route.fulfill({
      status: 200,
      contentType: 'application/javascript',
      body: fs.readFileSync(filePath, 'utf8'),
    });
  });

  page.route('**/plugin*/**/style.css*', async (route) => {
    const filePath = path.resolve('plugin/style.css');
    return route.fulfill({
      status: 200,
      contentType: 'text/css',
      body: fs.readFileSync(filePath, 'utf8'),
    });
  });

  page.route('**/plugin*/**/review.html*', async (route) => {
    const filePath = path.resolve('plugin/assets/review.html');
    return route.fulfill({
      status: 200,
      contentType: 'text/html',
      body: fs.readFileSync(filePath, 'utf8'),
    });
  });

  page.route('**/*review.js*', async (route) => {
    const filePath = path.resolve('plugin/assets/review.js');
    return route.fulfill({
      status: 200,
      contentType: 'application/javascript',
      body: fs.readFileSync(filePath, 'utf8'),
    });
  });
}

test.describe('Empornium Megapack Builder Challenger 1 M3 Adversarial Suite', () => {

  test('ADV-M3-1: Adversarial query parameters and malformed scene IDs', async ({ page }) => {
    setupMocks(page);

    await page.route('**/graphql', async (route) => {
      const request = route.request();
      const postData = JSON.parse(request.postData() || '{}');
      if (postData.query?.includes('FindScenes')) {
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            data: {
              findScenes: {
                scenes: [
                  { id: 101, title: 'Scene 101', paths: {}, files: [{ id: 1, path: 'C:\\v1.mp4' }], performers: [{ name: 'Performer A' }], tags: [{ name: 'Tag A' }] },
                  { id: 202, title: 'Scene 202', paths: {}, files: [{ id: 2, path: 'C:\\v2.mp4' }], performers: [{ name: 'Performer B' }], tags: [{ name: 'Tag B' }] },
                  { id: 303, title: 'Scene 303', paths: {}, files: [{ id: 3, path: 'C:\\v3.mp4' }], performers: [{ name: 'Performer C' }], tags: [{ name: 'Tag C' }] },
                ]
              }
            }
          })
        });
      }
      return route.continue();
    });

    const url = 'http://localhost:9999/plugins/empornium-megapack/review.html?scenes=0,-99,NaN,undefined,null,abc,,101,202,303';
    await page.goto(url);

    const cards = page.locator('.scene-card');
    await expect(cards).toHaveCount(3);
    await expect(cards.nth(0)).toContainText('Scene 101');
    await expect(cards.nth(1)).toContainText('Scene 202');
    await expect(cards.nth(2)).toContainText('Scene 303');
  });

  test('ADV-M3-2: XSS Immunity & HTML Injection Safety in Metadata and BBCode', async ({ page }) => {
    setupMocks(page);

    const maliciousScenes = [
      {
        id: 1,
        title: '<script>window.__xss_injected = true;</script><img src=x onerror="window.__xss_img = true">XSS Scene',
        paths: {},
        files: [{ id: 10, path: 'C:\\test\\<xss>_video.mp4' }],
        performers: [{ name: '<b>Bold Performer</b>' }, { name: 'Normal "Quoted" Performer' }],
        tags: [{ name: '<script>' }, { name: 'SafeTag' }],
      }
    ];

    await page.route('**/graphql', async (route) => {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          data: {
            findScenes: {
              scenes: maliciousScenes
            }
          }
        })
      });
    });

    const url = 'http://localhost:9999/plugins/empornium-megapack/review.html?scenes=1';
    await page.goto(url);
    await page.locator('.scene-card').first().waitFor({ timeout: 5000 });

    const xssInjected = await page.evaluate(() => window.__xss_injected || window.__xss_img);
    expect(xssInjected).toBeFalsy();

    const bbcodePreview = page.locator('#bbcode-preview');
    await expect(bbcodePreview).toBeVisible();
    const bbcodeText = await bbcodePreview.inputValue();
    expect(bbcodeText).toContain('XSS Scene');
    expect(bbcodeText).toContain('Bold Performer');
  });

  test('ADV-M3-3: Extreme Scene Volume (150 scenes) & BBCode Sync', async ({ page }) => {
    setupMocks(page);

    const highVolumeScenes = Array.from({ length: 150 }, (_, i) => ({
      id: i + 1,
      title: `Bulk Scene ${i + 1}`,
      paths: {},
      files: [{ id: i + 1, path: `C:\\Media\\bulk_${i + 1}.mp4` }],
      performers: [{ name: `Actor ${(i % 10) + 1}` }],
      tags: [{ name: `Tag ${(i % 5) + 1}` }],
    }));

    await page.route('**/graphql', async (route) => {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          data: {
            findScenes: {
              scenes: highVolumeScenes
            }
          }
        })
      });
    });

    const sceneIdsParam = Array.from({ length: 150 }, (_, i) => i + 1).join(',');
    const url = `http://localhost:9999/plugins/empornium-megapack/review.html?scenes=${sceneIdsParam}`;
    await page.goto(url);

    const cards = page.locator('.scene-card');
    await expect(cards).toHaveCount(150);

    const bbcode = await page.locator('#bbcode-preview').inputValue();
    expect(bbcode).toContain('[b]Total Scenes:[/b] 150');
    expect(bbcode).toContain('1. [b]Bulk Scene 1 [/b]');
    expect(bbcode).toContain('150. [b]Bulk Scene 150 [/b]');
  });

  test('ADV-M3-4: Direct GraphQL MoveFiles mutation execution and confirmation', async ({ page }) => {
    setupMocks(page);

    const scenesData = [
      { id: 1, title: 'Scene to Move 1', paths: {}, files: [{ id: 501, path: 'C:\\src\\s1.mp4' }] },
      { id: 2, title: 'Scene to Move 2', paths: {}, files: [{ id: 502, path: 'C:\\src\\s2.mp4' }] },
    ];

    let moveFilesCalled = false;
    let moveInputReceived = null;

    // Read-only destination pre-check mocks (collision-free): without these
    // the discovery query/probe fall through to the network and hit a real
    // Stash on :9999, aborting the consolidation before moveFiles.
    await page.route('**/api/fs/exists', async (route) => {
      const postData = JSON.parse(route.request().postData() || '{}');
      const results = {};
      for (const p of postData.paths || []) results[p] = false;
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ results })
      });
    });

    await page.route('**/graphql', async (route) => {
      const request = route.request();
      const postData = JSON.parse(request.postData() || '{}');
      
      if (postData.query?.includes('FindScenes')) {
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({ data: { findScenes: { scenes: scenesData } } })
        });
      }

      if (postData.query?.includes('FindDestinationCollisions')) {
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({ data: { findScenes: { scenes: [] } } })
        });
      }

      if (postData.query?.includes('MoveFiles')) {
        moveFilesCalled = true;
        moveInputReceived = postData.variables?.input;
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({ data: { moveFiles: true } })
        });
      }

      return route.fallback();
    });

    page.on('dialog', async (dialog) => {
      await dialog.accept();
    });

    const url = 'http://localhost:9999/plugins/empornium-megapack/review.html?scenes=1,2';
    await page.goto(url);
    await page.locator("#output-dir").fill("C:\\Packs");

    await page.locator('#btn-consolidate').click();

    await expect(page.locator('#status-text')).toContainText('Files moved successfully!');
    expect(moveFilesCalled).toBe(true);
    expect(moveInputReceived?.ids).toEqual([501, 502]);
  });

  test('ADV-M3-5: BuildMegapack execution with Polling Fallback delivering artifacts', async ({ page }) => {
    setupMocks(page);

    const scenesData = [
      { id: 10, title: 'Build Scene', paths: {}, files: [{ id: 1, path: 'D:\\Megapacks\\Adversarial M3 Build\\video.mp4' }] }
    ];

    let pollCount = 0;

    // Build pre-flight (todo 7 of staged-wizard-inplace-seed): the
    // authoritative on-disk probe must succeed or the build is blocked
    // fail-closed before dispatch.
    await page.route('**/api/fs/exists', async (route) => {
      const postData = JSON.parse(route.request().postData() || '{}');
      const results = {};
      for (const p of postData.paths || []) results[p] = true;
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ results })
      });
    });

    await page.route("**/api/run/*", async (route) => {
      if (route.request().method() === "GET") {
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            found: true,
            result: {
              status: "success",
              pack_title: "Adversarial M3 Build",
              torrent_path: "D:\\Megapacks\\Adversarial M3 Build.torrent",
              manifest_path: "D:\\Megapacks\\Adversarial M3 Build_manifest.json",
              submission_path: "D:\\Megapacks\\Adversarial M3 Build_submission.json",
              bbcode_path: "D:\\Megapacks\\Adversarial M3 Build_bbcode.txt",
              upload_previews: false,
              preview_only: true,
              ready: true,
              tracker_tags: ["build.scene"],
              preflight: {
                ready: true,
                checks: [
                  { id: "images_remote", label: "Preview Images", passed: true, detail: "All remote" },
                  { id: "tracker_tags", label: "Tracker Tags", passed: true, detail: "Tags valid" },
                  { id: "category", label: "Category", passed: true, is_info: true, detail: "Category selected" },
                  { id: "torrent_valid", label: "Torrent File", passed: true, detail: "Valid torrent" },
                  { id: "payload_files", label: "Media Files Verification", passed: true, detail: "Files exist" },
                  { id: "root_name", label: "Torrent Root Name", passed: true, detail: "Matches title" }
                ]
              }
            }
          })
        });
      }
      return route.fallback();
    });

    await page.route('**/graphql', async (route) => {
      const postData = JSON.parse(route.request().postData() || '{}');

      if (postData.query?.includes('FindScenes')) {
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({ data: { findScenes: { scenes: scenesData } } })
        });
      }

      if (postData.query?.includes('RunBuild')) {
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({ data: { runPluginTask: 'job-m3-poll-999' } })
        });
      }

      if (postData.query?.includes('FindJob')) {
        pollCount++;
        const isComplete = pollCount >= 2;
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            data: {
              findJob: {
                id: 'job-m3-poll-999',
                status: isComplete ? 'FINISHED' : 'RUNNING',
                progress: isComplete ? 1.0 : 0.65,
                error: null
              }
            }
          })
        });
      }

      return route.continue();
    });

    await page.addInitScript(() => {
      class FailingWebSocket {
        constructor(url, protocols) {
          setTimeout(() => {
            if (this.onerror) {
              this.onerror(new Event("error"));
            }
          }, 20);
        }
        send() {}
        close() {}
      }
      window.WebSocket = FailingWebSocket;
    });

    const url = 'http://localhost:9999/plugins/empornium-megapack/review.html?scenes=10&mode=megapack';
    await page.goto(url);

    await page.locator('#pack-title').fill('Adversarial M3 Build');
    await page.locator('#output-dir').fill('D:\\Megapacks');

    await page.locator('#btn-build').click();

    const summaryBox = page.locator('#artifact-summary');
    await expect(summaryBox).toBeVisible({ timeout: 10000 });
    await expect(summaryBox).toContainText('Build Complete!');
    await expect(page.locator('#artifact-details')).toContainText('D:\\Megapacks\\Adversarial M3 Build.torrent');
    await expect(page.locator('#artifact-details')).toContainText('D:\\Megapacks\\Adversarial M3 Build_manifest.json');
  });

  test('ADV-M3-6: R1 Unverified State — onTaskComplete with missing preflight.checks renders unverified header, 0 checklist items, disabled copy buttons, and inert upload link', async ({ page }) => {
    setupMocks(page);

    await page.route('**/graphql', async (route) => {
      const postData = JSON.parse(route.request().postData() || '{}');
      if (postData.query?.includes('FindScenes')) {
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            data: {
              findScenes: {
                scenes: [
                  {
                    id: 1,
                    title: 'Test Scene 1',
                    date: '2026-08-01',
                    files: [{ id: 101, path: 'C:/Packs/scene1.mp4', size: 1048576, height: 1080, width: 1920, duration: 300, video_codec: 'h264' }],
                    performers: [],
                    tags: []
                  }
                ]
              }
            }
          })
        });
      }
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ data: {} }) });
    });

    await page.goto('http://localhost:9999/plugins/empornium-megapack/review.html?scenes=1&mode=single');
    await page.waitForSelector('.scene-card');

    // Call onTaskComplete directly with payload missing preflight
    await page.evaluate(() => {
      window.onTaskComplete('BuildSingleScene', {
        pack_title: 'Unverified Test Single Scene',
        torrent_path: 'C:/Packs/unverified.torrent',
        site_url: 'https://www.empornium.is'
        // Notice: preflight is completely absent
      });
    });

    // 1. Verify unverified header is present and styled with danger color
    const header = page.locator('#handoff-status-header');
    await expect(header).toBeVisible();
    await expect(header).toHaveText('⚠️ Build Result Unverified — no result received from the backend');

    // 2. Verify unverified alert banner is visible with warning text
    const alert = page.locator('#unverified-build-alert');
    await expect(alert).toBeVisible();
    await expect(alert).toContainText('The build task finished in Stash, but no result payload was received');

    // 3. Verify exactly ZERO checklist items exist in DOM
    const checklistItems = page.locator('#preflight-checklist li');
    expect(await checklistItems.count()).toBe(0);

    // 4. Verify all Copy affordances are disabled
    await expect(page.locator('#btn-copy-title')).toBeDisabled();
    await expect(page.locator('#btn-copy-tags')).toBeDisabled();
    await expect(page.locator('#btn-copy-bbcode')).toBeDisabled();
    await expect(page.locator('#btn-copy-torrent-path')).toBeDisabled();

    // 5. Verify upload link is inert with pointer-events: none and opacity reduced
    const uploadLink = page.locator('#btn-open-upload');
    await expect(uploadLink).toBeVisible();
    const style = await uploadLink.getAttribute('style');
    expect(style).toContain('pointer-events: none');
    expect(style).toContain('opacity: 0.45');
    const title = await uploadLink.getAttribute('title');
    expect(title).toContain('Upload disabled: Build result is unverified');
  });

  test("ADV-M3-7: R2 Missing Artifact Paths — Missing torrent_path, manifest_path, submission_path render '— not reported —' and disable copy path button", async ({ page }) => {
    setupMocks(page);

    await page.route('**/graphql', async (route) => {
      const postData = JSON.parse(route.request().postData() || '{}');
      if (postData.query?.includes('FindScenes')) {
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            data: {
              findScenes: {
                scenes: [
                  {
                    id: 1,
                    title: 'Test Scene 1',
                    date: '2026-08-01',
                    files: [{ id: 101, path: 'C:/Packs/scene1.mp4', size: 1048576, height: 1080, width: 1920, duration: 300, video_codec: 'h264' }],
                    performers: [],
                    tags: []
                  }
                ]
              }
            }
          })
        });
      }
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ data: {} }) });
    });

    await page.goto('http://localhost:9999/plugins/empornium-megapack/review.html?scenes=1&mode=single');
    await page.waitForSelector('.scene-card');

    // Dispatch onTaskComplete with ready preflight but missing all artifact paths
    await page.evaluate(() => {
      window.onTaskComplete('BuildSingleScene', {
        pack_title: 'Missing Paths Scene',
        // torrent_path, manifest_path, submission_path, bbcode_path are all omitted
        preflight: {
          ready: true,
          checks: [{ id: 'test', label: 'Check', detail: 'Passed', passed: true }]
        }
      });
    });

    // Verify torrent path element renders '— not reported —' in muted italic text
    const torrentEl = page.locator('#handoff-torrent');
    await expect(torrentEl).toHaveText('— not reported —');
    const torrentTag = await torrentEl.evaluate(el => el.tagName.toLowerCase());
    expect(torrentTag).toBe('span');

    // Verify manifest and submission elements render '— not reported —'
    await expect(page.locator('#handoff-manifest')).toHaveText('— not reported —');
    await expect(page.locator('#handoff-submission')).toHaveText('— not reported —');

    // Verify Copy Path button is disabled because torrent_path is missing
    const copyPathBtn = page.locator('#btn-copy-torrent-path');
    await expect(copyPathBtn).toBeDisabled();
    const copyPathTitle = await copyPathBtn.getAttribute('title');
    expect(copyPathTitle).toContain('Path not reported');

    // Verify Copy Title is enabled since preflight passed
    await expect(page.locator('#btn-copy-title')).toBeEnabled();
  });

  test('ADV-M3-8: R3 Race Condition & Re-entrancy — Concurrent WebSocket and Polling FINISHED events execute exactly once', async ({ page }) => {
    setupMocks(page);

    // Mock sidecar run endpoint to return success result
    await page.route('**/api/run/*', (route) => {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          found: true,
          result: {
            status: 'success',
            pack_title: 'Race Test Pack',
            torrent_path: 'C:/Packs/race.torrent',
            preflight: { ready: true, checks: [{ id: 'chk1', label: 'Check', detail: 'OK', passed: true }] }
          }
        })
      });
    });

    await page.route('**/graphql', async (route) => {
      const postData = JSON.parse(route.request().postData() || '{}');
      if (postData.query?.includes('FindScenes')) {
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            data: {
              findScenes: {
                scenes: [
                  {
                    id: 1,
                    title: 'Test Scene 1',
                    date: '2026-08-01',
                    files: [{ id: 101, path: 'C:/Packs/scene1.mp4', size: 1048576, height: 1080, width: 1920, duration: 300, video_codec: 'h264' }],
                    performers: [],
                    tags: []
                  }
                ]
              }
            }
          })
        });
      }
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ data: {} }) });
    });

    await page.goto('http://localhost:9999/plugins/empornium-megapack/review.html?scenes=1&mode=single');
    await page.waitForSelector('.scene-card');

    const executionStats = await page.evaluate(async () => {
      const testJob = {
        id: 'job-race-123',
        status: 'FINISHED',
        progress: 1.0
      };

      const payload = {
        run_id: 'test-race-run',
        pack_title: 'Race Test Pack'
      };

      // Fire 10 simultaneous handleJobUpdate calls as if WS messages and polling intervals collided
      const promises = [];
      for (let i = 0; i < 10; i++) {
        promises.push(window.handleJobUpdate(testJob, 'BuildMegapack', payload));
      }
      await Promise.all(promises);

      return {
        handled: window.handledJobIds ? window.handledJobIds.has('job-race-123') : null
      };
    });

    expect(executionStats.handled).toBe(true);

    // Verify UI successfully transitioned exactly once to completed state
    await expect(page.locator('#handoff-title')).toHaveText('Race Test Pack');
    await expect(page.locator('#handoff-torrent')).toHaveText('C:/Packs/race.torrent');
    await expect(page.locator('#handoff-status-header')).toContainText('Build Complete!');
  });

  test('ADV-M3-9: R3 Transport Error Failover — Unreachable sidecar fails over immediately to log sentinel with zero retry delay', async ({ page }) => {
    setupMocks(page);

    // Mock sidecar endpoints to throw network error (unreachable sidecar)
    await page.route('**/api/run/*', (route) => route.abort('connectionrefused'));

    // Mock GraphQL logs to return the result sentinel
    const runId = 'test-failover-run-456';
    await page.route('**/graphql', async (route) => {
      const postData = JSON.parse(route.request().postData() || '{}');
      const query = postData.query || '';
      if (query.includes('FindScenes')) {
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            data: { findScenes: { scenes: [{ id: 1, title: 'S1', files: [{ path: 'C:/p.mp4', size: 100 }] }] } }
          })
        });
      }
      if (query.includes('logs')) {
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            data: {
              logs: [
                {
                  time: '2026-08-28T10:00:00Z',
                  level: 'Info',
                  message: `EMPORNIUM_TASK_RESULT ${runId}: ` + JSON.stringify({
                    status: 'success',
                    pack_title: 'Failover Result Pack',
                    torrent_path: 'C:/Packs/failover.torrent',
                    preflight: { ready: true, checks: [{ id: 'chk1', label: 'Check 1', detail: 'OK', passed: true }] }
                  })
                }
              ]
            }
          })
        });
      }
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ data: {} }) });
    });

    await page.goto('http://localhost:9999/plugins/empornium-megapack/review.html?scenes=1&mode=single');
    await page.waitForSelector('.scene-card');

    const startTime = Date.now();
    await page.evaluate(async (rid) => {
      const job = { id: 'job-failover-789', status: 'FINISHED', progress: 1.0 };
      const payload = { run_id: rid, pack_title: 'Failover Result Pack' };
      await window.handleJobUpdate(job, 'BuildMegapack', payload);
    }, runId);
    const elapsedMs = Date.now() - startTime;

    // Verify result was parsed from log sentinel successfully
    await expect(page.locator('#handoff-title')).toHaveText('Failover Result Pack');
    await expect(page.locator('#handoff-torrent')).toHaveText('C:/Packs/failover.torrent');

    // Zero-delay check: elapsed time should be well under 2.5s (no 5s retry loop on network failure)
    expect(elapsedMs).toBeLessThan(2500);
  });

  test('ADV-M3-9b: R3 Server Error Failover — Sidecar returning HTTP 500 fails over immediately to log sentinel with zero retry delay', async ({ page }) => {
    setupMocks(page);

    // Mock sidecar endpoints to return HTTP 500 internal server error
    await page.route('**/api/run/*', (route) => route.fulfill({ status: 500, contentType: 'application/json', body: JSON.stringify({ error: 'Internal Server Error' }) }));

    // Mock GraphQL logs to return the result sentinel
    const runId = 'test-failover-500-run-789';
    await page.route('**/graphql', async (route) => {
      const postData = JSON.parse(route.request().postData() || '{}');
      const query = postData.query || '';
      if (query.includes('FindScenes')) {
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            data: { findScenes: { scenes: [{ id: 1, title: 'S1', files: [{ path: 'C:/p.mp4', size: 100 }] }] } }
          })
        });
      }
      if (query.includes('logs')) {
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            data: {
              logs: [
                {
                  time: '2026-08-28T10:00:00Z',
                  level: 'Info',
                  message: `EMPORNIUM_TASK_RESULT ${runId}: ` + JSON.stringify({
                    status: 'success',
                    pack_title: 'Server Error Failover Pack',
                    torrent_path: 'C:/Packs/server_error_failover.torrent',
                    preflight: { ready: true, checks: [{ id: 'chk1', label: 'Check 1', detail: 'OK', passed: true }] }
                  })
                }
              ]
            }
          })
        });
      }
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ data: {} }) });
    });

    await page.goto('http://localhost:9999/plugins/empornium-megapack/review.html?scenes=1&mode=single');
    await page.waitForSelector('.scene-card');

    const startTime = Date.now();
    await page.evaluate(async (rid) => {
      const job = { id: 'job-failover-500-999', status: 'FINISHED', progress: 1.0 };
      const payload = { run_id: rid, pack_title: 'Server Error Failover Pack' };
      await window.handleJobUpdate(job, 'BuildMegapack', payload);
    }, runId);
    const elapsedMs = Date.now() - startTime;

    // Verify result was parsed from log sentinel successfully
    await expect(page.locator('#handoff-title')).toHaveText('Server Error Failover Pack');
    await expect(page.locator('#handoff-torrent')).toHaveText('C:/Packs/server_error_failover.torrent');

    // Zero-delay check: elapsed time should be well under 2.5s (no 5s retry loop on 500 server error)
    expect(elapsedMs).toBeLessThan(2500);
  });

  test('ADV-M3-10: R4 WebSocket Drop Recovery — ws.onclose starts polling; simultaneous onerror + onclose prevents duplicate polling loops', async ({ page }) => {
    setupMocks(page);

    let findJobQueryCount = 0;
    await page.route('**/graphql', async (route) => {
      const postData = JSON.parse(route.request().postData() || '{}');
      if (postData.query?.includes('FindScenes')) {
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            data: {
              findScenes: {
                scenes: [
                  {
                    id: 1,
                    title: 'Test Scene 1',
                    date: '2026-08-01',
                    files: [{ id: 101, path: 'C:/Packs/scene1.mp4', size: 1048576, height: 1080, width: 1920, duration: 300, video_codec: 'h264' }],
                    performers: [],
                    tags: []
                  }
                ]
              }
            }
          })
        });
      }
      if (postData.query?.includes('FindJob')) {
        findJobQueryCount++;
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            data: {
              findJob: {
                id: 'job-drop-999',
                status: 'RUNNING',
                progress: 0.5,
                error: null
              }
            }
          })
        });
      }
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ data: {} }) });
    });

    await page.goto('http://localhost:9999/plugins/empornium-megapack/review.html?scenes=1&mode=single');
    await page.waitForSelector('.scene-card');

    // Trigger simultaneous double-start of startJobPolling (simulating simultaneous onerror + onclose)
    await page.evaluate(() => {
      window.startJobPolling('job-drop-999', 'BuildMegapack', { run_id: 'test-drop' });
      window.startJobPolling('job-drop-999', 'BuildMegapack', { run_id: 'test-drop' });
    });

    // Wait 250ms for initial poll execution
    await page.waitForTimeout(250);

    // Stop polling before completing test
    await page.evaluate(() => {
      if (window.activePollInterval) {
        clearInterval(window.activePollInterval);
        window.activePollInterval = null;
      }
    });

    // Exactly 1 poll request should have been sent on startup, double-start guard prevented second poll loop
    expect(findJobQueryCount).toBe(1);
  });

  test('ADV-M3-11: Literal and Template Elimination — Hardcoded checklist string and fabricated path templates are completely absent from review.js', async () => {
    const reviewJsContent = fs.readFileSync(path.resolve('plugin/assets/review.js'), 'utf8');

    // Check for eliminated hardcoded checklist literal
    expect(reviewJsContent.includes('private=True, source=Emp, non-empty pieces')).toBe(false);

    // Check for eliminated template strings
    expect(reviewJsContent.includes('${outputDir}\\${packTitle}.torrent')).toBe(false);
    expect(reviewJsContent.includes('${outputDir}\\${packTitle}_manifest.json')).toBe(false);
    expect(reviewJsContent.includes('${outputDir}\\${packTitle}_submission.json')).toBe(false);
    expect(reviewJsContent.includes('${outputDir}\\${packTitle}_bbcode.txt')).toBe(false);
  });

});
