#!/usr/bin/env node
/**
 * GraphQL schema conformance check (dev tool, needs a live Stash).
 *
 * Extracts every GraphQL document embedded in the frontend sources and
 * validates it OFFLINE against the live Stash schema, obtained by
 * introspection. Nothing is ever executed against the server, so this is safe
 * to run at any time — mutations are parsed and validated, never sent.
 *
 * Why this exists: the review UI's GraphQL is only ever exercised through
 * mocked Playwright tests, so a query that the real server rejects looks green
 * in CI and fails silently in production. That happened twice —
 *   - `oshash` selected as a bare field on VideoFile (no such field; the real
 *     schema exposes `fingerprint(type:)`), which made the review page render
 *     an empty scene list with no error at all;
 *   - `subTasks { description }` on a `[String!]`, and `IN_LIST` on
 *     `CriterionModifier`, both of which the server rejects outright.
 * A rejected document fails validation as a WHOLE, so one bad field takes down
 * an entire query. Mocks cannot catch that. This can.
 *
 * Usage:
 *   node scripts/check_graphql_schema.mjs
 *   node scripts/check_graphql_schema.mjs --url http://127.0.0.1:9999/graphql
 *   node scripts/check_graphql_schema.mjs --schema .cache/stash-schema.json
 *   node scripts/check_graphql_schema.mjs --save-schema .cache/stash-schema.json
 *   node scripts/check_graphql_schema.mjs --update-baseline
 *   node scripts/check_graphql_schema.mjs path/to/other.js [...]
 *
 * Exit codes: 0 = no new violations, 1 = new violations, 2 = setup problem.
 *
 * Requires the `graphql` package:  npm install --save-dev graphql
 */

import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const REPO = path.resolve(__dirname, '..');
const BASELINE = path.join(__dirname, 'graphql_schema_baseline.json');

let graphql;
try {
  graphql = await import('graphql');
} catch {
  console.error(
    'ERROR: the `graphql` package is not installed.\n' +
      '       npm install --save-dev graphql\n'
  );
  process.exit(2);
}
const { buildClientSchema, getIntrospectionQuery, parse, validate, specifiedRules } = graphql;

// ---------------------------------------------------------------- arguments

const argv = process.argv.slice(2);
function flag(name) {
  const i = argv.indexOf(name);
  if (i === -1) return null;
  const v = argv[i + 1];
  argv.splice(i, v && !v.startsWith('--') ? 2 : 1);
  return v && !v.startsWith('--') ? v : true;
}

const updateBaseline = flag('--update-baseline') === true;
const schemaFile = flag('--schema');
const saveSchema = flag('--save-schema');
const url = flag('--url') || process.env.STASH_URL || 'http://127.0.0.1:9999/graphql';

// plugin/assets/review.js is the single source of the review UI: the
// backend/empornium_megapack/static/ mirror was deleted in the release audit
// (T4), so there is no second served copy to keep byte-identical anymore.
const DEFAULT_FILES = [
  'plugin/assets/review.js',
  'plugin/main.js',
];
const files = argv.length > 0 ? argv : DEFAULT_FILES;

// ------------------------------------------------------------------- schema

async function loadSchema() {
  if (schemaFile && typeof schemaFile === 'string') {
    return JSON.parse(fs.readFileSync(path.resolve(REPO, schemaFile), 'utf8'));
  }
  let res;
  try {
    res = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ query: getIntrospectionQuery() }),
    });
  } catch (e) {
    console.error(`ERROR: cannot reach Stash at ${url} — ${e.message}`);
    console.error('       Start Stash, pass --url, or use a cached --schema file.');
    process.exit(2);
  }
  const body = await res.json();
  if (body.errors) {
    console.error('ERROR: introspection failed: ' + JSON.stringify(body.errors).slice(0, 300));
    process.exit(2);
  }
  return body.data;
}

// --------------------------------------------------------------- extraction

