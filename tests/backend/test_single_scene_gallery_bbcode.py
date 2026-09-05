"""
End-to-end shape of the single-scene BBCode gallery.

The gallery is what separates a single-scene release from a megapack: a cover,
a labelled performer row, a screens grid, and the contact sheet tucked into a
spoiler. Megapacks keep the flat one-thumbnail-per-scene list.
"""

import sys
from pathlib import Path
from unittest import mock

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
BACKEND_DIR = PROJECT_ROOT / "backend"
PLUGIN_DIR = PROJECT_ROOT / "plugin"

if str(BACKEND_DIR) in sys.path:
    sys.path.remove(str(BACKEND_DIR))
sys.path.insert(0, str(BACKEND_DIR))
if str(PLUGIN_DIR) not in sys.path:
    sys.path.append(str(PLUGIN_DIR))

import task  # noqa: E402

HOST = "https://hamsterimg.net/images"
COVER_URL = f"{HOST}/cover.jpg"
SCREEN_URLS = [f"{HOST}/screen{i}.jpg" for i in range(1, 4)]
SCREEN_THUMB_URLS = [f"{HOST}/thumb_screen{i}.jpg" for i in range(1, 4)]
PERFORMER_URLS = [f"{HOST}/perf1.jpg", f"{HOST}/perf2.jpg"]
PERFORMER_THUMB_URLS = [f"{HOST}/thumb_perf1.jpg", f"{HOST}/thumb_perf2.jpg"]


@pytest.fixture
def media(tmp_path):
    media_dir = tmp_path / "release"
    media_dir.mkdir(parents=True)
    media_file = media_dir / "anji.and.honey.mp4"
    media_file.write_bytes(b"MEDIA" * 4096)
    return media_dir, media_file


def _payload(media_dir, media_file, single_scene=True):
    return {
        "single_scene": single_scene,
        "pack_title": "Anji And Honey",
        "output_dir": str(media_dir),
        "scenes": [
            {
                "id": 55,
                "title": "Anji And Honey",
                "path": str(media_file),
                "studio": "Baddies Galleryy",
                "performers": [
                    {"id": "7", "name": "Auhneesh Nicole"},
                    {"id": "8", "name": "Honey Dew"},
                ],
                "tags": ["Big Ass", "POV"],
                "height": 2160,
                "duration": 1057,
            }
        ],
        "notes": "Volleyball court.",
    }


def _run(payload, gallery, contact_sheet_url=f"{HOST}/sheet.jpg"):
    """Run a build with the gallery and the image host both stubbed out."""

    def fake_upload(paths, config=None, progress_callback=None):
        urls = []
        if gallery.get("cover"):
            urls.append(COVER_URL)
        urls.extend(SCREEN_URLS[: len(gallery.get("screens") or [])])
        urls.extend(PERFORMER_URLS[: len(gallery.get("performers") or [])])
        urls.append(contact_sheet_url)
        urls.extend(SCREEN_THUMB_URLS[: len(gallery.get("screens") or [])])
        urls.extend(PERFORMER_THUMB_URLS[: len(gallery.get("performers") or [])])
        while len(urls) < len(paths):
            urls.append(contact_sheet_url)
        return urls[:len(paths)]

    with mock.patch.object(task, "build_single_scene_gallery", return_value=gallery), \
         mock.patch.object(task, "upload_previews", side_effect=fake_upload):
        return task.run_build_megapack(payload)


def _full_gallery(tmp_path):
    from PIL import Image
    for f in ["cover.jpg", "s0.jpg", "s1.jpg", "s2.jpg", "p1.jpg", "p2.jpg"]:
        p = tmp_path / f
        if not p.exists():
            Image.new("RGB", (400, 300), color=(100, 100, 100)).save(p, format="JPEG")
    return {
        "cover": str(tmp_path / "cover.jpg"),
        "screens": [str(tmp_path / f"s{i}.jpg") for i in range(3)],
        "performers": [
            {"name": "Auhneesh Nicole", "path": str(tmp_path / "p1.jpg")},
            {"name": "Honey Dew", "path": str(tmp_path / "p2.jpg")},
        ],
    }


