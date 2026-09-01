import importlib.util
from pathlib import Path
from unittest import mock

import pytest

from empornium_megapack import images
from empornium_megapack.config import Settings

TASK_PY = Path(__file__).resolve().parent.parent.parent / "plugin" / "task.py"


def _load_task_module():
    spec = importlib.util.spec_from_file_location("plugin_task_gallery", str(TASK_PY))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestScreenTimestamps:
    def test_samples_avoid_the_head_and_tail(self):
        """Opening logos and end cards make terminal frames useless as thumbnails."""
        stamps = images.screen_timestamps(1000.0, 10)

        assert len(stamps) == 10
        assert stamps[0] == pytest.approx(50.0)
        assert stamps[-1] == pytest.approx(950.0)

    def test_samples_are_evenly_spaced_and_ordered(self):
        stamps = images.screen_timestamps(600.0, 5)

        gaps = [b - a for a, b in zip(stamps, stamps[1:])]
        assert gaps == pytest.approx([gaps[0]] * len(gaps))
        assert stamps == sorted(stamps)

    def test_single_screen_lands_mid_scene(self):
        assert images.screen_timestamps(100.0, 1) == pytest.approx([50.0])

    @pytest.mark.parametrize("duration,count", [(0, 10), (-5, 10), (600, 0), (600, -1)])
    def test_degenerate_inputs_yield_nothing(self, duration, count):
        assert images.screen_timestamps(duration, count) == []


class TestExtractScreens:
    def test_returns_empty_without_ffmpeg_rather_than_raising(self, tmp_path):
        """Screens are decorative; a missing ffmpeg must not fail the build."""
        with mock.patch.object(images, "resolve_ffmpeg", return_value=None):
            assert images.extract_screens("v.mp4", tmp_path, "t", 10, settings=Settings()) == []

    def test_returns_empty_when_duration_is_unknown(self, tmp_path):
        with mock.patch.object(images, "resolve_ffmpeg", return_value="ffmpeg"), \
             mock.patch.object(images, "probe_duration", return_value=None):
            assert images.extract_screens("v.mp4", tmp_path, "t", 10, settings=Settings()) == []

    def test_only_frames_actually_written_are_returned(self, tmp_path):
        """A partial extraction still yields a usable, honest screens list."""

        def fake_run(cmd, **kwargs):
            target = Path(cmd[-1])
            # Simulate ffmpeg failing on the 3rd sample only.
            if target.name.endswith("_screen_03.jpg"):
                return mock.Mock(returncode=1)
            target.write_bytes(b"x" * 64)
            return mock.Mock(returncode=0)

        with mock.patch.object(images, "resolve_ffmpeg", return_value="ffmpeg"), \
             mock.patch.object(images.subprocess, "run", side_effect=fake_run):
            result = images.extract_screens(
                "v.mp4", tmp_path, "title", 5, settings=Settings(), duration=600.0
            )

        assert len(result) == 4
        assert all(p.is_file() for p in result)
        assert not (tmp_path / "title_screen_03.jpg").exists()

    def test_names_are_zero_padded_and_ordered(self, tmp_path):
        def fake_run(cmd, **kwargs):
            Path(cmd[-1]).write_bytes(b"x" * 64)
            return mock.Mock(returncode=0)

        with mock.patch.object(images, "resolve_ffmpeg", return_value="ffmpeg"), \
             mock.patch.object(images.subprocess, "run", side_effect=fake_run):
            result = images.extract_screens(
                "v.mp4", tmp_path, "title", 11, settings=Settings(), duration=600.0
            )

        names = [p.name for p in result]
        assert names[0] == "title_screen_01.jpg"
        assert names[-1] == "title_screen_11.jpg"
        assert names == sorted(names)


