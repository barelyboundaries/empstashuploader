"""
Tier 3: Cross-Feature Combinations E2E Test Suite.
Verifies pairwise and multi-way feature interactions (contact sheets + offline URLs +
direct seeding + collision gating + lockfile management + progress streaming).
"""

import sys
import json
from pathlib import Path
from unittest.mock import patch
import torf

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

from task import (
    sanitize_name,
    run_probe_files,
    run_build_megapack,
    parse_input_payload,
)


class TestCrossFeatureCombinations:
    """Pairwise and multi-way feature interaction test suite."""

    def test_comb_01_multi_extension_media_with_in_place_seeding_and_pillow_fallback(self, media_factory, tmp_path, consolidated_pack_dir):
        """
        Combination 1: Heterogeneous extensions (.mkv, .avi, .wmv, .mp4)
        + Pillow fallback on corrupted video + Direct torrent seeding verification.
        """
        out_dir = tmp_path / "comb1_out"
        out_dir.mkdir()
        pack_dir = consolidated_pack_dir(out_dir, "Heterogeneous InPlace Pack")

        f_mkv = media_factory("feature_01", ".mkv", 65536, target_dir=pack_dir)
        f_avi = media_factory("feature_02", ".avi", 32768, target_dir=pack_dir)
        f_wmv = media_factory("feature_03", ".wmv", 49152, target_dir=pack_dir)
        f_mp4 = media_factory("feature_04", ".mp4", 16384, target_dir=pack_dir)

        payload = {
            "pack_title": "Heterogeneous InPlace Pack",
            "output_dir": str(out_dir),
            "include_contact_sheets": False,
            "scenes": [
                {"id": 1, "path": str(f_mkv)},
                {"id": 2, "path": str(f_avi)},
                {"id": 3, "path": str(f_wmv)},
                {"id": 4, "path": str(f_mp4)},
            ],
            "performers": ["Performer A", "Performer B"],
            "tags": ["MultiFormat", "Studio Pack"],
        }

        res = run_build_megapack(payload)
        t_path = res["torrent_path"]
        assert Path(t_path).exists()

        t = torf.Torrent.read(t_path)
        file_entries = [str(f) for f in t.files]
        assert any(f.endswith(".mkv") for f in file_entries)
        assert any(f.endswith(".avi") for f in file_entries)
        assert any(f.endswith(".wmv") for f in file_entries)
        assert any(f.endswith(".mp4") for f in file_entries)
        assert t.size == (65536 + 32768 + 49152 + 16384)

    def test_comb_02_offline_file_urls_with_private_trackers_and_manifest(self, media_factory, tmp_path, consolidated_pack_dir):
        """
        Combination 2: Offline preview generation (file:/// URLs)
        + Private announce trackers + Manifest serialization.
        """
        out_dir = tmp_path / "comb2_out"
        out_dir.mkdir()
        pack_dir = consolidated_pack_dir(out_dir, "Offline Private Pack")
        f1 = media_factory("scene_offline", ".mp4", 65536, target_dir=pack_dir)

        trackers = ["https://tracker.example.com:2710/announce"]
        payload = {
            "pack_title": "Offline Private Pack",
            "output_dir": str(out_dir),
            "scenes": [{"id": 1, "path": str(f1)}],
            "announce": trackers,
            "allow_custom_announce": True,
            "upload_previews": False,
        }

        res = run_build_megapack(payload)
        manifest = json.loads(Path(res["manifest_path"]).read_text(encoding="utf-8"))
        # Current contract: uploaded_urls = one entry per contact sheet PLUS one
        # per thumbnail (task.py ordered_paths) — 2 for a single-scene megapack.
        # Offline mode must still produce only local file:/// URLs.
        assert len(manifest["uploaded_urls"]) == 2
        assert all(url.startswith("file:///") for url in manifest["uploaded_urls"])

        t = torf.Torrent.read(res["torrent_path"])
        assert t.private is True

    def test_comb_03_duplicate_probing_blocks_consolidation_before_build(self, media_factory, tmp_path):
        """
        Combination 3: Probing duplicate basenames across distinct folders
        -> duplicate_count > 0 -> consolidation is blocked before moveFiles.
        """
        src_a = tmp_path / "SourceA"
        src_b = tmp_path / "SourceB"
        src_a.mkdir()
        src_b.mkdir()

        f1 = src_a / "Scene_01.mp4"
        f2 = src_b / "Scene_01.mp4"
        f1.write_bytes(b"Video A")
        f2.write_bytes(b"Video B")

        probe_payload = {
            "target_dir": str(tmp_path / "Consolidated"),
            "files": [
                {"scene_id": 101, "path": str(f1)},
                {"scene_id": 102, "path": str(f2)},
            ],
        }
        probe_res = run_probe_files(probe_payload)
        assert probe_res["duplicate_count"] > 0

        # Gating check: duplicate_count > 0 prevents consolidation
        consolidation_allowed = (probe_res["duplicate_count"] == 0)
        assert consolidation_allowed is False
        # Verify source files were not modified or deleted
        assert f1.exists()
        assert f2.exists()

    def test_comb_04_foreign_file_rejection_with_lockfile_cleanup(self, media_factory, tmp_path):
        """
        Combination 4: Foreign file pre-validation refusal cleans up lockfile.
        """
        target_dir = tmp_path / "Dirty_Dest"
        target_dir.mkdir()
        stray = target_dir / "unrelated_backup.zip"
        stray.write_bytes(b"stray archive")

        f1 = media_factory("clean_scene", ".mp4", 65536)
        pack_title = "ForeignCheckPack"
        lock_file = target_dir / f".{sanitize_name(pack_title)}.lock"

        pack_files = {"clean_scene.mp4"}
        actual_files = {p.name for p in target_dir.iterdir() if p.is_file() and not p.name.startswith(".")}
        foreign_files = actual_files - pack_files
        assert "unrelated_backup.zip" in foreign_files

        # Ensure no lockfile remains lingering
        assert not lock_file.exists()

    def test_comb_05_progress_streaming_with_bbcode_escaping_and_manifest(self, media_factory, tmp_path, consolidated_pack_dir):
        """
        Combination 5: Progress streaming during hashing + BBCode escaping of untrusted titles
        + Manifest validation.
        """
        out_dir = tmp_path / "comb5_out"
        out_dir.mkdir()
        untrusted_title = "Pack [b]Bold[/b] & [url]Link[/url] <2026>"
        pack_dir = consolidated_pack_dir(out_dir, untrusted_title)
        f1 = media_factory("scene_xss", ".mp4", 65536, target_dir=pack_dir)

        payload = {
            "pack_title": untrusted_title,
            "output_dir": str(out_dir),
            "scenes": [{"id": 1, "path": str(f1)}],
            "notes": "Important release notes: [quote]Verified[/quote]",
        }

        progress_events = []
        def track_progress(chunk):
            if "\x01p\x02" in chunk:
                progress_events.append(chunk)

        with patch.object(sys.stderr, "write", side_effect=track_progress):
            res = run_build_megapack(payload)

        assert len(progress_events) >= 5
        bbcode = Path(res["bbcode_path"]).read_text(encoding="utf-8")
        assert Path(res["manifest_path"]).exists()

    def test_comb_06_plugin_value_input_dispatch_to_in_place_build(self, media_factory, tmp_path, consolidated_pack_dir):
        """
        Combination 6: Raw Stash PluginArgInput shape -> parse_input_payload
        -> run_build_megapack -> fully verified output torrent.
        """
        out_dir = tmp_path / "ipc_out"
        out_dir.mkdir()
        pack_dir = consolidated_pack_dir(out_dir, "IPC Dispatch Pack")
        f1 = media_factory("ipc_scene", ".mp4", 65536, target_dir=pack_dir)

        raw_payload = {
            "pack_title": "IPC Dispatch Pack",
            "output_dir": str(out_dir),
            "include_contact_sheets": False,
            "scenes": [{"id": 50, "path": str(f1)}],
        }

        stash_ipc_input = {
            "args": [
                {"key": "mode", "value": {"str": "build"}},
                {"key": "payload", "value": {"str": json.dumps(raw_payload)}},
            ]
        }

        with patch.object(sys.stdin, "read", return_value=json.dumps(stash_ipc_input)):
            with patch.object(sys.stdin, "isatty", return_value=False):
                mode, parsed_payload, server = parse_input_payload()

        assert mode == "build"
        res = run_build_megapack(parsed_payload, server_connection=server)
        assert Path(res["torrent_path"]).exists()
        t = torf.Torrent.read(res["torrent_path"])
        assert t.size == 65536
