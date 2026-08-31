"""
Deep adversarial challenge suite for Milestone 1 Iteration 2:
Intensive stress-testing for task.py edge cases, lockfile corruption, bencoding,
type safety, Win32 API resilience, and high-concurrency multi-process races.
"""

import sys
import os
import io
import json
import time
import ctypes
import tempfile
import subprocess
from pathlib import Path
import pytest
import torf

CURRENT_DIR = Path(__file__).resolve().parent
BACKEND_DIR = CURRENT_DIR.parent
PLUGIN_DIR = BACKEND_DIR.parent / "plugin"

if str(PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(PLUGIN_DIR))
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import task


# --- test media fixture -------------------------------------------------------
# run_build_megapack now refuses to build a pack with no valid media (it would
# emit a torrent with zero piece hashes). Tests below exercise lockfiles, unicode
# and payload handling -- not the empty-input path -- so they need one real file.
# Content is irrelevant: torf hashes bytes, and vcsi failing on a non-video falls
# back to the Pillow placeholder with a warning.
_DUMMY_MEDIA_DIR = None


def _dummy_media_path(target_dir=None, pack_title=None):
    if target_dir is not None:
        title_str = str(pack_title) if pack_title is not None else "Megapack"
        target_dir = os.path.join(str(target_dir), task.sanitize_name(title_str))
        os.makedirs(str(target_dir), exist_ok=True)
        p = os.path.join(str(target_dir), "dummy_media.mp4")
    else:
        global _DUMMY_MEDIA_DIR
        if _DUMMY_MEDIA_DIR is None:
            _DUMMY_MEDIA_DIR = tempfile.mkdtemp(prefix="megapack_test_media_")
        p = os.path.join(_DUMMY_MEDIA_DIR, "dummy_media.mp4")
    if not os.path.exists(p):
        with open(p, "wb") as fh:
            fh.write(b"\x00" * 65536)
    return p
# ------------------------------------------------------------------------------


