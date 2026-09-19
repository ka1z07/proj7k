import pytest
from proj7k.parser import Beatmap7K, HitObject, NoteType
from proj7k.profiler.osr import ReplayFrame
from proj7k.profiler.matcher import (
    GhostTapReason,
    HitJudgment,
    align_replay_hits,
    compute_mania_hit_windows,
)


@pytest.fixture
def simple_beatmap():
    # OD = 8.0
    # Hit windows at OD 8:
    # MAX: 16ms, 300: 40ms, 200: 73ms, 100: 103ms, 50: 127ms, Miss: 164ms
    objects = [
        HitObject(column=0, time=1000.0, note_type=NoteType.RICE),
        HitObject(column=0, time=2000.0, note_type=NoteType.RICE),
        HitObject(column=1, time=1500.0, note_type=NoteType.LN, end_time=2500.0),
        # Column 2 has no objects (empty track)
    ]
    return Beatmap7K(
        title="Matcher Test",
        overall_difficulty=8.0,
        hit_objects=objects,
    )


def test_hit_windows_calculation():
    w = compute_mania_hit_windows(od=8.0)
    assert w.w_max == 16.0
    assert w.w_300 == 40.0
    assert w.w_200 == 73.0
    assert w.w_100 == 103.0
    assert w.w_50 == 127.0
    assert w.w_miss == 164.0


def test_causal_matching_clean_hits_and_ln(simple_beatmap):
    # Perfect press at 1000ms for note 0 (col 0)
    # Slightly early press at 1985ms for note 1 (col 0, -15ms -> MAX)
    # Perfect press at 1500ms for LN (col 1), release at 2505ms (+5ms)
    frames = [
        ReplayFrame(time_ms=0.0, keys=0),
        ReplayFrame(time_ms=1000.0, keys=1),    # Col 0 press
        ReplayFrame(time_ms=1060.0, keys=0),    # Col 0 release
        ReplayFrame(time_ms=1500.0, keys=2),    # Col 1 press (LN)
        ReplayFrame(time_ms=1985.0, keys=3),    # Col 0 press (-15ms) while Col 1 still held
        ReplayFrame(time_ms=2050.0, keys=2),    # Col 0 release
        ReplayFrame(time_ms=2505.0, keys=0),    # Col 1 release (LN tail +5ms)
    ]

    result = align_replay_hits(simple_beatmap, frames)

    assert result.total_hits == 3
    assert result.miss_count == 0
    assert result.total_ghost_taps == 0

    col0_hits = [h for h in result.aligned_hits if h.column == 0]
    assert len(col0_hits) == 2
    assert col0_hits[0].judgment == HitJudgment.MAX
    assert col0_hits[0].offset_ms == 0.0

    assert col0_hits[1].judgment == HitJudgment.MAX
    assert col0_hits[1].offset_ms == -15.0

    col1_hits = [h for h in result.aligned_hits if h.column == 1]
    assert len(col1_hits) == 1
    assert col1_hits[0].judgment == HitJudgment.MAX
    assert col1_hits[0].tail_offset_ms == 5.0


def test_causal_matching_miss_and_panic_ghost_taps(simple_beatmap):
    # Scenario:
    # 1. Note at 1000ms is missed (no tap around 1000ms)
    # 2. Panic ghost tap at 1250ms (outside note 0 window [836..1164] and note 1 window [1836..2164])
    # 3. Note at 2000ms hit late at 2050ms (+50ms -> GREAT)
    # 4. Empty track tap on Col 2 at 1600ms (Ghost tap on empty track)
    frames = [
        ReplayFrame(time_ms=0.0, keys=0),
        ReplayFrame(time_ms=1250.0, keys=1),   # Col 0 panic tap (ghost tap)
        ReplayFrame(time_ms=1300.0, keys=0),
        ReplayFrame(time_ms=1600.0, keys=4),   # Col 2 tap (empty track -> ghost tap)
        ReplayFrame(time_ms=1650.0, keys=0),
        ReplayFrame(time_ms=2050.0, keys=1),   # Col 0 late hit (+50ms)
        ReplayFrame(time_ms=2100.0, keys=0),
    ]

    result = align_replay_hits(simple_beatmap, frames)

    # Note 0 at 1000ms is MISS
    # Note 1 at 2000ms is GREAT (+50ms)
    # LN at 1500ms on Col 1 is MISS
    assert result.judgment_counts[HitJudgment.MISS] == 2
    assert result.judgment_counts[HitJudgment.GREAT] == 1

    # Ghost taps:
    # 1 on Col 0 (at 1250ms, out of window)
    # 1 on Col 2 (at 1600ms, empty track)
    assert result.total_ghost_taps == 2
    assert result.ghost_taps_by_column[0] == 1
    assert result.ghost_taps_by_column[2] == 1
    assert result.ghost_taps[0].reason == GhostTapReason.OUT_OF_WINDOW
    assert result.ghost_taps[1].reason == GhostTapReason.EMPTY_TRACK


def test_clock_rate_scaling(simple_beatmap):
    # At OD 8, 1.0x rate: W_300 = 40ms
    # At 1.5x DT rate: W_300 = 40 / 1.5 = 26.67ms
    # A hit at +30ms would be PERFECT (300) on 1.0x, but GREAT (200) on 1.5x DT
    frames = [
        ReplayFrame(time_ms=0.0, keys=0),
        ReplayFrame(time_ms=1030.0, keys=1),  # +30ms
        ReplayFrame(time_ms=1080.0, keys=0),
    ]

    res_1x = align_replay_hits(simple_beatmap, frames, clock_rate=1.0)
    hit_1x = [h for h in res_1x.aligned_hits if h.column == 0 and h.hit_time is not None][0]
    assert hit_1x.judgment == HitJudgment.PERFECT

    res_dt = align_replay_hits(simple_beatmap, frames, clock_rate=1.5)
    hit_dt = [h for h in res_dt.aligned_hits if h.column == 0 and h.hit_time is not None][0]
    assert hit_dt.judgment == HitJudgment.GREAT

