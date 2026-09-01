#!/usr/bin/env node
/**
 * Live-Stash CONTRACT test for `deleteFiles` (dev tool, opt-in, WRITES to Stash).
 *
 * Why this exists
 * ---------------
 * The Playwright suite mocks every GraphQL response, so it can only assert the
 * SHAPE of a call, never the server's rules about it. That gap shipped a real
 * bug: the consolidation Replace path called `deleteFiles` on a scene's primary
 * file, which Stash always refuses ("cannot delete primary file <path>"). Every
 * mocked test passed. The failure only appeared in production, after 26 of 30
 * files had already been moved.
 *
 * This test pins the two server-side rules the Replace path depends on:
 *
 *   A. deleteFiles REFUSES a scene's primary file.
 *   B. deleteFiles SUCCEEDS on a non-primary file of the same scene, and the
 *      file actually leaves the disk (it is a disk+DB delete, unlike
 *      destroyFiles, which is DB-only).
 *
 * Proving (A) also confirms the ordering assumption the UI relies on — that
 * `Scene.files` is returned primary-first, so files[0] identifies the primary.
 *
 * Safety
 * ------
 * This NEVER touches a pre-existing scene. It generates its own video with
 * ffmpeg, writes two byte-identical copies into a scratch folder inside a
 * configured library path, and scans only that folder. Stash fingerprints them
 * identically and attaches both files to ONE scene — the same shape that
 * produced the original bug.
 *
 * Assertion (A) is safe by construction: if Stash refuses, as expected, nothing
 * is deleted; if it unexpectedly allows the delete, the only file lost is one
 * this script created seconds earlier. Assertion (B) is deliberately
 * destructive, again only against its own file. Cleanup runs in a finally
 * block: the scene is destroyed and the scratch folder removed.
 *
 * It refuses to run without an explicit opt-in flag, because it writes to a
 * live library.
 *
 * Usage:
 *   node scripts/contract_delete_files_live.mjs --i-know-this-writes-to-stash
 *   node scripts/contract_delete_files_live.mjs --i-know-this-writes-to-stash --root "E:\\Stash"
 *   node scripts/contract_delete_files_live.mjs --i-know-this-writes-to-stash --keep   # skip cleanup (debugging)
 *
 * Exit codes: 0 = contract holds, 1 = contract violated, 2 = setup problem.
 */

import fs from 'node:fs';
import path from 'node:path';

const argv = process.argv.slice(2);
function flag(name) {
  const i = argv.indexOf(name);
  if (i === -1) return null;
  const v = argv[i + 1];
  argv.splice(i, v && !v.startsWith('--') ? 2 : 1);
  return v && !v.startsWith('--') ? v : true;
}

const OPT_IN = flag('--i-know-this-writes-to-stash') === true;
const KEEP = flag('--keep') === true;
const ROOT_OVERRIDE = flag('--root');
const URL = flag('--url') || process.env.STASH_URL || 'http://127.0.0.1:9999/graphql';
const FFMPEG =
  flag('--ffmpeg') ||
  process.env.FFMPEG ||
  path.join(process.env.USERPROFILE || '', '.stash', 'ffmpeg.exe');

const SCRATCH_DIR_NAME = '_empornium_contract_tmp';

if (!OPT_IN) {
  console.error(
    'REFUSING TO RUN.\n\n' +
      'This test writes to your live Stash library: it creates a scratch folder,\n' +
      'generates a video, triggers a scan, and then deletes what it created.\n' +
      'It never touches a pre-existing scene.\n\n' +
      'Re-run with --i-know-this-writes-to-stash if that is what you want.'
  );
  process.exit(2);
}

// ------------------------------------------------------------------ plumbing

async function gql(query, variables) {
  const res = await fetch(URL, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ query, variables }),
  });
  const body = await res.json();
  return body; // callers inspect .data / .errors themselves
}

async function gqlOrThrow(query, variables) {
  const body = await gql(query, variables);
  if (body.errors) throw new Error(JSON.stringify(body.errors.map((e) => e.message)));
  return body.data;
}

