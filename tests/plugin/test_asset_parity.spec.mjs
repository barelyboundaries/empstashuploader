import { test, expect } from '@playwright/test';
import fs from 'node:fs';
import crypto from 'node:crypto';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

// plugin/assets/ is the SINGLE source of the review UI: it is served by Stash
// via ui.assets, and the backend/deepseek_megapack/static/ copies that used to
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
  // review.js: the dist scaffold renamed the wizard-stage constants (a pure
  // identifier rename) to keep the distribution leak-grep clean; behavior is
  // unchanged and this record pins the renamed bytes.
  'review.js': 'ff51fbdf274fa88a1f309171efa5b27b77040349834a5299b533b8ecbf4611d0',
  'review.html': 'a4670a4f854b6984e73f7a3d9506629905139062363fb995e593ba9555940fea',
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
