import hashlib
from pathlib import Path
import pytest

from proj7k.parser import Beatmap7K, HitObject, NoteType, parse_osu_7k
from proj7k.profiler.matcher import AlignedHit, HitAlignmentResult, HitJudgment, align_replay_hits
from proj7k.profiler.osr import OSRReplay, ReplayFrame, serialize_osr
from proj7k.engine.scale import stars_of
from proj7k.field import trace_beatmap
from proj7k.profiler.response import analyze_strain_response
from proj7k.radar import TECHNIQUE_NAMES


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


def create_jack_chart(tmp_path: Path, num_notes: int = 240, gap_ms: int = 140) -> Path:
    """One long jack on column 0: nearly all of its weight is the Jack skill's."""
    lines = ["osu file format v14", "", "[General]", "Mode: 3", "", "[Difficulty]", "CircleSize: 7",
             "OverallDifficulty: 8", "", "[TimingPoints]", "0,400,4,2,0,100,1,0", "", "[HitObjects]"]
    lines += [f"36,192,{1000 + i * gap_ms},1,0,0:0:0:0:" for i in range(num_notes)]
    path = tmp_path / "jack.osu"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _play(beatmap, judge) -> HitAlignmentResult:
    """An alignment where `judge(i, note)` gives (judgment, tail judgment | None) of note i; a MISS has no hit time."""
    hits = []
    for i, ho in enumerate(beatmap.hit_objects):
        judgment, tail = judge(i, ho)
        missed = judgment == HitJudgment.MISS
        hits.append(AlignedHit(
            column=ho.column, target_time=ho.time,
            hit_time=None if missed else ho.time + 3.0, offset_ms=None if missed else 3.0,
            judgment=judgment, note_type=ho.note_type, end_time=ho.end_time, tail_judgment=tail,
        ))
    return HitAlignmentResult(
        aligned_hits=hits, total_hits=len(hits), miss_count=sum(h.judgment == HitJudgment.MISS for h in hits),
    )


def test_every_hit_is_laid_on_the_fields_demand_readings(tmp_path: Path):
    beatmap = parse_osu_7k(str(create_synthetic_chart(tmp_path)))
    field = trace_beatmap(beatmap)
    alignment = _play(beatmap, lambda i, ho: (HitJudgment.MAX, None))

    analyze_strain_response(alignment, beatmap, field=field)

    for hit in alignment.aligned_hits:
        assert set(hit.strains) == set(TECHNIQUE_NAMES)
    assert max(sum(h.strains.values()) for h in alignment.aligned_hits) > 0.0
    assert max(h.strains["jack"] for h in alignment.aligned_hits) == pytest.approx(
        max(field.d[i] * field.w[i, 0] for i in range(field.events.n))
    )


def test_a_player_who_breaks_down_reads_lower_than_one_who_does_not(tmp_path: Path):
    beatmap = parse_osu_7k(str(create_jack_chart(tmp_path)))
    field = trace_beatmap(beatmap)
    clean = analyze_strain_response(_play(beatmap, lambda i, ho: (HitJudgment.MAX, None)), beatmap, field=field)
    broke = analyze_strain_response(
        _play(beatmap, lambda i, ho: (HitJudgment.MISS if i >= 120 and i % 3 == 0 else (HitJudgment.GOOD if i >= 120 else HitJudgment.MAX), None)),
        beatmap, field=field,
    )

    cj, bj = clean.dimensions["jack"], broke.dimensions["jack"]
    assert cj.tested and bj.tested
    assert cj.broke_down is False and bj.broke_down is True
    assert bj.effective_capacity < bj.chart_level < cj.effective_capacity
    assert bj.star_rating == pytest.approx(stars_of(bj.effective_capacity))
    assert bj.dan_tier != ""
    assert len(bj.bins) > 0
    # the rice chart has no LN to fail at
    ln = clean.dimensions["ln_general"]
    assert ln.tested is False and ln.effective_capacity == 0.0 and ln.dan_tier == "0th"
    assert broke.dominant_technique == "jack" == broke.bottleneck_technique