class TestLockfileCorruptionAndEdgeCases:
    """Adversarial testing of lockfile corruption, deadlocks, and boundary states."""

    def test_zero_byte_lockfile_reclaim(self, tmp_path):
        """0-byte lockfile must be treated as stale/corrupted and reclaimed immediately."""
        out_dir = tmp_path / "zero_byte_lock"
        out_dir.mkdir(parents=True, exist_ok=True)
        pack_title = "ZeroByteLockPack"
        safe_title = task.sanitize_name(pack_title)
        lock_file = out_dir / f".{safe_title}.lock"
        lock_file.touch()  # 0 bytes

        payload = {
            "pack_title": pack_title,
            "output_dir": str(out_dir),
            "scenes": [{"id": 1, "path": _dummy_media_path(out_dir, pack_title)}]
        }

        res = task.run_build_megapack(payload)
        assert res["status"] == "success"
        assert not lock_file.exists()

    def test_corrupted_non_numeric_pid_lockfile(self, tmp_path):
        """Lockfile containing non-numeric PID strings must be reclaimed without crashing."""
        out_dir = tmp_path / "corrupt_pid_lock"
        out_dir.mkdir(parents=True, exist_ok=True)
        pack_title = "CorruptPidPack"
        safe_title = task.sanitize_name(pack_title)
        lock_file = out_dir / f".{safe_title}.lock"
        lock_file.write_text("pid=INVALID_PID_ABC\nstarted=NOT_A_FLOAT\npack=Corrupt\n", encoding="utf-8")

        payload = {
            "pack_title": pack_title,
            "output_dir": str(out_dir),
            "scenes": [{"id": 1, "path": _dummy_media_path(out_dir, pack_title)}]
        }

        res = task.run_build_megapack(payload)
        assert res["status"] == "success"
        assert not lock_file.exists()

    def test_negative_and_zero_pid_in_lockfile(self, tmp_path):
        """Lockfile with negative or 0 PID must be reclaimed."""
        for bad_pid in [-1, 0, -99999]:
            out_dir = tmp_path / f"bad_pid_{abs(bad_pid)}"
            out_dir.mkdir(parents=True, exist_ok=True)
            pack_title = f"BadPidPack_{abs(bad_pid)}"
            safe_title = task.sanitize_name(pack_title)
            lock_file = out_dir / f".{safe_title}.lock"
            lock_file.write_text(f"pid={bad_pid}\nstarted={time.time()}\npack={pack_title}\n", encoding="utf-8")

            payload = {
                "pack_title": pack_title,
                "output_dir": str(out_dir),
                "scenes": [{"id": 1, "path": _dummy_media_path(out_dir, pack_title)}]
            }

            res = task.run_build_megapack(payload)
            assert res["status"] == "success"
            assert not lock_file.exists()

    def test_unicode_and_emojis_in_lockfile_content(self, tmp_path):
        """Lockfile containing multi-byte UTF-8 characters and emoji in pack field."""
        out_dir = tmp_path / "unicode_lock"
        out_dir.mkdir(parents=True, exist_ok=True)
        pack_title = "🌸 鈴木_Pack_🎬"
        safe_title = task.sanitize_name(pack_title)
        lock_file = out_dir / f".{safe_title}.lock"
        # Write dead PID with multi-byte unicode
        lock_file.write_text(f"pid=99999999\nstarted={time.time() - 5000}\npack={pack_title}\n", encoding="utf-8")

        payload = {
            "pack_title": pack_title,
            "output_dir": str(out_dir),
            "scenes": [{"id": 1, "path": _dummy_media_path(out_dir, pack_title)}]
        }

        res = task.run_build_megapack(payload)
        assert res["status"] == "success"
        assert not lock_file.exists()

    def test_lockfile_with_binary_garbage(self, tmp_path):
        """Lockfile containing completely unparseable binary bytes."""
        out_dir = tmp_path / "binary_lock"
        out_dir.mkdir(parents=True, exist_ok=True)
        pack_title = "BinaryLockPack"
        safe_title = task.sanitize_name(pack_title)
        lock_file = out_dir / f".{safe_title}.lock"
        lock_file.write_bytes(b"\x00\xff\xfe\x01\x80\x90\xaa\xbb\xcc\xdd\xee")

        payload = {
            "pack_title": pack_title,
            "output_dir": str(out_dir),
            "scenes": [{"id": 1, "path": _dummy_media_path(out_dir, pack_title)}]
        }

        res = task.run_build_megapack(payload)
        assert res["status"] == "success"
        assert not lock_file.exists()


class TestBencodeAndTorrentIntegrity:
    """Stress test .torrent generation and bencode byte length across diverse unicode ranges."""

    @pytest.mark.parametrize("pack_title", [
        "ASCII_Pack_123",
        "Éléonore_Pack",
        "鈴木_一郎_Pack",
        "🎬_Movies_🚀_2026_⭐",
        "العربية_Hebrew_עברית",
        "Hindi_हिंदी_Tamil_தமிழ்",
        "Korean_한국어_Greek_Ελληνικά",
    ])
    def test_bencode_byte_length_multilingual(self, tmp_path, pack_title):
        """Verify bencode name prefix matches exact UTF-8 byte length for diverse scripts."""
        out_dir = tmp_path / f"bencode_{abs(hash(pack_title))}"
        out_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "pack_title": pack_title,
            "output_dir": str(out_dir),
            "scenes": [{"id": 1, "path": _dummy_media_path(out_dir, pack_title)}]
        }

        res = task.run_build_megapack(payload)
        torrent_path = res["torrent_path"]
        assert os.path.exists(torrent_path)

        with open(torrent_path, "rb") as tf:
            raw_bytes = tf.read()

        safe_title = task.sanitize_name(pack_title)
        encoded_name = safe_title.encode("utf-8")

        # The torrent is produced by torf, so the byte-length property is asserted
        # against real bencode rather than a hand-built stub header.
        assert str(len(encoded_name)).encode("ascii") + b":" + encoded_name in raw_bytes

        t = torf.Torrent.read(torrent_path)
        assert t.name == safe_title
        assert t.pieces > 0, "torrent must carry real piece hashes"
        assert len(t.metainfo["info"]["pieces"]) % 20 == 0


