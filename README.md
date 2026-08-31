# DeepSeek Megapack

A Stash plugin that turns selected scenes into Empornium-ready releases. Pick
scenes in Stash, open the in-Stash review UI, and build a megapack or a
single-scene release: contact sheets, a torrent, and the BBCode post, all in
one pass. An optional helper sidecar handles very large selections and folder
browsing.

This README is the single source of truth for the plugin. It replaces the
older DeepSeek project docs.

## What it is

- **In-Stash UI.** A bulk-action button on the Scenes page opens a review
  wizard inside Stash. The wizard walks through setup, locations, scene
  ordering, and the build actions.
- **Two release modes.** Megapack (many scenes, one torrent) or Single Scene
  (one scene, a full screens grid).
- **Contact sheets.** Each scene gets a contact sheet generated with vcsi and
  uploaded to HamsterImg. The image URLs land in the BBCode.
- **Torrent + BBCode.** The build produces a torrent (private, with the
  announce URL) and the formatted BBCode post ready to paste into Empornium.
- **Optional sidecar.** A small local backend that carries large scene
  selections (66+ scenes) past browser URL limits, offers a directory browser,
  and prefills health checks. See [Sidecar](#sidecar).

## Requirements

- **Stash v0.31 or newer.**
- **Python 3.12+ on PATH.** The plugin task runs as a child of the Stash
  process and Stash execs `python` literally. On Linux distros that ship only
  `python3`, install `python-is-python3` (or an equivalent symlink) so the
  `python` command resolves.
- **ffmpeg, optional with fallbacks.** The backend looks for ffmpeg in this
  order: the `ffmpeg_binary` setting, then PATH, then the cove install, then
  `~/.stash`. ffprobe is derived from the ffmpeg location automatically. If
  none of those exist, contact sheets fail but the rest of the build still
  runs.
- **vcsi.** Auto-installed by the installer into the plugin's virtual
  environment. You do not install it yourself.

## Install

### Option A: from the Stash plugin source

1. In Stash, go to **Settings → Plugins → Add Source** and enter
   `https://ccoggle-ui.github.io/deepseek-megapack/index.yml`.

   The placeholder is the URL of this repository's GitHub Pages `index.yml`.
   It is a literal placeholder because the Pages URL only exists after the
   first push. Replace it with the real URL once the Pages site is live.

2. Install the plugin from that source.
3. Open the installed plugin folder and run the installer:
   - Windows: `install.ps1`
   - macOS/Linux: `./install.sh`

### Option B: manual

1. Download the release zip.
2. Copy its contents to `~/.stash/plugins/deepseek-megapack`.
3. Run the installer from that folder (`install.ps1` on Windows,
   `./install.sh` on macOS/Linux).

The installer verifies Python 3.12+, creates a virtual environment inside the
plugin folder, installs the requirements, probes for ffmpeg, and prints next
steps. It writes nothing outside the plugin folder.

After installing, reload plugins from **Settings → Plugins** (or restart
Stash) and hard-refresh the browser.

## Sidecar

The sidecar is a small FastAPI backend that binds to `127.0.0.1:9941`. It
serves three things:

- **Large-selection transport.** Scene selections of 66+ scenes are carried
  through a short-lived token instead of a URL, so browser URL length limits
  and CSP iframe blocking never bite.
- **Directory browser.** Browse folders when choosing seed and scratch
  directories.
- **Health prefill.** The review UI prefills directory fields from the
  sidecar's health endpoint.

Start it with `start_backend.ps1` (Windows) or `start_backend.sh`
(macOS/Linux) from the distribution root. The script uses the virtual
environment the installer created under `plugin\.venv`; if that is missing it
tells you to run the installer first.

The sidecar binds to `127.0.0.1` only and has no authentication. Never expose
it beyond the local machine.

## Configuration

The backend reads `config.local.toml`. It looks for the file in this order:

1. The repository root (a dev checkout).
2. The package's parent directory, which is the plugin folder when the package
   is vendored at `~/.stash/plugins/deepseek-megapack`.

So in an installed plugin, put `config.local.toml` next to `task.py` in the
plugin folder. In a dev checkout, put it at the repo root.

Runtime directories (staging, output, scratch) default to
`~/.deepseek-megapack/runtime/` on end-user machines. They are never placed
under `~/.stash`, which Stash watches and would churn on plugin reloads. In a
dev checkout they stay under the repo's `runtime/` folder.

Every setting can also be set through an environment variable with the
`DEEPSEEK_` prefix. For example `DEEPSEEK_HAMSTER_API_KEY` overrides
`hamster_api_key`.

Here is the full template. Every field from the backend's `Settings` class is
listed with an empty or default value. Fill in only what you need. **Never
commit this file**; it is gitignored (`config.local.*`).

```toml
[backend]
host = "127.0.0.1"
port = 9941
stash_url = "http://localhost:9999"
stash_api_key = ""
staging_dir = ""
output_dir = ""
scratch_dir = ""
allow_origins = ["http://localhost:9999"]
file_time_policy = "creation"
file_time_ascending = true
stash_fetch_workers = 8
debug_harness = false
hamster_api_key = ""
contact_sheet_layout = "3x6"
contact_sheet_vcsi_timeout = 900
contact_sheet_upload_timeout = 60
contact_sheet_upload_retries = 3
contact_sheet_upload_backoff_base = 0.5
contact_sheet_upload_backoff_max = 15.0
contact_sheet_max_bytes = 10000000
upload_image_max_bytes = 10000000
presentation_max_bytes = 23000000
presentation_min_image_bytes = 120000
single_scene_screens = 10
screen_extract_timeout = 120
include_performer_images = true
include_scene_cover = true
vcsi_binary = ""
ffmpeg_binary = ""
empornium_announce_url = ""
empornium_site_url = ""
torrent_source = ""
bundle_after_build = false
pack_download_timeout = 3600
path_mappings = []
```

Secrets (the HamsterImg API key, the Empornium announce URL) live only in this
file or in `DEEPSEEK_` environment variables. They never appear in committed
files.

## Updates & uninstall

**Update.** Re-run the Add Source install, or re-copy the zip over the plugin
folder. Whether the in-app installer wipes the folder depends on the Stash
version, so back up `config.local.toml` before updating. If the virtual
environment is gone after an update, re-run the installer script in the plugin
folder to recreate it.

**Uninstall.** Delete the plugin folder (`~/.stash/plugins/deepseek-megapack`)
and, if present, `~/.deepseek-megapack/`.

## Releases

Each release bumps the `version:` field in `deepseek-megapack.yml`. The
published index version derives from it as `<version>-<shortsha>`, so keep the
field in sync with the release you are publishing.

## Behavior notes

- **Contact sheets degrade and continue.** If an image fails, the build
  continues with an explicit placeholder rather than aborting. This is the
  default and by design; there is no strictness toggle.
- **The announce URL lives only inside the built `.torrent`.** It never
  appears in the UI, the logs, or the BBCode.

## Security notes

- **Rotate the HamsterImg API key periodically.** See risk R8 in the project
  risks: a previously supplied key fragment must never be accepted, and the
  key should be rotated if it was ever used.
- **The sidecar has no authentication.** It binds to `127.0.0.1` only. Never
  expose it.
- **Secrets live only in `config.local.toml`** (gitignored) or in `DEEPSEEK_`
  environment variables.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Plugin missing from `{ plugins { id } }` | Manifest failed to parse. Check `deepseek-megapack.yml`. |
| Registered as ID `plugin` | The manifest was renamed to `plugin.yml`. Keep it named `deepseek-megapack.yml`; the ID comes from the filename. |
| Modal opens blank / iframe 404 | `ui.assets` mapping missing, or `/plugins/` used instead of `/plugin/`. |
| Task dispatch returns HTTP 400 | Plugin task args must be objects, not raw strings. |
| Progress bar hangs at 5% forever | WebSocket subprotocol mismatch. The subscription must use `graphql-transport-ws`. |
| `python` not found when a task runs | Python 3.12+ is not on the PATH Stash sees. Install it and reload plugins. |
| `fork/exec ...{pluginDir}...` not found | `{pluginDir}` was used in the `exec` element 0 position. Put a real binary there and the placeholder in an argument. |
| Contact sheets fail | `vcsi` or `ffmpeg` missing from the PATH of the Stash process. See Requirements. |
| Friendly missing-packages message at task start | Run the installer script in the plugin folder to set up the virtual environment. |
| Seed/scratch directory empty at first run | Set them in Stage 2 of the wizard. They no longer carry machine defaults. |

## Development

Run the backend tests:

```bash
pytest tests/backend tests/e2e -q
```

Run the Playwright UI tests:

```bash
npx playwright test
```

**GraphQL schema conformance.** The Playwright suite mocks every GraphQL
response, so a query the real Stash rejects still looks green. This script
validates each embedded GraphQL document against the live schema by
introspection:

```bash
node scripts/check_graphql_schema.mjs
```

It requires a live Stash on port 9999 for the introspection. Everything else
in the repo is offline-safe. Exit 1 means a new violation. Pre-existing
violations covered by runtime fallbacks are recorded in
`scripts/graphql_schema_baseline.json`; shrink that list, don't grow it.

**deleteFiles contract.** Schema conformance proves a call is well-formed, not
that the server will allow it. This script pins the server-side rules the
consolidation path depends on:

```bash
node scripts/contract_delete_files_live.mjs --i-know-this-writes-to-stash
```

It writes to Stash, so it refuses to run without the opt-in flag. It generates
its own clip, never touches a pre-existing scene, and cleans up after itself.