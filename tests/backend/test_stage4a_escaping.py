"""
Tests for Stage 4a: BBCode Escaping Security Fix.
Verifies that untrusted metadata strings (pack_title, performers, tags, notes)
are safely escaped via bbcode_escape into &#91; and &#93; without breaking layout,
preserving newlines in notes, and leaving safe_title on-disk filenames unaffected.
"""

import os
import sys
from pathlib import Path
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

import task


def test_stage4a_bbcode_escaping_evil_tags(tmp_path):
    """Untrusted BBCode and HTML injection in pack_title, performers, tags, and notes are escaped in _bbcode.txt."""
    out_dir = tmp_path / "stage4a_evil_out"
    out_dir.mkdir()

    evil_title = "[b]evil[/b] [url=http://x]click[/url]"
    pack_dir = out_dir / task.sanitize_name(evil_title)
    pack_dir.mkdir()

    media_file = pack_dir / "scene1.mp4"
    media_file.write_bytes(b"\x00" * 1024)

    evil_performer = "Performer [VIP] <Star>"
    evil_tag = "Tag [4K] & [Exclusive]"
    evil_notes = "Line 1 with [b]bold[/b]\nLine 2 with [url]link[/url]\nLine 3 [quote]quote[/quote]"

    payload = {
        "pack_title": evil_title,
        "output_dir": str(out_dir),
        "performers": [evil_performer],
        "tags": [evil_tag],
        "notes": evil_notes,
        "scenes": [{"id": 1, "path": str(media_file)}],
    }

    result = task.run_build_megapack(payload)
    assert result["status"] == "success"

    bbcode_path = Path(result["bbcode_path"])
    assert bbcode_path.exists()
    bbcode_content = bbcode_path.read_text(encoding="utf-8")

    # 1. pack_title must be escaped
    assert "&#91;b&#93;evil&#91;/b&#93; &#91;url=http://x&#93;click&#91;/url&#93;" in bbcode_content
    # Raw unescaped evil tags must NOT be present
    assert "[b]evil[/b]" not in bbcode_content
    assert "[url=http://x]" not in bbcode_content

    # 2. performer must be escaped
    assert "Performer &#91;VIP&#93; <Star>" in bbcode_content
    assert "Performer [VIP]" not in bbcode_content

    # 3. tag must be escaped
    assert "Tag &#91;4K&#93; & &#91;Exclusive&#93;" in bbcode_content
    assert "Tag [4K]" not in bbcode_content

    # 4. notes must be escaped while preserving line breaks
    assert "Line 1 with &#91;b&#93;bold&#91;/b&#93;\nLine 2 with &#91;url&#93;link&#91;/url&#93;\nLine 3 &#91;quote&#93;quote&#91;/quote&#93;" in bbcode_content

    # 5. Structure markers must remain intact. The title now sits in the
    #    banner masthead: its brackets are escaped, the banner's own are not.
    assert "[color=#f5f8fa]&#91;b&#93;evil" in bbcode_content
    assert "[bg=#202b33]" in bbcode_content
    assert "[b][color=#8a9ba8]Performers[/color][/b]" in bbcode_content
    assert "[b][color=#8a9ba8]Tags[/color][/b]" in bbcode_content
    assert "[b]Scenes Included:[/b] 1" in bbcode_content
    assert "[hr]" in bbcode_content


def test_stage4a_clean_input_byte_identity(tmp_path):
    """Clean input produces standard BBCode format with exact section headings and ordering.

    Pinned with the banner off so a header redesign cannot mask a regression in
    the body; test_stage4a_banner_byte_identity pins the banner itself.
    """
    out_dir = tmp_path / "stage4a_clean_out"
    out_dir.mkdir()
    pack_title = "Clean Megapack 2026"
    pack_dir = out_dir / pack_title
    pack_dir.mkdir()

    media_file = pack_dir / "clean_scene.mp4"
    media_file.write_bytes(b"\x00" * 1024)

    payload = {
        "pack_title": pack_title,
        "output_dir": str(out_dir),
        "performers": ["Alice Stone", "Bob Clark"],
        "tags": ["Studio Alpha", "1080p"],
        "notes": "Official studio release notes.",
        "scenes": [{"id": 10, "path": str(media_file)}],
        "banner": "off",
    }

    result = task.run_build_megapack(payload)
    bbcode_path = Path(result["bbcode_path"])
    content = bbcode_path.read_text(encoding="utf-8")

    safe_url = task._sanitize_image_url(result['uploaded_urls'][0])
    safe_thumb = task._sanitize_image_url(result['uploaded_urls'][1])
    expected_lines = [
        "[color=red][b]PREVIEW ONLY: Contains local file:/// URLs[/b][/color]",
        "",
        "[center][b][size=5]Clean Megapack 2026[/size][/b][/center]",
        "",
        "[b]Performers:[/b] Alice Stone & Bob Clark",
        "",
        "[b]Tags:[/b] Studio Alpha, 1080p",
        "",
        "[b]Scenes Included:[/b] 1",
        "1. [b]Scene 1[/b]",
        "",
        "[hr]",
        "",
        "[quote]Official studio release notes.[/quote]",
        "",
        "[b]Contact Sheets[/b]",
        "",
        "[spoiler=Show contact sheet]",
        f"[url={safe_url}][img=200]{safe_thumb}[/img][/url]",
        "[/spoiler]",
    ]
    expected_content = "\n".join(expected_lines)
    assert content == expected_content


