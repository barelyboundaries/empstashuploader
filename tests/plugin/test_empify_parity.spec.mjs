import { test, expect } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

// Parity guard: review.js empifyTag must behave exactly like
// backend/deepseek_megapack/metadata.py empify(), which is authoritative:
//
//   def empify(tag: str) -> str:
//       cleaned = re.sub(r"[^\w\s._-]", "", tag).lower()
//       cleaned = re.sub(r"[\s._-]+", ".", cleaned)
//       return cleaned.strip(".")[:32]
//
// Authoritative Python outputs (recorded from the project venv's python
// importing deepseek_megapack.metadata; repr/ascii form):
//
//   empify('a_b')             -> 'a.b'      (underscore runs become dots)
//   empify('foo bar')         -> 'foo.bar'  (whitespace runs become dots)
//   empify('café')            -> 'café'     (é is a letter; kept as-is)
//   empify('A/B:C')           -> 'abc'      (removed chars do NOT become separators)
//   empify('x' * 40)          -> 'x' * 32   (truncated to 32 chars)
//   empify('  ..Foo__BAR  ')  -> 'foo.bar'
//   empify('')                -> ''
//
// The old JS implementation (ASCII-only `[^a-z0-9]+` -> ".") diverged on the
// unicode cases: 'café' -> 'caf' and 'A/B:C' -> 'a.b.c'. This spec pins the
// backend behavior so the frontend cannot drift again.

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

// [frontend input, backend empify output] — every expected value below is the
// literal output of the Python implementation shown above.
const EMPIFY_CORPUS = [
  ['a_b', 'a.b'],
  ['foo bar', 'foo.bar'],
  ['café', 'café'],
  ['A/B:C', 'abc'],
  ['x'.repeat(40), 'x'.repeat(32)],
  ['  ..Foo__BAR  ', 'foo.bar'],
  ['', '']
];

test.describe('Empify Tag Parity between JS and Python', () => {
  test('empifyTag matches Python empify across parity corpus', async ({ page }) => {
    serveAssets(page);
    await page.route('**/graphql', async (route) => {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ data: { findScenes: { scenes: [] } } })
      });
    });

    await page.goto('http://localhost:9999/plugins/deepseek-megapack/review.html?scenes=1');

    for (const [rawInput, expected] of EMPIFY_CORPUS) {
      const result = await page.evaluate((val) => window.empifyTag(val), rawInput);
      expect(result, `empifyTag(${JSON.stringify(rawInput)})`).toBe(expected);
      expect(result.length).toBeLessThanOrEqual(32);
    }
  });

  test('empifyTag collapses separator runs and never emits empty-dot tags', async ({ page }) => {
    serveAssets(page);
    await page.route('**/graphql', async (route) => {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ data: { findScenes: { scenes: [] } } })
      });
    });

    await page.goto('http://localhost:9999/plugins/deepseek-megapack/review.html?scenes=1');

    // Backend-derived properties (not single fixed outputs): separator runs of
    // any mix collapse to one dot; edge separators are trimmed; tags made only
    // of separators empify to ''.
    const cases = [
      ['Blowjob - POV', 'blowjob.pov'],
      ['..tag..', 'tag'],
      ['- _ . -', ''],
      [' ___ ', ''],
      ['Ünïcodé  Täg', 'ünïcodé.täg']
    ];
    for (const [rawInput, expected] of cases) {
      const result = await page.evaluate((val) => window.empifyTag(val), rawInput);
      expect(result, `empifyTag(${JSON.stringify(rawInput)})`).toBe(expected);
    }
  });
});
