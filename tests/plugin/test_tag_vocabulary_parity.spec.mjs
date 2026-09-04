import { test, expect } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

// Parity guard: review.js resolveTags must produce identical output to
// backend/empornium_megapack/tags.py resolve_tags(), which is authoritative.
//
// Authoritative Python outputs (recorded from python importing empornium_megapack.tags):
//
//   resolve_tags([TagSource("Brown Hair", "scene_tag")])
//     -> tags: ["brown.hair", "brunette"], unmapped: [], ignored: []
//   resolve_tags([TagSource("Blowjob", "scene_tag")])
//     -> tags: ["blowjob"], unmapped: [], ignored: []
//   resolve_tags([TagSource("bLoWjOb", "scene_tag")])
//     -> tags: ["blowjob"], unmapped: [], ignored: []
//   resolve_tags([TagSource("4K Available", "scene_tag")])
//     -> tags: [], unmapped: [], ignored: ["4K Available"]
//   resolve_tags([TagSource("Missing Date", "scene_tag")])
//     -> tags: [], unmapped: [], ignored: ["Missing Date"]
//   resolve_tags([TagSource("Custom Tag 123", "scene_tag")])
//     -> tags: [], unmapped: ["Custom Tag 123"], ignored: []
//   resolve_tags([TagSource("Pamela Anderson", "performer")])
//     -> tags: ["pamela.anderson"], unmapped: [], ignored: []
//   resolve_tags([TagSource("Evil Angel", "studio")])
//     -> tags: ["evil.angel"], unmapped: [], ignored: []
//   resolve_tags([TagSource("1080p", "derived")])
//     -> tags: ["1080p"], unmapped: [], ignored: []
//   resolve_tags([
//     TagSource("Brown Hair", "scene_tag"),
//     TagSource("Missing Date", "scene_tag"),
//     TagSource("Unknown Tag", "scene_tag"),
//     TagSource("Alice", "performer"),
//     TagSource("Studio X", "studio"),
//     TagSource("1080p", "derived"),
//   ])
//     -> tags: ["1080p", "alice", "brown.hair", "brunette", "studio.x"]
//     -> unmapped: ["Unknown Tag"]
//     -> ignored: ["Missing Date"]

function serveAssets(page) {
  page.route('**/plugin*/**/review.html*', async (route) => {
    const filePath = path.resolve('plugin/assets/review.html');
    return route.fulfill({
      status: 200,
      contentType: 'text/html',
      body: fs.readFileSync(filePath, 'utf8')
    });
  });

  page.route('**/*review.js*', async (route) => {
    const filePath = path.resolve('plugin/assets/review.js');
    return route.fulfill({
      status: 200,
      contentType: 'application/javascript',
      body: fs.readFileSync(filePath, 'utf8')
    });
  });
}

const FIXTURE_VOCABULARY = {
  map: {
    'blowjob': ['blowjob'],
    'brown hair': ['brunette', 'brown.hair'],
    'all anal': ['all.anal', 'anal'],
    'amateur': ['amateur'],
    '69': ['69']
  },
  ignored: [
    '4k available',
    'hd available',
    'missing date',
    'missing cover image',
    'missing performer (female)'
  ]
};

// [sources, expectedResult]
const PARITY_FIXTURES = [
  {
    sources: [{ value: 'Brown Hair', kind: 'scene_tag' }],
    expected: {
      tags: ['brown.hair', 'brunette'],
      unmapped: [],
      ignored: []
    }
  },
  {
    sources: [{ value: 'Blowjob', kind: 'scene_tag' }],
    expected: {
      tags: ['blowjob'],
      unmapped: [],
      ignored: []
    }
  },
  {
    sources: [{ value: 'bLoWjOb', kind: 'scene_tag' }],
    expected: {
      tags: ['blowjob'],
      unmapped: [],
      ignored: []
    }
  },
  {
    sources: [{ value: '4K Available', kind: 'scene_tag' }],
    expected: {
      tags: [],
      unmapped: [],
      ignored: ['4K Available']
    }
  },
  {
    sources: [{ value: 'Missing Date', kind: 'scene_tag' }],
    expected: {
      tags: [],
      unmapped: [],
      ignored: ['Missing Date']
    }
  },
  {
    sources: [{ value: 'Custom Tag 123', kind: 'scene_tag' }],
    expected: {
      tags: [],
      unmapped: ['Custom Tag 123'],
      ignored: []
    }
  },
  {
    sources: [{ value: 'Pamela Anderson', kind: 'performer' }],
    expected: {
      tags: ['pamela.anderson'],
      unmapped: [],
      ignored: []
    }
  },
  {
    sources: [{ value: 'Evil Angel', kind: 'studio' }],
    expected: {
      tags: ['evil.angel'],
      unmapped: [],
      ignored: []
    }
  },
  {
    sources: [{ value: '1080p', kind: 'derived' }],
    expected: {
      tags: ['1080p'],
      unmapped: [],
      ignored: []
    }
  },
  {
    sources: [
      { value: 'Brown Hair', kind: 'scene_tag' },
      { value: 'Missing Date', kind: 'scene_tag' },
      { value: 'Unknown Tag', kind: 'scene_tag' },
      { value: 'Alice', kind: 'performer' },
      { value: 'Studio X', kind: 'studio' },
      { value: '1080p', kind: 'derived' }
    ],
    expected: {
      tags: ['1080p', 'alice', 'brown.hair', 'brunette', 'studio.x'],
      unmapped: ['Unknown Tag'],
      ignored: ['Missing Date']
    }
  }
];

