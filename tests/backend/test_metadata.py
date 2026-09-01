import tempfile
import os
from types import SimpleNamespace

from empornium_megapack.metadata import (
    MAX_TAGS,
    bbcode_escape,
    empify,
    format_duration,
    join_names,
    merge_tags,
    normalize_meta_input,
    pack_title_default,
    render_description,
    resolution_for,
    scene_title_default,
)
from empornium_megapack.models import ApplyRequest, MetaRequest, SceneReview
from empornium_megapack.review import PackService

from test_review import FakeStash, make_file, make_scene, make_scene_review, service, touch


def fake_primary(basename="a.mp4", height=1080, codec="h264", duration=600):
    return SimpleNamespace(basename=basename, height=height, video_codec=codec, duration=duration)


def test_resolution_ladder():
    assert resolution_for(100) == ""
    assert resolution_for(144) == "144p"
    assert resolution_for(239) == "144p"
    assert resolution_for(240) == "240p"
    assert resolution_for(480) == "480p"
    assert resolution_for(539) == "480p"
    assert resolution_for(1080) == "1080p"
    assert resolution_for(1919) == "1440p"
    assert resolution_for(1920) == "2160p"
    assert resolution_for(2559) == "2160p"
    assert resolution_for(3840) == "8K"
    assert resolution_for(6142) == "8K"
    assert resolution_for(6143) == "8K+"
    assert resolution_for(8000) == "8K+"
    assert resolution_for(None) == ""


def test_format_duration():
    assert format_duration(None) == ""
    assert format_duration(0) == ""
    assert format_duration(59.9) == "0:59"
    assert format_duration(60) == "1:00"
    assert format_duration(953.22) == "15:53"
    assert format_duration(3661) == "1:01:01"


def test_join_names():
    assert join_names([]) == ""
    assert join_names(["A"]) == "A"
    assert join_names(["A", "B"]) == "A & B"
    assert join_names(["A", "B", "C"]) == "A, B & C"
    assert join_names(["A", "B", "C", "D"]) == "A, B, C & D"


def test_empify():
    assert empify("Big Booty!") == "big.booty"
    assert empify("  Hairy   Pussy  ") == "hairy.pussy"
    assert empify("blonde-hair") == "blonde.hair"
    assert empify("x" * 50) == "x" * 32
    assert empify("Cock & Balls") == "cock.balls"


def test_bbcode_escape():
    assert bbcode_escape("a [b]bold[/b] c") == "a &#91;b&#93;bold&#91;/b&#93; c"
    assert bbcode_escape("  lots   of  space ") == "lots of space"
    assert bbcode_escape("line1\nline2") == "line1 line2"
    assert bbcode_escape("line1\nline2", keep_newlines=True) == "line1\nline2"
    assert bbcode_escape("ctrl\x00\x01chars") == "ctrlchars"


def test_scene_title_default(tmp_path):
    f = touch(str(tmp_path / "file_01.mp4"), 1_700_000_000)
    scene = make_scene_review(make_scene("1", [make_file("f1", f)]))
    assert scene_title_default(scene, None) == "1"
    assert scene_title_default(scene, fake_primary("file_01.mp4")) == "file_01.mp4"
    scene.title = "My Title"
    assert scene_title_default(scene, fake_primary("file_01.mp4")) == "My Title"


def test_pack_title_default(tmp_path):
    f1 = touch(str(tmp_path / "a.mp4"), 1_700_000_000)
    f2 = touch(str(tmp_path / "b.mp4"), 1_700_000_000)
    scenes = [
        make_scene_review(make_scene("1", [make_file("f1", f1)], title="One", date="2023-01-01", studio="S", performers=["A", "B"])),
        make_scene_review(make_scene("2", [make_file("f2", f2)], title="Two", date="2023-01-15", studio="S", performers=["A", "C", "D"])),
    ]
    title = pack_title_default(scenes)
    assert "[S]" in title
    assert "Megapack (2 scenes)" in title
    assert "(2023-01-01 to 2023-01-15)" in title
    assert " & " in title or "," in title
    partial = scenes[:1] + [make_scene_review(make_scene("3", [make_file("f3", f2)], studio="S"))]
    assert " to " not in pack_title_default(partial)


def test_pack_title_performer_cap(tmp_path):
    f = touch(str(tmp_path / "a.mp4"), 1_700_000_000)
    scenes = [make_scene_review(make_scene("1", [make_file("f1", f)], performers=[f"P{i}" for i in range(6)]))]
    title = pack_title_default(scenes)
    assert title.count("P") == 4
    assert "+2 more" in title


