import asyncio
import json
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .config import get_settings
from .models import (
    TokenCreateRequest,
    TokenCreateResponse,
    TokenGetResponse,
)
from .run_store import run_store, RUN_ID_REGEX
from .tags import TagVocabularyError, load_vocabulary
from .token_store import token_store
from .torrents import sanitize_announce_url

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    async def periodic_sweep():
        while True:
            try:
                await asyncio.sleep(600)  # Every 10 minutes
                token_store.sweep_expired()
                run_store.sweep_expired()
            except asyncio.CancelledError:
                break
            except Exception:
                pass

    sweep_task = asyncio.create_task(periodic_sweep())
    try:
        yield
    finally:
        sweep_task.cancel()
        try:
            await sweep_task
        except asyncio.CancelledError:
            pass


app = FastAPI(title="Empornium Megapack Builder Backend", version="0.2.0", lifespan=lifespan)

allow_origins = list(settings.allow_origins)
for default_origin in ["http://127.0.0.1:9999", "http://localhost:9999"]:
    if default_origin not in allow_origins:
        allow_origins.append(default_origin)
if settings.debug_harness:
    for harness_origin in ["http://127.0.0.1:9941", "http://localhost:9941"]:
        if harness_origin not in allow_origins:
            allow_origins.append(harness_origin)

