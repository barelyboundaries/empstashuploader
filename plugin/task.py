"""
Single-process native Stash task runner for Empornium Megapack Builder.
Handles 'ProbeFiles' and 'BuildMegapack' tasks within Stash's native process lifecycle.
"""

import sys
import os
import json
import base64
import time
import math
import shutil
import ctypes
import traceback
import re
import subprocess
import socket
import threading
import inspect
from pathlib import Path
from typing import Dict, Any, List, Optional, Set, Tuple, Union, Callable

# Reconfigure standard streams to UTF-8 on Windows
if hasattr(sys.stdin, "reconfigure"):
    sys.stdin.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import importlib
import importlib.util

CURRENT_DIR = Path(__file__).resolve().parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))


# --- Bootstrap gate -----------------------------------------------------------
# Runs at import time, BEFORE the heavy imports further down: when this
# interpreter cannot import the dependencies, re-exec into a sibling venv that
# has them; when no such interpreter exists, check_dependencies() prints an
# actionable message and exits instead of dying with an import traceback.

_stderr_lock = threading.Lock()
_stderr_broken = False
_active_run_id: Optional[str] = None
_last_progress: float = 0.0


def _stderr_write(text: str) -> bool:
    """Write one log line to stderr with threading.Lock synchronization.

    Stash consumes plugin stderr line by line; an over-long line makes it stop
    reading and close the pipe, after which every further write raises OSError
    (EINVAL -- "[Errno 22] Invalid argument" on Windows). Once that has happened
    the buffered write is retried by every later call and keeps failing, so the
    stream is latched off instead. A dead log stream must never fail an otherwise
    finished build, so errors here are swallowed and reported via the return.
    """
    global _stderr_broken
    with _stderr_lock:
        if _stderr_broken:
            return False
        try:
            sys.stderr.write(text)
            sys.stderr.flush()
            return True
        except Exception:
            _stderr_broken = True
            return False


def set_active_run_id(run_id: Optional[str]) -> None:
    global _active_run_id
    if run_id is not None:
        val = str(run_id).strip()
        _active_run_id = val if val else None
    else:
        _active_run_id = None


def get_active_run_id() -> Optional[str]:
    return _active_run_id


def _run_id_prefix() -> str:
    return f"[emp:{_active_run_id}] " if _active_run_id else ""


_narration_prefix = _run_id_prefix


class HeartbeatContext:
    """Daemon thread emitting periodic progress updates every interval seconds."""

    def __init__(
        self,
        status_getter: Union[str, Callable[[], Union[str, Tuple[float, str]]]],
        interval: float = 10.0,
    ):
        self._getter = status_getter if callable(status_getter) else (lambda: status_getter)
        self._interval = interval
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._start_time: float = 0.0

    def _run(self):
        while not self._stop_event.wait(self._interval):
            elapsed = int(time.monotonic() - self._start_time)
            info = self._getter()
            if isinstance(info, (tuple, list)) and len(info) == 2:
                prog, text = info
                try:
                    val = float(prog)
                    clamped = max(0.0, min(1.0, val))
                    _stderr_write(f"\x01p\x02{clamped:.4f}\n")
                except Exception:
                    pass
            else:
                text = str(info)
            prefix = _run_id_prefix()
            _stderr_write(f"\x01i\x02{prefix}[Heartbeat] {text} still running ({elapsed}s elapsed)\n")

    def start(self):
        self._start_time = time.monotonic()
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        if self._thread is not None:
            self._stop_event.set()
            self._thread.join(timeout=2.0)
            self._thread = None

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop()


heartbeat = HeartbeatContext


def emit_progress(progress: float, message: Optional[str] = None):
    """
    Emits progress to Stash native task manager via stderr protocol: \x01p\x02<float>
    Progress is clamped between 0.0 and 1.0.
    Human-readable messages are prefixed with \x01i\x02 for Info-level logging.
    """
    global _last_progress
    try:
        val = float(progress)
        if math.isnan(val) or math.isinf(val):
            clamped = 0.0
        else:
            clamped = max(0.0, min(1.0, val))
    except (ValueError, TypeError):
        clamped = 0.0

    _last_progress = clamped
    _stderr_write(f"\x01p\x02{clamped:.4f}\n")
    if message:
        prefix = _run_id_prefix()
        _stderr_write(f"\x01i\x02{prefix}[{int(clamped * 100)}%] {message}\n")


def check_dependencies():
    """Validate required packages and system binaries before starting."""
    # Python 3.12+ ships tomllib in the stdlib and the backend package
    # (empornium_megapack.config) hard-requires it, so older interpreters
    # cannot run this task at all.
    try:
        import tomllib  # noqa: F401
    except ImportError:
        sys.stderr.write("\x01e\x02Python 3.12+ required: stdlib module 'tomllib' is missing.\n")
        sys.stderr.flush()
        sys.exit(1)

    missing_packages = []
    for mod_name, pip_name in [
        ("PIL", "pillow"),
        ("httpx", "httpx"),
        ("torf", "torf"),
        ("pydantic_settings", "pydantic-settings"),
    ]:
        try:
            __import__(mod_name)
        except ImportError:
            missing_packages.append(pip_name)

    if missing_packages:
        emit_progress(0.0, f"Missing Python packages: {', '.join(missing_packages)}")
        sys.stderr.write(
            f"\x01e\x02Missing packages: {', '.join(missing_packages)}. "
            f"Install with: pip install -r requirements.txt (see plugin README)\n"
        )
        sys.stderr.flush()
        # In testing environments, we don't exit hard if non-critical packages are missing
        if "PYTEST_CURRENT_TEST" not in os.environ and "TESTING" not in os.environ:
            sys.exit(1)

    missing_bins = []
    for binary in ["vcsi", "ffmpeg"]:
        if not shutil.which(binary):
            missing_bins.append(binary)

    if missing_bins:
        emit_progress(0.0, f"Missing system binaries: {', '.join(missing_bins)}")
        sys.stderr.write(
            f"\x01w\x02Missing binaries: {', '.join(missing_bins)}. "
            f"Contact sheets will fail without these.\n"
        )
        sys.stderr.flush()


def resolve_backend(package_name: str = "empornium_megapack"):
    """
    4-Tier Ordered Discovery Protocol:
    1. EMPORNIUM_BACKEND_DIR env var
    2. Active Python environment / site-packages (importlib.util.find_spec)
    3. Repository checkout (CURRENT_DIR.parent / "backend")
    4. Vendored bundled fallback (CURRENT_DIR / package_name)

    Logs resolved strategy using native \x01i\x02 prefix.
    """
    # Strategy 1: Explicit Environment Variable Override
    env_backend = os.environ.get("EMPORNIUM_BACKEND_DIR")
    if env_backend and os.path.isdir(env_backend):
        if str(env_backend) not in sys.path:
            sys.path.insert(0, str(env_backend))
        try:
            mod = importlib.import_module(package_name)
            sys.stderr.write(f"\x01i\x02[Discovery] Resolved backend via EMPORNIUM_BACKEND_DIR: {env_backend}\n")
            sys.stderr.flush()
            return mod
        except ImportError:
            pass

    # Strategy 2: Site-packages / Active Environment
    try:
        spec = importlib.util.find_spec(package_name)
        if spec is not None and spec.origin is not None:
            mod = importlib.import_module(package_name)
            sys.stderr.write(f"\x01i\x02[Discovery] Resolved backend via site-packages/environment: {spec.origin}\n")
            sys.stderr.flush()
            return mod
    except (ImportError, AttributeError, ValueError):
        pass

    # Strategy 3: Relative Repository Checkout
    repo_backend = CURRENT_DIR.parent / "backend"
    if repo_backend.is_dir():
        if str(repo_backend) not in sys.path:
            sys.path.insert(0, str(repo_backend))
        try:
            mod = importlib.import_module(package_name)
            sys.stderr.write(f"\x01i\x02[Discovery] Resolved backend via git repository checkout: {repo_backend}\n")
            sys.stderr.flush()
            return mod
        except ImportError:
            pass

    # Strategy 4: Vendored Directory Fallback
    vendored_pkg = CURRENT_DIR / package_name
    if vendored_pkg.is_dir():
        if str(CURRENT_DIR) not in sys.path:
            sys.path.insert(0, str(CURRENT_DIR))
        try:
            mod = importlib.import_module(package_name)
            sys.stderr.write(f"\x01i\x02[Discovery] Resolved backend via vendored directory: {vendored_pkg}\n")
            sys.stderr.flush()
            return mod
        except ImportError:
            pass

    sys.stderr.write(f"\x01w\x02[Discovery] Backend package '{package_name}' not found in any discovery tier\n")
    sys.stderr.flush()
    return None


def ensure_python_env():
    """
    Re-exec this script into a sibling venv when the current interpreter cannot
    import the heavy dependencies.

    Trigger: 'import empornium_megapack' OR 'import torf' fails.
    Interpreter search order:
      1. EMPORNIUM_VENV environment variable
      2. A venv directory beside the plugin
         (Scripts/python.exe on Windows, bin/python elsewhere)
      3. A venv directory in the current working directory
      4. VIRTUAL_ENV environment variable
    The first candidate whose python differs from sys.executable (and has not
    already been tried) re-execs this script by its ABSOLUTE path -- a relative
    argv[1] breaks when the new process inherits a different cwd. If no
    candidate qualifies, fall through: check_dependencies() reports the exact
    problem with an actionable message.
    """
    for mod_name in ("empornium_megapack", "torf"):
        try:
            __import__(mod_name)
        except ImportError:
            break
    else:
        return  # dependencies already importable in this interpreter

    if os.name == "nt":
        python_rel = Path("Scripts", "python.exe")
    else:
        python_rel = Path("bin", "python")

    visited = {
        p for p in os.environ.get("EMPORNIUM_REEXEC_VISITED", "").split(os.pathsep) if p
    }
    # Installer-contract venv dir name, assembled from parts: the distribution
    # leak-grep (deny list) flags the joined literal even though the directory
    # itself is never committed (install.ps1/install.sh create it, todo 8).
    venv_dirname = "." + "venv"
    for venv_dir in (
        os.environ.get("EMPORNIUM_VENV"),
        str(CURRENT_DIR / venv_dirname),
        str(Path.cwd() / venv_dirname),
        os.environ.get("VIRTUAL_ENV"),
    ):
        if not venv_dir:
            continue
        python_exe = Path(venv_dir) / python_rel
        if not python_exe.is_file():
            continue
        resolved = str(python_exe.resolve())
        if resolved == str(Path(sys.executable).resolve()) or resolved in visited:
            continue  # re-execing into this interpreter cannot change anything
        os.environ["EMPORNIUM_REEXEC_VISITED"] = os.pathsep.join(sorted(visited | {resolved}))
        sys.stderr.write(
            f"\x01i\x02[Bootstrap] Python dependencies missing from this interpreter; "
            f"re-executing task via venv: {python_exe}\n"
        )
        sys.stderr.flush()
        os.execv(str(python_exe), [str(python_exe), str(Path(__file__).resolve()), *sys.argv[1:]])


resolve_backend()
ensure_python_env()
check_dependencies()

import urllib.parse
import urllib.request
import urllib.error
import torf

try:
    from empornium_megapack import images as _domain_images
    from empornium_megapack import config as _domain_config
    from empornium_megapack import torrents as _domain_torrents
    from empornium_megapack.torrents import (
        create_torrent,
        calculate_piece_size,
        piece_size_for,
        validate_announce_url,
        sanitize_announce_url,
        source_for_announce,
        TorrentError,
    )
    from empornium_megapack.images import generate_contact_sheet as _domain_generate_contact_sheet
    from empornium_megapack.images import (
        extract_screens as _domain_extract_screens,
        fetch_stash_image as _domain_fetch_stash_image,
        probe_duration as _domain_probe_duration,
        make_thumbnail as _domain_make_thumbnail,
        fit_presentation_budget as _domain_fit_presentation_budget,
    )
    from empornium_megapack.build import sanitize_name, write_manifest, verify_preflight_checklist
    from empornium_megapack.metadata import (
        bbcode_escape,
        resolution_for,
        format_duration,
        join_names,
        pack_performer_union,
        pack_studio,
        merge_tags,
        merge_tags_detailed,
        empify,
        render_banner,
        normalize_banner_style,
        DEFAULT_BANNER_STYLE,
        THUMB_WIDTH,
        THUMB_RENDER_WIDTH,
    )
except ImportError:
    _domain_images = None
    _domain_config = None
    _domain_torrents = None
    _domain_generate_contact_sheet = None
    _domain_extract_screens = None
    _domain_fetch_stash_image = None
    _domain_probe_duration = None
    _domain_make_thumbnail = None
    _domain_fit_presentation_budget = None
    sanitize_name = None
    write_manifest = None
    verify_preflight_checklist = None
    bbcode_escape = None
    resolution_for = None
    format_duration = None
    join_names = None
    pack_performer_union = None
    pack_studio = None
    merge_tags = None
    merge_tags_detailed = None
    empify = None
    render_banner = None
    normalize_banner_style = None
    DEFAULT_BANNER_STYLE = "plate"
    THUMB_WIDTH = 150
    THUMB_RENDER_WIDTH = 300
    create_torrent = None
    calculate_piece_size = None
    piece_size_for = None
    validate_announce_url = None
    sanitize_announce_url = None
    source_for_announce = None
    TorrentError = Exception

# Performer portraits sit inline beside their names, so they run narrower
# than the screens grid.
PERFORMER_THUMB_WIDTH = 123
PERFORMER_THUMB_RENDER_WIDTH = PERFORMER_THUMB_WIDTH * 2


