"""
The song's sections and how loud each one is (ADR-0031).

A mapper charts a song by its parts: a quiet intro, a verse that follows the vocals, a build, a chorus
or drop that gets the densest patterns, a break that rests the hands. A chart with one density for the
whole song reads as a wall of notes. Ranked charts vary their density from one four-measure stretch to
the next with a coefficient of variation of about 0.3 at every level; a chart picked with one onset
threshold for the whole song varies about 0.04.

1. Each measure is described by its loudness and its onset strength in the three bands, each turned
   into a z-score over the song.
2. Section boundaries are where the music changes: peaks of the novelty curve of the measures'
   self-similarity (a checkerboard kernel of `KERNEL` measures, Foote 2000), at least `MIN_SECTION`
   measures apart, moved onto a multiple of four measures from the first measure when one is within a
   measure (phrases come in fours).
3. Sections that sound alike get the same label (a chorus that comes back is the same chorus), and a
   label's sections share one energy: the mean of their loudness and onset z-scores, ranked over the
   song's measures to 0 (its quietest part) .. 1 (its loudest), and pulled toward 0.5 when the
   sections differ little in loudness (`FULL_CONTRAST_DB`).
"""

from dataclasses import dataclass
from typing import Dict, List, Sequence, Tuple

import numpy as np

from proj7k.generator.grid import GridPoint
from proj7k.generator.onset import OnsetEnvelope

#: Half-width of the checkerboard kernel, in measures.
KERNEL = 4
#: The shortest section, in measures.
MIN_SECTION = 4
#: A novelty peak counts as a boundary when it rises this many standard deviations above the mean.
PEAK_Z = 0.0
#: Sections whose mean loudness spans this many dB or more are spread over the whole 0..1 energy
#: range; a smaller span is spread proportionally less.
FULL_CONTRAST_DB = 6.0
#: Two sections are the same part when their mean features, z-scored over the song's sections, are
#: this close (Euclidean).
SAME_PART = 0.8


@dataclass(frozen=True)
class Section:
    first_bar: int
    last_bar: int          # inclusive
    label: int             # sections with the same label sound alike
    energy: float          # 0 (the song's quietest section) .. 1 (its loudest)


def find_sections(env: OnsetEnvelope, grid: Sequence[GridPoint]) -> List[Section]:
    bars = sorted({p.bar for p in grid})
    if not bars:
        return []
    feats, loud_db = _bar_features(env, grid, bars)
    bounds = _boundaries(feats)
    spans = [(bounds[i], bounds[i + 1] - 1) for i in range(len(bounds) - 1)]
    means = np.array([feats[a:b + 1].mean(axis=0) for a, b in spans])
    labels = _labels(means)
    raw = means[:, 0] * 0.5 + means[:, 1:4].mean(axis=1) * 0.5
    by_label: Dict[int, List[int]] = {}
    for i, lab in enumerate(labels):
        by_label.setdefault(lab, []).append(i)
    shared = np.array([raw[by_label[lab]].mean() for lab in labels])
    energy = _ranked(shared, np.array([b - a + 1 for a, b in spans], dtype=float))
    # A song that hardly changes keeps its sections near the middle: the rank alone would make one
    # chorus of a steady loop "loud" and the next "quiet".
    sec_db = np.array([np.median(loud_db[a:b + 1]) for a, b in spans])
    contrast = min(1.0, float(sec_db.max() - sec_db.min()) / FULL_CONTRAST_DB) if len(spans) > 1 else 0.0
    energy = 0.5 + (energy - 0.5) * contrast
    return [Section(bars[a], bars[b], int(lab), float(e)) for (a, b), lab, e in zip(spans, labels, energy)]


def section_energy(grid: Sequence[GridPoint], sections: Sequence[Section]) -> List[float]:
    """Each grid point's section energy."""
    by_bar: Dict[int, float] = {}
    for s in sections:
        for b in range(s.first_bar, s.last_bar + 1):
            by_bar[b] = s.energy
    return [by_bar.get(p.bar, 0.5) for p in grid]


