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
  // review.js: Change A — Stage 2 locations lockout and probeResultsMap wiring
  'review.js': '5dd0acac849823975f092524c9053bdd0359a93b6b3bb8cbbb857541bc4515be',
  // review.html: Change A — #locations-locked-notice and #btn-unlock-locations inside #stage-panel-2
  'review.html': '0733b25f0ec5d80c78b22198bd84032218571254eec01370f0baf3af805c617a',
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

test.describe('Suite-level test harness invariants', () => {
  test('every spec referencing FINISHED also routes /api/run to isolate sidecar', () => {
    const specsDir = __dirname;
    const specFiles = fs.readdirSync(specsDir).filter((f) => f.endsWith('.spec.mjs'));

    const unmocked = [];
    for (const specFile of specFiles) {
      const content = fs.readFileSync(path.join(specsDir, specFile), 'utf8');
      if (content.includes('FINISHED') && !content.includes('api/run')) {
        unmocked.push(specFile);
      }
    }

    expect(
      unmocked,
      `The following specs drive a FINISHED job without mocking /api/run/**. ` +
        `They will absorb a 5s retry loop when the sidecar is active:\n${unmocked.join('\n')}`
    ).toEqual([]);
  });
});
