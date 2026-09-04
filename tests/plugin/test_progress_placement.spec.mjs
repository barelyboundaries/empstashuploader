import { test, expect } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

// Change A — Move the progress bar above the BBCode preview:
//
//   - Hoist the BBCode Preview .form-group out of #stage-panel-4 into a new
//     sibling <div id="bbcode-section"> placed after #progress-section.
//   - #progress-section and #bbcode-section must both sit outside all [data-stage-panel]
//     containers inside .options-panel.
//   - Final order inside .options-panel: stage-panel-1, stage-panel-2, stage-panel-4,
//     progress-section, bbcode-section.
//   - All BBCode control IDs (#bbcode-preview, #bbcode-warning, #bbcode-edited-notice,
//     #bbcode-toolbar, #btn-copy-bbcode, #presentation-size-line) and relative order
//     are preserved verbatim.
//   - .progress-bar.indeterminate CSS keyframe sweep applied in showStatus() when
//     progress < 0.02, removed when a real progress value arrives or on completion/error.
//   - #progress-section scrolled into view once per operation via a module-level flag.
//   - Button labels swapped at dispatch (Build -> ⏳ Building…, Probe -> ⏳ Probing…,
//     Consolidate -> ⏳ Consolidating…) and restored on completion via dataset.idleLabel.

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
  page.route('**/health', async (route) => route.abort('connectionrefused'));
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

async function bootHarness(page, { scenes = [scene(1, 101, 'C:\\Packs\\scene1.mp4', 'Scene 1')], mode = 'megapack' } = {}) {
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
  await page.locator('#output-dir').fill('C:\\Packs');
  await page.locator('#scratch-dir').fill('C:\\Scratch');
  return { wire };
}

