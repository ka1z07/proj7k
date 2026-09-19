import numpy as np
import pytest

from proj7k.parser import Beatmap7K, HitObject, NoteType
from proj7k.profiler.matcher import AlignedHit, HitAlignmentResult, HitJudgment
from proj7k.profiler.pathology import analyze_pathology


def test_track_timing_error_and_bimanual_asymmetry():
    # Left hand: Col 0 (L3), Col 1 (L2), Col 2 (L1)
    # Right hand: Col 4 (R1), Col 5 (R2), Col 6 (R3)
    # Space / Centre: Col 3 (S)

    # Let Left hand have tight hits: offset = +2ms
    # Let Right hand have late hits with higher variance: offset = +20ms with +/-10ms
    hits = [
        # Left hand hits (cols 0, 1, 2)
        AlignedHit(column=0, target_time=1000, hit_time=1002, offset_ms=2.0, judgment=HitJudgment.MAX),
        AlignedHit(column=0, target_time=1200, hit_time=1202, offset_ms=2.0, judgment=HitJudgment.MAX),
        AlignedHit(column=1, target_time=1400, hit_time=1402, offset_ms=2.0, judgment=HitJudgment.MAX),
        AlignedHit(column=2, target_time=1600, hit_time=1602, offset_ms=2.0, judgment=HitJudgment.MAX),

        # Right hand hits (cols 4, 5, 6)
        AlignedHit(column=4, target_time=2000, hit_time=2010, offset_ms=10.0, judgment=HitJudgment.PERFECT),
        AlignedHit(column=4, target_time=2200, hit_time=2230, offset_ms=30.0, judgment=HitJudgment.GREAT),
        AlignedHit(column=5, target_time=2400, hit_time=2420, offset_ms=20.0, judgment=HitJudgment.PERFECT),
        AlignedHit(column=6, target_time=2600, hit_time=2620, offset_ms=20.0, judgment=HitJudgment.PERFECT),
    ]

    alignment = HitAlignmentResult(
        aligned_hits=hits,
        total_hits=len(hits),
        miss_count=0,
    )

    beatmap = Beatmap7K(
        hit_objects=[
            HitObject(time=h.target_time, column=h.column, note_type=NoteType.RICE)
            for h in hits
        ],
        circle_size=7,
        overall_difficulty=8.0,
    )

    report = analyze_pathology(alignment, beatmap)

    # Verify per-track pathology
    assert "L3" in report.tracks
    assert "R1" in report.tracks
    l3_track = report.tracks["L3"]
    assert l3_track.hit_count == 2
    assert pytest.approx(l3_track.mean_error_ms, abs=1e-2) == 2.0
    assert pytest.approx(l3_track.ur, abs=1e-2) == 0.0  # 0 std dev * 10

    r1_track = report.tracks["R1"]
    assert r1_track.hit_count == 2
    assert pytest.approx(r1_track.mean_error_ms, abs=1e-2) == 20.0
    assert r1_track.ur > 0.0

    # Verify bimanual asymmetry
    bimanual = report.bimanual
    assert bimanual.left_hit_count == 4
    assert bimanual.right_hit_count == 4
    assert pytest.approx(bimanual.left_mean_error_ms, abs=1e-2) == 2.0
    assert pytest.approx(bimanual.right_mean_error_ms, abs=1e-2) == 20.0
    assert bimanual.left_ur == 0.0
    assert bimanual.right_ur > 0.0
    assert bimanual.asymmetry_ratio > 1.0  # Right hand has significantly higher UR / error


def test_jack_stagnation_drift_exhaustion_detection():
    # Simulate a player hitting a continuous 160 BPM Jack on Col 3 (S)
    # 160 BPM 16th is ~93.75ms, 8th is ~187.5ms.
    # Let interval = 150ms.
    # 5 consecutive notes on col 3: delta t = 150ms.
    # The player suffers tendon fatigue: offsets drift from 0ms to 30ms (+50ms/s slope).
    t_start = 1000.0
    offsets = [0.0, 7.5, 15.0, 22.5, 30.0]
    dt = 150.0

    hits = []
    for i, off in enumerate(offsets):
        target = t_start + i * dt
        hits.append(
            AlignedHit(
                column=3,
                target_time=target,
                hit_time=target + off,
                offset_ms=off,
                judgment=HitJudgment.MAX if abs(off) <= 16 else HitJudgment.PERFECT,
            )
        )

    alignment = HitAlignmentResult(
        aligned_hits=hits,
        total_hits=len(hits),
        miss_count=0,
    )

    beatmap = Beatmap7K(
        hit_objects=[
            HitObject(time=h.target_time, column=h.column, note_type=NoteType.RICE)
            for h in hits
        ],
        circle_size=7,
        overall_difficulty=8.0,
    )

    report = analyze_pathology(alignment, beatmap)

    assert report.jack_drift.stagnation_jack_count == 5
    # Total elapsed time = 4 * 0.15s = 0.6s.
    # Drift = 30ms / 0.6s = 50 ms/s.
    assert pytest.approx(report.jack_drift.slope_ms_per_s, abs=1.0) == 50.0
    assert report.jack_drift.r_squared > 0.95
    assert report.jack_drift.fatigue_alert is True


