"""
Empirical Challenger 1 Test Suite: Video Contact Sheets and Torrents.
Performs adversarial empirical testing against task.py in Empornium Megapack Builder.
"""

import io
import os
import sys
import shutil
import tempfile
import subprocess
import pytest
from pathlib import Path
from PIL import Image, ImageStat
import torf

# Ensure the active interpreter's Scripts dir (venv tools) and the Stash tools
# dir are on PATH. Deriving from sys.executable keeps this portable: it is the
# venv Scripts dir whenever the suite runs under a venv.
os.environ["PATH"] = (
    os.pathsep.join(
        part
        for part in (str(Path(sys.executable).parent), str(Path.home() / ".stash"))
        if part
    )
    + os.pathsep
    + os.environ.get("PATH", "")
)

REPO_ROOT = Path(__file__).resolve().parents[2]
PLUGIN_DIR = REPO_ROOT / "plugin"
if str(PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(PLUGIN_DIR))

import task


# --- test media fixture -------------------------------------------------------
# run_build_megapack now refuses to build a pack with no valid media (it would
# emit a torrent with zero piece hashes). Tests below exercise lockfiles, unicode
# and payload handling -- not the empty-input path -- so they need one real file.
# Content is irrelevant: torf hashes bytes, and vcsi failing on a non-video falls
# back to the Pillow placeholder with a warning.
_DUMMY_MEDIA_DIR = None


def _dummy_media_path():
    global _DUMMY_MEDIA_DIR
    if _DUMMY_MEDIA_DIR is None:
        _DUMMY_MEDIA_DIR = tempfile.mkdtemp(prefix="megapack_test_media_")
    p = os.path.join(_DUMMY_MEDIA_DIR, "dummy_media.mp4")
    if not os.path.exists(p):
        with open(p, "wb") as fh:
            fh.write(b"\x00" * 65536)
    return p
# ------------------------------------------------------------------------------