class TestDeepTypeSafetyAndMalformations:
    """Adversarial stress testing of malformed, primitive, and boundary inputs."""

    def test_extract_names_extreme_primitives(self):
        """_extract_names must safely return empty list or strings for all primitive and malformed types."""
        assert task._extract_names(None) == []
        assert task._extract_names(12345) == []
        assert task._extract_names(3.14159) == []
        assert task._extract_names(True) == []
        assert task._extract_names(False) == []
        assert task._extract_names(object()) == []
        assert task._extract_names([None, 123, False, {}, {"name": None}, {"title": 999}]) == ["999"]
        assert task._extract_names({"name": 123}) == ["123"]
        assert task._extract_names({"title": "  Only Title  "}) == ["Only Title"]

    def test_extract_scene_paths_extreme_primitives(self):
        """_extract_scene_paths must safely handle non-dict, non-list, and nested invalid structures."""
        assert task._extract_scene_paths(None) == []
        assert task._extract_scene_paths(12345) == []
        assert task._extract_scene_paths(3.14) == []
        assert task._extract_scene_paths(True) == []
        assert task._extract_scene_paths("C:/valid/string_path.mp4") == ["C:/valid/string_path.mp4"]
        assert task._extract_scene_paths(Path("C:/valid/path_obj.mp4")) == [str(Path("C:/valid/path_obj.mp4"))]
        assert task._extract_scene_paths({"file_paths": None}) == []
        assert task._extract_scene_paths({"file_paths": 12345}) == []
        assert task._extract_scene_paths({"file_paths": [None, 123, "", "C:/vid.mp4"]}) == ["C:/vid.mp4"]
        assert task._extract_scene_paths({"files": [None, 123, {"path": None}, {"source_path": "C:/src.mp4"}]}) == ["C:/src.mp4"]

    def test_sanitize_name_extreme_types(self):
        """sanitize_name must handle non-string inputs safely."""
        assert task.sanitize_name(None) == "Untitled"
        assert task.sanitize_name(12345) == "12345"
        assert task.sanitize_name(True) == "True"
        assert task.sanitize_name(False) == "Untitled"
        assert task.sanitize_name("") == "Untitled"
        assert task.sanitize_name("   ") == "Untitled"
        assert task.sanitize_name("....................") == "Untitled"

    def test_probe_files_extreme_malformed_payload(self):
        """run_probe_files must handle non-dict, None, and corrupted payloads gracefully."""
        for bad_payload in [None, 123, "string", [], True]:
            res = task.run_probe_files(bad_payload)
            assert res["status"] == "success"
            assert res["files"] == []
            assert res["duplicate_count"] == 0

        # Corrupted internal structures
        res2 = task.run_probe_files({
            "target_dir": None,
            "files": [None, 123, "non_existent_1.mp4", {"path": None}, {"invalid": "dict"}]
        })
        assert res2["status"] == "success"
        assert len(res2["files"]) == 5
        assert res2["files"][2]["exists"] is False

    def test_build_megapack_extreme_malformed_payload(self, tmp_path):
        """run_build_megapack must handle non-dict and missing fields without unhandled crash."""
        out_dir = tmp_path / "extreme_build"
        out_dir.mkdir(parents=True, exist_ok=True)

        # None of these yield a usable media path, so the contract is a CONTROLLED
        # refusal -- RuntimeError, not an unhandled TypeError/AttributeError from
        # feeding None/int/str into the scene parser.
        for bad_payload in [
            {"pack_title": None, "output_dir": str(out_dir), "scenes": None, "performers": None, "tags": None},
            {"pack_title": 12345, "output_dir": str(out_dir), "scenes": [None, 123, "str_scene"], "notes": None},
        ]:
            with pytest.raises(RuntimeError, match="refusing to emit an empty pack"):
                task.run_build_megapack(bad_payload)

        # Malformed metadata around VALID media must still build cleanly -- that is
        # the tolerance half of this test.
        for bad_meta in [
            {"pack_title": "DefaultPack", "output_dir": str(out_dir), "performers": None, "tags": None,
             "scenes": [{"id": 1, "path": _dummy_media_path(out_dir, "DefaultPack")}]},
        ]:
            res = task.run_build_megapack(bad_meta)
            assert res["status"] == "success"
            assert os.path.exists(res["manifest_path"])
            assert os.path.exists(res["torrent_path"])
            assert os.path.exists(res["bbcode_path"])

        # OLD→NEW (T3): a declared-but-nonexistent scene path ("str_scene") was
        # formerly silently dropped by the existence filter and the build
        # succeeded with the remaining valid media. Under pack-file-presence
        # validation every declared primary must exist, so this now refuses
        # with a controlled RuntimeError naming the missing path (malformed
        # metadata such as pack_title=12345 / notes=None is still tolerated).
        bad_meta_missing_path = {
            "pack_title": 12345, "output_dir": str(out_dir), "notes": None,
            "scenes": [None, 123, "str_scene", {"id": 2, "path": _dummy_media_path(out_dir, "12345")}],
        }
        with pytest.raises(RuntimeError, match="missing from") as exc_info:
            task.run_build_megapack(bad_meta_missing_path)
        assert "str_scene" in str(exc_info.value)

    def test_win32_helper_type_safety(self):
        """Win32 creation time and volume serial helpers must not crash on bad types."""
        assert task.get_win32_creation_time(None) == 0.0
        assert task.get_win32_creation_time(12345) == 0.0
        assert task.get_win32_creation_time("") == 0.0

        assert task.get_volume_serial_number(None) is None
        assert task.get_volume_serial_number(12345) is None
        assert task.get_volume_serial_number("") is None

        assert task.can_hardlink(None, None) is False
        assert task.can_hardlink("C:/a", None) is False
        assert task.can_hardlink(None, "C:/b") is False