def test_ln_release_decoupling_and_stickiness():
    # 4 LNs on Col 0, 1, 2, 4
    # LN 1: Perfect release (+0ms)
    # LN 2: Panic release: released 60ms too early (-60ms)
    # LN 3: Sticky hold: released 80ms too late (+80ms)
    # LN 4: Sticky hold: released 100ms too late (+100ms)
    hits = [
        AlignedHit(
            column=0,
            target_time=1000,
            hit_time=1000,
            offset_ms=0.0,
            judgment=HitJudgment.MAX,
            note_type=NoteType.LN,
            end_time=2000,
            tail_release_time=2000,
            tail_offset_ms=0.0,
        ),
        AlignedHit(
            column=1,
            target_time=2500,
            hit_time=2500,
            offset_ms=0.0,
            judgment=HitJudgment.MAX,
            note_type=NoteType.LN,
            end_time=3500,
            tail_release_time=3440,
            tail_offset_ms=-60.0,  # Panic early release
        ),
        AlignedHit(
            column=2,
            target_time=4000,
            hit_time=4000,
            offset_ms=0.0,
            judgment=HitJudgment.MAX,
            note_type=NoteType.LN,
            end_time=5000,
            tail_release_time=5080,
            tail_offset_ms=80.0,  # Sticky late release
        ),
        AlignedHit(
            column=4,
            target_time=5500,
            hit_time=5500,
            offset_ms=0.0,
            judgment=HitJudgment.MAX,
            note_type=NoteType.LN,
            end_time=6500,
            tail_release_time=6600,
            tail_offset_ms=100.0,  # Sticky late release
        ),
    ]

    alignment = HitAlignmentResult(
        aligned_hits=hits,
        total_hits=len(hits),
        miss_count=0,
    )

    beatmap = Beatmap7K(
        hit_objects=[
            HitObject(time=h.target_time, column=h.column, note_type=NoteType.LN, end_time=h.end_time)
            for h in hits
        ],
        circle_size=7,
        overall_difficulty=8.0,
    )

    report = analyze_pathology(alignment, beatmap)

    ln_rep = report.ln_release
    assert ln_rep.total_lns == 4
    assert ln_rep.evaluated_releases == 4
    assert ln_rep.panic_release_count == 1
    assert pytest.approx(ln_rep.panic_release_rate, abs=1e-2) == 0.25
    assert ln_rep.sticky_count == 2
    assert pytest.approx(ln_rep.sticky_rate, abs=1e-2) == 0.50
    # Offsets: [0, -60, 80, 100] -> mean = 30.0ms
    assert pytest.approx(ln_rep.mean_tail_offset_ms, abs=1e-2) == 30.0
    assert ln_rep.tail_ur > 0.0


def test_cascade_failure_precursor_detection():
    # Build a sequence where the player plays well, but encounters a fast bracket pattern
    # in [1500, 2000]ms (500ms before t=2000), causing a fatal MISS at t=2000ms on Col 1.
    # Left hand bracket: Col 0 & Col 2 (gap 1) followed by Col 1.
    notes = [
        HitObject(time=1000, column=0, note_type=NoteType.RICE),
        HitObject(time=1200, column=4, note_type=NoteType.RICE),
        # Precursor window [1500, 2000]
        HitObject(time=1600, column=0, note_type=NoteType.RICE),
        HitObject(time=1600, column=2, note_type=NoteType.RICE),  # Outer bracket [gap:1]
        HitObject(time=1800, column=1, note_type=NoteType.RICE),  # Inner finger [mid]
        # Fatal break at t=2000 on Col 1
        HitObject(time=2000, column=1, note_type=NoteType.RICE),
    ]

    hits = [
        AlignedHit(column=0, target_time=1000, hit_time=1000, offset_ms=0.0, judgment=HitJudgment.MAX),
        AlignedHit(column=4, target_time=1200, hit_time=1200, offset_ms=0.0, judgment=HitJudgment.MAX),
        AlignedHit(column=0, target_time=1600, hit_time=1600, offset_ms=0.0, judgment=HitJudgment.MAX),
        AlignedHit(column=2, target_time=1600, hit_time=1600, offset_ms=0.0, judgment=HitJudgment.MAX),
        AlignedHit(column=1, target_time=1800, hit_time=1800, offset_ms=0.0, judgment=HitJudgment.MAX),
        AlignedHit(column=1, target_time=2000, hit_time=None, offset_ms=None, judgment=HitJudgment.MISS),
    ]

    alignment = HitAlignmentResult(
        aligned_hits=hits,
        total_hits=len(hits),
        miss_count=1,
    )

    beatmap = Beatmap7K(
        hit_objects=notes,
        circle_size=7,
        overall_difficulty=8.0,
    )

    report = analyze_pathology(alignment, beatmap)

    assert report.cascade_precursor is not None
    precursor = report.cascade_precursor
    assert precursor.fatal_time_ms == 2000.0
    assert precursor.fatal_column == 1
    assert precursor.precursor_start_ms == 1500.0
    assert precursor.precursor_note_count >= 3  # notes at 1600, 1600, 1800, 2000
    assert precursor.has_bracket_inversion is True
    assert precursor.dominant_technique == "bracket"


