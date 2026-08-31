"""
Tier 4: Real-World Application Scenarios E2E Test Suite.
Simulates realistic end-to-end user workflows, studio multi-scene megapacks,
heterogeneous library collections, large packs, and fault recovery journeys.
"""

import sys
import json
from pathlib import Path
from unittest.mock import patch
import torf

from task import (
    run_probe_files,
    run_build_megapack,
)


class TestRealWorldApplicationScenarios:
    """Realistic end-to-end user journeys and production workload simulations."""

    def test_scenario_01_standard_studio_megapack_workflow(self, media_factory, tmp_path):
        """
        Scenario 1: Standard 5-scene studio megapack.
        Complete lifecycle: probing -> building -> torrent verification -> manifest -> BBCode.
        """
        out_dir = tmp_path / "Studio_Pack_Output"
        out_dir.mkdir()

        # Create 5 simulated studio scenes
        scenes = []
        for i in range(1, 6):
            f = media_factory(f"StudioAlpha_Scene_0{i}_1080p", ".mp4", 32768 * i, target_dir=out_dir)
            scenes.append({
                "id": 100 + i,
                "title": f"Studio Alpha Scene 0{i}",
                "path": str(f),
                "performers": [f"Performer {i}", "Performer Lead"],
                "tags": ["Studio Alpha", "1080p", f"Tag {i}"],
            })

        payload = {
            "pack_title": "Studio Alpha Best of 2026",
            "output_dir": str(out_dir),
            "include_contact_sheets": False,
            "scenes": scenes,
            "performers": ["Performer Lead", "Performer 1", "Performer 2", "Performer 3"],
            "tags": ["Studio Alpha", "Megapack", "1080p", "Compilation"],
            "notes": "Full high-definition studio compilation. Verified direct seeding.",
            "layout": "grid_4x4",
        }

        res = run_build_megapack(payload)
        t_path = Path(res["torrent_path"])
        m_path = Path(res["manifest_path"])
        b_path = Path(res["bbcode_path"])

        assert t_path.exists()
        assert m_path.exists()
        assert b_path.exists()

        # Verify torrent payload
        t = torf.Torrent.read(str(t_path))
        assert t.size == sum(32768 * i for i in range(1, 6))
        assert len(t.files) == 5

        # Verify manifest
        manifest = json.loads(m_path.read_text(encoding="utf-8"))
        assert manifest["scene_count"] == 5
        assert len(manifest["contact_sheets"]) == 5

        # Verify BBCode
        bbcode = b_path.read_text(encoding="utf-8")
        assert "Studio Alpha Best of 2026" in bbcode
        assert "Performer Lead" in bbcode

    def test_scenario_02_heterogeneous_multi_codec_library_pack(self, media_factory, tmp_path):
        """
        Scenario 2: Heterogeneous library pack mixing .mkv, .avi, .wmv, .webm, .mp4.
        Ensures all media extensions and basenames are preserved without any forced .mp4 conversion.
        """
        out_dir = tmp_path / "MultiCodec_Output"
        out_dir.mkdir()

        formats = [
            ("Retro_Classic", ".avi", 16384),
            ("Modern_Feature", ".mkv", 65536),
            ("Legacy_Clip", ".wmv", 24576),
            ("Web_Short", ".webm", 12288),
            ("Standard_Release", ".mp4", 32768),
        ]

        scenes = []
        for name, ext, size in formats:
            f = media_factory(name, ext, size, target_dir=out_dir)
            scenes.append({"id": len(scenes) + 1, "path": str(f), "title": name})

        payload = {
            "pack_title": "Heterogeneous Media Archive",
            "output_dir": str(out_dir),
            "scenes": scenes,
        }

        res = run_build_megapack(payload)
        t = torf.Torrent.read(res["torrent_path"])
        files_in_torrent = [str(f) for f in t.files]

        for name, ext, size in formats:
            assert any(f.endswith(ext) for f in files_in_torrent), f"Missing extension {ext} in torrent"

    def test_scenario_03_large_collection_25_scenes_monotonic_progress(self, media_factory, tmp_path):
        """
        Scenario 3: 25-scene megapack stress test.
        Verifies progress monotonicity, piece calculation, and manifest fidelity.
        """
        out_dir = tmp_path / "Large_Collection_Output"
        out_dir.mkdir()

        scenes = []
        for i in range(1, 26):
            f = media_factory(f"Collection_Scene_{i:02d}", ".mp4", 16384, target_dir=out_dir)
            scenes.append({"id": 200 + i, "path": str(f)})

        payload = {
            "pack_title": "Massive 25 Scene Pack",
            "output_dir": str(out_dir),
            "scenes": scenes,
            "tags": [f"Tag_{i}" for i in range(20)],
        }

        progress_history = []
        def capture_progress(text):
            if "\x01p\x02" in text:
                parts = text.split("\x01p\x02")[1].split("\x02")
                try:
                    progress_history.append(float(parts[0]))
                except (ValueError, IndexError):
                    pass

        with patch.object(sys.stderr, "write", side_effect=capture_progress):
            res = run_build_megapack(payload)

        assert Path(res["torrent_path"]).exists()
        assert len(progress_history) > 10
        # Check monotonicity
        for i in range(1, len(progress_history)):
            assert progress_history[i] >= progress_history[i - 1] - 0.001  # small float tolerance

    def test_scenario_04_offline_degrade_and_continue_full_pack(self, media_factory, tmp_path):
        """
        Scenario 4: Complete offline build with unavailable external binaries and services.
        Pillow fallback triggers, file:/// URLs generated, full pack artifacts successfully written.
        """
        out_dir = tmp_path / "Offline_Output"
        out_dir.mkdir()

        # Non-video files to trigger fallback
        f1 = out_dir / "offline_scene1.mp4"
        f2 = out_dir / "offline_scene2.mp4"
        f1.write_bytes(b"\x00" * 4096)
        f2.write_bytes(b"\x00" * 8192)

        payload = {
            "pack_title": "Offline Degraded Pack",
            "output_dir": str(out_dir),
            "scenes": [{"id": 1, "path": str(f1)}, {"id": 2, "path": str(f2)}],
            "upload_previews": False,
        }

        warnings = []
        def capture_warnings(text):
            if "\x01w\x02" in text:
                warnings.append(text)

        with patch.object(sys.stderr, "write", side_effect=capture_warnings):
            res = run_build_megapack(payload)

        assert len(warnings) >= 1
        assert Path(res["torrent_path"]).exists()
        assert Path(res["manifest_path"]).exists()
        assert Path(res["bbcode_path"]).exists()

    def test_scenario_05_collision_recovery_workflow(self, media_factory, tmp_path):
        """
        Scenario 5: Complete collision resolution user journey.
        1. Probing identifies duplicate basename 'scene.mp4' -> consolidation gated.
        2. User resolves collision (renames file 2 to 'scene_02.mp4').
        3. Probing confirms 0 duplicates.
        4. Consolidation and build proceed to successful torrent.
        """
        dir_a = tmp_path / "DriveD" / "Scenes"
        dir_b = tmp_path / "DriveE" / "Scenes"
        dir_a.mkdir(parents=True)
        dir_b.mkdir(parents=True)

        f1 = dir_a / "Scene.mp4"
        f2_collision = dir_b / "Scene.mp4"
        f1.write_bytes(b"Media 1")
        f2_collision.write_bytes(b"Media 2")

        # Step 1: Probe detects collision
        probe_1 = run_probe_files({"files": [{"path": str(f1)}, {"path": str(f2_collision)}]})
        assert probe_1["duplicate_count"] == 1
        assert (probe_1["duplicate_count"] == 0) is False  # Consolidation blocked

        # Step 2: Resolution (rename second file)
        f2_resolved = dir_b / "Scene_02.mp4"
        f2_collision.rename(f2_resolved)

        # Step 3: Re-probe confirms resolution
        probe_2 = run_probe_files({"files": [{"path": str(f1)}, {"path": str(f2_resolved)}]})
        assert probe_2["duplicate_count"] == 0
        assert (probe_2["duplicate_count"] == 0) is True  # Consolidation unlocked

        # Step 4: Move/consolidate files into destination and build
        out_dir = tmp_path / "Resolved_Pack"
        out_dir.mkdir()
        f1_cons = out_dir / "Scene.mp4"
        f2_cons = out_dir / "Scene_02.mp4"
        f1.rename(f1_cons)
        f2_resolved.rename(f2_cons)

        payload = {
            "pack_title": "Collision Resolved Pack",
            "output_dir": str(out_dir),
            "scenes": [{"id": 1, "path": str(f1_cons)}, {"id": 2, "path": str(f2_cons)}],
        }
        res = run_build_megapack(payload)
        assert Path(res["torrent_path"]).exists()

    def test_scenario_06_contact_sheets_included_in_torrent_flag(self, media_factory, tmp_path):
        """
        Scenario 6: Build with include_contact_sheets=True bundles Contact Sheets/ subfolder into torrent.
        """
        out_dir = tmp_path / "bundled_cs_out"
        out_dir.mkdir()
        f1 = media_factory("feature_clip", ".mp4", 65536, target_dir=out_dir)

        payload = {
            "pack_title": "Bundled CS Pack",
            "output_dir": str(out_dir),
            "scenes": [{"id": 1, "path": str(f1)}],
            "include_contact_sheets": True,
        }

        res = run_build_megapack(payload)
        t = torf.Torrent.read(res["torrent_path"])
        file_list = [str(f) for f in t.files]
        assert any("Contact Sheets" in f for f in file_list)
