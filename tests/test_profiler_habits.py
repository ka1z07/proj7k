"""Technique habits read from replay key timing, merged across plays (ADR-0029)."""

import json
import random

import pytest

from proj7k.parser import Beatmap7K, HitObject, NoteType
from proj7k.profiler.habits import (
    HABITS_VERSION,
    HabitProfile,
    HabitStats,
    aggregate_habits,
    extract_play_habits,
    format_habit_profile,
    hist_quantile,
)
from proj7k.profiler.matcher import align_replay_hits
from proj7k.profiler.osr import ReplayFrame
from proj7k.profiler.storage import MatchSnapshot, ProfilerStorage


def _frames(events):
    """Replay frames from (column, down_ms, up_ms) key presses."""
    edges = []
    for col, down, up in events:
        edges.append((round(down), col, True))
        edges.append((round(up), col, False))
    edges.sort(key=lambda e: (e[0], not e[2]))
    keys = 0
    frames = []
    for t, col, down in edges:
        keys = keys | (1 << col) if down else keys & ~(1 << col)
        frames.append(ReplayFrame(time_ms=float(t), keys=keys))
    return frames


def _play(notes, presses, clock_rate=1.0):
    """Habit statistics of one play: `notes` as HitObjects, `presses` as (column, down, up) in song time."""
    beatmap = Beatmap7K(hit_objects=sorted(notes, key=lambda h: (h.time, h.column)))
    frames = _frames(presses)
    alignment = align_replay_hits(beatmap, frames, clock_rate=clock_rate)
    return extract_play_habits(alignment, frames, clock_rate)


def _profile(stats):
    return HabitProfile("p", "all_time", None, stats.plays, 0, stats).to_dict()


def test_column_offset_and_rice_hold():
    notes = [HitObject(column=0, time=1000 + 300 * i, note_type=NoteType.RICE) for i in range(20)]
    presses = [(0, n.time + 5, n.time + 85) for n in notes]
    col = _profile(_play(notes, presses))["columns"][0]
    assert col["hits"] == 20
    assert col["mean_offset_ms"] == pytest.approx(5.0)
    assert col["sd_offset_ms"] == pytest.approx(0.0)
    assert 80 <= col["hold_median_ms"] <= 84


def test_offsets_are_read_in_real_time_under_a_rate_mod():
    notes = [HitObject(column=2, time=1000 + 400 * i, note_type=NoteType.RICE) for i in range(10)]
    presses = [(2, n.time + 10, n.time + 160) for n in notes]
    col = _profile(_play(notes, presses, clock_rate=2.0))["columns"][2]
    assert col["mean_offset_ms"] == pytest.approx(5.0)
    assert 72 <= col["hold_median_ms"] <= 80


def _chord_play(middle_follows, seed=1, chords=300):
    """Chords on every column; each hand errs together, the middle key with `middle_follows` ('L' or 'R')."""
    rnd = random.Random(seed)
    notes, presses = [], []
    for i in range(chords):
        t = 1000 + 250 * i
        err_l, err_r = rnd.gauss(0, 10), rnd.gauss(0, 10)
        for c in range(7):
            hand = "L" if c < 3 else "R" if c > 3 else middle_follows
            lead = {0: -6, 1: 0, 2: 2}.get(c, 0)  # the left hand goes down outer key first
            down = t + (err_l if hand == "L" else err_r) + lead + rnd.gauss(0, 2)
            notes.append(HitObject(column=c, time=t, note_type=NoteType.RICE))
            presses.append((c, down, down + 90))
    return _play(notes, presses)


@pytest.mark.parametrize("middle", ["L", "R"])
def test_middle_key_is_assigned_to_the_hand_it_errs_with(middle):
    prof = _profile(_chord_play(middle))
    hands = prof["hands"]
    assert hands["middle_hand"] == middle
    assert hands["hands_separable"]
    assert hands["within_hand_corr"] > hands["cross_hand_corr"] + 0.3
    lead = {r["lane"]: r["first_rate"] for r in prof["hand_lead"]["L"]}
    assert lead["L3"] > 0.9 > lead["L2"]
    assert ("S" in lead) == (middle == "L")


def test_middle_key_undetermined_when_it_follows_neither_hand():
    rnd = random.Random(3)
    notes, presses = [], []
    for i in range(300):
        t = 1000 + 250 * i
        errs = [rnd.gauss(0, 10) for _ in range(7)]
        for c in range(7):
            notes.append(HitObject(column=c, time=t, note_type=NoteType.RICE))
            presses.append((c, t + errs[c], t + errs[c] + 90))
    hands = _profile(_play(notes, presses))["hands"]
    assert hands["middle_hand"] is None
    assert not hands["hands_separable"]


