import { test, expect } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

function setupStaticMocks(page) {
  page.route('**/plugins/empornium-megapack/**', async (route) => {
    const url = route.request().url();
    let filePath = '';
    if (url.includes('review.html')) {
      filePath = path.join(__dirname, '../../plugin/assets/review.html');
    } else if (url.includes('review.js')) {
      filePath = path.join(__dirname, '../../plugin/assets/review.js');
    } else if (url.includes('main.js')) {
      filePath = path.join(__dirname, '../../plugin/main.js');
    } else if (url.includes('style.css')) {
      filePath = path.join(__dirname, '../../plugin/style.css');
    } else {
      return route.fallback();
    }

    const contentType = filePath.endsWith('.html')
      ? 'text/html; charset=utf-8'
      : filePath.endsWith('.js')
      ? 'application/javascript; charset=utf-8'
      : 'text/css; charset=utf-8';

    return route.fulfill({
      status: 200,
      contentType,
      body: fs.readFileSync(filePath, 'utf8')
    });
  });

  page.route('**/scenes', async (route) => {
    const html = `
      <!DOCTYPE html>
      <html>
      <head><title>Stash Scenes</title></head>
      <body>
        <div class="btn-toolbar"></div>
        <div class="scene-card" data-scene-id="101">
          <input type="checkbox" class="card-check" value="101" checked />
          <div class="title">Solo Starlet Scene</div>
        </div>
        <div class="scene-card" data-scene-id="102">
          <input type="checkbox" class="card-check" value="102" />
          <div class="title">Second Scene Duo</div>
        </div>
        <script src="http://localhost:9999/plugins/empornium-megapack/main.js"></script>
      </body>
      </html>
    `;
    return route.fulfill({
      status: 200,
      contentType: 'text/html',
      body: html
    });
  });
}

