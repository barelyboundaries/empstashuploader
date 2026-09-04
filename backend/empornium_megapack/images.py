"""Stage 4: contact sheet generation via vcsi and HamsterImg upload.

Contact sheets are required pack payloads: a scene that cannot produce and
upload a contact sheet fails generation (see ROADMAP Stage 3 contract).
"""

from __future__ import annotations

import email.utils
import hashlib
import io
import os
import random
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import httpx

from .config import CONFIG_LOCAL_NAME, Settings, get_settings

HAMSTER_UPLOAD_URL = "https://hamsterimg.net/api/1/upload"
HAMSTER_IMAGE_MIME = "image/jpeg"
HAMSTER_IMAGE_EXT = "jpg"

_COVE_FFMPEG = Path(os.environ.get("LOCALAPPDATA", "")) / "cove" / "ffmpeg" / "ffmpeg.exe"

_REENCODE_QUALITIES = (85, 70, 55, 40, 25)
_RETRY_AFTER_MAX = 60.0

# Raster types Stash serves for real artwork; anything else (notably
# image/svg+xml) is a generated placeholder, not a picture worth posting.
_STASH_RASTER_TYPES = frozenset({"image/jpeg", "image/jpg", "image/png", "image/webp"})


class ContactSheetError(Exception):
    """Contact sheet generation or upload failed; pack generation must stop."""


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve_ffmpeg(settings: Settings) -> str | None:
    """Locate an ffmpeg binary: explicit config, PATH, cove, then ~/.stash layout."""
    if settings.ffmpeg_binary:
        return settings.ffmpeg_binary
    found = shutil.which("ffmpeg")
    if found:
        return found
    if _COVE_FFMPEG.is_file():
        return str(_COVE_FFMPEG)
    # Mirrors the Stash-bundled layout the legacy Windows launcher relied on.
    for candidate in (
        Path.home() / ".stash" / "ffmpeg.exe",
        Path.home() / ".stash" / "ffmpeg",
    ):
        if candidate.is_file():
            return str(candidate)
    return None


def _vcsi_command(settings: Settings) -> list[str]:
    if settings.vcsi_binary:
        return [settings.vcsi_binary]
    found = shutil.which("vcsi")
    if found:
        return [found]
    for candidate in (
        Path(sys.executable).with_name("vcsi.exe"),
        Path(sys.executable).with_name("vcsi"),
    ):
        if candidate.is_file():
            return [str(candidate)]
    raise ContactSheetError(
        "vcsi not found on PATH; install it in the backend venv or set vcsi_binary."
    )


def generate_contact_sheet(
    video_path: str | Path,
    out_path: str | Path,
    settings: Settings | None = None,
    layout: str | None = None,
    timeout: float | None = None,
) -> bool:
    """Render a grid contact sheet with vcsi. Returns True on success."""
    settings = settings or get_settings()
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    grid = layout or settings.contact_sheet_layout
    cmd = _vcsi_command(settings) + ["-g", grid, str(video_path), "-o", str(out)]
    env = dict(os.environ)
    ffmpeg = resolve_ffmpeg(settings)
    if ffmpeg:
        env["PATH"] = str(Path(ffmpeg).parent) + os.pathsep + env.get("PATH", "")
    timeout_val = timeout if timeout is not None else settings.contact_sheet_vcsi_timeout
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_val,
            env=env,
        )
    except subprocess.TimeoutExpired:
        raise
    except OSError:
        return False
    return proc.returncode == 0 and out.is_file() and out.stat().st_size > 0


def resolve_ffprobe(settings: Settings) -> str | None:
    """Locate ffprobe alongside whichever ffmpeg resolve_ffmpeg() found."""
    found = shutil.which("ffprobe")
    if found:
        return found
    ffmpeg = resolve_ffmpeg(settings)
    if ffmpeg:
        candidate = Path(ffmpeg).with_name("ffprobe" + Path(ffmpeg).suffix)
        if candidate.is_file():
            return str(candidate)
    return None


