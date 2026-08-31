"""Tier 5 Domain Adversarial Test Suite.

Comprehensive white-box adversarial stress tests covering all domain modules in
`backend/deepseek_megapack/`:
- models.py
- config.py
- paths.py
- gql.py
- torrents.py
- images.py
- metadata.py
- build.py
- review.py
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import httpx
import pydantic
import pytest
import torf

from deepseek_megapack.build import (
    BuildError,
    CONTACT_SHEETS_DIR,
    PACK_ROOT_NAME,
    StagedScene,
    _link_or_copy,
    _same_volume,
    _title_stem,
    build_created_utc,
    make_bundle,
    new_pack_id,
    payload_size,
    sanitize_name,
    stage_payload,
    unique_names,
    write_manifest,
)
from deepseek_megapack.config import CONFIG_LOCAL, CONFIG_LOCAL_NAME, Settings, get_settings
from deepseek_megapack.gql import SCENE_QUERY, MOVE_FILES_MUTATION, StashClient, StashError
from deepseek_megapack.images import (
    HAMSTER_IMAGE_EXT,
    HAMSTER_IMAGE_MIME,
    HAMSTER_UPLOAD_URL,
    ContactSheetError,
    ImageService,
    _retry_after_seconds,
    _retry_delay,
    _vcsi_command,
    enforce_size_limit,
    generate_contact_sheet,
    resolve_ffmpeg,
    sha256_file,
    upload_hamster,
)
from deepseek_megapack.metadata import (
    MAX_NOTES_LEN,
    MAX_TAGS,
    MAX_TITLE_LEN,
    ImagePlaceholderError,
    bbcode_escape,
    empify,
    finalize_description,
    format_duration,
    join_names,
    merge_tags,
    normalize_meta_input,
    pack_performer_union,
    pack_studio,
    pack_title_default,
    render_description,
    resolution_for,
    scene_title_default,
    tag_sources_for_scene,
)
from deepseek_megapack.models import (
    ApplyRequest,
    ApplyResponse,
    BuildRequest,
    BuildResponse,
    BuiltScene,
    CleanupResponse,
    FileReview,
    ImagesRequest,
    ImagesResponse,
    Issue,
    MetaRequest,
    MetaResponse,
    MoveFilesRequest,
    MoveFilesResponse,
    MovedFileItem,
    PackMeta,
    PackMetaInput,
    PolicyInfo,
    ResolvedScene,
    ReviewRequest,
    ReviewResponse,
    SceneImage,
    SceneMeta,
    SceneMetaInput,
    SceneReview,
    WarningItem,
)
from deepseek_megapack.paths import (
    OSHASH_CHUNK,
    PathMapper,
    _key,
    oshash_file,
    verify_same_file,
)
from deepseek_megapack.review import (
    PackService,
    _epoch,
    _needs_copy,
    _normname,
    _os_creation_time,
    _scene_id_key,
)
from deepseek_megapack.torrents import (
    CREATED_BY,
    MAX_PIECE_EXPONENT,
    MIN_PIECE_SIZE,
    TorrentError,
    calculate_piece_size,
    create_torrent,
    piece_size_for,
    sanitize_announce_url,
    source_for_announce,
    validate_announce_url,
)


# ==============================================================================
# 1. MODELS.PY ADVERSARIAL TESTS
# ==============================================================================

class TestModelsAdversarial:
    """Adversarial validation and boundary checks for models.py."""

    def test_request_empty_scene_ids_validation_errors(self):
        """All request models requiring scene_ids must reject empty lists."""
        for model_cls in (
            ReviewRequest,
            MetaRequest,
            ApplyRequest,
            ImagesRequest,
            BuildRequest,
        ):
            with pytest.raises(pydantic.ValidationError) as exc_info:
                model_cls(scene_ids=[])
            assert "scene_ids" in str(exc_info.value)

        with pytest.raises(pydantic.ValidationError):
            MoveFilesRequest(scene_ids=[], destination_folder="C:/target")

    def test_issue_model_optional_fields_and_defaults(self):
        """Issue must permit None for optional fields scene_id and path."""
        issue = Issue(code="err_code", message="err_msg")
        assert issue.code == "err_code"
        assert issue.message == "err_msg"
        assert issue.scene_id is None
        assert issue.path is None

        issue_full = Issue(code="c", message="m", scene_id="s1", path="/p/f.mp4")
        assert issue_full.scene_id == "s1"
        assert issue_full.path == "/p/f.mp4"

    def test_file_review_optional_fields(self):
        """FileReview permits None for video properties and creation_time."""
        fr = FileReview(
            file_id="f1",
            basename="test.mp4",
            path="C:/media/test.mp4",
            size=1000,
            mod_time="2026-01-01T00:00:00Z",
            created_at="2026-01-01T00:00:00Z",
            time_source="mod_time",
            exists=True,
            accessible=True,
            will_copy=False,
        )
        assert fr.width is None
        assert fr.height is None
        assert fr.duration is None
        assert fr.video_codec is None
        assert fr.creation_time is None

    def test_scene_review_defaults(self):
        """SceneReview initializes empty collections by default."""
        sr = SceneReview(scene_id="s1")
        assert sr.title == ""
        assert sr.performers == []
        assert sr.tags == []
        assert sr.files == []
        assert sr.issues == []
        assert sr.needs_choice is False
        assert sr.provisional_file_id is None

    def test_scene_meta_input_and_pack_meta_input_defaults(self):
        """PackMetaInput and SceneMetaInput default structures."""
        smi = SceneMetaInput(scene_id="s1")
        assert smi.title == ""
        assert smi.fetch_mode == "copy"

        pmi = PackMetaInput()
        assert pmi.title == ""
        assert pmi.tags is None
        assert pmi.notes == ""
        assert pmi.scenes == []

    def test_response_models_serialization_roundtrip(self):
        """Responses must serialize and deserialize cleanly to/from JSON."""
        warning = WarningItem(code="warn1", message="warning message")
        issue = Issue(code="err1", message="error message", scene_id="s1")
        policy = PolicyInfo(name="creation", ascending=True, note="note")

        review_resp = ReviewResponse(
            policy=policy,
            scenes=[],
            warnings=[warning],
            errors=[issue],
        )
        data = review_resp.model_dump()
        rebuilt = ReviewResponse.model_validate(data)
        assert rebuilt.policy.name == "creation"
        assert rebuilt.warnings[0].code == "warn1"
        assert rebuilt.errors[0].code == "err1"


# ==============================================================================
# 2. CONFIG.PY ADVERSARIAL TESTS
# ==============================================================================

class TestConfigAdversarial:
    """Adversarial tests for configuration parsing and overrides."""

    def test_settings_default_values(self):
        """Settings initializes all default values correctly."""
        s = Settings()
        assert s.host == "127.0.0.1"
        assert s.port == 9941
        assert s.stash_url == "http://localhost:9999"
        assert s.stash_api_key == ""
        assert s.file_time_policy == "creation"
        assert s.file_time_ascending is True
        assert s.stash_fetch_workers == 8
        assert s.contact_sheet_layout == "3x6"
        assert s.contact_sheet_max_bytes == 10_000_000
        assert s.path_mappings == []

    def test_settings_env_overrides(self, monkeypatch):
        """Environment variables with DEEPSEEK_ prefix override settings."""
        monkeypatch.setenv("DEEPSEEK_PORT", "8888")
        monkeypatch.setenv("DEEPSEEK_FILE_TIME_ASCENDING", "false")
        monkeypatch.setenv("DEEPSEEK_STASH_API_KEY", "secret_key_123")
        monkeypatch.setenv("DEEPSEEK_HAMSTER_API_KEY", "hamster_xyz")

        s = Settings()
        assert s.port == 8888
        assert s.file_time_ascending is False
        assert s.stash_api_key == "secret_key_123"
        assert s.hamster_api_key == "hamster_xyz"

    def test_get_settings_loads_toml_safely(self, tmp_path, monkeypatch):
        """get_settings reads the local settings file if present without crashing on unknown keys."""
        toml_content = b"""
        [backend]
        port = 7777
        stash_api_key = "toml_key"
        unknown_future_key = "should_be_ignored"
        """
        toml_file = tmp_path / CONFIG_LOCAL_NAME
        toml_file.write_bytes(toml_content)

        monkeypatch.setattr("deepseek_megapack.config.CONFIG_LOCAL", toml_file)
        get_settings.cache_clear()
        try:
            s = get_settings()
            assert s.port == 7777
            assert s.stash_api_key == "toml_key"
            assert not hasattr(s, "unknown_future_key")
        finally:
            get_settings.cache_clear()


# ==============================================================================
# 3. PATHS.PY ADVERSARIAL TESTS
# ==============================================================================

class TestPathsAdversarial:
    """Adversarial stress-testing for path mapping and oshash checksums."""

    def test_oshash_small_file_edge_cases(self, tmp_path):
        """Files with <= 8 bytes raise ValueError; files with 9-16 bytes compute chunk correctly."""
        # 0 bytes
        f0 = tmp_path / "zero.bin"
        f0.write_bytes(b"")
        with pytest.raises(ValueError, match="8 bytes or fewer"):
            oshash_file(f0)

        # 8 bytes
        f8 = tmp_path / "eight.bin"
        f8.write_bytes(b"12345678")
        with pytest.raises(ValueError, match="8 bytes or fewer"):
            oshash_file(f8)

        # 9 bytes -> (9 // 8) * 8 = 8 byte chunk
        f9 = tmp_path / "nine.bin"
        f9.write_bytes(b"123456789")
        h9 = oshash_file(f9)
        assert len(h9) == 16

        # 16 bytes
        f16 = tmp_path / "sixteen.bin"
        f16.write_bytes(b"1234567890abcdef")
        h16 = oshash_file(f16)
        assert len(h16) == 16

    def test_oshash_nonexistent_file_raises_oserror(self, tmp_path):
        """oshash on missing file raises OSError."""
        with pytest.raises(OSError):
            oshash_file(tmp_path / "does_not_exist.bin")

    def test_oshash_large_file_and_exact_boundary(self, tmp_path):
        """Test exact chunk boundaries: 64 KiB and 128 KiB."""
        # Exact 64 KiB
        f64k = tmp_path / "64k.bin"
        f64k.write_bytes(b"A" * OSHASH_CHUNK)
        h64k = oshash_file(f64k)
        assert len(h64k) == 16

        # 128 KiB
        f128k = tmp_path / "128k.bin"
        f128k.write_bytes(b"B" * (OSHASH_CHUNK * 2))
        h128k = oshash_file(f128k)
        assert len(h128k) == 16

    def test_key_normalization(self):
        """_key normalizes separators, duplicate slashes, and case."""
        assert _key("/media/folder/file.mp4") == ("media", "folder", "file.mp4")
        assert _key(r"D:\Media\\Folder\FILE.mp4") == ("d:", "media", "folder", "file.mp4")
        assert _key("///a///b///c///") == ("a", "b", "c")

    def test_path_mapper_malformed_config_entries_ignored(self):
        """PathMapper safely ignores non-list, bad length, or empty mapping pairs."""
        settings = SimpleNamespace(
            path_mappings=[
                "not-a-list",
                ["only-one-element"],
                ["a", "b", "c"],  # 3 elements
                ["", "D:\\local"],
                ["/remote", ""],
                ["/valid/remote", "D:\\ValidLocal"],
            ]
        )
        mapper = PathMapper(settings)
        assert mapper.apply("/valid/remote/video.mp4") == "D:\\ValidLocal/video.mp4"
        assert mapper.apply("/other/path.mp4") == "/other/path.mp4"

    def test_path_mapper_prefix_word_boundary_safety(self):
        """Mapping for /media must NOT match /media_other or /mediatree."""
        settings = Settings(path_mappings=[["/media", "D:\\Media"]])
        mapper = PathMapper(settings)
        assert mapper.apply("/media/sub/scene.mp4") == "D:\\Media/sub/scene.mp4"
        assert mapper.apply("/media") == "D:\\Media"
        assert mapper.apply("/media_backup/scene.mp4") == "/media_backup/scene.mp4"
        assert mapper.apply("/mediatree/scene.mp4") == "/mediatree/scene.mp4"

    def test_path_mapper_case_insensitive_tail_preservation(self):
        """Remote prefix matches case-insensitively while local tail casing is preserved."""
        settings = Settings(path_mappings=[["/MEDIA/LIBRARY", "E:\\StashStorage"]])
        mapper = PathMapper(settings)
        assert mapper.apply("/media/library/Actor/MyVideo.MP4") == "E:\\StashStorage/Actor/MyVideo.MP4"
        assert mapper.resolve("/media/library/Actor/MyVideo.MP4") == "E:\\StashStorage/Actor/MyVideo.MP4"

    def test_verify_same_file_adversarial(self, tmp_path):
        """Test verify_same_file edge cases: unmapped bypass, size mismatch, oshash fail-closed."""
        f = tmp_path / "test.mp4"
        f.write_bytes(b"\x01" * 100)

        # Unmapped: always trusted even if expected_size is None or wrong
        assert verify_same_file(str(f), expected_size=None, mapped=False) is True
        assert verify_same_file(str(f), expected_size=999, mapped=False) is True

        # Mapped with expected_size=None: returns True
        assert verify_same_file(str(f), expected_size=None, mapped=True) is True

        # Mapped with size mismatch
        assert verify_same_file(str(f), expected_size=100, mapped=True) is True
        assert verify_same_file(str(f), expected_size=50, mapped=True) is False

        # Mapped with missing file
        assert verify_same_file(str(tmp_path / "missing.mp4"), expected_size=100, mapped=True) is False

        # Mapped with oshash check
        good_osh = oshash_file(f)
        assert verify_same_file(str(f), expected_size=100, mapped=True, expected_oshash=good_osh) is True
        assert verify_same_file(str(f), expected_size=100, mapped=True, expected_oshash="wrongoshash12345") is False

        # Mapped with file <= 8 bytes when oshash is expected (fails closed)
        f_tiny = tmp_path / "tiny.bin"
        f_tiny.write_bytes(b"123")
        assert verify_same_file(str(f_tiny), expected_size=3, mapped=True, expected_oshash="anyhash") is False


# ==============================================================================
# 4. GQL.PY ADVERSARIAL TESTS
# ==============================================================================

class TestGqlAdversarial:
    """Adversarial tests for Stash GraphQL client error handling and queries."""

    def test_stash_client_api_key_header(self, monkeypatch):
        """ApiKey header is added when stash_api_key is set, omitted when empty."""
        captured_headers = {}

        def fake_post(url, **kwargs):
            captured_headers.update(kwargs.get("headers", {}))
            return MagicMock(status_code=200, json=lambda: {"data": {"findScene": None}})

        monkeypatch.setattr("httpx.post", fake_post)

        # With API key
        client_with_key = StashClient(Settings(stash_api_key="secret_token"))
        client_with_key.find_scene("1")
        assert captured_headers.get("ApiKey") == "secret_token"

        # Without API key
        captured_headers.clear()
        client_no_key = StashClient(Settings(stash_api_key=""))
        client_no_key.find_scene("1")
        assert "ApiKey" not in captured_headers

    def test_stash_client_network_error_raises_stasherror(self, monkeypatch):
        """Network connection failure raises StashError."""
        def fake_post(url, **kwargs):
            raise httpx.ConnectError("Connection refused")

        monkeypatch.setattr("httpx.post", fake_post)
        client = StashClient()
        with pytest.raises(StashError, match="Stash unreachable"):
            client.find_scene("1")

    def test_stash_client_non_200_http_status_raises_stasherror(self, monkeypatch):
        """Non-200 HTTP responses raise StashError."""
        def fake_post(url, **kwargs):
            return MagicMock(status_code=500, json=lambda: {})

        monkeypatch.setattr("httpx.post", fake_post)
        client = StashClient()
        with pytest.raises(StashError, match="HTTP 500"):
            client.find_scene("1")

    def test_stash_client_graphql_errors_raises_stasherror(self, monkeypatch):
        """GraphQL payload containing errors array raises StashError."""
        def fake_post(url, **kwargs):
            return MagicMock(
                status_code=200,
                json=lambda: {"errors": [{"message": "Scene not found in database"}]},
            )

        monkeypatch.setattr("httpx.post", fake_post)
        client = StashClient()
        with pytest.raises(StashError, match="Scene not found in database"):
            client.find_scene("1")

    def test_stash_client_fetch_scenes_empty_and_multithreaded(self, monkeypatch):
        """fetch_scenes handles empty lists and maps scene results."""
        client = StashClient()
        assert client.fetch_scenes([]) == {}

        def fake_post(url, json, **kwargs):
            sid = json["variables"]["id"]
            if sid == "1":
                return MagicMock(status_code=200, json=lambda: {"data": {"findScene": {"id": "1", "title": "Scene 1"}}})
            return MagicMock(status_code=200, json=lambda: {"data": {"findScene": None}})

        monkeypatch.setattr("httpx.post", fake_post)
        results = client.fetch_scenes(["1", "2"])
        assert results["1"]["title"] == "Scene 1"
        assert results["2"] is None

    def test_stash_client_move_files_with_destination_folder_id(self, monkeypatch):
        """move_files passes destination_folder_id variable when provided."""
        recorded_vars = {}

        def fake_post(url, json, **kwargs):
            recorded_vars.update(json["variables"])
            return MagicMock(status_code=200, json=lambda: {"data": {"moveFiles": True}})

        monkeypatch.setattr("httpx.post", fake_post)
        client = StashClient()
        success = client.move_files(["f1", "f2"], "D:/Dest", destination_folder_id="folder-99")
        assert success is True
        assert recorded_vars["input"]["ids"] == ["f1", "f2"]
        assert recorded_vars["input"]["destination_folder"] == "D:/Dest"
        assert recorded_vars["input"]["destination_folder_id"] == "folder-99"


# ==============================================================================
# 5. TORRENTS.PY ADVERSARIAL TESTS
# ==============================================================================

class TestTorrentsAdversarial:
    """Adversarial stress-testing for torrent generation and helper piece sizes."""

    @pytest.mark.parametrize(
        "total_bytes,expected_piece_size",
        [
            (-1000, MIN_PIECE_SIZE),
            (0, MIN_PIECE_SIZE),
            (1, MIN_PIECE_SIZE),
            (1024, MIN_PIECE_SIZE),
            (16 * 1024 * 1024, MIN_PIECE_SIZE),  # 16 MiB -> 16 KiB
            (32 * 1024 * 1024 - 1, MIN_PIECE_SIZE),  # < 32 MiB -> 16 KiB (exponent 14)
            (32 * 1024 * 1024, 32768),           # 32 MiB -> 32 KiB (exponent 15)
            (1024 * 1024 * 1024, 1024 * 1024),   # 1 GiB -> 1 MiB (2^20)
            (8 * 1024 * 1024 * 1024, 8 * 1024 * 1024),  # 8 GiB -> 8 MiB (2^23)
            (100 * 1024 * 1024 * 1024, 8 * 1024 * 1024),  # 100 GiB -> capped 8 MiB
        ],
    )
    def test_piece_size_for_boundaries(self, total_bytes, expected_piece_size):
        """piece_size_for clamps correctly to [16 KiB, 8 MiB] with 1 MiB / GiB scaling."""
        assert piece_size_for(total_bytes) == expected_piece_size
        assert calculate_piece_size(total_bytes) == expected_piece_size

    @pytest.mark.parametrize(
        "url,expected_source",
        [
            ("http://tracker.empornium.me:2710/announce", "Emp"),
            ("https://empornium.is/announce", "Emp"),
            ("http://tracker.enthralled.eu/announce", "Ent"),
            ("http://tracker.femdomcult.net/announce", "FDC"),
            ("http://tracker.happyfappy.org/announce", "HF"),
            ("http://tracker.kufirc.com/announce", "Kufirc"),
            ("http://tracker.pornbay.org/announce", "PBay"),
            ("http://tracker.customsite.org:8080/announce", "tracker.customsite.org"),
            ("", ""),
        ],
    )
    def test_source_for_announce_all_known_trackers(self, url, expected_source):
        """source_for_announce identifies tracker aliases accurately."""
        assert source_for_announce(url) == expected_source

    def test_validate_announce_url_adversarial(self):
        """validate_announce_url rejects invalid schemes, empty hosts, and missing endpoints."""
        with pytest.raises(TorrentError, match="http or https"):
            validate_announce_url("ftp://tracker.empornium.me/announce")
        with pytest.raises(TorrentError, match="http or https"):
            validate_announce_url("file:///announce")
        with pytest.raises(TorrentError, match="no host"):
            validate_announce_url("http:///announce")
        with pytest.raises(TorrentError, match="does not look like an announce endpoint"):
            validate_announce_url("http://tracker.empornium.me/scrape.php")

        # Valid shapes
        validate_announce_url("http://tracker.empornium.sx:2710/tok1/tok2/announce")
        validate_announce_url("https://tracker.empornium.me/announce.php?passkey=123")

    def test_sanitize_announce_url_complex_query_and_paths(self):
        """sanitize_announce_url masks passkeys and token path segments."""
        # Query with multiple params
        multi_q = "https://tracker.emp.me/announce.php?user=42&passkey=secretpass12345678901234567890&action=1"
        sanitized = sanitize_announce_url(multi_q)
        assert "secretpass" not in sanitized
        assert "passkey=" + "x" * 32 in sanitized
        assert "user=42" in sanitized

        # Path with 3 segments before announce
        path_url = "http://tracker.emp.me:2710/token1/token2/token3/announce"
        san_path = sanitize_announce_url(path_url)
        assert "token1" not in san_path
        assert "token2" not in san_path
        assert "token3" not in san_path
        assert san_path.endswith("/announce?passkey=" + "x" * 32)

    def test_create_torrent_callback_streaming(self, tmp_path):
        """create_torrent passes and executes progress callback during hashing."""
        payload_dir = tmp_path / "payload"
        payload_dir.mkdir()
        (payload_dir / "test.mp4").write_bytes(b"\x00" * (128 * 1024))
        out_torrent = tmp_path / "out.torrent"

        callback_calls = []

        def on_progress(t, filepath, done, total):
            callback_calls.append((filepath, done, total))

        meta = create_torrent(
            payload_dir=payload_dir,
            announce_url="http://tracker.empornium.me:2710/announce",
            out_path=out_torrent,
            callback=on_progress,
        )

        assert meta["total_bytes"] == 128 * 1024
        assert out_torrent.is_file()
        assert len(callback_calls) > 0


# ==============================================================================
# 6. IMAGES.PY ADVERSARIAL TESTS
# ==============================================================================

class TestImagesAdversarial:
    """Adversarial testing for contact sheet generation, vcsi, and HamsterImg."""

    def test_sha256_file_empty_and_large(self, tmp_path):
        """sha256_file computes known digests on empty and chunked files."""
        empty_file = tmp_path / "empty.bin"
        empty_file.write_bytes(b"")
        assert (
            sha256_file(empty_file)
            == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        )

        # 2 MB file
        large_file = tmp_path / "2mb.bin"
        large_file.write_bytes(b"\xaa" * (2 * 1024 * 1024))
        digest = sha256_file(large_file)
        assert len(digest) == 64

    def test_resolve_ffmpeg_priority_order(self, tmp_path, monkeypatch):
        """resolve_ffmpeg checks settings, then which, then cove fallback."""
        # Priority 1: Settings
        s1 = Settings(ffmpeg_binary="C:/custom/ffmpeg.exe")
        assert resolve_ffmpeg(s1) == "C:/custom/ffmpeg.exe"

        # Priority 2: shutil.which
        monkeypatch.setattr("shutil.which", lambda name: "C:/path/ffmpeg.exe" if name == "ffmpeg" else None)
        s2 = Settings()
        assert resolve_ffmpeg(s2) == "C:/path/ffmpeg.exe"

        # Priority 3: cove fallback
        monkeypatch.setattr("shutil.which", lambda name: None)
        cove_mock = tmp_path / "cove_ffmpeg.exe"
        cove_mock.touch()
        monkeypatch.setattr("deepseek_megapack.images._COVE_FFMPEG", cove_mock)
        assert resolve_ffmpeg(s2) == str(cove_mock)

        # Priority 4: None
        monkeypatch.setattr("deepseek_megapack.images._COVE_FFMPEG", tmp_path / "nonexistent.exe")
        monkeypatch.setattr("deepseek_megapack.images.Path.home", classmethod(lambda cls: tmp_path))
        assert resolve_ffmpeg(s2) is None

    def test_vcsi_command_resolution_and_missing_error(self, tmp_path, monkeypatch):
        """_vcsi_command resolves candidates or raises ContactSheetError."""
        # Priority 1: Settings
        s1 = Settings(vcsi_binary="C:/tools/vcsi.exe")
        assert _vcsi_command(s1) == ["C:/tools/vcsi.exe"]

        # Missing everywhere
        monkeypatch.setattr("shutil.which", lambda name: None)
        monkeypatch.setattr("sys.executable", str(tmp_path / "nonexistent_python.exe"))
        s2 = Settings()
        with pytest.raises(ContactSheetError, match="vcsi not found on PATH"):
            _vcsi_command(s2)

    def test_upload_hamster_error_payload_extraction(self, tmp_path, monkeypatch):
        """upload_hamster extracts API error messages from string or dict responses."""
        img = tmp_path / "test.jpg"
        img.write_bytes(b"\xff\xd8data")

        # Error response as string
        mock_response_str = MagicMock(
            status_code=400,
            json=lambda: {"error": "Invalid API key format"},
        )
        monkeypatch.setattr(
            "httpx.Client.post",
            lambda *args, **kwargs: mock_response_str,
        )
        with pytest.raises(ContactSheetError, match="Invalid API key format"):
            upload_hamster(img, Settings(hamster_api_key="key"))

        # Error response as dict with message
        mock_response_dict = MagicMock(
            status_code=400,
            json=lambda: {"error": {"message": "Account suspended"}},
        )
        monkeypatch.setattr(
            "httpx.Client.post",
            lambda *args, **kwargs: mock_response_dict,
        )
        with pytest.raises(ContactSheetError, match="Account suspended"):
            upload_hamster(img, Settings(hamster_api_key="key"))

    def test_image_service_temp_file_cleanup_on_failure(self, tmp_path, monkeypatch):
        """ImageService cleans up temp .vcsi.jpg files even when generation fails."""
        service = ImageService(Settings(staging_dir=tmp_path / "staging"))

        def failing_gen(video, out, settings, layout):
            Path(out).write_bytes(b"partial")
            return False

        monkeypatch.setattr(
            "deepseek_megapack.images.generate_contact_sheet",
            failing_gen,
        )
        with pytest.raises(ContactSheetError, match="generation failed"):
            service.contact_sheet("scene_fail", str(tmp_path / "vid.mp4"))

        # Confirm temporary file was deleted
        temp_file = service.sheet_dir / ".scene_fail.vcsi.jpg"
        assert not temp_file.exists()


# ==============================================================================
# 7. METADATA.PY ADVERSARIAL TESTS
# ==============================================================================

class TestMetadataAdversarial:
    """Adversarial tests for metadata rendering, escaping, and BBCode generation."""

    @pytest.mark.parametrize(
        "height,expected",
        [
            (None, ""),
            (0, ""),
            (-100, ""),
            (143, ""),
            (144, "144p"),
            (720, "720p"),
            (1080, "1080p"),
            (1440, "1440p"),
            (1920, "2160p"),
            (2560, "5K"),
            (3000, "6K"),
            (3584, "7K"),
            (3840, "8K"),
            (6143, "8K+"),
            (10000, "8K+"),
        ],
    )
    def test_resolution_for_all_tiers(self, height, expected):
        """resolution_for covers all ladder thresholds accurately."""
        assert resolution_for(height) == expected

    @pytest.mark.parametrize(
        "seconds,expected",
        [
            (None, ""),
            (0, ""),
            (-50, ""),
            (0.5, "0:00"),
            (59.9, "0:59"),
            (60, "1:00"),
            (3599, "59:59"),
            (3600, "1:00:00"),
            (7325, "2:02:05"),
        ],
    )
    def test_format_duration_edge_cases(self, seconds, expected):
        """format_duration formats minutes and hours cleanly."""
        assert format_duration(seconds) == expected

    def test_join_names_all_counts(self):
        """join_names handles 0, 1, 2, and N items."""
        assert join_names([]) == ""
        assert join_names(["Alice"]) == "Alice"
        assert join_names(["Alice", "Bob"]) == "Alice & Bob"
        assert join_names(["Alice", "Bob", "Charlie"]) == "Alice, Bob & Charlie"
        assert join_names(["A", "B", "C", "D"]) == "A, B, C & D"

    def test_empify_adversarial_strings(self):
        """empify handles special characters, unicode, and length bounds."""
        assert empify("") == ""
        assert empify("!!!@@@###$$$") == ""
        assert empify("tag_name_123") == "tag.name.123"
        assert empify("  Multiple   Spaces   And---Dashes  ") == "multiple.spaces.and.dashes"
        assert empify("a" * 100) == "a" * 32

    def test_bbcode_escape_control_chars_and_brackets(self):
        """bbcode_escape neutralizes brackets and removes non-printable ASCII."""
        raw = "Hello [b]World[/b]\x00\x07\x1f\nLine 2"
        # keep_newlines=False collapses all whitespace
        escaped_single = bbcode_escape(raw, keep_newlines=False)
        assert "&#91;b&#93;" in escaped_single
        assert "&#91;/b&#93;" in escaped_single
        assert "\x00" not in escaped_single
        assert "\n" not in escaped_single

        # keep_newlines=True preserves newline
        escaped_multi = bbcode_escape(raw, keep_newlines=True)
        assert "\n" in escaped_multi

    def test_pack_title_default_variations(self):
        """pack_title_default formats single studio, date ranges, and performer caps."""
        # Single studio, 5 performers, dates
        scenes = [
            SimpleNamespace(
                studio="StudioX",
                performers=["P1", "P2", "P3", "P4", "P5"],
                date="2026-01-01",
            ),
            SimpleNamespace(
                studio="StudioX",
                performers=["P1", "P2"],
                date="2026-02-01",
            ),
        ]
        title = pack_title_default(scenes)
        assert "[StudioX]" in title
        assert "Megapack (2 scenes)" in title
        assert "(2026-01-01 to 2026-02-01)" in title
        assert "+1 more" in title

        # Multiple studios -> studio omitted
        scenes[1].studio = "StudioY"
        assert "[StudioX]" not in pack_title_default(scenes)

    def test_finalize_description_adversarial_errors(self):
        """finalize_description enforces the no-placeholder guarantee."""
        # No placeholders in template
        with pytest.raises(ImagePlaceholderError, match="no image placeholders"):
            finalize_description("No image tags here", ["http://url1"])

        # Placeholder index out of range
        with pytest.raises(ImagePlaceholderError, match="unresolved placeholder"):
            finalize_description("{scene-image-2}", ["http://url1"])

        # Empty URL in replacement list
        with pytest.raises(ImagePlaceholderError, match="unresolved placeholder"):
            finalize_description("{scene-image-1}", [""])

        # Leftover placeholder
        with pytest.raises(ImagePlaceholderError, match="unresolved placeholder"):
            finalize_description("{scene-image-1} {scene-image-2}", ["http://url1"])


# ==============================================================================
# 8. BUILD.PY ADVERSARIAL TESTS
# ==============================================================================

class TestBuildAdversarial:
    """Adversarial tests for filesystem safety, name sanitization, and packaging."""

    @pytest.mark.parametrize(
        "raw_name,expected_prefix",
        [
            ("CON", "_CON"),
            ("prn", "_prn"),
            ("AUX", "_AUX"),
            ("nul", "_nul"),
            ("com1", "_com1"),
            ("COM9", "_COM9"),
            ("lpt1", "_lpt1"),
            ("LPT9", "_LPT9"),
        ],
    )
    def test_sanitize_name_windows_reserved_device_names(self, raw_name, expected_prefix):
        """sanitize_name protects against Windows reserved device names."""
        assert sanitize_name(raw_name) == expected_prefix

    def test_sanitize_name_invalid_characters_and_whitespace(self):
        """sanitize_name replaces invalid characters and collapses whitespace."""
        assert sanitize_name('file:name*with?bad"chars<and>pipes|') == "file_name_with_bad_chars_and_pipes_"
        assert sanitize_name("   ...   leading and trailing .   ") == "leading and trailing"
        assert sanitize_name("") == "Untitled"
        assert sanitize_name(None) == "Untitled"

    def test_sanitize_name_extension_preservation_on_truncation(self):
        """sanitize_name preserves media extensions when trimming long names."""
        long_name = "A" * 150 + ".mkv"
        cleaned = sanitize_name(long_name, max_len=50)
        assert len(cleaned) <= 50
        assert cleaned.endswith(".mkv")

    def test_unique_names_collision_chains(self):
        """unique_names handles chains of identical and pre-numbered basenames."""
        inputs = ["Scene", "scene", "SCENE (2)", "scene", "SCENE (2)"]
        outputs = unique_names(inputs)
        assert outputs == [
            "Scene",
            "scene (2)",
            "SCENE (2) (2)",
            "scene (3)",
            "SCENE (2) (3)",
        ]

    def test_title_stem_media_extensions(self):
        """_title_stem strips media extensions while leaving regular titles alone."""
        assert _title_stem("Scene One.mp4") == "Scene One"
        assert _title_stem("Scene Two.MKV") == "Scene Two"
        assert _title_stem("Scene Three.avi") == "Scene Three"
        assert _title_stem("Scene.Four.1080p") == "Scene.Four.1080p"

    def test_link_or_copy_missing_source_raises_build_error(self, tmp_path):
        """_link_or_copy raises BuildError if source file is missing."""
        missing = tmp_path / "missing.mp4"
        dest = tmp_path / "dest.mp4"
        with pytest.raises(BuildError, match="Source file missing"):
            _link_or_copy(missing, dest, prefer_hardlink=True)

    def test_make_bundle_creates_valid_zip_with_extras(self, tmp_path):
        """make_bundle creates a valid uncompressed zip containing payload and extras."""
        pack_root = tmp_path / "staging" / "packs" / "p1" / "My Pack"
        pack_root.mkdir(parents=True)
        (pack_root / "video.mp4").write_bytes(b"\x00" * 100)

        extra_artifact = tmp_path / "p1.torrent"
        extra_artifact.write_bytes(b"torrent_bytes")

        bundle_path = tmp_path / "p1.bundle.zip"
        make_bundle(pack_root, bundle_path, extras=[(extra_artifact, "p1.torrent")])

        assert bundle_path.is_file()
        with zipfile.ZipFile(bundle_path) as zf:
            names = zf.namelist()
            assert "p1.torrent" in names
            assert any(n.endswith("My Pack/video.mp4") for n in names)


# ==============================================================================
# 9. REVIEW.PY ADVERSARIAL TESTS
# ==============================================================================

class TestReviewAdversarial:
    """Adversarial tests for pack lifecycle, indexing, and move_files."""

    def test_scene_id_key_sorting(self):
        """_scene_id_key sorts numeric IDs by integer value, string IDs as strings."""
        assert _scene_id_key("42") == (0, 42)
        assert _scene_id_key("100") == (0, 100)
        assert _scene_id_key("scene_abc") == (1, "scene_abc")
        assert _scene_id_key("42") < _scene_id_key("100")
        assert _scene_id_key("100") < _scene_id_key("scene_abc")

    def test_pack_id_validation_path_traversal(self, tmp_path):
        """_validate_pack_id rejects invalid regex and directory traversal attempts."""
        svc = PackService(Settings(staging_dir=tmp_path / "staging"))

        with pytest.raises(ValueError, match="invalid pack id"):
            svc._validate_pack_id("../bad")

        with pytest.raises(ValueError, match="invalid pack id"):
            svc._validate_pack_id("12345")  # not 10 chars

        with pytest.raises(ValueError, match="invalid pack id"):
            svc._validate_pack_id("012345678g")  # not hex

        # Valid 10-char hex
        valid = "0123456789"
        path = svc._validate_pack_id(valid)
        assert path.name == valid

    def test_pack_index_atomic_persistence_and_recovery(self, tmp_path):
        """Pack registry writes atomically and safely restores across restarts."""
        out_dir = tmp_path / "output"
        svc1 = PackService(Settings(output_dir=out_dir))
        svc1.packs["0123456789"] = {
            "title": "Pack Alpha",
            "torrent": "0123456789.torrent",
            "manifest": "0123456789.manifest.json",
            "description": "0123456789.description.txt",
            "bundle": "0123456789.bundle.zip",
        }
        svc1._persist_index()

        # Restart service and verify index was restored
        svc2 = PackService(Settings(output_dir=out_dir))
        assert "0123456789" in svc2.packs
        assert svc2.packs["0123456789"]["title"] == "Pack Alpha"

    def test_move_files_missing_destination_error(self):
        """move_files returns missing_destination issue when destination is empty."""
        svc = PackService(stash=MagicMock())
        req = MoveFilesRequest(scene_ids=["s1"], destination_folder="   ")
        resp = svc.move_files(req)
        assert resp.error_count == 1
        assert any(e.code == "missing_destination" for e in resp.errors)

    def test_move_files_stash_failure_handling(self):
        """move_files captures Stash mutation failure (returning False) cleanly."""
        mock_stash = MagicMock()
        mock_stash.fetch_scenes.return_value = {
            "s1": {
                "id": "s1",
                "title": "Scene 1",
                "files": [{"id": "f1", "path": "D:/Old/s1.mp4", "basename": "s1.mp4"}],
            }
        }
        mock_stash.move_files.return_value = False

        svc = PackService(stash=mock_stash)
        req = MoveFilesRequest(scene_ids=["s1"], destination_folder="D:/New")
        resp = svc.move_files(req)

        assert resp.error_count == 1
        assert resp.moved_count == 0
        assert resp.items[0].status == "error"
        assert any(e.code == "stash_move_failed" for e in resp.errors)

    def test_move_files_stash_exception_handling(self):
        """move_files captures Stash network or GraphQL exception cleanly."""
        mock_stash = MagicMock()
        mock_stash.fetch_scenes.return_value = {
            "s1": {
                "id": "s1",
                "title": "Scene 1",
                "files": [{"id": "f1", "path": "D:/Old/s1.mp4", "basename": "s1.mp4"}],
            }
        }
        mock_stash.move_files.side_effect = StashError("Connection reset by peer")

        svc = PackService(stash=mock_stash)
        req = MoveFilesRequest(scene_ids=["s1"], destination_folder="D:/New")
        resp = svc.move_files(req)

        assert resp.error_count == 1
        assert resp.moved_count == 0
        assert resp.items[0].status == "error"
        assert any(e.code == "stash_move_error" for e in resp.errors)
