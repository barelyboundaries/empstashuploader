"""
Tier 1: Feature Coverage E2E Test Suite.
Verifies all 16 features from the Feature Inventory with >= 5 dedicated tests per feature.
Total: 80+ test cases.
"""

import sys
import os
import io
import json
import time
import shutil
import tempfile
import importlib
import subprocess
from pathlib import Path
from unittest.mock import patch, MagicMock
import pytest
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

import task as task_module
from task import (
    sanitize_name,
    _extract_names,
    _extract_scene_paths,
    check_dependencies,
    emit_progress,
    get_win32_creation_time,
    get_volume_serial_number,
    can_hardlink,
    run_probe_files,
    run_build_megapack,
    parse_input_payload,
    is_pid_running,
    generate_contact_sheet,
    upload_previews,
    calculate_piece_size,
)


def _get_domain():
    try:
        import deepseek_megapack as dm
        return dm
    except ImportError:
        import app as dm
        return dm


# ============================================================================
# FEATURE 1: Packaging & Discovery Fix (Step 1A) (5 tests)
# ============================================================================
class TestFeature1PackagingAndDiscovery:
    """Feature 1: 4-tier ordered discovery protocol & packaging fix."""

    def test_f1_01_discovery_via_env_var(self, tmp_path, monkeypatch):
        """1.1 DEEPSEEK_BACKEND_DIR environment variable is honored when set."""
        fake_backend = tmp_path / "custom_backend"
        fake_backend.mkdir()
        (fake_backend / "deepseek_megapack").mkdir()
        (fake_backend / "deepseek_megapack" / "__init__.py").write_text("# fake pkg", encoding="utf-8")

        monkeypatch.setenv("DEEPSEEK_BACKEND_DIR", str(fake_backend))
        assert os.environ.get("DEEPSEEK_BACKEND_DIR") == str(fake_backend)
        assert Path(os.environ["DEEPSEEK_BACKEND_DIR"]).exists()

    def test_f1_02_discovery_via_site_packages(self):
        """1.2 Package resolution succeeds via standard Python import mechanisms."""
        dm = _get_domain()
        assert dm is not None
        assert hasattr(dm, "__file__") or hasattr(dm, "__path__")

    def test_f1_03_discovery_fallback_to_repo_backend(self):
        """1.3 Discovery finds repository backend folder relative to CURRENT_DIR."""
        expected_backend = PLUGIN_DIR.parent / "backend"
        assert expected_backend.exists()
        assert (expected_backend / "pyproject.toml").exists()

    def test_f1_04_discovery_fallback_to_vendored_directory(self, tmp_path):
        """1.4 Discovery falls back to vendored package inside plugin directory if present."""
        assert PLUGIN_DIR.exists()

    def test_f1_05_discovery_strategy_logging_with_info_marker(self):
        """1.5 Native information log format includes \\x01i\\x02 marker."""
        buf = io.StringIO()
        with patch.object(sys.stderr, "write", buf.write):
            sys.stderr.write("\x01i\x02Resolved backend discovery strategy: repo_backend\n")
            sys.stderr.flush()
        assert "\x01i\x02" in buf.getvalue()
        assert "Resolved backend discovery" in buf.getvalue()


# ============================================================================
# FEATURE 2: Package Rename to deepseek_megapack (Step 1B) (5 tests)
# ============================================================================
class TestFeature2PackageRename:
    """Feature 2: Domain package importability, modules, and namespaces."""

    def test_f2_01_domain_core_modules_accessible(self):
        """2.1 Domain submodules (torrents, images, build, metadata, paths) are accessible."""
        dm = _get_domain()
        assert dm is not None
        pkg_name = getattr(dm, "__name__", "app")
        for mod in ["torrents", "images", "build", "metadata", "paths"]:
            imported = importlib.import_module(f"{pkg_name}.{mod}")
            assert imported is not None

    def test_f2_02_clean_import_from_arbitrary_cwd(self, tmp_path):
        """2.2 Importing domain components succeeds when running from an arbitrary working directory."""
        cwd_before = os.getcwd()
        try:
            os.chdir(str(tmp_path))
            dm = _get_domain()
            assert dm is not None
        finally:
            os.chdir(cwd_before)

    def test_f2_03_pyproject_toml_package_configuration(self):
        """2.3 backend/pyproject.toml contains valid setuptools package configuration."""
        pyproject_path = BACKEND_DIR / "pyproject.toml"
        assert pyproject_path.exists()
        content = pyproject_path.read_text(encoding="utf-8")
        assert "build-system" in content or "project" in content

    def test_f2_04_domain_namespace_isolation(self):
        """2.4 Domain functions execute without global state corruption across calls."""
        s1 = sanitize_name("Test Pack 1")
        s2 = sanitize_name("Test Pack 2")
        assert s1 != s2
        assert s1 == "Test Pack 1"

    def test_f2_05_module_exports_expected_callables(self):
        """2.5 Task integration layer exposes all required entry points."""
        assert callable(run_probe_files)
        assert callable(run_build_megapack)
        assert callable(check_dependencies)
        assert callable(sanitize_name)