def probe_duration(video_path: str | Path, settings: Settings | None = None) -> float | None:
    """Duration in seconds via ffprobe, or None if it cannot be determined."""
    settings = settings or get_settings()
    ffprobe = resolve_ffprobe(settings)
    if not ffprobe:
        return None
    cmd = [
        ffprobe,
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(video_path),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except (subprocess.TimeoutExpired, OSError):
        return None
    if proc.returncode != 0:
        return None
    try:
        duration = float(proc.stdout.strip())
    except ValueError:
        return None
    return duration if duration > 0 else None


def screen_timestamps(duration: float, count: int) -> list[float]:
    """
    Evenly spaced sample points, trimmed away from the head and tail.

    Opening logos and end cards make the very first and last frames poor
    thumbnails, so sampling runs across the middle 90% of the runtime.
    """
    if duration <= 0 or count <= 0:
        return []
    start = duration * 0.05
    end = duration * 0.95
    span = end - start
    if count == 1:
        return [start + span / 2]
    step = span / (count - 1)
    return [start + step * i for i in range(count)]


def extract_screens(
    video_path: str | Path,
    out_dir: str | Path,
    prefix: str,
    count: int,
    settings: Settings | None = None,
    duration: float | None = None,
    timeout: float | None = None,
) -> list[Path]:
    """
    Pull `count` evenly spaced stills out of a video with ffmpeg.

    Returns only the frames that were written. Screens are decorative: a video
    that yields none still builds, unlike a missing contact sheet.
    """
    settings = settings or get_settings()
    if count <= 0:
        return []
    ffmpeg = resolve_ffmpeg(settings)
    if not ffmpeg:
        return []
    if duration is None:
        duration = probe_duration(video_path, settings)
    if not duration:
        return []

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    timeout_val = timeout if timeout is not None else settings.screen_extract_timeout

    written: list[Path] = []
    for idx, ts in enumerate(screen_timestamps(duration, count), 1):
        target = out / f"{prefix}_screen_{idx:02d}.{HAMSTER_IMAGE_EXT}"
        cmd = [
            ffmpeg,
            "-nostdin",
            "-y",
            # -ss before -i seeks by keyframe, which is far faster than decoding
            # from zero and is accurate enough for a thumbnail.
            "-ss", f"{ts:.3f}",
            "-i", str(video_path),
            "-frames:v", "1",
            "-q:v", "2",
            str(target),
        ]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_val)
        except (subprocess.TimeoutExpired, OSError):
            continue
        if proc.returncode == 0 and target.is_file() and target.stat().st_size > 0:
            written.append(target)
    return written


def fetch_stash_image(
    endpoint: str,
    out_path: str | Path,
    settings: Settings | None = None,
    timeout: float = 30.0,
) -> Path | None:
    """
    Download an image Stash serves (a performer portrait, a scene cover).

    `endpoint` is a path relative to stash_url, e.g. "/performer/42/image".
    Returns the written path, or None if the fetch failed or returned no bytes.
    """
    settings = settings or get_settings()
    base = str(settings.stash_url or "").rstrip("/")
    if not base:
        return None
    headers = {}
    if settings.stash_api_key:
        headers["ApiKey"] = settings.stash_api_key
    url = f"{base}/{endpoint.lstrip('/')}"
    try:
        response = httpx.get(url, headers=headers, timeout=timeout, follow_redirects=True)
    except httpx.HTTPError:
        return None
    if response.status_code != 200 or not response.content:
        return None
    # Stash auto-generates an SVG initials avatar for performers with no real
    # portrait (and a default card for scenes with no screenshot). Those are
    # per-subject, so size and hash cannot tell them apart -- but they are
    # always image/svg+xml, while genuine artwork is a raster format.
    content_type = (response.headers.get("content-type") or "").split(";")[0].strip().lower()
    if content_type not in _STASH_RASTER_TYPES:
        return None
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(response.content)
    return out


