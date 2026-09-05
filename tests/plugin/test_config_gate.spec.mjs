import { test, expect } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

function serveAssets(page) {
  page.route('**/plugin*/**/main.js*', async (route) => {
    return route.fulfill({
      status: 200,
      contentType: 'application/javascript',
      body: fs.readFileSync(path.resolve('plugin/main.js'), 'utf8'),
    });
  });

  page.route('**/plugin*/**/style.css*', async (route) => {
    return route.fulfill({
      status: 200,
      contentType: 'text/css',
      body: fs.readFileSync(path.resolve('plugin/style.css'), 'utf8'),
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

  page.route('**/api/fs/exists*', async (route) => {
    const postData = JSON.parse(route.request().postData() || '{}');
    const results = {};
    for (const p of postData.paths || []) results[p] = true;
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ results }),
    });
  });

  // Suite-level harness invariant: isolate sidecar api/run
  page.route('**/api/run/**', async (route) => route.abort('connectionrefused'));
}

function scene(id, fileId, filePath, title) {
  return {
    id,
    title,
    date: '2026-01-01',
    paths: { screenshot: '' },
    files: [{
      id: fileId,
      path: filePath,
      size: 5000000,
      height: 1080,
      width: 1920,
      duration: 600,
      video_codec: 'h264',
      oshash: `oshash-${fileId}`,
    }],
    performers: [],
    tags: [],
    studio: null,
  };
}

async function bootHarness(page, {
  scenes = [
    scene(1, 101, 'C:\\Packs\\scene1.mp4', 'Scene 1'),
    scene(2, 102, 'C:\\Packs\\scene2.mp4', 'Scene 2')
  ],
  healthPayload = {
    status: 'ok',
    track: 'Empornium Megapack Builder',
    version: '0.2.0',
    scratch_dir: 'C:\\Scratch',
    output_dir: 'C:\\Packs',
    hamster_configured: true,
    announce_configured: true,
  }
} = {}) {
  serveAssets(page);
  let currentHealth = { ...healthPayload };
  const refreshCalls = [];

  await page.route('**/health', async (route) => {
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(currentHealth),
    });
  });

  await page.route('**/api/config/refresh', async (route) => {
    refreshCalls.push(route.request().method());
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        status: 'ok',
        hamster_configured: currentHealth.hamster_configured,
        announce_configured: currentHealth.announce_configured,
      }),
    });
  });

  await page.route('**/graphql', async (route) => {
    const postData = JSON.parse(route.request().postData() || '{}');
    const query = postData.query || '';
    if (query.includes('FindScenes')) {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ data: { findScenes: { scenes } } }),
      });
    }
    if (query.includes('runPluginTask')) {
      const taskName = postData.variables?.task_name || 'GenericTask';
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ data: { runPluginTask: `job-${taskName}-1` } }),
      });
    }
    if (query.includes('JobQueue')) {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ data: { jobQueue: [] } }),
      });
    }
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ data: {} }),
    });
  });

  const sceneIds = scenes.map((s) => s.id).join(',');
  await page.goto(`http://localhost:9999/plugins/empornium-megapack/review.html?scenes=${sceneIds}&mode=megapack`);
  await expect(page.locator('.scene-card')).toHaveCount(scenes.length);

  await page.locator('#output-dir').fill('C:\\Packs');
  await page.locator('#scratch-dir').fill('C:\\Scratch');

  return {
    setHealth: (newHealth) => { currentHealth = { ...currentHealth, ...newHealth }; },
    refreshCalls,
  };
}

