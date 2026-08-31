import json

import pytest
import torf

from deepseek_megapack.torrents import (
    TorrentError,
    _exact_exclude_globs,
    _fnmatch_escape,
    create_torrent,
    piece_size_for,
    sanitize_announce_url,
    source_for_announce,
    validate_announce_url,
)


ANNOUNCE = "https://tracker.empornium.me/announce.php?passkey=0123456789abcdef0123456789abcdef"
# Confirmed Empornium format: plain HTTP, path-carried tokens, no query param.
ANNOUNCE_PATH = "http://tracker.empornium.sx:2710/aaaaaaaa/bbbbbbbb/announce"


def test_piece_size_policy():
    assert piece_size_for(1 << 20) == 2**14  # 1 MiB payload -> clamped 16 KiB floor
    assert piece_size_for(1 << 30) == 2**20  # 1 GiB -> 1 MiB
    assert piece_size_for(5 << 30) == 2**22  # 5 GiB -> 4 MiB
    assert piece_size_for(1024 << 30) == 2**23  # 1 TiB -> capped 8 MiB
    assert piece_size_for(64) == 2**14  # tiny payload -> clamped to 16 KiB
    assert piece_size_for(0) == 2**14


def test_source_for_announce():
    assert source_for_announce(ANNOUNCE) == "Emp"  # helper-exact tag
    assert source_for_announce(ANNOUNCE_PATH) == "Emp"  # confirmed path-based host
    assert source_for_announce("https://tracker.enthralled.eu/announce") == "Ent"
    assert source_for_announce("https://tracker.femdomcult.net/announce") == "FDC"
    assert source_for_announce("https://tracker.pornbay.org/announce") == "PBay"
    assert source_for_announce("https://unknown.example/announce") == "unknown.example"
    assert source_for_announce("") == ""


def test_sanitize_announce_url_masks_passkey():
    sanitized = sanitize_announce_url(ANNOUNCE)
    assert "0123456789abcdef0123456789abcdef" not in sanitized
    assert "passkey=" + "x" * 32 in sanitized


def test_sanitize_announce_url_no_passkey():
    sanitized = sanitize_announce_url("https://tracker.empornium.me/announce.php")
    assert "passkey=" + "x" * 32 in sanitized


def test_sanitize_announce_url_masks_path_tokens():
    sanitized = sanitize_announce_url(ANNOUNCE_PATH)
    assert "aaaaaaaa" not in sanitized and "bbbbbbbb" not in sanitized
    assert sanitized == "http://tracker.empornium.sx:2710/xxxxxxxx/xxxxxxxx/announce?passkey=" + "x" * 32
    assert sanitized.count("announce") == 1


def test_sanitize_announce_url_masks_single_path_token():
    one = "http://tracker.empornium.sx:2710/abcdef0123456789abcdef0123456789/announce"
    sanitized = sanitize_announce_url(one)
    assert "abcdef0123456789abcdef0123456789" not in sanitized
    assert sanitized == "http://tracker.empornium.sx:2710/xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx/announce?passkey=" + "x" * 32


def test_validate_announce_url():
    validate_announce_url(ANNOUNCE)  # legacy https + ?passkey= still accepted
    validate_announce_url(ANNOUNCE_PATH)  # confirmed http path-based format
    validate_announce_url("http://tracker.empornium.sx:2710/aaaa/bbbb/announce")
    with pytest.raises(TorrentError, match="host"):
        validate_announce_url("https:///announce.php")
    with pytest.raises(TorrentError, match="announce"):
        validate_announce_url("http://tracker.empornium.sx:2710/aaaa/bbbb/upload.php")
    with pytest.raises(TorrentError, match="http or https"):
        validate_announce_url("ftp://tracker.empornium.sx/announce")


def _payload(tmp_path, file_size=64, subfile=True):
    root = tmp_path / "My Pack"
    root.mkdir()
    (root / "scene-a.mp4").write_bytes(b"\x00" * file_size)
    if subfile:
        sheets = root / "Contact Sheets"
        sheets.mkdir()
        (sheets / "scene-a.jpg").write_bytes(b"\xff\xd8" + b"\x00" * 16)
    return root


def test_create_torrent_roundtrip(tmp_path):
    payload = _payload(tmp_path, file_size=2**17)
    out = tmp_path / "pack.torrent"
    meta = create_torrent(payload, ANNOUNCE, out, piece_size=2**14, expected_bytes=2**17 + 18)
    assert len(meta["infohash"]) == 40
    assert meta["name"] == "My Pack"
    assert meta["piece_size"] == 2**14
    assert meta["total_bytes"] == 2**17 + 18
    assert meta["file_count"] == 2

    readback = torf.Torrent.read(out)
    assert readback.private is True
    assert readback.source == "Emp"
    assert readback.trackers == [[ANNOUNCE]]  # torf normalizes to list-of-lists
    assert readback.piece_size == 2**14
    assert all(any(f.name == name for f in readback.files) for name in ("scene-a.mp4", "scene-a.jpg"))


