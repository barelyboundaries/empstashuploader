"""Tests for Empornium tag vocabulary loading, validation, and resolution."""

from pathlib import Path
import pytest

from empornium_megapack.tags import (
    MAX_TAGS,
    ResolvedTags,
    TagSource,
    TagVocabularyCorruptError,
    TagVocabularyError,
    TagVocabularyNotFoundError,
    load_vocabulary,
    resolve_tags,
)


def test_1_ignored_entries_produce_no_output_tag():
    vocab = load_vocabulary()
    # Explicitly test required samples from brief
    required_samples = ["4K Available", "Missing Performer (Female)", "Missing Date"]
    for sample in required_samples:
        assert sample.lower() in vocab.ignored, f"{sample} should be in shipped ignored list"
        res = resolve_tags([TagSource(sample, "scene_tag")])
        assert res.tags == [], f"{sample} must produce no output tag"
        assert sample in res.ignored

    # Assert against every entry in the shipped [ignored] list
    all_ignored_sources = [TagSource(tag, "scene_tag") for tag in vocab.ignored]
    res_all = resolve_tags(all_ignored_sources)
    assert res_all.tags == []
    assert len(res_all.ignored) == len(vocab.ignored)


def test_2_unmapped_scene_tag_lands_in_unmapped_never_in_tags():
    tag_name = "Custom Stash Housekeeping 12345"
    res = resolve_tags([TagSource(tag_name, "scene_tag")])
    assert res.tags == []
    assert tag_name in res.unmapped
    assert res.ignored == []


def test_3_brown_hair_expands_to_both_brunette_and_brown_hair():
    res = resolve_tags([TagSource("Brown Hair", "scene_tag")])
    assert "brunette" in res.tags
    assert "brown.hair" in res.tags
    assert res.unmapped == []
    assert res.ignored == []


def test_4_case_insensitive_matching():
    for variant in ["blowjob", "Blowjob", "BLOWJOB", "bLoWjOb"]:
        res = resolve_tags([TagSource(variant, "scene_tag")])
        assert res.tags == ["blowjob"]
        assert res.unmapped == []
        assert res.ignored == []


def test_5_performer_studio_and_derived_bypass_gate():
    sources = [
        TagSource("Pamela Anderson", "performer"),
        TagSource("Evil Angel", "studio"),
        TagSource("1080p", "derived"),
        TagSource("h264", "derived"),
        TagSource("2024.01", "derived"),
    ]
    res = resolve_tags(sources)
    assert "pamela.anderson" in res.tags
    assert "evil.angel" in res.tags
    assert "1080p" in res.tags
    assert "h264" in res.tags
    assert "2024.01" in res.tags
    assert res.unmapped == []
    assert res.ignored == []


def test_6_max_tags_cap():
    sources = [TagSource(f"Performer {i}", "performer") for i in range(120)]
    res = resolve_tags(sources)
    assert len(res.tags) == MAX_TAGS
    assert len(res.tags) == 60


def test_7_missing_or_corrupt_file_raises_named_error(tmp_path):
    # Missing file
    missing_path = tmp_path / "nonexistent_emp_tags.toml"
    with pytest.raises(TagVocabularyNotFoundError):
        load_vocabulary(missing_path)
    assert issubclass(TagVocabularyNotFoundError, TagVocabularyError)

    # Corrupt / invalid TOML
    corrupt_file = tmp_path / "corrupt.toml"
    corrupt_file.write_text("not a valid toml table = [unclosed", encoding="utf-8")
    with pytest.raises(TagVocabularyCorruptError):
        load_vocabulary(corrupt_file)
    assert issubclass(TagVocabularyCorruptError, TagVocabularyError)

    # Missing [ignored].tags
    missing_ignored = tmp_path / "no_ignored.toml"
    missing_ignored.write_text('[map]\n"A" = "b"\n', encoding="utf-8")
    with pytest.raises(TagVocabularyCorruptError):
        load_vocabulary(missing_ignored)

    # Missing [map]
    missing_map = tmp_path / "no_map.toml"
    missing_map.write_text('[ignored]\ntags = ["A"]\n', encoding="utf-8")
    with pytest.raises(TagVocabularyCorruptError):
        load_vocabulary(missing_map)