def _bar_features(env: OnsetEnvelope, grid: Sequence[GridPoint], bars: List[int]) -> Tuple[np.ndarray, np.ndarray]:
    """[bars, 4]: loudness, low, mid, high onset strength per measure, z-scored over the song; and each
    measure's loudness in dB."""
    span: Dict[int, List[float]] = {}
    strength: Dict[int, List[np.ndarray]] = {}
    for p in grid:
        span.setdefault(p.bar, []).append(p.time_ms)
        strength.setdefault(p.bar, []).append(np.array([p.low, p.mid, p.high]))
    out = np.zeros((len(bars), 4))
    for i, b in enumerate(bars):
        t0, t1 = min(span[b]) / 1000.0, max(span[b]) / 1000.0
        f0, f1 = max(env.frame_of(t0), 0), min(env.frame_of(t1) + 1, env.n)
        loud = env.loudness_db[f0:f1] if f1 > f0 else np.array([-60.0])
        # The upper quartile: a measure the music starts or stops in is as loud as its music.
        out[i, 0] = float(np.percentile(np.maximum(loud, -60.0), 75))
        out[i, 1:] = np.mean(strength[b], axis=0)
    sd = out.std(axis=0)
    return (out - out.mean(axis=0)) / np.where(sd > 1e-9, sd, 1.0), out[:, 0].copy()


def _boundaries(feats: np.ndarray) -> List[int]:
    """Indices (into the measures) where sections start, plus the end."""
    n = len(feats)
    if n < 2 * MIN_SECTION:
        return [0, n]
    norm = feats / (np.linalg.norm(feats, axis=1, keepdims=True) + 1e-9)
    ssm = norm @ norm.T
    k = KERNEL
    sign = np.sign(np.arange(-k, k) + 0.5)
    kernel = np.outer(sign, sign)
    novelty = np.zeros(n)
    for i in range(k, n - k + 1):
        novelty[i] = float((ssm[i - k:i + k, i - k:i + k] * kernel).sum())
    # Loudness steps too: a drop can keep its spectrum shape and only get louder.
    loud = feats[:, 0]
    for i in range(k, n - k + 1):
        novelty[i] += abs(loud[i:i + k].mean() - loud[i - k:i].mean()) * k
    cut = novelty.mean() + PEAK_Z * novelty.std()
    peaks = [i for i in range(1, n - 1) if novelty[i] >= cut and novelty[i] >= novelty[i - 1] and novelty[i] >= novelty[i + 1]]
    chosen: List[int] = []
    for i in sorted(peaks, key=lambda i: -novelty[i]):
        j = int(round(i / 4.0) * 4)
        if abs(j - i) <= 1 and 0 < j < n:
            i = j
        if i < MIN_SECTION or n - i < MIN_SECTION or any(abs(i - c) < MIN_SECTION for c in chosen):
            continue
        chosen.append(i)
    return [0] + sorted(chosen) + [n]


def _ranked(values: np.ndarray, weights: np.ndarray) -> np.ndarray:
    """0..1 by rank, each section weighted by its length: a song's quietest half of its measures spans
    0..0.5 however loud it is, so one silent outro does not squeeze everything else to the top."""
    order = np.argsort(values, kind="stable")
    total = weights.sum()
    out = np.zeros(len(values))
    acc = 0.0
    for i in order:
        out[i] = (acc + weights[i] / 2.0) / total
        acc += weights[i]
    # Sections that share a value (one part, several times) share a rank.
    for v in np.unique(values):
        same = values == v
        out[same] = out[same].mean()
    return out


def _labels(means: np.ndarray) -> List[int]:
    sd = means.std(axis=0)
    means = (means - means.mean(axis=0)) / np.where(sd > 1e-9, sd, 1.0)
    labels: List[int] = []
    centers: List[np.ndarray] = []
    for m in means:
        d = [float(np.linalg.norm(m - c)) for c in centers]
        if d and min(d) <= SAME_PART:
            labels.append(int(np.argmin(d)))
        else:
            centers.append(m)
            labels.append(len(centers) - 1)
    return labels


def section_styles(grid: Sequence[GridPoint], sections: Sequence[Section], seed: int = 0) -> Dict[int, str]:
    """A pattern style (`patterns.STYLES`) for every measure: one per part, so a chorus that comes back
    is charted the same way, and a part is charted differently from the one before it where it can be.
    Quiet parts lean to plain patterns and stairs; loud ones to anything, jacks included."""
    rng = np.random.default_rng([seed, 31])
    by_label: Dict[int, str] = {}
    prev = None
    for s in sections:
        if s.label not in by_label:
            options = ["plain", "flow", "trill", "jack"] if s.energy >= 0.5 else ["plain", "flow", "trill"]
            fresh = [o for o in options if o != prev] or options
            by_label[s.label] = fresh[int(rng.integers(len(fresh)))]
        prev = by_label[s.label]
    out: Dict[int, str] = {}
    for s in sections:
        for b in range(s.first_bar, s.last_bar + 1):
            out[b] = by_label[s.label]
    return out
