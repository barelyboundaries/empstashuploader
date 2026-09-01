"""Path mapping for Docker / path-mapped Stash libraries.

Stash may report media paths as Stash sees them (e.g. ``/media/...`` inside
a container), while the backend accesses the same media via host-native
paths (``D:\\...``). ``path_mappings`` pairs a remote prefix (Stash's root)
with the local prefix that corresponds to it::

    path_mappings = [["/media", "D:\\Media"]]

Mapping is applied at probe time so every downstream consumer (review,
images, staging) operates on the local, accessable path. When a mapping
applies and the mapped file exists, its size is cross-checked against the
size Stash reported so a moved/replaced file that happens to match the
prefix is rejected (same-file guarantee) rather than silently linked or
copied into a pack.
"""

from __future__ import annotations

import os
from pathlib import Path

from .config import Settings, get_settings

OSHASH_CHUNK = 64 * 1024


def oshash_file(path: str | Path) -> str:
    """Stash oshash — OpenSubtitles checksum over the first+last 64 KiB.

    Mirrors stash v0.31 ``pkg/hash/oshash`` exactly: little-endian u64 sum
    of head + tail + file size, hex-formatted to 16 digits. O(128 KiB) reads.
    """
    size = os.path.getsize(path)
    if size <= 8:
        raise ValueError("cannot calculate oshash for files of 8 bytes or fewer")
    chunk = min(OSHASH_CHUNK, (size // 8) * 8)
    with open(path, "rb") as fh:
        head = fh.read(chunk)
        fh.seek(-chunk, os.SEEK_END)
        tail = fh.read(chunk)

    def le_sum(data: bytes) -> int:
        return sum(int.from_bytes(data[i : i + 8], "little") for i in range(0, len(data), 8))

    result = (le_sum(head) + le_sum(tail) + size) & 0xFFFFFFFFFFFFFFFF
    return f"{result:016x}"


def _key(raw: str) -> tuple:
    """Separator- and case-normalized absolute path segments."""
    normalized = str(Path(raw)).replace("\\", "/")
    return tuple(segment.lower() for segment in normalized.split("/") if segment)


class PathMapper:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self._mappings: list[tuple[tuple, tuple, str]] = []
        for pair in self.settings.path_mappings:
            if not isinstance(pair, (list, tuple)) or len(pair) != 2:
                continue
            remote, local = pair[0], pair[1]
            if not remote or not local:
                continue
            self._mappings.append((_key(remote), _key(local), str(local)))

    def is_mapped(self, path: str | Path) -> bool:
        return self._mapping_for(_key(str(path))) is not None

    def _mapping_for(self, key: tuple) -> tuple | None:
        for remote, local, local_prefix in self._mappings:
            if key == remote or (len(key) > len(remote) and key[: len(remote)] == remote):
                return (remote, local, local_prefix)
        return None

    def apply(self, path: str | Path) -> str:
        """Map a Stash-provided path to its local form (passthrough otherwise)."""
        raw = str(path)
        norm = raw.replace("\\", "/")
        norm_lower = norm.lower()
        for remote, local, local_prefix in self._mappings:
            remote_norm = "/" + "/".join(remote)
            if norm_lower == remote_norm or norm_lower.startswith(remote_norm + "/"):
                return local_prefix + norm[len(remote_norm):]
        return raw

    def resolve(self, path: str | Path) -> str:
        """Mapped local path with the original Stash casing of the tail preserved."""
        return self.apply(path)


def verify_same_file(local_path: str, expected_size: int | None, mapped: bool, expected_oshash: str | None = None) -> bool:
    """True when the local file matches the file Stash indexed.

    When a path mapping was applied the local file must match Stash's
    record: the size (and, when Stash provided it, the oshash fingerprint)
    must be identical. Anything else means the mapped prefix currently
    points at different content.
    """
    if not mapped:
        return True
    if expected_size is None:
        return True
    try:
        if os.path.getsize(local_path) != expected_size:
            return False
    except OSError:
        return False
    if expected_oshash:
        try:
            return oshash_file(local_path) == expected_oshash
        except (OSError, ValueError):
            return False  # fail closed: cannot confirm identity
    return True