def test_rolling_overlap_and_threshold():
    notes, presses = [], []
    t = 1000
    for gap in (50, 50, 50, 170, 170, 170):
        for i in range(60):
            c = i % 2
            notes.append(HitObject(column=c, time=t, note_type=NoteType.RICE))
            presses.append((c, t, t + 90))  # a 90 ms press: held into the next note under 90 ms
            t += gap
        t += 1000
    prof = _profile(_play(notes, presses))
    rates = {b["lo"]: b["overlap_rate"] for b in prof["roll"]}
    assert rates[40] == 1.0 and rates[160] == 0.0
    assert prof["roll_threshold_ms"] is None or 50 <= prof["roll_threshold_ms"] <= 180


def test_jack_compression():
    notes, presses = [], []
    for i in range(40):
        t = 1000 + 1000 * i
        notes += [HitObject(column=4, time=t, note_type=NoteType.RICE), HitObject(column=4, time=t + 80, note_type=NoteType.RICE)]
        presses += [(4, t - 10, t + 40), (4, t + 100, t + 150)]
    jack = {b["lo"]: b for b in _profile(_play(notes, presses))["jack"]}
    assert jack[60]["pairs"] == 40
    assert jack[60]["first_offset_ms"] == pytest.approx(-10.0)
    assert jack[60]["second_offset_ms"] == pytest.approx(20.0)


def test_ln_release_early_and_late():
    notes, presses = [], []
    for i, tail_err in enumerate([-80, -80, 0, 0, 0, 0, 0, 0, 70, 70]):
        t = 1000 + 1000 * i
        notes.append(HitObject(column=6, time=t, note_type=NoteType.LN, end_time=t + 500))
        presses.append((6, t, t + 500 + tail_err))
    col = _profile(_play(notes, presses))["columns"][6]
    assert col["ln_tails"] == 10
    assert col["ln_early_rate"] == pytest.approx(0.2)
    assert col["ln_late_rate"] == pytest.approx(0.2)
    assert col["ln_tail_mean_ms"] == pytest.approx(-2.0)


def test_stats_round_trip_and_merge_by_adding():
    a, b = _chord_play("R", seed=1, chords=100), _chord_play("R", seed=2, chords=100)
    a2 = HabitStats.from_dict(json.loads(json.dumps(a.to_dict())))
    a2.merge(HabitStats.from_dict(json.loads(json.dumps(b.to_dict()))))
    assert a2.plays == 2
    assert a2.columns[0]["n"] == a.columns[0]["n"] + b.columns[0]["n"]
    assert sum(a2.pairs["0-1"]["diff"]) == a.pairs["0-1"]["n"] + b.pairs["0-1"]["n"]
    assert HabitStats.from_dict({**a.to_dict(), "v": HABITS_VERSION + 1}) is None
    assert HabitStats.from_dict(None) is None


def test_hist_quantile():
    assert hist_quantile([0, 0, 3, 1], 0.5) == 2.0
    assert hist_quantile([0, 2, 0], 0.5, 4) == 6.0
    assert hist_quantile([0, 0], 0.5) is None


def _snapshot(ts, habits, h):
    summary = {"ingest": 4}
    if habits is not None:
        summary["habits"] = habits.to_dict()
    return MatchSnapshot(player_name="p", timestamp=ts, beatmap_hash="b", replay_hash=h, play_duration_s=60,
                         completion_rate=1.0, is_valid_play=True, is_failed=False, overall_ur=100.0,
                         capacities={}, summary=summary)


def test_aggregate_merges_stored_plays_and_counts_old_snapshots(tmp_path):
    with ProfilerStorage(db_path=tmp_path / "p.db") as storage:
        storage.save_snapshot(_snapshot(1000.0, _chord_play("R", seed=1, chords=100), "r1"))
        storage.save_snapshot(_snapshot(1000.0 + 86400 * 40, _chord_play("R", seed=2, chords=100), "r2"))
        storage.save_snapshot(_snapshot(1000.0 + 86400 * 41, None, "r3"))
        everything = aggregate_habits(storage, "p")
        recent = aggregate_habits(storage, "p", horizon_days=30, reference_timestamp=1000.0 + 86400 * 41)
    assert everything.plays == 2 and everything.stale_plays == 1
    assert recent.plays == 1 and recent.stale_plays == 1
    prof = everything.to_dict()
    assert prof["hands"]["middle_hand"] == "R"
    assert "Middle key: right hand" in format_habit_profile(prof)


def test_empty_profile_renders():
    prof = _profile(HabitStats())
    assert prof["plays"] == 0 and prof["notes"] == 0
    assert prof["hands"]["middle_hand"] is None
    format_habit_profile(prof)


def test_cli_habits_mode(tmp_path, capsys):
    from proj7k.profiler.cli import main

    db = tmp_path / "p.db"
    with ProfilerStorage(db_path=db) as storage:
        storage.save_snapshot(_snapshot(1000.0, _chord_play("R", seed=1, chords=100), "r1"))
    assert main(["--player", "p", "--habits", "--all-time", "--db", str(db), "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["plays"] == 1 and out["hands"]["middle_hand"] == "R"
