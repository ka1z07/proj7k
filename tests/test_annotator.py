"""
Tests for 7K beatmap metadata annotator and biaxial collections (Ticket 5 / SPEC-P2.3-02).
"""

import pytest

from proj7k.lazer.bridge import (
    LazerBeatmapRecord,
    BeatmapUpdatePayload,
    BeatmapRevertPayload,
)
from proj7k.lazer.annotator import (
    INJECTED_SUFFIX_PATTERN,
    strip_injected_suffix,
    format_injected_difficulty_name,
    generate_binned_skill_tags,
    inject_binned_skill_tags,
    strip_binned_skill_tags,
    TECHNIQUE_COLLECTIONS,
    TIER_COLLECTIONS,
    ALL_BIAXIAL_COLLECTIONS,
    get_technique_collection_name,
    get_tier_collection_name,
    get_beatmap_biaxial_collections,
    BeatmapCollectionEntry,
    build_biaxial_collection_map,
    annotate_lazer_beatmap,
    create_lazer_revert_payload,
    annotate_batch_and_build_collections,
)


def test_strip_injected_suffix_clean_names():
    assert strip_injected_suffix("Insane") == "Insane"
    assert strip_injected_suffix("Another (EX)") == "Another (EX)"
    assert strip_injected_suffix("Special [7K]") == "Special [7K]"
    assert strip_injected_suffix("Difficulty with 7★ star") == "Difficulty with 7★ star"
    assert strip_injected_suffix("") == ""


def test_strip_injected_suffix_injected_names():
    # Historical 1-word suffix format
    assert strip_injected_suffix("Hard (6.42★ Jack)") == "Hard"
    assert strip_injected_suffix("Special (EX) (7.10★ Tech)") == "Special (EX)"
    assert strip_injected_suffix("Insane (8.00★ LN_General)") == "Insane"
    assert strip_injected_suffix("Master (10.50★ LN_Release)") == "Master"
    assert strip_injected_suffix("(5.00★ Speed)") == ""
    assert strip_injected_suffix("   Hard (6.42★ Jack)") == "   Hard"

    # New 2-word suffix format with Dan tier
    assert strip_injected_suffix("Hard (6.42★ 7th Jack)") == "Hard"
    assert strip_injected_suffix("Freedom Dive (10.52★ Stellium Stream)") == "Freedom Dive"
    assert strip_injected_suffix("Galaxy (9.50★ Azimuth Tech)") == "Galaxy"
    assert strip_injected_suffix("Beginner (1.80★ 0th Rice)") == "Beginner"
    assert strip_injected_suffix("(5.00★ 4th Speed)") == ""


def test_format_injected_difficulty_name():
    # Automatically derives Dan tier from star rating
    assert format_injected_difficulty_name("Hard", 6.42, "jack") == "Hard (6.42★ 7th Jack)"
    assert format_injected_difficulty_name("Hard", 6.42, "Jack") == "Hard (6.42★ 7th Jack)"
    assert format_injected_difficulty_name("Insane", 8.0, "ln_general") == "Insane (8.00★ 10th LN_General)"
    assert format_injected_difficulty_name("Master", 9.156, "LN_Release") == "Master (9.16★ Azimuth LN_Release)"
    assert format_injected_difficulty_name("God", 10.8, "speed") == "God (10.80★ Stellium Speed)"
    assert format_injected_difficulty_name("Beginner", 2.0, "tech") == "Beginner (2.00★ 0th Tech)"
    assert format_injected_difficulty_name("", 5.0, "speed") == "(5.00★ 4th Speed)"

    # Explicit dan_tier override
    assert format_injected_difficulty_name("Hard", 6.42, "jack", dan_tier="6th") == "Hard (6.42★ 6th Jack)"


