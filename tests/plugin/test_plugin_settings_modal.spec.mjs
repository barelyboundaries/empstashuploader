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

  page.route('**/api/run/**', async (route) => route.abort('connectionrefused'));
}

function sampleScene(id) {
  return {
    id,
    title: `Scene ${id}`,
    date: '2026-01-01',
    paths: { screenshot: '' },
    files: [{
      id: id * 100,
      path: `C:\\Packs\\scene${id}.mp4`,
      size: 5000000,
      height: 1080,
      width: 1920,
      duration: 600,
      video_codec: 'h264',
      oshash: `oshash-${id}`,
    }],
    performers: [],
    tags: [],
    studio: null,
  };
}

async function bootSettingsHarness(page, {
  scenes = [sampleScene(1)],
  healthPayload = {
    status: 'ok',
    track: 'Empornium Megapack Builder',
    version: '0.2.0',
    scratch_dir: 'C:\\Scratch',
    output_dir: 'C:\\Packs',
    hamster_configured: true,
    hamster_source: 'Stash plugin settings',
    announce_configured: true,
    announce_source: 'Stash plugin settings',
  },
  storedPlugins = {
    'empornium-megapack': {
      announceUrl: 'http://tracker.empornium.sx:2710/test/announce',
      hamsterApiKey: 'hamster-initial-key',
    },
  },
  configurePluginError = null,
} = {}) {
  serveAssets(page);
  let currentHealth = { ...healthPayload };
  let currentPlugins = JSON.parse(JSON.stringify(storedPlugins));
  const refreshCalls = [];
  const configurePluginCalls = [];

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
        hamster_source: currentHealth.hamster_source,
        announce_source: currentHealth.announce_source,
        announce_valid: currentHealth.announce_valid,
        announce_invalid_reason: currentHealth.announce_invalid_reason,
      }),
    });
  });

  await page.route('**/graphql', async (route) => {
    const postData = JSON.parse(route.request().postData() || '{}');
    const query = postData.query || '';
    const vars = postData.variables || {};

    if (query.includes('FindScenes')) {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ data: { findScenes: { scenes } } }),
      });
    }

    if (query.includes('JobQueue')) {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ data: { jobQueue: [] } }),
      });
    }

    if (query.includes('PluginConfiguration') || (query.includes('configuration') && query.includes('plugins'))) {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ data: { configuration: { plugins: currentPlugins } } }),
      });
    }

    if (query.includes('configurePlugin')) {
      configurePluginCalls.push(vars);
      if (configurePluginError) {
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            errors: [{ message: configurePluginError }],
            data: null,
          }),
        });
      }
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ data: { configurePlugin: vars.input } }),
      });
    }

    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ data: {} }),
    });
  });

  await page.goto(`http://localhost:9999/plugins/empornium-megapack/review.html?scenes=1&mode=megapack`);
  await expect(page.locator('.scene-card')).toHaveCount(1);
  await page.locator('#output-dir').fill('C:\\Packs');
  await page.locator('#scratch-dir').fill('C:\\Scratch');

  return {
    setHealth: (h) => { currentHealth = { ...currentHealth, ...h }; },
    setConfigurePluginError: (err) => { configurePluginError = err; },
    getConfigurePluginCalls: () => configurePluginCalls,
    getRefreshCalls: () => refreshCalls,
  };
}

