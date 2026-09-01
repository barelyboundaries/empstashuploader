"""Adversarial stress-test suite for Milestone 2 domain seams & progress streaming.

Tests:
1. `create_torrent` with diverse callback invocations, error-raising callbacks, None callbacks, and large payloads.
2. Monotonic progress emission and boundaries [0.70, 0.90] during hashing.
3. Multi-file, non-ASCII Unicode, boundary byte sizes, and torf integrity verification.
4. Tracker, webseed, private flag, comment, and metadata propagation.
5. Error conditions and strictness defaults (degrade-and-continue fallback).
6. Large payload piece count scale test (thousands of pieces).
7. End-to-end full build megapack progress monotonicity trace.
"""

import io
import os
import sys
import tempfile
from pathlib import Path
from typing import List, Tuple, Any
from unittest.mock import patch

import pytest
import torf
from PIL import Image

# Add plugin dir to sys.path
PLUGIN_DIR = Path(__file__).resolve().parent.parent.parent / "plugin"
if str(PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(PLUGIN_DIR))

import task
from empornium_megapack.torrents import (
    TorrentError,
    create_torrent,
    piece_size_for,
    calculate_piece_size,
    source_for_announce,
    validate_announce_url,
    sanitize_announce_url,
)
from empornium_megapack.build import sanitize_name, write_manifest


ANNOUNCE_URL = "http://tracker.empornium.sx:2710/aaaaaaaa/bbbbbbbb/announce"


class TestCreateTorrentCallbacks:
    """Stress testing create_torrent callback seam."""

    def test_callback_none_succeeds(self, tmp_path):
        payload = tmp_path / "payload_none"
        payload.mkdir()
        (payload / "video.mp4").write_bytes(b"\x00" * (100 * 1024))
        out_torrent = tmp_path / "test_none.torrent"

        meta = create_torrent(
            payload_dir=payload,
            announce_url=ANNOUNCE_URL,
            out_path=out_torrent,
            piece_size=16384,
            callback=None,
        )

        assert out_torrent.is_file()
        assert meta["piece_count"] == 7  # ceil(102400 / 16384) = 7
        assert meta["total_bytes"] == 100 * 1024
        assert meta["file_count"] == 1
        assert len(meta["infohash"]) == 40

        readback = torf.Torrent.read(out_torrent)
        assert readback.verify(str(payload)) is True

    def test_callback_monotonic_progress_emission(self, tmp_path):
        payload = tmp_path / "payload_monotonic"
        payload.mkdir()
        # Create a 2 MB file with 16 KiB piece size = 128 pieces
        total_size = 2 * 1024 * 1024
        (payload / "scene_2mb.mp4").write_bytes(b"\xab" * total_size)
        out_torrent = tmp_path / "test_monotonic.torrent"

        calls: List[Tuple[torf.Torrent, Any, int, int]] = []

        def tracking_callback(torrent_obj, filepath, pieces_done, total_pieces):
            calls.append((torrent_obj, filepath, pieces_done, total_pieces))

        meta = create_torrent(
            payload_dir=payload,
            announce_url=ANNOUNCE_URL,
            out_path=out_torrent,
            piece_size=16384,
            callback=tracking_callback,
        )

        assert len(calls) > 0
        assert meta["piece_count"] == 128

        expected_total_pieces = 128
        prev_done = -1

        for torrent_obj, filepath, pieces_done, total_pieces in calls:
            assert isinstance(torrent_obj, torf.Torrent)
            assert str(filepath).endswith("scene_2mb.mp4")
            assert total_pieces == expected_total_pieces
            assert pieces_done >= prev_done, f"Non-monotonic progress: {prev_done} -> {pieces_done}"
            assert 0 <= pieces_done <= total_pieces
            prev_done = pieces_done

        # Final call must report all pieces completed
        last_torrent, last_file, last_done, last_total = calls[-1]
        assert last_done == expected_total_pieces
        assert last_total == expected_total_pieces

    @pytest.mark.parametrize(
        "exc_type,exc_msg",
        [
            (RuntimeError, "Callback aborted abruptly"),
            (ValueError, "Invalid hash state"),
            (ZeroDivisionError, "division by zero in callback"),
            (Exception, "General callback failure"),
        ],
    )
    def test_callback_exception_wrapped_in_torrent_error(self, tmp_path, exc_type, exc_msg):
        payload = tmp_path / "payload_err"
        payload.mkdir()
        (payload / "video.mp4").write_bytes(b"\x12" * (64 * 1024))
        out_torrent = tmp_path / "test_err.torrent"

        def failing_callback(torrent_obj, filepath, pieces_done, total_pieces):
            if pieces_done >= 1:
                raise exc_type(exc_msg)

        with pytest.raises(TorrentError) as exc_info:
            create_torrent(
                payload_dir=payload,
                announce_url=ANNOUNCE_URL,
                out_path=out_torrent,
                piece_size=16384,
                callback=failing_callback,
            )

        assert "Torrent generation failed" in str(exc_info.value)
        assert exc_msg in str(exc_info.value)
        # Torrent write occurs after generation, so failed generation should not write out_torrent
        assert not out_torrent.exists()

    def test_callback_scale_large_payload_pieces(self, tmp_path):
        payload = tmp_path / "payload_scale"
        payload.mkdir()
        # 16 MB with 16 KiB piece size = 1024 pieces
        total_size = 16 * 1024 * 1024
        # Write repeating chunks
        chunk = b"X" * (64 * 1024)
        with open(payload / "big_payload.mp4", "wb") as f:
            for _ in range(256):
                f.write(chunk)

        out_torrent = tmp_path / "big.torrent"
        call_count = 0
        last_reported_done = 0

        def scale_callback(torrent_obj, filepath, pieces_done, total_pieces):
            nonlocal call_count, last_reported_done
            call_count += 1
            last_reported_done = pieces_done

        meta = create_torrent(
            payload_dir=payload,
            announce_url=ANNOUNCE_URL,
            out_path=out_torrent,
            piece_size=16384,
            callback=scale_callback,
        )

        assert meta["piece_count"] == 1024
        assert meta["total_bytes"] == total_size
        assert call_count > 0
        assert last_reported_done == 1024

        readback = torf.Torrent.read(out_torrent)
        assert readback.verify(str(payload)) is True