def test_difficulty_name_idempotence():
    # 1. Repeated formatting with same values yields exact same result
    formatted_1 = format_injected_difficulty_name("Insane", 6.42, "jack")
    formatted_2 = format_injected_difficulty_name(formatted_1, 6.42, "jack")
    formatted_3 = format_injected_difficulty_name(formatted_2, 6.42, "jack")
    assert formatted_1 == "Insane (6.42★ 7th Jack)"
    assert formatted_2 == formatted_1
    assert formatted_3 == formatted_1

    # 2. Re-formatting with updated values replaces suffix without accumulating
    updated = format_injected_difficulty_name(formatted_1, 7.10, "tech")
    assert updated == "Insane (7.10★ 9th Tech)"

    # 3. Migration from historical suffix: replaces old suffix cleanly with new Dan suffix
    migrated = format_injected_difficulty_name("Insane (6.42★ Jack)", 6.42, "jack")
    assert migrated == "Insane (6.42★ 7th Jack)"

    # 4. Repeated stripping yields base name
    base = strip_injected_suffix(updated)
    assert base == "Insane"
    assert strip_injected_suffix(base) == "Insane"


def test_generate_binned_skill_tags():
    assert generate_binned_skill_tags("jack", 6.42) == ["dominant_jack", "jack_6★", "dan_7th"]
    assert generate_binned_skill_tags("LN_General", 7.8) == ["dominant_ln_general", "ln_general_7★", "dan_10th"]
    assert generate_binned_skill_tags("Tech", 5.0) == ["dominant_tech", "tech_5★", "dan_4th"]
    assert generate_binned_skill_tags("speed", 0.8) == ["dominant_speed", "speed_0★", "dan_0th"]
    assert generate_binned_skill_tags("speed", 10.5) == ["dominant_speed", "speed_10★", "dan_zenith"]
    assert generate_binned_skill_tags("stream", 11.2) == ["dominant_stream", "stream_11★", "dan_stellium"]


def test_inject_binned_skill_tags():
    # Empty tags
    assert inject_binned_skill_tags("", "jack", 6.42) == "dominant_jack jack_6★ dan_7th"
    assert inject_binned_skill_tags("   ", "jack", 6.42) == "dominant_jack jack_6★ dan_7th"

    # Existing tags without prior binned tags
    assert (
        inject_binned_skill_tags("rock anime", "jack", 6.42)
        == "rock anime dominant_jack jack_6★ dan_7th"
    )

    # Existing tags already having the exact same binned tags (deduplication)
    assert (
        inject_binned_skill_tags("rock anime dominant_jack jack_6★ dan_7th", "jack", 6.42)
        == "rock anime dominant_jack jack_6★ dan_7th"
    )

    # Update: replacing previous binned tags with new technique and rating
    assert (
        inject_binned_skill_tags("rock anime dominant_tech tech_5★ dan_4th", "jack", 6.42)
        == "rock anime dominant_jack jack_6★ dan_7th"
    )


def test_strip_binned_skill_tags():
    assert (
        strip_binned_skill_tags("rock anime dominant_jack jack_6★ dan_7th")
        == "rock anime"
    )
    assert strip_binned_skill_tags("dominant_jack jack_6★ dan_7th") == ""
    assert strip_binned_skill_tags("electronic instrumental") == "electronic instrumental"
    assert strip_binned_skill_tags("") == ""


def test_tags_injection_idempotence():
    raw_tags = "electronic instrumental"
    injected_1 = inject_binned_skill_tags(raw_tags, "stream", 7.2)
    injected_2 = inject_binned_skill_tags(injected_1, "stream", 7.2)
    injected_3 = inject_binned_skill_tags(injected_2, "stream", 7.2)

    assert injected_1 == "electronic instrumental dominant_stream stream_7★ dan_9th"
    assert injected_2 == injected_1
    assert injected_3 == injected_1

    stripped = strip_binned_skill_tags(injected_3)
    assert stripped == raw_tags


def test_technique_collections_constants_and_mapping():
    assert len(TECHNIQUE_COLLECTIONS) == 8
    expected_techs = [
        "7K Jack",
        "7K Tech",
        "7K Speed",
        "7K Stream",
        "7K LN General",
        "7K LN Tech",
        "7K LN Inverse",
        "7K LN Release",
    ]
    assert list(TECHNIQUE_COLLECTIONS) == expected_techs

    assert get_technique_collection_name("jack") == "7K Jack"
    assert get_technique_collection_name("Tech") == "7K Tech"
    assert get_technique_collection_name("speed") == "7K Speed"
    assert get_technique_collection_name("stream") == "7K Stream"
    assert get_technique_collection_name("ln_general") == "7K LN General"
    assert get_technique_collection_name("LN_General") == "7K LN General"
    assert get_technique_collection_name("LN Tech") == "7K LN Tech"
    assert get_technique_collection_name("ln_inverse") == "7K LN Inverse"
    assert get_technique_collection_name("ln_release") == "7K LN Release"