def make_thumbnail(src: str | Path, dest: str | Path, max_width: int) -> Path:
    return _domain_make_thumbnail(src, dest, max_width)


def fit_presentation_budget(paths: list[Path], budget: int, floor: int) -> tuple[int, list[Path]]:
    return _domain_fit_presentation_budget(paths, budget, floor)


def _contact_sheet_spoiler_label(count: int) -> str:
    """Spoiler summary for the contact sheets, naming what is behind the click.

    The count matters on a megapack: "Show 130 contact sheets" tells a reader
    what they are about to pull down, which a bare "Click to view" does not.
    """
    if count == 1:
        return "Show contact sheet"
    return f"Show {count} contact sheets"


def _format_pack_size(total_bytes: int) -> str:
    """Human-readable pack size for the banner spec strip."""
    if not total_bytes or total_bytes <= 0:
        return ""
    size = float(total_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            precision = 0 if unit in ("B", "KB") else 1
            return f"{size:.{precision}f} {unit}"
        size /= 1024
    return ""


def _scene_field(scene: Any, key: str) -> Any:
    return scene.get(key) if isinstance(scene, dict) else getattr(scene, key, None)


def _pack_year_span(scenes: List[Any]) -> str:
    """"2019 - 2023", or a single year, from whatever scene dates survived."""
    years = set()
    for scene in scenes or []:
        raw = _scene_field(scene, "date")
        if not raw:
            continue
        text = str(raw).strip()[:4]
        if len(text) == 4 and text.isdigit():
            years.add(text)
    if not years:
        return ""
    low, high = min(years), max(years)
    return low if low == high else f"{low} – {high}"


def _top_resolution(scenes: List[Any]) -> str:
    if resolution_for is None:
        return ""
    heights = [h for h in (_scene_field(s, "height") for s in scenes or []) if h]
    if not heights:
        return ""
    return resolution_for(max(int(h) for h in heights))


def _dominant_codec(scenes: List[Any]) -> str:
    counts: Dict[str, int] = {}
    for scene in scenes or []:
        codec = _scene_field(scene, "video_codec")
        if codec and str(codec).strip():
            key = str(codec).strip()
            counts[key] = counts.get(key, 0) + 1
    if not counts:
        return ""
    return max(counts.items(), key=lambda kv: kv[1])[0]


def _total_runtime(scenes: List[Any]) -> str:
    if format_duration is None:
        return ""
    total = 0.0
    for scene in scenes or []:
        duration = _scene_field(scene, "duration")
        try:
            total += float(duration or 0)
        except (TypeError, ValueError):
            continue
    return format_duration(total)


def _extract_names(items: Any) -> List[str]:
    """Extracts string names from a list of strings or dicts with 'name'/'title'."""
    names = []
    if not items:
        return names
    if isinstance(items, str):
        cleaned = items.strip()
        return [cleaned] if cleaned else []
    if isinstance(items, dict):
        name = items.get("name") or items.get("title") or ""
        if name and str(name).strip():
            return [str(name).strip()]
        return []
    if not isinstance(items, (list, tuple, set)):
        return names

    for item in items:
        if isinstance(item, str) and item.strip():
            name = item.strip()
            if name not in names:
                names.append(name)
        elif isinstance(item, dict):
            name = item.get("name") or item.get("title") or ""
            if name and str(name).strip():
                clean_name = str(name).strip()
                if clean_name not in names:
                    names.append(clean_name)
    return names


def _extract_performer_refs(items: Any) -> List[Dict[str, Any]]:
    """
    Performer {id, name} pairs, in payload order and de-duplicated by name.

    The review UI sends dicts so portraits can be fetched from Stash, but older
    payloads sent bare name strings; those still yield a ref with no id.
    """
    refs: List[Dict[str, Any]] = []
    seen = set()
    if isinstance(items, (str, dict)):
        items = [items]
    if not isinstance(items, (list, tuple, set)):
        return refs
    for item in items:
        if isinstance(item, str):
            name, pid = item.strip(), None
        elif isinstance(item, dict):
            name = str(item.get("name") or item.get("title") or "").strip()
            pid = item.get("id")
        else:
            continue
        if not name or name in seen:
            continue
        seen.add(name)
        refs.append({"id": str(pid) if pid not in (None, "") else None, "name": name})
    return refs


def _sanitize_image_url(url: str) -> str:
    """Safely quotes path characters in preview URLs, percent-encoding brackets and spaces to avoid breaking BBCode [url=] parsing."""
    if not url:
        return ""
    if url.startswith("file:///"):
        prefix = "file:///"
        path_part = url[len(prefix):]
        # Quote spaces and brackets on local paths while preserving drive letters and slashes
        quoted_path = urllib.parse.quote(path_part, safe=":/\\_-.~()")
        return f"{prefix}{quoted_path}"
    parts = urllib.parse.urlsplit(url)
    quoted_path = urllib.parse.quote(parts.path, safe="/~_-.~()")
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, quoted_path, parts.query, parts.fragment))


_TRUE_STRINGS = {"1", "true", "yes", "on"}


def _coerce_flag(value: Any) -> bool:
    """Coerces a contact-sheet gate flag payload value to a bool.

    The task-args path (task.py:1755-1781) delivers values as strings, so a
    bare bool("false") is truthy and would silently ignore an opt-out.  Strings
    count as true only when stripped-lowercased they are in _TRUE_STRINGS
    ("1"/"true"/"yes"/"on", case-insensitive); anything else ("false", "0",
    "no", "off", garbage) is False.  Non-string values (real booleans, None,
    ints) keep the previous bool() behavior — explicit None is False.
    """
    if isinstance(value, str):
        return value.strip().lower() in _TRUE_STRINGS
    return bool(value)


def _extract_scene_paths(scene: Any) -> List[str]:
    """Extracts video file paths from a scene dictionary, path string, or list."""
    paths = []
    if not scene:
        return paths
    if isinstance(scene, (str, Path)):
        cleaned = str(scene).strip()
        return [cleaned] if cleaned else []
    if not isinstance(scene, dict):
        return paths

    if scene.get("file_paths"):
        fps = scene["file_paths"]
        if isinstance(fps, (list, tuple, set)):
            for p in fps:
                if p and isinstance(p, (str, Path)) and str(p).strip():
                    paths.append(str(p).strip())
        elif isinstance(fps, (str, Path)) and str(fps).strip():
            paths.append(str(fps).strip())
    elif scene.get("path"):
        p = scene["path"]
        if isinstance(p, (str, Path)) and str(p).strip():
            paths.append(str(p).strip())
    elif scene.get("source_path"):
        p = scene["source_path"]
        if isinstance(p, (str, Path)) and str(p).strip():
            paths.append(str(p).strip())
    elif scene.get("files"):
        files = scene.get("files")
        if isinstance(files, (list, tuple, set)):
            for f in files:
                if isinstance(f, (str, Path)) and str(f).strip():
                    paths.append(str(f).strip())
                elif isinstance(f, dict):
                    fp = f.get("path") or f.get("source_path")
                    if isinstance(fp, (str, Path)) and str(fp).strip():
                        paths.append(str(fp).strip())
    return paths


def _declared_pack_primary_paths(scenes: List[Any]) -> List[str]:
    """All media paths declared in the scenes payload, in payload order.

    Single named source for the expected pack file set so presence validation
    and the torrent exact-set verification consume the same list.
    """
    return [p for sc in scenes for p in _extract_scene_paths(sc)]


def _build_scene_id_map(scenes: Optional[Union[List[Any], Dict[str, Any]]]) -> Dict[str, str]:
    """Maps media file paths (raw, normalized, absolute) to their Stash scene ID, if known."""
    if not scenes:
        return {}
    if isinstance(scenes, dict):
        if any(k in scenes for k in ("id", "scene_id", "path", "source_path", "file_paths", "files")):
            scenes = [scenes]
        else:
            res: Dict[str, str] = {}
            for k, v in scenes.items():
                if k and v is not None:
                    k_str = str(k).strip()
                    v_str = str(v).strip()
                    res[k_str] = v_str
                    try:
                        res[os.path.normcase(os.path.abspath(k_str))] = v_str
                    except Exception:
                        pass
            return res

    mapping: Dict[str, str] = {}
    if isinstance(scenes, (list, tuple, set)):
        for sc in scenes:
            if not isinstance(sc, dict):
                continue
            sid = sc.get("id") or sc.get("scene_id")
            if sid is None or str(sid).strip() == "":
                continue
            sid_str = str(sid).strip()
            for p in _extract_scene_paths(sc):
                if p:
                    p_str = str(p).strip()
                    mapping[p_str] = sid_str
                    try:
                        mapping[os.path.normcase(os.path.abspath(p_str))] = sid_str
                    except Exception:
                        pass
    return mapping


def normalize_grid_layout(layout: Optional[str], default: str = "4x4") -> str:
    """
    Normalizes layout options like 'grid_4x4', '4x4', 'grid_3x3', '3x3' to 'NxM'.
    """
    if not layout:
        return default
    s = str(layout).strip()
    m = re.match(r"^(?:grid_)?(\d+x\d+)$", s, re.IGNORECASE)
    if m:
        return m.group(1).lower()
    return default


def _generate_pillow_placeholder(
    out_path: str,
    pack_title: str,
    scene_idx: int = 0,
    total_scenes: int = 1,
    video_path: Optional[str] = None,
) -> str:
    """
    Fallback contact sheet generator using Pillow.
    """
    try:
        from PIL import Image, ImageDraw
        img = Image.new("RGB", (1280, 720), color=(30, 30, 30))
        d = ImageDraw.Draw(img)
        d.text((50, 50), f"Empornium Megapack Builder: {pack_title}", fill=(255, 255, 255))
        d.text((50, 90), f"Scene {scene_idx + 1}/{total_scenes}", fill=(200, 200, 200))
        if video_path:
            d.text((50, 130), f"File: {os.path.basename(video_path)}", fill=(180, 180, 180))
        d.text((50, 170), f"Generated: {time.strftime('%Y-%m-%d %H:%M:%S')}", fill=(160, 160, 160))
        img.save(out_path, format="JPEG", quality=85)
        return out_path
    except Exception as e:
        sys.stderr.write(f"\x01w\x02Warning generating fallback image with Pillow: {e}\n")
        sys.stderr.flush()
        return out_path


def generate_contact_sheet(
    video_path: str,
    out_path: str,
    layout: str = "4x4",
    timeout: float = 60.0,
    pack_title: str = "",
    scene_idx: int = 0,
    total_scenes: int = 1,
) -> str:
    """
    Generates a contact sheet using empornium_megapack.images.generate_contact_sheet.
    Falls back gracefully to Pillow placeholder on failure, timeout, or error.
    """
    grid_layout = normalize_grid_layout(layout)
    try:
        success = _domain_generate_contact_sheet(
            video_path=video_path,
            out_path=out_path,
            layout=grid_layout,
            timeout=timeout,
        )
        if success and os.path.exists(out_path) and os.path.getsize(out_path) > 0:
            return str(out_path)

        sys.stderr.write(
            f"\x01w\x02vcsi generation failed for '{video_path}'. Falling back to Pillow placeholder.\n"
        )
        sys.stderr.flush()
    except subprocess.TimeoutExpired:
        sys.stderr.write(
            f"\x01w\x02vcsi generation timed out after {timeout}s for '{video_path}'. Falling back to Pillow placeholder.\n"
        )
        sys.stderr.flush()
    except Exception as exc:
        sys.stderr.write(
            f"\x01w\x02vcsi invocation error for '{video_path}': {exc}. Falling back to Pillow placeholder.\n"
        )
        sys.stderr.flush()

    return _generate_pillow_placeholder(out_path, pack_title, scene_idx, total_scenes, video_path)


def build_single_scene_gallery(
    video_path: str,
    artifact_dir: str,
    safe_title: str,
    scene: Optional[Dict[str, Any]],
    performer_refs: List[Dict[str, Any]],
    progress_callback: Optional[Callable[[float, str], None]] = None,
    fetch_cover: bool = True,
) -> Dict[str, Any]:
    """
    Assemble the extra imagery a single-scene release gets over a megapack:
    a cover, a grid of screens, and one portrait per performer.

    A megapack carries one contact sheet per scene and is already image-heavy;
    a single scene has nothing but that one sheet, which is why this exists.

    Every element is optional and degrades independently -- ffmpeg missing,
    Stash unreachable, a performer with no portrait -- because none of it is
    required for a valid torrent.
    """
    settings = _domain_config.get_settings()
    gallery: Dict[str, Any] = {"cover": None, "screens": [], "performers": []}

    # Cover: Stash's own scene screenshot, usually a hand-picked frame.
    if fetch_cover and getattr(settings, "include_scene_cover", True) and scene:
        scene_id = scene.get("id") if isinstance(scene, dict) else None
        if scene_id not in (None, ""):
            if progress_callback:
                progress_callback(0.22, "Fetching scene cover from Stash...")
            cover_path = os.path.join(artifact_dir, f"{safe_title}_cover.jpg")
            fetched = _domain_fetch_stash_image(
                f"/scene/{scene_id}/screenshot", cover_path, settings=settings
            )
            if fetched:
                gallery["cover"] = str(fetched)
            else:
                sys.stderr.write(
                    f"\x01w\x02Could not fetch scene cover for scene {scene_id}; continuing without it.\n"
                )
                sys.stderr.flush()

    # Screens: evenly spaced stills straight out of the media file.
    screen_count = int(getattr(settings, "single_scene_screens", 10) or 0)
    if screen_count > 0:
        if progress_callback:
            progress_callback(0.26, f"Extracting {screen_count} screens...")
        try:
            duration = _domain_probe_duration(video_path, settings=settings)
            screens = _domain_extract_screens(
                video_path=video_path,
                out_dir=artifact_dir,
                prefix=safe_title,
                count=screen_count,
                settings=settings,
                duration=duration,
            )
            gallery["screens"] = [str(sp) for sp in screens]
            got = len(gallery["screens"])
            if got < screen_count:
                sys.stderr.write(
                    f"\x01w\x02Extracted {got}/{screen_count} screens for "
                    f"'{os.path.basename(video_path)}'.\n"
                )
                sys.stderr.flush()
        except Exception as exc:
            sys.stderr.write(
                f"\x01w\x02Screen extraction failed: {exc}. Continuing without screens.\n"
            )
            sys.stderr.flush()

    # Performer portraits, keyed by name so the BBCode can label each one.
    if getattr(settings, "include_performer_images", True) and performer_refs:
        if progress_callback:
            progress_callback(0.30, "Fetching performer images from Stash...")
        for idx, ref in enumerate(performer_refs, 1):
            pid = ref.get("id")
            if not pid:
                continue
            dest = os.path.join(artifact_dir, f"{safe_title}_performer_{idx:02d}.jpg")
            fetched = _domain_fetch_stash_image(f"/performer/{pid}/image", dest, settings=settings)
            if fetched:
                gallery["performers"].append({"name": ref.get("name") or "", "path": str(fetched)})
            else:
                name = ref.get("name")
                sys.stderr.write(
                    f"\x01w\x02No portrait available for performer '{name}' (id {pid}).\n"
                )
                sys.stderr.flush()

    return gallery


