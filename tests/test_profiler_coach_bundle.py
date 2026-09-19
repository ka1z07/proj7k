"""
Black-box Behavior & Integration Tests for Coaching Recommendations and
Downscaler Practice Bundle Pipeline (ADR-0012, SPEC-P4.1-05, ka1z07/proj7k#37).
"""

import hashlib
import json
from pathlib import Path
import zipfile
import pytest

from proj7k.lazer.bridge import LazerBeatmapRecord
from proj7k.parser import Beatmap7K, HitObject, NoteType, TimingPoint, dump_osu_7k, parse_osu_7k
from proj7k.profiler.aggregate import MacroProfile, DimensionMacroMetric
from proj7k.profiler.cli import main, run_ingestion
from proj7k.profiler.coach import (
    CandidateBeatmap,
    CoachingRecommendation,
    CoachingStrategy,
    PracticeBundleResult,
    ProgressionTierResult,
    extract_high_strain_slice,
    generate_coaching_recommendations,
    generate_targeted_practice_bundle,
    is_dan_beatmap,
    recall_candidate_beatmaps,
)
from proj7k.profiler.osr import OSRReplay, ReplayFrame, serialize_osr
from proj7k.profiler.response import DimensionCapacityResult, SkillRadarReport


def create_mock_beatmap_records() -> list[LazerBeatmapRecord]:
    """Creates a sample list of local Realm beatmap records including Dan maps and regular maps."""
    return [
        # Regular Jack map matching 4th Dan (~4.9★)
        LazerBeatmapRecord(
            id="rec-001",
            hash="hash1",
            md5_hash="md5_1",
            file_hash="fhash1",
            star_rating=4.95,
            difficulty_name="Heavy Jack [Insane]",
            tags="dominant_jack jack_4★ dan_4th chordjack",
            title="Beat of Hammers",
            artist="DJ Strike",
            ruleset_id=3,
            circle_size=7.0,
            creator="KzMapper",
        ),
        # Regular Stream map matching 7th Dan (~6.1★)
        LazerBeatmapRecord(
            id="rec-002",
            hash="hash2",
            md5_hash="md5_2",
            file_hash="fhash2",
            star_rating=6.15,
            difficulty_name="Flow Stream [Extra]",
            tags="dominant_stream stream_6★ dan_7th",
            title="River of Notes",
            artist="FlowMaster",
            ruleset_id=3,
            circle_size=7.0,
            creator="FlowMapper",
        ),
        # Regular Jack map matching 7th Dan (~6.2★)
        LazerBeatmapRecord(
            id="rec-003",
            hash="hash3",
            md5_hash="md5_3",
            file_hash="fhash3",
            star_rating=6.20,
            difficulty_name="Hyper Jack Hammer",
            tags="dominant_jack jack_6★ dan_7th",
            title="Iron Fist",
            artist="DJ Strike",
            ruleset_id=3,
            circle_size=7.0,
            creator="KzMapper",
        ),
        # Dan Test Map by Jinjin (MUST BE EXCLUDED)
        LazerBeatmapRecord(
            id="rec-dan-001",
            hash="hash_dan1",
            md5_hash="md5_dan1",
            file_hash="fhash_dan1",
            star_rating=4.90,
            difficulty_name="[7K] ~ 4th ~ Dan Course [Stage 1]",
            tags="jinjin dan 4th regular_dan",
            title="Jinjin's 7K Regular Dan Course",
            artist="Various Artists",
            ruleset_id=3,
            circle_size=7.0,
            creator="Jinjin",
        ),
        # Dan Test Map named Jinjin's Lv.16.6 (MUST BE EXCLUDED)
        LazerBeatmapRecord(
            id="rec-dan-002",
            hash="hash_dan2",
            md5_hash="md5_dan2",
            file_hash="fhash_dan2",
            star_rating=6.10,
            difficulty_name="incinerate - Rengoku [Jinjin's Lv.16.6]",
            tags="dan test 7th dan",
            title="Rengoku -Purgatorium-",
            artist="incinerate",
            ruleset_id=3,
            circle_size=7.0,
            creator="AnotherMapper",
        ),
        # Non-7K beatmap (ruleset_id != 3) (MUST BE EXCLUDED)
        LazerBeatmapRecord(
            id="rec-std-001",
            hash="hash_std",
            md5_hash="md5_std",
            file_hash="fhash_std",
            star_rating=5.0,
            difficulty_name="Standard Map",
            tags="dominant_jack",
            title="Standard Beat",
            artist="StandardArtist",
            ruleset_id=0,
            circle_size=4.0,
            creator="StdMapper",
        ),
    ]