class TestFetchStashImage:
    def _settings(self):
        return Settings(stash_url="http://localhost:9999", stash_api_key="secret")

    def test_writes_bytes_and_sends_the_api_key(self, tmp_path):
        dest = tmp_path / "cover.jpg"
        response = mock.Mock(status_code=200, content=b"y" * 4096,
                             headers={"content-type": "image/jpeg"})

        with mock.patch.object(images.httpx, "get", return_value=response) as get:
            result = images.fetch_stash_image("/scene/12/screenshot", dest, settings=self._settings())

        assert result == dest
        assert dest.read_bytes() == b"y" * 4096
        assert get.call_args.kwargs["headers"]["ApiKey"] == "secret"
        assert get.call_args.args[0] == "http://localhost:9999/scene/12/screenshot"

    def test_generated_svg_avatars_are_rejected(self, tmp_path):
        """
        Stash renders an initials avatar as image/svg+xml for performers with
        no portrait. They differ per performer, so only the content type
        distinguishes them from real artwork.
        """
        dest = tmp_path / "p.jpg"
        response = mock.Mock(status_code=200, content=b"<svg>" + b"x" * 3000,
                             headers={"content-type": "image/svg+xml"})

        with mock.patch.object(images.httpx, "get", return_value=response):
            assert images.fetch_stash_image("/performer/1/image", dest, settings=self._settings()) is None
        assert not dest.exists()

    @pytest.mark.parametrize("mime", ["image/jpeg", "image/png", "image/webp"])
    def test_raster_formats_are_accepted(self, tmp_path, mime):
        dest = tmp_path / "p.img"
        response = mock.Mock(status_code=200, content=b"y" * 4096,
                             headers={"content-type": mime})

        with mock.patch.object(images.httpx, "get", return_value=response):
            assert images.fetch_stash_image("/performer/1/image", dest, settings=self._settings()) == dest

    def test_content_type_parameters_are_tolerated(self, tmp_path):
        dest = tmp_path / "p.jpg"
        response = mock.Mock(status_code=200, content=b"y" * 4096,
                             headers={"content-type": "image/jpeg; charset=binary"})

        with mock.patch.object(images.httpx, "get", return_value=response):
            assert images.fetch_stash_image("/performer/1/image", dest, settings=self._settings()) == dest

    def test_transport_errors_return_none(self, tmp_path):
        dest = tmp_path / "p.jpg"
        with mock.patch.object(images.httpx, "get", side_effect=images.httpx.ConnectError("down")):
            assert images.fetch_stash_image("/performer/1/image", dest, settings=self._settings()) is None

    def test_non_200_returns_none(self, tmp_path):
        dest = tmp_path / "p.jpg"
        response = mock.Mock(status_code=404, content=b"x" * 4096,
                             headers={"content-type": "image/jpeg"})
        with mock.patch.object(images.httpx, "get", return_value=response):
            assert images.fetch_stash_image("/performer/1/image", dest, settings=self._settings()) is None

    def test_unconfigured_stash_url_returns_none(self, tmp_path):
        with mock.patch.object(images.httpx, "get") as get:
            assert images.fetch_stash_image("/x", tmp_path / "a.jpg", settings=Settings(stash_url="")) is None
        get.assert_not_called()


class TestPerformerRefs:
    def setup_method(self):
        self.task = _load_task_module()

    def test_dicts_keep_the_id_needed_to_fetch_a_portrait(self):
        refs = self.task._extract_performer_refs([{"id": 7, "name": "Honey Dew"}])

        assert refs == [{"id": "7", "name": "Honey Dew"}]

    def test_legacy_name_only_payloads_still_parse(self):
        """Older builds sent bare names; those scenes just get no portraits."""
        refs = self.task._extract_performer_refs(["Auhneesh Nicole"])

        assert refs == [{"id": None, "name": "Auhneesh Nicole"}]

    def test_duplicate_names_collapse_and_order_is_preserved(self):
        refs = self.task._extract_performer_refs(
            [{"id": 1, "name": "B"}, {"id": 2, "name": "A"}, {"id": 3, "name": "B"}]
        )

        assert [r["name"] for r in refs] == ["B", "A"]

    def test_blank_and_malformed_entries_are_dropped(self):
        assert self.task._extract_performer_refs([{"name": "  "}, 42, None, {}]) == []


