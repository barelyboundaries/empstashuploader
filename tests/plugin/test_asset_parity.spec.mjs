import { test, expect } from '@playwright/test';
import fs from 'node:fs';
import crypto from 'node:crypto';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

// plugin/assets/ is the SINGLE source of the review UI: it is served by Stash
// via ui.assets, and the backend/empornium_megapack/static/ copies that used to
// be parity-checked against it were deleted in the release audit (T4). There
// is no second copy to diff against anymore, so this guard pins each file's
// exact content as a hash-of-record instead.
//
// The asset set has already drifted once: 3529a65 landed the
// duplicate-filename UI in plugin/assets only, leaving the other served copy
// on pre-feature code until 9aef42b re-synced it. Nothing caught that
// automatically, hence this guard.
//
// A deliberate edit to review.js or review.html fails here with a hash
// mismatch — that is the signal to refresh ASSET_RECORD below in the same
// commit, so unreviewed or truncated asset changes can never slip through.
const ASSET_RECORD = {
  // review.js: T3 renamed JS/DOM/CSS identifiers to empornium branding
  'review.js': '68b9a8c3f741a02cf57e40512795d3a3a5139b22bf468c5e34dee0a35e76d1ae',
  // review.html: pre-existing hash (not modified by T3)
  'review.html': '6dc818d86a67d1597ed23b3e3a2fd907c1305aac4041dcc1eeb4695e4e41be3b',
};

function sha256(filePath) {
  return crypto.createHash('sha256').update(fs.readFileSync(filePath)).digest('hex');
}

test.describe('Single-source asset integrity — plugin/assets hash-of-record', () => {
  for (const [name, recorded] of Object.entries(ASSET_RECORD)) {
    test(`${name} matches its recorded sha256`, () => {
      const filePath = path.resolve(__dirname, '../../plugin/assets', name);

      // Assert existence first: a missing file would otherwise surface as an
      // opaque ENOENT rather than naming which asset is absent.
      expect(fs.existsSync(filePath), `missing plugin asset: ${filePath}`).toBe(true);

      expect(
        sha256(filePath),
        `${name} changed without refreshing its hash-of-record. ` +
          `plugin/assets is the single source of the review UI — every edit ` +
          `must update ASSET_RECORD in this spec in the same commit.`
      ).toBe(recorded);
    });
  }
});