def test_tier_collections_constants_and_boundaries():
    assert len(TIER_COLLECTIONS) == 4
    expected_tiers = [
        "7K Tier: 00th-03rd (1★-4★)",
        "7K Tier: 04th-06th (4★-6★)",
        "7K Tier: 07th-09th (6★-8★)",
        "7K Tier: 10th+ (8★+)",
    ]
    assert list(TIER_COLLECTIONS) == expected_tiers

    # Tier 1: SR < 4.0
    assert get_tier_collection_name(0.5) == "7K Tier: 00th-03rd (1★-4★)"
    assert get_tier_collection_name(3.99) == "7K Tier: 00th-03rd (1★-4★)"

    # Tier 2: 4.0 <= SR < 6.0
    assert get_tier_collection_name(4.0) == "7K Tier: 04th-06th (4★-6★)"
    assert get_tier_collection_name(5.999) == "7K Tier: 04th-06th (4★-6★)"

    # Tier 3: 6.0 <= SR < 8.0
    assert get_tier_collection_name(6.0) == "7K Tier: 07th-09th (6★-8★)"
    assert get_tier_collection_name(7.99) == "7K Tier: 07th-09th (6★-8★)"

    # Tier 4: SR >= 8.0
    assert get_tier_collection_name(8.0) == "7K Tier: 10th+ (8★+)"
    assert get_tier_collection_name(10.5) == "7K Tier: 10th+ (8★+)"


def test_get_beatmap_biaxial_collections_returns_exactly_two():
    # Acceptance criterion: exactly 2 target collections (1 technique + 1 tier)
    test_cases = [
        ("jack", 3.5, ("7K Jack", "7K Tier: 00th-03rd (1★-4★)")),
        ("tech", 4.5, ("7K Tech", "7K Tier: 04th-06th (4★-6★)")),
        ("stream", 6.8, ("7K Stream", "7K Tier: 07th-09th (6★-8★)")),
        ("ln_inverse", 9.2, ("7K LN Inverse", "7K Tier: 10th+ (8★+)")),
    ]
    for dominant_tech, star_rating, expected in test_cases:
        cols = get_beatmap_biaxial_collections(dominant_tech, star_rating)
        assert isinstance(cols, tuple)
        assert len(cols) == 2
        assert cols[0] in TECHNIQUE_COLLECTIONS
        assert cols[1] in TIER_COLLECTIONS
        assert cols == expected


def test_build_biaxial_collection_map():
    entries = [
        BeatmapCollectionEntry("md5-1", "jack", 3.2),
        BeatmapCollectionEntry("md5-2", "jack", 5.5),
        BeatmapCollectionEntry("md5-3", "stream", 7.0),
        BeatmapCollectionEntry("md5-4", "ln_inverse", 9.0),
        BeatmapCollectionEntry("md5-1", "jack", 3.2),  # Duplicate
    ]

    col_map = build_biaxial_collection_map(entries)
    assert len(col_map) == 12  # All 12 collections present

    # Check technique collections
    assert col_map["7K Jack"] == ["md5-1", "md5-2"]
    assert col_map["7K Stream"] == ["md5-3"]
    assert col_map["7K LN Inverse"] == ["md5-4"]
    assert col_map["7K Tech"] == []

    # Check tier collections
    assert col_map["7K Tier: 00th-03rd (1★-4★)"] == ["md5-1"]
    assert col_map["7K Tier: 04th-06th (4★-6★)"] == ["md5-2"]
    assert col_map["7K Tier: 07th-09th (6★-8★)"] == ["md5-3"]
    assert col_map["7K Tier: 10th+ (8★+)"] == ["md5-4"]