class TestBuildSingleSceneGallery:
    def setup_method(self):
        self.task = _load_task_module()

    def _settings(self, **kw):
        base = dict(single_scene_screens=3, include_performer_images=True, include_scene_cover=True)
        base.update(kw)
        return Settings(**base)

    def test_collects_cover_screens_and_portraits(self, tmp_path):
        def fake_fetch(endpoint, out_path, settings=None, **kw):
            Path(out_path).write_bytes(b"z" * 2048)
            return Path(out_path)

        with mock.patch.object(self.task._domain_config, "get_settings", return_value=self._settings()), \
             mock.patch.object(self.task, "_domain_fetch_stash_image", side_effect=fake_fetch), \
             mock.patch.object(self.task, "_domain_probe_duration", return_value=600.0), \
             mock.patch.object(self.task, "_domain_extract_screens",
                               return_value=[tmp_path / f"t_screen_{i:02d}.jpg" for i in (1, 2, 3)]):
            gallery = self.task.build_single_scene_gallery(
                video_path="v.mp4",
                artifact_dir=str(tmp_path),
                safe_title="t",
                scene={"id": 55},
                performer_refs=[{"id": "7", "name": "Honey Dew"}, {"id": "8", "name": "Anji"}],
            )

        assert gallery["cover"].endswith("t_cover.jpg")
        assert len(gallery["screens"]) == 3
        assert [p["name"] for p in gallery["performers"]] == ["Honey Dew", "Anji"]

    def test_performers_without_an_id_are_skipped(self, tmp_path):
        settings = self._settings(include_scene_cover=False, single_scene_screens=0)

        with mock.patch.object(self.task._domain_config, "get_settings", return_value=settings), \
             mock.patch.object(self.task, "_domain_fetch_stash_image") as fetch:
            gallery = self.task.build_single_scene_gallery(
                video_path="v.mp4", artifact_dir=str(tmp_path), safe_title="t",
                scene=None, performer_refs=[{"id": None, "name": "Nameless"}],
            )

        assert gallery["performers"] == []
        fetch.assert_not_called()

    def test_unreachable_stash_still_returns_a_gallery(self, tmp_path):
        """Cover and portraits are optional; the build must survive without them."""
        with mock.patch.object(self.task._domain_config, "get_settings", return_value=self._settings()), \
             mock.patch.object(self.task, "_domain_fetch_stash_image", return_value=None), \
             mock.patch.object(self.task, "_domain_probe_duration", return_value=600.0), \
             mock.patch.object(self.task, "_domain_extract_screens", return_value=[]):
            gallery = self.task.build_single_scene_gallery(
                video_path="v.mp4", artifact_dir=str(tmp_path), safe_title="t",
                scene={"id": 55}, performer_refs=[{"id": "7", "name": "Honey Dew"}],
            )

        assert gallery == {"cover": None, "screens": [], "performers": []}

    def test_screen_extraction_failure_is_contained(self, tmp_path):
        with mock.patch.object(self.task._domain_config, "get_settings", return_value=self._settings()), \
             mock.patch.object(self.task, "_domain_fetch_stash_image", return_value=None), \
             mock.patch.object(self.task, "_domain_probe_duration", side_effect=OSError("ffprobe gone")):
            gallery = self.task.build_single_scene_gallery(
                video_path="v.mp4", artifact_dir=str(tmp_path), safe_title="t",
                scene=None, performer_refs=[],
            )

        assert gallery["screens"] == []

    def test_each_element_can_be_disabled_by_config(self, tmp_path):
        settings = self._settings(
            single_scene_screens=0, include_performer_images=False, include_scene_cover=False
        )

        with mock.patch.object(self.task._domain_config, "get_settings", return_value=settings), \
             mock.patch.object(self.task, "_domain_fetch_stash_image") as fetch, \
             mock.patch.object(self.task, "_domain_extract_screens") as extract:
            gallery = self.task.build_single_scene_gallery(
                video_path="v.mp4", artifact_dir=str(tmp_path), safe_title="t",
                scene={"id": 55}, performer_refs=[{"id": "7", "name": "Honey Dew"}],
            )

        assert gallery == {"cover": None, "screens": [], "performers": []}
        fetch.assert_not_called()
        extract.assert_not_called()
