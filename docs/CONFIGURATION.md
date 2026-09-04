# Configuration

[← Back to the README](../README.md)

## Where the config file goes

The backend reads `config.local.toml`, looking in this order:

1. The repository root (a development checkout).
2. The package's parent directory — which is the plugin folder when the package is
   vendored at `~/.stash/plugins/empornium-megapack`.

So in an **installed plugin**, put `config.local.toml` next to `task.py` in the plugin
folder. In a **development checkout**, put it at the repository root.

> [!WARNING]
> **Never commit this file.** It holds your image host API key and your announce URL.
> It is gitignored as `config.local.*`.

## Environment variables

Every setting can also be supplied through an environment variable named
`EMPORNIUM_` + the field name, uppercased. That takes precedence over the file:

```bash
EMPORNIUM_HAMSTER_API_KEY=...             # overrides hamster_api_key
EMPORNIUM_STASH_API_KEY=...               # overrides stash_api_key
EMPORNIUM_EMPORNIUM_ANNOUNCE_URL=...      # overrides empornium_announce_url
```

The doubled prefix on the last one is not a typo: the field itself is named
`empornium_announce_url`, and the `EMPORNIUM_` prefix is applied on top of it.

This is the better option if you would rather not have secrets on disk at all.

## Runtime directories

Staging, output and scratch default to `~/.empornium-megapack/runtime/` on end-user
machines. They are deliberately **never** placed under `~/.stash`: Stash watches that
tree and would churn on every plugin reload. In a development checkout they stay under
the repository's own `runtime/` folder.

## Full template

Every field from the backend's `Settings` class, with its empty or default value. Fill
in only what you need.

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
presentation_banner = "plate"
vcsi_binary = ""
ffmpeg_binary = ""
empornium_announce_url = ""
empornium_site_url = ""
torrent_source = ""
bundle_after_build = false
pack_download_timeout = 3600
path_mappings = []
```

### The settings you will actually set

| Setting | Why |
|---|---|
| `hamster_api_key` | Required for remote image hosting. Without it, the BBCode keeps local `file:///` URLs and the pre-flight check fails the release. |
| `empornium_announce_url` | Embedded in the built torrent. Never logged, never shown in the UI. |
| `stash_api_key` | Needed if your Stash requires authentication. |
| `output_dir` / `scratch_dir` | Prefill the wizard's stage 2 fields so you don't retype them each run. |
| `contact_sheet_layout` | Contact sheet grid, `3x6` by default. |
| `single_scene_screens` | How many screens a Single Scene release grid contains. |
| `presentation_banner` | The header that opens every presentation. See below. |
| `ffmpeg_binary` | Set this if ffmpeg isn't on the PATH Stash sees. |
| `path_mappings` | Translate Stash's view of a path to the backend's, when they differ (containers, network mounts). |

## Behavior notes

- **Contact sheets degrade and continue.** If an image fails, the build carries on with
  an explicit placeholder rather than aborting. This is the default and by design;
  there is no strictness toggle. The pre-flight checklist is what catches the result.
- **Contact sheets are folded into a spoiler.** Both a single-scene release and a
  megapack put their sheets behind one `[spoiler=Show N contact sheets]` click, so a
  long pack opens as a readable post instead of a wall of thumbnails. Whether that also
  saves the reader bandwidth depends on Empornium's spoiler: browsers do fetch images
  inside a `display:none` container, so the saving is real only if the tracker injects
  the spoiler's HTML on click rather than hiding it with CSS.
- **The announce URL lives only inside the built `.torrent`.** It never appears in the
  UI, the logs, or the BBCode.
- **The presentation banner is pure BBCode.** No hosted logo, so there is nothing to keep
  alive and nothing charged against `presentation_max_bytes`. It paints its own background
  and text colors, so it renders the same on every Empornium skin, and it always links to
  [Stash](https://stashapp.cc) and to
  [the uploader repo](https://github.com/barelyboundaries/empstashuploader).

  | `presentation_banner` | Header |
  |---|---|
  | `plate` (default) | Masthead carrying the release title, plus a spec strip: scenes, runtime, size, top resolution, codec. Replaces the old centred title line. |
  | `rail` | One slim credit bar above the post. The centred title line stays. |
  | `signature` | A muted one-line credit above a rule. The centred title line stays. |
  | `off` | No banner at all. |

  A single build can override the configured default by passing `banner` in the task
  payload — useful for one-off uploads without editing the config.

## Updating and uninstalling

**Update.** Re-run the Add Source install, or re-copy the zip over the plugin folder.
Whether the in-app installer wipes the folder depends on your Stash version, so **back
up `config.local.toml` before updating**. If the virtual environment is gone afterwards,
re-run the installer script in the plugin folder to recreate it.

**Uninstall.** Delete the plugin folder (`~/.stash/plugins/empornium-megapack`) and, if
present, `~/.empornium-megapack/`.
