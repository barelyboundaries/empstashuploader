"""Stage 5: torrent generation with the verified helper piece-size policy.

The piece-size policy mirrors the reference stash-empornium helper exactly:
piece exponent = min(floor(log2(total_bytes / 1024)), 23), i.e. 1 MiB per
GiB of payload, capped at 2^23 (8 MiB). Floor is clamped to 2^14 (16 KiB).

The announce URL is sensitive: it is written into the private torrent (which
is the only place it may exist) and never into logs, manifests, BBCode, or
API responses. ``sanitize_announce_url`` exists for logging safety.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Callable, Any, Sequence
from urllib.parse import urlparse, urlunparse

import torf

MAX_PIECE_EXPONENT = 23  # 2^23 = 8 MiB
MIN_PIECE_SIZE = 2**14  # 16 KiB
CREATED_BY = "DeepSeek Megapack"


class TorrentError(Exception):
    """Torrent generation or validation failed; pack generation must stop."""


def piece_size_for(total_bytes: int) -> int:
    """Piece size for a payload, matching the reference helper's policy."""
    if total_bytes <= 0:
        return MIN_PIECE_SIZE
    exponent = int(math.log(max(total_bytes, 1) / 1024, 2))
    exponent = min(max(exponent, 14), MAX_PIECE_EXPONENT)
    return 2**exponent


calculate_piece_size = piece_size_for


def source_for_announce(announce_url: str) -> str:
    """Derive the torrent 'source' tag, matching the reference helper exactly."""
    host = (urlparse(announce_url).hostname or "").lower()
    known = {
        "empornium": "Emp",
        "enthralled": "Ent",
        "femdomcult": "FDC",
        "happyfappy": "HF",
        "kufirc": "Kufirc",
        "pornbay": "PBay",
    }
    for fragment, source in known.items():
        if fragment in host:
            return source
    return host or ""


def validate_announce_url(announce_url: str) -> None:
    """Validate the announce URL shape. Raises TorrentError on failure.

    Confirmed Empornium format (July–Aug 2026, 20 inspected torrents):
    ``http://tracker.empornium.sx:2710/<token>/<token>/announce`` — plain
    HTTP, port-based, path-carried tokens, no query parameter. HTTPS and
    ``?passkey=`` remain accepted for compatibility, neither is required.
    """
    parsed = urlparse(announce_url)
    if parsed.scheme not in ("http", "https"):
        raise TorrentError("Announce URL must use http or https.")
    if not parsed.hostname:
        raise TorrentError("Announce URL has no host.")
    if "announce" not in (parsed.path or ""):
        raise TorrentError("Announce URL path does not look like an announce endpoint.")


def _masked_path(path: str) -> str:
    """Mask path-carried announce tokens (``/announce`` with 1+ leading segments)."""
    segments = [s for s in path.split("/") if s]
    if len(segments) >= 2 and segments[-1] == "announce":
        return "/" + "/".join(["x" * len(s) for s in segments[:-1]] + ["announce"])
    return path


def sanitize_announce_url(announce_url: str) -> str:
    """Mask passkey query and path tokens for safe logging (never log in place)."""
    parsed = urlparse(announce_url)
    fixed_query = "&".join(
        "passkey=" + "x" * 32 if part.startswith("passkey=") else part
        for part in parsed.query.split("&") if part
    )
    if not fixed_query:
        fixed_query = "passkey=" + "x" * 32
    return urlunparse((parsed.scheme, parsed.netloc, _masked_path(parsed.path), "", fixed_query, ""))


def _fnmatch_escape(text: str) -> str:
    """Escape fnmatch special characters so a glob matches ``text`` literally.

    torf matches exclude globs with ``fnmatch.fnmatch`` (case-insensitive, with
    ``os.path.normcase`` applied to both sides). Wrapping each special character
    in a single-character class makes the glob match that literal character
    (``[`` -> ``[[]``, a class containing ``[``) instead of opening a character
    class or range. ``!`` and spaces have no fnmatch meaning outside classes
    and need no escaping.
    """
    return "".join(f"[{c}]" if c in "*?[]" else c for c in text)