def test_cli_pathology_stdout_and_json(tmp_path):
    import json
    from proj7k.profiler.cli import main, run_profiler
    from proj7k.profiler.osr import OSRReplay, ReplayFrame, serialize_osr

    # Create a synthetic map with bracket and LN
    osu_content = """osu file format v14
[General]
Mode: 3
[Difficulty]
CircleSize: 7
OverallDifficulty: 8
[HitObjects]
36,192,1000,1,0,0:0:0:0:
182,192,1000,128,0,2000:0:0:0:0:
109,192,1200,1,0,0:0:0:0:
36,192,1400,1,0,0:0:0:0:
109,192,1600,1,0,0:0:0:0:
"""
    osu_file = tmp_path / "pathology_map.osu"
    osu_file.write_text(osu_content, encoding="utf-8")

    # Replay:
    # 1. Col 0 tap at 1000ms (+0ms)
    # 2. Col 2 LN at 1000ms (+0ms), released late at 2080ms (+80ms sticky)
    # 3. Col 1 tap at 1200ms (+0ms)
    # 4. Col 0 tap at 1400ms (+0ms)
    # 5. Col 1 fatal MISS at 1600ms
    frames = [
        ReplayFrame(time_ms=0.0, keys=0),
        ReplayFrame(time_ms=1000.0, keys=5),    # Col 0 (1) + Col 2 (4) = 5
        ReplayFrame(time_ms=1050.0, keys=4),    # Col 0 released, Col 2 held
        ReplayFrame(time_ms=1200.0, keys=6),    # Col 1 pressed
        ReplayFrame(time_ms=1250.0, keys=4),    # Col 1 released
        ReplayFrame(time_ms=1400.0, keys=5),    # Col 0 pressed
        ReplayFrame(time_ms=1450.0, keys=4),    # Col 0 released
        ReplayFrame(time_ms=2080.0, keys=0),    # Col 2 released (+80ms sticky)
    ]

    replay = OSRReplay(
        mode=3,
        game_version=20240101,
        player_name="PathologyPlayer",
        action_frames=frames,
    )
    osr_file = tmp_path / "pathology.osr"
    osr_file.write_bytes(serialize_osr(replay))

    # Test run_profiler
    report = run_profiler(osr_file, osu_file)
    assert report.pathology is not None
    assert report.pathology.ln_release.sticky_count == 1
    assert report.pathology.cascade_precursor is not None
    assert report.pathology.cascade_precursor.fatal_time_ms == 1600.0

    # Test CLI human-readable stdout
    from pytest import CaptureFixture
    import sys
    from io import StringIO

    saved_stdout = sys.stdout
    out_buf = StringIO()
    sys.stdout = out_buf
    try:
        code = main(["--replay", str(osr_file), "--beatmap", str(osu_file)])
    finally:
        sys.stdout = saved_stdout

    assert code == 0
    out_str = out_buf.getvalue()
    assert "Micro-Pathology Diagnostics" in out_str
    assert "Bimanual Load" in out_str
    assert "LN Release Decoupling" in out_str
    assert "Cascade Failure Precursor" in out_str

    # Test CLI --json
    out_buf_json = StringIO()
    sys.stdout = out_buf_json
    try:
        code_json = main(["--replay", str(osr_file), "--beatmap", str(osu_file), "--json"])
    finally:
        sys.stdout = saved_stdout

    assert code_json == 0
    json_data = json.loads(out_buf_json.getvalue())
    assert "pathology" in json_data
    assert "bimanual" in json_data["pathology"]
    assert "ln_release" in json_data["pathology"]
    assert json_data["pathology"]["ln_release"]["sticky_count"] == 1
    assert "cascade_precursor" in json_data["pathology"]
    assert json_data["pathology"]["cascade_precursor"]["fatal_time_ms"] == 1600.0


