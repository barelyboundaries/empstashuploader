"""
Tests for presentation size budgeting and real thumbnail generation (Fix A and Fix B).
"""

import io
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch
import pytest
from PIL import Image

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
from empornium_megapack.images import make_thumbnail, fit_presentation_budget
from empornium_megapack.metadata import THUMB_WIDTH, THUMB_RENDER_WIDTH
from empornium_megapack.build import verify_preflight_checklist
from empornium_megapack.config import get_settings


def test_make_thumbnail_resize_preserve_aspect_ratio(tmp_path):
    """make_thumbnail produces a JPEG at the requested width and preserves aspect ratio."""
    src = tmp_path / "large.jpg"
    dest = tmp_path / "thumbs" / "large_thumb.jpg"
    # Create 800x400 image (2:1 aspect ratio)
    img = Image.new("RGB", (800, 400), color=(100, 150, 200))
    img.save(src, format="JPEG")

    result_path = make_thumbnail(src, dest, max_width=400)
    assert result_path == dest
    assert dest.exists()

    with Image.open(dest) as thumb:
        assert thumb.format == "JPEG"
        assert thumb.width == 400
        assert thumb.height == 200


def test_make_thumbnail_never_upscales(tmp_path):
    """make_thumbnail never upscales a smaller source and copies it through."""
    src = tmp_path / "small.jpg"
    dest = tmp_path / "thumbs" / "small_thumb.jpg"
    img = Image.new("RGB", (150, 100), color=(50, 100, 150))
    img.save(src, format="JPEG")

    result_path = make_thumbnail(src, dest, max_width=400)
    assert result_path == dest
    assert dest.exists()

    with Image.open(dest) as thumb:
        assert thumb.width == 150
        assert thumb.height == 100


def test_make_thumbnail_handles_rgba_png(tmp_path):
    """make_thumbnail converts RGBA PNG to RGB JPEG at requested width."""
    src = tmp_path / "transparent.png"
    dest = tmp_path / "thumbs" / "transparent_thumb.jpg"
    img = Image.new("RGBA", (600, 300), color=(255, 0, 0, 128))
    img.save(src, format="PNG")

    result_path = make_thumbnail(src, dest, max_width=300)
    assert result_path == dest
    assert dest.exists()

    with Image.open(dest) as thumb:
        assert thumb.format == "JPEG"
        assert thumb.mode == "RGB"
        assert thumb.width == 300
        assert thumb.height == 150


def test_fit_presentation_budget_under_budget_byte_identical(tmp_path):
    """fit_presentation_budget over a set that is already under budget returns files byte-identical."""
    p1 = tmp_path / "img1.jpg"
    p2 = tmp_path / "img2.jpg"

    Image.new("RGB", (100, 100), color=(255, 0, 0)).save(p1, format="JPEG")
    Image.new("RGB", (100, 100), color=(0, 255, 0)).save(p2, format="JPEG")

    bytes_1 = p1.read_bytes()
    bytes_2 = p2.read_bytes()

    total_bytes, failed = fit_presentation_budget([p1, p2], budget=500_000, floor=5_000)
    assert failed == []
    assert total_bytes == len(bytes_1) + len(bytes_2)
    assert p1.read_bytes() == bytes_1
    assert p2.read_bytes() == bytes_2


def test_fit_presentation_budget_waterfilling_shrinks_oversized_only(tmp_path):
    """fit_presentation_budget leaves small images untouched and shrinks oversized images to fit budget."""
    small_p = tmp_path / "small.jpg"
    Image.new("RGB", (50, 50), color=(10, 20, 30)).save(small_p, format="JPEG", quality=85)
    small_orig_bytes = small_p.read_bytes()
    small_orig_size = len(small_orig_bytes)

    large_p = tmp_path / "large.jpg"
    import random
    rng = random.Random(42)
    noise_data = bytes(rng.getrandbits(8) for _ in range(1200 * 800 * 3))
    large_img = Image.frombytes("RGB", (1200, 800), noise_data)
    large_img.save(large_p, format="JPEG", quality=95)
    large_orig_size = large_p.stat().st_size

    assert large_orig_size > 100_000

    target_budget = small_orig_size + 40_000
    floor = 10_000

    total_bytes, failed = fit_presentation_budget([small_p, large_p], budget=target_budget, floor=floor)
    assert failed == []
    assert total_bytes <= target_budget
    # Small image must remain untouched byte-for-byte
    assert small_p.read_bytes() == small_orig_bytes
    # Large image must have shrunk
    assert large_p.stat().st_size < large_orig_size