class TestCreateTorrentPayloadAndPieceBoundaries:
    """Stress testing large payloads, multi-file structures, Unicode, and edge cases."""

    def test_multi_file_heterogeneous_payload_with_unicode_and_verify(self, tmp_path):
        payload = tmp_path / "Pack_🎬_日本語_2026"
        payload.mkdir()

        # Diverse file sizes and nested subdirectories
        sub_dir = payload / "Subfolder With Spaces"
        sub_dir.mkdir()
        cs_dir = payload / "Contact Sheets"
        cs_dir.mkdir()

        files_data = {
            payload / "video_1byte.mp4": b"X",
            payload / "video_exact_16k.mp4": b"A" * 16384,
            payload / "video_odd_size.mp4": b"B" * 33333,
            sub_dir / "video_sub_500k.mp4": b"C" * (500 * 1024),
            sub_dir / "unicode_файл_тест.mkv": b"D" * (120 * 1024),
            cs_dir / "sheet1.jpg": b"\xff\xd8\xff\xe0" + b"\x00" * 4096,
        }

        total_bytes = 0
        for p, content in files_data.items():
            p.write_bytes(content)
            total_bytes += len(content)

        out_torrent = tmp_path / "multi_file.torrent"
        meta = create_torrent(
            payload_dir=payload,
            announce_url=ANNOUNCE_URL,
            out_path=out_torrent,
            name="Pack_🎬_日本語_2026",
            piece_size=32768,
            expected_bytes=total_bytes,
        )

        assert meta["name"] == "Pack_🎬_日本語_2026"
        assert meta["total_bytes"] == total_bytes
        assert meta["file_count"] == 6
        assert meta["piece_size"] == 32768

        readback = torf.Torrent.read(out_torrent)
        assert readback.verify(str(payload)) is True

    def test_multi_trackers_and_webseeds_and_metadata(self, tmp_path):
        payload = tmp_path / "payload_trackers"
        payload.mkdir()
        (payload / "file.mp4").write_bytes(b"DATA" * 1000)
        out_torrent = tmp_path / "trackers.torrent"

        trackers = [
            "http://tracker1.empornium.sx:2710/announce",
            "http://tracker2.pornbay.org/announce",
        ]
        webseeds = [
            "https://seed1.example.org/files/",
            "https://seed2.example.org/files/",
        ]

        meta = create_torrent(
            payload_dir=payload,
            out_path=out_torrent,
            trackers=trackers,
            web_seeds=webseeds,
            comment="Megapack comment tag",
            source="CustomSourceTag",
            created_by="Challenger Custom Harness",
            private=True,
        )

        readback = torf.Torrent.read(out_torrent)
        assert readback.private is True
        assert readback.source == "CustomSourceTag"
        assert readback.comment == "Megapack comment tag"
        assert readback.created_by == "Challenger Custom Harness"
        assert readback.webseeds == webseeds
        assert readback.trackers == [
            ["http://tracker1.empornium.sx:2710/announce"],
            ["http://tracker2.pornbay.org/announce"],
        ]

    def test_nonexistent_payload_path_raises(self, tmp_path):
        nonexistent = tmp_path / "does_not_exist_dir"
        with pytest.raises(TorrentError, match="Payload path not found"):
            create_torrent(nonexistent, announce_url=ANNOUNCE_URL)

    def test_piece_size_aliases_and_policy(self):
        assert calculate_piece_size is piece_size_for
        # Clamped minimum
        assert calculate_piece_size(-100) == 16384
        assert calculate_piece_size(0) == 16384
        assert calculate_piece_size(1024) == 16384
        # 1 GiB -> 1 MiB
        assert calculate_piece_size(1024 * 1024 * 1024) == 1048576
        # Capped maximum 8 MiB (2^23)
        assert calculate_piece_size(500 * 1024 * 1024 * 1024) == 8388608