test.describe('Tag Vocabulary Parity between JS and Python', () => {
  test('resolveTags matches Python resolve_tags across parity corpus', async ({ page }) => {
    serveAssets(page);
    await page.route('**/graphql', async (route) => {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ data: { findScenes: { scenes: [] } } })
      });
    });

    await page.goto('http://localhost:9999/plugins/empornium-megapack/review.html?scenes=1');

    for (const fixture of PARITY_FIXTURES) {
      const result = await page.evaluate(
        ({ sources, vocab }) => window.resolveTags(sources, vocab),
        { sources: fixture.sources, vocab: FIXTURE_VOCABULARY }
      );

      expect(result.tags, `tags for ${JSON.stringify(fixture.sources)}`).toEqual(fixture.expected.tags);
      expect(result.unmapped, `unmapped for ${JSON.stringify(fixture.sources)}`).toEqual(fixture.expected.unmapped);
      expect(result.ignored, `ignored for ${JSON.stringify(fixture.sources)}`).toEqual(fixture.expected.ignored);
    }
  });

  test('unmapped tags collapsible displays correctly and is collapsed by default', async ({ page }) => {
    serveAssets(page);
    await page.route('**/graphql', async (route) => {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          data: {
            findScenes: {
              scenes: [
                {
                  id: '1',
                  title: 'Scene 1',
                  files: [{ path: '/media/s1.mp4', height: 1080, duration: 600, video_codec: 'h264' }],
                  tags: [{ name: 'Custom Stash Tag' }]
                }
              ]
            }
          }
        })
      });
    });

    await page.goto('http://localhost:9999/plugins/empornium-megapack/review.html?scenes=1');

    // Initially unmapped collapsible should be hidden
    const collapsible = page.locator('#unmapped-tags-collapsible');
    await expect(collapsible).toBeHidden();

    // Trigger onTaskComplete with unmapped tags
    await page.evaluate(() => {
      const payload = {
        status: 'success',
        pack_title: 'Unmapped Pack',
        bbcode: '[b]Test BBCode[/b]',
        tracker_tags: ['1080p', 'scene'],
        unmapped_tags: ['Tag One', 'Tag Two'],
        uploaded_urls: ['https://img/1.jpg'],
        preview_only: false
      };
      if (typeof window.onTaskComplete === 'function') {
        window.onTaskComplete('BuildMegapack', payload);
      }
    });

    await expect(collapsible).toBeVisible();
    const summary = page.locator('#unmapped-tags-summary');
    await expect(summary).toContainText('2 Stash tags have no Empornium equivalent (not sent)');

    // Assert collapsed by default (open attribute not present)
    const isOpen = await collapsible.evaluate((el) => el.hasAttribute('open'));
    expect(isOpen).toBe(false);

    // Assert list contains unmapped tag names
    const listItems = page.locator('#unmapped-tags-list li');
    await expect(listItems).toHaveCount(2);
    await expect(listItems.nth(0)).toHaveText('Tag One');
    await expect(listItems.nth(1)).toHaveText('Tag Two');
  });

  test('sidecar unreachable degrades visibly in #bbcode-warning with unfiltered tags', async ({ page }) => {
    serveAssets(page);
    await page.route('**/graphql', async (route) => {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          data: {
            findScenes: {
              scenes: [
                {
                  id: '1',
                  title: 'Scene 1',
                  files: [{ path: '/media/s1.mp4', height: 1080 }],
                  tags: [{ name: 'Custom Unmapped Tag' }]
                }
              ]
            }
          }
        })
      });
    });

    // Simulate failed vocabulary fetch
    await page.route('**/api/tags/vocabulary', async (route) => {
      return route.abort('failed');
    });

    await page.goto('http://localhost:9999/plugins/empornium-megapack/review.html?scenes=1&mode=single');

    // Wait for fetchVocabulary to fail and update BBCode
    const warning = page.locator('#bbcode-warning');
    await expect(warning).toBeVisible({ timeout: 5000 });
    await expect(warning).toContainText('Tag vocabulary unavailable — tags shown unfiltered');

    const preview = page.locator('#bbcode-preview');
    const val = await preview.inputValue();
    expect(val).toContain('[b]Tags:[/b] Custom Unmapped Tag');
  });
});
