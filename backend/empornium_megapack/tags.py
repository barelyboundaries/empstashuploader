"""Empornium tag vocabulary resolution and source typing.

Empornium publishes no unauthenticated tag API: tags live behind login and
this plugin holds no site session. The vocabulary is a curated, vendored
mapping asset in data/emp_tags.toml.

Performer names, studio names, and derived technical tags bypass the
vocabulary gate because performers cannot be bounded and derived tags are
generated in Empornium shape. Only scene tags are filtered against the
vocabulary (ignored blocklist or mapped whitelist). Unmapped scene tags are
dropped and surfaced to the user.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Iterable, Literal

MAX_TAGS = 60


def empify(tag: str) -> str:
    """Normalize a tag string to Empornium tracker tag convention.

    Strips non-word/space/dot/dash characters, lowercases, replaces separator runs
    with single dots, strips leading/trailing dots, and truncates to 32 chars.
    """
    cleaned = re.sub(r"[^\w\s._-]", "", tag).lower()
    cleaned = re.sub(r"[\s._-]+", ".", cleaned)
    return cleaned.strip(".")[:32]


@dataclass(frozen=True)
class TagSource:
    """A typed source of tag data for a scene."""

    value: str
    kind: Literal["scene_tag", "performer", "studio", "derived"]


@dataclass
class ResolvedTags:
    """Outcome of resolving tag sources against Empornium's vocabulary."""

    tags: list[str]  # what goes to the tracker
    unmapped: list[str]  # original Stash tag names, dropped — surfaced to the user
    ignored: list[str]  # original names matched by the blocklist — dropped silently


@dataclass(frozen=True)
class Vocabulary:
    """Curated Empornium tag mapping and blocklist."""

    map: dict[str, list[str]]
    ignored: frozenset[str]

    def __getitem__(self, item: str):
        if item == "map":
            return self.map
        if item == "ignored":
            return self.ignored
        raise KeyError(item)

    def __contains__(self, item: str) -> bool:
        return item in ("map", "ignored")


class TagVocabularyError(RuntimeError):
    """Base error for tag vocabulary operations."""


class TagVocabularyNotFoundError(TagVocabularyError, FileNotFoundError):
    """Raised when emp_tags.toml cannot be located on disk."""


class TagVocabularyCorruptError(TagVocabularyError, ValueError):
    """Raised when emp_tags.toml is malformed or missing required sections."""


