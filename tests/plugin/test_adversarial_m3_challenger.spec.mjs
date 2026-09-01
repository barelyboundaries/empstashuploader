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
    const bbcodeText = await bbcodePreview.innerText();
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

    const bbcode = await page.locator('#bbcode-preview').innerText();
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

});
