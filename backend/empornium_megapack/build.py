"""Stage 5: pack staging, torrent layout, and retention.

Layout (originals are never modified; hardlinks when possible):

    <staging>/packs/<pack_id>/<sanitized title>/
        scene-a.mp4
        scene-b.mp4
        Contact Sheets/
            scene-a.jpg
            scene-b.jpg

Torrent/artifact files land in the output dir. Staging is retained until an
explicit cleanup call after seeding.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from .config import Settings

PACK_ROOT_NAME = "packs"
CONTACT_SHEETS_DIR = "Contact Sheets"

_RESERVED_WINDOWS = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}
_INVALID_CHARS_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


class BuildError(Exception):
    """Pack staging/building failed; generation must stop."""


def sanitize_name(name: str, max_len: int = 120) -> str:
    """Filesystem-safe name: valid chars only, trimmed, reserved-name proof."""
    if not isinstance(name, str):
        name = str(name or "")
    cleaned = _INVALID_CHARS_RE.sub("_", name).strip(" .")
    cleaned = re.sub(r"\s+", " ", cleaned)
    if not cleaned:
        cleaned = "Untitled"
    if cleaned.upper() in _RESERVED_WINDOWS:
        cleaned = "_" + cleaned
    if len(cleaned) > max_len:
        head, _, ext = cleaned.rpartition(".")
        if ext and len(ext) <= 16:
            cleaned = head[: max_len - 1 - len(ext)] + "." + ext
        else:
            cleaned = cleaned[:max_len]
    return cleaned.strip(" .") or "Untitled"


def unique_names(names: list[str]) -> list[str]:
    """Deduplicate names case-insensitively, preserving order."""
    seen: dict[str, int] = {}
    result: list[str] = []
    for name in names:
        key = name.lower()
        if key not in seen:
            seen[key] = 1
            result.append(name)
            continue
        seen[key] += 1
        candidate = f"{name} ({seen[key]})"
        while candidate.lower() in seen:
            seen[key] += 1
            candidate = f"{name} ({seen[key]})"
        seen[candidate.lower()] = 1
        result.append(candidate)
    return result


def _same_volume(source: Path, staging: Path) -> bool:
    try:
        return os.stat(source).st_dev == os.stat(staging).st_dev
    except OSError:
        return False


def _link_or_copy(source: Path, dest: Path, prefer_hardlink: bool) -> bool:
    """Hardlink when allowed, else copy. Returns linked flag.

    Hardlinks never get their mode changed: the inode is shared with the
    original, so chmod would mutate the source file.
    """
    if not source.is_file():
        raise BuildError(f"Source file missing during staging: {source}")
    if prefer_hardlink:
        try:
            os.link(source, dest)
            return True
        except OSError:
            pass
    shutil.copy2(source, dest)
    os.chmod(dest, 0o666)
    return False


class StagedScene:
    __slots__ = ("scene_id", "title", "video_name", "sheet_name", "linked", "staged_path")

    def __init__(self, scene_id: str, title: str, video_name: str, sheet_name: str, linked: bool, staged_path: Path):
        self.scene_id = scene_id
        self.title = title
        self.video_name = video_name
        self.sheet_name = sheet_name
        self.linked = linked
        self.staged_path = staged_path


_MEDIA_EXTS = (".mp4", ".mkv", ".avi", ".mov", ".webm", ".m4v", ".wmv", ".flv")


def _title_stem(title: str) -> str:
    """Title without a trailing media extension (untitled scenes fall back to
    the basename, which already carries the extension)."""
    name = sanitize_name(title)
    for ext in _MEDIA_EXTS:
        if name.lower().endswith(ext):
            return name[: -len(ext)]
    return name


def stage_payload(
    pack_root: Path,
    title: str,
    scenes: list[dict],
    settings: Settings,
    http_download=None,
) -> list[StagedScene]:
    """Stage scene videos + contact sheets under the pack title dir.

    ``scenes``: ordered list of dicts with keys scene_id, title, source_path
    (or None for download mode), sheet_path, fetch_mode ('copy'|'download'),
    expected_size. Returns one StagedScene per input scene.
    """
    root = pack_root / sanitize_name(title)
    root.mkdir(parents=True, exist_ok=True)
    sheets_dir = root / CONTACT_SHEETS_DIR
    sheets_dir.mkdir(parents=True, exist_ok=True)

    first_source = next((Path(s["source_path"]).parent for s in scenes if s.get("source_path")), None)
    prefer_link = first_source is not None and _same_volume(first_source, pack_root)

    video_names = unique_names([_title_stem(s["title"]) + ".mp4" for s in scenes])
    sheet_names = unique_names([_title_stem(s["title"]) + ".jpg" for s in scenes])

    staged: list[StagedScene] = []
    for idx, scene in enumerate(scenes):
        dest_video = root / video_names[idx]
        source = Path(scene["source_path"]) if scene.get("source_path") else None
        linked = False
        if scene["fetch_mode"] == "download" or source is None or not source.is_file():
            if http_download is None:
                raise BuildError(f"Scene {scene['scene_id']} needs an HTTP download but none configured.")
            http_download(
                scene_id=scene["scene_id"],
                dest=dest_video,
                expected=scene["expected_size"],
            )
        else:
            linked = _link_or_copy(source, dest_video, prefer_link)
        sheet_src = Path(scene["sheet_path"])
        shutil.copy2(sheet_src, sheets_dir / sheet_names[idx])
        os.chmod(sheets_dir / sheet_names[idx], 0o666)
        staged.append(
            StagedScene(
                scene_id=scene["scene_id"],
                title=scene["title"],
                video_name=video_names[idx],
                sheet_name=sheet_names[idx],
                linked=linked,
                staged_path=dest_video,
            )
        )
    return staged


def payload_size(pack_root: Path) -> int:
    return sum(f.stat().st_size for f in pack_root.rglob("*") if f.is_file())


def write_manifest(manifest_path: str | Path, data: dict) -> Path:
    out = Path(manifest_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return out


def make_bundle(pack_root: Path, bundle_path: Path, extras: list[tuple[Path, str]] | None = None) -> None:
    """Zip staged media + contact sheets; ``extras`` (built artifacts) go at the root."""
    bundle_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(bundle_path, "w", compression=zipfile.ZIP_STORED) as zf:
        for file in sorted(pack_root.rglob("*")):
            if file.is_file():
                zf.write(file, arcname=file.relative_to(pack_root.parent))
        for artifact, arcname in extras or []:
            if artifact.is_file():
                zf.write(artifact, arcname=arcname)


def new_pack_id() -> str:
    return uuid.uuid4().hex[:10]


def build_created_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def verify_preflight_checklist(
    torrent_path: str | Path,
    submission_path: str | Path | None = None,
    output_dir: str | Path | None = None,
    payload_root: str | Path | None = None,
    pack_title: str | None = None,
    submission_data: dict | None = None,
    presentation_bytes: int | None = None,
) -> dict:
    """
    R4 (6d): Pre-flight checklist verification of generated artifacts on disk.
    Reads artifacts back directly from disk with torf (never trusting unverified in-memory state).
    """
    import torf

    checks = []
    t_path = Path(torrent_path)
    out_dir = Path(output_dir) if output_dir else t_path.parent

    # 1. Submission artifact inspection (preview_only & remote image URLs)
    sub = submission_data
    if sub is None and submission_path and Path(submission_path).exists():
        try:
            sub = json.loads(Path(submission_path).read_text(encoding="utf-8"))
        except Exception:
            sub = {}

    sub = sub or {}
    preview_only = sub.get("preview_only", True)
    image_urls = sub.get("image_urls", [])

    all_remote = bool(image_urls) and all(
        isinstance(u, str) and (u.startswith("http://") or u.startswith("https://"))
        for u in image_urls
    )
    if not preview_only and all_remote:
        checks.append({
            "id": "images_remote",
            "label": "Preview Images",
            "passed": True,
            "detail": f"All {len(image_urls)} preview image(s) hosted remotely",
        })
    else:
        local_count = sum(1 for u in image_urls if isinstance(u, str) and u.startswith("file:///"))
        checks.append({
            "id": "images_remote",
            "label": "Preview Images",
            "passed": False,
            "detail": f"Contains {local_count or len(image_urls)} local file:/// preview URL(s). Remote hosting required.",
        })

    measured_presentation = presentation_bytes if presentation_bytes is not None else sub.get("presentation_bytes")
    if measured_presentation is not None:
        from empornium_megapack.config import get_settings
        cap = get_settings().presentation_max_bytes
        total = measured_presentation
        checks.append({
            "id": "presentation_size",
            "label": "Presentation Size",
            "passed": total <= cap,
            "detail": f"{total/1048576:.2f} MiB of {cap/1048576:.2f} MiB budget (Empornium cap 25.00 MiB)",
        })

    # 2. Tracker tags
    tracker_tags = sub.get("tracker_tags", [])
    if isinstance(tracker_tags, list) and len(tracker_tags) > 0:
        checks.append({
            "id": "tracker_tags",
            "label": "Tracker Tags",
            "passed": True,
            "detail": f"{len(tracker_tags)} valid tracker tags generated",
        })
    else:
        checks.append({
            "id": "tracker_tags",
            "label": "Tracker Tags",
            "passed": False,
            "detail": "Tracker tags list is empty",
        })

    # 3. Category static reminder (6b)
    checks.append({
        "id": "category",
        "label": "Category",
        "passed": True,
        "is_info": True,
        "detail": "Category — you select this on the upload form.",
    })

    # 4. Torrent artifact validation with torf
    torrent_obj = None
    if not t_path.exists():
        checks.append({
            "id": "torrent_valid",
            "label": "Torrent File (torf)",
            "passed": False,
            "detail": f".torrent file not found at '{t_path}'",
        })
    else:
        try:
            torrent_obj = torf.Torrent.read(str(t_path))
            pieces_ok = torrent_obj.pieces > 0 and bool(torrent_obj.metainfo.get("info", {}).get("pieces"))
            private_ok = torrent_obj.private is True
            source_ok = bool(torrent_obj.source)

            if pieces_ok and private_ok and source_ok:
                checks.append({
                    "id": "torrent_valid",
                    "label": "Torrent File (torf)",
                    "passed": True,
                    "detail": f"private=True, source={torrent_obj.source}, {torrent_obj.pieces} piece(s) verified",
                })
            else:
                issues = []
                if not private_ok:
                    issues.append("private flag is False")
                if not pieces_ok:
                    issues.append("pieces is empty")
                if not source_ok:
                    issues.append("source tag missing")
                checks.append({
                    "id": "torrent_valid",
                    "label": "Torrent File (torf)",
                    "passed": False,
                    "detail": f"Torrent validation failed: {', '.join(issues)}",
                })
        except Exception as exc:
            checks.append({
                "id": "torrent_valid",
                "label": "Torrent File (torf)",
                "passed": False,
                "detail": f"Failed to parse .torrent: {exc}",
            })

    # 5. Media files on-disk exact path verification
    if torrent_obj is not None:
        try:
            missing_files = []
            is_single_file = bool(
                torrent_obj.metainfo
                and isinstance(torrent_obj.metainfo.get("info"), dict)
                and "length" in torrent_obj.metainfo["info"]
            )
            for tf in torrent_obj.files:
                if is_single_file:
                    if payload_root is not None:
                        p_root = Path(payload_root)
                        if p_root.is_file() or p_root.name.casefold() == Path(tf).name.casefold():
                            tf_path = p_root
                        else:
                            tf_path = p_root / tf
                    else:
                        tf_path = out_dir / tf
                        if not tf_path.exists() and out_dir.is_file() and out_dir.name.casefold() == Path(tf).name.casefold():
                            tf_path = out_dir
                else:
                    base_dir = Path(payload_root) if payload_root is not None else out_dir
                    tf_path = base_dir / tf
                    if not tf_path.exists():
                        # Handle multi-file torrents where tf starts with root name
                        try:
                            rel = tf.relative_to(torrent_obj.name)
                            tf_path = base_dir / rel
                        except Exception:
                            tf_path = base_dir.parent / tf
                if not tf_path.exists():
                    missing_files.append(str(tf))
            if not missing_files:
                checks.append({
                    "id": "payload_files",
                    "label": "Media Files Verification",
                    "passed": True,
                    "detail": f"All {len(torrent_obj.files)} payload file(s) exist on disk",
                })
            else:
                checks.append({
                    "id": "payload_files",
                    "label": "Media Files Verification",
                    "passed": False,
                    "detail": f"Missing {len(missing_files)} file(s) on disk: {', '.join(missing_files[:3])}",
                })
        except Exception as exc:
            checks.append({
                "id": "payload_files",
                "label": "Media Files Verification",
                "passed": False,
                "detail": f"Verification error: {exc}",
            })
    else:
        checks.append({
            "id": "payload_files",
            "label": "Media Files Verification",
            "passed": False,
            "detail": "Cannot verify files without valid .torrent",
        })

    # 6. Torrent root name vs pack title match (warning only, do not block)
    if torrent_obj is not None:
        is_single_file = bool(
            torrent_obj.metainfo
            and isinstance(torrent_obj.metainfo.get("info"), dict)
            and "length" in torrent_obj.metainfo["info"]
        )
        if is_single_file:
            checks.append({
                "id": "root_name",
                "label": "Torrent Root Name",
                "passed": True,
                "is_warning": False,
                "detail": f"Single-file torrent — tracker displays the filename '{torrent_obj.name}'",
            })
        elif pack_title:
            clean_title = sanitize_name(pack_title)
            if torrent_obj.name == pack_title or torrent_obj.name == clean_title:
                checks.append({
                    "id": "root_name",
                    "label": "Torrent Root Name",
                    "passed": True,
                    "is_warning": False,
                    "detail": f"Root folder matches pack title ('{torrent_obj.name}')",
                })
            else:
                checks.append({
                    "id": "root_name",
                    "label": "Torrent Root Name",
                    "passed": True,
                    "is_warning": True,
                    "detail": f"Torrent root folder '{torrent_obj.name}' differs from pack title '{pack_title}' (folder name is what tracker will display)",
                })

    all_passed = all(
        c["passed"] for c in checks
        if not c.get("is_warning") and not c.get("is_info")
    )

    return {
        "ready": all_passed,
        "checks": checks,
    }