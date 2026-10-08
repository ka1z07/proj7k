import math
import pytest
from typing import List

from proj7k.parser import (
    Beatmap7K,
    HitObject,
    NoteType,
    TimingPoint,
    parse_osu_7k,
    dump_osu_7k,
)
from proj7k.downscaler.mapper import TwoTierDanMapper, DanTarget
from proj7k.downscaler.validator import DualGateValidator, longest_new_silence
from proj7k.downscaler.pruner import ExcessLossPruner, PruningResult
from proj7k.downscaler.pipeline import downscale_beatmap, DownscaleOptions, DownscaleResult
from proj7k.field import trace_beatmap


def _build_dense_chordjack_beatmap(bpm: float = 290.0, measures: int = 16) -> Beatmap7K:
    """
    Constructs a high-difficulty 7K Chordjack beatmap (3-chords and 2-chords on 8th notes).
    At BPM 290, P90 strain ~ 156 (9th/10th Dan level).
    """
    beat_length = 60000.0 / bpm
    step_ms = beat_length / 2.0  # 8th notes

    hit_objects: List[HitObject] = []
    total_steps = measures * 8

    for step in range(total_steps):
        t = step * step_ms
        # Alternate dense chord patterns with same-column jacks
        pattern = step % 4
        if pattern == 0:
            cols = [0, 2, 4]
        elif pattern == 1:
            cols = [0, 3, 5]
        elif pattern == 2:
            cols = [1, 3, 6]
        else:
            cols = [1, 4, 6]

        for c in cols:
            hit_objects.append(HitObject(column=c, time=t, note_type=NoteType.RICE))

    return Beatmap7K(
        title="High Diff Chordjack",
        artist="Test Artist",
        creator="Tester",
        version="Extra",
        hit_objects=hit_objects,
        timing_points=[
            TimingPoint(time=0.0, beat_length=beat_length, meter=4, uninherited=True)
        ],
    )


def _build_dense_stream_beatmap(bpm: float = 280.0, measures: int = 16) -> Beatmap7K:
    """
    Constructs a high-difficulty 7K Stream beatmap (rapid single 16th notes sweeping lanes).
    At BPM 280, P90 strain ~ 91.8.
    """
    beat_length = 60000.0 / bpm
    step_ms = beat_length / 4.0  # 16th notes

    hit_objects: List[HitObject] = []
    total_steps = measures * 16

    # Flow sweep across 7 columns: 0, 1, 2, 3, 4, 5, 6, 5, 4, 3, 2, 1, ...
    lane_seq = [0, 1, 2, 3, 4, 5, 6, 5, 4, 3, 2, 1]
    for step in range(total_steps):
        t = step * step_ms
        col = lane_seq[step % len(lane_seq)]
        hit_objects.append(HitObject(column=col, time=t, note_type=NoteType.RICE))

    return Beatmap7K(
        title="High Diff Stream",
        artist="Test Artist",
        creator="Tester",
        version="Extra",
        hit_objects=hit_objects,
        timing_points=[
            TimingPoint(time=0.0, beat_length=beat_length, meter=4, uninherited=True)
        ],
    )


def _stars(bm: Beatmap7K) -> float:
    return trace_beatmap(bm).profile.total_stars


def test_pruner_already_below_target_noop():
    bm = _build_dense_chordjack_beatmap(bpm=120.0, measures=4)

    res = ExcessLossPruner().prune(bm, target_sr=_stars(bm) + 2.0)

    assert isinstance(res, PruningResult)
    assert res.iterations_run == 0 and res.converged
    assert len(res.downscaled_beatmap.hit_objects) == len(bm.hit_objects)
    assert res.final_star == res.initial_star


def test_pruner_lands_on_the_target_star_within_tolerance():
    bm = _build_dense_chordjack_beatmap(bpm=290.0, measures=12)
    init = _stars(bm)
    target = init * 0.75

    pruner = ExcessLossPruner(prune_ratio=0.20, max_iterations=20, tolerance=0.05)
    res = pruner.prune(bm, target_sr=target)

    assert res.iterations_run > 0 and res.converged
    assert res.final_star < init
    # the engine's own star, read back from the pruned chart, is at the target: from above, within tolerance...
    assert _stars(res.downscaled_beatmap) == pytest.approx(res.final_star)
    assert res.final_star <= target * 1.05
    # ...and the batches were halved on overshoot rather than blowing through it
    assert res.final_star >= target * 0.85
    assert len(res.downscaled_beatmap.hit_objects) < len(bm.hit_objects)
    assert [r.removed_in_batch for r in res.history] and all(r.passed_validation for r in res.history)