def _exact_exclude_globs(payload_name: str, relative_paths: Sequence[str]) -> list[str]:
    """Escaped exclude globs matching exactly ``relative_paths`` under the payload.

    Empirically pinned against torf 4.3.1 (backend/tests/test_torrents.py):

    - Globs are matched with ``fnmatch.fnmatch`` — case-insensitive, with
      ``os.path.normcase`` applied to BOTH sides, so forward-slash globs match
      backslash paths on Windows and case differences never matter.
    - The matched string is ``<payload_dir_basename>/<relpath>``: the payload
      dir basename, NOT the ``name=`` override (verified: with ``name="Custom"``
      the payload-dir-prefixed glob still matches, the name-prefixed one does
      not).
    - When every file under the payload shares one deeper common subdirectory,
      torf's internal ``commonpath`` sinks below the payload dir and the
      matched string gains exactly ONE extra ``<name>/`` prefix
      (``<name>/<name>/<rel>``). Both prefix forms are therefore emitted; the
      doubled form matches nothing in normal layouts (a file would have to sit
      under a directory named exactly like the payload dir) and fixes the
      all-files-in-one-subdirectory shape.
    - ``*``, ``?``, ``[`` and ``]`` in path components are escaped via
      character classes so they match literally; ``!`` and spaces need none.
    """
    prefix = _fnmatch_escape(Path(payload_name).name)
    globs: list[str] = []
    for rel in relative_paths:
        parts = [p for p in str(rel).replace("\\", "/").split("/") if p and p not in (".", "..")]
        if not parts:
            continue
        escaped = "/".join(_fnmatch_escape(part) for part in parts)
        globs.append(f"{prefix}/{escaped}")
        # torf quirk: when every payload file shares one deeper common
        # subdirectory, the matched string is ``<name>/<name>/<rel>``.
        globs.append(f"{prefix}/{prefix}/{escaped}")
    return globs


def create_torrent(
    payload_dir: str | Path,
    announce_url: str | None = None,
    out_path: str | Path | None = None,
    *,
    source: str | None = None,
    piece_size: int | None = None,
    created_by: str = CREATED_BY,
    expected_bytes: int | None = None,
    callback: Callable[[torf.Torrent, str, int, int], None] | None = None,
    trackers: list[str] | None = None,
    web_seeds: list[str] | None = None,
    private: bool | None = None,
    comment: str | None = None,
    name: str | None = None,
    exclude_globs: list[str] | tuple[str, ...] | None = None,
    include_globs: list[str] | tuple[str, ...] | None = None,
    exclude_exact: Sequence[str] | None = None,
) -> dict:
    """Create a torrent for ``payload_dir``. Returns torrent metadata.

    The announce URL is written into the torrent and is never returned or
    logged. ``expected_bytes`` (the separately computed payload size) is
    cross-checked against the torrent's own total. Live hashing progress
    is streamed via ``callback(torrent, filepath, pieces_done, total_pieces)``.

    ``exclude_exact`` lists relative paths (forward or backslash separated)
    to exclude from the payload by EXACT match — glob special characters are
    escaped internally, so a path containing ``[ ] * ?`` is excluded by its
    literal name only. Only meaningful for directory payloads.
    """
    payload = Path(payload_dir)
    if not payload.exists():
        raise TorrentError(f"Payload path not found: {payload}")

    tracker_list: list[str] | None = None
    if announce_url:
        validate_announce_url(announce_url)
        tracker_list = [announce_url]
    elif trackers:
        tracker_list = list(trackers)

    is_private = private if private is not None else bool(tracker_list)
    torrent_source = source
    if not torrent_source and tracker_list:
        torrent_source = source_for_announce(tracker_list[0])

    all_exclude_globs = list(exclude_globs or ())
    if exclude_exact:
        all_exclude_globs.extend(_exact_exclude_globs(payload.name, exclude_exact))

    torrent = torf.Torrent(
        path=str(payload),
        name=name,
        exclude_globs=all_exclude_globs,
        include_globs=include_globs or (),
        trackers=tracker_list,
        webseeds=web_seeds,
        private=is_private,
        source=torrent_source,
        created_by=created_by,
        comment=comment,
        creation_date=None,
    )

    if piece_size is not None:
        if piece_size < MIN_PIECE_SIZE:
            raise TorrentError(f"Piece size {piece_size} is below the 16 KiB minimum.")
        torrent.piece_size = piece_size
    else:
        torrent.piece_size = piece_size_for(torrent.size)

    try:
        if callback is not None:
            torrent.generate(callback=callback)
        else:
            torrent.generate()
    except Exception as exc:  # torf raises TorrentError and relatives
        raise TorrentError(f"Torrent generation failed: {exc}") from exc

    if is_private and not torrent.private:
        raise TorrentError("Torrent was not marked private.")
    if expected_bytes is not None and torrent.size != expected_bytes:
        raise TorrentError(
            f"Torrent size {torrent.size} does not match staged payload {expected_bytes}."
        )

    if out_path is not None:
        out = Path(out_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        try:
            torrent.write(out, overwrite=True)
        except Exception as exc:
            raise TorrentError(f"Torrent write failed: {exc}") from exc

    return {
        "infohash": str(torrent.infohash),
        "name": torrent.name,
        "total_bytes": torrent.size,
        "piece_size": torrent.piece_size,
        "piece_count": torrent.pieces,
        "file_count": len(torrent.files),
    }