def test_fit_presentation_budget_failed_floor_reported(tmp_path):
    """An image that cannot reach the floor is reported in the returned list and does not raise."""
    p = tmp_path / "huge.jpg"
    img = Image.new("RGB", (500, 500), color=(123, 45, 67))
    img.save(p, format="JPEG")

    # Set budget and floor to impossible value (e.g. 1 byte)
    total_bytes, failed = fit_presentation_budget([p], budget=1, floor=1)
    assert p in failed
    assert total_bytes == p.stat().st_size


def test_megapack_bbcode_distinct_thumb_and_full_urls(tmp_path):
    """Megapack BBCode embeds the thumbnail URL inside [img=200] and the full-size URL in enclosing [url=]."""
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    pack_title = "Distinct URL Megapack"
    pack_dir = out_dir / pack_title
    pack_dir.mkdir()

    media_file = pack_dir / "scene.mp4"
    media_file.write_bytes(b"\x00" * 1024)

    payload = {
        "pack_title": pack_title,
        "output_dir": str(out_dir),
        "scenes": [{"id": 1, "path": str(media_file)}],
    }

    full_url = "https://imgbox.com/full_sheet_1.jpg"
    thumb_url = "https://imgbox.com/thumb_sheet_1.jpg"

    def mock_upload(paths, payload_arg, progress_callback=None):
        return [full_url, thumb_url]

    with patch.object(task, "upload_previews", side_effect=mock_upload):
        result = task.run_build_megapack(payload)

    assert result["status"] == "success"
    bbcode = Path(result["bbcode_path"]).read_text(encoding="utf-8")

    assert f"[url={full_url}][img={THUMB_WIDTH}]{thumb_url}[/img][/url]" in bbcode
    assert full_url != thumb_url


def test_verify_preflight_presentation_size_check(tmp_path):
    """verify_preflight_checklist checks presentation_bytes against cap and omits when None."""
    dummy_media = tmp_path / "dummy.mp4"
    dummy_media.write_bytes(b"\x00" * 1024)
    dummy_torrent = tmp_path / "test.torrent"
    import torf
    t = torf.Torrent(path=str(dummy_media), trackers=["http://tracker.example.com/announce"])
    t.generate()
    t.write(str(dummy_torrent))

    cap = get_settings().presentation_max_bytes

    # 1. Passed check when under cap
    res_passed = verify_preflight_checklist(
        torrent_path=dummy_torrent,
        presentation_bytes=cap - 1000,
        submission_data={"image_urls": ["https://imgbox.com/1.jpg"], "preview_only": False, "tracker_tags": ["tag1"]},
    )
    checks_passed = {c["id"]: c for c in res_passed["checks"]}
    assert "presentation_size" in checks_passed
    assert checks_passed["presentation_size"]["passed"] is True

    # 2. Failed check when over cap
    res_failed = verify_preflight_checklist(
        torrent_path=dummy_torrent,
        presentation_bytes=cap + 500_000,
        submission_data={"image_urls": ["https://imgbox.com/1.jpg"], "preview_only": False, "tracker_tags": ["tag1"]},
    )
    checks_failed = {c["id"]: c for c in res_failed["checks"]}
    assert "presentation_size" in checks_failed
    assert checks_failed["presentation_size"]["passed"] is False

    # 3. Omitted check when presentation_bytes is None
    res_omitted = verify_preflight_checklist(
        torrent_path=dummy_torrent,
        presentation_bytes=None,
        submission_data={"image_urls": ["https://imgbox.com/1.jpg"], "preview_only": False, "tracker_tags": ["tag1"]},
    )
    checks_omitted = {c["id"]: c for c in res_omitted["checks"]}
    assert "presentation_size" not in checks_omitted