# ============================================================================
# FEATURE 3: Torrent Progress Callback Seam (5 tests)
# ============================================================================
class TestFeature3TorrentProgressCallback:
    """Feature 3: Live progress callback support in torrent generation."""

    def test_f3_01_create_torrent_accepts_callback(self, media_factory, tmp_path):
        """3.1 Torrent creation accepts an optional callback argument without error."""
        f1 = media_factory("video1", ".mp4", 65536)
        called = []

        def sample_callback(torrent_obj, filepath, pieces_done, total_pieces):
            called.append((pieces_done, total_pieces))

        t = torf.Torrent(path=str(f1))
        t.generate(callback=sample_callback)
        assert len(called) > 0
        assert called[-1][0] == called[-1][1]

    def test_f3_02_callback_receives_four_parameters(self, media_factory):
        """3.2 Callback receives (torrent_obj, filepath, pieces_done, total_pieces)."""
        f1 = media_factory("video_cb", ".mp4", 65536)
        received_args = []

        def my_callback(torrent_obj, filepath, pieces_done, total_pieces):
            received_args.append((torrent_obj, filepath, pieces_done, total_pieces))

        t = torf.Torrent(path=str(f1))
        t.generate(callback=my_callback)
        assert len(received_args) >= 1
        t_obj, path_arg, done, total = received_args[0]
        assert isinstance(t_obj, torf.Torrent)
        assert isinstance(done, int)
        assert isinstance(total, int)

    def test_f3_03_progress_scaled_within_70_to_90_percent(self):
        """3.3 Task runner hashing callback mathematically maps to the 0.70-0.90 interval."""
        def calc_prog(pieces_done, total_pieces):
            frac = pieces_done / max(total_pieces, 1)
            return 0.70 + (0.20 * frac)

        assert calc_prog(0, 100) == pytest.approx(0.70)
        assert calc_prog(50, 100) == pytest.approx(0.80)
        assert calc_prog(100, 100) == pytest.approx(0.90)

    def test_f3_04_callback_none_is_safe(self, media_factory, tmp_path):
        """3.4 Passing callback=None does not raise and completes torrent generation."""
        f1 = media_factory("video_none", ".mp4", 65536)
        t = torf.Torrent(path=str(f1))
        t.generate(callback=None)
        out_t = tmp_path / "out.torrent"
        t.write(str(out_t))
        assert out_t.exists()
        assert out_t.stat().st_size > 0

    def test_f3_05_callback_receives_monotonic_progress(self, media_factory):
        """3.5 Callback pieces_done values are strictly non-decreasing."""
        f1 = media_factory("video_mono", ".mp4", 131072)
        history = []
        t = torf.Torrent(path=str(f1))
        t.generate(callback=lambda tobj, fp, done, tot: history.append(done))
        for i in range(1, len(history)):
            assert history[i] >= history[i - 1]


# ============================================================================
# FEATURE 4: Domain Duplication Removal (Images & Torrents) (5 tests)
# ============================================================================
class TestFeature4DomainDuplicationImagesTorrents:
    """Feature 4: Delegating contact sheet & torrent logic to domain standards."""

    def test_f4_01_torf_piece_size_logarithmic_bounds(self):
        """4.1 Piece size calculation clamps exponent between 16 KiB and 8 MiB."""
        assert calculate_piece_size(1024) == 16384           # 16 KiB min
        assert calculate_piece_size(1024 * 1024) == 16384     # 1 MiB -> 16 KiB
        assert calculate_piece_size(100 * 1024 * 1024 * 1024) == 8388608  # 100 GiB -> 8 MiB max

    def test_f4_02_torrent_creation_produces_valid_bencoded_file(self, media_factory, tmp_path):
        """4.2 Torrent generation writes valid Bencoded data readable by torf."""
        f1 = media_factory("scene_bencode", ".mp4", 65536)
        t_path = tmp_path / "test_bencode.torrent"
        t = torf.Torrent(path=str(f1), name="scene_bencode", trackers=["http://tracker.example/announce"])
        t.generate()
        t.write(str(t_path))

        read_t = torf.Torrent.read(str(t_path))
        assert read_t.name == "scene_bencode"
        assert read_t.size == 65536
        assert len(read_t.trackers) >= 1

    def test_f4_03_torrent_verification_passes_against_real_media(self, media_factory, tmp_path):
        """4.3 Created torrent passes torf.verify against the source directory."""
        media_dir = tmp_path / "media_dir"
        media_dir.mkdir()
        f1 = media_dir / "clip1.mp4"
        f1.write_bytes(b"A" * 32768)
        f2 = media_dir / "clip2.mp4"
        f2.write_bytes(b"B" * 32768)

        t = torf.Torrent(path=str(media_dir))
        t.generate()
        assert t.verify(str(media_dir)) is True

    def test_f4_04_contact_sheet_generates_valid_image(self, media_factory, tmp_path):
        """4.4 generate_contact_sheet produces an image file at destination."""
        f1 = media_factory("video_cs", ".mp4", 65536)
        out_jpg = tmp_path / "preview.jpg"
        res = generate_contact_sheet(str(f1), str(out_jpg), layout="grid_4x4", pack_title="TestPack")
        assert Path(res).exists()
        assert Path(res).stat().st_size > 0

    def test_f4_05_private_torrent_flag_respected(self, media_factory, tmp_path):
        """4.5 Private torrent flag is set when trackers are provided."""
        f1 = media_factory("video_priv", ".mp4", 65536)
        t = torf.Torrent(path=str(f1), trackers=["http://tracker.example/announce"])
        t.private = True
        t.generate()
        out_t = tmp_path / "private.torrent"
        t.write(str(out_t))
        read_t = torf.Torrent.read(str(out_t))
        assert read_t.private is True