def test_end_to_end_replay_jack_drift_and_asymmetry(tmp_path):
    from proj7k.profiler.cli import run_profiler
    from proj7k.profiler.osr import OSRReplay, ReplayFrame, serialize_osr

    # Beatmap with:
    # 1. Jack on Col 3 (S): 5 notes at 1000, 1150, 1300, 1450, 1600 (interval 150ms <= 220ms)
    # 2. Left hand notes: 4 notes on Col 0
    # 3. Right hand notes: 2 notes on Col 4
    osu_lines = [
        "osu file format v14",
        "[General]",
        "Mode: 3",
        "[Difficulty]",
        "CircleSize: 7",
        "OverallDifficulty: 8",
        "[HitObjects]",
        # Col 0 (left isolated stream notes > 220ms apart):
        "36,192,200,1,0,0:0:0:0:",
        "36,192,500,1,0,0:0:0:0:",
        "36,192,800,1,0,0:0:0:0:",
        "36,192,2400,1,0,0:0:0:0:",
        # Col 3 (jack):
        "256,192,1000,1,0,0:0:0:0:",
        "256,192,1150,1,0,0:0:0:0:",
        "256,192,1300,1,0,0:0:0:0:",
        "256,192,1450,1,0,0:0:0:0:",
        "256,192,1600,1,0,0:0:0:0:",
        # Col 4 (right isolated stream notes > 220ms apart):
        "329,192,2000,1,0,0:0:0:0:",
        "329,192,2800,1,0,0:0:0:0:",
    ]
    osu_file = tmp_path / "drift_map.osu"
    osu_file.write_text("\n".join(osu_lines), encoding="utf-8")

    # Replay:
    # Left hand hits with 0ms offset
    # Jack hits drift from 0ms to +30ms
    frames = [
        ReplayFrame(time_ms=0.0, keys=0),
        # Left hits
        ReplayFrame(time_ms=200.0, keys=1), ReplayFrame(time_ms=230.0, keys=0),
        ReplayFrame(time_ms=500.0, keys=1), ReplayFrame(time_ms=530.0, keys=0),
        ReplayFrame(time_ms=800.0, keys=1), ReplayFrame(time_ms=830.0, keys=0),
        ReplayFrame(time_ms=2400.0, keys=1), ReplayFrame(time_ms=2430.0, keys=0),
        # Jack hits on Col 3 (key bit = 8)
        ReplayFrame(time_ms=1000.0, keys=8), ReplayFrame(time_ms=1030.0, keys=0),
        ReplayFrame(time_ms=1157.5, keys=8), ReplayFrame(time_ms=1180.0, keys=0),
        ReplayFrame(time_ms=1315.0, keys=8), ReplayFrame(time_ms=1340.0, keys=0),
        ReplayFrame(time_ms=1472.5, keys=8), ReplayFrame(time_ms=1500.0, keys=0),
        ReplayFrame(time_ms=1630.0, keys=8), ReplayFrame(time_ms=1650.0, keys=0),
        # Right hits on Col 4 (key bit = 16)
        ReplayFrame(time_ms=2000.0, keys=16), ReplayFrame(time_ms=2030.0, keys=0),
        ReplayFrame(time_ms=2800.0, keys=16), ReplayFrame(time_ms=2830.0, keys=0),
    ]


    replay = OSRReplay(
        mode=3,
        game_version=20240101,
        player_name="DriftPlayer",
        action_frames=frames,
    )
    osr_file = tmp_path / "drift.osr"
    osr_file.write_bytes(serialize_osr(replay))

    report = run_profiler(osr_file, osu_file)
    path = report.pathology
    assert path is not None

    # Verify Bimanual Load Asymmetry: Left has 4, Right has 2 -> ratio = 2.0
    assert path.bimanual.left_hit_count == 4
    assert path.bimanual.right_hit_count == 2
    assert pytest.approx(path.bimanual.load_asymmetry_ratio, abs=1e-2) == 2.0

    # Verify Jack Drift
    assert path.jack_drift.stagnation_jack_count == 5
    assert pytest.approx(path.jack_drift.slope_ms_per_s, abs=1.0) == 50.0
    assert path.jack_drift.fatigue_alert is True