def test_losing_exactly_the_tolerance_reads_the_charts_own_difficulty(tmp_path: Path):
    """The inverse of the engine's equation: a player at the chart's D loses eps of it, so reads D (ADR-0020)."""
    beatmap = parse_osu_7k(str(create_jack_chart(tmp_path)))
    field = trace_beatmap(beatmap)
    from proj7k.engine.params import DEFAULT
    from proj7k.engine.skills import SKILLS
    k = SKILLS.index("rc_jack")
    weight = float(field.w[:, k].sum())
    lost_notes = int(round(DEFAULT.eps_rc * weight))
    alignment = _play(beatmap, lambda i, ho: (HitJudgment.MISS if i < lost_notes else HitJudgment.MAX, None))
    # losing a whole number of notes is not exactly eps * W, so read what was lost, not the idealisation
    report = analyze_strain_response(alignment, beatmap, field=field)
    jack = report.dimensions["jack"]
    assert jack.chart_level == pytest.approx(field.profile.skills["rc_jack"].D)
    assert jack.effective_capacity == pytest.approx(jack.chart_level, rel=0.15)


def test_a_play_that_failed_out_is_not_held_to_the_rest_of_the_song(tmp_path: Path):
    beatmap = parse_osu_7k(str(create_jack_chart(tmp_path)))
    field = trace_beatmap(beatmap)
    half_ms = beatmap.hit_objects[120].time
    alignment = _play(beatmap, lambda i, ho: (HitJudgment.MAX if i < 120 else HitJudgment.MISS, None))

    held_to_all = analyze_strain_response(alignment, beatmap, field=field).dimensions["jack"]
    failed_out = analyze_strain_response(alignment, beatmap, field=field, played_until_s=half_ms / 1000.0 - 0.01).dimensions["jack"]

    assert failed_out.events < held_to_all.events
    assert failed_out.broke_down is False
    assert failed_out.effective_capacity > held_to_all.effective_capacity


def test_ln_releases_are_what_the_ln_skills_are_read_from(tmp_path: Path):
    lines = ["osu file format v14", "", "[General]", "Mode: 3", "", "[Difficulty]", "CircleSize: 7",
             "OverallDifficulty: 8", "", "[TimingPoints]", "0,400,4,2,0,100,1,0", "", "[HitObjects]"]
    xs = [36, 109, 182, 256, 329, 402, 475]
    for i in range(150):
        c = (i * 3) % 7
        lines.append(f"{xs[c]},192,{1000 + i * 100},128,0,{1000 + i * 100 + 260}:0:0:0:0:")
    beatmap = parse_osu_7k("\n".join(lines) + "\n")
    field = trace_beatmap(beatmap)

    clean = analyze_strain_response(_play(beatmap, lambda i, ho: (HitJudgment.MAX, HitJudgment.MAX)), beatmap, field=field)
    dropped = analyze_strain_response(_play(beatmap, lambda i, ho: (HitJudgment.MAX, HitJudgment.MISS)), beatmap, field=field)

    assert clean.dimensions["ln_release"].tested and dropped.dimensions["ln_release"].tested
    assert dropped.dimensions["ln_release"].effective_capacity < clean.dimensions["ln_release"].effective_capacity
    # Inverse is read at the press (§8.1); the tails dropped here are not what it measures
    assert dropped.dimensions["ln_inverse"].effective_capacity == clean.dimensions["ln_inverse"].effective_capacity
    # an LN chart's rice skills are not what dropping tails shows
    assert not dropped.dimensions["jack"].tested


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

    # Point-to-point alignment with the engine's demand readings, in aligned_hits dict
    assert len(report_dict["aligned_hits"]) > 0
    assert "strains" in report_dict["aligned_hits"][0]
    assert set(report_dict["aligned_hits"][0]["strains"].keys()) == set(TECHNIQUE_NAMES)
    assert report.field is not None and report.field.total_D > 0

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


def test_black_box_osr_synthetic_breakdown(tmp_path: Path):
    """
    Black-box test verifying the skill level read for a high-demand breakdown and Dan tier
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
    assert jack_result.broke_down is True
    # the player's level is strictly lower than what the chart asks
    assert jack_result.effective_capacity < jack_result.chart_level
    assert jack_result.star_rating > 0.0
    assert jack_result.dan_tier != ""
