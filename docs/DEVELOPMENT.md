# Development

[← Back to the README](../README.md)

## Layout

| Path | What it is |
|---|---|
| `plugin/` | The Stash plugin itself — manifest, `task.py`, `main.js`, and the review UI under `assets/` |
| `backend/empornium_megapack/` | The FastAPI sidecar and the build pipeline (torrents, contact sheets, BBCode, staging) |
| `scripts/` | Build and verification tooling |
| `tests/backend/`, `tests/e2e/` | pytest suites |
| `tests/plugin/` | Playwright suites for the review UI |

## Tests

Backend and end-to-end:

```bash
pytest tests/backend tests/e2e -q
```

Playwright UI suites:

```bash
npx playwright test
```

The Playwright suites mock at the **network layer** — `page.route("**/graphql")` plus
the sidecar endpoints on `:9941`. Nothing leaks to a real Stash or a real backend.

## GraphQL schema conformance

Because the Playwright suites mock every GraphQL response, a query that the real Stash
would reject still looks green. This script validates each embedded GraphQL document
against the live schema by introspection:

```bash
node scripts/check_graphql_schema.mjs
```

It requires a live Stash on port 9999 for the introspection; everything else in the repo
is offline-safe. Exit 1 means a new violation. Pre-existing violations that are covered
by runtime fallbacks are recorded in `scripts/graphql_schema_baseline.json` — **shrink
that list, don't grow it.**

## The deleteFiles contract

Schema conformance proves a call is well-formed, not that the server will allow it. This
script pins the server-side rules that the consolidation path depends on:

```bash
node scripts/contract_delete_files_live.mjs --i-know-this-writes-to-stash
```

It writes to Stash, so it refuses to run without the opt-in flag. It generates its own
clip, never touches a pre-existing scene, and cleans up after itself.

## README screenshots

The screenshot set under `docs/screenshots/` is generated, not hand-captured:

```bash
node scripts/capture_readme_screenshots.mjs
```

It serves the review UI straight off disk and answers every Stash call, sidecar
endpoint and job result from synthetic fixtures defined at the top of the script. The
scenes are invented and the thumbnails are a flat placeholder, so the output is always
safe to publish. Re-run it whenever the UI changes.

## Releases

Each release bumps the `version:` field in `plugin/empornium-megapack.yml`. The published
index version derives from it as `<version>-<shortsha>`, so keep the field in sync with
the release you are publishing.

`.github/workflows/pages.yml` builds the plugin zip and publishes `index.yml` to GitHub
Pages on every push to `main` that touches the plugin, the backend package, the zip
builder, or the workflow itself. That published `index.yml` is the Add Source URL users
install from.

## Secrets hygiene

`scripts/check_secrets.ps1` scans tracked files for secret-shaped strings. Secrets belong
only in `config.local.toml` (gitignored) or `EMPORNIUM_` environment variables.
