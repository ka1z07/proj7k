"""
Phrases that sound the same are charted the same (ADR-0045).

A mapper writes a motif once and brings it back when the music does: in the human LIMBO chart 39% of
8-beat phrases come back elsewhere, note for note or mirrored. Mapperatorinator writes each window from
the last ~8 seconds and reuses none (0% in every chart, fast or sequential; meow, 2026-10-26), so a
chorus's second pass is a new idea and the chart reads as incoherent.

`reuse_phrases` cuts the song into phrases of `PHRASE_BARS` measures from the first measure. Each phrase
is compared with every earlier phrase of the same layout by its harmony (a chroma vector per beat: the
drums of a pop song make every phrase's onsets look alike, the chords and the melody do not) and by the
onset strengths of its grid points in the three bands (as `phrases.group_measures`); when the closest is
at least `SIMILAR` alike in both, the earlier phrase's notes are written over it, beat for beat (on the
target's own beats), long notes and all.
A copied phrase can be the source of a later one, so a chorus that comes back three times is charted
once. Quiet phrases are left alone.
"""

from dataclasses import replace
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple, Union

import numpy as np

from proj7k.generator import legal
from proj7k.generator.audio import load_audio
from proj7k.generator.grid import BEAT, DOWNBEAT, GridPoint, build_grid, onset_shift_ms
from proj7k.generator.onset import onset_envelope
from proj7k.parser import HitObject, TimingPoint

#: Measures per phrase: two measures of 4/4 are the 8 beats the reuse was measured on.
PHRASE_BARS = 2
#: Cosine similarity of two phrases' band strengths at or above which the later one is charted as the
#: earlier.
SIMILAR = 0.9
#: A phrase this quiet (mean strength) is left alone: in a quiet passage everything looks alike.
MIN_ACTIVITY = 0.05
#: The pitch range the chroma is read from, Hz.
CHROMA_LOW, CHROMA_HIGH = 80.0, 4000.0