class TestSingleSceneGalleryBBCode:
    def test_cover_is_rendered_full_width_and_centered(self, media, tmp_path):
        media_dir, media_file = media
        bbcode = _run(_payload(media_dir, media_file), _full_gallery(tmp_path))["bbcode"]

        assert f"[center][img]{COVER_URL}[/img][/center]" in bbcode

    def test_screens_are_a_thumbnail_grid_under_a_heading(self, media, tmp_path):
        media_dir, media_file = media
        bbcode = _run(_payload(media_dir, media_file), _full_gallery(tmp_path))["bbcode"]

        assert "[b]Screens[/b]" in bbcode
        for i, url in enumerate(SCREEN_URLS):
            thumb_url = SCREEN_THUMB_URLS[i]
            assert f"[url={url}][img={task.THUMB_WIDTH}]{thumb_url}[/img][/url]" in bbcode

    def test_performers_are_labelled_with_their_names(self, media, tmp_path):
        media_dir, media_file = media
        bbcode = _run(_payload(media_dir, media_file), _full_gallery(tmp_path))["bbcode"]

        assert "[b]Performers[/b]" in bbcode
        assert f"[img={task.PERFORMER_THUMB_WIDTH}]{PERFORMER_THUMB_URLS[0]}[/img] Auhneesh Nicole" in bbcode
        assert f"[img={task.PERFORMER_THUMB_WIDTH}]{PERFORMER_THUMB_URLS[1]}[/img] Honey Dew" in bbcode

    def test_contact_sheet_is_folded_into_a_spoiler(self, media, tmp_path):
        """It is the tallest image in the post and would otherwise dominate it."""
        media_dir, media_file = media
        bbcode = _run(_payload(media_dir, media_file), _full_gallery(tmp_path))["bbcode"]

        assert "[b]Contact Sheet[/b]" in bbcode
        spoiler = bbcode[bbcode.index("[spoiler=Show contact sheet]"):bbcode.index("[/spoiler]")]
        assert f"[img]{HOST}/sheet.jpg[/img]" in spoiler

    def test_sections_appear_in_reading_order(self, media, tmp_path):
        media_dir, media_file = media
        bbcode = _run(_payload(media_dir, media_file), _full_gallery(tmp_path))["bbcode"]

        assert (
            bbcode.index("[center][img]")
            < bbcode.index("[b]Performers[/b]")
            < bbcode.index("[b]Screens[/b]")
            < bbcode.index("[b]Contact Sheet[/b]")
        )

    def test_existing_header_layout_is_preserved(self, media, tmp_path):
        """The gallery is additive; the header the user already had must survive."""
        media_dir, media_file = media
        bbcode = _run(_payload(media_dir, media_file), _full_gallery(tmp_path))["bbcode"]

        assert "[color=#f5f8fa]Anji And Honey[/color]" in bbcode
        assert "STASH RELEASE · Baddies Galleryy" in bbcode
        assert "[color=#f5f8fa]Volleyball court.[/color]" in bbcode
        assert "[b][color=#8a9ba8]Studio[/color][/b][color=#5c7080]: [/color][color=#f5f8fa]Baddies Galleryy[/color]" in bbcode
        assert "[b][color=#8a9ba8]Performers[/color][/b][color=#5c7080]: [/color][color=#f5f8fa]Auhneesh Nicole & Honey Dew[/color]" in bbcode

    def test_every_uploaded_url_is_reported_for_the_preflight_gate(self, media, tmp_path):
        """images_remote checks this list, so a missed URL would pass a local path."""
        media_dir, media_file = media
        res = _run(_payload(media_dir, media_file), _full_gallery(tmp_path))

        expected_urls = [COVER_URL] + SCREEN_URLS + PERFORMER_URLS + [f"{HOST}/sheet.jpg"] + SCREEN_THUMB_URLS + PERFORMER_THUMB_URLS
        assert res["uploaded_urls"] == expected_urls
        assert res["submission_payload"]["image_urls"] == res["uploaded_urls"]


