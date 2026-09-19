import hashlib
from pathlib import Path
import pytest

from proj7k.parser import Beatmap7K, HitObject, NoteType, parse_osu_7k
from proj7k.profiler.matcher import AlignedHit, HitAlignmentResult, HitJudgment, align_replay_hits
from proj7k.profiler.osr import OSRReplay, ReplayFrame, serialize_osr
from proj7k.radar import TECHNIQUE_NAMES
from proj7k.strain import (
    TechniqueStrainTimeseries,
    compute_8d_strain_timeseries,
)


def create_synthetic_chart(tmp_path: Path, bpm: float = 150.0, num_notes: int = 40) -> Path:
    """Creates a controlled synthetic osu!mania 7K chart."""
    lines = [
        "osu file format v14",
        "",
        "[General]",
        "Mode: 3",
        "",
        "[Difficulty]",
        "CircleSize: 7",
        "OverallDifficulty: 8",
        "",
        "[TimingPoints]",
        f"0,{60000.0 / bpm},4,2,0,100,1,0",
        "",
        "[HitObjects]",
    ]
    # Generate alternating Jack on column 0 every 150ms
    col_x = [36, 109, 182, 256, 329, 402, 475]
    for i in range(num_notes):
        t = 1000 + i * 150
        c = 0 if i < 20 else (i % 7)
        x = col_x[c]
        lines.append(f"{x},192,{t},1,0,0:0:0:0:")

    osu_content = "\n".join(lines) + "\n"
    osu_path = tmp_path / "synthetic_test.osu"
    osu_path.write_text(osu_content, encoding="utf-8")
    return osu_path


def test_8d_strain_timeseries_computation_and_point_to_point_alignment(tmp_path: Path):
    osu_path = create_synthetic_chart(tmp_path)
    beatmap = parse_osu_7k(str(osu_path))

    # 1. Compute continuous 8-dimensional strain curves
    timeseries: TechniqueStrainTimeseries = compute_8d_strain_timeseries(beatmap)

    assert timeseries.step_seconds == 0.25
    assert len(timeseries.times) > 0
    for tech in TECHNIQUE_NAMES:
        curve = getattr(timeseries, tech)
        assert len(curve) == len(timeseries.times)
        assert all(isinstance(v, (int, float)) for v in curve)

    # Jack strain should be positive where col 0 repeated notes exist
    assert max(timeseries.jack) > 0.0

    # 2. Point-to-point interpolation at hit timestamps
    t_probe = 1500.0  # ms
    strains_at_t = timeseries.get_strains_at(t_probe / 1000.0)
    assert set(strains_at_t.keys()) == set(TECHNIQUE_NAMES)
    assert strains_at_t["jack"] >= 0.0


def test_strain_response_inflection_detection_and_dan_mapping(tmp_path: Path):
    from proj7k.profiler.response import analyze_strain_response

    osu_path = create_synthetic_chart(tmp_path, bpm=160.0, num_notes=50)
    beatmap = parse_osu_7k(str(osu_path))
    timeseries = compute_8d_strain_timeseries(beatmap)

    # Synthetic player with breakdown at high strain:
    # First 25 notes (lower strain): accurate hits (offset 2..5ms, MAX/PERFECT)
    # Next 25 notes (higher strain): breakdown (offset 35..60ms and MISSes)
    aligned_hits = []
    for i, ho in enumerate(beatmap.hit_objects):
        t = ho.time
        strains = timeseries.get_strains_at(t / 1000.0)
        if i < 25:
            offset = 3.0
            judgment = HitJudgment.MAX
            hit_t = t + offset
        else:
            if i % 3 == 0:
                offset = None
                judgment = HitJudgment.MISS
                hit_t = None
            else:
                offset = 40.0
                judgment = HitJudgment.GOOD
                hit_t = t + offset

        aligned_hits.append(
            AlignedHit(
                column=ho.column,
                target_time=t,
                hit_time=hit_t,
                offset_ms=offset,
                judgment=judgment,
                strains=strains,
            )
        )

    alignment = HitAlignmentResult(
        aligned_hits=aligned_hits,
        total_hits=sum(1 for h in aligned_hits if h.judgment != HitJudgment.MISS),
        miss_count=sum(1 for h in aligned_hits if h.judgment == HitJudgment.MISS),
    )

    report = analyze_strain_response(alignment, beatmap, strain_timeseries=timeseries)

    assert "jack" in report.dimensions
    jack_res = report.dimensions["jack"]
    assert jack_res.tested is True
    assert jack_res.has_inflection is True
    assert jack_res.effective_capacity > 0.0
    assert jack_res.star_rating > 0.0
    assert jack_res.dan_tier != ""
    assert len(jack_res.bins) > 0

    # LN General should be untested (pure rice chart)
    ln_gen = report.dimensions["ln_general"]
    assert ln_gen.tested is False
    assert ln_gen.effective_capacity == 0.0
    assert ln_gen.dan_tier == "0th"