def _make_dummy_record(
    id_str: str = "rec-1",
    ruleset_id: int = 3,
    circle_size: float = 7.0,
    difficulty_name: str = "Hard",
    tags: str = "jubeat electronic",
    star_rating: float = 3.5,
    md5_hash: str = "md5-dummy",
) -> LazerBeatmapRecord:
    return LazerBeatmapRecord(
        id=id_str,
        hash="hash-" + id_str,
        md5_hash=md5_hash,
        file_hash="file-" + id_str,
        star_rating=star_rating,
        difficulty_name=difficulty_name,
        tags=tags,
        title="Dummy Song",
        artist="Dummy Artist",
        ruleset_id=ruleset_id,
        circle_size=circle_size,
    )


def test_annotate_lazer_beatmap_7k():
    record = _make_dummy_record(
        difficulty_name="Hyper",
        tags="jubeat",
        star_rating=3.5,
    )
    payload = annotate_lazer_beatmap(record, star_rating=6.78, dominant_tech="stream")
    assert isinstance(payload, BeatmapUpdatePayload)
    assert payload.id == record.id
    assert payload.star_rating == 6.78
    assert payload.difficulty_name == "Hyper (6.78★ 8th Stream)"
    assert payload.tags == "jubeat dominant_stream stream_6★ dan_8th"


def test_annotate_lazer_beatmap_non_7k_guard():
    # 4K mania
    record_4k = _make_dummy_record(ruleset_id=3, circle_size=4.0)
    with pytest.raises(ValueError, match="not a 7K osu!mania beatmap"):
        annotate_lazer_beatmap(record_4k, star_rating=5.0, dominant_tech="jack")

    # Standard osu! (ruleset 0)
    record_std = _make_dummy_record(ruleset_id=0, circle_size=7.0)
    with pytest.raises(ValueError, match="not a 7K osu!mania beatmap"):
        annotate_lazer_beatmap(record_std, star_rating=5.0, dominant_tech="jack")


def test_create_lazer_revert_payload():
    record = _make_dummy_record(
        difficulty_name="Hyper (6.78★ 8th Stream)",
        tags="jubeat dominant_stream stream_6★ dan_8th",
        star_rating=6.78,
    )
    # Revert with explicit original star rating
    revert_payload = create_lazer_revert_payload(record, original_star_rating=3.5)
    assert isinstance(revert_payload, BeatmapRevertPayload)
    assert revert_payload.id == record.id
    assert revert_payload.difficulty_name == "Hyper"
    assert revert_payload.tags == "jubeat"
    assert revert_payload.star_rating == 3.5

    # Revert without explicit star rating defaults to record's star rating
    revert_payload_default = create_lazer_revert_payload(record)
    assert revert_payload_default.star_rating == 6.78
    assert revert_payload_default.difficulty_name == "Hyper"
    assert revert_payload_default.tags == "jubeat"


def test_annotate_batch_and_build_collections():
    r1 = _make_dummy_record("id-1", difficulty_name="Easy", md5_hash="md5-1")
    r2 = _make_dummy_record("id-2", difficulty_name="Hard", md5_hash="md5-2")

    items = [
        (r1, 3.2, "jack"),
        (r2, 7.5, "ln_inverse"),
    ]

    updates, collections = annotate_batch_and_build_collections(items)
    assert len(updates) == 2
    assert updates[0].difficulty_name == "Easy (3.20★ 0th Jack)"
    assert updates[1].difficulty_name == "Hard (7.50★ 10th LN_Inverse)"

    assert len(collections) == 12
    assert collections["7K Jack"] == ["md5-1"]
    assert collections["7K LN Inverse"] == ["md5-2"]
    assert collections["7K Tier: 00th-03rd (1★-4★)"] == ["md5-1"]
    assert collections["7K Tier: 07th-09th (6★-8★)"] == ["md5-2"]


def test_custom_technique_title_and_collection():
    from proj7k.lazer.annotator import format_dominant_title
    assert format_dominant_title("Custom Tech") == "Custom_Tech"
    assert get_technique_collection_name("Unorthodox") == "7K Unorthodox"


