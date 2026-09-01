import base64
import io
import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch
from PIL import Image
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = REPO_ROOT / "backend"
PLUGIN_DIR = REPO_ROOT / "plugin"

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
if str(PLUGIN_DIR) not in sys.path:
    sys.path.append(str(PLUGIN_DIR))
if str(REPO_ROOT) not in sys.path:
    sys.path.append(str(REPO_ROOT))

import task
from empornium_megapack import config as _domain_config
from empornium_megapack.config import Settings
from empornium_megapack.images import ContactSheetError


# ============================================================================
# Item 1 Tests: Image Size Ceiling before upload_hamster
# ============================================================================

def test_upload_previews_shrinks_oversize_image_before_upload(tmp_path, monkeypatch):
    """An image over the cap is shrunk under it before upload_hamster is called."""
    img_path = tmp_path / "oversize.jpg"
    img = Image.new("RGB", (3000, 3000), color=(200, 100, 50))
    img.save(img_path, format="JPEG", quality=95)

    original_size = img_path.stat().st_size
    cap = original_size // 2

    settings = _domain_config.get_settings()
    monkeypatch.setattr(settings, "upload_image_max_bytes", cap)
    monkeypatch.setattr(settings, "hamster_api_key", "test_key")

    call_size = None

    def mock_upload(p, settings=None):
        nonlocal call_size
        call_size = Path(p).stat().st_size
        return "https://hamsterimg.net/shrunk.jpg"

    monkeypatch.setattr("empornium_megapack.images.upload_hamster", mock_upload)

    urls = task.upload_previews([str(img_path)], {"upload_previews": True})

    assert urls == ["https://hamsterimg.net/shrunk.jpg"]
    assert call_size is not None
    assert call_size <= cap
    assert img_path.stat().st_size <= cap


def test_upload_previews_under_cap_is_byte_identical(tmp_path, monkeypatch):
    """A file under the cap is byte-identical after upload_previews."""
    img_path = tmp_path / "small.jpg"
    img = Image.new("RGB", (100, 100), color=(50, 100, 150))
    img.save(img_path, format="JPEG", quality=85)

    original_bytes = img_path.read_bytes()
    original_size = len(original_bytes)

    settings = _domain_config.get_settings()
    monkeypatch.setattr(settings, "upload_image_max_bytes", original_size + 10000)
    monkeypatch.setattr(settings, "hamster_api_key", "test_key")
    monkeypatch.setattr("empornium_megapack.images.upload_hamster", lambda p, settings=None: "https://hamsterimg.net/small.jpg")

    urls = task.upload_previews([str(img_path)], {"upload_previews": True})

    assert urls == ["https://hamsterimg.net/small.jpg"]
    assert img_path.read_bytes() == original_bytes


def test_upload_previews_unshrinkable_image_degrades_to_file_url(tmp_path, monkeypatch):
    """An image that cannot be shrunk degrades to a file:/// URL and does not raise."""
    img_path = tmp_path / "cannot_shrink.jpg"
    img_path.write_bytes(b"invalid jpeg bytes that cause error")

    settings = _domain_config.get_settings()
    monkeypatch.setattr(settings, "upload_image_max_bytes", 100)
    monkeypatch.setattr(settings, "hamster_api_key", "test_key")

    urls = task.upload_previews([str(img_path)], {"upload_previews": True})

    assert len(urls) == 1
    assert urls[0].startswith("file:///")
    assert "cannot_shrink.jpg" in urls[0]


# ============================================================================
# Item 2 Tests: Chunked BBCode Sentinel
# ============================================================================

def test_emit_bbcode_sentinel_roundtrip_large_multiline_utf8(capsys):
    """emit_bbcode_sentinel round-trips a >100k multi-line UTF-8 BBCode string through stderr."""
    run_id = "test-bbcode-roundtrip-run-42"
    scene_blocks = []
    for i in range(400):
        scene_blocks.append(
            f"[b]Scene {i + 1}: Performer 桜井 / Auhneesh Nicole™ / Élodie [2160p][/b]\n"
            f"[quote]Special description with non-ASCII: ★★★★★ — {i * 12345}[/quote]\n"
            f"[url=https://hamsterimg.net/img_{i}.jpg][img=200]https://hamsterimg.net/img_{i}.jpg[/img][/url]"
        )
    big_bbcode = "\n\n[hr]\n\n".join(scene_blocks)
    assert len(big_bbcode.encode("utf-8")) > 100000

    payload = {"run_id": run_id}
    result = {"status": "success", "bbcode": big_bbcode}

    task.emit_bbcode_sentinel(payload, result)

    captured = capsys.readouterr()
    stderr = captured.err

    sentinel_prefix = f"\x01i\x02EMPORNIUM_TASK_BBCODE {run_id} "
    lines = [ln for ln in stderr.splitlines() if sentinel_prefix in ln]
    assert len(lines) > 1, "Expected multiple chunk lines for >100k payload"

    chunks_by_index = {}
    total_chunks = None
    for ln in lines:
        idx_marker = ln.index(sentinel_prefix) + len(sentinel_prefix)
        rest = ln[idx_marker:].strip()
        ratio_part, chunk_b64 = rest.split(":", 1)
        cur_idx_s, total_s = ratio_part.strip().split("/")
        cur_idx, total = int(cur_idx_s), int(total_s)
        if total_chunks is None:
            total_chunks = total
        else:
            assert total_chunks == total
        chunks_by_index[cur_idx] = chunk_b64.strip()

    assert total_chunks == len(chunks_by_index)
    assembled_b64 = "".join(chunks_by_index[i] for i in range(1, total_chunks + 1))
    decoded_bbcode = base64.b64decode(assembled_b64).decode("utf-8")

    assert decoded_bbcode == big_bbcode