def test_merge_tags_deterministic_and_sources(tmp_path):
    f = touch(str(tmp_path / "a.mp4"), 1_700_000_000)
    scenes = [
        make_scene_review(
            make_scene(
                "1",
                [make_file("f1", f, video_codec="h264")],
                title="One",
                date="2023-01-01",
                performers=["Alice"],
                tags=["Blonde", "Blonde", "Big Booty!"],
            )
        ),
    ]
    tags = merge_tags(scenes, {"1": fake_primary(codec="h264")})
    assert tags == sorted(tags)
    assert len(tags) == len(set(tags))
    joined = " ".join(tags)
    assert "alice" in joined
    assert "blonde" in joined
    assert "big.booty" in joined
    assert "h264" in joined
    assert "2023" in joined
    assert "2023.01" in joined
    assert "2023.01.01" in joined
    assert "1080p" in joined


def test_merge_tags_caps():
    fake_scene = SceneReview(scene_id="1", tags=[f"Tag {i}" for i in range(200)])
    assert len(merge_tags([fake_scene])) == MAX_TAGS


def test_render_description_image_only_scenes(tmp_path):
    desc = render_description(
        "My [Title]",
        ["a.b", "c.d"],
        "note line one\nnote line two",
        [
            {"image_url": "https://img/x.jpg"},
            {"image_url": "https://img/y.jpg"},
            {"image_url": None},
        ],
    )
    assert "[size=4][b]My &#91;Title&#93;[/b][/size]" in desc
    assert desc.count("[url=") == 2
    assert "[url=https://img/x.jpg][img=200]https://img/x.jpg[/img][/url]" in desc
    assert "[url=https://img/y.jpg][img=200]https://img/y.jpg[/img][/url]" in desc
    assert "1." not in desc
    assert "[b]Video:[/b]" not in desc
    assert "Autumn Falls" not in desc
    assert "[b]Tags:[/b] a.b c.d" in desc
    assert "note line one[br]note line two" in desc
    assert "[url=javascript:alert(1)]" not in desc


def test_render_description_no_image_no_broken_output():
    desc = render_description("T", [], "", [{"image_url": ""}])
    assert "[url=" not in desc
    assert "T" in desc


def test_normalize_meta_input():
    out = normalize_meta_input("  My Title  ", ["Bad! Tag", "bad tag", "Nice.Tag"], "  ", "Default", ["d1"])
    assert out["title"] == "My Title"
    assert out["tags"] == ["bad.tag", "nice.tag"]
    empty = normalize_meta_input("", ["x"], "", "Default Title", ["d1"])
    assert empty["title"] == "Default Title"
    noisy = normalize_meta_input("[Broken]", ["y"], "", "Default", ["d1"])
    assert noisy["title"] == "&#91;Broken&#93;"


def test_normalize_meta_input_none_tags_use_defaults():
    out = normalize_meta_input("T", None, "", "Default", ["d1", "d2"])
    assert out["tags"] == ["d1", "d2"]


def test_normalize_meta_input_empty_tags_clear():
    out = normalize_meta_input("T", [], "", "Default", ["d1", "d2"])
    assert out["tags"] == []


def test_meta_defaults(tmp_path):
    f = touch(str(tmp_path / "scene_a.mp4"), 1_700_000_000)
    svc = service(
        tmp_path,
        {"1": make_scene("1", [make_file("f1", f)], title="Alpha", date="2023-01-01", studio="S", performers=["A", "B"], tags=["Blonde", "POV"])},
    )
    resp = svc.meta(MetaRequest(scene_ids=["1"]))
    assert resp.errors == []
    assert resp.meta.title.startswith("[S]")
    assert resp.meta.scenes[0].title == "Alpha"
    assert resp.meta.scenes[0].resolution == "1080p"
    assert resp.meta.scenes[0].fetch_mode == "copy"
    assert "scene-image-1" in resp.meta.description
    assert resp.meta.tags == sorted(resp.meta.tags)


def test_scene_tags_flow_through_real_build_path(tmp_path):
    f = touch(str(tmp_path / "scene_a.mp4"), 1_700_000_000)
    svc = service(
        tmp_path,
        {"1": make_scene("1", [make_file("f1", f, video_codec="h264")], title="Alpha", date="2023-01-01", performers=["Alice"], tags=["Amateur", "POV", "Amateur"])},
    )
    resp = svc.meta(MetaRequest(scene_ids=["1"]))
    joined = " ".join(resp.meta.tags)
    assert "amateur" in joined
    assert "pov" in joined
    assert "alice" in joined
    assert "h264" in joined
    assert "1080p" in joined
    assert "2023.01.01" in joined
    assert resp.meta.tags == sorted(resp.meta.tags)


