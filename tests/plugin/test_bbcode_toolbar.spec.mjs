import { test, expect } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

function setupMocks(page) {
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
    return route.fulfill({
      status: 200,
      contentType: 'text/html',
      body: fs.readFileSync(path.resolve('plugin/assets/review.html'), 'utf8'),
    });
  });

  page.route('**/*review.js*', async (route) => {
    return route.fulfill({
      status: 200,
      contentType: 'application/javascript',
      body: fs.readFileSync(path.resolve('plugin/assets/review.js'), 'utf8'),
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

test.describe('BBCode Formatting Toolbar & Textarea Single Source of Truth', () => {
  test.beforeEach(async ({ page }) => {
    setupMocks(page);

    await page.route('**/graphql', async (route) => {
      const postData = JSON.parse(route.request().postData() || '{}');
      if (postData.query && postData.query.includes('FindScenes')) {
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            data: {
              findScenes: {
                scenes: [
                  {
                    id: 1,
                    title: 'Scene Alpha',
                    paths: {},
                    files: [{ id: 101, path: 'C:\\Media\\alpha.mp4', height: 1080, duration: 1800 }],
                    performers: [{ id: 1, name: 'Emma Cruz' }],
                    tags: [{ id: 1, name: 'Featured' }],
                  },
                  {
                    id: 2,
                    title: 'Scene Beta',
                    paths: {},
                    files: [{ id: 102, path: 'C:\\Media\\beta.mp4', height: 1080, duration: 2400 }],
                    performers: [{ id: 2, name: 'Chloe Adams' }],
                    tags: [{ id: 2, name: 'Exclusive' }],
                  },
                ],
              },
            },
          }),
        });
      }
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ data: {} }) });
    });
  });

  // 1. Wrapping a selection produces [tag]selection[/tag] and leaves text selected afterwards
  test('1. Wrapping a selection produces [tag]selection[/tag] and leaves text selected afterwards', async ({ page }) => {
    await page.goto('http://localhost:9999/plugins/empornium-megapack/review.html?scenes=1&mode=single');
    await expect(page.locator('#bbcode-preview')).toBeVisible();

    const textarea = page.locator('#bbcode-preview');
    await textarea.fill('Hello Emma Cruz World');

    // Select Emma Cruz (start: 6, end: 15)
    await page.evaluate(() => {
      const el = document.getElementById('bbcode-preview');
      el.focus();
      el.setSelectionRange(6, 15);
    });

    // Click Bold button
    await page.locator('#btn-tag-b').click();

    // Verify wrapped text in textarea
    await expect(textarea).toHaveValue('Hello [b]Emma Cruz[/b] World');

    // Verify Emma Cruz remains selected
    const selected = await page.evaluate(() => {
      const el = document.getElementById('bbcode-preview');
      return el.value.substring(el.selectionStart, el.selectionEnd);
    });
    expect(selected).toBe('Emma Cruz');
  });

  // 2. Clicking with no selection inserts tag pair with caret between opening and closing tags
  test('2. Clicking with no selection inserts tag pair with caret between opening and closing tags', async ({ page }) => {
    await page.goto('http://localhost:9999/plugins/empornium-megapack/review.html?scenes=1&mode=single');
    await expect(page.locator('#bbcode-preview')).toBeVisible();

    const textarea = page.locator('#bbcode-preview');
    await textarea.fill('');

    // Set caret at 0
    await page.evaluate(() => {
      const el = document.getElementById('bbcode-preview');
      el.focus();
      el.setSelectionRange(0, 0);
    });

    // Click Italic button
    await page.locator('#btn-tag-i').click();

    // Verify tag pair inserted
    await expect(textarea).toHaveValue('[i][/i]');

    // Verify caret is placed between tags (position 3)
    const caretPos = await page.evaluate(() => {
      const el = document.getElementById('bbcode-preview');
      return { start: el.selectionStart, end: el.selectionEnd };
    });
    expect(caretPos.start).toBe(3);
    expect(caretPos.end).toBe(3);
  });

  // 3. A value-taking tag ([url=…]) produces correct markup from its inline control with no window.prompt
  test('3. A value-taking tag ([url=…]) produces correct markup from its inline control with no window.prompt', async ({ page }) => {
    let promptCalled = false;
    page.on('dialog', (dialog) => {
      promptCalled = true;
      dialog.dismiss();
    });

    await page.goto('http://localhost:9999/plugins/empornium-megapack/review.html?scenes=1&mode=single');
    await expect(page.locator('#bbcode-preview')).toBeVisible();

    const textarea = page.locator('#bbcode-preview');
    await textarea.fill('Visit Empornium Tracker');

    // Select Empornium Tracker (start: 6, end: 23)
    await page.evaluate(() => {
      const el = document.getElementById('bbcode-preview');
      el.focus();
      el.setSelectionRange(6, 23);
    });

    // Click URL button
    await page.locator('#btn-tag-url').click();

    // Inline popover must be visible, NO prompt dialog
    const popover = page.locator('#toolbar-popover');
    await expect(popover).toBeVisible();
    expect(promptCalled).toBe(false);

    // Fill URL and confirm
    await page.locator('#toolbar-popover-input').fill('https://empornium.is');
    await page.locator('#toolbar-popover-confirm').click();

    // Popover hides and textarea receives [url=https://empornium.is]Empornium Tracker[/url]
    await expect(popover).toBeHidden();
    await expect(textarea).toHaveValue('Visit [url=https://empornium.is]Empornium Tracker[/url]');
    expect(promptCalled).toBe(false);
  });

  // 4. Native undo: type text, click toolbar button, press Ctrl+Z — tag insertion is undone and typed text survives
  test('4. Native undo: type text, click toolbar button, press Ctrl+Z — tag insertion is undone and typed text survives', async ({ page }) => {
    await page.goto('http://localhost:9999/plugins/empornium-megapack/review.html?scenes=1&mode=single');
    await expect(page.locator('#bbcode-preview')).toBeVisible();

    const textarea = page.locator('#bbcode-preview');
    await textarea.click();
    await textarea.fill('');

    // Type text into the textarea so native undo stack records it
    await textarea.pressSequentially('Quick brown fox');

    // Select brown (start: 6, end: 11)
    await page.evaluate(() => {
      const el = document.getElementById('bbcode-preview');
      el.setSelectionRange(6, 11);
    });

    // Click Bold button
    await page.locator('#btn-tag-b').click();
    await expect(textarea).toHaveValue('Quick [b]brown[/b] fox');

    // Press Ctrl+Z to undo tag insertion
    await textarea.press('Control+z');

    // Tag insertion is undone, typed text survives!
    await expect(textarea).toHaveValue('Quick brown fox');

    // Press Ctrl+Y to redo tag insertion
    await textarea.press('Control+y');
    await expect(textarea).toHaveValue('Quick [b]brown[/b] fox');
  });

  // 5. Copy button yields exactly the textarea current value, including manual user edits
  test('5. Copy button yields exactly the textarea current value, including manual user edits', async ({ page, context }) => {
    await context.grantPermissions(['clipboard-read', 'clipboard-write']);

    await page.goto('http://localhost:9999/plugins/empornium-megapack/review.html?scenes=1&mode=single');
    await expect(page.locator('#bbcode-preview')).toBeVisible();

    const customText = '[center][b]Custom Manual User Edits[/b][/center]';
    const textarea = page.locator('#bbcode-preview');
    await textarea.fill(customText);

    const copyBtn = page.locator('#btn-copy-bbcode');
    await copyBtn.click();
    await expect(copyBtn).toContainText('Copied!');

    const clipboardContent = await page.evaluate(() => navigator.clipboard.readText());
    expect(clipboardContent).toBe(customText);
  });

  // 6. The R4 rule: user edits are not silently destroyed by scene-selection changes
  test('6. The R4 rule: user edits are not silently destroyed by scene-selection changes', async ({ page }) => {
    await page.goto('http://localhost:9999/plugins/empornium-megapack/review.html?scenes=1,2&mode=megapack');
    await expect(page.locator('.scene-card')).toHaveCount(2);

    const textarea = page.locator('#bbcode-preview');
    await expect(textarea).toHaveValue(/Scene Alpha/);

    // User makes custom edits directly in the textarea
    const userProtectedBBCode = '[b]My Custom Manual Layout[/b] - do not overwrite!';
    await textarea.fill(userProtectedBBCode);
    await textarea.dispatchEvent('input');

    // User edits pack title which ordinarily invokes updateBBCode()
    await page.locator('#pack-title').fill('Renamed Pack Title');

    // Changing scene selection or title must NOT overwrite the user manual edits
    await expect(textarea).toHaveValue(userProtectedBBCode);
  });

  // 7. Megapack single long image line soft-wraps rather than forcing horizontal scroll
  test('7. Megapack single long image line soft-wraps rather than forcing horizontal scroll', async ({ page }) => {
    await page.goto('http://localhost:9999/plugins/empornium-megapack/review.html?scenes=1&mode=single');
    await expect(page.locator('#bbcode-preview')).toBeVisible();

    const textarea = page.locator('#bbcode-preview');

    // Emulate ~130 image tags on a single continuous line as emitted by plugin/task.py
    const longImageLine = Array.from({ length: 130 }, (_, i) => '[img=150]https://hamsterimg.net/scene_' + (i + 1) + '.jpg[/img]').join('');
    await textarea.fill(longImageLine);

    // Ensure scrollWidth matches clientWidth (soft wrap on, no horizontal scrollbar overflow)
    const metrics = await textarea.evaluate((el) => ({
      scrollWidth: el.scrollWidth,
      clientWidth: el.clientWidth,
      wrap: el.getAttribute('wrap'),
    }));

    expect(metrics.wrap).not.toBe('off');
    // With soft wrapping, scrollWidth should equal clientWidth (within 4px margin for subpixel/border)
    expect(metrics.scrollWidth).toBeLessThanOrEqual(metrics.clientWidth + 4);
  });

  // 8. bbcodeIsFinal latching and payload.bbcode_truncated warning path behave correctly
  test('8. bbcodeIsFinal latching and payload.bbcode_truncated warning path behave correctly', async ({ page }) => {
    await page.goto('http://localhost:9999/plugins/empornium-megapack/review.html?scenes=1&mode=single');
    await expect(page.locator('#bbcode-preview')).toBeVisible();

    // Test warning path: dispatch task completion with bbcode_truncated = true
    await page.evaluate(() => {
      if (typeof window.onTaskComplete === 'function') {
        window.onTaskComplete('BuildSingleScene', {
          bbcode_truncated: true,
          bbcode_path: 'C:\\\\Packs\\\\test.bbcode',
        });
      }
    });

    const warningEl = page.locator('#bbcode-warning');
    await expect(warningEl).toBeVisible();
    await expect(warningEl).toContainText('provisional (truncated)');

    // Now arrive with final build result: latch bbcodeIsFinal
    const finalSubmissionBBCode = '[center][b]Final Gazelle BBCode Submission[/b][/center]\n[img=200]https://hamsterimg.net/final.jpg[/img]';
    await page.evaluate((finalText) => {
      if (typeof window.onTaskComplete === 'function') {
        window.onTaskComplete('BuildSingleScene', {
          bbcode: finalText,
        });
      }
      // Trigger updateBBCode to verify it respects bbcodeIsFinal latch
      if (typeof window.updateBBCode === 'function') {
        window.updateBBCode();
      }
    }, finalSubmissionBBCode);

    await expect(warningEl).toBeHidden();
    await expect(page.locator('#bbcode-preview')).toHaveValue(finalSubmissionBBCode);
  });

  // 9. Value-taking tag [spoiler=…] with inline popover, keyboard shortcuts (Escape cancel, Enter submit)
  test('9. Value-taking tag [spoiler=…] with inline popover, keyboard shortcuts (Escape cancel, Enter submit)', async ({ page }) => {
    await page.goto('http://localhost:9999/plugins/empornium-megapack/review.html?scenes=1&mode=single');
    await expect(page.locator('#bbcode-preview')).toBeVisible();

    const textarea = page.locator('#bbcode-preview');
    await textarea.fill('Confidential Scene Details');

    // Select 'Scene Details' (start: 13, end: 26)
    await page.evaluate(() => {
      const el = document.getElementById('bbcode-preview');
      el.focus();
      el.setSelectionRange(13, 26);
    });

    // Click Spoiler button
    await page.locator('#btn-tag-spoiler').click();
    const popover = page.locator('#toolbar-popover');
    await expect(popover).toBeVisible();
    await expect(page.locator('#toolbar-popover-label')).toHaveText('Spoiler Title:');

    // Press Escape to cancel: popover closes without modifying textarea
    await page.keyboard.press('Escape');
    await expect(popover).toBeHidden();
    await expect(textarea).toHaveValue('Confidential Scene Details');

    // Re-open spoiler popover
    await page.locator('#btn-tag-spoiler').click();
    await expect(popover).toBeVisible();

    // Type spoiler title and press Enter
    await page.locator('#toolbar-popover-input').fill('Plot Twist');
    await page.keyboard.press('Enter');

    await expect(popover).toBeHidden();
    await expect(textarea).toHaveValue('Confidential [spoiler=Plot Twist]Scene Details[/spoiler]');
  });

  // 10. Fixed-choice selects [size=N] and [color=…] wrap selection and retain selection
  test('10. Fixed-choice selects [size=N] and [color=…] wrap selection and retain selection', async ({ page }) => {
    await page.goto('http://localhost:9999/plugins/empornium-megapack/review.html?scenes=1&mode=single');
    await expect(page.locator('#bbcode-preview')).toBeVisible();

    const textarea = page.locator('#bbcode-preview');
    await textarea.fill('Vibrant Header Text');

    // Select Vibrant Header (start: 0, end: 14)
    await page.evaluate(() => {
      const el = document.getElementById('bbcode-preview');
      el.focus();
      el.setSelectionRange(0, 14);
    });

    // Select Size 4
    await page.locator('#toolbar-size').selectOption('4');
    await expect(textarea).toHaveValue('[size=4]Vibrant Header[/size] Text');

    // The formatted text remains selected inside the tags (start: 8, end: 22)
    const selectedAfterSize = await page.evaluate(() => {
      const el = document.getElementById('bbcode-preview');
      return el.value.substring(el.selectionStart, el.selectionEnd);
    });
    expect(selectedAfterSize).toBe('Vibrant Header');

    // Now select Color 'red' while still selected
    await page.locator('#toolbar-color').selectOption('red');
    await expect(textarea).toHaveValue('[size=4][color=red]Vibrant Header[/color][/size] Text');
  });

  // 11. Self-closing [hr] and multiline [list] formatting
  test('11. Self-closing [hr] and multiline [list] formatting', async ({ page }) => {
    await page.goto('http://localhost:9999/plugins/empornium-megapack/review.html?scenes=1&mode=single');
    await expect(page.locator('#bbcode-preview')).toBeVisible();

    const textarea = page.locator('#bbcode-preview');
    await textarea.fill('');

    // Click HR with empty selection
    await page.locator('#btn-tag-hr').click();
    await expect(textarea).toHaveValue('[hr]');

    // Fill multiline list items
    await textarea.fill('First Item\nSecond Item\nThird Item');
    await page.evaluate(() => {
      const el = document.getElementById('bbcode-preview');
      el.focus();
      el.setSelectionRange(0, el.value.length);
    });

    // Click List button
    await page.locator('#btn-tag-list').click();
    await expect(textarea).toHaveValue('[list]\n[*]First Item\n[*]Second Item\n[*]Third Item\n[/list]');
  });

  // 12. Caret position preserved after paste / input without mouseup/keyup
  test('12. Caret position preserved after paste / input without mouseup/keyup', async ({ page }) => {
    await page.goto('http://localhost:9999/plugins/empornium-megapack/review.html?scenes=1&mode=single');
    const textarea = page.locator('#bbcode-preview');
    await textarea.fill('Hello');

    // Simulate paste appending ' World'
    await page.evaluate(() => {
      const el = document.getElementById('bbcode-preview');
      el.focus();
      el.setSelectionRange(5, 5);
      document.execCommand('insertText', false, ' World');
    });

    // Caret is now at index 11. Click Bold button
    await page.locator('#btn-tag-b').click();
    await expect(textarea).toHaveValue('Hello World[b][/b]');
  });

  // 13. Popover dismisses automatically on clicking another toolbar button or focusing textarea
  test('13. Popover dismisses automatically on clicking another toolbar button or focusing textarea', async ({ page }) => {
    await page.goto('http://localhost:9999/plugins/empornium-megapack/review.html?scenes=1&mode=single');
    const textarea = page.locator('#bbcode-preview');
    const popover = page.locator('#toolbar-popover');

    // Open URL popover
    await page.locator('#btn-tag-url').click();
    await expect(popover).toBeVisible();

    // Clicking Bold button dismisses popover and applies bold formatting
    await page.locator('#btn-tag-b').click();
    await expect(popover).toBeHidden();
    await expect(textarea).toHaveValue(/\[b\]/);

    // Open Spoiler popover
    await page.locator('#btn-tag-spoiler').click();
    await expect(popover).toBeVisible();

    // Focusing textarea directly dismisses popover
    await textarea.focus();
    await expect(popover).toBeHidden();
  });

  // 14. URL popover input sanitizes multiline/tab whitespace and trims properly
  test('14. URL popover input sanitizes multiline/tab whitespace and trims properly', async ({ page }) => {
    await page.goto('http://localhost:9999/plugins/empornium-megapack/review.html?scenes=1&mode=single');
    const textarea = page.locator('#bbcode-preview');
    await textarea.fill('Link text');
    await page.evaluate(() => {
      const el = document.getElementById('bbcode-preview');
      el.focus();
      el.setSelectionRange(0, 9);
    });

    await page.locator('#btn-tag-url').click();
    const input = page.locator('#toolbar-popover-input');
    await input.fill('  https://example.com/path\n\twith-whitespace   ');
    await page.locator('#toolbar-popover-confirm').click();

    await expect(textarea).toHaveValue('[url=https://example.com/path with-whitespace]Link text[/url]');
  });

  // 15. Multiline [list] formatting handles Windows CRLF (\r\n) line endings cleanly
  test('15. Multiline [list] formatting handles Windows CRLF (\r\n) line endings cleanly', async ({ page }) => {
    await page.goto('http://localhost:9999/plugins/empornium-megapack/review.html?scenes=1&mode=single');
    const textarea = page.locator('#bbcode-preview');
    await textarea.fill('Alpha\r\nBeta\r\nGamma');
    await page.evaluate(() => {
      const el = document.getElementById('bbcode-preview');
      el.focus();
      el.setSelectionRange(0, el.value.length);
    });

    await page.locator('#btn-tag-list').click();
    await expect(textarea).toHaveValue('[list]\n[*]Alpha\n[*]Beta\n[*]Gamma\n[/list]');
  });

  // 16. Multiline [list] formatting does not duplicate [*] bullets and trims trailing newlines
  test('16. Multiline [list] formatting does not duplicate [*] bullets and trims trailing newlines', async ({ page }) => {
    await page.goto('http://localhost:9999/plugins/empornium-megapack/review.html?scenes=1&mode=single');
    const textarea = page.locator('#bbcode-preview');
    await textarea.fill('[*]Pre-bulleted 1\n[*]Pre-bulleted 2\n');
    await page.evaluate(() => {
      const el = document.getElementById('bbcode-preview');
      el.focus();
      el.setSelectionRange(0, el.value.length);
    });

    await page.locator('#btn-tag-list').click();
    await expect(textarea).toHaveValue('[list]\n[*]Pre-bulleted 1\n[*]Pre-bulleted 2\n[/list]');
  });

  // 17. Collapsed caret position preserved when using <select> controls (size/color)
  test('17. Collapsed caret position preserved when using <select> controls (size/color)', async ({ page }) => {
    await page.goto('http://localhost:9999/plugins/empornium-megapack/review.html?scenes=1&mode=single');
    const textarea = page.locator('#bbcode-preview');
    await textarea.fill('Hello World');

    // Place collapsed caret at index 5 (after "Hello")
    await page.evaluate(() => {
      const el = document.getElementById('bbcode-preview');
      el.focus();
      el.setSelectionRange(5, 5);
    });

    // Change size select to 3
    await page.locator('#toolbar-size').selectOption('3');
    await expect(textarea).toHaveValue('Hello[size=3][/size] World');

    // Caret is inside tag pair (index 13: 5 + 8)
    const caretPos = await page.evaluate(() => {
      const el = document.getElementById('bbcode-preview');
      return { start: el.selectionStart, end: el.selectionEnd };
    });
    expect(caretPos.start).toBe(13);
    expect(caretPos.end).toBe(13);
  });

  // 18. Native undo (Ctrl+Z) undoes formatting applied via <select> controls and popovers
  test('18. Native undo (Ctrl+Z) undoes formatting applied via <select> controls and popovers', async ({ page }) => {
    await page.goto('http://localhost:9999/plugins/empornium-megapack/review.html?scenes=1&mode=single');
    const textarea = page.locator('#bbcode-preview');
    await textarea.click();
    await textarea.fill('');
    await textarea.pressSequentially('Undoable paragraph text');

    // Select 'paragraph' (start: 9, end: 18)
    await page.evaluate(() => {
      const el = document.getElementById('bbcode-preview');
      el.setSelectionRange(9, 18);
    });

    // Apply size 5 via select
    await page.locator('#toolbar-size').selectOption('5');
    await expect(textarea).toHaveValue('Undoable [size=5]paragraph[/size] text');

    // Undo with Ctrl+Z
    await textarea.press('Control+z');
    await expect(textarea).toHaveValue('Undoable paragraph text');
  });

  // 19. Scroll position is preserved when opening and closing popovers
  test('19. Scroll position is preserved when opening and closing popovers', async ({ page }) => {
    await page.goto('http://localhost:9999/plugins/empornium-megapack/review.html?scenes=1&mode=single');
    const textarea = page.locator('#bbcode-preview');

    // Fill large content to induce scrolling
    const longContent = Array.from({ length: 50 }, (_, i) => `Line ${i + 1} of long description content`).join('\n');
    await textarea.fill(longContent);

    // Scroll down 200px and place caret at line 40
    await page.evaluate(() => {
      const el = document.getElementById('bbcode-preview');
      el.scrollTop = 200;
      el.focus();
      el.setSelectionRange(el.value.indexOf('Line 40'), el.value.indexOf('Line 40') + 7);
    });

    const scrollBefore = await page.evaluate(() => document.getElementById('bbcode-preview').scrollTop);
    expect(scrollBefore).toBeGreaterThanOrEqual(150);

    // Open URL popover
    await page.locator('#btn-tag-url').click();
    const popover = page.locator('#toolbar-popover');
    await expect(popover).toBeVisible();

    // Cancel popover via Escape
    await page.keyboard.press('Escape');
    await expect(popover).toBeHidden();

    // Scroll position should remain preserved
    const scrollAfter = await page.evaluate(() => document.getElementById('bbcode-preview').scrollTop);
    expect(scrollAfter).toBe(scrollBefore);
  });
});