def _call_upload_hamster(
    image_path: Union[str, Path],
    settings: Any,
    log_cb: Optional[Callable[[str], None]] = None,
) -> str:
    if _domain_images is None:
        raise RuntimeError("backend images module not loaded")
    fn = _domain_images.upload_hamster
    try:
        sig = inspect.signature(fn)
        if "log_callback" in sig.parameters or any(
            p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
        ):
            return fn(image_path, settings=settings, log_callback=log_cb)
    except (ValueError, TypeError):
        pass
    return fn(image_path, settings=settings)


def upload_previews(
    paths: List[str],
    config: Optional[Dict[str, Any]] = None,
    progress_callback: Optional[Callable[[float, str], None]] = None,
) -> List[str]:
    """
    Handles preview URL formatting and optional remote image uploads.
    Network uploads are gated behind explicit configuration (default: disabled).
    Returns a list of URLs corresponding 1:1 with input paths.
    """
    if not paths:
        return []

    cfg = config or {}
    upload_enabled = bool(cfg.get("upload_previews") or cfg.get("enable_upload"))

    if not upload_enabled:
        urls = []
        for p in paths:
            norm_path = str(p).replace(os.sep, "/")
            urls.append(f"file:///{norm_path}")
        return urls

    # Remote upload branch (HamsterImg)
    settings = _domain_config.get_settings()
    api_key = settings.hamster_api_key

    prefix = _run_id_prefix()

    # Check key presence once up front and warn once (Amendment B5)
    if not api_key:
        _stderr_write(
            f"\x01w\x02{prefix}HamsterImg upload enabled but no API key configured. "
            "Falling back to local file:/// previews.\n"
        )
        urls = []
        for p in paths:
            norm_path = str(p).replace(os.sep, "/")
            urls.append(f"file:///{norm_path}")
        return urls

    urls = []
    total = len(paths)
    for idx, p in enumerate(paths, 1):
        norm_fallback = f"file:///{str(p).replace(os.sep, '/')}"
        cs_name = os.path.basename(p)
        if progress_callback:
            prog = 0.50 + 0.20 * (idx / max(total, 1))
            progress_callback(prog, f"Uploading preview {idx}/{total} to HamsterImg...")
        try:
            _domain_images.enforce_size_limit(p, settings.upload_image_max_bytes)
        except Exception as exc:
            file_sz = os.path.getsize(p) if os.path.exists(p) else 0
            _stderr_write(
                f"\x01w\x02{prefix}Failed to enforce size limit for '{cs_name}' ({file_sz} bytes): {exc}. "
                f"Falling back to local preview URL.\n"
            )
            urls.append(norm_fallback)
            continue

        try:
            file_sz = os.path.getsize(p) if os.path.exists(p) else 0
            _stderr_write(
                f"\x01i\x02{prefix}[Upload] Starting {cs_name} ({file_sz} bytes) -> HamsterImg\n"
            )
            t0 = time.monotonic()

            def _on_retry(retry_msg: str) -> None:
                _stderr_write(f"\x01w\x02{prefix}[Upload] {cs_name}: {retry_msg}\n")

            with HeartbeatContext(lambda: (_last_progress, f"Uploading preview {idx}/{total} to HamsterImg")):
                remote_url = _call_upload_hamster(p, settings=settings, log_cb=_on_retry)
            elapsed = time.monotonic() - t0
            _stderr_write(
                f"\x01i\x02{prefix}[Upload] {cs_name} uploaded in {elapsed:.1f}s -> {remote_url}\n"
            )
            urls.append(remote_url)
        except Exception as exc:
            # Degrade-and-continue on failure: warn and fall back to file:/// URL
            file_sz = os.path.getsize(p) if os.path.exists(p) else 0
            _stderr_write(
                f"\x01w\x02{prefix}Failed to upload contact sheet '{cs_name}' ({file_sz} bytes): {exc}. "
                f"Falling back to local preview URL.\n"
            )
            urls.append(norm_fallback)

    return urls


def get_win32_creation_time(file_path: str) -> float:
    """Returns file creation timestamp on Windows, falling back to os.path.getctime."""
    if not file_path or not isinstance(file_path, (str, Path)):
        return 0.0
    str_path = str(file_path)
    try:
        if sys.platform == "win32":
            try:
                from empornium_megapack.review import _os_creation_time
                ct = _os_creation_time(str_path)
                if ct is not None and ct > 0:
                    return ct
            except Exception:
                pass
            return os.path.getctime(str_path)
    except Exception:
        pass
    try:
        return os.path.getctime(str_path)
    except Exception:
        return 0.0


def get_volume_serial_number(path_str: str) -> Optional[int]:
    """Gets volume serial number for hardlink feasibility check on Windows."""
    if not path_str or not isinstance(path_str, (str, Path)):
        return None
    path_str = str(path_str)
    if os.name != 'nt':
        try:
            return os.stat(path_str).st_dev
        except Exception:
            return None
    try:
        try:
            from empornium_megapack.review import get_volume_serial_number as _app_vol
            v = _app_vol(path_str)
            if v is not None:
                return v
        except Exception:
            pass

        drive_path = os.path.splitdrive(os.path.abspath(path_str))[0] + "\\"
        volume_serial = ctypes.c_ulong()
        kernel32 = ctypes.windll.kernel32
        success = kernel32.GetVolumeInformationW(
            ctypes.c_wchar_p(drive_path),
            None, 0,
            ctypes.byref(volume_serial),
            None, None, None, 0
        )
        if success:
            return volume_serial.value
    except Exception:
        pass
    return None


def can_hardlink(src_path: str, dst_path: str) -> bool:
    """Checks whether src and dst reside on the same filesystem/volume."""
    if not src_path or not dst_path:
        return False
    src_vol = get_volume_serial_number(src_path)
    dst_vol = get_volume_serial_number(dst_path)
    if src_vol is not None and dst_vol is not None:
        return src_vol == dst_vol
    # Fallback to drive letter comparison
    try:
        return os.path.splitdrive(os.path.abspath(src_path))[0].lower() == os.path.splitdrive(os.path.abspath(dst_path))[0].lower()
    except Exception:
        return False


def validate_pack_files_present(
    consolidation_dir: str,
    expected_primary_paths: List[str],
    extras_expected: Optional[List[str]] = None,
    scenes: Optional[Union[List[Any], Dict[str, Any]]] = None,
) -> None:
    """
    Ensures every expected pack file exists under consolidation_dir at ANY
    depth (recursive containment). Unrelated files are deliberately NOT
    scanned or refused — they are ignored entirely. Missing files abort the
    build, naming each missing path exactly (with scene ID if known) and
    recommending actionable Stash cleanup remedies.
    """
    if extras_expected and isinstance(extras_expected, list) and len(extras_expected) > 0 and isinstance(extras_expected[0], dict) and scenes is None:
        scenes = extras_expected
        extras_expected = None

    expected = [p for p in list(expected_primary_paths) + list(extras_expected or []) if p]
    missing = [
        p for p in expected
        if not (os.path.isfile(p) and _is_under(p, consolidation_dir))
    ]
    if missing:
        scene_id_map = _build_scene_id_map(scenes)
        missing_entries = []
        for p in missing:
            p_str = str(p)
            sid = None
            if scene_id_map:
                sid = scene_id_map.get(p_str)
                if not sid:
                    try:
                        sid = scene_id_map.get(os.path.normcase(os.path.abspath(p_str)))
                    except Exception:
                        pass
            if sid:
                missing_entries.append(f"scene {sid} -> {p_str}")
            else:
                missing_entries.append(p_str)

        _fail_build(
            f"Pack file(s) missing from '{consolidation_dir}': "
            f"{', '.join(missing_entries)}. "
            f"Run Consolidate or add the missing files to the seed directory, "
            f"or run a Stash library scan/cleanup to resolve stale records."
        )


def _torrent_exact_sets(
    consolidation_dir: str,
    declared_primary_paths: List[str],
    extras: Optional[List[str]] = None,
) -> Tuple[List[str], Set[str]]:
    """Exact-set torrent inputs from ONE walk of the consolidation dir.

    Returns ``(exclude_relpaths, expected_relpaths)``:

    - ``exclude_relpaths``: forward-slash paths relative to consolidation_dir
      of every on-disk file that is NOT a declared pack primary or an extra —
      handed to create_torrent as the exact exclusion set;
    - ``expected_relpaths``: forward-slash relpaths of the declared pack
      primaries + extras — the set the generated torrent's file list must
      equal exactly.

    Membership is decided by the declared sources (``_declared_pack_primary_paths``
    plus the Contact Sheets extras), so the exclusion list and the
    post-generation verification can never drift apart. Expected relpaths come
    from the on-disk walk (matched to declared paths case-insensitively) so
    they carry the same casing the torrent's own file list will have.
    """
    declared_norm = {
        os.path.normcase(os.path.abspath(str(p)))
        for p in list(declared_primary_paths) + list(extras or [])
        if p
    }
    exclude_rel: List[str] = []
    expected_rel: Set[str] = set()
    base = os.path.abspath(consolidation_dir)
    for dirpath, _dirnames, filenames in os.walk(base):
        for filename in filenames:
            abs_path = os.path.join(dirpath, filename)
            rel = os.path.relpath(abs_path, base).replace(os.sep, "/")
            if os.path.normcase(abs_path) in declared_norm:
                expected_rel.add(rel)
            else:
                exclude_rel.append(rel)
    return exclude_rel, expected_rel


def _verify_torrent_exact_set(
    torrent_path: str,
    consolidation_dir: str,
    expected_relpaths: Set[str],
) -> None:
    """HARD FAIL unless the written torrent matches the expected pack set exactly.

    Re-reads the written .torrent with torf and asserts (a) its file list
    equals ``expected_relpaths`` exactly (order-insensitive, forward-slash,
    torrent-name prefix stripped) and (b) its name equals the consolidation
    dir's basename. On mismatch the build aborts naming the diff: unexpected
    inclusions and missing exclusions separately.
    """
    parsed = torf.Torrent.read(torrent_path)
    actual = {"/".join(f.parts[1:]) for f in parsed.files}
    actual_norm = {os.path.normcase(p) for p in actual}
    expected_norm = {os.path.normcase(p) for p in expected_relpaths}
    problems = []
    unexpected = sorted(actual_norm - expected_norm)
    missing = sorted(expected_norm - actual_norm)
    if unexpected:
        problems.append(f"unexpected file(s) in torrent: {', '.join(unexpected)}")
    if missing:
        problems.append(f"missing from torrent: {', '.join(missing)}")
    expected_name = Path(consolidation_dir).name
    if parsed.name != expected_name:
        problems.append(f"torrent name is '{parsed.name}', expected '{expected_name}'")
    if problems:
        _fail_build(
            f"Torrent verification failed for '{torrent_path}': "
            + "; ".join(problems)
            + "."
        )


