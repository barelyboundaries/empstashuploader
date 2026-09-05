import { test, expect } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

// Change B — Lock the UI while a Stash task is in flight:
//
//   1. Click Build -> #btn-build, #btn-consolidate, #btn-probe, #output-dir,
//      .scene-remove-btn, #mode-single are all disabled; #busy-banner is visible.
//   2. A second click on #btn-build while locked issues no second runPluginTask (assert
//      on mutation call count, not on DOM state).
//   3. .scene-card[draggable="false"] while locked.
//   4. renderScenes() firing mid-build does not re-enable #btn-build (regression guard for
//      the updateActionAvailability early branch).
//   5. Job -> FINISHED unlocks; job -> FAILED unlocks; job -> CANCELLED unlocks.
//   6. A validation abort (no active scenes) unlocks without ever dispatching.
//   7. The escape hatch appears after the 60s threshold and unlocks on click.
//   8. #bbcode-preview is disabled in the build tier and enabled in the mutation tier.

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

  // Isolate sidecar api/run and health routes per suite-level harness invariant
  page.route('**/api/run/**', async (route) => route.abort('connectionrefused'));
  page.route('**/health', async (route) => {
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ ok: true, status: 'connected', version: '0.2.0', scratch_dir: 'C:\\Scratch', announce_configured: true, hamster_configured: true }),
    });
  });
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

async function bootHarness(page, { scenes = [scene(1, 101, 'C:\\Packs\\scene1.mp4', 'Scene 1'), scene(2, 102, 'C:\\Packs\\scene2.mp4', 'Scene 2')], mode = 'megapack' } = {}) {
  serveAssets(page);
  const wire = { tasks: [], mutations: [] };

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
      wire.tasks.push(postData.variables);
      const taskName = postData.variables?.task_name || 'GenericTask';
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ data: { runPluginTask: `job-${taskName}-1` } }),
      });
    }
    if (query.includes('MoveFiles') || query.includes('moveFiles')) {
      wire.mutations.push(postData.variables);
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ data: { moveFiles: true } }),
      });
    }
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ data: {} }) });
  });

  const sceneIds = scenes.map((s) => s.id).join(',');
  await page.goto(`http://localhost:9999/plugins/empornium-megapack/review.html?scenes=${sceneIds}&mode=${mode}`);
  await expect(page.locator('.scene-card')).toHaveCount(scenes.length);
  await page.locator('#pack-title').fill('My Megapack');
  await page.locator('#output-dir').fill('C:\\Packs');
  await page.locator('#scratch-dir').fill('C:\\Scratch');
  return { wire };
}