def upload_hamster(
    image_path: str | Path,
    settings: Settings | None = None,
    log_callback: Callable[[str], None] | None = None,
) -> str:
    """Upload an image to HamsterImg. Returns the remote URL.

    Transient failures (network errors, 429, 5xx, malformed JSON, unexpected
    response shape) are retried with exponential backoff plus jitter; the
    ``Retry-After`` header of a 429 is honored (bounded). Definitive failures
    (missing API key, API-reported error) raise ContactSheetError immediately.
    A contact sheet that cannot be uploaded fails pack generation.
    """
    settings = settings or get_settings()
    api_key = settings.hamster_api_key.strip()
    if not api_key:
        raise ContactSheetError(
            "No HamsterImg API key configured; set EMPORNIUM_HAMSTER_API_KEY "
            f"or hamster_api_key in {CONFIG_LOCAL_NAME}."
        )
    data = {"type": "file", "action": "upload", "nsfw": "1", "format": "json"}
    headers = {"accept": "application/json", "X-API-Key": api_key}
    timeout = httpx.Timeout(
        settings.contact_sheet_upload_timeout, connect=10.0
    )
    last_message = ""
    retries = settings.contact_sheet_upload_retries
    for attempt in range(retries):
        retry = False
        backoff: float | None = None
        fail_reason = ""
        try:
            filename = Path(image_path).name or f"preview.{HAMSTER_IMAGE_EXT}"
            with Path(image_path).open("rb") as fh:
                files = {"source": (filename, fh, HAMSTER_IMAGE_MIME)}
                with httpx.Client(timeout=timeout) as client:
                    response = client.post(
                        HAMSTER_UPLOAD_URL, files=files, data=data, headers=headers
                    )
        except httpx.RequestError as exc:
            fail_reason = f"network error: {exc}"
            last_message = fail_reason
            retry = True
        if not retry:
            status = response.status_code
            try:
                payload = response.json()
            except ValueError:
                payload = None
            if status == 429:
                fail_reason = "HTTP 429"
                last_message = "server returned HTTP 429"
                retry = True
                backoff = _retry_after_seconds(response)
            elif status >= 500:
                fail_reason = f"HTTP {status}"
                last_message = f"server returned HTTP {status}"
                retry = True
            elif status >= 400:
                if isinstance(payload, dict) and "error" in payload:
                    message = payload["error"]
                    if isinstance(message, dict):
                        message = message.get("message", message)
                    raise ContactSheetError(f"HamsterImg upload failed: {message}")
                raise ContactSheetError(f"HamsterImg rejected the upload (HTTP {status})")
            else:
                if isinstance(payload, dict) and "error" in payload:
                    message = payload["error"]
                    if isinstance(message, dict):
                        message = message.get("message", message)
                    raise ContactSheetError(f"HamsterImg upload failed: {message}")
                try:
                    return str(payload["image"]["url"])
                except (KeyError, TypeError):
                    fail_reason = "unexpected response shape"
                    last_message = fail_reason
                    retry = True
        if retry and attempt < retries - 1:
            delay = backoff if backoff is not None else _retry_delay(attempt, settings)
            if log_callback:
                log_callback(
                    f"attempt {attempt + 1}/{retries} failed ({fail_reason}), retrying in {delay:.1f}s"
                )
            time.sleep(delay)
    raise ContactSheetError(f"HamsterImg upload failed after retries: {last_message}")


def _retry_delay(attempt: int, settings: Settings) -> float:
    """Exponential backoff with jitter, capped after jitter is applied."""
    delay = settings.contact_sheet_upload_backoff_base * (2 ** attempt)
    jitter = delay * random.uniform(0.0, 0.25)
    return min(delay + jitter, settings.contact_sheet_upload_backoff_max)


def _retry_after_seconds(response) -> float | None:
    """Parse ``Retry-After`` (delta-seconds or HTTP-date), bounded and clamped."""
    raw = response.headers.get("Retry-After")
    if raw is None:
        return None
    try:
        return min(max(float(raw), 0.0), _RETRY_AFTER_MAX)
    except ValueError:
        pass
    try:
        parsed = email.utils.parsedate_to_datetime(raw)
    except (TypeError, ValueError, OverflowError):
        return None
    if parsed is None or parsed.tzinfo is None:
        return None
    remaining = (parsed - datetime.now(timezone.utc)).total_seconds()
    return min(max(remaining, 0.0), _RETRY_AFTER_MAX)


def enforce_size_limit(image_path: str | Path, max_bytes: int) -> None:
    """Deterministically re-encode an oversized JPEG down to ``max_bytes``.

    Policy: re-encode at progressively lower quality, then downscale by 0.75
    per round. The output is deterministic for a given input, so digests stay
    stable across runs. Raises ContactSheetError if no encoding fits.
    """
    path = Path(image_path)
    if path.stat().st_size <= max_bytes:
        return
    from PIL import Image

    with Image.open(path) as image:
        image.load()
        scale = 1.0
        for _ in range(8):
            resized = image
            if scale < 1.0:
                resized = image.resize(
                    (max(1, int(image.width * scale)), max(1, int(image.height * scale))),
                    Image.LANCZOS,
                )
            for quality in _REENCODE_QUALITIES:
                buffer = io.BytesIO()
                resized.save(buffer, format="JPEG", quality=quality, optimize=True, progressive=True)
                data = buffer.getvalue()
                if len(data) <= max_bytes:
                    path.write_bytes(data)
                    return
            scale *= 0.75
        raise ContactSheetError(
            f"Contact sheet cannot be re-encoded under {max_bytes} bytes "
            f"({image.width}x{image.height} at full size)"
        )


