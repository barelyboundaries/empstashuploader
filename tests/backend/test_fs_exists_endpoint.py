"""
Tests for POST /api/fs/exists endpoint.

Validates bounded filesystem existence probe for consolidation counter safety:
- Happy path: existing file → true, nonexistent → false
- Validation: max 100 paths, each ≤500 chars, must be absolute, no UNC paths
- Body validation is dict-level (400), NOT Pydantic (422)
"""

import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
BACKEND_DIR = PROJECT_ROOT / "backend"

if str(BACKEND_DIR) in sys.path:
    sys.path.remove(str(BACKEND_DIR))
sys.path.insert(0, str(BACKEND_DIR))

import deepseek_megapack.main


@pytest.fixture
def client():
    return TestClient(deepseek_megapack.main.app)


class TestFsExistsHappy:
    """Happy-path existence checks against real temp files."""

    def test_existing_file_returns_true(self, client, tmp_path):
        """Given a real temp file, the probe returns true."""
        real_file = tmp_path / "existing.mp4"
        real_file.write_bytes(b"\x00" * 128)

        response = client.post("/api/fs/exists", json={"paths": [str(real_file)]})

        assert response.status_code == 200
        body = response.json()
        assert body == {"results": {str(real_file): True}}

    def test_nonexistent_file_returns_false(self, client, tmp_path):
        """Given a path that does not exist, the probe returns false."""
        fake_path = tmp_path / "nope_does_not_exist.mp4"

        response = client.post("/api/fs/exists", json={"paths": [str(fake_path)]})

        assert response.status_code == 200
        body = response.json()
        assert body == {"results": {str(fake_path): False}}

    def test_mixed_existing_and_nonexistent(self, client, tmp_path):
        """Given a mix of real and fake paths, each is reported accurately."""
        real_file = tmp_path / "real.mp4"
        real_file.write_bytes(b"\x00" * 64)
        fake_path = tmp_path / "fake.mp4"

        response = client.post(
            "/api/fs/exists",
            json={"paths": [str(real_file), str(fake_path)]},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["results"][str(real_file)] is True
        assert body["results"][str(fake_path)] is False

    def test_directory_is_not_a_file(self, client, tmp_path):
        """A directory path returns false — only isfile counts."""
        dir_path = tmp_path / "subdir"
        dir_path.mkdir()

        response = client.post("/api/fs/exists", json={"paths": [str(dir_path)]})

        assert response.status_code == 200
        assert response.json()["results"][str(dir_path)] is False


class TestFsExistsValidation:
    """Input validation that must return 400, not 422."""

    def test_missing_paths_key_returns_400(self, client):
        """Body without 'paths' key → 400 (dict-level, NOT Pydantic 422)."""
        response = client.post("/api/fs/exists", json={"not_paths": []})
        assert response.status_code == 400

    def test_empty_body_returns_400(self, client):
        """Empty body → 400."""
        response = client.post("/api/fs/exists", json={})
        assert response.status_code == 400

    def test_101_paths_returns_400(self, client):
        """Exceeding the 100-path cap → 400."""
        paths = [f"C:\\Packs\\file{i}.mp4" for i in range(101)]
        response = client.post("/api/fs/exists", json={"paths": paths})
        assert response.status_code == 400

    def test_100_paths_is_allowed(self, client):
        """Exactly 100 paths is within the cap (validation passes; existence results vary)."""
        paths = [f"C:\\Packs\\file{i}.mp4" for i in range(100)]
        response = client.post("/api/fs/exists", json={"paths": paths})
        # Should NOT be 400 — these are invalid Windows paths but the count is fine.
        # The endpoint returns 200 with false for nonexistent paths.
        assert response.status_code == 200
        assert len(response.json()["results"]) == 100

    def test_relative_path_returns_400(self, client):
        """Relative paths are rejected → 400."""
        response = client.post("/api/fs/exists", json={"paths": ["relative/path.mp4"]})
        assert response.status_code == 400

    def test_unc_path_returns_400(self, client):
        """UNC paths (\\\\server\\share) are rejected → 400 to prevent thread-blocking isfile."""
        response = client.post(
            "/api/fs/exists",
            json={"paths": ["\\\\server\\share\\file.mp4"]},
        )
        assert response.status_code == 400

    def test_unc_path_forward_slash_returns_400(self, client):
        """Forward-slash UNC paths (//server/share/file) must also be rejected → 400."""
        response = client.post(
            "/api/fs/exists",
            json={"paths": ["//server/share/video.mp4"]},
        )
        assert response.status_code == 400
        assert "UNC paths are not allowed" in response.json()["detail"]

    def test_unc_path_forward_slash_no_trailing_returns_400(self, client):
        """Forward-slash UNC path without trailing segment (//server/share) → 400."""
        response = client.post(
            "/api/fs/exists",
            json={"paths": ["//server/share"]},
        )
        assert response.status_code == 400

    def test_unc_path_mixed_backslash_slash_returns_400(self, client):
        """Mixed-separator UNC path (\\/server/share/video.mp4) must be rejected → 400."""
        response = client.post(
            "/api/fs/exists",
            json={"paths": ["\\/server/share/video.mp4"]},
        )
        assert response.status_code == 400
        assert "UNC paths are not allowed" in response.json()["detail"]

    def test_unc_path_mixed_slash_backslash_returns_400(self, client):
        """Mixed-separator UNC path (/\\server/share/video.mp4) must be rejected → 400."""
        response = client.post(
            "/api/fs/exists",
            json={"paths": ["/\\server/share/video.mp4"]},
        )
        assert response.status_code == 400
        assert "UNC paths are not allowed" in response.json()["detail"]

    def test_path_too_long_returns_400(self, client):
        """A path exceeding 500 chars → 400."""
        long_name = "a" * 600
        response = client.post(
            "/api/fs/exists",
            json={"paths": [f"C:\\Packs\\{long_name}.mp4"]},
        )
        assert response.status_code == 400

    def test_paths_not_a_list_returns_400(self, client):
        """'paths' as a string instead of list → 400."""
        response = client.post("/api/fs/exists", json={"paths": "C:\\file.mp4"})
        assert response.status_code == 400

    def test_path_not_a_string_returns_400(self, client):
        """A non-string element in paths list → 400."""
        response = client.post("/api/fs/exists", json={"paths": [123]})
        assert response.status_code == 400