def test_stage4a_banner_byte_identity(tmp_path):
    """The default 'plate' banner is pinned exactly, on one physical line.

    The tracker runs descriptions through nl2br, so a newline anywhere inside
    the banner becomes a blank band between the masthead and the spec strip.
    Stats with no value (this pack has no duration, height or codec) are
    dropped and the surviving cells share the width.
    """
    out_dir = tmp_path / "stage4a_banner_out"
    out_dir.mkdir()
    pack_title = "Clean Megapack 2026"
    pack_dir = out_dir / pack_title
    pack_dir.mkdir()

    media_file = pack_dir / "clean_scene.mp4"
    media_file.write_bytes(b"\x00" * 1024)

    result = task.run_build_megapack({
        "pack_title": pack_title,
        "output_dir": str(out_dir),
        "performers": ["Alice Stone"],
        "notes": "Official studio release notes.",
        "scenes": [{"id": 10, "path": str(media_file)}],
    })

    # Located by content, not index: the preview warning and the page-wrapper
    # opener both sit above it and either can move.
    lines = result["bbcode"].split(chr(10))
    banner = next(ln for ln in lines if ln.startswith("[bg=#202b33]"))
    assert banner == (
        "[bg=#202b33][table=100%,nball,nopad][tr][td=16px][/td]"
        "[td=vab][size=1][color=#8a9ba8]STASH MEGAPACK[/color][/size]"
        "[br][size=6][font=Trebuchet MS][b][color=#f5f8fa]Clean Megapack 2026[/color][/b][/font][/size][/td]"
        "[td=vab][align=right][size=1]"
        "[url=https://stashapp.cc][color=#48aff0]stashapp.cc[/color][/url]"
        "[color=#5c7080] · [/color]"
        "[url=https://github.com/barelyboundaries/empstashuploader]"
        "[color=#48aff0]Empornium Stash Uploader[/color][/url]"
        "[/size][/align][/td][td=16px][/td][/tr][/table][/bg]"
        "[bg=#30404d][table=100%,nball][tr]"
        "[td=vam,50%][align=center][size=1][color=#8a9ba8]SCENES[/color][/size]"
        "[br][size=3][color=#f5f8fa][b]1[/b][/color][/size][/align][/td]"
        "[td=vam,50%][align=center][size=1][color=#8a9ba8]SIZE[/color][/size]"
        "[br][size=3][color=#f5f8fa][b]1 KB[/b][/color][/size][/align][/td]"
        "[/tr][/table][/bg]"
    )


def test_stage4a_banner_off_removes_it_and_restores_the_centred_title(tmp_path):
    """The 'off' style is a real opt-out, not a blank line."""
    out_dir = tmp_path / "stage4a_banner_off_out"
    out_dir.mkdir()
    pack_title = "Clean Megapack 2026"
    pack_dir = out_dir / pack_title
    pack_dir.mkdir()

    media_file = pack_dir / "clean_scene.mp4"
    media_file.write_bytes(b"\x00" * 1024)

    result = task.run_build_megapack({
        "pack_title": pack_title,
        "output_dir": str(out_dir),
        "scenes": [{"id": 10, "path": str(media_file)}],
        "banner": "off",
    })

    bbcode = result["bbcode"]
    assert "stashapp.cc" not in bbcode
    assert "[bg=#202b33]" not in bbcode
    assert "[center][b][size=5]Clean Megapack 2026[/size][/b][/center]" in bbcode


def test_stage4a_safe_title_disk_isolation(tmp_path):
    """safe_title used for filenames on disk is produced by sanitize_name, not bbcode_escape."""
    out_dir = tmp_path / "stage4a_disk_out"
    out_dir.mkdir()

    raw_title = "My [Special] *Pack* <2026>"
    pack_dir = out_dir / task.sanitize_name(raw_title)
    pack_dir.mkdir()

    media_file = pack_dir / "disk_scene.mp4"
    media_file.write_bytes(b"\x00" * 1024)

    payload = {
        "pack_title": raw_title,
        "output_dir": str(out_dir),
        "scenes": [{"id": 1, "path": str(media_file)}],
    }

    result = task.run_build_megapack(payload)
    manifest_file = Path(result["manifest_path"])
    bbcode_file = Path(result["bbcode_path"])
    torrent_file = Path(result["torrent_path"])

    # On disk, filename has sanitized characters (underscores), not HTML entities like &#91;
    assert "&#91;" not in manifest_file.name
    assert "&#93;" not in manifest_file.name
    assert "&#91;" not in bbcode_file.name
    assert "&#91;" not in torrent_file.name
    assert manifest_file.exists()
    assert bbcode_file.exists()
    assert torrent_file.exists()