def test_is_dan_beatmap_strict_filtering():
    """Verifies Dan test maps are strictly identified and regular maps are never misidentified."""
    dan_cases = [
        # Jinjin as creator
        {"creator": "Jinjin", "title": "Random Song", "difficulty_name": "Normal", "tags": ""},
        {"creator": "jinjin", "title": "Another Song", "difficulty_name": "Hard", "tags": ""},
        # Dan course titles and patterns
        {"creator": "MapperA", "title": "7K 5th Dan Exam", "difficulty_name": "Stage 2", "tags": ""},
        {"creator": "MapperB", "title": "osu!mania Regular Dan", "difficulty_name": "7th Dan", "tags": ""},
        {"creator": "MapperC", "title": "LN Dan Phase 2", "difficulty_name": "[7K] ~ 4th ~", "tags": ""},
        {"creator": "MapperD", "title": "Song", "difficulty_name": "Track [Jinjin's Lv.8]", "tags": ""},
        {"creator": "MapperE", "title": "Song", "difficulty_name": "Stage 4", "tags": "jinjin dan course"},
        {"creator": "MapperF", "title": "Song", "difficulty_name": "10th Dan Course", "tags": ""},
        {"creator": "MapperG", "title": "Song", "difficulty_name": "Gamma Dan", "tags": ""},
    ]

    for case in dan_cases:
        assert is_dan_beatmap(case), f"Expected Dan map identification for: {case}"

    # Non-Dan regular maps
    clean_cases = [
        {"creator": "KzMapper", "title": "Dance Dance Revolution", "difficulty_name": "Heavy", "tags": "dominant_jack dan_4th"},
        {"creator": "StepMania", "title": "Danger Zone", "difficulty_name": "Jack [Insane]", "tags": "jack_5★ dan_5th"},
        {"creator": "Bob", "title": "Flower Dance", "difficulty_name": "Expert (6.20★ 7th Jack)", "tags": "dominant_tech"},
    ]

    for case in clean_cases:
        assert not is_dan_beatmap(case), f"False positive Dan identification for: {case}"


def test_candidate_beatmap_recall_with_dan_exclusion():
    """Verifies local Realm scan recalls candidate beatmaps matching target skill/tier with Dan exclusion."""
    records = create_mock_beatmap_records()

    # Query for Jack at 4th Dan (~4.9★)
    candidates = recall_candidate_beatmaps(
        records=records,
        target_technique="jack",
        target_sr=4.9,
        target_dan="4th",
        max_candidates=5,
        exclude_dan=True,
    )

    assert len(candidates) >= 1
    # Candidate must be Heavy Jack (rec-001)
    cand_ids = [c.id for c in candidates]
    assert "rec-001" in cand_ids
    # Dan map rec-dan-001 and rec-dan-002 must NOT be included
    assert "rec-dan-001" not in cand_ids
    assert "rec-dan-002" not in cand_ids
    # Non-7K map must not be included
    assert "rec-std-001" not in cand_ids

    first = candidates[0]
    assert first.matched_skill == "jack"
    assert abs(first.star_rating - 4.9) < 0.5