def test_stable_player_without_inflection_reaches_peak_strain(tmp_path: Path):
    from proj7k.profiler.response import analyze_strain_response

    osu_path = create_synthetic_chart(tmp_path, bpm=160.0, num_notes=40)
    beatmap = parse_osu_7k(str(osu_path))
    timeseries = compute_8d_strain_timeseries(beatmap)

    # Player hits all notes cleanly with no misses
    aligned_hits = [
        AlignedHit(
            column=ho.column,
            target_time=ho.time,
            hit_time=ho.time + 2.0,
            offset_ms=2.0,
            judgment=HitJudgment.MAX,
            strains=timeseries.get_strains_at(ho.time / 1000.0),
        )
        for ho in beatmap.hit_objects
    ]

    alignment = HitAlignmentResult(
        aligned_hits=aligned_hits,
        total_hits=len(aligned_hits),
        miss_count=0,
    )

    report = analyze_strain_response(alignment, beatmap, strain_timeseries=timeseries)
    jack_res = report.dimensions["jack"]
    assert jack_res.tested is True
    assert jack_res.has_inflection is False
    assert pytest.approx(jack_res.effective_capacity, abs=1e-1) == jack_res.peak_chart_strain


def test_end_to_end_profiler_cli_radar_output(tmp_path: Path, capsys):
    from proj7k.profiler.cli import main, run_ingestion

    osu_path = create_synthetic_chart(tmp_path, bpm=160.0, num_notes=40)
    beatmap = parse_osu_7k(str(osu_path))
    beatmap_content = osu_path.read_text(encoding="utf-8")
    md5_hash = hashlib.md5(beatmap_content.encode("utf-8")).hexdigest()

    # Generate synthetic replay matching notes
    frames = [ReplayFrame(time_ms=0.0, keys=0)]
    for ho in beatmap.hit_objects:
        # col to bitmask: 1 << col
        k_mask = 1 << ho.column
        frames.append(ReplayFrame(time_ms=ho.time + 3.0, keys=k_mask))
        frames.append(ReplayFrame(time_ms=ho.time + 50.0, keys=0))

    replay = OSRReplay(
        mode=3,
        game_version=20240101,
        beatmap_hash=md5_hash,
        player_name="DanMaster",
        replay_hash="hash_radar",
        c300g=len(beatmap.hit_objects),
        c300=0,
        c200=0,
        c100=0,
        c50=0,
        miss=0,
        total_score=1000000,
        max_combo=len(beatmap.hit_objects),
        perfect=True,
        mods=0,
        timestamp_ticks=638000000000000000,
        action_frames=frames,
    )
    osr_path = tmp_path / "dan_test.osr"
    osr_path.write_bytes(serialize_osr(replay))

    # 1. Test run_ingestion / run_profiler returns skill_radar
    report = run_ingestion(osr_path, osu_path)
    assert report.skill_radar is not None
    assert len(report.skill_radar.dimensions) == 8
    assert report.skill_radar.overall_dan != ""

    report_dict = report.to_dict()
    assert "skill_radar" in report_dict
    assert "dimensions" in report_dict["skill_radar"]
    assert "overall_dan" in report_dict["skill_radar"]

    # Point-to-point strain alignment check in aligned_hits dict
    assert len(report_dict["aligned_hits"]) > 0
    assert "strains" in report_dict["aligned_hits"][0]
    assert set(report_dict["aligned_hits"][0]["strains"].keys()) == set(TECHNIQUE_NAMES)

    # 2. Test CLI stdout formatting
    ret_code = main(["--replay", str(osr_path), "--beatmap", str(osu_path)])
    assert ret_code == 0
    captured = capsys.readouterr()
    assert "8-Dimension Skill Radar & Dan Breakdown" in captured.out
    assert "Overall Dan:" in captured.out

    # 3. Test CLI JSON output
    ret_json = main(["--replay", str(osr_path), "--beatmap", str(osu_path), "--json"])
    assert ret_json == 0
    captured_json = capsys.readouterr()
    import json
    data = json.loads(captured_json.out)
    assert "skill_radar" in data
    assert "dimensions" in data["skill_radar"]
    assert "jack" in data["skill_radar"]["dimensions"]