def test_pruner_only_deletes_whole_notes_and_never_adds_any():
    bm = _build_dense_chordjack_beatmap(bpm=290.0, measures=8)
    res = ExcessLossPruner().prune(bm, target_sr=_stars(bm) * 0.8)

    original = {(h.column, round(h.time, 3)) for h in bm.hit_objects}
    kept = {(h.column, round(h.time, 3)) for h in res.downscaled_beatmap.hit_objects}
    assert kept < original
    assert res.total_notes_removed == len(original) - len(kept)


def test_end_to_end_downscale_chordjack_preservation():
    bm = _build_dense_chordjack_beatmap(bpm=290.0, measures=16)
    orig = trace_beatmap(bm).profile
    target = DownscaleOptions(target_sr=orig.total_stars * 0.8, target_dan=None, prune_ratio=0.18, max_iterations=20)

    result = downscale_beatmap(bm, target)

    assert isinstance(result, DownscaleResult)
    assert result.target.target_sr == pytest.approx(orig.total_stars * 0.8)
    # Dominant skill conserved, as the difficulty engine reads it (the gate's own reading)
    assert result.validation.dominant_conserved is True
    assert result.validation.cosine_similarity >= 0.80
    assert result.validation.passed is True
    # The star the loop steered by is the star of the practice chart
    assert result.downscaled_stars < result.original_stars
    assert result.downscaled_stars == pytest.approx(result.pruning_result.final_star)
    assert result.downscaled_stars <= result.target.target_sr * 1.05
    assert result.notes_removed > 0

    # Roundtrip .osu text check
    reparsed = parse_osu_7k(dump_osu_7k(result.downscaled_beatmap))
    assert len(reparsed.hit_objects) == len(result.downscaled_beatmap.hit_objects)
    assert "[P-" in reparsed.version
    assert "proj7k_downscaled" in reparsed.tags


def test_end_to_end_downscale_by_dan_tier():
    bm = _build_dense_chordjack_beatmap(bpm=290.0, measures=16)
    result = downscale_beatmap(bm, DownscaleOptions(target_dan="5th", max_iterations=20))

    assert result.target.target_dan == "5th"
    assert result.downscaled_stars <= result.target.target_sr * 1.05
    assert "[P-5th" in result.downscaled_beatmap.version


def test_end_to_end_downscale_stream_preservation():
    bm = _build_dense_stream_beatmap(bpm=280.0, measures=16)
    orig = trace_beatmap(bm).profile
    result = downscale_beatmap(bm, DownscaleOptions(target_sr=orig.total_stars * 0.8, target_dan=None, prune_ratio=0.18, max_iterations=20))

    assert result.downscaled_stars < result.original_stars
    assert result.validation.cosine_similarity >= 0.80
    assert result.validation.dominant_conserved is True
    assert result.notes_removed > 0


def test_downbeat_and_chord_invariants_preserved():
    bm = _build_dense_chordjack_beatmap(bpm=290.0, measures=10)
    opts = DownscaleOptions(target_dan="2nd", prune_ratio=0.25, max_iterations=20)
    result = downscale_beatmap(bm, opts)
    assert result.notes_removed > 0

    # Invariants verification:
    # 1. 1/1 measure downbeats must have at least 1 note
    measure_len = 60000.0 / 290.0 * 4.0
    for m in range(10):
        t_downbeat = m * measure_len
        notes_at_db = [
            ho for ho in result.downscaled_beatmap.hit_objects
            if abs(ho.time - t_downbeat) <= 3.0
        ]
        assert len(notes_at_db) >= 1, f"Measure {m} downbeat at {t_downbeat}ms was emptied!"


# --- free mode: the target difficulty without the dominant technique ------------------------------------------


