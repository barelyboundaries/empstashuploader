# Troubleshooting

[← Back to the README](../README.md)

## Install and startup

| Symptom | Cause / fix |
|---|---|
| Friendly "missing packages" message when a task starts | The virtual environment was never created, or an update wiped it. Run the installer script in the plugin folder. |
| `python` not found when a task runs | Python 3.12+ is not on the PATH that Stash sees. Install it and reload plugins. On distributions shipping only `python3`, install `python-is-python3`. |
| `pip install -r plugin/requirements.txt` fails | Expected — `requirements.txt` is not standalone-installable. vcsi 7.0.17 pins `pillow==11.2.1` and `numpy==2.2.6`, which conflict with `pillow>=12`. Use `install.ps1` / `install.sh`, which install vcsi with `--no-deps`. |
| `fork/exec ...{pluginDir}...` not found | `{pluginDir}` was used in position 0 of the `exec` element. Put a real binary there and the placeholder in an argument. |

## Plugin registration

| Symptom | Cause / fix |
|---|---|
| Plugin missing from `{ plugins { id } }` | The manifest failed to parse. Check `empornium-megapack.yml`. |
| Registered under the ID `plugin` | The manifest was renamed to `plugin.yml`. The ID comes from the filename — keep it named `empornium-megapack.yml`. |
| Modal opens blank, or the iframe 404s | The `ui.assets` mapping is missing, or `/plugins/` was used where `/plugin/` was required. |
| Plugin settings missing from Settings → Plugins (no ▾ chevron on the row) | Stash caches plugin manifests and only re-reads them on **Reload plugins** (button at the top of Settings → Plugins) or a restart. Deploying a new `empornium-megapack.yml` is not enough — new settings and tasks stay invisible until you reload. Confirm with `{ plugins { id settings { name } } }`: an empty list means the manifest was never re-read. |
| Stale UI behavior after upgrading the plugin | A Stash tab open across upgrades may keep running the previous `review.js`. Automatic version checks now handle this on modal open, but upgrading from a build older than this fix requires one hard reload (`Ctrl+Shift+R`). |

## Running a build

| Symptom | Cause / fix |
|---|---|
| Task dispatch returns HTTP 400 | Plugin task args must be objects, not raw strings. |
| Progress bar hangs at 5% forever | WebSocket subprotocol mismatch — the subscription must use `graphql-transport-ws`. |
| Contact sheets fail | `vcsi` or `ffmpeg` is missing from the PATH of the Stash process. See [Requirements](../README.md#requirements). |
| Seed/scratch directory empty on first run | Set them in stage 2 of the wizard. They deliberately carry no machine-specific defaults; prefill them from `config.local.toml` if you want them remembered. |
| Build blocked: "unresolved filename collision(s)" | Two or more selected scenes share a media filename and would overwrite each other. Resolve them in stage 3 before continuing. |
| Release marked **not ready**, copy buttons disabled | The pre-flight checklist failed. The most common cause is BBCode still containing local `file:///` URLs — enable preview upload, or re-run once the image host is reachable. |
| Consolidation aborts with a destination check failure | The sidecar is not running or is outdated. See [Sidecar](SIDECAR.md#troubleshooting). |

## Still stuck

Check the Stash logs first — the plugin task runs as a child of the Stash process, and
its failures surface there. The build also publishes its result to the logs, so a build
that reports "unverified" in the UI usually has the real error waiting in the Stash log.
