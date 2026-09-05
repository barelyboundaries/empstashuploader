"""
Tests for Stage 4c: Metadata & BBCode Engine Composition.
Verifies integration of resolution_for, format_duration, join_names,
pack_performer_union, pack_studio, and empify into the emitted BBCode text.
"""

import json
import os
import sys
from pathlib import Path
import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
BACKEND_DIR = PROJECT_ROOT / "backend"
PLUGIN_DIR = PROJECT_ROOT / "plugin"

if str(BACKEND_DIR) in sys.path:
    sys.path.remove(str(BACKEND_DIR))
sys.path.insert(0, str(BACKEND_DIR))

if str(PLUGIN_DIR) not in sys.path:
    sys.path.append(str(PLUGIN_DIR))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

import task
from empornium_megapack.metadata import (
    resolution_for,
    format_duration,
    join_names,
    pack_performer_union,
    pack_studio,
    empify,
)


def test_stage4c_resolution_and_duration_badges(tmp_path):
    """Resolution and duration badges appear on itemized scenes in BBCode."""
    out_dir = tmp_path / "stage4c_res_dur_out"
    out_dir.mkdir()
    pack_title = "Resolution Duration Megapack"
    pack_dir = out_dir / pack_title
    pack_dir.mkdir()

    f1 = pack_dir / "s1.mp4"
    f2 = pack_dir / "s2.mp4"
    f1.write_bytes(b"\x00" * 1024)
    f2.write_bytes(b"\x00" * 1024)

    payload = {
        "pack_title": pack_title,
        "output_dir": str(out_dir),
        "scenes": [
            {
                "id": 1,
                "title": "Ultra HD Feature",
                "path": str(f1),
                "height": 2160,
                "duration": 3661,
                "performers": ["Star A"],
            },
            {
                "id": 2,
                "title": "HD Short",
                "path": str(f2),
                "height": 1080,
                "duration": 540,
                "performers": ["Star B"],
            },
        ],
    }

    res = task.run_build_megapack(payload)
    bbcode = Path(res["bbcode_path"]).read_text(encoding="utf-8")

    # Scene 1: 2160 height -> [2160p], 3661s -> [1:01:01]
    assert "1. [b]Ultra HD Feature[/b] (Star A) [2160p] [1:01:01]" in bbcode
    # Scene 2: 1080 height -> [1080p], 540s -> [9:00]
    assert "2. [b]HD Short[/b] (Star B) [1080p] [9:00]" in bbcode


def test_stage4c_performer_union_overflow_cap(tmp_path):
    """Performer union caps at 4 performers and renders '+N more' for large casts."""
    out_dir = tmp_path / "stage4c_perfs_out"
    out_dir.mkdir()
    pack_title = "Cast Megapack"
    pack_dir = out_dir / pack_title
    pack_dir.mkdir()

    f1 = pack_dir / "cast_scene.mp4"
    f1.write_bytes(b"\x00" * 1024)

    # 7 distinct performers across scenes
    all_stars = ["Alice", "Bob", "Charlie", "Diana", "Eve", "Frank", "Grace"]
    payload = {
        "pack_title": pack_title,
        "output_dir": str(out_dir),
        "performers": all_stars,
        "scenes": [
            {
                "id": 1,
                "title": "Ensemble Scene",
                "path": str(f1),
                "performers": all_stars,
            }
        ],
    }

    res = task.run_build_megapack(payload)
    bbcode = Path(res["bbcode_path"]).read_text(encoding="utf-8")

    # Top 4 alphabetically: Alice, Bob, Charlie, Diana. Extra = 3 (+3 more)
    assert "Alice, Bob, Charlie & Diana +3 more" in bbcode
    assert "[b][color=#8a9ba8]Performers[/color][/b]" in bbcode


def test_stage4c_unified_studio_header(tmp_path):
    """Studio header is rendered when scenes share a single common studio."""
    out_dir = tmp_path / "stage4c_studio_out"
    out_dir.mkdir()
    pack_title = "Studio Alpha Megapack"
    pack_dir = out_dir / pack_title
    pack_dir.mkdir()

    f1 = pack_dir / "studio_scene.mp4"
    f1.write_bytes(b"\x00" * 1024)

    payload = {
        "pack_title": pack_title,
        "output_dir": str(out_dir),
        "scenes": [
            {
                "id": 1,
                "title": "Scene Alpha",
                "path": str(f1),
                "studio": "Studio Alpha Exclusive",
            }
        ],
    }

    res = task.run_build_megapack(payload)
    bbcode = Path(res["bbcode_path"]).read_text(encoding="utf-8")
    assert "Studio Alpha Exclusive" in bbcode
    assert "[b][color=#8a9ba8]Studio[/color][/b]" in bbcode


def test_stage4c_empify_tag_cleaning():
    """empify cleanly normalizes tag strings removing illegal tracker characters and metacharacters."""
    assert empify("Big [Budget]") == "big.budget"
    assert empify("4K Ultra-HD / HDR") == "4k.ultra.hd.hdr"
    assert empify("Star: Performer <1080p>") == "star.performer.1080p"


def test_stage4c_tracker_tags_cleanliness(tmp_path):
    """tracker_tags emitted in manifest and result contains no brackets, braces, or whitespace."""
    out_dir = tmp_path / "stage4c_tags_clean_out"
    out_dir.mkdir()
    pack_title = "Evil Tags Megapack"
    pack_dir = out_dir / pack_title
    pack_dir.mkdir()

    f1 = pack_dir / "evil_tags_scene.mp4"
    f1.write_bytes(b"\x00" * 1024)

    payload = {
        "pack_title": pack_title,
        "output_dir": str(out_dir),
        "tags": ["Tag [VIP]", "[4K] HDR", "  Messy   Tag  ", "Exclusive / Uncut", "Test [Tag]!"],
        "scenes": [
            {
                "id": 1,
                "title": "Scene [1]",
                "path": str(f1),
                "performers": ["Performer [Star]", "Jane   Doe \t \n"],
                "tags": ["Scene [Tag]", "VR [180]"],
                "studio": "Studio [Alpha]",
                "height": 2160,
                "duration": 3600,
                "video_codec": "hevc [main10]",
                "date": "2026-07-04",
            }
        ],
    }

    res = task.run_build_megapack(payload)
    manifest = json.loads(Path(res["manifest_path"]).read_text(encoding="utf-8"))

    tracker_tags = manifest["tracker_tags"]
    assert len(tracker_tags) > 0
    assert tracker_tags == res["tracker_tags"]

    for tag in tracker_tags:
        assert isinstance(tag, str)
        assert len(tag) > 0
        # Assert no BBCode metacharacters or whitespace
        assert "[" not in tag, f"Tag '{tag}' contains ["
        assert "]" not in tag, f"Tag '{tag}' contains ]"
        assert "{" not in tag, f"Tag '{tag}' contains {{"
        assert "}" not in tag, f"Tag '{tag}' contains }}"
        assert "<" not in tag, f"Tag '{tag}' contains <"
        assert ">" not in tag, f"Tag '{tag}' contains >"
        assert " " not in tag, f"Tag '{tag}' contains space"
        assert "\t" not in tag, f"Tag '{tag}' contains tab"
        assert "\n" not in tag, f"Tag '{tag}' contains newline"
        assert "\r" not in tag, f"Tag '{tag}' contains carriage return"
