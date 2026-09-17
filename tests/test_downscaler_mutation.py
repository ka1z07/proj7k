import hashlib
import tempfile
import os
import pytest
from proj7k.parser import (
    Beatmap7K,
    HitObject,
    NoteType,
    TimingPoint,
    parse_osu_7k,
    dump_osu_7k,
)
from proj7k.downscaler import (
    apply_pure_deletion,
    update_practice_metadata,
    create_practice_beatmap,
    export_practice_beatmap,
)

SAMPLE_COMPLEX_OSU = """osu file format v14

[General]
AudioFilename: audio.mp3
AudioLeadIn: 0
PreviewTime: -1
Countdown: 0
SampleSet: Normal
StackLeniency: 0.7
Mode: 3
LetterboxInBreaks: 0
SpecialStyle: 0
WidescreenStoryboard: 0

[Metadata]
Title:Imperishable Night
Artist:ZUN
Creator:Jinjin
Version:Stellium Dan Phase
Source:Touhou
Tags:boss 7k ln chordjack

[Difficulty]
HPDrainRate:8
CircleSize:7
OverallDifficulty:8.5
ApproachRate:8
SliderMultiplier:1.4
SliderTickRate:1

[Events]
//Background and Video events
0,0,"bg.jpg",0,0
//Break Periods
//Storyboard Sound Samples

[TimingPoints]
1000,300,4,2,1,70,1,0
5000,-100,4,2,1,70,0,0

[HitObjects]
36,192,1000,1,0,0:0:0:0:
109,192,1000,1,0,0:0:0:0:
182,192,1150,128,0,2000:0:0:0:0:
256,192,1150,1,0,0:0:0:0:
329,192,1300,1,0,0:0:0:0:
402,192,1300,128,0,2500:0:0:0:0:
475,192,1450,1,0,0:0:0:0:
36,192,1600,1,0,0:0:0:0:
109,192,1600,128,0,3000:0:0:0:0:
256,192,1750,1,0,0:0:0:0:
"""


def test_dump_osu_7k_basic():
    bm = parse_osu_7k(SAMPLE_COMPLEX_OSU)
    dumped = dump_osu_7k(bm)

    assert "osu file format v14" in dumped
    assert "[General]" in dumped
    assert "Mode: 3" in dumped
    assert "[Metadata]" in dumped
    assert "Title:Imperishable Night" in dumped
    assert "Artist:ZUN" in dumped
    assert "Version:Stellium Dan Phase" in dumped
    assert "Tags:boss 7k ln chordjack" in dumped
    assert "[Difficulty]" in dumped
    assert "CircleSize:7" in dumped
    assert "[TimingPoints]" in dumped
    assert "[HitObjects]" in dumped

    # Round trip parsing
    reloaded = parse_osu_7k(dumped)
    assert reloaded.title == bm.title
    assert reloaded.artist == bm.artist
    assert reloaded.creator == bm.creator
    assert reloaded.version == bm.version
    assert reloaded.mode == 3
    assert reloaded.circle_size == 7
    assert reloaded.overall_difficulty == 8.5
    assert len(reloaded.timing_points) == len(bm.timing_points)
    assert len(reloaded.hit_objects) == len(bm.hit_objects)

    for orig_ho, new_ho in zip(bm.hit_objects, reloaded.hit_objects):
        assert orig_ho.column == new_ho.column
        assert orig_ho.time == new_ho.time
        assert orig_ho.note_type == new_ho.note_type
        if orig_ho.note_type == NoteType.LN:
            assert orig_ho.end_time == new_ho.end_time


def test_dump_osu_7k_synthetic():
    """Verify dump_osu_7k works on programmatic Beatmap7K instances without raw sections."""
    tp = TimingPoint(time=500.0, beat_length=400.0, meter=4, uninherited=True)
    hos = [
        HitObject(column=0, time=500.0, note_type=NoteType.RICE),
        HitObject(column=3, time=700.0, note_type=NoteType.LN, end_time=1500.0),
        HitObject(column=6, time=900.0, note_type=NoteType.RICE),
    ]
    bm = Beatmap7K(
        title="Synthetic Song",
        artist="Synthetic Artist",
        creator="Tester",
        version="Extra",
        overall_difficulty=8.0,
        timing_points=[tp],
        hit_objects=hos,
    )

    dumped = dump_osu_7k(bm)
    reloaded = parse_osu_7k(dumped)

    assert reloaded.title == "Synthetic Song"
    assert reloaded.artist == "Synthetic Artist"
    assert reloaded.version == "Extra"
    assert len(reloaded.hit_objects) == 3
    assert reloaded.hit_objects[0].column == 0
    assert reloaded.hit_objects[0].note_type == NoteType.RICE
    assert reloaded.hit_objects[1].column == 3
    assert reloaded.hit_objects[1].note_type == NoteType.LN
    assert reloaded.hit_objects[1].end_time == 1500.0
    assert reloaded.hit_objects[2].column == 6