app.add_middleware(
    CORSMiddleware,
    allow_origins=allow_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_ALLOWED_HOSTS = {"127.0.0.1", "localhost", "::1", "[::1]", "testserver"}


@app.middleware("http")
async def restrict_host_header(request: Request, call_next):
    """Reject (403) requests whose Host header hostname is not loopback —
    blocks DNS-rebinding and cross-origin simple requests from reading
    /health or /api/fs/exists. The port is stripped before comparison
    (IPv6 bracket form included). A MISSING Host header (HTTP/1.0) is
    allowed: browsers always send Host, so it is not a rebinding vector.
    """
    host = request.headers.get("host")
    if host is not None:
        hostname = host.lower()
        if hostname.startswith("["):
            end = hostname.find("]")
            hostname = hostname[: end + 1] if end != -1 else hostname
        elif ":" in hostname:
            hostname = hostname.rsplit(":", 1)[0]
        if hostname not in _ALLOWED_HOSTS:
            return JSONResponse(
                status_code=403, content={"detail": "Untrusted Host header"}
            )
    return await call_next(request)


@app.post("/api/token", response_model=TokenCreateResponse)
def create_token_endpoint(request: TokenCreateRequest, req: Request):
    """
    Store scene IDs in temporary token store with 1-hour TTL.
    Validates that sceneIds is a non-empty list of positive integers (max 200).
    Enforces in-memory rate limiting (max 10 creates per 60 seconds).
    """
    client_ip = req.client.host if req.client else "127.0.0.1"
    if not token_store.check_rate_limit(client_ip=client_ip, max_requests=10, window_seconds=60.0):
        raise HTTPException(
            status_code=429, detail="Too many token creation requests. Rate limit: 10 per minute."
        )

    try:
        token = token_store.create_token(request.sceneIds)
        return TokenCreateResponse(token=token)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/token/{token}", response_model=TokenGetResponse)
def get_token_endpoint(token: str):
    """
    Retrieve scene IDs for a given token. Returns 404 if expired or not found.
    """
    scene_ids = token_store.get_token(token)
    if scene_ids is None:
        raise HTTPException(status_code=404, detail="Token not found or expired")
    return TokenGetResponse(sceneIds=scene_ids)


def get_build_stamp() -> Optional[str]:
    """Retrieve the build stamp from env or BUILD_STAMP metadata file.

    Returns:
        str | None: String stamp (e.g. '0.2.0-639bc89') or None if unversioned/dev checkout.
    """
    env_stamp = os.environ.get("EMPORNIUM_BUILD_STAMP")
    if env_stamp and env_stamp.strip():
        return env_stamp.strip()

    current_dir = Path(__file__).resolve().parent
    candidates = (
        current_dir / "BUILD_STAMP",          # vendored inside empornium_megapack/
        current_dir.parent / "BUILD_STAMP",   # plugin root or backend root
    )
    for path in candidates:
        if path.is_file():
            try:
                content = path.read_text(encoding="utf-8").strip()
                if content:
                    return content
            except Exception:
                pass
    return None


# Resolved once, at import, and never re-read.
#
# /health used to call get_build_stamp() per request, which reads BUILD_STAMP
# off disk. Deploying a new plugin build rewrites that file underneath the
# already-running sidecar, so the OLD process immediately began reporting the
# NEW stamp. The plugin's StartBackend task compares /health's build_stamp
# against the installed stamp to decide whether a sidecar is stale and needs
# restarting -- so after any upgrade the two always matched, StartBackend
# adopted the old process, and the check could never fire in the one situation
# it exists for. Freezing the value at process start is what makes that
# comparison mean "the code this process is running" instead of "whatever is
# on disk right now".
_build_stamp_cache: Optional[str] = None
_build_stamp_cached: bool = False


def current_build_stamp() -> Optional[str]:
    """The build stamp as of this process's start.

    Deliberately does NOT track later changes to BUILD_STAMP on disk; see the
    note above. Use get_build_stamp() for a live read of what is on disk.
    """
    global _build_stamp_cache, _build_stamp_cached
    if not _build_stamp_cached:
        _build_stamp_cache = get_build_stamp()
        _build_stamp_cached = True
    return _build_stamp_cache


def reset_build_stamp_cache() -> None:
    """Drop the cached stamp so the next call re-resolves. Tests only."""
    global _build_stamp_cache, _build_stamp_cached
    _build_stamp_cache = None
    _build_stamp_cached = False


# Prime at import so the value reflects process start, not first request.
current_build_stamp()


@app.get("/health")
def health():
    return {
        "status": "ok",
        "track": "Empornium Megapack Builder",
        "version": app.version,
        "build_stamp": current_build_stamp(),
        "stash_url": settings.stash_url,
        "staging_dir": str(settings.staging_dir),
        "output_dir": str(settings.output_dir),
        "scratch_dir": str(settings.scratch_dir),
        "file_time_policy": settings.file_time_policy,
        "file_time_ascending": settings.file_time_ascending,
        "contact_sheet_layout": settings.contact_sheet_layout,
        "hamster_configured": bool(settings.hamster_api_key.strip()),
        "announce_configured": bool(settings.empornium_announce_url.strip()),
        "bundle_after_build": settings.bundle_after_build,
    }


@app.get("/api/tags/vocabulary")
def get_tags_vocabulary():
    """Return the curated Empornium tag vocabulary mapping and ignored list.

    Returns:
        {"map": {stash_tag_lower: [emp_tag, ...]}, "ignored": [stash_tag_lower, ...]}
    """
    try:
        vocab = load_vocabulary()
        return {
            "map": vocab.map,
            "ignored": sorted(vocab.ignored),
        }
    except TagVocabularyError as err:
        raise HTTPException(status_code=500, detail=str(err))


_MAX_FS_EXISTS_PATHS = 100
_MAX_PATH_LEN = 500


@app.post("/api/fs/exists")
async def fs_exists(request: Request):
    """Probe filesystem existence for a bounded list of absolute paths.

    Returns ``{"results": {"<path>": true|false}}`` per path.
    Existence is ``os.path.isfile()`` only — no listing, globbing, or deletion.
    Dict-level validation raises HTTP 400 (not Pydantic 422).
    """
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    if not isinstance(body, dict) or "paths" not in body:
        raise HTTPException(status_code=400, detail="Missing required field: paths")

    paths = body["paths"]
    if not isinstance(paths, list):
        raise HTTPException(status_code=400, detail="paths must be a list")

    if len(paths) > _MAX_FS_EXISTS_PATHS:
        raise HTTPException(
            status_code=400,
            detail=f"Too many paths (max {_MAX_FS_EXISTS_PATHS})",
        )

    for p in paths:
        if not isinstance(p, str):
            raise HTTPException(status_code=400, detail="Each path must be a string")
        if len(p) > _MAX_PATH_LEN:
            raise HTTPException(
                status_code=400,
                detail=f"Path exceeds {_MAX_PATH_LEN} characters",
            )
        if not os.path.isabs(p):
            raise HTTPException(status_code=400, detail=f"Path must be absolute: {p}")
        # Reject UNC paths — os.path.isfile on a dead share blocks the event
        # loop. Separators are normalized first so every spelling Windows
        # accepts is caught: \\server\share, //server/share, and the mixed
        # forms \/server and /\server.
        if p.replace("/", "\\").startswith("\\\\"):
            raise HTTPException(status_code=400, detail=f"UNC paths are not allowed: {p}")

    results = {p: await asyncio.to_thread(os.path.isfile, p) for p in paths}
    return {"results": results}


_MAX_RUN_BODY_BYTES = 2 * 1024 * 1024


@app.post("/api/run/{run_id}")
async def post_run_result(run_id: str, request: Request):
    if not RUN_ID_REGEX.match(run_id):
        raise HTTPException(status_code=400, detail="Invalid run_id format")

    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > _MAX_RUN_BODY_BYTES:
                raise HTTPException(status_code=400, detail="Payload exceeds 2MB limit")
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid Content-Length header")

    raw_body = await request.body()
    if len(raw_body) > _MAX_RUN_BODY_BYTES:
        raise HTTPException(status_code=400, detail="Payload exceeds 2MB limit")

    try:
        data = json.loads(raw_body)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="Payload must be a JSON object")

    # Defense-in-depth sanitization of announce_url field if present
    if "announce_url" in data and isinstance(data["announce_url"], str):
        data["announce_url"] = sanitize_announce_url(data["announce_url"])

    run_store.store_run(run_id, data)
    return {"status": "ok", "run_id": run_id}


@app.get("/api/run/{run_id}")
def get_run_result(run_id: str):
    if not RUN_ID_REGEX.match(run_id):
        raise HTTPException(status_code=400, detail="Invalid run_id format")

    result = run_store.get_run(run_id)
    if result is None:
        return {"found": False}
    return {"found": True, "result": result}


@app.post("/api/shutdown")
async def shutdown_endpoint():
    """Gracefully shuts down the sidecar process after returning HTTP 200."""
    async def _delayed_shutdown(delay: float = 0.2):
        await asyncio.sleep(delay)
        if "PYTEST_CURRENT_TEST" not in os.environ and "TESTING" not in os.environ:
            os._exit(0)

    asyncio.create_task(_delayed_shutdown())
    return {"status": "ok", "detail": "Server shutting down"}