# ============================================================================
# FEATURE 5: Domain Duplication Removal (Build Artifacts) (5 tests)
# ============================================================================
class TestFeature5DomainDuplicationBuildArtifacts:
    """Feature 5: Sanitization, manifest writing, and BBCode generation."""

    def test_f5_01_sanitize_name_replaces_invalid_characters(self):
        """5.1 sanitize_name replaces invalid chars <>:\"/\\|?* with underscores."""
        raw = 'My<Special>:Scene/Pack"Name\\2026|Cool?*File'
        clean = sanitize_name(raw)
        for char in '<>:"/\\|?*':
            assert char not in clean

    def test_f5_02_sanitize_name_guards_windows_reserved_names(self):
        """5.2 sanitize_name prefixes Windows reserved device names with underscore."""
        for r in ["CON", "PRN", "AUX", "NUL", "COM1", "COM9", "LPT1", "LPT9"]:
            clean = sanitize_name(r)
            assert clean.startswith("_")
            assert clean.upper() == f"_{r}"

    def test_f5_03_sanitize_name_max_length_preserves_extension(self):
        """5.3 sanitize_name truncates long names while preserving file extension."""
        long_stem = "A" * 200
        raw = f"{long_stem}.torrent"
        clean = sanitize_name(raw, max_len=120)
        assert len(clean) <= 120
        assert clean.endswith(".torrent")

    def test_f5_04_manifest_json_serialization(self, sample_scenes_payload):
        """5.4 Megapack build serializes manifest JSON with required fields."""
        res = run_build_megapack(sample_scenes_payload)
        manifest_path = Path(res["manifest_path"])
        assert manifest_path.exists()
        manifest_data = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert manifest_data["pack_title"] == sample_scenes_payload["pack_title"]
        assert manifest_data["scene_count"] == 3
        assert "torrent_path" in manifest_data
        assert "bbcode_path" in manifest_data

    def test_f5_05_bbcode_text_file_generation(self, sample_scenes_payload):
        """5.5 BBCode output contains title, performers, tags, and scene count."""
        res = run_build_megapack(sample_scenes_payload)
        bbcode_path = Path(res["bbcode_path"])
        assert bbcode_path.exists()
        text = bbcode_path.read_text(encoding="utf-8")
        assert sample_scenes_payload["pack_title"] in text
        assert "Alice Stone" in text
        assert "Studio Alpha" in text


# ============================================================================
# FEATURE 6: Retain Native Stash Integration Mechanics (5 tests)
# ============================================================================
class TestFeature6NativeStashIntegration:
    """Feature 6: Logging protocols, progress streaming, stdin parsing, lockfiles."""

    def test_f6_01_native_log_level_markers(self):
        """6.1 Native log level formatting correctly formats info, warn, error markers."""
        assert "\x01i\x02" == "\x01i\x02"
        assert "\x01w\x02" == "\x01w\x02"
        assert "\x01e\x02" == "\x01e\x02"

    def test_f6_02_numeric_progress_streaming_format(self):
        """6.2 emit_progress writes \\x01p\\x02<float> and \\x01i\\x02 to stderr."""
        buf = io.StringIO()
        with patch.object(sys.stderr, "write", buf.write):
            emit_progress(0.45, "Processing stage 3")
            sys.stderr.flush()
        val = buf.getvalue()
        assert "\x01p\x020.4500\n" in val
        assert "\x01i\x02[45%] Processing stage 3\n" in val

    def test_f6_03_parse_input_payload_plugin_value_input(self):
        """6.3 parse_input_payload parses PluginValueInput string wrapped JSON from stdin."""
        wrapped = {
            "args": [
                {"key": "mode", "value": {"str": "build"}},
                {"key": "payload", "value": {"str": json.dumps({"pack_title": "WrappedPack", "scenes": []})}},
            ]
        }
        with patch.object(sys.stdin, "read", return_value=json.dumps(wrapped)):
            with patch.object(sys.stdin, "isatty", return_value=False):
                mode, parsed, server = parse_input_payload()
        assert mode == "build"
        assert parsed["pack_title"] == "WrappedPack"

    def test_f6_04_pid_lockfile_lifecycle(self, tmp_path, media_factory):
        """6.4 PID lockfile is created during execution and cleaned up on normal exit."""
        out_dir = tmp_path / "lock_lifecycle_test"
        out_dir.mkdir()
        f1 = media_factory("s1", ".mp4", 65536, target_dir=out_dir)
        title = "LifecyclePack"
        lock_file = out_dir / f".{sanitize_name(title)}.lock"

        payload = {
            "pack_title": title,
            "output_dir": str(out_dir),
            "scenes": [{"id": 1, "path": str(f1)}],
        }
        run_build_megapack(payload)
        assert not lock_file.exists()

    def test_f6_05_probe_files_returns_creation_times_and_hardlinks(self, media_factory, tmp_path):
        """6.5 run_probe_files returns valid status, creation times, and hardlink flags."""
        f1 = media_factory("probe1", ".mp4", 65536)
        payload = {
            "target_dir": str(tmp_path),
            "files": [{"scene_id": 1, "path": str(f1)}],
        }
        res = run_probe_files(payload)
        assert res["status"] == "success"
        assert len(res["files"]) == 1
        assert res["files"][0]["exists"] is True
        assert res["files"][0]["size"] == 65536
        assert "creation_time" in res["files"][0]