def test_pure_deletion_mutation_atomic():
    bm = parse_osu_7k(SAMPLE_COMPLEX_OSU)
    total_notes_orig = len(bm.hit_objects)
    assert total_notes_orig == 10

    # Index 2 is an LN (col 2, time 1150, end 2000)
    # Index 4 is a Rice (col 4, time 1300)
    # Index 5 is an LN (col 5, time 1300, end 2500)
    to_remove_indices = [2, 4, 5]
    mutated = apply_pure_deletion(bm, to_remove_indices)

    # 1. Total count is reduced by exactly len(to_remove_indices)
    assert len(mutated.hit_objects) == total_notes_orig - 3
    # 2. Original beatmap is unmodified
    assert len(bm.hit_objects) == total_notes_orig

    # 3. Verify removed notes are completely absent (primitive closed set integrity)
    remaining_keys = [
        (h.column, h.time, h.note_type, h.end_time) for h in mutated.hit_objects
    ]
    assert (2, 1150.0, NoteType.LN, 2000.0) not in remaining_keys
    assert (4, 1300.0, NoteType.RICE, None) not in remaining_keys
    assert (5, 1300.0, NoteType.LN, 2500.0) not in remaining_keys

    # Verify no stray rice note was created from LN head/tail (pure deletion invariant)
    assert not any(h.column == 2 and h.time == 1150.0 for h in mutated.hit_objects)
    assert not any(h.column == 2 and h.time == 2000.0 for h in mutated.hit_objects)
    assert not any(h.column == 5 and h.time == 1300.0 for h in mutated.hit_objects)
    assert not any(h.column == 5 and h.time == 2500.0 for h in mutated.hit_objects)


def test_pure_deletion_with_hitobject_set_and_predicate():
    bm = parse_osu_7k(SAMPLE_COMPLEX_OSU)

    # Remove using HitObject instances
    target_ln = bm.hit_objects[2]
    mutated = apply_pure_deletion(bm, [target_ln])
    assert len(mutated.hit_objects) == len(bm.hit_objects) - 1
    assert target_ln not in mutated.hit_objects

    # Remove using predicate (e.g. remove all notes on column 0)
    mutated_col0 = apply_pure_deletion(bm, lambda ho: ho.column == 0)
    assert all(ho.column != 0 for ho in mutated_col0.hit_objects)
    assert len(mutated_col0.hit_objects) == len(bm.hit_objects) - 2


def test_update_practice_metadata():
    bm = parse_osu_7k(SAMPLE_COMPLEX_OSU)
    orig_md5 = "a1b2c3d4e5f67890abcdef1234567890"

    updated = update_practice_metadata(bm, target_dan="7th", original_md5=orig_md5)
    assert updated.version == "[P-7th] Stellium Dan Phase"
    assert "proj7k_downscaled" in updated.tags
    assert "target_7th" in updated.tags
    assert "orig_md5_a1b2c3d4" in updated.tags
    assert "boss 7k ln chordjack" in updated.tags

    # Idempotent tags: calling again should not duplicate tags
    again = update_practice_metadata(updated, target_dan="7th", original_md5=orig_md5)
    tags_list = again.tags.split()
    assert tags_list.count("proj7k_downscaled") == 1
    assert tags_list.count("target_7th") == 1
    assert tags_list.count("orig_md5_a1b2c3d4") == 1


def test_export_practice_beatmap_e2e_roundtrip():
    # Write sample osu to temporary file
    with tempfile.NamedTemporaryFile("w", suffix=".osu", delete=False) as f:
        f.write(SAMPLE_COMPLEX_OSU)
        tmp_src_path = f.name

    try:
        orig_bytes = SAMPLE_COMPLEX_OSU.encode("utf-8")
        expected_md5 = hashlib.md5(orig_bytes).hexdigest()

        # Thin out notes: delete index 0, 2 (an LN), 5 (an LN), 7
        to_remove = [0, 2, 5, 7]

        with tempfile.NamedTemporaryFile("w", suffix=".osu", delete=False) as out_f:
            tmp_out_path = out_f.name

        practice_bm, dumped_text = export_practice_beatmap(
            content_or_path=tmp_src_path,
            target_dan="7th",
            notes_to_remove=to_remove,
            output_path=tmp_out_path,
        )

        # 1. Output file exists and matches returned text
        assert os.path.exists(tmp_out_path)
        with open(tmp_out_path, "r", encoding="utf-8") as f:
            file_content = f.read()
        assert file_content == dumped_text

        # 2. Metadata compliance
        assert practice_bm.version == "[P-7th] Stellium Dan Phase"
        assert f"orig_md5_{expected_md5[:8]}" in practice_bm.tags
        assert "target_7th" in practice_bm.tags
        assert "proj7k_downscaled" in practice_bm.tags

        # 3. Strict 100% round-trip re-parsing verification
        reparsed = parse_osu_7k(tmp_out_path)
        assert reparsed.title == practice_bm.title
        assert reparsed.artist == practice_bm.artist
        assert reparsed.version == practice_bm.version
        assert reparsed.tags == practice_bm.tags
        assert len(reparsed.hit_objects) == len(practice_bm.hit_objects)
        assert len(reparsed.hit_objects) == 10 - len(to_remove)

        for expected_ho, actual_ho in zip(practice_bm.hit_objects, reparsed.hit_objects):
            assert actual_ho.column == expected_ho.column
            assert actual_ho.time == expected_ho.time
            assert actual_ho.note_type == expected_ho.note_type
            if expected_ho.note_type == NoteType.LN:
                assert actual_ho.end_time == expected_ho.end_time

        # Clean up output file
        os.remove(tmp_out_path)
    finally:
        os.remove(tmp_src_path)


