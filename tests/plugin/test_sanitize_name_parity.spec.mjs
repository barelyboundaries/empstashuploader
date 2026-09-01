import { test, expect } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

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

const CORPUS = [
  ['', 'Untitled'],
  ['   ', 'Untitled'],
  ['  . . ', 'Untitled'],
  ['CON', '_CON'],
  ['prn', '_prn'],
  ['aux', '_aux'],
  ['nul', '_nul'],
  ['COM1', '_COM1'],
  ['com9', '_com9'],
  ['lpt1', '_lpt1'],
  ['LPT9', '_LPT9'],
  ['COM10', 'COM10'],
  ['CON.txt', 'CON.txt'],
  ['a<b>c:\"d\"e|f?g*h', 'a_b_c__d_e_f_g_h'],
  ['hello\x00world\x1ftest', 'hello_world_test'],
  ['  spaced   name  with   tabs\t\nand newlines  ', 'spaced name with tabs__and newlines'],
  ['  .. leading and trailing dots and spaces ..  ', 'leading and trailing dots and spaces'],
  ['Unicode 日本語 映画 (2026)', 'Unicode 日本語 映画 (2026)'],
  ['Русский Фильм 2026 (Оригинал)', 'Русский Фильм 2026 (Оригинал)'],
  ['Pack 🚀 and 💎 [4K]', 'Pack 🚀 and 💎 [4K]'],
  ['Éléphant and Café', 'Éléphant and Café'],
  ['a'.repeat(150) + '.mp4', 'a'.repeat(116) + '.mp4'],
  ['a'.repeat(150) + '.superlongextension', 'a'.repeat(120)],
  ['a'.repeat(150), 'a'.repeat(120)],
  ['   ' + 'a'.repeat(150) + '   ', 'a'.repeat(120)],
];

test.describe('Sanitize Name Parity between JS and Python', () => {
  test('matches Python sanitize_name across parity corpus', async ({ page }) => {
    serveAssets(page);
    await page.route('**/graphql', async (route) => {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ data: { findScenes: { scenes: [] } } })
      });
    });

    await page.goto('http://localhost:9999/plugins/empornium-megapack/review.html?scenes=1');

    for (const [rawInput, expected] of CORPUS) {
      const result = await page.evaluate((val) => window.sanitizeName(val), rawInput);
      expect(result).toBe(expected);
      expect(result.length).toBeLessThanOrEqual(120);
    }
  });

  test('getPackDestinationFolder handles subfolder and compat rule', async ({ page }) => {
    serveAssets(page);
    await page.route('**/graphql', async (route) => {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ data: { findScenes: { scenes: [] } } })
      });
    });

    await page.goto('http://localhost:9999/plugins/empornium-megapack/review.html?scenes=1');

    // Subfolder creation
    const subResult = await page.evaluate(() => window.getPackDestinationFolder('C:\\Packs', 'My Pack'));
    expect(subResult).toBe('C:\\Packs\\My Pack');

    // Compat rule: basename matches pack title
    const compatResult = await page.evaluate(() => window.getPackDestinationFolder('C:\\Packs\\My Pack', 'My Pack'));
    expect(compatResult).toBe('C:\\Packs\\My Pack');

    // Case-insensitive compat rule
    const caseCompatResult = await page.evaluate(() => window.getPackDestinationFolder('C:\\Packs\\MY PACK', 'my pack'));
    expect(caseCompatResult).toBe('C:\\Packs\\MY PACK');

    // POSIX path style
    const posixResult = await page.evaluate(() => window.getPackDestinationFolder('/media/packs', 'My Pack'));
    expect(posixResult).toBe('/media/packs/My Pack');
  });
});