test.describe('Stage 7 Feature 2 — Single-Scene Mode Switch & Pre-Dispatch Validation Gate', () => {

  test('1. Selection of 1 scene via main.js opens modal in single mode with header, logo, and badge', async ({ page }) => {
    setupStaticMocks(page);

    const mockScene = {
      id: 101,
      title: 'Solo Starlet Scene',
      date: '2026-03-01',
      paths: { screenshot: 'http://localhost:9999/preview/101.jpg' },
      files: [{ id: 1001, path: 'C:\\Media\\Solo.mp4', size: 1024000, height: 1080, width: 1920, duration: 1800, video_codec: 'h264' }],
      performers: [{ id: 1, name: 'Star One' }],
      tags: [{ id: 1, name: '1080p' }],
      studio: { id: 10, name: 'Alpha Studio' }
    };

    await page.route('**/graphql', async (route) => {
      const postData = route.request().postDataJSON();
      if (postData?.query?.includes('FindScenes')) {
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({ data: { findScenes: { scenes: [mockScene] } } })
        });
      }
      return route.fallback();
    });

    await page.goto('http://localhost:9999/scenes');

    const btn = page.locator('#empornium-megapack-btn');
    await expect(btn).toBeVisible();
    await btn.click();

    const modal = page.locator('#empornium-megapack-modal');
    await expect(modal).toBeVisible();

    // Verify main.js state and modal header
    const mode = await page.evaluate(() => window._emporniumMode);
    expect(mode).toBe('single');

    await expect(modal.locator('.empornium-modal-title')).toContainText('Empornium Single-Scene Uploader');
    await expect(modal.locator('.empornium-logo')).toContainText('🎬');
    await expect(modal.locator('.empornium-badge')).toContainText('1 scene(s) selected');
  });

  test('2. Selection of 2+ scenes via main.js opens modal in megapack mode with header, logo, and badge', async ({ page }) => {
    setupStaticMocks(page);

    await page.goto('http://localhost:9999/scenes');

    // Select second checkbox so 2 scenes are selected
    await page.locator('input.card-check[value="102"]').check();

    const btn = page.locator('#empornium-megapack-btn');
    await btn.click();

    const modal = page.locator('#empornium-megapack-modal');
    await expect(modal).toBeVisible();

    const mode = await page.evaluate(() => window._emporniumMode);
    expect(mode).toBe('megapack');

    await expect(modal.locator('.empornium-modal-title')).toContainText('DeepSeek Megapack Builder');
    await expect(modal.locator('.empornium-logo')).toContainText('📦');
    await expect(modal.locator('.empornium-badge')).toContainText('2 scene(s) selected');
  });

  test('3. Review UI renders in single mode: switcher state, labels, helper text, hidden consolidation button and banner', async ({ page }) => {
    setupStaticMocks(page);

    const mockScene = {
      id: 101,
      title: 'Solo Starlet Scene',
      date: '2026-03-01',
      paths: { screenshot: 'http://localhost:9999/preview/101.jpg' },
      files: [{ id: 1001, path: 'C:\\Packs\\Solo.mp4', size: 1024000, height: 1080, width: 1920, duration: 1800, video_codec: 'h264' }],
      performers: [{ id: 1, name: 'Star One' }, { id: 2, name: 'Star Two' }],
      tags: [{ id: 1, name: '4K' }],
      studio: { id: 10, name: 'Alpha Studio' }
    };

    await page.route('**/graphql', async (route) => {
      const postData = route.request().postDataJSON();
      if (postData?.query?.includes('FindScenes')) {
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({ data: { findScenes: { scenes: [mockScene] } } })
        });
      }
      return route.fallback();
    });

    await page.goto('http://localhost:9999/plugins/empornium-megapack/review.html?scenes=101&mode=single');
    await page.locator("#output-dir").fill("C:\\Packs");

    // 1. Header Mode Switcher Toggle
    const radioSingle = page.locator('#mode-single');
    const radioMegapack = page.locator('#mode-megapack');
    await expect(radioSingle).toBeChecked();
    await expect(radioSingle).toBeEnabled();
    await expect(radioMegapack).toBeDisabled();
    await expect(page.locator('#label-mode-megapack')).toHaveAttribute('title', 'Megapack mode requires 2 or more scenes');

    // 2. Labels and Defaults
    await expect(page.locator('#label-pack-title')).toHaveText('Release Title');
    await expect(page.locator('#pack-title')).toHaveValue('Solo Starlet Scene');

    await expect(page.locator('#label-output-dir')).toHaveText('Artifact Output Directory');
    await expect(page.locator('#output-dir-helper')).toBeVisible();
    await expect(page.locator('#output-dir-helper')).toContainText('Torrent, BBCode and contact sheet are written here. Your media file is not moved.');

    // 3. Action Buttons & Visibility
    await expect(page.locator('#btn-build')).toHaveText('🚀 Build Single Scene');
    await expect(page.locator('#btn-build')).toBeEnabled();

    await expect(page.locator('#group-consolidate')).toBeHidden();
    await expect(page.locator('#collision-banner')).toBeHidden();
    await expect(page.locator('#opt-upload-previews')).toBeVisible();
    await expect(page.locator('#opt-upload-previews')).toBeEnabled();
  });

  test('4. Single-scene BBCode live preview emits scene-shaped formatting without numbered list or total count', async ({ page }) => {
    setupStaticMocks(page);

    const mockScene = {
      id: 101,
      title: 'Solo Starlet Scene',
      date: '2026-03-01',
      paths: { screenshot: 'http://localhost:9999/preview/101.jpg' },
      files: [{ id: 1001, path: 'C:\\Media\\Solo.mp4', size: 1024000, height: 1080, width: 1920, duration: 1800, video_codec: 'h264' }],
      performers: [{ id: 1, name: 'Star One' }, { id: 2, name: 'Star Two' }],
      tags: [{ id: 1, name: 'Feature' }, { id: 2, name: '4K' }],
      studio: { id: 10, name: 'Alpha Studio' }
    };

    await page.route('**/graphql', async (route) => {
      const postData = route.request().postDataJSON();
      if (postData?.query?.includes('FindScenes')) {
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({ data: { findScenes: { scenes: [mockScene] } } })
        });
      }
      return route.fallback();
    });

    await page.goto('http://localhost:9999/plugins/empornium-megapack/review.html?scenes=101&mode=single');

    await page.locator('#pack-notes').fill('Special single-scene release notes!');

    const bbcode = await page.locator('#bbcode-preview').innerText();

    // Verify Scene Title + Badges
    expect(bbcode).toContain('[center][b][size=5]Solo Starlet Scene [1080p] [30m 0s][/size][/b][/center]');
    expect(bbcode).toContain('[b]Studio:[/b] Alpha Studio');
    expect(bbcode).toContain('[b]Performers:[/b] Star One, Star Two');
    expect(bbcode).toContain('[b]Tags:[/b] Feature, 4K');
    expect(bbcode).toContain('[quote]Special single-scene release notes![/quote]');

    // Verify Megapack-specific breakdown is NOT present
    expect(bbcode).not.toContain('Total Scenes:');
    expect(bbcode).not.toContain('1. [b]');
    expect(bbcode).not.toContain('Scenes Included:');
  });

  test('5. Pre-dispatch validation gate in single mode: blocks on 0 media files and >1 media files', async ({ page }) => {
    setupStaticMocks(page);

    let currentSceneFiles = [];

    await page.route('**/graphql', async (route) => {
      const postData = route.request().postDataJSON();
      if (postData?.query?.includes('FindScenes')) {
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            data: {
              findScenes: {
                scenes: [
                  {
                    id: 101,
                    title: 'Zero File Scene',
                    paths: {},
                    files: currentSceneFiles,
                    performers: [],
                    tags: []
                  }
                ]
              }
            }
          })
        });
      }
      return route.fallback();
    });

    // 1. Zero media files
    currentSceneFiles = [];
    await page.goto('http://localhost:9999/plugins/empornium-megapack/review.html?scenes=101&mode=single');
    await page.locator("#output-dir").fill("C:\\Packs");

    const buildBtn = page.locator('#btn-build');
    await expect(buildBtn).toBeDisabled();
    await expect(buildBtn).toHaveAttribute('title', 'Selected scene has no valid media file');

    // Direct buildMegapack() execution aborts cleanly
    await page.evaluate(() => window.buildMegapack());
    await expect(page.locator('#status-text')).toContainText('Build aborted: Single Scene mode requires exactly 1 media file');

    // 2. Multi-file scene (>1 files)
    currentSceneFiles = [
      { id: 1001, path: 'C:\\Media\\Part1.mp4' },
      { id: 1002, path: 'C:\\Media\\Part2.mp4' }
    ];
    await page.evaluate(() => window.loadScenes([101]));
    await expect(buildBtn).toBeDisabled();
    await expect(buildBtn).toHaveAttribute('title', 'Single Scene mode requires exactly 1 media file (found 2)');

    await page.evaluate(() => window.buildMegapack());
    await expect(page.locator('#status-text')).toContainText('Build aborted: Single Scene mode requires exactly 1 media file (found 2)');

    // 3. Exactly 1 valid media file -> enabled (file under the seed dir —
    // todo 7 of staged-wizard-inplace-seed gates single mode on containment)
    currentSceneFiles = [{ id: 1001, path: 'C:\\Packs\\Solo.mp4' }];
    await page.evaluate(() => window.loadScenes([101]));
    await expect(buildBtn).toBeEnabled();
    await expect(buildBtn).toHaveAttribute('title', 'Build single-scene torrent, contact sheet, and BBCode');
  });

  test('6. BuildSingleScene task dispatch payload formatting and type compliance', async ({ page }) => {
    setupStaticMocks(page);

    const mockScene = {
      id: 101,
      title: 'Solo Scene Dispatch',
      date: '2026-03-01',
      paths: { screenshot: 'http://localhost:9999/preview/101.jpg' },
      files: [{ id: 1001, path: 'C:\\Packs\\Solo.mp4', size: 1024000, height: 2160, width: 3840, duration: 3600, video_codec: 'hevc' }],
      performers: [{ id: 1, name: 'Soloist' }],
      tags: [{ id: 1, name: '4K' }],
      studio: { id: 10, name: 'Dispatch Studio' }
    };

    let buildCapturedTask = null;
    let buildCapturedArgs = null;

    // Build pre-flight (todo 7 of staged-wizard-inplace-seed): the
    // authoritative on-disk probe must succeed or the build is blocked
    // fail-closed before dispatch.
    await page.route('**/api/fs/exists', async (route) => {
      const postData = route.request().postDataJSON();
      const results = {};
      for (const p of postData.paths || []) results[p] = true;
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ results })
      });
    });

    await page.route('**/graphql', async (route) => {
      const postData = route.request().postDataJSON();

      if (postData?.query?.includes('FindScenes')) {
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({ data: { findScenes: { scenes: [mockScene] } } })
        });
      }

      if (postData?.query?.includes('RunBuild')) {
        buildCapturedTask = postData.variables?.task_name;
        buildCapturedArgs = postData.variables?.args;
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({ data: { runPluginTask: 'job-single-123' } })
        });
      }

      return route.fallback();
    });

    await page.goto('http://localhost:9999/plugins/empornium-megapack/review.html?scenes=101&mode=single');
    await page.locator("#output-dir").fill("C:\\Packs");

    await page.locator('#opt-upload-previews').check();
    await page.locator('#btn-build').click();

    // The dispatch is now two async round-trips (pre-flight probe -> mutation),
    // so poll instead of asserting immediately after the click.
    await expect.poll(() => buildCapturedTask).toBe('BuildSingleScene');
    expect(buildCapturedArgs).toBeTruthy();

    const modeArg = buildCapturedArgs.find((a) => a.key === 'mode');
    expect(modeArg?.value?.str).toBe('single');

    const payloadArg = buildCapturedArgs.find((a) => a.key === 'payload');
    expect(payloadArg).toBeTruthy();
    const payload = JSON.parse(payloadArg.value.str);

    expect(payload.single_scene).toBe(true);
    expect(payload.pack_title).toBe('Solo Scene Dispatch');
    expect(payload.upload_previews).toBe(true);
    expect(payload.scenes).toHaveLength(1);
    expect(payload.scenes[0].id).toBe(101);
    expect(payload.scenes[0].path).toBe('C:\\Packs\\Solo.mp4');
    expect(payload.scenes[0].height).toBe(2160);
    expect(payload.scenes[0].duration).toBe(3600);
    expect(payload.scenes[0].video_codec).toBe('hevc');
  });

  test('7. onTaskComplete handoff rendering for BuildSingleScene', async ({ page }) => {
    setupStaticMocks(page);

    const mockScene = {
      id: 101,
      title: 'Handoff Single Scene',
      date: '2026-03-01',
      paths: { screenshot: 'http://localhost:9999/preview/101.jpg' },
      files: [{ id: 1001, path: 'C:\\Media\\Solo.mp4', size: 1024000, height: 1080, width: 1920, duration: 1800, video_codec: 'h264' }],
      performers: [{ id: 1, name: 'Soloist' }],
      tags: [{ id: 1, name: '4k' }],
      studio: { id: 10, name: 'Studio One' }
    };

    await page.route('**/graphql', async (route) => {
      const postData = route.request().postDataJSON();
      if (postData?.query?.includes('FindScenes')) {
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({ data: { findScenes: { scenes: [mockScene] } } })
        });
      }
      return route.fallback();
    });

    await page.goto('http://localhost:9999/plugins/empornium-megapack/review.html?scenes=101&mode=single');

    // Trigger onTaskComplete with BuildSingleScene payload
    await page.evaluate(() => {
      window.onTaskComplete('BuildSingleScene', {
        pack_title: 'Handoff Single Scene',
        output_dir: 'C:\\Packs',
        torrent_path: 'C:\\Packs\\Handoff Single Scene.torrent',
        manifest_path: 'C:\\Packs\\Handoff Single Scene_manifest.json',
        submission_path: 'C:\\Packs\\Handoff Single Scene_submission.json',
        upload_previews: true,
        preview_only: false,
        ready: true,
        tracker_tags: ['soloist', '4k'],
        site_url: 'https://www.empornium.is'
      });
    });

    const summaryBox = page.locator('#artifact-summary');
    await expect(summaryBox).toBeVisible();

    await expect(page.locator('#handoff-status-header')).toContainText('🎉 Build Complete! — Ready for Manual Upload');
    await expect(page.locator('#handoff-title')).toHaveText('Handoff Single Scene');
    await expect(page.locator('#handoff-tags')).toHaveText('soloist 4k');
    await expect(page.locator('#handoff-torrent')).toHaveText('C:\\Packs\\Handoff Single Scene.torrent');

    // Preflight checklist items
    const checklist = page.locator('#preflight-checklist');
    await expect(checklist).toContainText('Media Files Verification: Single media file exists on disk');
    await expect(checklist).toContainText('Torrent Name: Single-file torrent — tracker displays media filename');

    // Empornium link
    const uploadLink = page.locator('#btn-open-upload');
    await expect(uploadLink).toBeVisible();
    await expect(uploadLink).toHaveAttribute('href', 'https://www.empornium.is/upload.php');
  });

  test('8. Release Title fallback in single-scene mode when scene title is blank or whitespace', async ({ page }) => {
    setupStaticMocks(page);

    const mockScene = {
      id: 401,
      title: '   ', // blank / whitespace title
      paths: {},
      files: [{ id: 4001, path: 'D:\\Incoming\\Stash_VIP_Feature_2026.mp4', height: 1080, duration: 2400 }],
      performers: [{ id: 1, name: 'VIP Performer' }],
      tags: [{ id: 2, name: 'Exclusive' }]
    };

    await page.route('**/graphql', async (route) => {
      const postData = route.request().postDataJSON();
      if (postData?.query?.includes('FindScenes')) {
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({ data: { findScenes: { scenes: [mockScene] } } })
        });
      }
      return route.fallback();
    });

    await page.goto('http://localhost:9999/plugins/empornium-megapack/review.html?scenes=401&mode=single');

    // Title input should default from sanitized basename
    await expect(page.locator('#pack-title')).toHaveValue('Stash_VIP_Feature_2026');

    // BBCode preview should carry the fallback title
    const bbcode = await page.locator('#bbcode-preview').innerText();
    expect(bbcode).toContain('[center][b][size=5]Stash_VIP_Feature_2026 [1080p] [40m 0s][/size][/b][/center]');
    expect(bbcode).toContain('[b]Performers:[/b] VIP Performer');
    expect(bbcode).toContain('[b]Tags:[/b] Exclusive');
  });

  test('9. Direct URL load review.html?scenes=501 without mode param infers single-scene mode automatically', async ({ page }) => {
    setupStaticMocks(page);

    const mockScene = {
      id: 501,
      title: 'Direct URL Single Scene',
      paths: {},
      files: [{ id: 5001, path: 'C:\\Packs\\DirectScene.mp4', height: 1080, duration: 1200 }],
      performers: [{ id: 1, name: 'Direct Actor' }],
      tags: []
    };

    await page.route('**/graphql', async (route) => {
      const postData = route.request().postDataJSON();
      if (postData?.query?.includes('FindScenes')) {
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({ data: { findScenes: { scenes: [mockScene] } } })
        });
      }
      return route.fallback();
    });

    // Navigate with &mode=single parameter
    await page.goto('http://localhost:9999/plugins/empornium-megapack/review.html?scenes=501&mode=single');
    await page.locator("#output-dir").fill("C:\\Packs");

    const radioSingle = page.locator('#mode-single');
    const radioMegapack = page.locator('#mode-megapack');
    await expect(radioSingle).toBeChecked();
    await expect(radioSingle).toBeEnabled();
    await expect(radioMegapack).toBeDisabled();

    await expect(page.locator('#btn-build')).toHaveText('🚀 Build Single Scene');
    await expect(page.locator('#btn-build')).toBeEnabled();
    await expect(page.locator('#label-pack-title')).toHaveText('Release Title');
    await expect(page.locator('#pack-title')).toHaveValue('Direct URL Single Scene');
  });

  test('10. Pre-dispatch validation gate in megapack mode: duplicate collisions disable build and abort direct buildMegapack() call', async ({ page }) => {
    setupStaticMocks(page);

    const mockScenes = [
      {
        id: 601,
        title: 'Scene Alpha',
        files: [{ id: 6001, path: 'D:\\SourceA\\alpha.mp4' }],
        performers: [],
        tags: []
      },
      {
        id: 602,
        title: 'Scene Beta',
        files: [{ id: 6002, path: 'D:\\SourceB\\alpha.mp4' }],
        performers: [],
        tags: []
      }
    ];

    let taskDispatched = false;

    await page.route('**/graphql', async (route) => {
      const postData = route.request().postDataJSON();
      if (postData?.query?.includes('FindScenes')) {
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({ data: { findScenes: { scenes: mockScenes } } })
        });
      }
      if (postData?.query?.includes('RunBuild')) {
        taskDispatched = true;
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({ data: { runPluginTask: 'job-collision-fail' } })
        });
      }
      return route.fallback();
    });

    await page.goto('http://localhost:9999/plugins/empornium-megapack/review.html?scenes=601,602&mode=megapack');

    const buildBtn = page.locator('#btn-build');
    await expect(buildBtn).toBeDisabled();
    await expect(buildBtn).toHaveAttribute('title', '1 filename collision must be resolved first');

    // Attempt direct buildMegapack() execution — must abort cleanly without GraphQL task dispatch
    await page.evaluate(() => window.buildMegapack());
    expect(taskDispatched).toBe(false);

    const statusText = page.locator('#status-text');
    await expect(statusText).toContainText('Build aborted: 1 unresolved filename collision(s)');
  });

  test('11. Dynamic scene removal and restoration in single-scene mode updates buttons and empty state', async ({ page }) => {
    setupStaticMocks(page);

    const mockScene = {
      id: 701,
      title: 'Removable Solo Scene',
      files: [{ id: 7001, path: 'C:\\Packs\\SoloRemovable.mp4' }],
      performers: [{ id: 1, name: 'Soloist' }],
      tags: []
    };

    await page.route('**/graphql', async (route) => {
      const postData = route.request().postDataJSON();
      if (postData?.query?.includes('FindScenes')) {
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({ data: { findScenes: { scenes: [mockScene] } } })
        });
      }
      return route.fallback();
    });

    await page.goto('http://localhost:9999/plugins/empornium-megapack/review.html?scenes=701&mode=single');
    await page.locator("#output-dir").fill("C:\\Packs");

    const buildBtn = page.locator('#btn-build');
    await expect(buildBtn).toBeEnabled();

    // Remove the single scene
    await page.locator('.scene-remove-btn').click();

    // Scene list shows empty state
    await expect(page.locator('#scene-list')).toContainText('All scenes have been removed from the pack.');
    await expect(buildBtn).toBeDisabled();
    await expect(buildBtn).toHaveAttribute('title', 'No scenes selected');

    // Direct buildMegapack() call aborts cleanly
    await page.evaluate(() => window.buildMegapack());
    await expect(page.locator('#status-text')).toContainText('Build aborted: No active scenes in selection.');

    // Restore scenes
    await page.locator('#btn-restore-all').click();
    await expect(buildBtn).toBeEnabled();
    await expect(page.locator('.scene-title')).toContainText('#1 - Removable Solo Scene');
  });

  test('12. Mode Switcher options validation: Megapack mode disables Single Scene option when 2+ scenes are selected', async ({ page }) => {
    setupStaticMocks(page);

    const mockScenes = [
      { id: 801, title: 'Multi Scene 1', files: [{ id: 8001, path: 'C:\\Packs\\scene1.mp4' }], performers: [], tags: [] },
      { id: 802, title: 'Multi Scene 2', files: [{ id: 8002, path: 'C:\\Packs\\scene2.mp4' }], performers: [], tags: [] }
    ];

    await page.route('**/graphql', async (route) => {
      const postData = route.request().postDataJSON();
      if (postData?.query?.includes('FindScenes')) {
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({ data: { findScenes: { scenes: mockScenes } } })
        });
      }
      return route.fallback();
    });

    await page.goto('http://localhost:9999/plugins/empornium-megapack/review.html?scenes=801,802&mode=megapack');

    const radioMegapack = page.locator('#mode-megapack');
    const radioSingle = page.locator('#mode-single');
    const labelSingle = page.locator('#label-mode-single');

    await expect(radioMegapack).toBeChecked();
    await expect(radioMegapack).toBeEnabled();
    await expect(radioSingle).toBeDisabled();
    await expect(labelSingle).toHaveAttribute('title', 'Single Scene mode requires exactly 1 scene');

    // Remove one scene so exactly 1 scene remains
    await page.locator('.scene-remove-btn').first().click();

    // Now Single mode should become enabled and Megapack disabled
    await expect(radioSingle).toBeEnabled();
    await expect(radioMegapack).toBeDisabled();
    await expect(page.locator('#label-mode-megapack')).toHaveAttribute('title', 'Megapack mode requires 2 or more scenes');
  });

  test('13. Pre-dispatch validation gate in megapack mode: files outside the seed dir disable build and abort direct buildMegapack() call', async ({ page }) => {
    setupStaticMocks(page);

    const unconsolidatedScenes = [
      {
        id: 901,
        title: 'Unconsolidated Scene 1',
        files: [{ id: 9001, path: 'D:\\SourceA\\scene1.mp4' }],
        performers: [],
        tags: []
      },
      {
        id: 902,
        title: 'Unconsolidated Scene 2',
        files: [{ id: 9002, path: 'E:\\SourceB\\scene2.mp4' }],
        performers: [],
        tags: []
      }
    ];

    let taskDispatched = false;

    await page.route('**/graphql', async (route) => {
      const postData = route.request().postDataJSON();
      if (postData?.query?.includes('FindScenes')) {
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({ data: { findScenes: { scenes: unconsolidatedScenes } } })
        });
      }
      if (postData?.query?.includes('RunBuild')) {
        taskDispatched = true;
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({ data: { runPluginTask: 'job-unconsolidated-fail' } })
        });
      }
      return route.fallback();
    });

    await page.goto('http://localhost:9999/plugins/empornium-megapack/review.html?scenes=901,902&mode=megapack');

    const buildBtn = page.locator('#btn-build');
    const consolidateBtn = page.locator('#btn-consolidate');

    // Consolidate is enabled, but Build is disabled because the files are not
    // under the seed dir (C:\Packs). OLD (pre-todo-7): "2 file(s) must be
    // consolidated into destination directory first". NEW: the exact
    // missing-file list.
    await expect(consolidateBtn).toBeEnabled();
    await expect(buildBtn).toBeDisabled();
    await expect(buildBtn).toHaveAttribute('title', '2 file(s) missing from the seed directory: scene1.mp4, scene2.mp4. Run Consolidate or add the missing files.');

    // Attempt direct buildMegapack() call — must abort cleanly without dispatch
    await page.evaluate(() => window.buildMegapack());
    expect(taskDispatched).toBe(false);
    await expect(page.locator('#status-text')).toContainText('Build aborted: 2 file(s) missing from the seed directory: scene1.mp4, scene2.mp4');

    // Dynamically changing output-dir to D:\SourceA leaves 1 file missing
    // from the seed dir (E:\SourceB\scene2.mp4)
    await page.locator('#output-dir').fill('D:\\SourceA');
    await expect(buildBtn).toBeDisabled();
    await expect(buildBtn).toHaveAttribute('title', '1 file(s) missing from the seed directory: scene2.mp4. Run Consolidate or add the missing files.');
  });

  test('14. Single-scene mode in-place building: media file under the seed dir builds and the payload carries seed_dir', async ({ page }) => {
    setupStaticMocks(page);

    // OLD intent (pre-todo-7): "media file OUTSIDE the artifact output
    // directory remains buildable" — obsolete under in-place seeding: the
    // single scene's primary must sit under the seed dir (recursive), and the
    // dispatched payload now carries seed_dir.
    const soloScene = {
      id: 951,
      title: 'In-Place Solo Scene',
      files: [{ id: 9501, path: 'C:\\Packs\\solo_in_place.mp4' }],
      performers: [{ id: 1, name: 'Soloist' }],
      tags: []
    };

    let buildDispatchedPayload = null;

    // Build pre-flight (todo 7 of staged-wizard-inplace-seed): the
    // authoritative on-disk probe must succeed or the build is blocked
    // fail-closed before dispatch.
    await page.route('**/api/fs/exists', async (route) => {
      const postData = route.request().postDataJSON();
      const results = {};
      for (const p of postData.paths || []) results[p] = true;
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ results })
      });
    });

    await page.route('**/graphql', async (route) => {
      const postData = route.request().postDataJSON();
      if (postData?.query?.includes('FindScenes')) {
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({ data: { findScenes: { scenes: [soloScene] } } })
        });
      }
      if (postData?.query?.includes('RunBuild')) {
        const payloadStr = postData.variables?.args?.find((a) => a.key === 'payload')?.value?.str;
        buildDispatchedPayload = JSON.parse(payloadStr);
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({ data: { runPluginTask: 'job-single-inplace' } })
        });
      }
      return route.fallback();
    });

    await page.goto('http://localhost:9999/plugins/empornium-megapack/review.html?scenes=951&mode=single');
    // Seed-dir field starts EMPTY (no machine-path default) — set it explicitly.
    await page.locator('#output-dir').fill('C:\\Packs');

    const buildBtn = page.locator('#btn-build');
    await expect(buildBtn).toBeEnabled();
    await expect(buildBtn).toHaveAttribute('title', 'Build single-scene torrent, contact sheet, and BBCode');

    // Build dispatches successfully
    await buildBtn.click();
    await expect.poll(() => buildDispatchedPayload).toBeTruthy();
    expect(buildDispatchedPayload.single_scene).toBe(true);
    expect(buildDispatchedPayload.seed_dir).toBe('C:\\Packs');
    // OLD (todo 7): payload.output_dir mirrored the seed dir.
    // NEW (todo 8): output_dir is dropped from the UI payload; task.py's
    // legacy fallback covers old payloads only.
    expect(buildDispatchedPayload.output_dir).toBeUndefined();
    expect(buildDispatchedPayload.scenes[0].path).toBe('C:\\Packs\\solo_in_place.mp4');
  });

  test('15. Single-scene consolidation affordance is fully suppressed', async ({ page }) => {
    setupStaticMocks(page);

    const soloScene = {
      id: 961,
      title: 'No Consolidation Solo Scene',
      files: [{ id: 9601, path: 'C:\\Media\\solo.mp4' }],
      performers: [],
      tags: []
    };

    await page.route('**/graphql', async (route) => {
      const postData = route.request().postDataJSON();
      if (postData?.query?.includes('FindScenes')) {
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({ data: { findScenes: { scenes: [soloScene] } } })
        });
      }
      return route.fallback();
    });

    await page.goto('http://localhost:9999/plugins/empornium-megapack/review.html?scenes=961&mode=single');

    // Group consolidate is hidden
    await expect(page.locator('#group-consolidate')).toBeHidden();

    // Direct invocation or check of btn-consolidate
    const btnConsolidate = page.locator('#btn-consolidate');
    await expect(btnConsolidate).toBeDisabled();
    await expect(btnConsolidate).toHaveAttribute('title', 'Consolidation is not used in Single Scene mode');
  });

});