def test_technique_mode_that_stops_above_the_target_suggests_free_mode_and_free_mode_reaches_it():
    bm = _build_dense_chordjack_beatmap(bpm=290.0, measures=12)
    target = _stars(bm) * 0.6

    kept = downscale_beatmap(bm, DownscaleOptions(target_sr=target, target_dan=None))
    # Keeping the chordjack, the loop runs out of deletions the dual gate allows well above the target...
    assert kept.mode == "technique" and kept.pruning_result.stopped_by_validation
    assert kept.downscaled_stars > target * 1.05
    assert kept.suggest_free_mode is True and kept.to_dict()["suggest_free_mode"] is True

    free = downscale_beatmap(bm, DownscaleOptions(target_sr=target, target_dan=None, preserve_technique=False))
    # ...and free mode, which lets the technique go, lands on it.
    assert free.mode == "free" and free.to_dict()["mode"] == "free"
    assert target * 0.85 <= free.downscaled_stars <= target * 1.05
    assert free.suggest_free_mode is False
    assert free.validation.passed is True and free.validation.details["preserve_technique"] is False
    # Free mode keeps no dominant skill, so the practice chart is not labelled with one.
    assert free.downscaled_beatmap.version.startswith("[P-") and "jack" not in free.downscaled_beatmap.version
    assert not any(t.startswith("dominant_") for t in free.downscaled_beatmap.tags.split())


def test_free_mode_still_keeps_the_chart_reasonable():
    bm = _build_dense_chordjack_beatmap(bpm=290.0, measures=12)
    free = downscale_beatmap(bm, DownscaleOptions(target_sr=_stars(bm) * 0.6, target_dan=None, preserve_technique=False))
    out = free.downscaled_beatmap

    # Pure deletion, every downbeat still sounded, hands in balance, no silence opened.
    original = {(h.column, round(h.time, 3)) for h in bm.hit_objects}
    assert {(h.column, round(h.time, 3)) for h in out.hit_objects} < original
    measure_len = 60000.0 / 290.0 * 4.0
    for m in range(12):
        assert any(abs(h.time - m * measure_len) <= 3.0 for h in out.hit_objects)
    left, right = free.bimanual_flux_ratio
    assert 0.40 <= left <= 0.60
    assert longest_new_silence(bm, out) == (0.0, 0.0)


def test_a_chart_already_at_the_target_suggests_nothing():
    bm = _build_dense_chordjack_beatmap(bpm=120.0, measures=4)
    res = downscale_beatmap(bm, DownscaleOptions(target_sr=_stars(bm) + 2.0, target_dan=None))
    assert res.suggest_free_mode is False


def _quarter_notes(measures: int, skip=()) -> Beatmap7K:
    beat = 500.0
    return Beatmap7K(
        title="t", artist="a", creator="c", version="v",
        hit_objects=[
            HitObject(column=b % 7, time=b * beat, note_type=NoteType.RICE)
            for b in range(measures * 4) if b not in skip
        ],
        timing_points=[TimingPoint(time=0.0, beat_length=beat, meter=4, uninherited=True)],
    )


def test_longest_new_silence_allows_a_measure_and_flags_more():
    original = _quarter_notes(6)
    assert longest_new_silence(original, original) == (0.0, 0.0)
    # Emptying one measure's worth of beats is allowed: the gap is the beat that was there plus a measure.
    assert longest_new_silence(original, _quarter_notes(6, skip=range(5, 9))) == (0.0, 0.0)
    # Two measures is not; the silence is reported where it opens.
    at, excess = longest_new_silence(original, _quarter_notes(6, skip=range(5, 13)))
    assert at == 2000.0 and excess == pytest.approx(2000.0)


def test_free_mode_validator_ignores_the_technique_but_not_a_new_silence():
    original = _quarter_notes(6)
    silenced = _quarter_notes(6, skip=range(5, 13))

    technique = DualGateValidator().validate(original, silenced)
    free = DualGateValidator(preserve_technique=False).validate(original, silenced)
    assert free.passed is False and "silence" in free.details["gate2_violations"][0]
    assert free.cosine_similarity == pytest.approx(technique.cosine_similarity)

    thinned = _quarter_notes(6, skip=range(1, 24, 2))
    assert DualGateValidator(preserve_technique=False).validate(original, thinned).passed is True