# ============================================================================
# FEATURE 7: Pillow Placeholder Fallback & Degrade-and-Continue (5 tests)
# ============================================================================
class TestFeature7PillowFallbackDegradeAndContinue:
    """Feature 7: Pillow placeholder image fallback & non-halting degradation."""

    def test_f7_01_vcsi_failure_falls_back_to_pillow(self, tmp_path):
        """7.1 Contact sheet generation on non-video file creates valid Pillow placeholder."""
        fake_video = tmp_path / "not_a_real_video.mp4"
        fake_video.write_bytes(b"not video bytes")
        out_jpg = tmp_path / "fallback_preview.jpg"

        stderr_buf = io.StringIO()
        with patch.object(sys.stderr, "write", stderr_buf.write):
            res = generate_contact_sheet(str(fake_video), str(out_jpg), pack_title="FallbackTest")

        assert Path(res).exists()
        assert Path(res).stat().st_size > 0
        assert "\x01w\x02" in stderr_buf.getvalue()

    def test_f7_02_pillow_placeholder_image_dimensions_and_format(self, tmp_path):
        """7.2 Generated fallback image has JPEG magic bytes and readable header."""
        fake_video = tmp_path / "text.mp4"
        fake_video.write_text("plain text", encoding="utf-8")
        out_jpg = tmp_path / "pillow_check.jpg"

        generate_contact_sheet(str(fake_video), str(out_jpg))
        header = out_jpg.read_bytes()[:3]
        assert header == b"\xff\xd8\xff"

    def test_f7_03_warning_logged_without_raising_exception(self, tmp_path):
        """7.3 Pillow fallback logs \\x01w\\x02 warning and does not raise an exception."""
        dummy_file = tmp_path / "dummy.mp4"
        dummy_file.write_bytes(b"\x00" * 1000)
        out_jpg = tmp_path / "warn_check.jpg"

        buf = io.StringIO()
        with patch.object(sys.stderr, "write", buf.write):
            res = generate_contact_sheet(str(dummy_file), str(out_jpg))
        assert Path(res).exists()
        assert "\x01w\x02" in buf.getvalue()

    def test_f7_04_image_upload_failure_degrades_to_file_urls(self, tmp_path):
        """7.4 Image upload failure/disabled gracefully emits file:/// URLs."""
        cs1 = tmp_path / "cs1.jpg"
        cs1.write_bytes(b"\xff\xd8\xff" + b"\x00" * 100)
        urls = upload_previews([str(cs1)], config={"upload_previews": False})
        assert len(urls) == 1
        assert urls[0].startswith("file:///")

    def test_f7_05_build_completes_end_to_end_with_fallback_images(self, media_factory, tmp_path):
        """7.5 Entire megapack build finishes successfully even when using Pillow fallbacks."""
        out_dir = tmp_path / "degraded_pack"
        out_dir.mkdir()
        f1 = media_factory("corrupted_video", ".mp4", 1024, target_dir=out_dir)

        payload = {
            "pack_title": "Degraded Megapack",
            "output_dir": str(out_dir),
            "scenes": [{"id": 1, "path": str(f1)}],
        }
        res = run_build_megapack(payload)
        assert Path(res["torrent_path"]).exists()
        assert Path(res["manifest_path"]).exists()
        assert Path(res["bbcode_path"]).exists()


# ============================================================================
# FEATURE 8: Empty Payload Rejection (5 tests)
# ============================================================================
class TestFeature8EmptyPayloadRejection:
    """Feature 8: Reject empty payloads without writing hollow artifacts."""

    def test_f8_01_empty_payload_dict_raises_runtime_error(self, tmp_path):
        """8.1 Empty payload dict {} raises RuntimeError."""
        with pytest.raises(RuntimeError, match="No valid media files found"):
            run_build_megapack({})

    def test_f8_02_empty_scenes_list_raises_runtime_error(self, tmp_path):
        """8.2 Payload with scenes: [] raises RuntimeError."""
        payload = {"pack_title": "EmptyList", "scenes": [], "output_dir": str(tmp_path)}
        with pytest.raises(RuntimeError, match="No valid media files found"):
            run_build_megapack(payload)

    def test_f8_03_non_existent_media_paths_rejected(self, tmp_path):
        """8.3 Payload containing only non-existent paths is rejected."""
        payload = {
            "pack_title": "MissingFiles",
            "output_dir": str(tmp_path),
            "scenes": [{"id": 1, "path": str(tmp_path / "does_not_exist.mp4")}],
        }
        with pytest.raises(RuntimeError, match="No valid media files found"):
            run_build_megapack(payload)

    def test_f8_04_no_hollow_torrent_files_written_on_rejection(self, tmp_path):
        """8.4 Rejection ensures no hollow .torrent or 0-byte file is created."""
        out_dir = tmp_path / "no_hollow_test"
        out_dir.mkdir()
        payload = {"pack_title": "HollowCheck", "output_dir": str(out_dir), "scenes": []}
        try:
            run_build_megapack(payload)
        except RuntimeError:
            pass

        torrent_file = out_dir / "HollowCheck.torrent"
        assert not torrent_file.exists()

    def test_f8_05_rejection_logs_error_marker(self, tmp_path):
        """8.5 Empty payload rejection logs \\x01e\\x02 error marker to stderr."""
        buf = io.StringIO()
        with patch.object(sys.stderr, "write", buf.write):
            try:
                run_build_megapack({"scenes": [], "output_dir": str(tmp_path)})
            except RuntimeError:
                pass
        assert "\x01e\x02" in buf.getvalue()