test.describe('In-app Plugin Settings Dialog', () => {
  test('the dialog opens from BOTH entry points and closes via Cancel and Escape', async ({ page }) => {
    await bootSettingsHarness(page, {
      healthPayload: {
        status: 'ok',
        track: 'Empornium Megapack Builder',
        version: '0.2.0',
        scratch_dir: 'C:\\Scratch',
        output_dir: 'C:\\Packs',
        hamster_configured: true,
        announce_configured: false,
      },
    });

    const modal = page.locator('#plugin-settings-modal');
    const announceInput = page.locator('#setting-announce-url');
    const headerSettingsBtn = page.locator('#btn-settings');
    const bannerSettingsBtn = page.locator('#btn-config-settings');
    const cancelBtn = page.locator('#btn-cancel-plugin-settings');

    await expect(modal).toBeHidden();

    // 1. Open from Entry point 1 (⚙ Settings button in header)
    await headerSettingsBtn.click();
    await expect(modal).toBeVisible();
    await expect(announceInput).toBeFocused();

    // Close via Cancel button
    await cancelBtn.click();
    await expect(modal).toBeHidden();
    await expect(headerSettingsBtn).toBeFocused();

    // 2. Open from Entry point 2 (Enter details button in warning banner)
    await expect(bannerSettingsBtn).toBeVisible();
    await bannerSettingsBtn.click();
    await expect(modal).toBeVisible();
    await expect(announceInput).toBeFocused();

    // Close via Escape key
    await page.keyboard.press('Escape');
    await expect(modal).toBeHidden();
    await expect(bannerSettingsBtn).toBeFocused();
  });

  test('fields prefill from the mocked configuration { plugins } payload', async ({ page }) => {
    await bootSettingsHarness(page, {
      storedPlugins: {
        'empornium-megapack': {
          announceUrl: 'http://tracker.custom.net:2710/user123/pass456/announce',
          hamsterApiKey: 'hamster-key-abcdef-999',
        },
      },
    });

    const modal = page.locator('#plugin-settings-modal');
    await page.locator('#btn-settings').click();
    await expect(modal).toBeVisible();

    await expect(page.locator('#setting-announce-url')).toHaveValue('http://tracker.custom.net:2710/user123/pass456/announce');
    await expect(page.locator('#setting-hamster-key')).toHaveValue('hamster-key-abcdef-999');
  });

  test('Save issues a configurePlugin mutation carrying plugin_id empornium-megapack and BOTH field values, including an untouched prefilled one', async ({ page }) => {
    const harness = await bootSettingsHarness(page, {
      storedPlugins: {
        'empornium-megapack': {
          announceUrl: 'http://tracker.saved.net:2710/announce',
          hamsterApiKey: 'original-hamster-key',
        },
      },
    });

    await page.locator('#btn-settings').click();
    await expect(page.locator('#plugin-settings-modal')).toBeVisible();

    // Verify prefilled
    await expect(page.locator('#setting-announce-url')).toHaveValue('http://tracker.saved.net:2710/announce');
    await expect(page.locator('#setting-hamster-key')).toHaveValue('original-hamster-key');

    // Edit only hamster key; leave announce untouched
    await page.locator('#setting-hamster-key').fill('newly-updated-hamster-key');

    // Click Save
    await page.locator('#btn-save-plugin-settings').click();

    // Assert configurePlugin mutation was sent
    const calls = harness.getConfigurePluginCalls();
    expect(calls.length).toBe(1);
    expect(calls[0]).toEqual({
      plugin_id: 'empornium-megapack',
      input: {
        announceUrl: 'http://tracker.saved.net:2710/announce',
        hamsterApiKey: 'newly-updated-hamster-key',
      },
    });
  });

  test('after a successful save, /api/config/refresh is called and the config gate re-checks (banner clears / Build re-enables)', async ({ page }) => {
    const harness = await bootSettingsHarness(page, {
      healthPayload: {
        status: 'ok',
        track: 'Empornium Megapack Builder',
        version: '0.2.0',
        scratch_dir: 'C:\\Scratch',
        output_dir: 'C:\\Packs',
        hamster_configured: false,
        announce_configured: true,
      },
      storedPlugins: {
        'empornium-megapack': {
          announceUrl: 'http://tracker.test/announce',
          hamsterApiKey: '',
        },
      },
    });

    const banner = page.locator('#config-warning-banner');
    const buildBtn = page.locator('#btn-build');
    await expect(banner).toBeVisible();
    await expect(buildBtn).toBeDisabled();

    // Open settings via warning banner button
    await page.locator('#btn-config-settings').click();
    await expect(page.locator('#plugin-settings-modal')).toBeVisible();

    // Fill hamster key
    await page.locator('#setting-hamster-key').fill('fresh-hamster-key');

    // Simulate backend resolving both as configured after save/refresh
    harness.setHealth({
      hamster_configured: true,
      announce_configured: true,
    });

    // Save
    await page.locator('#btn-save-plugin-settings').click();

    // Assert dialog closed, banner cleared, and Build re-enabled
    await expect(page.locator('#plugin-settings-modal')).toBeHidden();
    await expect(banner).toBeHidden();
    await expect(buildBtn).toBeEnabled();

    // Assert /api/config/refresh was called
    expect(harness.getRefreshCalls().length).toBeGreaterThanOrEqual(1);
  });

  test('a GraphQL error on save keeps the dialog open and shows the message', async ({ page }) => {
    const harness = await bootSettingsHarness(page, {
      configurePluginError: 'Failed to write plugin configuration: permission denied',
    });

    await page.locator('#btn-settings').click();
    const modal = page.locator('#plugin-settings-modal');
    await expect(modal).toBeVisible();

    const errEl = page.locator('#plugin-settings-error');
    await expect(errEl).toBeHidden();

    // Attempt save
    await page.locator('#btn-save-plugin-settings').click();

    // Dialog must stay open, error message surfaced
    await expect(modal).toBeVisible();
    await expect(errEl).toBeVisible();
    await expect(errEl).toContainText('Failed to write plugin configuration: permission denied');
  });

  test('the per-field source warning appears when /health reports "config file" or "env" for that field, and does NOT appear when it reports "Stash plugin settings" or "not set"', async ({ page }) => {
    // 1. First run: announce from env, hamster from config file
    const harness = await bootSettingsHarness(page, {
      healthPayload: {
        status: 'ok',
        track: 'Empornium Megapack Builder',
        version: '0.2.0',
        scratch_dir: 'C:\\Scratch',
        output_dir: 'C:\\Packs',
        hamster_configured: true,
        hamster_source: 'config file',
        announce_configured: true,
        announce_source: 'env',
      },
    });

    await page.locator('#btn-settings').click();
    const modal = page.locator('#plugin-settings-modal');
    await expect(modal).toBeVisible();

    const warnAnnounce = page.locator('#source-warning-announce');
    const warnHamster = page.locator('#source-warning-hamster');

    await expect(warnAnnounce).toBeVisible();
    await expect(warnAnnounce).toContainText('env');
    await expect(warnHamster).toBeVisible();
    await expect(warnHamster).toContainText('config file');

    // Close dialog
    await page.locator('#btn-cancel-plugin-settings').click();
    await expect(modal).toBeHidden();

    // 2. Second state: announce from config file, hamster from env
    harness.setHealth({
      announce_source: 'config file',
      hamster_source: 'env',
    });

    await page.locator('#btn-settings').click();
    await expect(modal).toBeVisible();

    await expect(warnAnnounce).toBeVisible();
    await expect(warnAnnounce).toContainText('config file');
    await expect(warnHamster).toBeVisible();
    await expect(warnHamster).toContainText('env');

    // Close dialog
    await page.locator('#btn-cancel-plugin-settings').click();
    await expect(modal).toBeHidden();

    // 3. Third state: Stash plugin settings and not set -> NO warnings should appear
    harness.setHealth({
      announce_source: 'Stash plugin settings',
      hamster_source: 'not set',
    });

    await page.locator('#btn-settings').click();
    await expect(modal).toBeVisible();

    await expect(warnAnnounce).toBeHidden();
    await expect(warnHamster).toBeHidden();
  });

  test('both inputs are type="password" by default and the reveal toggle flips one to type="text"', async ({ page }) => {
    await bootSettingsHarness(page);

    await page.locator('#btn-settings').click();
    await expect(page.locator('#plugin-settings-modal')).toBeVisible();

    const announceInput = page.locator('#setting-announce-url');
    const hamsterInput = page.locator('#setting-hamster-key');
    const toggleAnnounce = page.locator('#btn-toggle-announce-url');
    const toggleHamster = page.locator('#btn-toggle-hamster-key');

    // Assert defaults are password
    await expect(announceInput).toHaveAttribute('type', 'password');
    await expect(hamsterInput).toHaveAttribute('type', 'password');

    // Toggle announce URL visibility
    await toggleAnnounce.click();
    await expect(announceInput).toHaveAttribute('type', 'text');
    await expect(hamsterInput).toHaveAttribute('type', 'password');

    // Toggle back to password
    await toggleAnnounce.click();
    await expect(announceInput).toHaveAttribute('type', 'password');

    // Toggle hamster key visibility
    await toggleHamster.click();
    await expect(hamsterInput).toHaveAttribute('type', 'text');
    await expect(announceInput).toHaveAttribute('type', 'password');

    // Toggle back
    await toggleHamster.click();
    await expect(hamsterInput).toHaveAttribute('type', 'password');
  });

  test('saving an invalid URL in the dialog keeps it open and shows the inline reason; saving a valid one closes it and clears the banner', async ({ page }) => {
    const harness = await bootSettingsHarness(page, {
      healthPayload: {
        status: 'ok',
        track: 'Empornium Megapack Builder',
        version: '0.2.0',
        scratch_dir: 'C:\\Scratch',
        output_dir: 'C:\\Packs',
        hamster_configured: true,
        announce_configured: false,
      },
      storedPlugins: {
        'empornium-megapack': {
          announceUrl: '',
          hamsterApiKey: 'valid-hamster-key',
        },
      },
    });

    const banner = page.locator('#config-warning-banner');
    const modal = page.locator('#plugin-settings-modal');
    await expect(banner).toBeVisible();

    // Open settings modal
    await page.locator('#btn-settings').click();
    await expect(modal).toBeVisible();

    const announceInput = page.locator('#setting-announce-url');
    const saveBtn = page.locator('#btn-save-plugin-settings');
    const inlineErr = page.locator('#setting-announce-error');

    // Type invalid announce URL 'test'
    await announceInput.fill('test');

    // Backend refresh mock verdict: configured but invalid
    harness.setHealth({
      announce_configured: true,
      announce_valid: false,
      announce_invalid_reason: 'Announce URL must use http or https.',
    });

    await saveBtn.click();

    // Assert: Dialog KEEPS OPEN
    await expect(modal).toBeVisible();
    // Assert: Inline error on the announce field is visible and contains reason
    await expect(inlineErr).toBeVisible();
    await expect(inlineErr).toContainText('Announce URL must use http or https.');

    // Assert: Secrets discipline — input value 'test' must not be in error message or attributes
    const errText = await inlineErr.textContent();
    expect(errText).not.toContain('test');
    await expect(inlineErr).not.toHaveAttribute('data-value', /test/);

    // Value was written to Stash before validation
    const calls = harness.getConfigurePluginCalls();
    expect(calls.length).toBe(1);
    expect(calls[0].input.announceUrl).toBe('test');

    // Now fix: type a valid URL
    const validUrl = 'http://tracker.empornium.sx:2710/secret123/secret456/announce';
    await announceInput.fill(validUrl);

    // Inline error clears on input
    await expect(inlineErr).toBeHidden();

    // Backend refresh mock verdict: valid
    harness.setHealth({
      announce_configured: true,
      announce_valid: true,
      announce_invalid_reason: '',
    });

    await saveBtn.click();

    // Assert: Dialog closes and banner is cleared
    await expect(modal).toBeHidden();
    await expect(banner).toBeHidden();
  });
});