class TestPluginTaskIntegrationSeams:
    """Stress testing plugin/task.py integration with domain functions."""

    def test_hashing_callback_stream_range_and_formatting(self, monkeypatch):
        emitted_progress: List[Tuple[float, str]] = []

        def mock_emit_progress(progress: float, message: str = None):
            emitted_progress.append((progress, message or ""))

        # Simulate the hashing_callback closure defined in plugin/task.py:run_build_megapack
        def hashing_callback(torrent_obj, filepath, pieces_done, total_pieces):
            frac = pieces_done / max(total_pieces, 1)
            prog = 0.70 + (0.20 * frac)
            mock_emit_progress(prog, f"Hashing torrent pieces ({pieces_done}/{total_pieces})...")

        dummy_torrent = None
        total_pieces = 50
        for i in range(total_pieces + 1):
            hashing_callback(dummy_torrent, "video.mp4", i, total_pieces)

        assert len(emitted_progress) == 51

        # Check bounds: must start at 0.70 and end at 0.90
        start_prog, start_msg = emitted_progress[0]
        end_prog, end_msg = emitted_progress[-1]

        assert pytest.approx(start_prog, 1e-4) == 0.70
        assert "0/50" in start_msg
        assert pytest.approx(end_prog, 1e-4) == 0.90
        assert "50/50" in end_msg

        # Monotonicity check
        prev_p = -1.0
        for p, msg in emitted_progress:
            assert 0.70 <= p <= 0.90
            assert p >= prev_p
            assert "Hashing torrent pieces" in msg
            prev_p = p

    def test_contact_sheet_degrade_and_continue_on_vcsi_timeout(self, tmp_path, monkeypatch):
        out_jpg = tmp_path / "preview.jpg"
        stderr_buffer = io.StringIO()
        monkeypatch.setattr(sys, "stderr", stderr_buffer)

        # Mock domain generate_contact_sheet to raise TimeoutExpired
        import subprocess

        def mock_domain_cs(**kwargs):
            raise subprocess.TimeoutExpired(cmd=["vcsi"], timeout=5.0)

        monkeypatch.setattr(task, "_domain_generate_contact_sheet", mock_domain_cs)

        res = task.generate_contact_sheet(
            video_path=str(tmp_path / "dummy.mp4"),
            out_path=str(out_jpg),
            layout="4x4",
            timeout=5.0,
            pack_title="Timeout Test Pack",
        )

        assert res == str(out_jpg)
        assert out_jpg.exists()
        assert out_jpg.stat().st_size > 0

        # Verify Pillow placeholder generated valid image
        with Image.open(out_jpg) as im:
            assert im.size == (1280, 720)
            assert im.format == "JPEG"

        # Verify warning marker logged
        stderr_val = stderr_buffer.getvalue()
        assert "\x01w\x02" in stderr_val
        assert "timed out after 5.0s" in stderr_val

    def test_contact_sheet_degrade_and_continue_on_vcsi_failure(self, tmp_path, monkeypatch):
        out_jpg = tmp_path / "preview_fail.jpg"
        stderr_buffer = io.StringIO()
        monkeypatch.setattr(sys, "stderr", stderr_buffer)

        # Mock domain generate_contact_sheet returning False
        def mock_domain_cs_fail(**kwargs):
            return False

        monkeypatch.setattr(task, "_domain_generate_contact_sheet", mock_domain_cs_fail)

        res = task.generate_contact_sheet(
            video_path=str(tmp_path / "dummy.mp4"),
            out_path=str(out_jpg),
            layout="4x4",
            pack_title="Failure Fallback Pack",
        )

        assert res == str(out_jpg)
        assert out_jpg.exists()
        assert out_jpg.stat().st_size > 0

        # Verify warning marker logged
        stderr_val = stderr_buffer.getvalue()
        assert "\x01w\x02" in stderr_val
        assert "vcsi generation failed" in stderr_val

    def test_empty_scenes_payload_fails_cleanly(self, tmp_path):
        out_dir = tmp_path / "output_empty"
        out_dir.mkdir()

        payload = {
            "pack_title": "Empty Pack Test",
            "output_dir": str(out_dir),
            "scenes": [],
        }

        with pytest.raises(RuntimeError, match="No valid media files found"):
            task.run_build_megapack(payload)

        # Ensure no torrent or manifest artifacts were emitted
        assert len(list(out_dir.glob("*.torrent"))) == 0
        assert len(list(out_dir.glob("*_manifest.json"))) == 0
        assert len(list(out_dir.glob(".*.lock"))) == 0

    def test_sanitize_name_and_manifest_domain_wiring(self, tmp_path):
        assert sanitize_name("CON") == "_CON"
        raw_name = "CON: Illegal * Pack / Name? <2026>"
        safe = sanitize_name(raw_name)
        assert safe == "CON_ Illegal _ Pack _ Name_ _2026_"

        manifest_file = tmp_path / "manifest.json"
        data = {"pack_title": safe, "scene_count": 5}
        result_path = write_manifest(manifest_file, data)

        assert result_path == manifest_file
        assert manifest_file.exists()
        import json

        loaded = json.loads(manifest_file.read_text(encoding="utf-8"))
        assert loaded["pack_title"] == safe
        assert loaded["scene_count"] == 5

    def test_full_build_megapack_progress_trace_monotonicity(self, tmp_path, monkeypatch):
        out_dir = tmp_path / "output_build"
        out_dir.mkdir(parents=True, exist_ok=True)
        pack_title = "Empirical Progress Test Pack"
        pack_dir = out_dir / sanitize_name(pack_title)
        pack_dir.mkdir(parents=True, exist_ok=True)
        # Create 3 real dummy video files
        f1 = pack_dir / "scene_01.mp4"
        f2 = pack_dir / "scene_02.mkv"
        f3 = pack_dir / "scene_03.avi"
        f1.write_bytes(b"\x01" * (512 * 1024))
        f2.write_bytes(b"\x02" * (1024 * 1024))
        f3.write_bytes(b"\x03" * (256 * 1024))

        progress_trace: List[Tuple[float, str]] = []

        orig_emit = task.emit_progress
        def tracking_emit(progress: float, message: str = None):
            progress_trace.append((float(progress), message or ""))
            orig_emit(progress, message)

        monkeypatch.setattr(task, "emit_progress", tracking_emit)

        # Mock domain cs to avoid needing external vcsi binary
        monkeypatch.setattr(task, "_domain_generate_contact_sheet", lambda **kwargs: False)

        payload = {
            "pack_title": "Empirical Progress Test Pack",
            "output_dir": str(out_dir),
            "scenes": [
                {"scene_id": "s1", "path": str(f1)},
                {"scene_id": "s2", "path": str(f2)},
                {"scene_id": "s3", "path": str(f3)},
            ],
            "trackers": ["http://tracker.empornium.sx:2710/aaaaaaaa/bbbbbbbb/announce"],
        }

        result = task.run_build_megapack(payload)

        assert result["status"] == "success"
        assert os.path.exists(result["torrent_path"])
        assert os.path.exists(result["manifest_path"])
        assert os.path.exists(result["bbcode_path"])

        # Check full progress trace
        assert len(progress_trace) >= 6
        assert pytest.approx(progress_trace[0][0], 1e-4) == 0.05
        assert pytest.approx(progress_trace[-1][0], 1e-4) == 1.0

        # Monotonicity check
        prev_p = -1.0
        for p, msg in progress_trace:
            assert 0.0 <= p <= 1.0
            assert p >= prev_p, f"Progress decreased: {prev_p} -> {p} (msg: {msg})"
            prev_p = p