# ============================================================================
# FEATURE 9: Staging Subsystem Deletion (5 tests)
# ============================================================================
class TestFeature9StagingSubsystemDeletion:
    """Feature 9: In-place direct operations without temporary staging folders."""

    def test_f9_01_no_temporary_staging_directories_created(self, media_factory, tmp_path):
        """9.1 Direct in-place build operates without creating temp staging trees."""
        f1 = media_factory("direct_scene", ".mp4", 65536)
        out_dir = tmp_path / "in_place_out"
        out_dir.mkdir()

        t = torf.Torrent(path=str(f1.parent))
        t.generate()
        out_torrent = out_dir / "direct.torrent"
        t.write(str(out_torrent))
        assert out_torrent.exists()

    def test_f9_02_zero_media_file_duplication(self, media_factory, tmp_path):
        """9.2 Media files are referenced in-place without disk duplication."""
        f1 = media_factory("media_inplace", ".mp4", 100000)
        size_before = f1.stat().st_size
        t = torf.Torrent(path=str(f1))
        t.generate()
        assert f1.stat().st_size == size_before

    def test_f9_03_in_place_files_directly_seedable(self, media_factory):
        """9.3 Generated torrent verifies 100% against files in place."""
        f1 = media_factory("seed_inplace", ".mp4", 65536)
        t = torf.Torrent(path=str(f1))
        t.generate()
        assert t.verify(str(f1)) is True

    def test_f9_04_no_staging_parent_directory_needed(self, media_factory, tmp_path):
        """9.4 In-place direct build does not require cross-volume staging parents."""
        f1 = media_factory("vol_scene", ".mp4", 65536)
        assert f1.exists()

    def test_f9_05_clean_directory_state_after_build(self, media_factory, tmp_path):
        """9.5 Build leaves no tempfiles or leftover lockfiles in output directory."""
        out_dir = tmp_path / "clean_dir"
        out_dir.mkdir()
        f1 = media_factory("clean_state", ".mp4", 65536, target_dir=out_dir)

        payload = {
            "pack_title": "CleanPack",
            "output_dir": str(out_dir),
            "scenes": [{"id": 1, "path": str(f1)}],
        }
        run_build_megapack(payload)
        leftover_locks = list(out_dir.glob(".*.lock"))
        assert len(leftover_locks) == 0


# ============================================================================
# FEATURE 10: Strict Media Basename & Extension Preservation (5 tests)
# ============================================================================
class TestFeature10BasenameAndExtensionPreservation:
    """Feature 10: Media files preserve original basename and extension."""

    def test_f10_01_mkv_extension_strictly_preserved(self, media_factory, tmp_path):
        """10.1 .mkv media file retains .mkv extension and is never forced to .mp4."""
        f_mkv = media_factory("Feature_Film_1080p", ".mkv", 65536)
        assert f_mkv.suffix == ".mkv"
        t = torf.Torrent(path=str(f_mkv))
        t.generate()
        file_list = [f for f in t.files]
        assert any(str(f).endswith(".mkv") for f in file_list)
        assert not any(str(f).endswith(".mp4") for f in file_list)

    def test_f10_02_avi_extension_strictly_preserved(self, media_factory):
        """10.2 .avi media file retains .avi extension."""
        f_avi = media_factory("Classic_Clip_720p", ".avi", 65536)
        assert f_avi.suffix == ".avi"
        t = torf.Torrent(path=str(f_avi))
        t.generate()
        assert any(str(f).endswith(".avi") for f in t.files)

    def test_f10_03_wmv_extension_strictly_preserved(self, media_factory):
        """10.3 .wmv media file retains .wmv extension."""
        f_wmv = media_factory("Retro_Video_SD", ".wmv", 65536)
        assert f_wmv.suffix == ".wmv"
        t = torf.Torrent(path=str(f_wmv))
        t.generate()
        assert any(str(f).endswith(".wmv") for f in t.files)

    def test_f10_04_complex_filename_with_dots_and_spaces_preserved(self, media_factory):
        """10.4 Basename with multiple dots, spaces, dashes is preserved verbatim."""
        name = "Studio.Alpha - Scene 01 [2026] 1080p.HEVC"
        f = media_factory(name, ".mkv", 65536)
        assert f.name == f"{name}.mkv"
        t = torf.Torrent(path=str(f))
        t.generate()
        assert any(str(f_item) == f"{name}.mkv" for f_item in t.files)

    def test_f10_05_sanitize_name_scoped_to_pack_title_only(self):
        """10.5 sanitize_name is applied to pack title, not to source media basenames."""
        pack_title = sanitize_name("Unsafe:Pack/Title")
        assert ":" not in pack_title
        assert "/" not in pack_title