def test_create_torrent_size_mismatch_raises(tmp_path):
    payload = _payload(tmp_path, file_size=2**17)
    out = tmp_path / "pack.torrent"
    with pytest.raises(TorrentError, match="does not match"):
        create_torrent(payload, ANNOUNCE, out, piece_size=2**14, expected_bytes=12345)


def test_create_torrent_bad_announce_refused_before_generate(tmp_path):
    payload = _payload(tmp_path)
    out = tmp_path / "pack.torrent"
    with pytest.raises(TorrentError, match="http or https"):
        create_torrent(payload, "ftp://bad/announce", out)


def test_create_torrent_path_based_announce_roundtrip(tmp_path):
    payload = _payload(tmp_path, file_size=2**17)
    out = tmp_path / "pack.torrent"
    meta = create_torrent(payload, ANNOUNCE_PATH, out, piece_size=2**14)
    assert meta["name"] == "My Pack"
    assert meta["file_count"] == 2

    readback = torf.Torrent.read(out)
    assert readback.private is True
    assert readback.source == "Emp"
    assert readback.trackers == [[ANNOUNCE_PATH]]
    raw = out.read_bytes()
    assert ANNOUNCE_PATH.encode() in raw  # only inside the private torrent
    assert b"passkey" not in raw
    assert b"aaaaaaaa" in raw and b"bbbbbbbb" in raw  # tokens belong to the torrent itself


def test_create_torrent_piece_below_minimum_raises(tmp_path):
    payload = _payload(tmp_path)
    with pytest.raises(TorrentError, match="16 KiB"):
        create_torrent(payload, ANNOUNCE, tmp_path / "t.torrent", piece_size=1024)


def test_torrent_contains_no_manifest_metadata(tmp_path):
    payload = _payload(tmp_path, file_size=2**17)
    out = tmp_path / "pack.torrent"
    meta = create_torrent(payload, ANNOUNCE, out, piece_size=2**14)
    raw = out.read_bytes()
    assert b"announce.php?passkey=0123456789abcdef0123456789abcdef" in raw  # the torrent itself carries it (required)
    assert b"manifest" not in raw
    assert json.dumps(meta).count("passkey") == 0  # returned metadata never contains it


def test_torrent_piece_count_exact(tmp_path):
    payload = _payload(tmp_path, file_size=2**17)
    out = tmp_path / "pack.torrent"
    meta = create_torrent(payload, ANNOUNCE, out, piece_size=2**14)
    assert meta["piece_count"] == 9  # ceil(131090 / 16384) = 9 pieces
    assert meta["total_bytes"] == 2**17 + 18


# ============================================================================
# T4 (staged-wizard-inplace-seed): exact-set exclusion via exclude_exact
# ============================================================================

def test_fnmatch_escape_wraps_special_chars_in_character_classes():
    # Pinned fnmatch semantics (verified against Python 3.14 fnmatch + torf
    # 4.3.1's is_excluded): wrapping each of * ? [ ] in a one-character class
    # makes the glob match that literal character. '!' and spaces have no
    # fnmatch meaning outside classes and pass through untouched.
    assert _fnmatch_escape("a[1].txt") == "a[[]1[]].txt"
    assert _fnmatch_escape("b]c.mp4") == "b[]]c.mp4"
    assert _fnmatch_escape("x*y.mp4") == "x[*]y.mp4"
    assert _fnmatch_escape("q?.mp4") == "q[?].mp4"
    assert _fnmatch_escape("[!neg].mp4") == "[[]!neg[]].mp4"
    assert _fnmatch_escape("!important.txt") == "!important.txt"
    assert _fnmatch_escape("b c.txt") == "b c.txt"
    assert _fnmatch_escape("ünïcode [x].mp4") == "ünïcode [[]x[]].mp4"


def test_exact_exclude_globs_prefixes_with_payload_name_and_escapes():
    globs = _exact_exclude_globs("Seed [Dir]", ["a[1].txt", "sub/x.mp4"])
    # Normal form: <name>/<rel> — the payload dir basename, NOT a name= override
    assert "Seed [[]Dir[]]/a[[]1[]].txt" in globs
    assert "Seed [[]Dir[]]/sub/x.mp4" in globs
    # Doubled form for the all-files-in-one-subdir torf quirk
    assert "Seed [[]Dir[]]/Seed [[]Dir[]]/a[[]1[]].txt" in globs