class TestGalleryDegradation:
    def test_empty_gallery_leaves_just_the_spoilered_contact_sheet(self, media, tmp_path):
        """No ffmpeg and no Stash still produces a valid, postable description."""
        media_dir, media_file = media
        empty = {"cover": None, "screens": [], "performers": []}

        bbcode = _run(_payload(media_dir, media_file), empty)["bbcode"]

        assert "[b]Screens[/b]" not in bbcode
        assert "[b]Performers[/b]\n" not in bbcode
        assert "[b]Contact Sheet[/b]" in bbcode
        assert f"[img]{HOST}/sheet.jpg[/img]" in bbcode

    def test_partial_gallery_omits_only_the_missing_sections(self, media, tmp_path):
        media_dir, media_file = media
        from PIL import Image
        s0 = tmp_path / "s0.jpg"
        Image.new("RGB", (400, 300), color=(100, 100, 100)).save(s0, format="JPEG")
        partial = {"cover": None, "screens": [str(s0)], "performers": []}

        bbcode = _run(_payload(media_dir, media_file), partial)["bbcode"]

        assert "[center][img]" not in bbcode
        assert "[b]Screens[/b]" in bbcode
        assert f"[url={SCREEN_URLS[0]}][img={task.THUMB_WIDTH}]{SCREEN_THUMB_URLS[0]}[/img][/url]" in bbcode


class TestMegapackUnchanged:
    def test_megapack_keeps_the_flat_linked_thumbnail_list(self, tmp_path):
        """Megapacks keep the flat list; only the single-scene gallery is excluded.

        The list itself is folded into a spoiler (see TestContactSheetSpoiler),
        but it stays a flat run of linked thumbnails rather than a gallery.
        """
        media_dir = tmp_path / "pack"
        media_dir.mkdir(parents=True)
        files = []
        for name in ("one.mp4", "two.mp4"):
            f = media_dir / name
            f.write_bytes(b"MEDIA" * 4096)
            files.append(f)

        payload = {
            "single_scene": False,
            "pack_title": "Pack",
            "output_dir": str(media_dir),
            "scenes": [
                {"id": i, "title": n.stem, "path": str(n), "performers": ["A"], "tags": ["X"], "height": 1080, "duration": 600}
                for i, n in enumerate(files, 1)
            ],
        }

        sheet_urls = [f"{HOST}/cs1.jpg", f"{HOST}/cs2.jpg"]
        with mock.patch.object(task, "build_single_scene_gallery") as gallery, \
             mock.patch.object(task, "upload_previews", return_value=sheet_urls):
            res = task.run_build_megapack(payload)

        gallery.assert_not_called()
        for url in sheet_urls:
            assert f"[url={url}][img={task.THUMB_WIDTH}]{url}[/img][/url]" in res["bbcode"]
        assert "[b]Screens[/b]" not in res["bbcode"]
        assert "[b]Performers[/b]" not in res["bbcode"]


class TestContactSheetSpoiler:
    """Sheets sit behind a click so a long pack does not open as a wall of images."""

    def test_megapack_sheet_wall_is_folded_away_and_counted(self, tmp_path):
        media_dir = tmp_path / "pack"
        media_dir.mkdir(parents=True)
        files = []
        for name in ("one.mp4", "two.mp4", "three.mp4"):
            f = media_dir / name
            f.write_bytes(b"MEDIA" * 4096)
            files.append(f)

        payload = {
            "single_scene": False,
            "pack_title": "Pack",
            "output_dir": str(media_dir),
            "scenes": [
                {"id": i, "title": n.stem, "path": str(n), "performers": ["A"],
                 "tags": ["X"], "height": 1080, "duration": 600}
                for i, n in enumerate(files, 1)
            ],
        }

        sheet_urls = [f"{HOST}/cs{i}.jpg" for i in range(1, 4)]
        with mock.patch.object(task, "upload_previews", return_value=sheet_urls):
            bbcode = task.run_build_megapack(payload)["bbcode"]

        assert "[b]Contact Sheets[/b]" in bbcode
        assert "[spoiler=Show 3 contact sheets]" in bbcode

        opened = bbcode.index("[spoiler=Show 3 contact sheets]")
        closed = bbcode.index("[/spoiler]")
        spoiler = bbcode[opened:closed]
        # every sheet is inside the fold, and none escaped above it
        for url in sheet_urls:
            assert url in spoiler
        assert "[img" not in bbcode[:opened]

    def test_the_joined_thumbnail_line_survives_the_fold(self, tmp_path):
        """nl2br: a newline between two [img] tags forces one thumbnail per row."""
        media_dir = tmp_path / "pack"
        media_dir.mkdir(parents=True)
        for name in ("one.mp4", "two.mp4"):
            (media_dir / name).write_bytes(b"MEDIA" * 4096)

        payload = {
            "single_scene": False,
            "pack_title": "Pack",
            "output_dir": str(media_dir),
            "scenes": [
                {"id": i, "title": n, "path": str(media_dir / n), "performers": ["A"],
                 "tags": ["X"], "height": 1080, "duration": 600}
                for i, n in enumerate(("one.mp4", "two.mp4"), 1)
            ],
        }

        sheet_urls = [f"{HOST}/cs1.jpg", f"{HOST}/cs2.jpg"]
        with mock.patch.object(task, "upload_previews", return_value=sheet_urls):
            bbcode = task.run_build_megapack(payload)["bbcode"]

        thumbs = [ln for ln in bbcode.split(chr(10)) if "[img=" in ln]
        assert len(thumbs) == 1, thumbs

    def test_label_is_singular_for_one_sheet(self):
        assert task._contact_sheet_spoiler_label(1) == "Show contact sheet"
        assert task._contact_sheet_spoiler_label(2) == "Show 2 contact sheets"
        assert task._contact_sheet_spoiler_label(130) == "Show 130 contact sheets"