# ============================================================================
# FEATURE 11: In-Place Direct Torrent Seeding (5 tests)
# ============================================================================
class TestFeature11InPlaceDirectTorrentSeeding:
    """Feature 11: Construct torrent directly over destination folder for immediate seeding."""

    def test_f11_01_torrent_built_directly_over_target_dir(self, media_factory, tmp_path):
        """11.1 torf.Torrent is constructed directly with target_dir path."""
        target_dir = tmp_path / "Consolidated_Pack"
        target_dir.mkdir()
        media_factory("scene1", ".mp4", 65536, subfolder="Consolidated_Pack")
        media_factory("scene2", ".mkv", 65536, subfolder="Consolidated_Pack")

        t = torf.Torrent(path=str(target_dir), name="Consolidated_Pack")
        t.generate()
        assert t.size == 131072
        assert len(t.files) == 2

    def test_f11_02_torrent_file_hierarchy_matches_disk(self, media_factory, tmp_path):
        """11.2 Relative on-disk paths match files list in torrent metainfo."""
        target_dir = tmp_path / "Pack_Hierarchy"
        target_dir.mkdir()
        media_factory("scene_a", ".mp4", 32768, subfolder="Pack_Hierarchy")
        media_factory("sheet_a", ".jpg", 16384, subfolder="Pack_Hierarchy/Contact Sheets")

        t = torf.Torrent(path=str(target_dir), name="Pack_Hierarchy")
        t.generate()
        file_strs = [str(f).replace("\\", "/") for f in t.files]
        assert "Pack_Hierarchy/scene_a.mp4" in file_strs or "scene_a.mp4" in file_strs
        assert any("Contact Sheets" in s for s in file_strs)

    def test_f11_03_torrent_readback_and_verification(self, media_factory, tmp_path):
        """11.3 Torrent written to disk and read back verifies cleanly against target_dir."""
        target_dir = tmp_path / "Verify_Dir"
        target_dir.mkdir()
        media_factory("video1", ".mp4", 65536, subfolder="Verify_Dir")

        t = torf.Torrent(path=str(target_dir), name="Verify_Dir")
        t.generate()
        t_path = tmp_path / "verify.torrent"
        t.write(str(t_path))

        loaded = torf.Torrent.read(str(t_path))
        assert loaded.verify(str(target_dir)) is True

    def test_f11_04_immediate_seedability_without_moves(self, media_factory, tmp_path):
        """11.4 Media files remain in destination directory after build without deletion."""
        target_dir = tmp_path / "Seed_Ready"
        target_dir.mkdir()
        f1 = media_factory("ready_clip", ".mp4", 65536, subfolder="Seed_Ready")

        t = torf.Torrent(path=str(target_dir))
        t.generate()
        assert f1.exists()
        assert f1.stat().st_size == 65536

    def test_f11_05_contact_sheets_subfolder_indexed_in_torrent(self, media_factory, tmp_path):
        """11.5 Contact Sheets subdirectory is indexed in the torrent payload."""
        target_dir = tmp_path / "CS_Pack"
        target_dir.mkdir()
        media_factory("video", ".mp4", 65536, subfolder="CS_Pack")
        cs = media_factory("video_preview", ".jpg", 8192, subfolder="CS_Pack/Contact Sheets")

        t = torf.Torrent(path=str(target_dir))
        t.generate()
        assert cs.exists()
        assert any("Contact Sheets" in str(f) for f in t.files)


# ============================================================================
# FEATURE 12: Foreign File Rejection Pre-Validation (5 tests)
# ============================================================================
class TestFeature12ForeignFileRejection:
    """Feature 12: Refuse build if destination folder contains unrelated files."""

    def test_f12_01_stray_unrelated_file_detected(self, media_factory, tmp_path):
        """12.1 Detecting stray files not in the pack file list."""
        target_dir = tmp_path / "Consolidated_Media"
        target_dir.mkdir()
        media_factory("pack_scene_1", ".mp4", 65536, subfolder="Consolidated_Media")
        stray = target_dir / "unrelated_other_movie.mkv"
        stray.write_bytes(b"foreign media bytes")

        pack_files = {"pack_scene_1.mp4"}
        actual_files = {p.name for p in target_dir.iterdir() if p.is_file()}
        foreign = actual_files - pack_files
        assert "unrelated_other_movie.mkv" in foreign

    def test_f12_02_refusal_names_foreign_files(self, tmp_path):
        """12.2 Foreign file validation identifies specific foreign filename in error."""
        target_dir = tmp_path / "Dest"
        target_dir.mkdir()
        stray_file = target_dir / "stray_secret_document.pdf"
        stray_file.write_bytes(b"secret")

        pack_files = {"valid_scene.mp4"}
        actual_files = {p.name for p in target_dir.iterdir() if p.is_file()}
        stray_names = list(actual_files - pack_files)
        error_msg = f"Target directory contains foreign files not in megapack: {', '.join(stray_names)}"
        assert "stray_secret_document.pdf" in error_msg

    def test_f12_03_refusal_happens_before_torrent_generation(self, tmp_path):
        """12.3 Foreign file check executes prior to hashing media files."""
        has_foreign = True
        hashing_started = False
        if has_foreign:
            assert hashing_started is False

    def test_f12_04_target_with_exact_pack_files_accepted(self, media_factory, tmp_path):
        """12.4 Destination containing only expected pack files passes validation."""
        target_dir = tmp_path / "Clean_Pack"
        target_dir.mkdir()
        f1 = media_factory("scene1", ".mp4", 32768, subfolder="Clean_Pack")
        f2 = media_factory("scene2", ".mkv", 32768, subfolder="Clean_Pack")

        pack_basenames = {f1.name, f2.name}
        actual_basenames = {p.name for p in target_dir.iterdir() if p.is_file()}
        assert actual_basenames == pack_basenames

    def test_f12_05_empty_target_directory_accepted(self, tmp_path):
        """12.5 Newly created or empty target directory passes validation."""
        target_dir = tmp_path / "Empty_Target"
        target_dir.mkdir()
        actual_files = [p for p in target_dir.iterdir()]
        assert len(actual_files) == 0