def find_vocabulary_file() -> Path:
    """Locate the emp_tags.toml vocabulary data file.

    Search order: package data directory first (standard wheel / editable /
    vendored layout), then sibling and repo candidate paths. If the file
    cannot be found, raises TagVocabularyNotFoundError; never silently falls
    back to unfiltered tags.
    """
    pkg_dir = Path(__file__).resolve().parent
    candidates = [
        pkg_dir / "data" / "emp_tags.toml",
        pkg_dir.parent / "data" / "emp_tags.toml",
        pkg_dir.parent.parent / "backend" / "empornium_megapack" / "data" / "emp_tags.toml",
        Path.home() / ".stash" / "plugins" / "empornium-megapack" / "empornium_megapack" / "data" / "emp_tags.toml",
        Path.home() / ".stash" / "plugins" / "empornium-megapack" / "data" / "emp_tags.toml",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise TagVocabularyNotFoundError(
        f"Empornium tag vocabulary data file (emp_tags.toml) not found. "
        f"Checked candidates: {[str(c) for c in candidates]}"
    )


@lru_cache(maxsize=4)
def load_vocabulary(path: Path | str | None = None) -> Vocabulary:
    """Load and parse the Empornium tag vocabulary from emp_tags.toml.

    Returns a cached Vocabulary object containing:
    - map: dict[str_lower, list[str]] (Stash tag -> list of Empornium tag tokens)
    - ignored: frozenset[str_lower] (Stash housekeeping tags to drop silently)

    Raises TagVocabularyNotFoundError if the file does not exist, or
    TagVocabularyCorruptError if it cannot be parsed or lacks required sections.
    """
    target_path = Path(path) if path is not None else find_vocabulary_file()
    if not target_path.is_file():
        raise TagVocabularyNotFoundError(f"Tag vocabulary file not found: {target_path}")

    try:
        with target_path.open("rb") as fh:
            data = tomllib.load(fh)
    except Exception as err:
        raise TagVocabularyCorruptError(
            f"Failed to parse vocabulary TOML at {target_path}: {err}"
        ) from err

    if not isinstance(data, dict):
        raise TagVocabularyCorruptError(f"Vocabulary TOML root must be a table, got {type(data)}")

    ignored_section = data.get("ignored")
    if not isinstance(ignored_section, dict) or "tags" not in ignored_section:
        raise TagVocabularyCorruptError("Vocabulary TOML missing [ignored].tags table/key")

    raw_ignored = ignored_section.get("tags")
    if not isinstance(raw_ignored, list):
        raise TagVocabularyCorruptError("[ignored].tags must be a list of strings")

    ignored_set = frozenset(str(t).strip().lower() for t in raw_ignored if str(t).strip())

    map_section = data.get("map")
    if not isinstance(map_section, dict):
        raise TagVocabularyCorruptError("Vocabulary TOML missing [map] section")

    vocab_map: dict[str, list[str]] = {}
    for k, v in map_section.items():
        key_norm = str(k).strip().lower()
        if not key_norm:
            continue
        tokens = [tok.strip() for tok in str(v).split() if tok.strip()]
        vocab_map[key_norm] = tokens

    return Vocabulary(map=vocab_map, ignored=ignored_set)


def resolve_tags(
    sources: Iterable[TagSource],
    vocab: Vocabulary | dict | None = None,
) -> ResolvedTags:
    """Resolve typed tag sources against the Empornium vocabulary.

    Resolution order per source:
    1. kind in ("performer", "studio", "derived"):
       empify(value), keep. Never gated by vocabulary.
    2. kind == "scene_tag":
       a. case-insensitive hit in ignored -> drop, append original to ignored
       b. case-insensitive hit in map -> split the RHS on whitespace, add every token
       c. no hit -> drop, append original to unmapped

    Tracker tags are deduped, sorted, and capped at MAX_TAGS (60).
    Unmapped and ignored lists preserve unique original names in encounter order.
    """
    if vocab is None:
        vocab = load_vocabulary()

    vocab_map = vocab.map if hasattr(vocab, "map") else vocab.get("map", {})
    vocab_ignored = vocab.ignored if hasattr(vocab, "ignored") else vocab.get("ignored", frozenset())
    if not isinstance(vocab_ignored, (set, frozenset)):
        vocab_ignored = frozenset(str(x).lower().strip() for x in vocab_ignored)

    tags: list[str] = []
    unmapped: list[str] = []
    ignored: list[str] = []

    for src in sources:
        val = src.value.strip() if src.value else ""
        if not val:
            continue

        if src.kind in ("performer", "studio", "derived"):
            emp = empify(val)
            if emp:
                tags.append(emp)
        elif src.kind == "scene_tag":
            lower_val = val.lower()
            if lower_val in vocab_ignored:
                ignored.append(src.value)
            elif lower_val in vocab_map:
                tokens = vocab_map[lower_val]
                if isinstance(tokens, str):
                    tokens = [tok.strip() for tok in tokens.split() if tok.strip()]
                tags.extend(tokens)
            else:
                unmapped.append(src.value)
        else:
            unmapped.append(src.value)

    unique_tags = sorted(set(tags))[:MAX_TAGS]
    unique_unmapped = list(dict.fromkeys(unmapped))
    unique_ignored = list(dict.fromkeys(ignored))

    return ResolvedTags(
        tags=unique_tags,
        unmapped=unique_unmapped,
        ignored=unique_ignored,
    )