def test_dual_coaching_strategies():
    """Verifies Bottleneck Breaker and Specialty Push strategies from a SkillRadarReport."""
    radar = SkillRadarReport(
        overall_dan="6th",
        dominant_technique="stream",
        bottleneck_technique="jack",
        dimensions={
            "stream": DimensionCapacityResult(
                dimension="stream",
                effective_capacity=22.0,
                star_rating=6.50,
                dan_tier="8th",
                has_inflection=False,
                peak_chart_strain=25.0,
                tested=True,
                bins=[],
            ),
            "jack": DimensionCapacityResult(
                dimension="jack",
                effective_capacity=12.0,
                star_rating=4.50,
                dan_tier="3rd",
                has_inflection=True,
                peak_chart_strain=20.0,
                tested=True,
                bins=[],
            ),
            "tech": DimensionCapacityResult(
                dimension="tech",
                effective_capacity=16.0,
                star_rating=5.30,
                dan_tier="5th",
                has_inflection=False,
                peak_chart_strain=18.0,
                tested=True,
                bins=[],
            ),
        },
    )

    records = create_mock_beatmap_records()

    recs = generate_coaching_recommendations(
        report_or_profile=radar,
        strategy="both",
        records=records,
    )

    assert len(recs) == 2
    strat_map = {r.strategy: r for r in recs}
    assert CoachingStrategy.BOTTLENECK_BREAKER.value in strat_map
    assert CoachingStrategy.SPECIALTY_PUSH.value in strat_map

    # Bottleneck Breaker should target 'jack'
    bb = strat_map[CoachingStrategy.BOTTLENECK_BREAKER.value]
    assert bb.target_technique == "jack"
    assert bb.current_star_rating == 4.50
    assert bb.current_dan_tier == "3rd"
    assert len(bb.candidates) > 0
    assert all(c.matched_skill == "jack" for c in bb.candidates)

    # Specialty Push should target 'stream'
    sp = strat_map[CoachingStrategy.SPECIALTY_PUSH.value]
    assert sp.target_technique == "stream"
    assert sp.current_star_rating == 6.50
    assert sp.current_dan_tier == "8th"
    assert len(sp.candidates) > 0
    assert all(c.matched_skill == "stream" for c in sp.candidates)


def create_long_chart_with_fatal_section(tmp_path: Path) -> Path:
    """Creates a chart with low density early on and a dense chordjack section around 30s."""
    lines = [
        "osu file format v14",
        "",
        "[General]",
        "AudioFilename: audio.mp3",
        "Mode: 3",
        "",
        "[Difficulty]",
        "CircleSize: 7",
        "OverallDifficulty: 8",
        "",
        "[TimingPoints]",
        "0,300.0,4,2,0,100,1,0",
        "20000,300.0,4,2,0,100,1,0",
        "",
        "[HitObjects]",
    ]
    col_x = [36, 109, 182, 256, 329, 402, 475]

    # Warmup notes: 0s to 18s (one note per 1000ms)
    for t in range(1000, 19000, 1000):
        lines.append(f"{col_x[0]},192,{t},1,0,0:0:0:0:")

    # Fatal high-strain dense section: 20s to 35s (chordjacks on columns 0, 2, 4, 6 every 150ms)
    for t in range(20000, 36000, 150):
        lines.append(f"{col_x[0]},192,{t},1,0,0:0:0:0:")
        lines.append(f"{col_x[2]},192,{t},1,0,0:0:0:0:")
        lines.append(f"{col_x[4]},192,{t},1,0,0:0:0:0:")

    # Outro notes: 40s to 60s
    for t in range(40000, 60000, 1000):
        lines.append(f"{col_x[1]},192,{t},1,0,0:0:0:0:")

    osu_p = tmp_path / "long_chart.osu"
    osu_p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    # Also create a dummy audio file
    (tmp_path / "audio.mp3").write_bytes(b"MOCK_AUDIO_DATA_FOR_BUNDLE_TEST")
    return osu_p


def test_high_strain_section_slice_extraction(tmp_path: Path):
    """Verifies section slice extraction around fatal failure timestamp (-10s, +5s)."""
    osu_path = create_long_chart_with_fatal_section(tmp_path)
    beatmap = parse_osu_7k(str(osu_path))

    fatal_t = 30000.0  # 30.0s
    # Slice should be [20000.0, 35000.0]
    sliced = extract_high_strain_slice(
        beatmap=beatmap,
        fatal_time_ms=fatal_t,
        buffer_before_ms=10000.0,
        buffer_after_ms=5000.0,
    )

    assert len(sliced.hit_objects) > 0
    for ho in sliced.hit_objects:
        assert 20000.0 <= ho.time <= 35000.0

    # Ensure warmup notes (<20s) and outro notes (>35s) are excluded
    times = [ho.time for ho in sliced.hit_objects]
    assert min(times) >= 20000.0
    assert max(times) <= 35000.0
    assert sliced.audio_filename == "audio.mp3"