const results = [];
function check(name, passed, detail) {
  results.push({ name, passed, detail });
  console.log(`  ${passed ? 'PASS' : 'FAIL'}  ${name}${detail ? `\n          ${detail}` : ''}`);
}

async function waitForJob(jobId, label, timeoutMs = 120000) {
  const started = Date.now();
  for (;;) {
    const data = await gqlOrThrow(
      `query FindJob($id: ID!) { findJob(input: { id: $id }) { id status progress } }`,
      { id: jobId }
    );
    const status = data.findJob ? data.findJob.status : 'FINISHED'; // job drops off the queue when done
    if (['FINISHED', 'CANCELLED', 'FAILED'].includes(status)) return status;
    if (Date.now() - started > timeoutMs) throw new Error(`${label} did not finish within ${timeoutMs}ms`);
    await new Promise((r) => setTimeout(r, 1000));
  }
}

// -------------------------------------------------------------------- set up

let scratchDir = null;
let sceneId = null;

try {
  // Pick a library root Stash already scans, so a scoped scan will pick our
  // scratch folder up.
  const cfg = await gqlOrThrow(`{ configuration { general { stashes { path excludeVideo } } } }`);
  const stashes = (cfg.configuration.general.stashes || []).filter((s) => !s.excludeVideo);
  if (stashes.length === 0) {
    console.error('ERROR: Stash has no video-enabled library paths configured.');
    process.exit(2);
  }
  const root = typeof ROOT_OVERRIDE === 'string' ? ROOT_OVERRIDE : stashes[0].path;
  if (typeof ROOT_OVERRIDE === 'string' && !stashes.some((s) => root.startsWith(s.path))) {
    console.error(`ERROR: --root ${root} is not inside a configured library path; Stash would not scan it.`);
    process.exit(2);
  }
  if (!fs.existsSync(FFMPEG)) {
    console.error(`ERROR: ffmpeg not found at ${FFMPEG} — pass --ffmpeg <path>.`);
    process.exit(2);
  }

  scratchDir = path.join(root, SCRATCH_DIR_NAME);
  if (fs.existsSync(scratchDir)) {
    console.error(
      `ERROR: ${scratchDir} already exists. A previous run may not have cleaned up.\n` +
        '       Inspect and remove it manually, then re-run.'
    );
    process.exit(2);
  }

  console.log(`Stash:   ${URL}`);
  console.log(`Scratch: ${scratchDir}`);
  console.log('');

  fs.mkdirSync(scratchDir, { recursive: true });
  const fileA = path.join(scratchDir, 'contract_probe.mp4');
  const fileB = path.join(scratchDir, 'contract_probe_copy.mp4');

  // A 1-second clip is enough for Stash to treat it as a VideoFile.
  const { spawnSync } = await import('node:child_process');
  const ff = spawnSync(
    FFMPEG,
    ['-y', '-f', 'lavfi', '-i', 'testsrc=duration=1:size=320x240:rate=10', '-pix_fmt', 'yuv420p', fileA],
    { encoding: 'utf8' }
  );
  if (ff.status !== 0) {
    console.error(`ERROR: ffmpeg failed (exit ${ff.status}):\n${(ff.stderr || '').slice(-800)}`);
    process.exit(2);
  }
  // Byte-identical copy => identical oshash => Stash attaches BOTH files to one
  // scene. This reproduces the exact shape that caused the production failure.
  fs.copyFileSync(fileA, fileB);

  console.log('Scanning scratch folder...');
  const scan = await gqlOrThrow(
    `mutation Scan($input: ScanMetadataInput!) { metadataScan(input: $input) }`,
    { input: { paths: [scratchDir] } }
  );
  await waitForJob(scan.metadataScan, 'scan');

  // ------------------------------------------------------------- assertions

  const found = await gqlOrThrow(
    `query($p: String!) {
       findScenes(scene_filter: { path: { value: $p, modifier: INCLUDES } } filter: { per_page: 10 }) {
         scenes { id files { id path } }
       }
     }`,
    { p: SCRATCH_DIR_NAME }
  );
  const scenes = found.findScenes.scenes || [];
  if (scenes.length !== 1) {
    console.error(
      `ERROR: expected exactly 1 scene from the scratch folder, got ${scenes.length}. ` +
        'Cannot run the contract assertions.'
    );
    process.exit(2);
  }
  sceneId = scenes[0].id;
  const files = scenes[0].files || [];

  console.log('\nContract assertions:');
  check(
    'scan attaches both identical copies to ONE scene (2 files)',
    files.length === 2,
    `got ${files.length} file(s): ${files.map((f) => path.basename(f.path)).join(', ')}`
  );
  if (files.length !== 2) {
    console.error('\nCannot continue: the primary/non-primary distinction needs 2 files.');
    process.exit(2);
  }

  const primary = files[0];
  const secondary = files[1];

  // (A) The rule the Replace path got wrong. Safe: the expected outcome is a
  //     refusal that deletes nothing.
  const refusal = await gql(`mutation Del($ids: [ID!]!) { deleteFiles(ids: $ids) }`, { ids: [primary.id] });
  const refusalMsg = refusal.errors ? refusal.errors.map((e) => e.message).join('; ') : '';
  check(
    'deleteFiles REFUSES a primary file',
    !!refusal.errors,
    refusalMsg || 'NO ERROR RETURNED — Stash accepted the delete of a primary file'
  );
  check(
    'refusal names the primary-file rule (the string the UI keys on)',
    /primary/i.test(refusalMsg),
    refusalMsg ? `message: ${refusalMsg}` : 'no error message to inspect'
  );
  check(
    'refused primary file is still on disk',
    fs.existsSync(primary.path),
    primary.path
  );
  check(
    'Scene.files is primary-first (files[0] is the file Stash calls primary)',
    /primary/i.test(refusalMsg),
    'inferred from the refusal targeting files[0]; the UI relies on this ordering'
  );

  // (B) The path Replace legitimately uses. Destructive, on our own file only.
  const del = await gql(`mutation Del($ids: [ID!]!) { deleteFiles(ids: $ids) }`, { ids: [secondary.id] });
  check(
    'deleteFiles SUCCEEDS on a non-primary file',
    !del.errors && del.data && del.data.deleteFiles === true,
    del.errors ? del.errors.map((e) => e.message).join('; ') : `returned ${JSON.stringify(del.data)}`
  );
  check(
    'deleted non-primary file is gone from DISK (disk+DB, not destroyFiles semantics)',
    !fs.existsSync(secondary.path),
    secondary.path
  );
} catch (err) {
  console.error(`\nERROR: ${err.message}`);
  process.exitCode = 2;
} finally {
  // ------------------------------------------------------------------ clean
  if (KEEP) {
    console.log(`\n--keep set: leaving scene ${sceneId} and ${scratchDir} in place.`);
  } else {
    try {
      if (sceneId != null) {
        await gql(
          `mutation Destroy($input: SceneDestroyInput!) { sceneDestroy(input: $input) }`,
          { input: { id: sceneId, delete_file: true, delete_generated: true } }
        );
      }
    } catch (e) {
      console.error(`cleanup: sceneDestroy failed — ${e.message}`);
    }
    try {
      if (scratchDir && fs.existsSync(scratchDir)) fs.rmSync(scratchDir, { recursive: true, force: true });
    } catch (e) {
      console.error(`cleanup: could not remove ${scratchDir} — ${e.message}`);
    }
    console.log('\nCleaned up scratch scene and folder.');
  }
}

if (process.exitCode === 2) process.exit(2);

const failed = results.filter((r) => !r.passed);
console.log(`\n===== ${results.length} assertions | ${failed.length} failed =====`);
if (failed.length > 0) {
  console.log('\nCONTRACT VIOLATED — the consolidation Replace path relies on these rules:');
  for (const f of failed) console.log(`  - ${f.name}`);
  process.exit(1);
}
console.log('deleteFiles contract holds.');
process.exit(0);