class TestMetaPanelStyleGating:
    """Verifies render_meta_panel is emitted for plate/rail, falls back to flat lines for signature/off,
    and handles tag defaults correctly across single-scene and megapack builds.
    """

    @pytest.mark.parametrize("style", ["plate", "rail"])
    def test_single_scene_emits_panel_for_plate_and_rail(self, media, tmp_path, style):
        media_dir, media_file = media
        p = _payload(media_dir, media_file, single_scene=True)
        p["banner"] = style
        bbcode = _run(p, _full_gallery(tmp_path))["bbcode"]

        assert "[bg=#202b33][table=100%,nball,nopad]" in bbcode
        assert "[b][color=#8a9ba8]Studio[/color][/b][color=#5c7080]: [/color][color=#f5f8fa]Baddies Galleryy[/color]" in bbcode
        assert "[b][color=#8a9ba8]Performers[/color][/b][color=#5c7080]: [/color][color=#f5f8fa]Auhneesh Nicole & Honey Dew[/color]" in bbcode
        assert "[b][color=#8a9ba8]Tags[/color][/b][color=#5c7080]: [/color][color=#f5f8fa]Big Ass, POV[/color]" in bbcode
        assert "[color=#f5f8fa]Volleyball court.[/color]" in bbcode
        assert "[quote]" not in bbcode
        assert "\n[b]Studio:[/b]" not in bbcode
        assert "\n[b]Performers:[/b]" not in bbcode
        assert "\n[b]Tags:[/b]" not in bbcode

    @pytest.mark.parametrize("style", ["signature", "off"])
    def test_single_scene_emits_flat_lines_for_signature_and_off(self, media, tmp_path, style):
        media_dir, media_file = media
        p = _payload(media_dir, media_file, single_scene=True)
        p["banner"] = style
        bbcode = _run(p, _full_gallery(tmp_path))["bbcode"]

        assert "[table=100%,nball,nopad]" not in bbcode
        assert "\n[b]Studio:[/b] Baddies Galleryy" in bbcode
        assert "\n[b]Performers:[/b] Auhneesh Nicole & Honey Dew" in bbcode
        assert "\n[b]Tags:[/b] Big Ass, POV" in bbcode
        assert "[quote]Volleyball court.[/quote]" in bbcode

    def test_single_scene_has_no_fallback_for_missing_tags(self, media, tmp_path):
        media_dir, media_file = media
        # plate (panel)
        p_plate = _payload(media_dir, media_file, single_scene=True)
        p_plate["banner"] = "plate"
        p_plate["scenes"][0]["tags"] = []
        bbcode_plate = _run(p_plate, _full_gallery(tmp_path))["bbcode"]
        assert "[b][color=#8a9ba8]Tags[/color][/b]" not in bbcode_plate
        assert "Megapack" not in bbcode_plate

        # off (flat)
        p_off = _payload(media_dir, media_file, single_scene=True)
        p_off["banner"] = "off"
        p_off["scenes"][0]["tags"] = []
        bbcode_off = _run(p_off, _full_gallery(tmp_path))["bbcode"]
        assert "[b]Tags:[/b]" not in bbcode_off
        assert "Megapack" not in bbcode_off

    @pytest.mark.parametrize("style", ["plate", "rail"])
    def test_megapack_emits_panel_for_plate_and_rail(self, tmp_path, style):
        media_dir = tmp_path / f"pack_{style}"
        media_dir.mkdir(parents=True)
        files = []
        for name in ("one.mp4", "two.mp4"):
            f = media_dir / name
            f.write_bytes(b"MEDIA" * 4096)
            files.append(f)

        payload = {
            "single_scene": False,
            "pack_title": f"pack_{style}",
            "output_dir": str(media_dir),
            "banner": style,
            "scenes": [
                {"id": 1, "title": "Scene 1", "path": str(files[0]), "studio": "PackStudio", "performers": ["Alice"], "tags": ["TagA"], "height": 1080, "duration": 600},
                {"id": 2, "title": "Scene 2", "path": str(files[1]), "studio": "PackStudio", "performers": ["Bob"], "tags": ["TagB"], "height": 1080, "duration": 600},
            ],
            "notes": "Pack notes.",
        }

        with mock.patch.object(task, "upload_previews", return_value=[f"{HOST}/cs1.jpg", f"{HOST}/cs2.jpg"]):
            bbcode = task.run_build_megapack(payload)["bbcode"]

        assert "[bg=#202b33][table=100%,nball,nopad]" in bbcode
        assert "[b][color=#8a9ba8]Studio[/color][/b][color=#5c7080]: [/color][color=#f5f8fa]PackStudio[/color]" in bbcode
        assert "[b][color=#8a9ba8]Performers[/color][/b][color=#5c7080]: [/color][color=#f5f8fa]Alice & Bob[/color]" in bbcode
        assert "[b][color=#8a9ba8]Tags[/color][/b][color=#5c7080]: [/color][color=#f5f8fa]TagA, TagB[/color]" in bbcode
        assert "[color=#f5f8fa]Pack notes.[/color]" in bbcode
        assert "[quote]" not in bbcode
        assert "\n[b]Studio:[/b]" not in bbcode
        assert "\n[b]Performers:[/b]" not in bbcode
        assert "\n[b]Tags:[/b]" not in bbcode
        assert "\n[b]Scenes Included:[/b] 2" in bbcode

    @pytest.mark.parametrize("style", ["signature", "off"])
    def test_megapack_emits_flat_lines_for_signature_and_off(self, tmp_path, style):
        media_dir = tmp_path / f"pack_{style}"
        media_dir.mkdir(parents=True)
        files = []
        for name in ("one.mp4", "two.mp4"):
            f = media_dir / name
            f.write_bytes(b"MEDIA" * 4096)
            files.append(f)

        payload = {
            "single_scene": False,
            "pack_title": f"pack_{style}",
            "output_dir": str(media_dir),
            "banner": style,
            "scenes": [
                {"id": 1, "title": "Scene 1", "path": str(files[0]), "studio": "PackStudio", "performers": ["Alice"], "tags": ["TagA"], "height": 1080, "duration": 600},
                {"id": 2, "title": "Scene 2", "path": str(files[1]), "studio": "PackStudio", "performers": ["Bob"], "tags": ["TagB"], "height": 1080, "duration": 600},
            ],
            "notes": "Pack notes.",
        }

        with mock.patch.object(task, "upload_previews", return_value=[f"{HOST}/cs1.jpg", f"{HOST}/cs2.jpg"]):
            bbcode = task.run_build_megapack(payload)["bbcode"]

        assert "[table=100%,nball,nopad]" not in bbcode
        assert "\n[b]Studio:[/b] PackStudio" in bbcode
        assert "\n[b]Performers:[/b] Alice & Bob" in bbcode
        assert "\n[b]Tags:[/b] TagA, TagB" in bbcode
        assert "\n[b]Scenes Included:[/b] 2" in bbcode
        assert "[quote]Pack notes.[/quote]" in bbcode

    def test_megapack_no_tags_fallback_appears_in_panel_and_flat_lines(self, tmp_path):
        media_dir = tmp_path / "pack_notags"
        media_dir.mkdir(parents=True)
        files = []
        for name in ("one.mp4", "two.mp4"):
            f = media_dir / name
            f.write_bytes(b"MEDIA" * 4096)
            files.append(f)

        base_payload = {
            "single_scene": False,
            "pack_title": "pack_notags",
            "output_dir": str(media_dir),
            "scenes": [
                {"id": 1, "title": "Scene 1", "path": str(files[0]), "studio": "PackStudio", "performers": ["Alice"], "tags": [], "height": 1080, "duration": 600},
                {"id": 2, "title": "Scene 2", "path": str(files[1]), "studio": "PackStudio", "performers": ["Bob"], "tags": [], "height": 1080, "duration": 600},
            ],
        }

        # Panel path (plate)
        payload_plate = dict(base_payload, banner="plate")
        with mock.patch.object(task, "upload_previews", return_value=[f"{HOST}/cs1.jpg", f"{HOST}/cs2.jpg"]):
            bbcode_plate = task.run_build_megapack(payload_plate)["bbcode"]
        assert "[b][color=#8a9ba8]Tags[/color][/b][color=#5c7080]: [/color][color=#f5f8fa]Megapack[/color]" in bbcode_plate

        # Flat lines path (off)
        payload_off = dict(base_payload, banner="off")
        with mock.patch.object(task, "upload_previews", return_value=[f"{HOST}/cs1.jpg", f"{HOST}/cs2.jpg"]):
            bbcode_off = task.run_build_megapack(payload_off)["bbcode"]
        assert "\n[b]Tags:[/b] Megapack" in bbcode_off

    def test_fallback_to_flat_lines_when_render_meta_panel_is_none(self, media, tmp_path):
        media_dir, media_file = media
        p = _payload(media_dir, media_file, single_scene=True)
        p["banner"] = "plate"
        with mock.patch.object(task, "render_meta_panel", None):
            bbcode = _run(p, _full_gallery(tmp_path))["bbcode"]
        assert "\n[b]Studio:[/b] Baddies Galleryy" in bbcode
        assert "\n[b]Performers:[/b] Auhneesh Nicole & Honey Dew" in bbcode
        assert "\n[b]Tags:[/b] Big Ass, POV" in bbcode
        assert "[b][color=#8a9ba8]Studio[/color][/b]" not in bbcode

    def test_megapack_fallback_to_flat_lines_when_render_meta_panel_is_none(self, tmp_path):
        media_dir = tmp_path / "pack_fallback"
        media_dir.mkdir(parents=True)
        files = []
        for name in ("one.mp4", "two.mp4"):
            f = media_dir / name
            f.write_bytes(b"MEDIA" * 4096)
            files.append(f)

        payload = {
            "single_scene": False,
            "pack_title": "pack_fallback",
            "output_dir": str(media_dir),
            "banner": "plate",
            "scenes": [
                {"id": 1, "title": "Scene 1", "path": str(files[0]), "studio": "PackStudio", "performers": ["Alice"], "tags": ["TagA"], "height": 1080, "duration": 600},
                {"id": 2, "title": "Scene 2", "path": str(files[1]), "studio": "PackStudio", "performers": ["Bob"], "tags": ["TagB"], "height": 1080, "duration": 600},
            ],
            "notes": "Pack notes.",
        }

        with mock.patch.object(task, "render_meta_panel", None), \
             mock.patch.object(task, "upload_previews", return_value=[f"{HOST}/cs1.jpg", f"{HOST}/cs2.jpg"]):
            bbcode = task.run_build_megapack(payload)["bbcode"]

        assert "\n[b]Studio:[/b] PackStudio" in bbcode
        assert "\n[b]Performers:[/b] Alice & Bob" in bbcode
        assert "\n[b]Tags:[/b] TagA, TagB" in bbcode
        assert "\n[b]Scenes Included:[/b] 2" in bbcode
        assert "[quote]Pack notes.[/quote]" in bbcode
        assert "[b][color=#8a9ba8]Studio[/color][/b]" not in bbcode




