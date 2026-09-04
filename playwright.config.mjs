import { defineConfig } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

const evidenceDir = path.resolve(
  process.env.EVIDENCE_DIR ?? 'test-results/evidence',
);
fs.mkdirSync(evidenceDir, { recursive: true });

export default defineConfig({
  testDir: './tests/plugin',
  testMatch: [
    'test_token_66_flow.spec.mjs',
    'test_modal_integration.spec.mjs',
    'test_adversarial_m2.spec.mjs',
    'test_m2_adversarial.spec.mjs',
    'test_adversarial_m3_challenger.spec.mjs',
    'test_challenger_m3_e2e_journey.spec.mjs',
    'test_live_stash_contract.spec.mjs',
    'test_stage6_handoff.spec.mjs',
    'test_directory_browser.spec.mjs',
    'test_duplicate_detection.spec.mjs',
    'test_destination_collision.spec.mjs',
    'test_consolidate_move_only_missing.spec.mjs',
    'test_build_gating_inplace.spec.mjs',
    'test_wizard_stages.spec.mjs',
    'test_asset_parity.spec.mjs',
    'test_graphql_schema_conformance.spec.mjs',
    'test_single_scene_mode.spec.mjs',
    'test_task_failure_detection.spec.mjs',
    'test_result_sentinel_handoff.spec.mjs',
    'test_modal_mount_scroll.spec.mjs',
    'test_sanitize_name_parity.spec.mjs',
    'test_empify_parity.spec.mjs',
    'test_defect_fixes_ux.spec.mjs',
    'test_presentation_budget.spec.mjs',
    'test_sidecar_probe_diagnostics.spec.mjs',
    'test_adversarial_preview_challenger_2.spec.mjs',
    'test_scene_grid_drag.spec.mjs',
    'test_scene_card_interior.spec.mjs',
    'test_bbcode_toolbar.spec.mjs',
    'test_missing_source_reconcile.spec.mjs',
    'test_sidecar_queue_flood.spec.mjs',
    'test_progress_placement.spec.mjs',
    'test_build_lockout.spec.mjs',
    'test_tag_vocabulary_parity.spec.mjs'
  ],
  outputDir: path.join(evidenceDir, 'test-results'),
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 20_000,
  reporter: [['line'], ['json', { outputFile: path.join(evidenceDir, 'playwright-results.json') }]],
  projects: [
    {
      name: 'chromium',
      use: {
        browserName: 'chromium',
        baseURL: process.env.STASH_URL ?? 'http://localhost:9999',
        screenshot: 'only-on-failure',
        trace: 'off',
      },
    },
  ],
});