def test_three_tier_targeted_practice_bundle_generation(tmp_path: Path):
    """
    Verifies generation of Three-Tier Progression Bundle (Recovery, Bridge, Push)
    and packaging into standalone .osz files.
    """
    osu_path = create_long_chart_with_fatal_section(tmp_path)
    beatmap = parse_osu_7k(str(osu_path))
    audio_path = tmp_path / "audio.mp3"
    bundle_out_dir = tmp_path / "bundles"

    fatal_t = 30000.0
    # Provide player capacity lower than fatal section strain to trigger downscaling
    bundle: PracticeBundleResult = generate_targeted_practice_bundle(
        beatmap=beatmap,
        fatal_time_ms=fatal_t,
        player_capacity=10.0,
        dominant_technique="jack",
        output_dir=bundle_out_dir,
        audio_path=audio_path,
    )

    assert bundle.fatal_time_ms == fatal_t
    assert bundle.slice_start_ms == 20000.0
    assert bundle.slice_end_ms == 35000.0

    assert "recovery" in bundle.tiers
    assert "bridge" in bundle.tiers
    assert "push" in bundle.tiers

    rec = bundle.tiers["recovery"]
    bri = bundle.tiers["bridge"]
    pus = bundle.tiers["push"]

    # Monotonic progression: Recovery < Bridge <= Push
    assert len(rec.beatmap.hit_objects) <= len(bri.beatmap.hit_objects)
    assert len(bri.beatmap.hit_objects) <= len(pus.beatmap.hit_objects)

    # Verify .osz packaging
    assert bundle.combined_osz_path is not None
    assert bundle.combined_osz_path.exists()
    assert bundle.combined_osz_path.suffix == ".osz"

    # Inspect zip contents
    with zipfile.ZipFile(bundle.combined_osz_path, "r") as z:
        names = z.namelist()
        # Must contain audio.mp3
        assert "audio.mp3" in names
        # Must contain 3 practice .osu difficulty files
        osu_entries = [n for n in names if n.endswith(".osu")]
        assert len(osu_entries) == 3


def test_cli_recommend_and_bundle_end_to_end(tmp_path: Path, monkeypatch, capsys):
    """Verifies end-to-end CLI with --recommend and --bundle arguments."""
    osu_path = create_long_chart_with_fatal_section(tmp_path)
    beatmap = parse_osu_7k(str(osu_path))
    audio_path = tmp_path / "audio.mp3"

    # Create a replay that fails at 30.0s
    beatmap_bytes = osu_path.read_bytes()
    md5_hash = hashlib.md5(beatmap_bytes).hexdigest()

    frames = [
        ReplayFrame(time_ms=0.0, keys=0),
        ReplayFrame(time_ms=10000.0, keys=1),
        ReplayFrame(time_ms=25000.0, keys=5),
        ReplayFrame(time_ms=30000.0, keys=0),
    ]

    replay = OSRReplay(
        mode=3,
        game_version=20240101,
        beatmap_hash=md5_hash,
        player_name="TestHero",
        replay_hash="rep_test_001",
        c300g=15,
        c300=5,
        c200=2,
        c100=0,
        c50=0,
        miss=10,
        total_score=350000,
        max_combo=15,
        perfect=False,
        mods=0,
        life_bar="0|1.0,29000|0.3,30000|0.0",
        timestamp_ticks=638000000000000000,
        action_frames=frames,
    )

    osr_path = tmp_path / "failed_play.osr"
    osr_path.write_bytes(serialize_osr(replay))

    bundle_dir = tmp_path / "cli_bundles"

    # Test text output mode
    exit_code = main([
        "--replay", str(osr_path),
        "--beatmap", str(osu_path),
        "--fatal-time", "30000",
        "--recommend",
        "--bundle",
        "--bundle-dir", str(bundle_dir),
        "--no-save",
    ])
    assert exit_code == 0
    captured = capsys.readouterr()
    assert "Coaching Recommendations" in captured.out
    assert "Targeted Practice Bundle" in captured.out
    assert "Recovery" in captured.out
    assert "Bridge" in captured.out
    assert "Push" in captured.out

    # Test JSON output mode
    exit_code_json = main([
        "--replay", str(osr_path),
        "--beatmap", str(osu_path),
        "--fatal-time", "30000",
        "--recommend",
        "--bundle",
        "--bundle-dir", str(bundle_dir),
        "--no-save",
        "--json",
    ])
    assert exit_code_json == 0
    captured_json = capsys.readouterr()
    data = json.loads(captured_json.out)
    assert "coaching_recommendations" in data
    assert "practice_bundle" in data
    assert data["practice_bundle"]["fatal_time_ms"] == 30000.0