test.describe('Entry Gate — Announce URL and HamsterImg API Key Configuration Gate', () => {
  test('banner appears and Build is disabled when announce_configured is false', async ({ page }) => {
    await bootHarness(page, {
      healthPayload: {
        status: 'ok',
        track: 'Empornium Megapack Builder',
        version: '0.2.0',
        scratch_dir: 'C:\\Scratch',
        output_dir: 'C:\\Packs',
        hamster_configured: true,
        announce_configured: false,
      }
    });

    const banner = page.locator('#config-warning-banner');
    await expect(banner).toBeVisible();
    await expect(banner).toContainText('Empornium announce URL');
    await expect(banner).toContainText('Stash -> Settings -> Plugins -> Empornium Megapack Builder');

    const btnBuild = page.locator('#btn-build');
    await expect(btnBuild).toBeDisabled();
    await expect(btnBuild).toHaveAttribute('title', /Empornium announce URL.*Stash -> Settings -> Plugins -> Empornium Megapack Builder/);
  });

  test('banner appears and Build is disabled when hamster_configured is false', async ({ page }) => {
    await bootHarness(page, {
      healthPayload: {
        status: 'ok',
        track: 'Empornium Megapack Builder',
        version: '0.2.0',
        scratch_dir: 'C:\\Scratch',
        output_dir: 'C:\\Packs',
        hamster_configured: false,
        announce_configured: true,
      }
    });

    const banner = page.locator('#config-warning-banner');
    await expect(banner).toBeVisible();
    await expect(banner).toContainText('HamsterImg API key');
    await expect(banner).toContainText('Stash -> Settings -> Plugins -> Empornium Megapack Builder');

    const btnBuild = page.locator('#btn-build');
    await expect(btnBuild).toBeDisabled();
    await expect(btnBuild).toHaveAttribute('title', /HamsterImg API key.*Stash -> Settings -> Plugins -> Empornium Megapack Builder/);
  });

  test('banner appears and names both when both are false', async ({ page }) => {
    await bootHarness(page, {
      healthPayload: {
        status: 'ok',
        track: 'Empornium Megapack Builder',
        version: '0.2.0',
        scratch_dir: 'C:\\Scratch',
        output_dir: 'C:\\Packs',
        hamster_configured: false,
        announce_configured: false,
      }
    });

    const banner = page.locator('#config-warning-banner');
    await expect(banner).toBeVisible();
    await expect(banner).toContainText('Empornium announce URL');
    await expect(banner).toContainText('HamsterImg API key');
    await expect(banner).toContainText('Stash -> Settings -> Plugins -> Empornium Megapack Builder');

    const btnBuild = page.locator('#btn-build');
    await expect(btnBuild).toBeDisabled();
    await expect(btnBuild).toHaveAttribute('title', /Empornium announce URL and HamsterImg API key.*Stash -> Settings -> Plugins -> Empornium Megapack Builder/);
  });

  test('fail closed: banner appears and Build is disabled when configuration fields are absent', async ({ page }) => {
    await bootHarness(page, {
      healthPayload: {
        status: 'ok',
        track: 'Empornium Megapack Builder',
        version: '0.2.0',
        scratch_dir: 'C:\\Scratch',
        output_dir: 'C:\\Packs',
      }
    });

    const banner = page.locator('#config-warning-banner');
    await expect(banner).toBeVisible();
    await expect(banner).toContainText('Stash -> Settings -> Plugins -> Empornium Megapack Builder');

    const btnBuild = page.locator('#btn-build');
    await expect(btnBuild).toBeDisabled();
  });

  test('banner clears and Build re-enables after a successful Recheck', async ({ page }) => {
    const harness = await bootHarness(page, {
      healthPayload: {
        status: 'ok',
        track: 'Empornium Megapack Builder',
        version: '0.2.0',
        scratch_dir: 'C:\\Scratch',
        output_dir: 'C:\\Packs',
        hamster_configured: false,
        announce_configured: true,
      }
    });

    const banner = page.locator('#config-warning-banner');
    const btnBuild = page.locator('#btn-build');
    const btnRecheck = page.locator('#btn-config-recheck');
    const reasonEl = page.locator('#action-disabled-reason');

    await expect(banner).toBeVisible();
    await expect(btnBuild).toBeDisabled();
    await expect(reasonEl).toBeVisible();
    await expect(reasonEl).toHaveText(await btnBuild.getAttribute('title'));

    // User updates settings in Stash, backend now returns true for both
    harness.setHealth({
      hamster_configured: true,
      announce_configured: true,
    });

    // Click Recheck
    await btnRecheck.click();

    // Recheck triggers POST /api/config/refresh and re-queries /health
    expect(harness.refreshCalls.length).toBeGreaterThanOrEqual(1);
    await expect(banner).toBeHidden();
    await expect(btnBuild).toBeEnabled();
    await expect(reasonEl).toBeHidden();
  });

  test('composition with busy lock: clean config recheck does not enable button while UI is busy', async ({ page }) => {
    const harness = await bootHarness(page, {
      healthPayload: {
        status: 'ok',
        track: 'Empornium Megapack Builder',
        version: '0.2.0',
        scratch_dir: 'C:\\Scratch',
        output_dir: 'C:\\Packs',
        hamster_configured: false,
        announce_configured: true,
      }
    });

    const btnBuild = page.locator('#btn-build');
    await expect(btnBuild).toBeDisabled();

    // Set UI busy manually
    await page.evaluate(() => {
      window.setUiBusy(true, 'build', 'BuildMegapack');
    });
    await expect(btnBuild).toHaveAttribute('title', /in progress — controls locked/);

    // Update config to clean
    harness.setHealth({
      hamster_configured: true,
      announce_configured: true,
    });

    // Trigger config check while busy
    await page.evaluate(async () => {
      await window.checkConfigGate();
    });

    // Button MUST remain disabled due to busy lock
    await expect(btnBuild).toBeDisabled();
    await expect(btnBuild).toHaveAttribute('title', /in progress — controls locked/);

    // Unset UI busy -> button now enables because config is valid
    await page.evaluate(() => {
      window.setUiBusy(false);
    });
    await expect(btnBuild).toBeEnabled();
  });
});