def create_ffmpeg_video(out_path: str, duration: int = 10, pattern: str = "testsrc") -> str:
    """Generates a synthetic video fixture with high visual variation."""
    ffmpeg_bin = shutil.which("ffmpeg")
    assert ffmpeg_bin, "ffmpeg executable not found on PATH"
    
    if pattern == "testsrc":
        lavfi_filter = f"testsrc=duration={duration}:size=640x360:rate=30"
    elif pattern == "smptebars":
        lavfi_filter = f"smptebars=duration={duration}:size=640x360:rate=30"
    elif pattern == "rgbtest":
        lavfi_filter = f"rgbtestsrc=duration={duration}:size=640x360:rate=30"
    else:
        lavfi_filter = f"testsrc=duration={duration}:size=640x360:rate=30"

    cmd = [
        ffmpeg_bin,
        "-y",
        "-f", "lavfi",
        "-i", lavfi_filter,
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-t", str(duration),
        str(out_path)
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    assert res.returncode == 0, f"ffmpeg generation failed: {res.stderr}"
    assert os.path.exists(out_path) and os.path.getsize(out_path) > 0
    return out_path


def create_corrupt_video(out_path: str) -> str:
    """Creates a corrupted/invalid video file."""
    with open(out_path, "wb") as f:
        f.write(b"RIFF\x00\x00\x00\x00AVI LIST\x00\x00\x00\x00corrupted junk data header \x00\xff\xfe\xfd")
    return out_path


def test_empirical_contact_sheet_variance():
    """
    Test 1: Verify contact sheets generated from real video have pixel variance > 500
    vs flat Pillow placeholder which has low variance.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        vid_path = str(tmp_path / "sample_testsrc.mp4")
        create_ffmpeg_video(vid_path, duration=15, pattern="testsrc")

        # 1. Generate real contact sheet via task.generate_contact_sheet
        real_cs_path = str(tmp_path / "real_cs.jpg")
        task.generate_contact_sheet(
            video_path=vid_path,
            out_path=real_cs_path,
            layout="grid_4x4",
            timeout=30.0,
            pack_title="Test Pack"
        )
        assert os.path.exists(real_cs_path)
        assert os.path.getsize(real_cs_path) > 0

        # Open real contact sheet and calculate variance
        with Image.open(real_cs_path) as img:
            rgb_img = img.convert("RGB")
            stat = ImageStat.Stat(rgb_img)
            var_r, var_g, var_b = stat.var
            mean_var = sum(stat.var) / len(stat.var)
            print(f"\n[Real Contact Sheet] Variance R={var_r:.2f}, G={var_g:.2f}, B={var_b:.2f}, Mean={mean_var:.2f}")
            # Assert variance is well above 500 threshold
            assert var_r > 500, f"Red channel variance {var_r} <= 500"
            assert var_g > 500, f"Green channel variance {var_g} <= 500"
            assert var_b > 500, f"Blue channel variance {var_b} <= 500"
            assert mean_var > 500, f"Mean variance {mean_var} <= 500"

        # 2. Compare against Pillow fallback placeholder
        flat_cs_path = str(tmp_path / "flat_cs.jpg")
        task._generate_pillow_placeholder(
            out_path=flat_cs_path,
            pack_title="Test Pack Flat",
            scene_idx=0,
            total_scenes=1,
            video_path=vid_path
        )
        assert os.path.exists(flat_cs_path)

        with Image.open(flat_cs_path) as flat_img:
            flat_rgb = flat_img.convert("RGB")
            flat_stat = ImageStat.Stat(flat_rgb)
            flat_mean_var = sum(flat_stat.var) / len(flat_stat.var)
            print(f"[Placeholder Flat Sheet] Variance R={flat_stat.var[0]:.2f}, G={flat_stat.var[1]:.2f}, B={flat_stat.var[2]:.2f}, Mean={flat_mean_var:.2f}")
            # Placeholder has near-zero variance on flat dark gray background
            assert flat_mean_var < 500, f"Flat placeholder variance unexpectedly high: {flat_mean_var}"
            assert mean_var > flat_mean_var * 5, "Real contact sheet variance should be vastly higher than flat placeholder"


def test_empirical_layout_matrix():
    """
    Test 2: Verify different layout options (grid_3x3, grid_4x4, 2x5) execute and produce valid sheets.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        vid_path = str(tmp_path / "sample_smpte.mp4")
        create_ffmpeg_video(vid_path, duration=20, pattern="smptebars")

        layouts = ["grid_3x3", "grid_4x4", "2x5", "3x3", "grid_2x5"]
        for l in layouts:
            cs_out = str(tmp_path / f"cs_{l}.jpg")
            res = task.generate_contact_sheet(
                video_path=vid_path,
                out_path=cs_out,
                layout=l,
                timeout=30.0,
                pack_title="Layout Test"
            )
            assert os.path.exists(res)
            assert os.path.getsize(res) > 10000, f"Layout {l} generated empty/tiny file"
            with Image.open(res) as img:
                stat = ImageStat.Stat(img.convert("RGB"))
                mean_var = sum(stat.var) / len(stat.var)
                assert mean_var > 500, f"Layout {l} produced insufficient variance: {mean_var}"


def test_empirical_corrupt_video_fallback():
    """
    Test 3: Verify corrupt video file triggers graceful fallback to Pillow placeholder
    and logs \\x01w\\x02 warning to stderr.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        corrupt_vid = str(tmp_path / "corrupt.mp4")
        create_corrupt_video(corrupt_vid)

        cs_out = str(tmp_path / "corrupt_cs.jpg")
        
        # Intercept stderr
        old_stderr = sys.stderr
        sys.stderr = io.StringIO()
        try:
            res = task.generate_contact_sheet(
                video_path=corrupt_vid,
                out_path=cs_out,
                layout="grid_4x4",
                timeout=10.0,
                pack_title="Corrupt Test"
            )
            stderr_val = sys.stderr.getvalue()
        finally:
            sys.stderr = old_stderr

        # Assert fallback image was created
        assert os.path.exists(res)
        assert os.path.getsize(res) > 0

        # Assert warning prefix \x01w\x02 in stderr
        assert "\x01w\x02" in stderr_val, f"Expected \\x01w\\x02 warning in stderr, got: {stderr_val}"
        assert "vcsi generation failed" in stderr_val or "Falling back to Pillow placeholder" in stderr_val

        # Verify output is placeholder (low variance)
        with Image.open(res) as img:
            stat = ImageStat.Stat(img.convert("RGB"))
            mean_var = sum(stat.var) / len(stat.var)
            assert mean_var < 500, f"Corrupt fallback expected low variance placeholder, got {mean_var}"


def test_empirical_torrent_generation_and_readback():
    """
    Test 4: Verify genuine torrent creation using torf.Torrent.read()
    Checks pieces, hashes, total size, structure, and announce trackers.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        out_dir = str(tmp_path / "output")
        pack_title = "Empirical MegaPack Alpha"
        pack_dir = Path(out_dir) / task.sanitize_name(pack_title)
        pack_dir.mkdir(parents=True, exist_ok=True)
        vid1 = str(pack_dir / "scene_alpha.mp4")
        vid2 = str(pack_dir / "scene_beta.mp4")
        create_ffmpeg_video(vid1, duration=10, pattern="testsrc")
        create_ffmpeg_video(vid2, duration=12, pattern="smptebars")

        size1 = os.path.getsize(vid1)
        size2 = os.path.getsize(vid2)
        total_media_size = size1 + size2

        payload = {
            "pack_title": pack_title,
            "output_dir": out_dir,
            "scenes": [
                {"id": "1", "file_paths": [vid1], "performers": [{"name": "Actor A"}], "tags": ["HD", "Scene"]},
                {"id": "2", "file_paths": [vid2], "performers": [{"name": "Actor B"}], "tags": ["4K", "Scene"]},
            ],
            "layout": "grid_3x3",
            "allow_custom_announce": True,
            "announce": ["https://tracker.example.com/announce", "https://backup.example.com/announce"],
            "include_contact_sheets": False,
        }

        old_stderr = sys.stderr
        sys.stderr = io.StringIO()
        try:
            result = task.run_build_megapack(payload)
            stderr_out = sys.stderr.getvalue()
        finally:
            sys.stderr = old_stderr

        assert result["status"] == "success"
        torrent_path = result["torrent_path"]
        assert os.path.exists(torrent_path)

        # Read back with torf
        t = torf.Torrent.read(torrent_path)
        assert t.name == task.sanitize_name(pack_title)
        assert t.size == total_media_size
        assert t.trackers == [["https://tracker.example.com/announce"], ["https://backup.example.com/announce"]]
        assert t.private is True
        assert t.created_by == "Empornium Megapack Builder"
        assert t.pieces > 0
        raw_pieces = t.metainfo["info"]["pieces"]
        assert isinstance(raw_pieces, bytes)
        assert len(raw_pieces) == t.pieces * 20

        # Check files inside torrent
        file_names = [os.path.basename(str(f)) for f in t.files]
        assert "scene_alpha.mp4" in file_names
        assert "scene_beta.mp4" in file_names
        assert not any("Contact Sheets" in str(f) for f in t.files)

        # Check contact sheets generated
        assert len(result["contact_sheets"]) == 2
        for cs_path in result["contact_sheets"]:
            assert os.path.exists(cs_path)
            with Image.open(cs_path) as img:
                stat = ImageStat.Stat(img.convert("RGB"))
                assert sum(stat.var) / len(stat.var) > 500

        # Check progress emissions in stderr
        progress_lines = [l for l in stderr_out.splitlines() if l.startswith("\x01p\x02")]
        assert len(progress_lines) >= 5
        # Verify hashing progress between 0.70 and 0.90 occurred
        hashing_progress = [float(l.replace("\x01p\x02", "")) for l in progress_lines if 0.70 <= float(l.replace("\x01p\x02", "")) <= 0.90]
        assert len(hashing_progress) > 0


def test_empirical_torrent_with_contact_sheets_included():
    """
    Test 5: Verify torrent generation with include_contact_sheets=True
    Includes the 'Contact Sheets' subdirectory in the torrent.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        out_dir = str(tmp_path / "output_cs")
        pack_title = "Pack With Sheets"
        pack_dir = Path(out_dir) / task.sanitize_name(pack_title)
        pack_dir.mkdir(parents=True, exist_ok=True)
        vid = str(pack_dir / "feature.mp4")
        create_ffmpeg_video(vid, duration=8, pattern="testsrc")

        payload = {
            "pack_title": pack_title,
            "output_dir": out_dir,
            "scenes": [{"id": "100", "path": vid}],
            "include_contact_sheets": True,
            "layout": "grid_4x4",
        }

        result = task.run_build_megapack(payload)
        assert result["status"] == "success"
        torrent_path = result["torrent_path"]
        
        t = torf.Torrent.read(torrent_path)
        file_names = [os.path.basename(str(f)) for f in t.files]
        assert "feature.mp4" in file_names
        assert any("Contact Sheets" in str(f) for f in t.files)
        assert t.size > os.path.getsize(vid)


def test_empirical_adversarial_special_names_and_collisions():
    """
    Test 6: Adversarial titles with Windows reserved names, invalid characters,
    and multiple scene files with identical basenames (collision handling in M3).
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        dir_a = tmp_path / "folder_a"
        dir_b = tmp_path / "folder_b"
        dir_a.mkdir()
        dir_b.mkdir()

        # Same filename 'clip.mp4' in two different folders
        file_a = str(dir_a / "clip.mp4")
        file_b = str(dir_b / "clip.mp4")
        create_ffmpeg_video(file_a, duration=5, pattern="testsrc")
        create_ffmpeg_video(file_b, duration=6, pattern="smptebars")

        out_dir = str(tmp_path / "out_adv")
        pack_title = "CON: Pack <Ultra> *Test* | 2026? \"Edition\" / Final."
        safe_title = task.sanitize_name(pack_title)
        pack_dir = Path(out_dir) / safe_title
        pack_dir.mkdir(parents=True, exist_ok=True)

        payload = {
            "pack_title": pack_title,
            "output_dir": out_dir,
            "include_contact_sheets": False,
            "scenes": [
                {"id": "1", "file_paths": [file_a]},
                {"id": "2", "file_paths": [file_b]},
            ],
            "layout": "grid_3x3",
        }

        # 1. Probing must detect duplicate_count == 1
        probe_res = task.run_probe_files({"files": [file_a, file_b], "output_dir": out_dir})
        assert probe_res["duplicate_count"] == 1

        # 2. Build must hard-block and raise RuntimeError on collision in M3
        with pytest.raises(RuntimeError, match="Basename collision detected"):
            task.run_build_megapack(payload)

        # 3. Non-colliding adversarial names must succeed when consolidated into pack_dir
        file_a_in_out = str(pack_dir / "clip_a.mp4")
        file_b_in_out = str(pack_dir / "clip_b.mp4")
        create_ffmpeg_video(file_a_in_out, duration=5, pattern="testsrc")
        create_ffmpeg_video(file_b_in_out, duration=6, pattern="smptebars")
        payload["scenes"] = [
            {"id": "1", "file_paths": [file_a_in_out]},
            {"id": "2", "file_paths": [file_b_in_out]},
        ]
        result = task.run_build_megapack(payload)
        assert result["status"] == "success"
        torrent_path = result["torrent_path"]
        assert os.path.exists(torrent_path)
        t = torf.Torrent.read(torrent_path)
        assert len(t.files) == 2


def test_empirical_timeout_triggers_fallback():
    """
    Test 7: Verify that a tight timeout (e.g. 0.001s) triggers timeout handling,
    logs \\x01w\\x02, and falls back gracefully to Pillow placeholder.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        vid = str(tmp_path / "timeout_vid.mp4")
        create_ffmpeg_video(vid, duration=10, pattern="testsrc")
        cs_out = str(tmp_path / "timeout_cs.jpg")

        old_stderr = sys.stderr
        sys.stderr = io.StringIO()
        try:
            res = task.generate_contact_sheet(
                video_path=vid,
                out_path=cs_out,
                layout="grid_4x4",
                timeout=0.0001,  # Almost instantaneous timeout
                pack_title="Timeout Test"
            )
            stderr_out = sys.stderr.getvalue()
        finally:
            sys.stderr = old_stderr

        assert os.path.exists(res)
        assert "\x01w\x02" in stderr_out
        assert "timed out" in stderr_out or "vcsi invocation error" in stderr_out


def test_empirical_zero_files_raises_and_writes_nothing():
    """
    Test 8: An empty scene list must RAISE.

    This previously asserted status == "success" plus a "valid stub torrent".
    That stub had zero piece hashes -- torf rejects it with
    "Invalid metainfo: ['info']['pieces'] is empty" -- so the pack was unusable
    while the Stash job reported FINISHED. Refusing to build is the contract.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        out_dir = str(tmp_path / "out_empty")
        payload = {
            "pack_title": "Empty Pack",
            "output_dir": out_dir,
            # Intentionally empty -- this is the case under test.
            "scenes": [],
        }

        old_stderr = sys.stderr
        sys.stderr = io.StringIO()
        try:
            with pytest.raises(RuntimeError, match="refusing to emit an empty pack"):
                task.run_build_megapack(payload)
            stderr_out = sys.stderr.getvalue()
        finally:
            sys.stderr = old_stderr

        assert "eNo valid media files found" in stderr_out
        safe_title = task.sanitize_name("Empty Pack")
        assert not os.path.exists(os.path.join(out_dir, f"{safe_title}.torrent"))
        assert not os.path.exists(os.path.join(out_dir, f".{safe_title}.lock"))

