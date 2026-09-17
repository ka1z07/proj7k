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
from proj7k.downscaler.validator import DualGateValidator
from proj7k.downscaler.pruner import WindowedPeakBatchPruner, PruningResult
from proj7k.downscaler.pipeline import downscale_beatmap, DownscaleOptions, DownscaleResult
from proj7k.strain import compute_dual_hand_strain
from proj7k.radar import compute_technique_radar


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


def test_pruner_already_below_target_noop():
    bm = _build_dense_chordjack_beatmap(bpm=120.0, measures=4)
    init_strain = compute_dual_hand_strain(bm)

    pruner = WindowedPeakBatchPruner()
    # Target strain higher than initial
    target_strain = init_strain.p90_strain + 50.0
    res = pruner.prune(bm, target_strain=target_strain)

    assert isinstance(res, PruningResult)
    assert res.iterations_run == 0
    assert len(res.downscaled_beatmap.hit_objects) == len(bm.hit_objects)


def test_pruner_converges_to_target_strain():
    bm = _build_dense_chordjack_beatmap(bpm=290.0, measures=12)
    init_strain = compute_dual_hand_strain(bm)

    # Set target to 75% of original P90 strain
    target_strain = init_strain.p90_strain * 0.75

    pruner = WindowedPeakBatchPruner(prune_ratio=0.20, max_iterations=20)
    res = pruner.prune(bm, target_strain=target_strain)

    assert res.iterations_run > 0
    assert res.final_p90_strain < init_strain.p90_strain
    # Final P90 strain should converge close to target
    assert res.final_p90_strain <= target_strain * 1.15
    # Notes were removed monotonically
    assert len(res.downscaled_beatmap.hit_objects) < len(bm.hit_objects)


def test_end_to_end_downscale_chordjack_preservation():
    # 9th/10th Dan Chordjack (~7.0★) downscaled to 7th Dan (~6.1★)
    bm = _build_dense_chordjack_beatmap(bpm=290.0, measures=16)
    orig_radar = compute_technique_radar(bm)
    assert orig_radar.dominant_technique == "jack"

    opts = DownscaleOptions(target_dan="7th", prune_ratio=0.18, max_iterations=20)
    result = downscale_beatmap(bm, opts)

    assert isinstance(result, DownscaleResult)
    assert result.target.target_dan == "7th"
    # Dominant technique strictly conserved!
    assert result.downscaled_radar.dominant_technique == "jack"
    assert result.validation.dominant_conserved is True
    # Cosine similarity >= 0.80
    assert result.validation.cosine_similarity >= 0.80
    # Both gates passed
    assert result.validation.passed is True

    # P90 strain and Star rating decreased
    assert result.downscaled_strain.p90_strain < result.original_strain.p90_strain
    assert result.downscaled_rating.star_rating < result.original_rating.star_rating
    assert result.notes_removed > 0

    # Roundtrip .osu text check
    dumped_osu = dump_osu_7k(result.downscaled_beatmap)
    reparsed = parse_osu_7k(dumped_osu)
    assert len(reparsed.hit_objects) == len(result.downscaled_beatmap.hit_objects)
    assert "[P-7th" in reparsed.version
    assert "proj7k_downscaled" in reparsed.tags


def test_end_to_end_downscale_stream_preservation():
    # Stream/Speed map (~5.1★) downscaled to 2nd Dan (~4.1★)
    bm = _build_dense_stream_beatmap(bpm=280.0, measures=16)
    orig_radar = compute_technique_radar(bm)
    assert orig_radar.dominant_technique in ("stream", "speed")

    opts = DownscaleOptions(target_dan="2nd", prune_ratio=0.18, max_iterations=20)
    result = downscale_beatmap(bm, opts)

    assert result.downscaled_strain.p90_strain < result.original_strain.p90_strain
    assert result.validation.cosine_similarity >= 0.80
    assert result.validation.dominant_conserved is True
    assert result.notes_removed > 0


def test_downbeat_and_chord_invariants_preserved():
    bm = _build_dense_chordjack_beatmap(bpm=290.0, measures=10)
    opts = DownscaleOptions(target_dan="5th", prune_ratio=0.25, max_iterations=20)
    result = downscale_beatmap(bm, opts)

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