class ImageService:
    """Generates and uploads contact sheets, tracking digests per scene.

    Generation, size enforcement, and upload are deterministic: the same
    scene with the same layout yields the same digest, so a cached
    (digest, url) pair avoids re-uploading. The rendered JPEG is kept in the
    staging area under ``contact_sheets`` for later bundling; the mapping
    lives in memory only (Stash is never mutated).

    Resumability: a partial failure (scene N fails after 1..N-1 uploaded)
    leaves the completed mappings in ``_cache``/``sheets`` and their
    digest-named JPEGs on disk, so a rerun of the same pack skips
    re-uploading the completed scenes and retries only the failed one.
    Remote images already uploaded cannot be deleted (HamsterImg exposes no
    deletion API); digest reuse makes re-runs idempotent in practice.
    """

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self.sheet_dir = self.settings.staging_dir / "contact_sheets"
        self._cache: dict[str, tuple[str, str]] = {}
        self.sheets: dict[str, Path] = {}

    def contact_sheet(
        self,
        scene_id: str,
        video_path: str,
        layout: str | None = None,
        log_callback: Callable[[str], None] | None = None,
    ) -> tuple[str, str, Path]:
        """Generate, size-check, and upload one contact sheet.

        Returns ``(url, digest, local_path)``. Raises ContactSheetError on
        generation or upload failure.
        """
        self.sheet_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.sheet_dir / f".{scene_id}.vcsi.jpg"
        try:
            if not generate_contact_sheet(video_path, tmp, self.settings, layout):
                raise ContactSheetError(
                    f"Contact sheet generation failed for scene {scene_id}: {video_path}"
                )
            enforce_size_limit(tmp, self.settings.contact_sheet_max_bytes)
            digest = sha256_file(tmp)
            final = self.sheet_dir / f"{scene_id}.{digest[:8]}.jpg"
            if not final.exists():
                tmp.replace(final)
            cached = self._cache.get(scene_id)
            if cached is not None and cached[0] == digest:
                url = cached[1]
            else:
                url = upload_hamster(final, self.settings, log_callback=log_callback)
                self._cache[scene_id] = (digest, url)
            self.sheets[scene_id] = final
            return url, digest, final
        finally:
            if tmp.exists():
                tmp.unlink()

    def url_for(self, scene_id: str, video_path: str, layout: str | None = None) -> str:
        """URL for an already-generated sheet; generates/uploads if needed.

        Avoids re-running vcsi when the scene's sheet is still cached.
        Raises ContactSheetError on failure (required artifact).
        """
        cached = self._cache.get(scene_id)
        sheet = self.sheets.get(scene_id)
        if cached is not None and sheet is not None and sheet.is_file():
            return cached[1]
        url, _, _ = self.contact_sheet(scene_id, video_path, layout)
        return url

    def digest_for(self, scene_id: str) -> str | None:
        cached = self._cache.get(scene_id)
        return cached[0] if cached else None


def make_thumbnail(src: str | Path, dest: str | Path, max_width: int) -> Path:
    """Downscale an image to `max_width` px wide, preserving aspect ratio.

    Empornium bills the presentation for the bytes of every embedded image,
    so a [img=200] tag pointed at a full-size sheet costs the full file.
    A real thumbnail is what keeps a megapack post under the cap.
    """
    from PIL import Image

    src_p = Path(src)
    dest_p = Path(dest)
    dest_p.parent.mkdir(parents=True, exist_ok=True)

    with Image.open(src_p) as img:
        img.load()
        if img.width <= max_width:
            if src_p.resolve() != dest_p.resolve():
                shutil.copy2(src_p, dest_p)
            return dest_p

        # Convert RGBA/LA/P to RGB before saving
        if img.mode in ("RGBA", "LA", "P"):
            if img.mode in ("RGBA", "LA"):
                background = Image.new("RGB", img.size, (255, 255, 255))
                background.paste(img, mask=img.split()[-1])
                img = background
            else:
                img = img.convert("RGB")
        elif img.mode != "RGB":
            img = img.convert("RGB")

        new_height = max(1, int(img.height * (max_width / img.width)))
        resized = img.resize((max_width, new_height), Image.LANCZOS)
        resized.save(dest_p, format="JPEG", quality=85, optimize=True, progressive=True)
        return dest_p


def fit_presentation_budget(paths: list[Path], budget: int, floor: int) -> tuple[int, list[Path]]:
    """Shrink the images an Empornium presentation embeds to fit `budget`.

    Returns (total_bytes_after, paths_that_could_not_be_shrunk_enough).
    """
    if not paths:
        return 0, []

    path_list = [Path(p) for p in paths]
    # Sort ascending by current size
    sorted_paths = sorted(path_list, key=lambda p: p.stat().st_size if p.exists() else 0)

    remaining_budget = budget
    n = len(sorted_paths)
    failed_paths: list[Path] = []

    for idx, path in enumerate(sorted_paths):
        if not path.exists():
            continue
        current_size = path.stat().st_size
        remaining_count = n - idx
        fair_share = remaining_budget // remaining_count if remaining_count > 0 else remaining_budget

        if current_size <= fair_share:
            remaining_budget -= current_size
            continue

        target_cap = max(fair_share, floor)
        try:
            enforce_size_limit(path, target_cap)
            achieved = path.stat().st_size
            remaining_budget -= achieved
        except Exception:
            failed_paths.append(path)
            if path.exists():
                remaining_budget -= path.stat().st_size

    total_bytes_after = sum(p.stat().st_size for p in path_list if p.exists())
    return total_bytes_after, failed_paths

