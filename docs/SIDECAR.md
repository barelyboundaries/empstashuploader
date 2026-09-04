# The sidecar

[← Back to the README](../README.md)

A small FastAPI backend that runs alongside Stash and binds to `127.0.0.1:9941`.

Ordinary builds work without it. **Consolidation and its collision pre-check require
it.**

## What it provides

- **Large-selection transport.** Selections of 66+ scenes are carried through a
  short-lived token rather than the URL, so browser URL length limits and CSP iframe
  blocking never truncate a big pack.
- **A directory browser.** Browse the server filesystem when choosing seed and scratch
  directories, instead of typing paths by hand.
- **Health prefill.** The review UI prefills the directory fields from the sidecar's
  health endpoint when it opens.

## Starting it

Run from the distribution root:

```bash
./start_backend.sh
```

```powershell
.\start_backend.ps1
```

The script uses the virtual environment the installer created under `plugin/.venv`. If
that is missing, it tells you to run the installer first.

The wizard's header badge shows whether the sidecar is connected and running the
expected version, so you can tell at a glance whether consolidation is available.

## The port is fixed

`9941` is a constant, not a preference. The CSP in the plugin manifest, the frontend,
and the backend are all pinned to it. If the port is already occupied, the start scripts
print a port-in-use error and exit rather than silently binding elsewhere.

## Security

The sidecar binds to `127.0.0.1` only and has **no authentication**. Never expose it
beyond the local machine — no port forwarding, no reverse proxy, no `0.0.0.0`.

## Known limitation: scene links on port 9941

When the review UI is opened through the sidecar itself (port 9941) rather than through
Stash, "Open scene in Stash" links fall back to Stash's default port `9999`. If your
Stash listens on a different port, those links will point to the wrong place. Opening
the wizard from within Stash — the normal path — is unaffected.

## Troubleshooting

If consolidation fails with either of these:

```
Consolidation aborted: destination check failed — Filesystem probe failed (HTTP 404)
Consolidation aborted: destination check failed — Filesystem probe failed: destination not reachable
```

**Cause.** The sidecar is not running, or the installed version is outdated.

**Fix.** Run `start_backend.ps1` / `start_backend.sh` from the distribution root. If the
virtual environment is missing, run `install.ps1` / `install.sh` first to create it. If
the header badge reports an outdated version, restart the sidecar so it picks up the
installed build.