def test_build_biaxial_collection_map_with_tuples_and_no_empty():
    tuples_entries = [
        ("md5-t1", "jack", 3.0),
        ("md5-t2", "tech", 5.0),
    ]
    col_map = build_biaxial_collection_map(tuples_entries, include_empty=False)
    assert "7K Jack" in col_map
    assert "7K Tech" in col_map
    assert "7K Speed" not in col_map  # empty collection not included
    assert col_map["7K Jack"] == ["md5-t1"]


def test_annotate_batch_skips_non_7k():
    r_7k = _make_dummy_record("id-7k", ruleset_id=3, circle_size=7.0, md5_hash="md5-7k")
    r_4k = _make_dummy_record("id-4k", ruleset_id=3, circle_size=4.0, md5_hash="md5-4k")

    items = [
        (r_7k, 6.0, "stream"),
        (r_4k, 5.0, "jack"),
    ]
    updates, collections = annotate_batch_and_build_collections(items)
    assert len(updates) == 1
    assert updates[0].id == "id-7k"
    assert collections["7K Stream"] == ["md5-7k"]
    assert "md5-4k" not in collections["7K Jack"]


def test_space_in_dominant_tech_sanitized_for_tags():
    # Verify that 'LN General' doesn't fragment into space-separated invalid tags
    tags = generate_binned_skill_tags("LN General", 7.2)
    assert tags == ["dominant_ln_general", "ln_general_7★", "dan_9th"]
    injected = inject_binned_skill_tags("anime", "LN General", 7.2)
    assert injected == "anime dominant_ln_general ln_general_7★ dan_9th"
    # Ensure tokens are clean single words
    tokens = injected.split()
    assert "dominant_ln_general" in tokens
    assert "ln_general_7★" in tokens
    assert "dan_9th" in tokens
    assert "LN" not in tokens
    assert "General" not in tokens


def test_none_dominant_technique_fallback():
    # Blank or 'None' (e.g. empty beatmaps from radar.py) falls back to 7K Tech
    assert get_technique_collection_name("None") == "7K Tech"
    assert get_technique_collection_name("none") == "7K Tech"
    assert get_technique_collection_name("") == "7K Tech"
    tags = generate_binned_skill_tags("None", 0.0)
    assert tags == ["dominant_tech", "tech_0★", "dan_0th"]


def test_trailing_whitespace_suffix_idempotence():
    name_with_trailing = "Hard (6.42★ Jack)   "
    assert strip_injected_suffix(name_with_trailing) == "Hard"
    # Reformatting should not accumulate and upgrades to new Dan suffix
    reformatted = format_injected_difficulty_name(name_with_trailing, 6.42, "Jack")
    assert reformatted == "Hard (6.42★ 7th Jack)"


def test_complex_and_unicode_difficulty_names_idempotence_sweep():
    # Property sweep over varied special characters, brackets, Japanese text, and star ratings
    complex_names = [
        "Special [7K] (EX+)",
        "~Insane~ -Another-",
        "東方妖々夢 (Hard 7★)",
        "Freedom Dive [FOUR DIMENSIONS]",
        "123 (456) [789]",
        "Very Long Difficulty Name With Many Words And Punctuation!!",
        "",
    ]
    techniques = [
        "jack", "tech", "speed", "stream",
        "ln_general", "ln_tech", "ln_inverse", "ln_release",
        "LN General", "LN_Release", "None",
    ]
    ratings = [0.0, 1.25, 3.99, 4.0, 5.99, 6.0, 7.99, 8.0, 10.5, 12.345]

    for name in complex_names:
        for tech in techniques:
            for sr in ratings:
                # 1. First format
                f1 = format_injected_difficulty_name(name, sr, tech)
                # 2. Repeated format with identical parameters is strictly identical
                f2 = format_injected_difficulty_name(f1, sr, tech)
                assert f1 == f2, f"Idempotence failed for name={name!r}, sr={sr}, tech={tech!r}"

                # 3. Stripping returns the base name
                stripped = strip_injected_suffix(f1)
                assert stripped == name.strip()