test.describe('Change B — Lock the UI while a Stash task is in flight', () => {

  test('1. Click Build -> #btn-build, #btn-consolidate, #btn-probe, #output-dir, .scene-remove-btn, #mode-single are all disabled; #busy-banner is visible', async ({ page }) => {
    await bootHarness(page, {
      scenes: [
        scene(1, 101, 'C:\\Packs\\scene1.mp4', 'Scene 1'),
        scene(2, 102, 'C:\\Packs\\scene2.mp4', 'Scene 2'),
      ],
    });

    const btnBuild = page.locator('#btn-build');
    const btnConsolidate = page.locator('#btn-consolidate');
    const btnProbe = page.locator('#btn-probe');
    const outputDir = page.locator('#output-dir');
    const sceneRemoveBtn = page.locator('.scene-card').first().locator('.scene-remove-btn');
    const modeSingle = page.locator('#mode-single');
    const busyBanner = page.locator('#busy-banner');

    await expect(btnBuild).toBeEnabled();
    await btnBuild.click();

    await expect(btnBuild).toBeDisabled();
    await expect(btnConsolidate).toBeDisabled();
    await expect(btnProbe).toBeDisabled();
    await expect(outputDir).toBeDisabled();
    await expect(sceneRemoveBtn).toBeDisabled();
    await expect(modeSingle).toBeDisabled();
    await expect(busyBanner).toBeVisible();
    await expect(page.locator('#busy-banner-text')).toContainText('⏳ BuildMegapack in progress — controls locked until it finishes or fails.');
  });

  test('2. A second click on #btn-build while locked issues no second runPluginTask (assert on mutation call count, not DOM state)', async ({ page }) => {
    const { wire } = await bootHarness(page, {
      scenes: [
        scene(1, 101, 'C:\\Packs\\scene1.mp4', 'Scene 1'),
        scene(2, 102, 'C:\\Packs\\scene2.mp4', 'Scene 2'),
      ],
    });

    const btnBuild = page.locator('#btn-build');
    await expect(btnBuild).toBeEnabled();

    // First click dispatches task
    await btnBuild.click();
    await expect.poll(() => wire.tasks.length).toBe(1);

    // Second click while locked (force: true bypasses Playwright disabled check to simulate user/script click)
    await btnBuild.click({ force: true });
    // Also simulate direct DOM click invocation
    await page.evaluate(() => document.getElementById('btn-build').click());

    await page.waitForTimeout(200);

    // Assert strictly on mutation count: exactly 1 mutation call occurred
    expect(wire.tasks.length).toBe(1);
  });

  test('3. .scene-card[draggable="false"] while locked', async ({ page }) => {
    await bootHarness(page, {
      scenes: [
        scene(1, 101, 'C:\\Packs\\scene1.mp4', 'Scene 1'),
        scene(2, 102, 'C:\\Packs\\scene2.mp4', 'Scene 2'),
      ],
    });

    // Before build, cards are draggable
    await expect(page.locator('.scene-card[draggable="true"]')).toHaveCount(2);

    await page.locator('#btn-build').click();

    // While locked, cards are not draggable
    await expect(page.locator('.scene-card[draggable="false"]')).toHaveCount(2);
  });

  test('4. renderScenes() firing mid-build does not re-enable #btn-build (updateActionAvailability early return regression guard)', async ({ page }) => {
    await bootHarness(page, {
      scenes: [
        scene(1, 101, 'C:\\Packs\\scene1.mp4', 'Scene 1'),
        scene(2, 102, 'C:\\Packs\\scene2.mp4', 'Scene 2'),
      ],
    });

    const btnBuild = page.locator('#btn-build');
    await btnBuild.click();
    await expect(btnBuild).toBeDisabled();

    // Re-render scenes mid-build
    await page.evaluate(() => window.renderScenes());

    // Regression check: Build must remain locked and disabled
    await expect(btnBuild).toBeDisabled();
    await expect(btnBuild).toHaveAttribute('title', /in progress — controls locked/);
    // Newly rendered cards must inherit the lock
    await expect(page.locator('.scene-card[draggable="false"]')).toHaveCount(2);
    await expect(page.locator('.scene-card').first().locator('.scene-remove-btn')).toBeDisabled();
  });

  test('5. Job -> FINISHED unlocks; job -> FAILED unlocks; job -> CANCELLED unlocks', async ({ page }) => {
    await bootHarness(page, {
      scenes: [
        scene(1, 101, 'C:\\Packs\\scene1.mp4', 'Scene 1'),
        scene(2, 102, 'C:\\Packs\\scene2.mp4', 'Scene 2'),
      ],
    });

    const btnBuild = page.locator('#btn-build');
    const busyBanner = page.locator('#busy-banner');

    // 5a: FINISHED unlocks
    await btnBuild.click();
    await expect(busyBanner).toBeVisible();
    await expect(btnBuild).toBeDisabled();

    await page.evaluate(() => {
      window.handleJobUpdate({ id: 'job-BuildMegapack-1', status: 'FINISHED' }, 'BuildMegapack', {});
    });
    await expect(busyBanner).toBeHidden();
    await expect(btnBuild).toBeEnabled();

    // 5b: FAILED unlocks
    await btnBuild.click();
    await expect(busyBanner).toBeVisible();
    await expect(btnBuild).toBeDisabled();

    await page.evaluate(() => {
      window.handleJobUpdate({ id: 'job-BuildMegapack-2', status: 'FAILED', error: 'simulated failure' }, 'BuildMegapack', {});
    });
    await expect(busyBanner).toBeHidden();
    await expect(btnBuild).toBeEnabled();

    // 5c: CANCELLED unlocks
    await btnBuild.click();
    await expect(busyBanner).toBeVisible();
    await expect(btnBuild).toBeDisabled();

    await page.evaluate(() => {
      window.handleJobUpdate({ id: 'job-BuildMegapack-3', status: 'CANCELLED' }, 'BuildMegapack', {});
    });
    await expect(busyBanner).toBeHidden();
    await expect(btnBuild).toBeEnabled();
  });

  test('6. A validation abort (no active scenes) unlocks without ever dispatching', async ({ page }) => {
    const { wire } = await bootHarness(page, {
      scenes: [
        scene(1, 101, 'C:\\Packs\\scene1.mp4', 'Scene 1'),
        scene(2, 102, 'C:\\Packs\\scene2.mp4', 'Scene 2'),
      ],
    });

    // Remove all scenes from the pack
    await page.locator('.scene-card[data-scene-id="1"] .scene-remove-btn').click();
    await page.locator('.scene-card[data-scene-id="2"] .scene-remove-btn').click();

    // Try triggering build
    await page.evaluate(() => window.runExclusive('build', 'BuildMegapack', window.buildMegapack));

    // Must show validation abort error
    await expect(page.locator('#status-text')).toContainText('Build aborted: No active scenes in selection.');

    // Must be unlocked (not stuck busy)
    const isBusy = await page.evaluate(() => window.getUiBusy());
    expect(isBusy).toBe(false);
    await expect(page.locator('#busy-banner')).toBeHidden();

    // Must have dispatched no GraphQL mutations
    expect(wire.tasks.length).toBe(0);
  });

  test('7. The escape hatch appears after the 60s threshold and unlocks on click', async ({ page }) => {
    await bootHarness(page, {
      scenes: [
        scene(1, 101, 'C:\\Packs\\scene1.mp4', 'Scene 1'),
        scene(2, 102, 'C:\\Packs\\scene2.mp4', 'Scene 2'),
      ],
    });

    const btnBuild = page.locator('#btn-build');
    const unlockBtn = page.locator('#btn-busy-unlock');
    const bannerText = page.locator('#busy-banner-text');

    await btnBuild.click();
    await expect(page.locator('#busy-banner')).toBeVisible();
    await expect(unlockBtn).toBeHidden();

    // Simulate 65 seconds elapsed since lock
    await page.evaluate(() => {
      window.setBusyStartedAt(Date.now() - 65000);
      window.updateBusyEscapeHatch();
    });

    await expect(unlockBtn).toBeVisible();
    await expect(bannerText).toContainText('Controls locked for 1m 5s — ');

    // Click "Unlock anyway"
    await unlockBtn.click();

    // UI is now unlocked
    await expect(page.locator('#busy-banner')).toBeHidden();
    await expect(btnBuild).toBeEnabled();
    await expect(page.locator('#status-text')).toContainText(
      'Controls unlocked manually. The Stash job may still be running — check the Task Manager before starting another build.'
    );
  });

  test('8. #bbcode-preview is disabled in the build tier and enabled in the mutation tier', async ({ page }) => {
    await bootHarness(page, {
      scenes: [
        scene(1, 101, 'C:\\Packs\\scene1.mp4', 'Scene 1'),
        scene(2, 102, 'C:\\Packs\\scene2.mp4', 'Scene 2'),
      ],
    });

    const bbcodePreview = page.locator('#bbcode-preview');
    const btnBuild = page.locator('#btn-build');
    const btnProbe = page.locator('#btn-probe');

    // Build tier: locks BBCode editor
    await btnBuild.click();
    await expect(bbcodePreview).toBeDisabled();

    // Unlock via terminal job update
    await page.evaluate(() => {
      window.handleJobUpdate({ id: 'job-BuildMegapack-1', status: 'FINISHED' }, 'BuildMegapack', {});
    });
    await expect(bbcodePreview).toBeEnabled();

    // Mutation tier (Probe): BBCode editor remains enabled
    await btnProbe.click();
    await expect(btnProbe).toBeDisabled();
    await expect(page.locator('#output-dir')).toBeDisabled();
    await expect(bbcodePreview).toBeEnabled();

    // Unlock via terminal job update
    await page.evaluate(() => {
      window.handleJobUpdate({ id: 'job-ProbeFiles-1', status: 'FINISHED' }, 'ProbeFiles', {});
    });
    await expect(bbcodePreview).toBeEnabled();
  });
});