def test_empty_user_tags_clear_generated_tags(tmp_path):
    f = touch(str(tmp_path / "a.mp4"), 1_700_000_000)
    svc = service(
        tmp_path,
        {"1": make_scene("1", [make_file("f1", f)], performers=["Alice"], tags=["Amateur"])},
    )
    resp = svc.apply(ApplyRequest(scene_ids=["1"], meta={"title": "T", "tags": [], "notes": "", "scenes": []}))
    assert resp.errors == []
    assert resp.meta.tags == []
    assert "[b]Tags:[/b]" not in resp.meta.description


def test_omitted_tags_use_generated_defaults(tmp_path):
    f = touch(str(tmp_path / "a.mp4"), 1_700_000_000)
    svc = service(
        tmp_path,
        {"1": make_scene("1", [make_file("f1", f)], performers=["Alice"], tags=["Amateur"])},
    )
    resp = svc.apply(ApplyRequest(scene_ids=["1"], meta={"title": "T", "notes": "", "scenes": []}))
    assert resp.errors == []
    assert "amateur" in resp.meta.tags


def test_meta_user_override_and_apply_echo(tmp_path):
    f = touch(str(tmp_path / "scene_a.mp4"), 1_700_000_000)
    svc = service(
        tmp_path,
        {"1": make_scene("1", [make_file("f1", f)], title="Alpha", performers=["A"])},
    )
    meta_req = MetaRequest(scene_ids=["1"])
    meta_resp = svc.meta(meta_req)
    user = {
        "title": "My [Pack]",
        "tags": ["MyTag", "mytag", "New Tag!"],
        "notes": "hi\nthere",
        "scenes": [{"scene_id": "1", "title": "Renamed [Scene]", "fetch_mode": "download"}],
    }
    apply_resp = svc.apply(ApplyRequest(scene_ids=["1"], meta=user))
    assert apply_resp.errors == []
    assert apply_resp.meta.title == "My &#91;Pack&#93;"
    assert apply_resp.meta.tags == ["mytag", "new.tag"]
    assert apply_resp.meta.notes == "hi\nthere"
    assert apply_resp.meta.scenes[0].title == "Renamed &#91;Scene&#93;"
    assert apply_resp.meta.scenes[0].fetch_mode == "download"
    assert "My &#91;Pack&#93;" in apply_resp.meta.description
    assert any(w.code == "http_download" for w in apply_resp.warnings)


def test_download_mode_allows_missing_file(tmp_path):
    svc = service(tmp_path, {"1": make_scene("1", [make_file("f1", str(tmp_path / "gone.mp4"))])})
    resp = svc.apply(ApplyRequest(scene_ids=["1"], meta={"title": "", "tags": [], "notes": "", "scenes": [{"scene_id": "1", "fetch_mode": "download"}]}))
    assert resp.errors == []
    assert resp.meta.scenes[0].fetch_mode == "download"


def test_copy_mode_rejects_missing_file(tmp_path):
    svc = service(tmp_path, {"1": make_scene("1", [make_file("f1", str(tmp_path / "gone.mp4"))])})
    resp = svc.apply(ApplyRequest(scene_ids=["1"], meta={"title": "", "tags": [], "notes": "", "scenes": [{"scene_id": "1", "fetch_mode": "copy"}]}))
    assert [e.code for e in resp.errors] == ["copy_needs_local_file"]


def test_bad_fetch_mode_rejected(tmp_path):
    f = touch(str(tmp_path / "a.mp4"), 1_700_000_000)
    svc = service(tmp_path, {"1": make_scene("1", [make_file("f1", f)])})
    resp = svc.apply(ApplyRequest(scene_ids=["1"], meta={"title": "", "tags": [], "notes": "", "scenes": [{"scene_id": "1", "fetch_mode": "teleport"}]}))
    assert [e.code for e in resp.errors] == ["bad_fetch_mode"]


def test_apply_without_meta_still_works(tmp_path):
    f = touch(str(tmp_path / "a.mp4"), 1_700_000_000)
    svc = service(tmp_path, {"1": make_scene("1", [make_file("f1", f)])})
    resp = svc.apply(ApplyRequest(scene_ids=["1"]))
    assert resp.errors == []
    assert resp.meta is not None
    assert resp.meta.scenes[0].title == os.path.basename(f)