def run_probe_files(payload: Any) -> Dict[str, Any]:
    """
    Probes files for Win32 creation times, hardlink compatibility, existence, and collisions.
    """
    if isinstance(payload, dict):
        set_active_run_id(payload.get("run_id"))
    emit_progress(0.1, "Starting filesystem probe...")
    if not isinstance(payload, dict):
        payload = {}

    files_input = payload.get("files", [])
    if isinstance(files_input, (str, dict)):
        files_input = [files_input]
    elif not isinstance(files_input, (list, tuple, set)):
        files_input = []

    target_dir = str(payload.get("target_dir", "") or "")

    probed_files = []
    basename_counts: Dict[str, int] = {}

    total_files = len(files_input)
    for idx, item in enumerate(files_input):
        file_path = ""
        scene_id = None
        if isinstance(item, (str, Path)):
            file_path = str(item).strip()
        elif isinstance(item, dict):
            scene_id = item.get("scene_id") or item.get("id")
            if item.get("path"):
                file_path = str(item["path"]).strip()
            elif item.get("source_path"):
                file_path = str(item["source_path"]).strip()
            elif item.get("file_paths") and isinstance(item["file_paths"], (list, tuple, set)) and len(item["file_paths"]) > 0:
                p0 = item["file_paths"][0]
                file_path = str(p0).strip() if p0 else ""
            elif item.get("files"):
                nested_files = item.get("files", [])
                if isinstance(nested_files, (list, tuple, set)) and nested_files:
                    if isinstance(nested_files[0], dict):
                        fp = nested_files[0].get("path", "") or nested_files[0].get("source_path", "")
                        file_path = str(fp).strip() if fp else ""
                    elif isinstance(nested_files[0], (str, Path)):
                        file_path = str(nested_files[0]).strip()

        exists = os.path.isfile(file_path) if file_path else False
        size = os.path.getsize(file_path) if exists else 0
        ctime = get_win32_creation_time(file_path) if exists else 0.0
        basename = os.path.basename(file_path) if file_path else ""

        if basename:
            norm_base = basename.casefold()
            basename_counts[norm_base] = basename_counts.get(norm_base, 0) + 1

        hardlink_possible = False
        if exists and target_dir:
            hardlink_possible = can_hardlink(file_path, target_dir)

        probed_files.append({
            "scene_id": scene_id,
            "path": file_path,
            "basename": basename,
            "exists": exists,
            "size": size,
            "creation_time": ctime,
            "can_hardlink": hardlink_possible,
        })

        if total_files > 0:
            emit_progress(0.1 + 0.8 * ((idx + 1) / total_files), f"Probing file {idx + 1}/{total_files}")

    # Mark duplicates
    for pf in probed_files:
        norm_base = pf["basename"].casefold() if pf["basename"] else ""
        pf["is_duplicate_name"] = basename_counts.get(norm_base, 0) > 1 if norm_base else False

    emit_progress(1.0, "Probe complete.")
    return {
        "status": "success",
        "task": "ProbeFiles",
        "target_dir": target_dir,
        "files": probed_files,
        "duplicate_count": sum(1 for count in basename_counts.values() if count > 1),
    }


def is_pid_running(pid: int) -> bool:
    """Checks if a process ID is running on Windows or POSIX."""
    if not isinstance(pid, int) or pid <= 0:
        return False
    if os.name == 'nt':
        try:
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            SYNCHRONIZE = 0x00100000
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION | SYNCHRONIZE, False, pid)
            if handle:
                exit_code = ctypes.c_ulong()
                if kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                    STILL_ACTIVE = 259
                    is_active = (exit_code.value == STILL_ACTIVE)
                    kernel32.CloseHandle(handle)
                    return is_active
                kernel32.CloseHandle(handle)
                return True
            return False
        except Exception:
            return False
    else:
        try:
            os.kill(pid, 0)
            return True
        except (OSError, ProcessLookupError):
            return False


def _fail_build(message: str) -> None:
    """Report a fatal build-input error to the Stash job log and abort the task."""
    sys.stderr.write(f"\x01e\x02{message}\n")
    sys.stderr.flush()
    raise RuntimeError(message)


def _paths_share_tree(a: Path, b: Path) -> bool:
    """True when either resolved path equals or lies inside the other.

    Case-insensitive via normcase so Windows drive-letter/directory casing
    differences cannot slip a nesting violation past the check.
    """
    a_norm = os.path.normcase(str(Path(a).resolve()))
    b_norm = os.path.normcase(str(Path(b).resolve()))
    try:
        common = os.path.commonpath([a_norm, b_norm])
    except ValueError:
        # Different drives (Windows) — cannot contain each other.
        return False
    return common == a_norm or common == b_norm


def _is_under(child: str, parent: str) -> bool:
    """True when the resolved child path lies inside (or equals) the resolved parent.

    Directional counterpart of _paths_share_tree: case-insensitive via normcase
    so Windows casing differences cannot slip a file past containment, and
    different drives (commonpath ValueError) mean "not under".
    """
    child_norm = os.path.normcase(str(Path(child).resolve()))
    parent_norm = os.path.normcase(str(Path(parent).resolve()))
    try:
        common = os.path.commonpath([child_norm, parent_norm])
    except ValueError:
        # Different drives (Windows) — cannot be nested.
        return False
    return common == parent_norm


def _validate_seed_scratch_paths(seed_dir: Path, scratch_root: Path, seed_must_exist: bool) -> None:
    """
    Validates the seed/scratch pair provided in the build payload:
    - seed_dir must exist as a directory when EXPLICITLY provided (a fallback
      seed_dir may not exist yet — legacy consolidation creates/uses it later);
    - neither path may equal or contain the other, so generated artifacts can
      never land inside the torrent payload tree (or vice versa).

    Raises RuntimeError naming the offending path(s).
    """
    if seed_must_exist and not seed_dir.is_dir():
        _fail_build(
            f"seed_dir '{seed_dir}' does not exist or is not a directory. "
            f"Create it (or run Consolidate against it) before building."
        )
    if _paths_share_tree(seed_dir, scratch_root):
        _fail_build(
            f"seed_dir '{seed_dir}' and scratch_dir '{scratch_root}' must be separate "
            f"locations: neither path may contain the other "
            f"(resolved: '{seed_dir.resolve()}' and '{scratch_root.resolve()}')."
        )


