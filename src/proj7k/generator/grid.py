"""
Candidate note times: the chart's beat grid, each point read against the onset envelope.

Every beat of every uninherited timing section is split either binary (1/2 and 1/4) or ternary (1/3)
depending on which subdivision the music actually plays in that measure; the choice is made per
measure, so a song that swings for two bars gets triplets there and straight 16ths elsewhere, and
measures that sound alike get the same grid (a choice per beat flipped on noise, ADR-0030). Each point keeps
how strong the strongest onset near it is (within an eighth of a beat, at most 25 ms), the strength
per band, and where in the spectrum that onset sits.
"""

from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence

import numpy as np

from proj7k.generator.onset import OnsetEnvelope, frame_time
from proj7k.parser import TimingPoint

#: Metric levels: 0 measure downbeat, 1 beat, 2 half beat, 3 quarter or third of a beat.
DOWNBEAT, BEAT, HALF, SUB = 0, 1, 2, 3
#: Below this loudness (dB under the song's loud level) a frame is silence, and no note goes there.
SILENCE_DB = -38.0
#: A triplet beat needs its 1/3 points this much stronger than its 1/4 points.
TERNARY_MARGIN = 1.3


@dataclass(frozen=True)
class GridPoint:
    time_ms: float
    level: int
    strength: float          # onset strength near the point, ~0..1.5
    low: float
    mid: float
    high: float
    brightness: float        # 0 (low) .. 1 (high)
    silent: bool
    beat: float = 0.0        # beats since the first beat of the song, counted across timing sections
    bar: int = 0             # the measure, numbered across timing sections
    slot: int = 0            # place in the measure, in 48ths of a beat


def onset_shift_ms(env: OnsetEnvelope, timing: Sequence[TimingPoint], limit_ms: float = 60.0) -> float:
    """
    How far the music's onsets sit from a given timing's beats, in ms (positive: the onsets come later).

    A chart's timing is set by ear against the way osu! plays the file, and the onset envelope reads
    the decoded file; the two can disagree by a few tens of ms (decoder delay, soft attacks). The shift
    that puts the most onset strength on the beats is that disagreement; reading the grid through it
    keeps every point's strength window centred on the attack it belongs to.
    """
    sections = sorted((tp for tp in timing if tp.uninherited and tp.beat_length > 0), key=lambda tp: tp.time)
    if not sections or env.n < 2:
        return 0.0
    end_ms = env.duration_s * 1000.0
    beats: List[float] = []
    for i, tp in enumerate(sections):
        sec_end = sections[i + 1].time if i + 1 < len(sections) else end_ms
        first = tp.time - np.floor(tp.time / tp.beat_length) * tp.beat_length if i == 0 else tp.time
        beats.extend(np.arange(first, sec_end, tp.beat_length / 2.0))
    t = np.asarray(beats) / 1000.0
    if t.size == 0:
        return 0.0
    frames_t = frame_time(np.arange(env.n))
    shifts = np.arange(-limit_ms, limit_ms + 0.5, 1.0)
    scores = [np.interp(t + s / 1000.0, frames_t, env.total, left=0.0, right=0.0).sum() for s in shifts]
    return float(shifts[int(np.argmax(scores))])


def build_grid(env: OnsetEnvelope, timing: Sequence[TimingPoint], end_ms: Optional[float] = None,
               shift_ms: float = 0.0) -> List[GridPoint]:
    sections = sorted((tp for tp in timing if tp.uninherited and tp.beat_length > 0), key=lambda tp: tp.time)
    if not sections:
        raise ValueError("没有可用的 timing（缺少非继承 timing point）")
    end_ms = env.duration_s * 1000.0 if end_ms is None else end_ms
    points: List[GridPoint] = []
    # Beats and measures are counted so that every measure starts on a whole multiple of its meter
    # (a section's count resumes at the next such multiple), which is what lets stretches of 1, 2 or
    # 4 beats line up with the measures when the pattern sampler looks for repeats.
    beat0 = 0                # beat count of this section's first measure
    bar0 = 0                 # measures before this section
    for i, tp in enumerate(sections):
        L = tp.beat_length
        meter = tp.meter if tp.meter and tp.meter > 0 else 4
        sec_end = sections[i + 1].time if i + 1 < len(sections) else end_ms
        k = k0 = 0
        if i == 0:  # osu! extends the first section backwards to the start of the song
            k = k0 = -int(np.floor(tp.time / L))
        ternary_bar: Dict[int, bool] = {}
        while True:
            tb = tp.time + k * L
            if tb >= sec_end - 1e-6:
                break
            place = dict(beat=beat0 + k - (k0 // meter) * meter, bar=bar0 + k // meter - k0 // meter)
            if tb >= 0:
                level = DOWNBEAT if k % meter == 0 else BEAT
                points.append(_point(env, tb, level, L, shift_ms, place, 0, k % meter))
            m = k // meter
            if m not in ternary_bar:
                starts = [tp.time + (m * meter + j) * L + shift_ms for j in range(meter)]
                ternary_bar[m] = _is_ternary(env, [t for t in starts if t < sec_end + shift_ms], L)
            subs = ((1 / 3, SUB), (2 / 3, SUB)) if ternary_bar[m] else ((0.25, SUB), (0.5, HALF), (0.75, SUB))
            for frac, lvl in subs:
                t = tb + frac * L
                if 0 <= t < sec_end - 1e-6:
                    points.append(_point(env, t, lvl, L, shift_ms, place, frac, k % meter))
            k += 1
        beat0 += k - (k0 // meter) * meter
        beat0 = -(-beat0 // 4) * 4
        bar0 = (points[-1].bar + 1) if points else bar0
    points.sort(key=lambda p: p.time_ms)
    return points


def _strength_at(env: OnsetEnvelope, t_ms: float, beat_ms: float) -> float:
    f = env.peak(t_ms / 1000.0, _window_s(beat_ms))
    return float(env.total[f])


def _window_s(beat_ms: float) -> float:
    return min(0.025, beat_ms / 8000.0)


def _is_ternary(env: OnsetEnvelope, beats: Sequence[float], L: float) -> bool:
    """Whether a measure (the start times of its beats) plays in thirds of a beat rather than quarters."""
    if not beats:
        return False
    s_bin = sum(max(_strength_at(env, tb + 0.25 * L, L), _strength_at(env, tb + 0.75 * L, L)) for tb in beats)
    s_tri = sum(max(_strength_at(env, tb + L / 3, L), _strength_at(env, tb + 2 * L / 3, L)) for tb in beats)
    return s_tri > 0.2 * len(beats) and s_tri > TERNARY_MARGIN * s_bin


def _point(env: OnsetEnvelope, t_ms: float, level: int, beat_ms: float, shift_ms: float = 0.0,
           place: Optional[dict] = None, frac: float = 0.0, beat_in_bar: int = 0) -> GridPoint:
    f = env.peak((t_ms + shift_ms) / 1000.0, _window_s(beat_ms))
    place = place or {}
    return GridPoint(
        time_ms=float(t_ms),
        level=level,
        strength=float(env.total[f]),
        low=float(env.bands[0][f]),
        mid=float(env.bands[1][f]),
        high=float(env.bands[2][f]),
        brightness=float(env.brightness[f]),
        silent=bool(env.loudness_db[f] < SILENCE_DB),
        beat=float(place.get("beat", 0.0)) + frac,
        bar=int(place.get("bar", 0)),
        slot=int(round((beat_in_bar + frac) * 48)),
    )