def test_black_box_osr_synthetic_breakdown_inflection(tmp_path: Path):
    """
    Black-box test verifying accurate strain-error inflection detection and Dan tier
    mapping from a serialized .osr replay exhibiting high-strain breakdown (Ticket 0017 AC5).
    """
    from proj7k.profiler.cli import run_ingestion

    # Chart with escalating Jack strain:
    # Phase 1: 30 notes spaced at 250ms on alternating lanes (low/moderate strain)
    # Phase 2: 30 notes spaced at 100ms on Col 0 (high-strain 10th Dan chordjack)
    lines = [
        "osu file format v14",
        "",
        "[General]",
        "Mode: 3",
        "",
        "[Difficulty]",
        "CircleSize: 7",
        "OverallDifficulty: 8",
        "",
        "[HitObjects]",
    ]
    t_curr = 1000
    hit_objects_info = []
    # Phase 1: 30 notes
    for i in range(30):
        c = i % 7
        x = [36, 109, 182, 256, 329, 402, 475][c]
        lines.append(f"{x},192,{t_curr},1,0,0:0:0:0:")
        hit_objects_info.append((c, t_curr))
        t_curr += 250

    # Phase 2: 30 intense Jack notes on column 0
    for i in range(30):
        c = 0
        x = 36
        lines.append(f"{x},192,{t_curr},1,0,0:0:0:0:")
        hit_objects_info.append((c, t_curr))
        t_curr += 100

    osu_content = "\n".join(lines) + "\n"
    osu_path = tmp_path / "escalating_map.osu"
    osu_path.write_text(osu_content, encoding="utf-8")
    md5_hash = hashlib.md5(osu_content.encode("utf-8")).hexdigest()

    # Replay:
    # Phase 1: accurately hit (+2ms)
    # Phase 2: player breaks down (+45ms errors and 10 misses)
    frames = [ReplayFrame(time_ms=0.0, keys=0)]
    for i, (col, target_t) in enumerate(hit_objects_info):
        if i < 30:
            # Clean hit
            frames.append(ReplayFrame(time_ms=target_t + 2.0, keys=1 << col))
            frames.append(ReplayFrame(time_ms=target_t + 40.0, keys=0))
        else:
            # High-strain collapse
            if i % 3 == 0:
                # Miss (no press in window)
                continue
            else:
                # Severe late hit
                frames.append(ReplayFrame(time_ms=target_t + 45.0, keys=1 << col))
                frames.append(ReplayFrame(time_ms=target_t + 85.0, keys=0))

    replay = OSRReplay(
        mode=3,
        game_version=20240101,
        beatmap_hash=md5_hash,
        player_name="BreakdownPlayer",
        replay_hash="hash_break",
        c300g=30,
        c300=0,
        c200=10,
        c100=10,
        c50=0,
        miss=10,
        total_score=650000,
        max_combo=30,
        perfect=False,
        mods=0,
        timestamp_ticks=638000000000000000,
        action_frames=frames,
    )
    osr_path = tmp_path / "breakdown.osr"
    osr_path.write_bytes(serialize_osr(replay))

    # Black-box run
    report = run_ingestion(osr_path, osu_path)

    assert report.skill_radar is not None
    jack_result = report.skill_radar.dimensions["jack"]
    assert jack_result.tested is True
    assert jack_result.has_inflection is True
    # Inflection capacity should be strictly lower than peak chart strain
    assert jack_result.effective_capacity < jack_result.peak_chart_strain
    assert jack_result.star_rating > 0.0
    assert jack_result.dan_tier != ""