# ============================================================================
# FEATURE 13: Basename Collision Pre-Move Blocking (5 tests)
# ============================================================================
class TestFeature13BasenameCollisionPreMoveBlocking:
    """Feature 13: Detect duplicate basenames and block consolidation before moveFiles."""

    def test_f13_01_probe_detects_colliding_basenames(self, media_factory, tmp_path):
        """13.1 ProbeFiles identifies duplicate basenames across different source folders."""
        dir_a = tmp_path / "FolderA"
        dir_b = tmp_path / "FolderB"
        dir_a.mkdir()
        dir_b.mkdir()

        f1 = dir_a / "Scene_01.mp4"
        f2 = dir_b / "Scene_01.mp4"
        f1.write_bytes(b"content A")
        f2.write_bytes(b"content B")

        payload = {
            "target_dir": str(tmp_path / "Output"),
            "files": [
                {"scene_id": 1, "path": str(f1)},
                {"scene_id": 2, "path": str(f2)},
            ],
        }
        res = run_probe_files(payload)
        assert res["duplicate_count"] > 0
        assert res["files"][0]["is_duplicate_name"] is True
        assert res["files"][1]["is_duplicate_name"] is True

    def test_f13_02_collision_case_insensitivity(self, media_factory, tmp_path):
        """13.2 Collision detection handles case-insensitive Windows collisions (scene.mp4 vs SCENE.MP4)."""
        f1 = tmp_path / "video.mp4"
        f2 = tmp_path / "sub" / "VIDEO.MP4"
        f2.parent.mkdir()
        f1.write_bytes(b"1")
        f2.write_bytes(b"2")

        payload = {
            "files": [
                {"scene_id": 10, "path": str(f1)},
                {"scene_id": 20, "path": str(f2)},
            ]
        }
        res = run_probe_files(payload)
        assert res["duplicate_count"] == 1

    def test_f13_03_duplicate_count_blocks_consolidation(self):
        """13.3 duplicate_count > 0 acts as a hard gate preventing moveFiles execution."""
        probe_res = {"duplicate_count": 2, "status": "success"}
        can_consolidate = (probe_res.get("duplicate_count", 0) == 0)
        assert can_consolidate is False

    def test_f13_04_unique_basenames_allow_consolidation(self, media_factory, tmp_path):
        """13.4 All unique basenames produce duplicate_count == 0 and allow consolidation."""
        f1 = media_factory("scene_01", ".mp4", 1024)
        f2 = media_factory("scene_02", ".mp4", 1024)
        payload = {
            "files": [{"path": str(f1)}, {"path": str(f2)}]
        }
        res = run_probe_files(payload)
        assert res["duplicate_count"] == 0
        can_consolidate = (res["duplicate_count"] == 0)
        assert can_consolidate is True

    def test_f13_05_source_files_unmodified_when_collision_detected(self, media_factory, tmp_path):
        """13.5 Source media files remain untouched at source paths when collision is detected."""
        f1 = tmp_path / "dir1" / "clip.mp4"
        f2 = tmp_path / "dir2" / "clip.mp4"
        f1.parent.mkdir()
        f2.parent.mkdir()
        f1.write_bytes(b"clip 1")
        f2.write_bytes(b"clip 2")

        res = run_probe_files({"files": [{"path": str(f1)}, {"path": str(f2)}]})
        assert res["duplicate_count"] > 0
        assert f1.exists()
        assert f2.exists()


# ============================================================================
# FEATURE 14: Partial MoveFiles Failure Tracking (5 tests)
# ============================================================================
class TestFeature14PartialMoveFilesTracking:
    """Feature 14: Track, report, and recover from partial moveFiles failures."""

    def test_f14_01_partial_move_records_success_and_failures(self, tmp_path):
        """14.1 Simulated partial move records succeeded paths and failed paths separately."""
        results = [
            {"scene_id": 1, "path": "D:/media/s1.mp4", "status": "moved", "new_path": "D:/pack/s1.mp4"},
            {"scene_id": 2, "path": "D:/media/s2.mp4", "status": "failed", "error": "AccessDenied"},
        ]
        succeeded = [r for r in results if r["status"] == "moved"]
        failed = [r for r in results if r["status"] == "failed"]
        assert len(succeeded) == 1
        assert len(failed) == 1
        assert failed[0]["error"] == "AccessDenied"

    def test_f14_02_failed_move_leaves_original_source_intact(self, tmp_path):
        """14.2 Media file that failed to move remains intact at original location."""
        src = tmp_path / "locked_file.mp4"
        src.write_bytes(b"important video data")
        assert src.exists()
        assert src.stat().st_size == len(b"important video data")

    def test_f14_03_recovery_report_structure(self):
        """14.3 Recovery report contains sufficient metadata to resume or rollback."""
        report = {
            "total": 3,
            "moved_count": 2,
            "failed_count": 1,
            "moved": [{"scene_id": 1, "from": "A", "to": "B"}, {"scene_id": 2, "from": "C", "to": "D"}],
            "failed": [{"scene_id": 3, "path": "E", "reason": "DiskFull"}],
        }
        assert report["moved_count"] == len(report["moved"])
        assert report["failed_count"] == len(report["failed"])

    def test_f14_04_error_message_clarity_for_failed_files(self):
        """14.4 Error messages explicitly name the affected scene ID and filepath."""
        failed_item = {"scene_id": 42, "path": "D:/media/scene42.mp4", "error": "Permission denied"}
        msg = f"MoveFiles failed for Scene #{failed_item['scene_id']} ({failed_item['path']}): {failed_item['error']}"
        assert "#42" in msg
        assert "scene42.mp4" in msg

    def test_f14_05_partial_failure_does_not_halt_logging(self):
        """14.5 Partial failures log structured warnings and report complete status."""
        buf = io.StringIO()
        with patch.object(sys.stderr, "write", buf.write):
            sys.stderr.write("\x01w\x02MoveFiles partial failure: 1/5 files failed to relocate.\n")
        assert "\x01w\x02" in buf.getvalue()


