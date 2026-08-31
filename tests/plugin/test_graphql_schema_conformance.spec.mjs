import { test, expect } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

// Stash has NEVER exposed a bare `oshash` field on VideoFile/BaseFile — the
// real schema exposes `fingerprint(type: String!)` and `fingerprints`.
// Selecting `oshash` directly makes Stash reject the ENTIRE document with
// GRAPHQL_VALIDATION_FAILED ("Cannot query field \"oshash\" on type
// \"VideoFile\""), which took down scene loading AND the destination-collision
// pre-check against a live Stash (verified on v0.31.1-160-gc1fa78e0).
// The correct selection is the alias `oshash: fingerprint(type: "oshash")`,
// which keeps the downstream `file.oshash` shape unchanged.
// The backend/deepseek_megapack/static/ copy was deleted in the release audit
// (T4) — plugin/assets/review.js is the single source of the review UI.
const SOURCES = [path.resolve(__dirname, '../../plugin/assets/review.js')];

test.describe('GraphQL schema conformance (oshash)', () => {
  for (const src of SOURCES) {
    const label = path.relative(path.resolve(__dirname, '../..'), src);

    test(`${label}: never selects a bare oshash field`, async () => {
      const text = fs.readFileSync(src, 'utf8');
      const offenders = text
        .split('\n')
        .map((line, i) => ({ line: line.trim(), n: i + 1 }))
        .filter(({ line }) => /^oshash\s*$/.test(line));
      expect(
        offenders,
        `bare "oshash" selection(s) found at line(s) ${offenders.map((o) => o.n).join(', ')} — ` +
          'use `oshash: fingerprint(type: "oshash")` instead'
      ).toEqual([]);
    });

    test(`${label}: every oshash selection uses the fingerprint alias`, async () => {
      const text = fs.readFileSync(src, 'utf8');
      const aliases = text.match(/oshash:\s*fingerprint\(type:\s*"oshash"\)/g) || [];
      // Three query sites: FindScenes, FindScene, FindDestinationCollisions.
      expect(aliases.length).toBe(3);
    });
  }
});