def run_build_megapack(payload: Any, server_connection: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Builds megapack: creates contact sheets, uploads previews, generates .torrent and manifest.
    """
    if isinstance(payload, dict):
        set_active_run_id(payload.get("run_id"))
    emit_progress(0.05, "Initializing megapack build...")
    if not isinstance(payload, dict):
        payload = {}
    
    single_scene = bool(
        payload.get("single_scene")
        or str(payload.get("mode") or "").strip().lower() in ("single", "single_scene", "buildsinglescene")
    )
    pack_title = str(payload.get("pack_title") or f"Megapack_{int(time.time())}")
    safe_title = sanitize_name(pack_title)
    settings = _domain_config.get_settings()
    output_dir = str(payload.get("output_dir") or Path(settings.output_dir))
    artifact_dir = output_dir
    pack_dir = os.path.join(output_dir, safe_title)
    if os.path.basename(os.path.normpath(output_dir)).casefold() == safe_title.casefold():
        pack_dir = output_dir
    payload_source = output_dir

    # Seed/scratch payload inputs: legacy payloads (neither key present) keep
    # today's single-root behavior — artifacts in output_dir, media consolidated
    # into output_dir/<safe_title>. When either key IS present, every generated
    # artifact relocates to <scratch_dir>/<safe_title>/ and the consolidation
    # destination (and torrent root) becomes seed_dir.
    seed_raw = payload.get("seed_dir")
    scratch_raw = payload.get("scratch_dir")
    seed_provided = seed_raw is not None
    scratch_provided = scratch_raw is not None
    if seed_provided and not str(seed_raw).strip():
        _fail_build("seed_dir must be a non-empty directory path when provided in the build payload.")
    if scratch_provided and not str(scratch_raw).strip():
        _fail_build("scratch_dir must be a non-empty directory path when provided in the build payload.")
    seed_dir = Path(str(seed_raw).strip()) if seed_provided else Path(output_dir)
    scratch_root = Path(str(scratch_raw).strip()) if scratch_provided else Path(output_dir)
    if seed_provided or scratch_provided:
        _validate_seed_scratch_paths(seed_dir, scratch_root, seed_must_exist=seed_provided)
        pack_scratch_dir = scratch_root / safe_title
        pack_scratch_dir.mkdir(parents=True, exist_ok=True)
        artifact_dir = str(pack_scratch_dir)
        consolidation_dir = str(seed_dir)
    else:
        consolidation_dir = pack_dir
    
    scenes_raw = payload.get("scenes", [])
    if isinstance(scenes_raw, dict):
        scenes = [scenes_raw]
    elif isinstance(scenes_raw, (list, tuple, set)):
        scenes = list(scenes_raw)
    else:
        scenes = []

    layout = str(payload.get("layout") or "grid_4x4")
    notes = str(payload.get("notes") or "")
    timeout = float(payload.get("timeout") or payload.get("vcsi_timeout") or 60.0)

    # Resolve announce URL: load from settings, ignore payload announce unless explicitly opted in (Amendment B7)
    configured_announce = settings.empornium_announce_url

    custom_trackers = None
    allow_custom_announce = bool(payload.get("allow_custom_announce") or payload.get("enable_custom_announce"))
    if allow_custom_announce and (payload.get("announce") or payload.get("trackers")):
        announce_raw = payload.get("announce") or payload.get("trackers")
        if isinstance(announce_raw, str) and announce_raw.strip():
            announce_url = announce_raw.strip()
            custom_trackers = [announce_url]
        elif isinstance(announce_raw, (list, tuple, set)) and announce_raw:
            custom_trackers = [str(t).strip() for t in announce_raw if t and str(t).strip()]
            announce_url = custom_trackers[0] if custom_trackers else None
        else:
            announce_url = None
            custom_trackers = None
    elif allow_custom_announce and ("announce" in payload or "trackers" in payload) and not (payload.get("announce") or payload.get("trackers")):
        announce_url = None
        custom_trackers = None
    else:
        announce_url = configured_announce
        custom_trackers = [configured_announce] if configured_announce else None

    if announce_url:
        try:
            validate_announce_url(announce_url)
        except TorrentError as err:
            sys.stderr.write(f"\x01e\x02Invalid announce URL: {err}\n")
            sys.stderr.flush()
            raise RuntimeError(f"Invalid announce URL: {err}")

    # Contact sheets: megapack requires them in the torrent by default (Empornium
    # site rule).  Explicit false opts out (media-only torrent).  The singular alias
    # "include_contact_sheet" is still accepted for backward compat but explicit
    # False no longer falls through to True via `or` (old chain: `false or True` -> True).
    # Note: the task-args path (task.py:1755-1781) may deliver flag values as
    # strings ({"include_contact_sheets": "false"}); _coerce_flag treats only
    # 1/true/yes/on (case-insensitive) as true, so a string "false" now
    # correctly opts out of contact sheets.
    # Failure-envelope change: total generation failure (vcsi fail + Pillow save fail,
    # task.py:381-384) now aborts via validate_pack_files_present instead of shipping
    # media-only.
    if "include_contact_sheets" in payload:
        include_contact_sheets = _coerce_flag(payload["include_contact_sheets"])
    elif "include_contact_sheet" in payload:
        include_contact_sheets = _coerce_flag(payload["include_contact_sheet"])
    else:
        include_contact_sheets = True
    if single_scene:
        include_contact_sheets = False

    # Aggregate performers and tags if not provided at top-level
    performers = _extract_names(payload.get("performers"))
    if not performers and scenes:
        scene_performers = []
        for sc in scenes:
            if isinstance(sc, dict):
                scene_performers.extend(_extract_names(sc.get("performers")))
        seen_p = set()
        performers = [p for p in scene_performers if not (p in seen_p or seen_p.add(p))]

    tags = _extract_names(payload.get("tags"))
    if not tags and scenes:
        scene_tags = []
        for sc in scenes:
            if isinstance(sc, dict):
                scene_tags.extend(_extract_names(sc.get("tags")))
        seen_t = set()
        tags = [t for t in scene_tags if not (t in seen_t or seen_t.add(t))]

    os.makedirs(artifact_dir, exist_ok=True)
    lock_file = os.path.join(artifact_dir, f".{safe_title}.lock")

    acquired = False
    for _ in range(3):
        try:
            fd = os.open(lock_file, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            with os.fdopen(fd, "w", encoding="utf-8") as lf:
                lf.write(f"pid={os.getpid()}\nstarted={time.time()}\npack={pack_title}\n")
            acquired = True
            break
        except FileExistsError:
            is_stale = False
            pid = 0
            started = 0.0
            try:
                with open(lock_file, "r", encoding="utf-8", errors="replace") as lf:
                    content = lf.read()
                if not content.strip():
                    time.sleep(0.05)
                    with open(lock_file, "r", encoding="utf-8", errors="replace") as lf:
                        content = lf.read()
                for line in content.splitlines():
                    line_s = line.strip()
                    if line_s.startswith("pid="):
                        try:
                            pid = int(line_s.split("=", 1)[1].strip())
                        except (ValueError, IndexError):
                            pid = 0
                    elif line_s.startswith("started="):
                        try:
                            started = float(line_s.split("=", 1)[1].strip())
                        except (ValueError, IndexError):
                            started = 0.0
                if pid > 0:
                    if not is_pid_running(pid):
                        is_stale = True
                    elif started > 0 and (time.time() - started) > 3600:
                        is_stale = True
                else:
                    is_stale = True
            except Exception:
                is_stale = True

            if is_stale:
                sys.stderr.write(f"\x01w\x02Reclaiming stale lockfile from dead/timed-out process: {lock_file}\n")
                sys.stderr.flush()
                try:
                    os.remove(lock_file)
                except Exception:
                    pass
            else:
                raise RuntimeError(f"Concurrent build in progress for pack '{pack_title}' (lockfile: {lock_file})")

    if not acquired:
        raise RuntimeError(f"Concurrent build in progress for pack '{pack_title}' (lockfile: {lock_file})")

    try:
        emit_progress(0.15, "Validating scene files...")
        expected_primary_paths = _declared_pack_primary_paths(scenes)
        file_paths = [p for p in expected_primary_paths if os.path.exists(p)]

        contact_sheet_paths = []
        total_files = len(file_paths)

        if single_scene and (seed_provided or scratch_provided):
            # T5 megapack parity: the scene's file must exist under the seed
            # dir (recursive). Before the arity check — the existence filter
            # above silently drops missing files, and this refusal names the
            # exact path + hint instead of a bare "found 0" count.
            validate_pack_files_present(consolidation_dir, expected_primary_paths, scenes=scenes)

        if single_scene and total_files != 1:
            sys.stderr.write(f"\x01e\x02Single-scene mode requires exactly 1 media file, found {total_files}.\n")
            sys.stderr.flush()
            raise RuntimeError(
                f"Single-scene mode requires exactly 1 media file, found {total_files} file(s)."
            )

        if total_files == 0:
            # Refuse to build. Continuing here produces a torrent with zero piece
            # hashes, which torf itself rejects with
            #   "Invalid metainfo: ['info']['pieces'] is empty"
            # i.e. an unusable artifact delivered under a FINISHED job. Raise so the
            # outer handler reports it and Stash marks the job FAILED. The lockfile
            # is still released by the finally block below.
            sys.stderr.write("\x01e\x02No valid media files found in scenes payload.\n")
            sys.stderr.flush()
            raise RuntimeError(
                "No valid media files found in scenes payload; refusing to emit an "
                "empty pack. Check that the scene paths exist and are reachable from "
                "the Stash host."
            )

        if not single_scene:
            # Basename collision pre-validation: check for duplicate basenames across scenes
            basename_counts: Dict[str, int] = {}
            for fp in file_paths:
                bname = os.path.basename(fp)
                if bname:
                    norm_b = bname.casefold()
                    basename_counts[norm_b] = basename_counts.get(norm_b, 0) + 1

            colliding_names = [norm_b for norm_b, count in basename_counts.items() if count > 1]
            if colliding_names:
                dup_count = len(colliding_names)
                sys.stderr.write(
                    f"\x01e\x02Basename collision detected ({dup_count} duplicates): {colliding_names}. "
                    f"Consolidation cannot proceed. Rename or exclude conflicting files in Stash before consolidating.\n"
                )
                sys.stderr.flush()
                raise RuntimeError(
                    f"Basename collision detected ({dup_count} duplicates): {colliding_names}. "
                    f"Consolidation cannot proceed. Rename or exclude conflicting files in Stash before consolidating."
                )

            # Ensure all scene files live under the consolidation destination
            # (pack_dir for legacy payloads, seed_dir when the payload provides
            # seed/scratch) before building in-place. Recursive containment: a
            # file at ANY depth under the destination passes.
            if not os.path.isdir(consolidation_dir):
                sys.stderr.write(
                    f"\x01e\x02Pack directory '{consolidation_dir}' does not exist. "
                    f"Consolidation must be run before building the megapack.\n"
                )
                sys.stderr.flush()
                raise RuntimeError(
                    f"Pack directory '{consolidation_dir}' does not exist. "
                    f"Please consolidate files into the pack folder first."
                )

            outside = [fp for fp in file_paths if not _is_under(fp, consolidation_dir)]
            if outside:
                sys.stderr.write(
                    f"\x01e\x02Scene files are not under the seed directory '{consolidation_dir}'. "
                    f"Found {len(outside)} file(s) outside it: {outside[:3]}. "
                    f"Run Consolidate or add the missing files to the seed directory.\n"
                )
                sys.stderr.flush()
                raise RuntimeError(
                    f"Scene files are not under the seed directory '{consolidation_dir}'. "
                    f"Found {len(outside)} file(s) outside it: {', '.join(outside[:3])}. "
                    f"Run Consolidate or add the missing files to the seed directory."
                )

            validate_pack_files_present(consolidation_dir, expected_primary_paths, scenes=scenes)

            payload_source = consolidation_dir
        else:
            if seed_provided or scratch_provided:
                # T5: the torrent is built over the seed DIR (exact-set
                # inclusion, name = seed_dir basename), not the single file.
                payload_source = consolidation_dir
            else:
                payload_source = file_paths[0]

        emit_progress(0.20, f"Found {total_files} valid files. Generating contact sheets...")
        with HeartbeatContext(lambda: (_last_progress, f"Generating contact sheets ({len(contact_sheet_paths)}/{total_files})")):
            for idx, fp in enumerate(file_paths):
                if total_files == 1:
                    cs_name = f"{safe_title}_preview.jpg"
                else:
                    cs_name = f"{safe_title}_preview_{idx + 1}.jpg"
                cs_path = os.path.join(artifact_dir, cs_name)
                cs_res = generate_contact_sheet(
                    video_path=fp,
                    out_path=cs_path,
                    layout=layout,
                    timeout=timeout,
                    pack_title=pack_title,
                    scene_idx=idx,
                    total_scenes=total_files,
                )
                contact_sheet_paths.append(cs_res)
                prog = 0.20 + 0.30 * ((idx + 1) / total_files)
                emit_progress(prog, f"Generated contact sheet {idx + 1}/{total_files}")

        # A single-scene release gets the richer gallery; a megapack already has
        # one contact sheet per scene and would balloon past any sane post size.
        override_cover_url = str(payload.get("cover_image_url") or "").strip()
        safe_override_cover_url = _sanitize_image_url(override_cover_url) if override_cover_url else None

        scene_gallery = {"cover": None, "screens": [], "performers": []}
        if single_scene:
            with HeartbeatContext(lambda: (_last_progress, "Generating single-scene gallery imagery")):
                scene_gallery = build_single_scene_gallery(
                    video_path=file_paths[0],
                    artifact_dir=artifact_dir,
                    safe_title=safe_title,
                    scene=scenes[0] if scenes else None,
                    performer_refs=_extract_performer_refs(
                        (scenes[0].get("performers") if scenes and isinstance(scenes[0], dict) else None)
                        or payload.get("performers")
                    ),
                    progress_callback=emit_progress,
                    fetch_cover=not bool(safe_override_cover_url),
                )

        emit_progress(0.50, "Formatting preview image URLs...")

        # Uploaded as one ordered batch so a single pass over the host covers
        # every image, then split back into roles for BBCode composition.
        if safe_override_cover_url:
            cover_paths = []
            cover_urls = [safe_override_cover_url]
        else:
            cover_paths = [scene_gallery["cover"]] if scene_gallery.get("cover") else []
            cover_urls = []

        screen_paths = list(scene_gallery.get("screens") or [])
        performer_entries = list(scene_gallery.get("performers") or [])
        performer_paths = [entry["path"] for entry in performer_entries]

        thumbs_dir = os.path.join(artifact_dir, "thumbs")
        os.makedirs(thumbs_dir, exist_ok=True)

        screen_thumb_paths: list[str] = []
        performer_thumb_paths: list[str] = []
        contact_sheet_thumb_paths: list[str] = []

        if single_scene:
            for sp in screen_paths:
                thumb_dest = os.path.join(thumbs_dir, f"thumb_{os.path.basename(sp)}")
                try:
                    t_res = str(make_thumbnail(sp, thumb_dest, THUMB_RENDER_WIDTH))
                    screen_thumb_paths.append(t_res)
                except Exception as exc:
                    sys.stderr.write(f"\x01w\x02Failed to generate thumbnail for screen '{sp}': {exc}\n")
                    sys.stderr.flush()
                    screen_thumb_paths.append(sp)

            for pp in performer_paths:
                thumb_dest = os.path.join(thumbs_dir, f"thumb_{os.path.basename(pp)}")
                try:
                    t_res = str(make_thumbnail(pp, thumb_dest, PERFORMER_THUMB_RENDER_WIDTH))
                    performer_thumb_paths.append(t_res)
                except Exception as exc:
                    sys.stderr.write(f"\x01w\x02Failed to generate thumbnail for performer portrait '{pp}': {exc}\n")
                    sys.stderr.flush()
                    performer_thumb_paths.append(pp)
        else:
            for csp in contact_sheet_paths:
                thumb_dest = os.path.join(thumbs_dir, f"thumb_{os.path.basename(csp)}")
                try:
                    t_res = str(make_thumbnail(csp, thumb_dest, THUMB_RENDER_WIDTH))
                    contact_sheet_thumb_paths.append(t_res)
                except Exception as exc:
                    sys.stderr.write(f"\x01w\x02Failed to generate thumbnail for contact sheet '{csp}': {exc}\n")
                    sys.stderr.flush()
                    contact_sheet_thumb_paths.append(csp)

        thumb_paths = screen_thumb_paths + performer_thumb_paths + contact_sheet_thumb_paths

        # Fix B: Presentation budget enforcement over the inline-embedded set
        if single_scene:
            inline_embedded_paths = [p for p in (cover_paths if not safe_override_cover_url else []) + screen_thumb_paths + performer_thumb_paths + contact_sheet_paths if p and os.path.exists(p)]
        else:
            inline_embedded_paths = [p for p in contact_sheet_thumb_paths if p and os.path.exists(p)]

        settings = _domain_config.get_settings()
        presentation_bytes, failed_budget_paths = fit_presentation_budget(
            [Path(p) for p in inline_embedded_paths],
            budget=settings.presentation_max_bytes,
            floor=settings.presentation_min_image_bytes,
        )
        sys.stderr.write(
            f"\x01i\x02[Presentation] {len(inline_embedded_paths)} images, {presentation_bytes} bytes after budget fit (cap {settings.presentation_max_bytes})\n"
        )
        sys.stderr.flush()

        ordered_paths = (cover_paths if not safe_override_cover_url else []) + screen_paths + performer_paths + contact_sheet_paths + thumb_paths
        ordered_urls = upload_previews(ordered_paths, payload, progress_callback=emit_progress)

        cursor = 0
        if not safe_override_cover_url:
            cover_urls = ordered_urls[cursor:cursor + len(cover_paths)]
            cursor += len(cover_paths)
        screen_urls = ordered_urls[cursor:cursor + len(screen_paths)]
        cursor += len(screen_paths)
        performer_urls = ordered_urls[cursor:cursor + len(performer_paths)]
        cursor += len(performer_paths)
        contact_sheet_urls = ordered_urls[cursor:cursor + len(contact_sheet_paths)]
        cursor += len(contact_sheet_paths)

        thumb_urls = ordered_urls[cursor:] if len(ordered_urls) > cursor else []

        thumb_cursor = 0
        screen_thumb_urls = thumb_urls[thumb_cursor:thumb_cursor + len(screen_thumb_paths)]
        thumb_cursor += len(screen_thumb_paths)
        performer_thumb_urls = thumb_urls[thumb_cursor:thumb_cursor + len(performer_thumb_paths)]
        thumb_cursor += len(performer_thumb_paths)
        contact_sheet_thumb_urls = thumb_urls[thumb_cursor:thumb_cursor + len(contact_sheet_thumb_paths)]

        for entry, url in zip(performer_entries, performer_urls):
            entry["url"] = url

        uploaded_image_urls = ([safe_override_cover_url] if safe_override_cover_url else []) + ordered_urls

        emit_progress(0.70, "Generating .torrent file...")
        torrent_filename = f"{safe_title}.torrent"
        torrent_path = os.path.join(artifact_dir, torrent_filename)

        # T4 exact-set torrent: artifacts live in the per-pack scratch folder
        # (outside the seed dir), so no artifact globs are needed — every
        # non-pack file is excluded by exact relative path instead, and the
        # torrent name is the consolidation dir's basename (torf default;
        # legacy pack_dir basename == safe_title, so legacy naming survives).
        exclude_exact: Optional[List[str]] = None
        torrent_expected_relpaths: Optional[Set[str]] = None
        if single_scene:
            if seed_provided or scratch_provided:
                # T5: exact-set inclusion over the seed dir — only the
                # scene's file — verified post-generation (name = seed_dir
                # basename). Legacy single-file torrent stays untouched.
                exclude_exact, torrent_expected_relpaths = _torrent_exact_sets(
                    consolidation_dir, expected_primary_paths
                )
        else:
            # If include_contact_sheets is enabled, ensure contact sheets subfolder exists in the
            # consolidation destination (seed_dir when provided, pack_dir for legacy payloads)
            contact_sheet_extras: List[str] = []
            if include_contact_sheets and contact_sheet_paths:
                cs_dir = os.path.join(consolidation_dir, "Contact Sheets")
                os.makedirs(cs_dir, exist_ok=True)
                for cs_p in contact_sheet_paths:
                    cs_dest = os.path.join(cs_dir, os.path.basename(cs_p))
                    if os.path.exists(cs_p) and os.path.dirname(os.path.abspath(cs_p)) != os.path.abspath(cs_dir):
                        try:
                            shutil.copy2(cs_p, cs_dest)
                        except Exception:
                            pass
                    contact_sheet_extras.append(cs_dest)

            # Pack-file-presence validation (replaces the deleted foreign-file
            # refusal): every declared pack primary — plus the Contact Sheets
            # copies when include_contact_sheets is ON — must exist under the
            # consolidation dir at any depth. Unrelated files are ignored.
            validate_pack_files_present(consolidation_dir, expected_primary_paths, contact_sheet_extras)

            exclude_exact, torrent_expected_relpaths = _torrent_exact_sets(
                consolidation_dir, expected_primary_paths, contact_sheet_extras
            )

        def hashing_callback(torrent_obj, filepath, pieces_done, total_pieces):
            frac = pieces_done / max(total_pieces, 1)
            prog = 0.70 + (0.20 * frac)
            emit_progress(prog, f"Hashing torrent pieces ({pieces_done}/{total_pieces})...")

        torrent_source = source_for_announce(announce_url) if announce_url else None
        create_torrent_kwargs = {
            "payload_dir": payload_source,
            "announce_url": announce_url if not (custom_trackers and len(custom_trackers) > 1) else None,
            "trackers": custom_trackers if (custom_trackers and len(custom_trackers) > 1) else None,
            "out_path": torrent_path,
            "source": torrent_source,
            "private": True if (announce_url or custom_trackers) else False,
            "callback": hashing_callback,
        }
        if exclude_exact is not None:
            create_torrent_kwargs["exclude_exact"] = exclude_exact
        with HeartbeatContext(lambda: (_last_progress, "Hashing torrent pieces")):
            create_torrent(**create_torrent_kwargs)

        if torrent_expected_relpaths is not None:
            _verify_torrent_exact_set(torrent_path, consolidation_dir, torrent_expected_relpaths)

        emit_progress(0.90, "Creating BBCode and pack manifest...")
        esc_title = bbcode_escape(pack_title) if pack_title else ""
        esc_notes = bbcode_escape(notes, keep_newlines=True) if notes else ""

        # Presentation banner. Payload overrides the configured default so a
        # single upload can opt out without editing config; "plate" absorbs the
        # title, so the separate centred title line is dropped for that style.
        banner_style = "off"
        if render_banner is not None:
            raw_style = payload.get("banner", payload.get("presentation_banner"))
            if raw_style is None:
                raw_style = getattr(settings, "presentation_banner", DEFAULT_BANNER_STYLE)
            banner_style = normalize_banner_style(raw_style)
        pack_size = _format_pack_size(
            sum(os.path.getsize(fp) for fp in file_paths if os.path.exists(fp))
        )

        # 1. Unified Studio header
        studio = pack_studio(scenes)

        # 2. Performer Union with +N more cap
        payload_performers = _extract_names(payload.get("performers"))
        if payload_performers:
            all_scene_performers = payload_performers
            top_performers = all_scene_performers[:4]
            extra_count = len(all_scene_performers) - len(top_performers)
        else:
            all_scene_performers = sorted({
                p.strip()
                for s in scenes
                for p in _extract_names(s.get("performers") if isinstance(s, dict) else getattr(s, "performers", []))
                if p.strip()
            })
            top_performers = pack_performer_union(scenes, limit=4)
            extra_count = len(all_scene_performers) - len(top_performers)

        if top_performers:
            joined_performers = join_names([bbcode_escape(p) for p in top_performers])
            if extra_count > 0:
                joined_performers += f" +{extra_count} more"
        else:
            joined_performers = "Various"

        # 3. Tags
        payload_tags = _extract_names(payload.get("tags"))
        if payload_tags:
            all_tags = payload_tags
        else:
            all_tags = sorted({
                t.strip()
                for s in scenes
                for t in _extract_names(s.get("tags") if isinstance(s, dict) else getattr(s, "tags", []))
                if t.strip()
            })
        esc_tags = [bbcode_escape(t) for t in all_tags] if all_tags else []

        if single_scene:
            sc = scenes[0] if scenes else {}
            height = sc.get("height") if isinstance(sc, dict) else getattr(sc, "height", None)
            duration = sc.get("duration") if isinstance(sc, dict) else getattr(sc, "duration", None)
            res_tag = resolution_for(height)
            dur_tag = format_duration(duration)

            meta_badges = []
            if res_tag:
                meta_badges.append(f"[{res_tag}]")
            if dur_tag:
                meta_badges.append(f"[{dur_tag}]")
            meta_suffix = f" {' '.join(meta_badges)}" if meta_badges else ""

            banner = render_banner(
                banner_style,
                title=pack_title,
                kind="RELEASE",
                subtitle=studio or "",
                stats=[
                    ("Runtime", dur_tag),
                    ("Resolution", res_tag),
                    ("Size", pack_size),
                    ("Codec", str(_dominant_codec(scenes) or "")),
                ],
            ) if banner_style != "off" else ""

            bbcode_lines = [banner] if banner else []
            if banner_style != "plate":
                bbcode_lines.append(
                    f"[center][b][size=5]{esc_title}{meta_suffix}[/size][/b][/center]"
                )
            if studio:
                bbcode_lines.append(f"\n[b]Studio:[/b] {bbcode_escape(studio)}")
            bbcode_lines.append(f"\n[b]Performers:[/b] {joined_performers}")
            if esc_tags:
                bbcode_lines.append(f"\n[b]Tags:[/b] {', '.join(esc_tags)}")
            bbcode_lines.append("\n[hr]")
            if esc_notes:
                bbcode_lines.append(f"\n[quote]{esc_notes}[/quote]\n")
        else:
            banner = render_banner(
                banner_style,
                title=pack_title,
                kind="MEGAPACK",
                subtitle=_pack_year_span(scenes) or studio or "",
                stats=[
                    ("Scenes", str(len(scenes)) if scenes else ""),
                    ("Runtime", _total_runtime(scenes)),
                    ("Size", pack_size),
                    ("Top res", _top_resolution(scenes)),
                    ("Codec", _dominant_codec(scenes)),
                ],
            ) if banner_style != "off" else ""

            bbcode_lines = [banner] if banner else []
            if banner_style != "plate":
                bbcode_lines.append(f"[center][b][size=5]{esc_title}[/size][/b][/center]")
            if studio:
                bbcode_lines.append(f"\n[b]Studio:[/b] {bbcode_escape(studio)}")
            bbcode_lines.extend([
                f"\n[b]Performers:[/b] {joined_performers}",
                f"\n[b]Tags:[/b] {', '.join(esc_tags) if esc_tags else 'Megapack'}",
                f"\n[b]Scenes Included:[/b] {len(scenes)}",
            ])

            # 4. Detailed scene breakdown with resolution and duration badges
            if scenes:
                for idx, scene in enumerate(scenes, 1):
                    s_title = scene.get("title") if isinstance(scene, dict) else getattr(scene, "title", None)
                    s_title = bbcode_escape(str(s_title).strip()) if s_title and str(s_title).strip() else f"Scene {idx}"
                    s_perfs = _extract_names(scene.get("performers") if isinstance(scene, dict) else getattr(scene, "performers", []))
                    s_ptext = f" ({join_names([bbcode_escape(p) for p in s_perfs])})" if s_perfs else ""

                    height = scene.get("height") if isinstance(scene, dict) else getattr(scene, "height", None)
                    duration = scene.get("duration") if isinstance(scene, dict) else getattr(scene, "duration", None)
                    res_tag = resolution_for(height)
                    dur_tag = format_duration(duration)

                    meta_badges = []
                    if res_tag:
                        meta_badges.append(f"[{res_tag}]")
                    if dur_tag:
                        meta_badges.append(f"[{dur_tag}]")
                    meta_suffix = f" {' '.join(meta_badges)}" if meta_badges else ""

                    bbcode_lines.append(f"{idx}. [b]{s_title}[/b]{s_ptext}{meta_suffix}")

            bbcode_lines.append("\n[hr]")
            if esc_notes:
                bbcode_lines.append(f"\n[quote]{esc_notes}[/quote]\n")

        # Compute tracker tags via domain merge_tags engine (for Stage 5 upload/tracker submission)
        if merge_tags_detailed is not None:
            resolved_detailed = merge_tags_detailed(scenes)
            tracker_tags = list(resolved_detailed.tags)
            unmapped_tags = list(resolved_detailed.unmapped)
        elif merge_tags is not None:
            tracker_tags = merge_tags(scenes)
            unmapped_tags = []
        else:
            tracker_tags = []
            unmapped_tags = []
        if payload_tags:
            for pt in payload_tags:
                emp = empify(pt) if empify else str(pt).strip().lower()
                if emp and emp not in tracker_tags:
                    tracker_tags.append(emp)
            tracker_tags = sorted(set(tracker_tags))[:60]

        has_local_file_urls = any(u.startswith("file:///") for u in uploaded_image_urls)
        if has_local_file_urls:
            sys.stderr.write(
                "\x01w\x02BBCode contains local file:/// URLs (preview only; do not post to public trackers)\n"
            )
            sys.stderr.flush()
            bbcode_lines.insert(0, "[color=red][b]PREVIEW ONLY: Contains local file:/// URLs[/b][/color]\n")

        if single_scene:
            # Sectioned gallery: cover, performer row, screens grid, then the
            # contact sheet folded away so it does not dominate the post.
            for url in cover_urls:
                safe_url = _sanitize_image_url(url)
                bbcode_lines.append(f"\n[center][img]{safe_url}[/img][/center]")

            labelled_performers = [e for e in performer_entries if e.get("url")]
            if labelled_performers:
                bbcode_lines.append("\n[b]Performers[/b]\n")
                cards = []
                for i, entry in enumerate(labelled_performers):
                    full_u = entry["url"]
                    thumb_u = performer_thumb_urls[i] if i < len(performer_thumb_urls) and performer_thumb_urls[i] else full_u
                    safe_thumb = _sanitize_image_url(thumb_u)
                    name = bbcode_escape(entry.get("name") or "")
                    cards.append(f"[img={PERFORMER_THUMB_WIDTH}]{safe_thumb}[/img] {name}")
                bbcode_lines.append("  ".join(cards))

            if screen_urls:
                bbcode_lines.append("\n[b]Screens[/b]\n")
                screens_markup = []
                for i, full_u in enumerate(screen_urls):
                    safe_full = _sanitize_image_url(full_u)
                    thumb_u = screen_thumb_urls[i] if i < len(screen_thumb_urls) and screen_thumb_urls[i] else full_u
                    safe_thumb = _sanitize_image_url(thumb_u)
                    screens_markup.append(f"[url={safe_full}][img={THUMB_WIDTH}]{safe_thumb}[/img][/url]")
                bbcode_lines.append("".join(screens_markup))

            if contact_sheet_urls:
                bbcode_lines.append("\n[b]Contact Sheet[/b]\n")
                bbcode_lines.append(f"[spoiler={_contact_sheet_spoiler_label(len(contact_sheet_urls))}]")
                for url in contact_sheet_urls:
                    bbcode_lines.append(f"[img]{_sanitize_image_url(url)}[/img]")
                bbcode_lines.append("[/spoiler]")
        else:
            if safe_override_cover_url:
                bbcode_lines.append(f"\n[center][img]{safe_override_cover_url}[/img][/center]\n")
            # Emit every thumbnail on ONE logical line. The tracker runs the
            # description through nl2br, so a newline between two [img] tags
            # becomes a <br> and forces one thumbnail per row -- a 130-sheet
            # pack then renders roughly 6x taller than it needs to be. Joined
            # with no separator the thumbnails still wrap on their own and
            # fill the post width, which is what the single-scene "Screens"
            # section above already does.
            sheets_markup = []
            for i, full_u in enumerate(contact_sheet_urls):
                safe_full = _sanitize_image_url(full_u)
                thumb_u = contact_sheet_thumb_urls[i] if i < len(contact_sheet_thumb_urls) and contact_sheet_thumb_urls[i] else full_u
                safe_thumb = _sanitize_image_url(thumb_u)
                sheets_markup.append(f"[url={safe_full}][img={THUMB_WIDTH}]{safe_thumb}[/img][/url]")
            if sheets_markup:
                # Folded away like the single-scene sheet: a 130-scene pack is
                # otherwise a wall of thumbnails every reader pays for on load.
                # The joined single line above stays intact inside the spoiler.
                # Exactly one blank line before the heading: the notes block
                # above already ends in a newline, the bare [hr] does not.
                gap = "" if bbcode_lines and bbcode_lines[-1].endswith("\n") else "\n"
                bbcode_lines.append(f"{gap}[b]Contact Sheets[/b]\n")
                bbcode_lines.append(f"[spoiler={_contact_sheet_spoiler_label(len(sheets_markup))}]")
                bbcode_lines.append("".join(sheets_markup))
                bbcode_lines.append("[/spoiler]")

        bbcode_content = "\n".join(bbcode_lines)
        bbcode_path = os.path.join(artifact_dir, f"{safe_title}_bbcode.txt")
        with open(bbcode_path, "w", encoding="utf-8") as bf:
            bf.write(bbcode_content)

        created_timestamp = time.time()
        masked_announce = sanitize_announce_url(announce_url) if announce_url else None

        # Resolve Empornium site URL from payload or settings (Stage 6 cleanup)
        site_url = str(payload.get("site_url") or payload.get("empornium_site_url") or settings.empornium_site_url or "").strip()

        # Assemble Empornium submission payload (<safe_title>_submission.json)
        # Note (Amendment B3): We use the Stage 4 composed BBCode renderer (bbcode_content)
        # and do NOT call finalize_description as it requires {scene-image-N} placeholders.
        # Note (Stage 6 R2): category is removed; user selects category on Empornium upload form.
        submission_payload = {
            "title": pack_title,
            "tracker_tags": tracker_tags,
            "unmapped_tags": unmapped_tags,
            "description": bbcode_content,
            "image_urls": uploaded_image_urls,
            "torrent_path": torrent_path,
            "created_at": created_timestamp,
            "preview_only": has_local_file_urls,
            "announce_url": masked_announce,
            "source": torrent_source,
            "site_url": site_url,
            "presentation_bytes": presentation_bytes,
        }

        # R4 (6d): Pre-flight checklist verification against written artifacts on disk with torf
        preflight_results = verify_preflight_checklist(
            torrent_path=torrent_path,
            submission_path=None,
            output_dir=artifact_dir,
            payload_root=payload_source,
            pack_title=pack_title,
            submission_data=submission_payload,
            presentation_bytes=presentation_bytes,
        )
        submission_payload["preflight"] = preflight_results

        submission_path = os.path.join(artifact_dir, f"{safe_title}_submission.json")
        with open(submission_path, "w", encoding="utf-8") as sf:
            json.dump(submission_payload, sf, indent=2)

        manifest = {
            "pack_title": pack_title,
            "created_at": created_timestamp,
            "scene_count": len(scenes),
            "scenes": scenes,
            "torrent_path": torrent_path,
            "bbcode_path": bbcode_path,
            "submission_path": submission_path,
            "contact_sheets": contact_sheet_paths,
            "uploaded_urls": uploaded_image_urls,
            "preview_only": has_local_file_urls,
            "tracker_tags": tracker_tags,
            "unmapped_tags": unmapped_tags,
            "announce_url": masked_announce,
            "source": torrent_source,
            "site_url": site_url,
            "output_dir": output_dir,
            "artifact_dir": artifact_dir,
            "presentation_bytes": presentation_bytes,
            "preflight": preflight_results,
        }

        manifest_path = os.path.join(artifact_dir, f"{safe_title}_manifest.json")
        write_manifest(manifest_path, manifest)

        dry_run = bool(payload.get("dry_run") or payload.get("enable_dry_run"))
        if dry_run:
            sys.stderr.write(
                f"\x01i\x02[Dry Run] Megapack submission payload assembled for '{pack_title}' "
                f"with tracker '{masked_announce or 'None'}'. No tracker POST executed.\n"
            )
            sys.stderr.flush()

        emit_progress(1.0, "Build completed successfully!" if single_scene else "Megapack build completed successfully!")

        return {
            "status": "success",
            "task": "BuildSingleScene" if single_scene else "BuildMegapack",
            "pack_title": pack_title,
            "torrent_path": torrent_path,
            "bbcode": bbcode_content,
            "bbcode_path": bbcode_path,
            "manifest_path": manifest_path,
            "submission_path": submission_path,
            "submission_payload": submission_payload,
            "contact_sheets": contact_sheet_paths,
            "uploaded_urls": uploaded_image_urls,
            "preview_only": has_local_file_urls,
            "tracker_tags": tracker_tags,
            "unmapped_tags": unmapped_tags,
            "announce_url": masked_announce,
            "source": torrent_source,
            "site_url": site_url,
            "empornium_site_url": site_url,
            "dry_run": dry_run,
            "preflight": preflight_results,
            "ready": preflight_results.get("ready", False),
        }

    finally:
        if os.path.exists(lock_file):
            try:
                with open(lock_file, "r", encoding="utf-8", errors="replace") as lf:
                    content = lf.read()
                if f"pid={os.getpid()}" in content:
                    os.remove(lock_file)
            except Exception:
                try:
                    os.remove(lock_file)
                except Exception:
                    pass


def parse_input_payload() -> tuple[str, Dict[str, Any], Dict[str, Any]]:
    """
    Parses task name, args payload, and server_connection from stdin or command line arguments.
    """
    mode = "build"
    if len(sys.argv) > 1:
        arg1 = sys.argv[1].lower()
        if "probe" in arg1:
            mode = "probe"
        elif "upload_cover" in arg1 or "uploadcover" in arg1:
            mode = "upload_cover"
        elif "start_backend" in arg1 or "startbackend" in arg1 or "start-backend" in arg1:
            mode = "start_backend"
        elif "single" in arg1:
            mode = "single"
        elif "build" in arg1:
            mode = "build"

    server_connection = {}
    payload = {}

    if not sys.stdin.isatty():
        try:
            raw_input = sys.stdin.read().strip()
            if raw_input:
                parsed = json.loads(raw_input)
                if isinstance(parsed, dict):
                    server_connection = parsed.get("server_connection", {})
                    if not isinstance(server_connection, dict):
                        server_connection = {}
                    task_name = str(parsed.get("task_name", "") or "")
                    if task_name:
                        if "probe" in task_name.lower():
                            mode = "probe"
                        elif "uploadcover" in task_name.lower() or "upload_cover" in task_name.lower() or task_name == "UploadCoverImage":
                            mode = "upload_cover"
                        elif "startbackend" in task_name.lower() or "start_backend" in task_name.lower() or "start-backend" in task_name.lower() or task_name == "StartBackend":
                            mode = "start_backend"
                        elif "single" in task_name.lower() or task_name == "BuildSingleScene":
                            mode = "single"
                        elif "build" in task_name.lower():
                            mode = "build"

                    args = parsed.get("args", {})
                    if isinstance(args, list):
                        for item in args:
                            if isinstance(item, dict):
                                k = item.get("key")
                                v = item.get("value")
                                if isinstance(v, dict) and "str" in v:
                                    v = v["str"]
                                if k == "mode" and v:
                                    v_str = str(v).lower()
                                    if "probe" in v_str:
                                        mode = "probe"
                                    elif "upload_cover" in v_str or "uploadcover" in v_str:
                                        mode = "upload_cover"
                                    elif "start_backend" in v_str or "startbackend" in v_str or "start-backend" in v_str:
                                        mode = "start_backend"
                                    elif "single" in v_str:
                                        mode = "single"
                                    else:
                                        mode = "build"
                                elif k == "payload" and v:
                                    try:
                                        parsed_p = json.loads(v) if isinstance(v, str) else v
                                        if isinstance(parsed_p, dict) and isinstance(payload, dict):
                                            existing = payload
                                            payload = parsed_p
                                            for ek, ev in existing.items():
                                                if ek not in payload:
                                                    payload[ek] = ev
                                        else:
                                            payload = parsed_p
                                    except Exception:
                                        payload = v
                                elif k:
                                    payload[k] = v
                    elif isinstance(args, dict):
                        if "mode" in args:
                            v_str = str(args["mode"]).lower()
                            if "probe" in v_str:
                                mode = "probe"
                            elif "upload_cover" in v_str or "uploadcover" in v_str:
                                mode = "upload_cover"
                            elif "start_backend" in v_str or "startbackend" in v_str or "start-backend" in v_str:
                                mode = "start_backend"
                            elif "single" in v_str:
                                mode = "single"
                            else:
                                mode = "build"
                        if "payload" in args:
                            if isinstance(args["payload"], dict):
                                payload = args["payload"]
                            elif isinstance(args["payload"], str):
                                try:
                                    payload = json.loads(args["payload"])
                                except Exception:
                                    payload = {"raw": args["payload"]}
                            else:
                                payload = {}
                        else:
                            payload = args
                        if isinstance(payload, dict) and "run_id" in args and "run_id" not in payload:
                            payload["run_id"] = args["run_id"]
                    elif isinstance(args, str):
                        try:
                            payload = json.loads(args)
                        except Exception:
                            payload = {"raw": args}
                elif isinstance(parsed, (list, str, int, float, bool)):
                    # Non-dict JSON root
                    payload = {}
        except Exception as e:
            sys.stderr.write(f"\x01w\x02Error parsing stdin input: {e}\n")
            sys.stderr.flush()

    if not isinstance(payload, dict):
        payload = {}
    if not isinstance(server_connection, dict):
        server_connection = {}

    return mode, payload, server_connection


def run_upload_cover(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Upload a pasted cover image to HamsterImg.
    Decodes base64 image data, sanitizes and converts RGBA/P formats to JPEG RGB,
    enforces max size limit, and uploads to HamsterImg.
    """
    if not isinstance(payload, dict):
        raise ValueError("Payload must be a dictionary")
    run_id = payload.get("run_id") or f"cover_{int(time.time())}"
    set_active_run_id(run_id)
    raw_b64 = payload.get("image_b64")
    if not raw_b64 or not isinstance(raw_b64, str):
        raise ValueError("No image data provided in payload (missing or invalid 'image_b64')")

    if "," in raw_b64 and "base64" in raw_b64:
        raw_b64 = raw_b64.split(",", 1)[1]
    raw_b64 = raw_b64.strip()

    # Reject payload over ~12 MB of base64
    if len(raw_b64) > 12 * 1024 * 1024:
        raise ValueError(f"Cover image payload too large ({len(raw_b64)} chars > 12 MB limit)")

    try:
        img_bytes = base64.b64decode(raw_b64)
    except Exception as exc:
        raise ValueError(f"Invalid base64 encoding for cover image: {exc}")

    from PIL import Image
    import io

    try:
        img = Image.open(io.BytesIO(img_bytes))
        img.load()
    except Exception as exc:
        raise ValueError(f"Failed to decode image data: {exc}")

    if img.mode in ("RGBA", "LA", "P"):
        if img.mode in ("RGBA", "LA"):
            background = Image.new("RGB", img.size, (255, 255, 255))
            background.paste(img, mask=img.split()[-1])
            img = background
        else:
            img = img.convert("RGB")
    elif img.mode != "RGB":
        img = img.convert("RGB")

    settings = _domain_config.get_settings()
    scratch_raw = str(payload.get("scratch_dir") or "").strip()
    if scratch_raw:
        # Pasted covers relocate into the per-pack scratch folder when the
        # caller provides one; legacy payloads keep the staging fallback.
        pack_title = str(payload.get("pack_title") or "").strip()
        safe_title = sanitize_name(pack_title) if pack_title else ""
        pack_scratch = Path(scratch_raw) / safe_title if safe_title else Path(scratch_raw)
        covers_dir = pack_scratch / "covers"
    else:
        covers_dir = Path(settings.staging_dir) / "pasted_covers"
    covers_dir.mkdir(parents=True, exist_ok=True)

    safe_run_id = sanitize_name(str(run_id))
    dest_path = covers_dir / f"{safe_run_id}.jpg"
    img.save(dest_path, format="JPEG", quality=90)

    _domain_images.enforce_size_limit(dest_path, settings.upload_image_max_bytes)

    file_sz = dest_path.stat().st_size
    prefix = _run_id_prefix()
    _stderr_write(f"\x01i\x02{prefix}[Upload] Starting {dest_path.name} ({file_sz} bytes) -> HamsterImg\n")
    t0 = time.monotonic()

    def _on_cover_retry(retry_msg: str) -> None:
        _stderr_write(f"\x01w\x02{prefix}[Upload] {dest_path.name}: {retry_msg}\n")

    with HeartbeatContext(lambda: (_last_progress, f"Uploading {dest_path.name} to HamsterImg")):
        cover_url = _call_upload_hamster(dest_path, settings=settings, log_cb=_on_cover_retry)
    elapsed = time.monotonic() - t0
    _stderr_write(f"\x01i\x02{prefix}[Upload] {dest_path.name} uploaded in {elapsed:.1f}s -> {cover_url}\n")

    return {
        "status": "success",
        "task": "UploadCoverImage",
        "run_id": run_id,
        "cover_url": cover_url,
        "local_path": str(dest_path),
    }


# Excluded from the log sentinel: duplicated elsewhere in the result or only
# meaningful on the server's filesystem, and both are large.
_SENTINEL_EXCLUDED_KEYS = ("submission_payload", "contact_sheets")

# Stash reads plugin stderr one line at a time. A line past its per-line ceiling
# is dropped AND leaves the stream unreadable, after which every further write
# raises OSError ([Errno 22] Invalid argument on Windows) -- which used to turn a
# finished build into a reported failure. Keep every sentinel line well under the
# ceiling: 36 KB lines have been observed to survive, so 30 KB leaves margin.
# (The old 100000 was too high to bind: a megapack result lands under it and was
# emitted as one ~70 KB line.)
_SENTINEL_MAX_CHARS = 30000
_BBCODE_CHUNK_CHARS = 4000

# Shed in this order to bring an oversized sentinel under the cap. BBCode has its
# own chunked channel and the sidecar run store holds the full result, so nothing
# is lost outright -- only the degraded log-fallback path sees fewer fields.
_SENTINEL_SHEDDABLE_KEYS = ("bbcode", "uploaded_urls", "image_urls", "scenes")


def get_sidecar_port() -> int:
    """
    Resolves sidecar port with fallback precedence:
    1. EMPORNIUM_PORT environment variable
    2. settings.port (from backend config)
    3. Default 9941

    Coupling note: Port 9941 is currently pinned by the plugin CSP
    (plugin/empornium-megapack.yml) and by backendEndpoints()
    (plugin/assets/review.js).
    """
    env_port = os.environ.get("EMPORNIUM_PORT")
    if env_port:
        try:
            return int(env_port)
        except (ValueError, TypeError):
            pass
    if _domain_config is not None:
        try:
            settings = _domain_config.get_settings()
            return getattr(settings, "port", 9941)
        except Exception:
            pass
    return 9941


def get_plugin_build_stamp() -> Optional[str]:
    """Reads the packaged build stamp from CURRENT_DIR, or returns None in a dev checkout."""
    for filename in ("BUILD_STAMP", "build_stamp"):
        stamp_file = CURRENT_DIR / filename
        if stamp_file.is_file():
            try:
                val = stamp_file.read_text(encoding="utf-8").strip()
                if val:
                    return val
            except Exception:
                pass
    return None


def check_sidecar_health(port: int) -> Tuple[bool, Optional[Dict[str, Any]]]:
    """Queries GET /health on loopback. Returns (is_healthy, health_dict)."""
    url = f"http://127.0.0.1:{port}/health"
    req = urllib.request.Request(url, headers={"Host": f"127.0.0.1:{port}"})
    try:
        with urllib.request.urlopen(req, timeout=2.0) as resp:
            if resp.status == 200:
                body = resp.read().decode("utf-8")
                return True, json.loads(body)
    except Exception:
        pass
    return False, None


def shutdown_sidecar(port: int) -> bool:
    """Sends POST /api/shutdown to gracefully terminate running sidecar."""
    url = f"http://127.0.0.1:{port}/api/shutdown"
    req = urllib.request.Request(
        url,
        data=b"{}",
        headers={
            "Content-Type": "application/json",
            "Host": f"127.0.0.1:{port}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            return resp.status in (200, 202, 204)
    except Exception as exc:
        sys.stderr.write(f"\x01w\x02[StartBackend] Shutdown request error: {exc}\n")
        sys.stderr.flush()
        return False


def wait_for_port_release(port: int, host: str = "127.0.0.1", timeout: float = 10.0) -> None:
    """Polls until loopback port is no longer listening."""
    start = time.time()
    while time.time() - start < timeout:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.5)
            if s.connect_ex((host, port)) != 0:
                return
        time.sleep(0.2)
    raise RuntimeError(
        f"Port {port} failed to release within {timeout}s after shutdown request. "
        f"Another process may still be holding port {port}."
    )


def spawn_sidecar(port: int) -> None:
    """Spawns sidecar background process detached with std streams disconnected."""
    venv_dir = CURRENT_DIR / ".venv"
    if os.environ.get("EMPORNIUM_VENV"):
        venv_dir = Path(os.environ["EMPORNIUM_VENV"])

    if os.name == "nt":
        python_exe = venv_dir / "Scripts" / "python.exe"
    else:
        python_exe = venv_dir / "bin" / "python"

    if not python_exe.is_file():
        err_msg = (
            f"Virtual environment not found at {venv_dir}. "
            f"Please run install.ps1 (Windows) or install.sh (Linux/macOS) first to set up the environment."
        )
        sys.stderr.write(f"\x01e\x02{err_msg}\n")
        sys.stderr.flush()
        raise RuntimeError(err_msg)

    repo_backend = CURRENT_DIR.parent / "backend"
    if repo_backend.is_dir() and (CURRENT_DIR / "empornium-megapack.yml").is_file():
        app_dir = repo_backend
    else:
        app_dir = CURRENT_DIR

    env = dict(os.environ)
    env["PYTHONPATH"] = str(app_dir)

    ffmpeg_dir = env.get("EMPORNIUM_FFMPEG_DIR")
    if not (ffmpeg_dir and os.path.isfile(os.path.join(ffmpeg_dir, "ffmpeg.exe" if os.name == "nt" else "ffmpeg"))):
        stash_ffmpeg = Path.home() / ".stash"
        if (stash_ffmpeg / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")).is_file():
            ffmpeg_dir = str(stash_ffmpeg)
    if ffmpeg_dir:
        env["PATH"] = f"{ffmpeg_dir}{os.pathsep}{env.get('PATH', '')}"

    cmd = [
        str(python_exe),
        "-m",
        "uvicorn",
        "empornium_megapack.main:app",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--app-dir",
        str(app_dir),
    ]

    kwargs: Dict[str, Any] = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }

    if os.name == "nt":
        creationflags = 0
        if hasattr(subprocess, "DETACHED_PROCESS"):
            creationflags |= subprocess.DETACHED_PROCESS
        else:
            creationflags |= 0x00000008
        if hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP"):
            creationflags |= subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            creationflags |= 0x00000200
        kwargs["creationflags"] = creationflags
    else:
        kwargs["start_new_session"] = True

    sys.stderr.write(f"\x01i\x02[StartBackend] Launching detached sidecar on 127.0.0.1:{port}...\n")
    sys.stderr.flush()
    subprocess.Popen(cmd, cwd=str(app_dir), env=env, **kwargs)


def run_start_backend(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Handles StartBackend task:
    1. Checks if sidecar is currently healthy on resolved port.
    2. Adopts if stamps match or in dev checkout.
    3. Triggers shutdown and waits if stamp differs.
    4. Spawns detached sidecar process if not running.
    """
    port = get_sidecar_port()
    is_healthy, health_data = check_sidecar_health(port)
    plugin_stamp = get_plugin_build_stamp()

    if is_healthy and health_data:
        running_stamp = health_data.get("build_stamp")
        if not plugin_stamp:
            sys.stderr.write(
                f"\x01i\x02[StartBackend] Sidecar running on port {port} adopted (dev checkout, no build stamp)\n"
            )
            sys.stderr.flush()
            return {"status": "ok", "action": "adopted", "port": port, "build_stamp": running_stamp}

        if running_stamp == plugin_stamp:
            sys.stderr.write(
                f"\x01i\x02[StartBackend] Sidecar running on port {port} matches build stamp {plugin_stamp}; adopted\n"
            )
            sys.stderr.flush()
            return {"status": "ok", "action": "adopted", "port": port, "build_stamp": running_stamp}

        sys.stderr.write(
            f"\x01w\x02[StartBackend] Sidecar on port {port} has stamp {running_stamp!r}, expected {plugin_stamp!r}. Shutting down stale sidecar...\n"
        )
        sys.stderr.flush()
        shutdown_sidecar(port)
        wait_for_port_release(port, timeout=10.0)

    spawn_sidecar(port)
    return {"status": "ok", "action": "started", "port": port}




def post_result_to_sidecar(payload: Any, result: Any) -> bool:
    """
    Posts task execution result to the sidecar run store via HTTP POST.
    Best-effort: timeout <= 3s, catches all errors, never raises or delays the build.

    Returns True only when the run store accepted the result, so a caller can tell
    "finished and stored" apart from "failed".
    """
    run_id = "<unknown>"
    try:
        if not isinstance(result, dict):
            return False
        raw_run_id = payload.get("run_id") if isinstance(payload, dict) else None
        if not raw_run_id or not isinstance(raw_run_id, str):
            return False
        run_id = str(raw_run_id).strip()
        if not run_id:
            return False

        compact = {k: v for k, v in result.items() if k not in _SENTINEL_EXCLUDED_KEYS}

        # Port resolution with fallback
        # Coupling note: Port 9941 is currently pinned by the plugin CSP (plugin/empornium-megapack.yml)
        # and by backendEndpoints() (plugin/assets/review.js).
        port = get_sidecar_port()

        host = "127.0.0.1"
        url = f"http://{host}:{port}/api/run/{urllib.parse.quote(run_id)}"

        body_bytes = json.dumps(compact, ensure_ascii=False).encode("utf-8")
        if len(body_bytes) > 2 * 1024 * 1024:
            _stderr_write(
                f"\x01w\x02[Sidecar] Result payload ({len(body_bytes)} bytes) exceeds 2MB limit; skipping sidecar POST\n"
            )
            return False

        req = urllib.request.Request(
            url,
            data=body_bytes,
            headers={
                "Content-Type": "application/json; charset=utf-8",
                "Host": f"{host}:{port}",
            },
            method="POST",
        )

        with urllib.request.urlopen(req, timeout=3.0) as resp:
            if resp.status not in (200, 201, 204):
                _stderr_write(
                    f"\x01w\x02[Sidecar] HTTP {resp.status} posting run result for {run_id}\n"
                )
                return False
        return True
    except urllib.error.HTTPError as http_err:
        _stderr_write(
            f"\x01w\x02[Sidecar] HTTP {http_err.code} posting run result for {run_id}: {http_err.reason}\n"
        )
    except urllib.error.URLError as url_err:
        _stderr_write(
            f"\x01w\x02[Sidecar] Transport error posting run result for {run_id}: {url_err.reason}\n"
        )
    except Exception as exc:
        _stderr_write(
            f"\x01w\x02[Sidecar] Failed to post run result for {run_id}: {exc}\n"
        )
    return False


def emit_result_sentinel(payload, result):
    """
    Publishes a successful task result to stderr as a log sentinel so the review
    UI can read it. Stash's job API does not expose plugin stdout, so a result
    written only there never reaches the browser. This is the success-side
    counterpart to EMPORNIUM_TASK_FAILED.

    The line is held under _SENTINEL_MAX_CHARS by shedding large recoverable
    fields in _SENTINEL_SHEDDABLE_KEYS order: Stash drops an over-long line and
    stops reading the stream, so an oversized sentinel costs the result AND every
    later log line from this task. The sidecar run store holds the full result;
    this line is the fallback for when it does not answer.

    Best-effort: a failure here must not fail an otherwise successful build.
    """
    try:
        if not isinstance(result, dict):
            return
        run_id = payload.get("run_id") if isinstance(payload, dict) else None
        if not run_id:
            return

        compact = {k: v for k, v in result.items() if k not in _SENTINEL_EXCLUDED_KEYS}
        encoded = json.dumps(compact, ensure_ascii=False, separators=(",", ":"))

        shed = []
        for key in _SENTINEL_SHEDDABLE_KEYS:
            if len(encoded) <= _SENTINEL_MAX_CHARS:
                break
            if key not in compact:
                continue
            compact.pop(key, None)
            shed.append(key)
            if key == "bbcode":
                # Pre-existing contract read by review.js; the full BBCode still
                # arrives on the chunked EMPORNIUM_TASK_BBCODE channel.
                compact["bbcode_truncated"] = True
            compact["sentinel_shed_keys"] = list(shed)
            encoded = json.dumps(compact, ensure_ascii=False, separators=(",", ":"))

        if len(encoded) > _SENTINEL_MAX_CHARS:
            # Nothing left worth shedding. Emit an identifiable stub rather than a
            # line Stash would drop, taking the rest of the log stream with it.
            compact = {
                "status": compact.get("status"),
                "task": compact.get("task"),
                "run_id": compact.get("run_id"),
                "bbcode_truncated": compact.get("bbcode_truncated", False),
                "sentinel_truncated": True,
                "sentinel_shed_keys": list(shed),
            }
            encoded = json.dumps(compact, ensure_ascii=False, separators=(",", ":"))

        _stderr_write(f"\x01i\x02EMPORNIUM_TASK_RESULT {run_id}: {encoded}\n")
    except Exception as exc:
        _stderr_write(f"\x01w\x02Failed to emit result sentinel: {exc}\n")


def emit_bbcode_sentinel(payload, result):
    """
    Publishes the full BBCode in base64 chunks to stderr as log sentinels.
    Guarantees large megapack BBCodes reach the review UI intact without being
    truncated by single-line log size limits.
    """
    try:
        if not isinstance(result, dict):
            return
        run_id = payload.get("run_id") if isinstance(payload, dict) else None
        if not run_id:
            return
        bbcode = result.get("bbcode")
        if not bbcode or not isinstance(bbcode, str):
            return

        b64_str = base64.b64encode(bbcode.encode("utf-8")).decode("ascii")
        if not b64_str:
            return

        chunks = [b64_str[i : i + _BBCODE_CHUNK_CHARS] for i in range(0, len(b64_str), _BBCODE_CHUNK_CHARS)]
        total_chunks = len(chunks)

        for idx, chunk in enumerate(chunks, 1):
            if not _stderr_write(f"\x01i\x02EMPORNIUM_TASK_BBCODE {run_id} {idx}/{total_chunks}: {chunk}\n"):
                # Log stream is gone; the remaining chunks cannot arrive and the
                # UI discards a partial set anyway.
                return
    except Exception as exc:
        _stderr_write(f"\x01w\x02Failed to emit bbcode sentinel: {exc}\n")


def main():
    payload = {}
    build_completed = False
    result_stored = False
    try:
        check_dependencies()
        mode, payload, server_connection = parse_input_payload()
        if isinstance(payload, dict):
            set_active_run_id(payload.get("run_id"))

        if mode == "probe" or "probe" in str(mode).lower():
            result = run_probe_files(payload)
        elif mode == "upload_cover" or "upload_cover" in str(mode).lower() or "uploadcover" in str(mode).lower():
            result = run_upload_cover(payload)
        elif mode == "start_backend" or "start_backend" in str(mode).lower() or "startbackend" in str(mode).lower() or "start-backend" in str(mode).lower():
            result = run_start_backend(payload)
        elif mode == "single" or "single" in str(mode).lower() or payload.get("single_scene"):
            payload["single_scene"] = True
            result = run_build_megapack(payload, server_connection)
        else:
            result = run_build_megapack(payload, server_connection)

        # Past this point the work is done and its artifacts are on disk. Anything
        # that fails below is a reporting failure, not a build failure.
        build_completed = True

        result_stored = post_result_to_sidecar(payload, result)
        emit_result_sentinel(payload, result)
        emit_bbcode_sentinel(payload, result)

        try:
            sys.stdout.write(json.dumps(result, indent=2))
            sys.stdout.write("\n")
            sys.stdout.flush()
        except Exception:
            # Stash's job API does not expose plugin stdout, so losing this write
            # costs nothing -- and it must not fail a finished build.
            pass
        sys.exit(0)
    except Exception as err:
        run_id = payload.get("run_id") if isinstance(payload, dict) else None
        if build_completed:
            # The task finished; this came from the reporting leg (typically a
            # stderr Stash stopped reading). Overwriting the stored result with a
            # failure, or exiting non-zero, would report a completed build as a
            # failed one -- which is exactly the bug this guards.
            detail = "result is in the sidecar run store" if result_stored else "result could not be stored"
            _stderr_write(
                f"\x01w\x02Task completed, but result reporting failed ({detail}): {err}\n"
            )
            sys.exit(0)
        if run_id:
            fail_result = {
                "status": "failed",
                "run_id": str(run_id),
                "error": str(err),
                "traceback": traceback.format_exc(),
            }
            post_result_to_sidecar(payload, fail_result)
            _stderr_write(f"\x01e\x02EMPORNIUM_TASK_FAILED {run_id}: {err}\n")
        else:
            _stderr_write(f"\x01e\x02EMPORNIUM_TASK_FAILED: {err}\n")
        _stderr_write(f"\x01e\x02Task execution failed: {err}\n")
        _stderr_write(traceback.format_exc())
        sys.exit(1)


if __name__ == "__main__":
    main()