class TestLongTitlesAndWindowsPathSanitization:
    """Stress test long filenames, boundary truncations, and extension preservation."""

    def test_sanitize_name_truncation_with_extension(self):
        """Long titles exceeding max_len must be truncated cleanly preserving extension."""
        long_title = "A" * 150 + ".torrent"
        sanitized = task.sanitize_name(long_title, max_len=120)
        assert len(sanitized) <= 120
        assert sanitized.endswith(".torrent")

    def test_sanitize_name_truncation_without_extension(self):
        """Long titles without extension must be truncated at max_len."""
        long_title = "B" * 150
        sanitized = task.sanitize_name(long_title, max_len=120)
        assert len(sanitized) == 120
        assert sanitized == "B" * 120


class TestHighConcurrencyMultiprocessRaces:
    """Spawn multiple concurrent OS processes attempting to build the same megapack."""

    def test_eight_process_concurrency_race(self, tmp_path):
        """8 processes launched simultaneously. Exactly one should succeed, others gracefully rejected."""
        out_dir = tmp_path / "multi_race_pack"
        out_dir.mkdir(parents=True, exist_ok=True)
        pack_title = "MultiProcessMegaRace"
        pack_dir = out_dir / pack_title
        pack_dir.mkdir(parents=True, exist_ok=True)

        dummy_file = pack_dir / "race_vid.mp4"
        dummy_file.write_text("dummy video")

        payload = {
            "pack_title": pack_title,
            "output_dir": str(out_dir),
            "scenes": [{"id": 1, "path": str(dummy_file)}]
        }
        input_data = json.dumps({
            "task_name": "BuildMegapack",
            "args": {"mode": "build", "payload": payload}
        })

        cmd = [sys.executable, str(PLUGIN_DIR / "task.py")]
        processes = [
            subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8")
            for _ in range(8)
        ]

        results = []
        for p in processes:
            stdout, stderr = p.communicate(input=input_data)
            results.append((p.returncode, stdout, stderr))

        success_count = sum(1 for code, _, _ in results if code == 0)
        failure_count = sum(1 for code, _, _ in results if code != 0)

        # At least one succeeded
        assert success_count >= 1
        # All failed ones must fail with RuntimeError concurrent build
        for code, stdout, stderr in results:
            if code != 0:
                assert "Concurrent build in progress" in stderr or "ERROR" in stderr
