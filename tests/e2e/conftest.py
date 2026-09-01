"""
Pytest configuration and shared fixtures for the DeepSeek Megapack E2E test suite.
"""

import sys
import os
import tempfile
from pathlib import Path
from typing import Dict, Any, List, Optional
import pytest

# Ensure backend is at the very front of sys.path so 'app' and 'empornium_megapack' resolve cleanly
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

from task import sanitize_name


def resolve_domain():
    """
    Resolves the domain layer package, preferring empornium_megapack
    with fallback to app during milestone transition.
    """
    try:
        import empornium_megapack as dm
        return dm
    except ImportError:
        import app as dm
        return dm


@pytest.fixture
def domain_module():
    """Fixture providing the active domain module (empornium_megapack or app)."""
    return resolve_domain()


@pytest.fixture
def media_factory(tmp_path):
    """
    Factory fixture to create realistic dummy video files with arbitrary
    extensions and content without calling external ffmpeg encoders.
    """
    created_files = []

    def _create(
        name: str = "scene_01",
        ext: str = ".mp4",
        size_bytes: int = 65536,
        subfolder: Optional[str] = None,
        target_dir: Optional[Any] = None,
    ) -> Path:
        if target_dir:
            base_dir = Path(target_dir) / subfolder if subfolder else Path(target_dir)
        else:
            base_dir = tmp_path / subfolder if subfolder else tmp_path
        base_dir.mkdir(parents=True, exist_ok=True)
        if not ext.startswith("."):
            ext = f".{ext}"
        filename = f"{name}{ext}" if not name.endswith(ext) else name
        file_path = base_dir / filename
        # Write dummy byte pattern
        content = (b"\x00\x01\x02\x03\x04\x05\x06\x07" * (size_bytes // 8 + 1))[:size_bytes]
        file_path.write_bytes(content)
        created_files.append(file_path)
        return file_path

    return _create


@pytest.fixture
def consolidated_pack_dir():
    """
    Creates the pack folder the legacy build contract requires:
    <output_dir>/<sanitize_name(pack_title)>/ — the post-consolidation state
    the Consolidate step produces. run_build_megapack's legacy preflight
    (plugin/task.py) refuses to build unless this directory already exists
    and contains every declared scene file.

    Usage: pack_dir = consolidated_pack_dir(out_dir, pack_title)
    """
    def _make(out_dir, pack_title):
        pack_dir = Path(out_dir) / sanitize_name(pack_title)
        pack_dir.mkdir(parents=True, exist_ok=True)
        return pack_dir

    return _make


@pytest.fixture
def sample_scenes_payload(media_factory, tmp_path, consolidated_pack_dir):
    """
    Returns a standard realistic multi-scene build payload with dummy media
    consolidated into <output_dir>/<pack title>/ (the post-consolidation
    pack folder the build preflight requires).
    """
    output_dir = tmp_path / "Output_Megapack"
    output_dir.mkdir(parents=True, exist_ok=True)

    pack_dir = consolidated_pack_dir(output_dir, "Test Studio Megapack Vol 1")

    f1 = media_factory("Scene_Alpha", ".mp4", 65536, target_dir=pack_dir)
    f2 = media_factory("Scene_Beta", ".mkv", 131072, target_dir=pack_dir)
    f3 = media_factory("Scene_Gamma", ".avi", 98304, target_dir=pack_dir)

    return {
        "pack_title": "Test Studio Megapack Vol 1",
        "output_dir": str(output_dir),
        "scenes": [
            {
                "id": 101,
                "title": "Scene Alpha 1080p",
                "path": str(f1),
                "performers": ["Alice Stone", "Bob Clark"],
                "tags": ["1080p", "Studio Alpha", "Feature"],
            },
            {
                "id": 102,
                "title": "Scene Beta 4K",
                "path": str(f2),
                "performers": ["Alice Stone", "Charlie Davis"],
                "tags": ["4K", "Studio Alpha", "Exclusive"],
            },
            {
                "id": 103,
                "title": "Scene Gamma 720p",
                "path": str(f3),
                "performers": ["Diana Prince"],
                "tags": ["720p", "Studio Alpha"],
            },
        ],
        "performers": ["Alice Stone", "Bob Clark", "Charlie Davis", "Diana Prince"],
        "tags": ["Studio Alpha", "Megapack", "1080p", "4K"],
        "notes": "Quality release from official studio archives.",
        "layout": "grid_4x4",
    }