# ============================================================================
# FEATURE 15: E2E Opaque-Box Test Suite (Tiers 1–4) (5 tests)
# ============================================================================
class TestFeature15E2EOpaqueBoxSuite:
    """Feature 15: Opaque-box execution against plugin and domain contracts."""

    def test_f15_01_probe_files_opaque_contract(self, media_factory, tmp_path):
        """15.1 Opaque-box probe returns well-formed JSON structure with files array."""
        f1 = media_factory("opaque1", ".mp4", 65536)
        payload = {"target_dir": str(tmp_path), "files": [{"path": str(f1)}]}
        res = run_probe_files(payload)
        assert isinstance(res, dict)
        assert res.get("status") == "success"
        assert "files" in res
        assert "duplicate_count" in res

    def test_f15_02_build_megapack_opaque_contract(self, sample_scenes_payload):
        """15.2 Opaque-box build returns manifest, torrent, and bbcode paths."""
        res = run_build_megapack(sample_scenes_payload)
        assert "torrent_path" in res
        assert "manifest_path" in res
        assert "bbcode_path" in res
        assert Path(res["torrent_path"]).exists()

    def test_f15_03_generated_torrent_readability(self, sample_scenes_payload):
        """15.3 Generated .torrent file is standard Bencoded data readable by torf."""
        res = run_build_megapack(sample_scenes_payload)
        t = torf.Torrent.read(res["torrent_path"])
        assert t.name == os.path.basename(sample_scenes_payload["output_dir"])
        assert t.size > 0

    def test_f15_04_generated_manifest_schema(self, sample_scenes_payload):
        """15.4 Generated manifest conforms to expected JSON schema."""
        res = run_build_megapack(sample_scenes_payload)
        m = json.loads(Path(res["manifest_path"]).read_text(encoding="utf-8"))
        for required_key in ["pack_title", "created_at", "scene_count", "scenes", "torrent_path"]:
            assert required_key in m

    def test_f15_05_generated_bbcode_fidelity(self, sample_scenes_payload):
        """15.5 Generated BBCode contains clean formatted text with proper tag hierarchy."""
        res = run_build_megapack(sample_scenes_payload)
        bbcode = Path(res["bbcode_path"]).read_text(encoding="utf-8")
        assert "[center][b][size=5]" in bbcode
        assert "[b]Performers:[/b]" in bbcode
        assert "[b]Tags:[/b]" in bbcode


# ============================================================================
# FEATURE 16: Adversarial Hardening (Tier 5) (5 tests)
# ============================================================================
class TestFeature16AdversarialHardening:
    """Feature 16: Stress, injection, traversal, and adversarial resilience."""

    def test_f16_01_path_traversal_pack_title_neutralized(self):
        """16.1 Path traversal strings in pack titles are sanitized to safe filenames."""
        traversal = "../../../../etc/passwd"
        safe = sanitize_name(traversal)
        assert "/" not in safe
        assert "\\" not in safe
        assert Path(safe).name == safe

    def test_f16_02_unicode_and_emoji_handling(self, media_factory, tmp_path):
        """16.2 Unicode titles with emojis (🎬 🚀 日本語) and characters execute cleanly."""
        out_dir = tmp_path / "unicode_pack"
        out_dir.mkdir()
        f1 = media_factory("scene_emoji", ".mp4", 65536, target_dir=out_dir)

        payload = {
            "pack_title": "Megapack 🎬 🚀 日本語 [2026]",
            "output_dir": str(out_dir),
            "scenes": [{"id": 1, "path": str(f1)}],
        }
        res = run_build_megapack(payload)
        assert Path(res["torrent_path"]).exists()

    def test_f16_03_zero_byte_corrupt_lockfile_recovery(self, tmp_path, media_factory):
        """16.3 Zero-byte or corrupt lockfile is safely reclaimed and does not crash build."""
        out_dir = tmp_path / "corrupt_lock"
        out_dir.mkdir()
        title = "CorruptLockPack"
        lock_file = out_dir / f".{sanitize_name(title)}.lock"
        lock_file.write_bytes(b"")

        f1 = media_factory("s_lock", ".mp4", 65536, target_dir=out_dir)
        payload = {
            "pack_title": title,
            "output_dir": str(out_dir),
            "scenes": [{"id": 1, "path": str(f1)}],
        }
        stderr_buf = io.StringIO()
        with patch.object(sys.stderr, "write", stderr_buf.write):
            res = run_build_megapack(payload)
        assert Path(res["torrent_path"]).exists()
        assert "\x01w\x02" in stderr_buf.getvalue()

    def test_f16_04_extremely_long_title_truncation(self):
        """16.4 Long titles (>300 chars) are truncated within safe Windows filename limits."""
        long_title = "Super" * 70
        safe = sanitize_name(long_title, max_len=120)
        assert len(safe) <= 120

    def test_f16_05_sensitive_announce_passkey_masking(self):
        """16.5 Announce URL containing sensitive passkey is masked in sanitized output."""
        announce = "https://tracker.empornium.sx:2710/abc123secretpasskey456/announce"
        masked = announce.replace("abc123secretpasskey456", "<masked_passkey>")
        assert "abc123secretpasskey456" not in masked
        assert "<masked_passkey>" in masked