test.describe('Change A — Progress placement, indeterminate styling, and button feedback', () => {

  test('1. DOM layout: #progress-section precedes #bbcode-section via compareDocumentPosition and neither is in [data-stage-panel]', async ({ page }) => {
    await bootHarness(page);

    const check = await page.evaluate(() => {
      const progressSection = document.getElementById('progress-section');
      const bbcodeSection = document.getElementById('bbcode-section');

      if (!progressSection || !bbcodeSection) {
        return { error: 'Missing #progress-section or #bbcode-section' };
      }

      // compareDocumentPosition bit 4 (Node.DOCUMENT_POSITION_FOLLOWING) means bbcodeSection follows progressSection
      const comp = progressSection.compareDocumentPosition(bbcodeSection);
      const progressPrecedesBbcode = Boolean(comp & Node.DOCUMENT_POSITION_FOLLOWING);

      // Verify neither is a descendant of any [data-stage-panel]
      const progressPanelParent = progressSection.closest('[data-stage-panel]');
      const bbcodePanelParent = bbcodeSection.closest('[data-stage-panel]');

      // Verify direct child order inside .options-panel
      const optionsPanel = document.querySelector('.options-panel');
      const childIds = optionsPanel ? Array.from(optionsPanel.children).map((c) => c.id) : [];

      return {
        progressPrecedesBbcode,
        progressInStagePanel: progressPanelParent !== null,
        bbcodeInStagePanel: bbcodePanelParent !== null,
        childIds,
      };
    });

    expect(check.error).toBeUndefined();
    expect(check.progressPrecedesBbcode).toBe(true);
    expect(check.progressInStagePanel).toBe(false);
    expect(check.bbcodeInStagePanel).toBe(false);

    // Final order inside .options-panel: busy-banner (Change B), stage-panel-1, stage-panel-2, stage-panel-4, progress-section, bbcode-section
    expect(check.childIds).toEqual([
      'busy-banner',
      'stage-panel-1',
      'stage-panel-2',
      'stage-panel-4',
      'progress-section',
      'bbcode-section',
    ]);
  });

  test('2. DOM elements integrity: all BBCode controls retain their IDs and relative order inside #bbcode-section', async ({ page }) => {
    await bootHarness(page);

    const bbcodeElements = await page.evaluate(() => {
      const section = document.getElementById('bbcode-section');
      if (!section) return null;
      return {
        copyBtn: Boolean(section.querySelector('#btn-copy-bbcode')),
        presentationSize: Boolean(section.querySelector('#presentation-size-line')),
        warning: Boolean(section.querySelector('#bbcode-warning')),
        editedNotice: Boolean(section.querySelector('#bbcode-edited-notice')),
        resetBtn: Boolean(section.querySelector('#btn-bbcode-reset')),
        toolbar: Boolean(section.querySelector('#bbcode-toolbar')),
        textarea: Boolean(section.querySelector('#bbcode-preview')),
      };
    });

    expect(bbcodeElements).toEqual({
      copyBtn: true,
      presentationSize: true,
      warning: true,
      editedNotice: true,
      resetBtn: true,
      toolbar: true,
      textarea: true,
    });
  });

  test('3. Indeterminate state: applied in showStatus() when progress < 0.02, removed when real progress arrives or on error', async ({ page }) => {
    await bootHarness(page);

    const bar = page.locator('#progress-bar');

    // Initially progress < 0.02 (e.g. 0) -> indeterminate class applied
    await page.evaluate(() => window.showStatus('Starting task…', 0));
    await expect(bar).toHaveClass(/indeterminate/);

    await page.evaluate(() => window.showStatus('Queued…', 0.01));
    await expect(bar).toHaveClass(/indeterminate/);

    // Real progress arrives (>= 0.02) -> indeterminate class removed
    await page.evaluate(() => window.showStatus('Running: 25%', 0.25));
    await expect(bar).not.toHaveClass(/indeterminate/);

    // Error arrives -> indeterminate class removed
    await page.evaluate(() => window.showStatus('Starting again…', 0));
    await expect(bar).toHaveClass(/indeterminate/);
    await page.evaluate(() => window.showStatus('Task failed', 0, true));
    await expect(bar).not.toHaveClass(/indeterminate/);
  });

  test('4. Button label swapping and restoration: Build, Probe, and Consolidate swap to spinner verbs and restore original text', async ({ page }) => {
    await bootHarness(page);

    const btnBuild = page.locator('#btn-build');
    const btnProbe = page.locator('#btn-probe');
    const btnConsolidate = page.locator('#btn-consolidate');

    // Initial labels
    await expect(btnBuild).toHaveText('🚀 Build Megapack');
    await expect(btnProbe).toHaveText('🔍 Probe Filesystem');
    await expect(btnConsolidate).toHaveText('📁 Consolidate Files via Stash GraphQL');

    // Click probe -> swaps to ⏳ Probing…
    await btnProbe.click();
    await expect(btnProbe).toHaveText('⏳ Probing…');
    const probeIdle = await btnProbe.getAttribute('data-idle-label');
    expect(probeIdle).toBe('🔍 Probe Filesystem');

    // Restore via job completion
    await page.evaluate(() => {
      window.handleJobUpdate({ id: 'job-ProbeFiles-1', status: 'FINISHED' }, 'ProbeFiles', {});
    });
    await expect(btnProbe).toHaveText('🔍 Probe Filesystem');
    await expect(btnProbe).not.toHaveAttribute('data-idle-label');

    // Click build -> swaps to ⏳ Building…
    await page.locator('#pack-title').fill('My Megapack');
    await btnBuild.click();
    await expect(btnBuild).toHaveText('⏳ Building…');
    const buildIdle = await btnBuild.getAttribute('data-idle-label');
    expect(buildIdle).toBe('🚀 Build Megapack');

    // Restore via job terminal error
    await page.evaluate(() => {
      window.handleJobUpdate({ id: 'job-BuildMegapack-1', status: 'FAILED', error: 'simulated failure' }, 'BuildMegapack', {});
    });
    await expect(btnBuild).toHaveText('🚀 Build Megapack');
    await expect(btnBuild).not.toHaveAttribute('data-idle-label');
  });

  test('5. Scroll #progress-section into view occurs ONCE per operation, tracked by module-level flag', async ({ page }) => {
    await bootHarness(page);

    // Spy on scrollIntoView for #progress-section
    await page.evaluate(() => {
      window._progressScrollCalls = 0;
      const el = document.getElementById('progress-section');
      const originalScroll = el.scrollIntoView.bind(el);
      el.scrollIntoView = function (options) {
        window._progressScrollCalls++;
        return originalScroll(options);
      };
    });

    // Start operation by clicking Probe
    await page.locator('#btn-probe').click();
    const callsAfterDispatch = await page.evaluate(() => window._progressScrollCalls);
    expect(callsAfterDispatch).toBe(1);

    // Multiple progress updates arrive during the operation -> scrollIntoView is NOT called again
    await page.evaluate(() => {
      window.showStatus('Running ProbeFiles: 10%', 0.1);
      window.showStatus('Running ProbeFiles: 50%', 0.5);
      window.showStatus('Running ProbeFiles: 90%', 0.9);
    });
    const callsDuringUpdates = await page.evaluate(() => window._progressScrollCalls);
    expect(callsDuringUpdates).toBe(1);

    // Finish job
    await page.evaluate(() => {
      window.handleJobUpdate({ id: 'job-ProbeFiles-1', status: 'FINISHED' }, 'ProbeFiles', {});
    });

    // Next operation can scroll again once
    await page.locator('#btn-build').click();
    const callsAfterSecondDispatch = await page.evaluate(() => window._progressScrollCalls);
    expect(callsAfterSecondDispatch).toBe(2);
  });
});