def test_immutability_of_input_beatmap():
    """Verify that pure deletion and metadata update do not mutate original input objects."""
    bm = parse_osu_7k(SAMPLE_COMPLEX_OSU)
    orig_version = bm.version
    orig_tags = bm.tags
    orig_meta_ver = bm.extra_sections["Metadata"]["Version"]
    orig_meta_tags = bm.extra_sections["Metadata"]["Tags"]
    orig_ho_count = len(bm.hit_objects)

    practice_bm = create_practice_beatmap(
        bm,
        target_dan="5th",
        dominant_skill="Chordjack",
        notes_to_remove=[0, 1, 2],
    )

    # Derived beatmap updated
    assert practice_bm.version == "[P-5th Chordjack] Stellium Dan Phase"
    assert "dominant_Chordjack" in practice_bm.tags
    assert len(practice_bm.hit_objects) == orig_ho_count - 3

    # Original beatmap strictly unchanged
    assert bm.version == orig_version
    assert bm.tags == orig_tags
    assert bm.extra_sections["Metadata"]["Version"] == orig_meta_ver
    assert bm.extra_sections["Metadata"]["Tags"] == orig_meta_tags
    assert len(bm.hit_objects) == orig_ho_count


def test_high_difficulty_dense_7k_e2e():
    """
    End-to-end test on a dense high-difficulty 7K beatmap (Chordjack + LN Inverse pattern)
    with 100+ hit objects, verifying pure deletion thinning, round-trip serialization,
    and strain/feature compatibility.
    """
    # Build high difficulty 7K pattern (200 BPM, 300ms beat length)
    tps = [
        TimingPoint(time=0.0, beat_length=300.0, meter=4, uninherited=True),
        TimingPoint(time=3600.0, beat_length=250.0, meter=4, uninherited=True),
    ]
    hos = []
    t = 1000.0
    # Create 32 measures of dense 16th and 8th notes with chords and LNs
    for i in range(40):
        # 3-note or 4-note chordjack on 8th beats
        cols = [0, 2, 4] if (i % 2 == 0) else [1, 3, 5]
        if i % 4 == 0:
            cols.append(6)
        for col in cols:
            if col in (2, 5) and (i % 3 == 0):
                # LN note
                hos.append(HitObject(column=col, time=t, note_type=NoteType.LN, end_time=t + 250.0))
            else:
                # Rice note
                hos.append(HitObject(column=col, time=t, note_type=NoteType.RICE))
        t += 150.0  # 8th note step

    assert len(hos) >= 120

    bm = Beatmap7K(
        title="High Difficulty 7K Masterpiece",
        artist="Virtuoso",
        creator="Charter",
        version="Ultimate [10th Dan]",
        overall_difficulty=9.0,
        timing_points=tps,
        hit_objects=hos,
    )

    # Select 25% of notes for pure deletion thinning (every 4th note)
    notes_to_remove = [i for i in range(len(hos)) if i % 4 == 0]
    expected_surviving_count = len(hos) - len(notes_to_remove)

    with tempfile.NamedTemporaryFile("w", suffix=".osu", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        practice_bm, dumped_text = export_practice_beatmap(
            content_or_path=bm,
            target_dan="7th",
            dominant_skill="Jack",
            notes_to_remove=notes_to_remove,
            output_path=tmp_path,
        )

        assert len(practice_bm.hit_objects) == expected_surviving_count
        assert practice_bm.version == "[P-7th Jack] Ultimate [10th Dan]"
        assert "target_7th" in practice_bm.tags
        assert "dominant_Jack" in practice_bm.tags

        # Re-parse from file
        reloaded = parse_osu_7k(tmp_path)
        assert len(reloaded.hit_objects) == expected_surviving_count
        assert reloaded.version == practice_bm.version
        assert reloaded.tags == practice_bm.tags
        assert len(reloaded.timing_points) == 2

        # Verify exact hit object matches
        for expected_h, actual_h in zip(practice_bm.hit_objects, reloaded.hit_objects):
            assert actual_h.column == expected_h.column
            assert actual_h.time == expected_h.time
            assert actual_h.note_type == expected_h.note_type
            if expected_h.note_type == NoteType.LN:
                assert actual_h.end_time == expected_h.end_time

        # Verify that downstream strain/feature analysis works smoothly on the parsed practice beatmap
        from proj7k.features import extract_beatmap_features
        features = extract_beatmap_features(reloaded)
        assert features.total_notes == expected_surviving_count
        assert features.avg_nps > 0.0

    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)