# ============================================================================
# Item 3 Tests: Cover Image Upload & Build Override
# ============================================================================

def test_run_upload_cover_success_png_with_alpha(tmp_path, monkeypatch):
    """run_upload_cover with RGBA PNG base64 produces a JPEG on disk and calls upload_hamster once."""
    staging_dir = tmp_path / "staging"
    settings = _domain_config.get_settings()
    monkeypatch.setattr(settings, "staging_dir", staging_dir)
    monkeypatch.setattr(settings, "hamster_api_key", "test_key")

    img = Image.new("RGBA", (200, 200), color=(255, 0, 0, 128))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    b64_data = base64.b64encode(buf.getvalue()).decode("ascii")

    uploaded_path = None

    def mock_upload(p, settings=None):
        nonlocal uploaded_path
        uploaded_path = str(p)
        return "https://hamsterimg.net/images/pasted_cover_001.jpg"

    monkeypatch.setattr("empornium_megapack.images.upload_hamster", mock_upload)

    payload = {
        "run_id": "cover-test-rgba-001",
        "image_b64": f"data:image/png;base64,{b64_data}",
        "filename": "pasted.png",
    }

    res = task.run_upload_cover(payload)

    assert res["status"] == "success"
    assert res["task"] == "UploadCoverImage"
    assert res["run_id"] == "cover-test-rgba-001"
    assert res["cover_url"] == "https://hamsterimg.net/images/pasted_cover_001.jpg"

    saved_path = Path(res["local_path"])
    assert saved_path.exists()
    assert saved_path.suffix == ".jpg"
    assert uploaded_path == str(saved_path)

    with Image.open(saved_path) as saved_img:
        assert saved_img.format == "JPEG"
        assert saved_img.mode == "RGB"


def test_run_upload_cover_oversize_payload_raises():
    """An oversize base64 payload raises ValueError before decoding."""
    oversize_b64 = "A" * (13 * 1024 * 1024)
    payload = {
        "run_id": "cover-oversize",
        "image_b64": oversize_b64,
    }
    with pytest.raises(ValueError, match="too large"):
        task.run_upload_cover(payload)


def test_run_upload_cover_non_image_payload_fails():
    """A non-image payload fails cleanly with a ValueError."""
    bad_bytes = b"Hello, this is just plain text, not an image file at all!"
    bad_b64 = base64.b64encode(bad_bytes).decode("ascii")
    payload = {
        "run_id": "cover-bad-bytes",
        "image_b64": bad_b64,
    }
    with pytest.raises(ValueError, match="Failed to decode image data"):
        task.run_upload_cover(payload)


def test_build_payload_with_cover_image_url_override(tmp_path, monkeypatch):
    """A build payload carrying cover_image_url puts that exact URL in BBCode and does not call fetch_stash_image."""
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    pack_title = "Cover Override Pack"
    pack_dir = out_dir / pack_title
    pack_dir.mkdir()

    media_file = pack_dir / "scene1.mp4"
    media_file.write_bytes(b"\x00" * 1024)

    cover_override = "https://hamsterimg.net/images/user_pasted_cover.jpg"

    payload = {
        "pack_title": pack_title,
        "output_dir": str(out_dir),
        "upload_previews": False,
        "single_scene": True,
        "cover_image_url": cover_override,
        "scenes": [{"id": 42, "path": str(media_file), "title": "Scene 42"}],
    }

    mock_fetch = MagicMock(return_value=None)
    monkeypatch.setattr("empornium_megapack.images.fetch_stash_image", mock_fetch)

    result = task.run_build_megapack(payload)

    assert result["status"] == "success"
    for call in mock_fetch.call_args_list:
        assert "/screenshot" not in str(call)

    assert cover_override in result["bbcode"]
    assert f"[center][img]{cover_override}[/img][/center]" in result["bbcode"]
    assert cover_override in result["uploaded_urls"]