def test_exclude_exact_removes_unrelated_files_with_glob_special_chars(tmp_path):
    """Pinned torf 4.3.1 behavior: exclude_exact removes files by EXACT relative
    path even when names contain glob-special characters.

    Empirically observed torf 4.3.1 behavior this test pins:
    - exclude globs are matched with fnmatch.fnmatch (case-insensitive,
      os.path.normcase on both sides) against "<payload_dir_basename>/<relpath>";
    - '[' opens a character class, so it must be escaped as '[[' + ']' (a class
      containing the literal char); ']' as '[]]'; '*' as '[*]'; '?' as '[?]';
    - '!' and spaces have no fnmatch meaning and need no escaping;
    - hidden dotfiles are dropped by torf itself (filter_files hidden=False).
    """
    payload = tmp_path / "Seed [Dir]"
    payload.mkdir()
    (payload / "keep.mp4").write_bytes(b"K" * 64)
    nested = payload / "sub"
    nested.mkdir()
    (nested / "x.mp4").write_bytes(b"X" * 64)
    (payload / "sub2").mkdir()
    (payload / "sub2" / "x.mp4").write_bytes(b"Y" * 64)  # must survive: different dir

    unrelated = [
        "a[1].txt",
        "b c.txt",
        "!important.txt",
        "ünïcode.txt",
        "sub/[weird] name.mp4",
    ]
    for rel in unrelated:
        target = payload / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"U" * 64)
    (payload / ".dotfile").write_bytes(b"D" * 64)  # hidden: torf drops it anyway

    out = tmp_path / "pack.torrent"
    meta = create_torrent(
        payload,
        ANNOUNCE,
        out,
        piece_size=2**14,
        exclude_exact=[
            "a[1].txt",
            "b c.txt",
            "!important.txt",
            "ünïcode.txt",
            "sub/x.mp4",
            "sub/[weird] name.mp4",
        ],
    )
    assert meta["name"] == "Seed [Dir]"
    assert meta["file_count"] == 2

    readback = torf.Torrent.read(out)
    actual = {"/".join(f.parts[1:]) for f in readback.files}
    assert actual == {"keep.mp4", "sub2/x.mp4"}


def test_exclude_exact_survives_all_files_in_one_subdir_quirk(tmp_path):
    """torf 4.3.1 quirk: when every payload file shares one deeper common
    subdirectory, torf's internal commonpath sinks below the payload dir and
    the glob-matched string gains an extra '<name>/' prefix. The doubled-form
    glob emitted by _exact_exclude_globs keeps exact exclusion working."""
    payload = tmp_path / "Pack"
    videos = payload / "Videos"
    videos.mkdir(parents=True)
    (videos / "a.mp4").write_bytes(b"A" * 64)
    (videos / "notes.txt").write_bytes(b"N" * 64)

    out = tmp_path / "t.torrent"
    meta = create_torrent(
        payload, ANNOUNCE, out, piece_size=2**14, exclude_exact=["Videos/notes.txt"]
    )
    assert meta["file_count"] == 1
    readback = torf.Torrent.read(out)
    assert {"/".join(f.parts[1:]) for f in readback.files} == {"Videos/a.mp4"}
    assert readback.name == "Pack"


def test_exclude_exact_is_case_insensitive_and_separator_agnostic(tmp_path):
    """torf matches globs case-insensitively with normcase on both sides, so a
    backslash lowercase exclusion input matches the on-disk path."""
    payload = tmp_path / "Pack"
    payload.mkdir()
    (payload / "keep.mp4").write_bytes(b"K" * 64)
    (payload / "Deep Dir").mkdir()
    (payload / "Deep Dir" / "Nested File.TXT").write_bytes(b"N" * 64)

    out = tmp_path / "t.torrent"
    create_torrent(
        payload,
        ANNOUNCE,
        out,
        piece_size=2**14,
        exclude_exact=["deep dir\\nested file.txt"],
    )
    readback = torf.Torrent.read(out)
    assert {"/".join(f.parts[1:]) for f in readback.files} == {"keep.mp4"}


def test_exclude_exact_does_not_exclude_similar_but_different_paths(tmp_path):
    """Exact match only: excluding 'sub/x.mp4' must not touch 'sub/x2.mp4'."""
    payload = tmp_path / "Pack"
    payload.mkdir()
    (payload / "sub").mkdir()
    (payload / "sub" / "x.mp4").write_bytes(b"X" * 64)
    (payload / "sub" / "x2.mp4").write_bytes(b"Y" * 64)

    out = tmp_path / "t.torrent"
    meta = create_torrent(
        payload, ANNOUNCE, out, piece_size=2**14, exclude_exact=["sub/x.mp4"]
    )
    assert meta["file_count"] == 1
    readback = torf.Torrent.read(out)
    assert {"/".join(f.parts[1:]) for f in readback.files} == {"sub/x2.mp4"}


def test_exclude_exact_name_override_still_uses_payload_dir_basename(tmp_path):
    """The glob prefix is the payload dir basename even when name= is set
    (torf matches globs against paths prefixed with the payload dir basename,
    while torrent.files carries the name= override)."""
    payload = tmp_path / "Real Dir"
    payload.mkdir()
    (payload / "keep.mp4").write_bytes(b"K" * 64)
    (payload / "drop[me].txt").write_bytes(b"D" * 64)

    out = tmp_path / "t.torrent"
    meta = create_torrent(
        payload,
        ANNOUNCE,
        out,
        piece_size=2**14,
        name="Custom Name",
        exclude_exact=["drop[me].txt"],
    )
    assert meta["name"] == "Custom Name"
    readback = torf.Torrent.read(out)
    assert {"/".join(f.parts[1:]) for f in readback.files} == {"keep.mp4"}