def _phrases(grid: Sequence[GridPoint]) -> List[Tuple[int, List[int]]]:
    """(first bar, grid indices) of each phrase of `PHRASE_BARS` measures, counted from the first bar."""
    bars: Dict[int, List[int]] = {}
    for i, p in enumerate(grid):
        bars.setdefault(p.bar, []).append(i)
    if not bars:
        return []
    first = min(bars)
    out: Dict[int, List[int]] = {}
    for bar, idx in bars.items():
        out.setdefault(first + (bar - first) // PHRASE_BARS * PHRASE_BARS, []).extend(idx)
    return sorted((b, sorted(idx)) for b, idx in out.items())


def chroma(samples: np.ndarray, rate: int, times_ms: Sequence[float]) -> np.ndarray:
    """A 12-bin pitch-class vector for each stretch between consecutive `times_ms` (one row fewer), each
    centred on its mean, so a phrase is compared by which notes it plays and not by how loud it is."""
    edges = np.clip((np.asarray(times_ms) / 1000.0 * rate).astype(int), 0, len(samples))
    out = np.zeros((max(0, len(edges) - 1), 12))
    for i, (a, b) in enumerate(zip(edges, edges[1:])):
        seg = samples[a:b]
        if len(seg) < 256:
            continue
        spec = np.abs(np.fft.rfft(seg * np.hanning(len(seg))))
        freqs = np.fft.rfftfreq(len(seg), 1.0 / rate)
        keep = (freqs >= CHROMA_LOW) & (freqs <= CHROMA_HIGH)
        pc = np.round(12 * np.log2(freqs[keep] / 440.0)).astype(int) % 12
        np.add.at(out[i], pc, spec[keep] ** 2)
        out[i] = np.sqrt(out[i])
        out[i] -= out[i].mean()
    return out


def _cos(u: np.ndarray, v: np.ndarray) -> float:
    return float(u @ v / (np.linalg.norm(u) * np.linalg.norm(v) + 1e-12))


def sources(grid: Sequence[GridPoint], beat_chroma: Optional[Dict[float, np.ndarray]] = None) -> Dict[int, int]:
    """{phrase's first bar: the first bar of the earlier phrase it is charted as}. `beat_chroma` maps each
    beat's grid time to its chroma; without it phrases are compared by their onsets alone."""
    seen: List[Tuple[int, tuple, np.ndarray, np.ndarray]] = []   # (first bar, layout, onsets, chroma)
    out: Dict[int, int] = {}
    for bar, idx in _phrases(grid):
        layout = tuple((grid[i].bar - bar, grid[i].slot) for i in idx)
        v = np.array([[grid[i].low, grid[i].mid, grid[i].high] for i in idx], dtype=float).ravel()
        if v.mean() < MIN_ACTIVITY:
            continue
        c = np.concatenate([beat_chroma.get(grid[i].time_ms, np.zeros(12)) for i in idx
                            if grid[i].level in (DOWNBEAT, BEAT)]) if beat_chroma else np.zeros(0)
        best, best_sim = None, -1.0
        for b, lay, u, d in seen:
            if lay != layout:
                continue
            sim = min(_cos(v, u), _cos(c, d) if c.size else 1.0)
            if sim >= SIMILAR and sim > best_sim:
                best, best_sim = b, sim
        if best is not None:
            out[bar] = out.get(best, best)
        seen.append((bar, layout, v, c))
    return out


def copy_phrases(objs: Sequence[HitObject], grid: Sequence[GridPoint], timing: Sequence[TimingPoint],
                 plan: Dict[int, int]) -> Tuple[List[HitObject], int]:
    """(the chart with each phrase in `plan` written over by its source, how many phrases were)."""
    times = np.array([p.time_ms for p in grid])
    beats = np.array([p.beat for p in grid])
    first = {}
    for p in grid:
        if p.bar not in first or p.beat < first[p.bar]:
            first[p.bar] = p.beat

    def span(bar):
        b0 = first[bar]
        b1 = first.get(bar + PHRASE_BARS, b0 + 4 * PHRASE_BARS)
        return b0, b1

    out = list(objs)
    copied = 0
    for target, source in sorted(plan.items()):
        (s0, s1), (t0, t1) = span(source), span(target)
        at = np.interp(np.array([o.time for o in out]) + 1, times, beats)
        notes = [o for o, b in zip(out, at) if s0 <= b < s1]
        if not notes:
            continue
        moved = []
        for o in notes:
            t = np.interp(np.interp(o.time, times, beats) - s0 + t0, beats, times)
            end = None if o.end_time is None else np.interp(np.interp(o.end_time, times, beats) - s0 + t0, beats, times)
            moved.append(replace(o, time=float(round(t)), end_time=None if end is None else float(round(end))))
        out = [o for o, b in zip(out, at) if not t0 <= b < t1] + moved
        copied += 1
    out.sort(key=lambda o: (o.time, o.column))
    return legal.sanitize(out, timing)[0], copied


def reuse_phrases(objs: Sequence[HitObject], timing: Sequence[TimingPoint],
                  audio_path: Union[str, Path]) -> Tuple[List[HitObject], int, int]:
    """(the chart with alike phrases charted alike, phrases copied, phrases in the song)."""
    audio = load_audio(audio_path)
    env = onset_envelope(audio)
    shift = onset_shift_ms(env, timing)
    grid = build_grid(env, timing, shift_ms=shift)
    beats = [p.time_ms for p in grid if p.level in (DOWNBEAT, BEAT)]
    # Each beat's stretch runs to the next beat; the last one is as long as the one before it.
    ends = beats + [2 * beats[-1] - beats[-2]] if len(beats) > 1 else beats
    rows = chroma(audio.samples, audio.sample_rate, [t + shift for t in ends])
    plan = sources(grid, dict(zip(beats, rows)))
    out, copied = copy_phrases(objs, grid, timing, plan)
    return out, copied, len(_phrases(grid))