// GraphQL bodies never contain escaped backticks, so a non-greedy match between
// backticks is sufficient to pull template-literal documents out of the source.
function extractDocuments(src) {
  const out = [];
  const re = /`[^`]*`/gs;
  let m;
  while ((m = re.exec(src)) !== null) {
    const body = m[0].slice(1, -1);
    if (!/^\s*(query|mutation|subscription|fragment)\s/m.test(body)) continue;
    if (!body.includes('{')) continue;
    out.push({
      body,
      line: src.slice(0, m.index).split('\n').length,
      // A document assembled with ${...} cannot be parsed as-is. Report it
      // rather than guessing at the interpolated value.
      interpolated: body.includes('${'),
      name: (body.match(/(query|mutation|subscription|fragment)\s+(\w+)/) || [])[2] || '(anonymous)',
    });
  }
  return out;
}

// Baseline keys deliberately exclude line numbers so that unrelated edits above
// a known violation do not invalidate the baseline.
const keyOf = (file, name, message) => `${path.basename(file)}::${name}::${message}`;

// -------------------------------------------------------------------- check

const introspection = await loadSchema();
if (saveSchema && typeof saveSchema === 'string') {
  const dest = path.resolve(REPO, saveSchema);
  fs.mkdirSync(path.dirname(dest), { recursive: true });
  fs.writeFileSync(dest, JSON.stringify(introspection));
  console.log(`schema cached -> ${saveSchema}`);
}

const schema = buildClientSchema(introspection);

let baseline = new Set();
if (!updateBaseline && fs.existsSync(BASELINE)) {
  baseline = new Set(JSON.parse(fs.readFileSync(BASELINE, 'utf8')).accepted || []);
}

const found = [];
let total = 0;
let skipped = 0;

for (const file of files) {
  const abs = path.resolve(REPO, file);
  if (!fs.existsSync(abs)) {
    console.error(`?? MISSING  ${file}`);
    process.exitCode = 2;
    continue;
  }
  const docs = extractDocuments(fs.readFileSync(abs, 'utf8'));
  console.log(`\n=== ${file} — ${docs.length} document(s) ===`);
  for (const doc of docs) {
    total++;
    if (doc.interpolated) {
      skipped++;
      console.log(`  ~~ ${file}:${doc.line} ${doc.name} — skipped (JS interpolation)`);
      continue;
    }
    let messages = [];
    try {
      messages = validate(schema, parse(doc.body), specifiedRules).map((e) => e.message);
    } catch (e) {
      messages = [`PARSE ERROR: ${e.message}`];
    }
    if (messages.length === 0) {
      console.log(`  ok ${file}:${doc.line} ${doc.name}`);
      continue;
    }
    const entries = messages.map((message) => ({
      file,
      line: doc.line,
      name: doc.name,
      message,
      key: keyOf(file, doc.name, message),
    }));
    found.push(...entries);
    const isNew = entries.some((e) => !baseline.has(e.key));
    console.log(`  ${isNew ? 'XX' : '--'} ${file}:${doc.line} ${doc.name}${isNew ? '' : ' (baselined)'}`);
    for (const e of entries) console.log(`       ${e.message}`);
  }
}

if (updateBaseline) {
  const accepted = [...new Set(found.map((e) => e.key))].sort();
  fs.writeFileSync(
    BASELINE,
    JSON.stringify(
      {
        _comment:
          'Known-accepted GraphQL schema violations. Each entry is a PRE-EXISTING ' +
          'bug that is tolerated because a runtime fallback covers it. Shrink this ' +
          'list; never grow it without a written reason. Regenerate with ' +
          '`node scripts/check_graphql_schema.mjs --update-baseline`.',
        accepted,
      },
      null,
      2
    ) + '\n'
  );
  console.log(`\nbaseline updated — ${accepted.length} accepted violation(s)`);
  process.exit(0);
}

const fresh = found.filter((e) => !baseline.has(e.key));
const baselined = found.length - fresh.length;

console.log(
  `\n===== ${total} documents | ${fresh.length} NEW violation(s) | ` +
    `${baselined} baselined | ${skipped} skipped =====`
);

if (fresh.length > 0) {
  console.log('\nNEW violations (these will be rejected by the live server):');
  for (const e of fresh) console.log(`  ${e.file}:${e.line} ${e.name} — ${e.message}`);
  console.log(
    '\nA rejected document fails as a WHOLE — one bad field takes down the entire\n' +
      'query. Fix it, or if a runtime fallback genuinely covers it, record it with\n' +
      '  node scripts/check_graphql_schema.mjs --update-baseline'
  );
  process.exit(1);
}

console.log('No new schema violations.');
process.exit(process.exitCode || 0);
