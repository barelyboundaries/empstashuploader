import pytest

from empornium_megapack.config import Settings
from empornium_megapack.paths import PathMapper, oshash_file, verify_same_file



def mapper(path_mappings):
    return PathMapper(Settings(path_mappings=path_mappings))


def test_passthrough_without_mapping():
    m = mapper([])
    assert m.apply(r"D:\Media\Show\a.mp4") == r"D:\Media\Show\a.mp4"
    assert m.is_mapped(r"D:\Media\Show\a.mp4") is False


def test_maps_remote_prefix_to_local():
    m = mapper([["/media", r"D:\Media"]])
    assert m.apply("/media/Show/a.mp4") == r"D:\Media/Show/a.mp4"
    assert m.is_mapped("/media/Show/a.mp4") is True
    assert m.apply("/media") == r"D:\Media"


def test_map_is_case_and_separator_insensitive():
    m = mapper([["/media", r"D:\Media"]])
    assert m.apply("/MEDIA/Show/a.mp4") == r"D:\Media/Show/a.mp4"
    assert m.apply(r"\media\Show\a.mp4") == r"D:\Media/Show/a.mp4"


def test_nonmatching_prefix_passthrough():
    m = mapper([["/media", r"D:\Media"]])
    assert m.apply("/videos/Show/a.mp4") == "/videos/Show/a.mp4"
    assert m.apply("D:/other/a.mp4") == "D:/other/a.mp4"


def test_remote_prefix_not_part_of_other_word():
    m = mapper([["/media", r"D:\Media"]])
    assert m.apply("/mediatree/a.mp4") == "/mediatree/a.mp4"


def test_multiple_mappings_first_wins_by_length_order():
    m = mapper([["/media/private", r"D:\Private"], ["/media", r"D:\Media"]])
    assert m.apply("/media/private/x.mp4") == r"D:\Private/x.mp4"
    assert m.apply("/media/public/x.mp4") == r"D:\Media/public/x.mp4"


def test_verify_same_file(tmp_path):
    f = tmp_path / "a.mp4"
    f.write_bytes(b"\x00" * 100)
    assert verify_same_file(str(f), 100, mapped=True) is True
    assert verify_same_file(str(f), 200, mapped=True) is False
    assert verify_same_file(str(f), 100, mapped=False) is True
    assert verify_same_file(str(f), 200, mapped=False) is True  # unmapped -> trusted
    assert verify_same_file(str(tmp_path / "missing.mp4"), 100, mapped=True) is False


def _le_sum(data: bytes) -> int:
    return sum(int.from_bytes(data[i : i + 8], "little") for i in range(0, len(data), 8))


def _expected_oshash(path, size):
    chunk = min(64 * 1024, (size // 8) * 8)
    with open(path, "rb") as fh:
        head = fh.read(chunk)
        fh.seek(-chunk, 2)
        tail = fh.read(chunk)
    return f"{(_le_sum(head) + _le_sum(tail) + size) & 0xFFFFFFFFFFFFFFFF:016x}"


def test_oshash_matches_stash_algorithm(tmp_path):
    payload = bytes(range(256)) * 400  # 102400 bytes (> 64 KiB)
    f = tmp_path / "big.bin"
    f.write_bytes(payload)
    assert oshash_file(f) == _expected_oshash(f, len(payload))
    assert len(oshash_file(f)) == 16


def test_oshash_small_file_chunk_truncated_to_multiple_of_8(tmp_path):
    f = tmp_path / "small.bin"
    f.write_bytes(b"\x01\x02\x03")  # 3 bytes -> too small, oshash is undefined
    with pytest.raises(ValueError, match="8 bytes"):
        oshash_file(f)

    f2 = tmp_path / "med.bin"
    payload = bytes(range(32)) * 4  # 128 bytes
    f2.write_bytes(payload)
    assert oshash_file(f2) == _expected_oshash(f2, len(payload))


def test_verify_same_file_with_oshash(tmp_path):
    f = tmp_path / "a.mp4"
    f.write_bytes(b"\x00" * 100)
    good = oshash_file(f)
    assert verify_same_file(str(f), 100, mapped=True, expected_oshash=good) is True
    assert verify_same_file(str(f), 100, mapped=True, expected_oshash="0" * 16) is False
    assert verify_same_file(str(f), 101, mapped=True, expected_oshash=good) is False
    # mapped but oshash cannot be computed (file too small) -> fail closed
    tiny = tmp_path / "tiny.bin"
    tiny.write_bytes(b"\x00")
    assert verify_same_file(str(tiny), 1, mapped=True, expected_oshash="1" * 16) is False
