"""
A constant tempo and the first downbeat, from the onset envelope.

1. **Period.** The envelope's autocorrelation, with a comb over the lag's 2nd and 4th multiples (a
   real pulse repeats), weighted by a prior centred on 170 BPM (7K songs mostly run 140-220).
2. **Refinement.** Around the winning lag, tempos within ±2.5 % are scored by the strength of the
   envelope's Fourier components at the beat frequency and its 2nd and 4th harmonics (0.05 BPM steps,
   then 0.005 around the best). Over a three-minute song a tempo off by 0.1 % already loses most of it.
3. **Phase and downbeat.** The sharpest bin is the beat, unless the point half a beat away carries more
   mid-band (snare, vocal, chord) onset, in which case the sharpest bin was the off-beat; of the four beats in a
   4/4 measure, the one with the most low-band onset is the downbeat.
   The offset written to a chart is the downbeat moved `CHART_SYNC_MS` earlier (see there).
4. **Confidence.** The song is cut into four parts and each is phased on its own; if their beats
   disagree by more than 25 ms, the tempo probably changes (or there is none), and the estimate says so.

Songs with tempo changes need their timing from elsewhere (`--timing-from`, `--bpm/--offset`).
"""

from dataclasses import dataclass
from typing import List, Tuple

import numpy as np

from proj7k.generator.onset import FRAME_S, OnsetEnvelope, frame_time

MIN_BPM, MAX_BPM = 70.0, 300.0
PRIOR_BPM, PRIOR_SIGMA_OCT = 170.0, 0.6
PHASE_BINS = 128
DRIFT_LIMIT_S = 0.025
#: How much earlier a ranked chart's beat sits than the onset envelope's reading of the same beat. Measured
#: on 40 charts of Kai's osu!lazer library with their own timing (`onset_shift_ms`, median 28.5 ms; mp3 29,
#: ogg 28, so not a decoder delay): charts are timed to where an attack starts to be heard, the envelope
#: peaks where the rise is steepest. An estimated offset is moved by it, so a generated chart plays in sync.
CHART_SYNC_MS = 28.0


@dataclass(frozen=True)
class TempoEstimate:
    bpm: float
    offset_ms: float        # a downbeat as a chart times it (CHART_SYNC_MS before the envelope's beat), >= 0
    confidence: float       # 0..1: sharpness of the fold, scaled
    steady: bool            # the four parts of the song agree on the beat
    drift_ms: float         # largest disagreement between the parts' beat phases

    @property
    def beat_ms(self) -> float:
        return 60000.0 / self.bpm


def estimate_tempo(env: OnsetEnvelope) -> TempoEstimate:
    x = env.total.astype(np.float64)
    if x.size < int(4.0 / FRAME_S) or not np.any(x > 0):
        raise ValueError("音频太短或没有可辨认的起音，无法自动测 BPM；请用 --bpm/--offset 或 --timing-from 指定 timing。")
    x = x - x.mean()

    lag = _coarse_lag(x)
    bpm = _refine_bpm(env.total, 60.0 / (lag * FRAME_S))
    beat_s = 60.0 / bpm
    pulse_s, sharp = _phase(env.total, beat_s)
    phase_s = _on_beat(env, beat_s, pulse_s)
    downbeat_s = _downbeat(env, beat_s, phase_s)
    drift = _drift(env.total, beat_s, pulse_s)
    offset_ms = (downbeat_s * 1000.0 - CHART_SYNC_MS) % (4 * beat_s * 1000.0)
    confidence = float(np.clip((sharp - 1.0) / 3.0, 0.0, 1.0))
    return TempoEstimate(bpm=round(float(bpm), 3), offset_ms=float(round(offset_ms)), confidence=confidence,
                         steady=bool(drift <= DRIFT_LIMIT_S), drift_ms=round(float(drift) * 1000.0, 1))


def _coarse_lag(x: np.ndarray) -> int:
    n = len(x)
    spec = np.fft.rfft(x, 2 * n)
    ac = np.fft.irfft(spec * np.conj(spec))[:n]
    ac = ac / max(ac[0], 1e-12)
    # A period rarely falls on a whole number of frames; the best neighbouring lag stands for it.
    ac = np.maximum(ac, np.maximum(np.roll(ac, 1), np.roll(ac, -1)))
    lo = int(np.floor(60.0 / MAX_BPM / FRAME_S))
    hi = int(np.ceil(60.0 / MIN_BPM / FRAME_S))
    best, best_score = lo, -np.inf
    for lag in range(max(lo, 2), min(hi, n // 4)):
        comb = ac[lag] + 0.5 * ac[min(2 * lag, n - 1)] + 0.25 * ac[min(4 * lag, n - 1)]
        bpm = 60.0 / (lag * FRAME_S)
        prior = np.exp(-0.5 * (np.log2(bpm / PRIOR_BPM) / PRIOR_SIGMA_OCT) ** 2)
        score = comb * (0.5 + prior)
        if score > best_score:
            best, best_score = lag, score
    return best


def _fold(env_total: np.ndarray, period_s: float) -> np.ndarray:
    t = frame_time(np.arange(len(env_total)))
    ph = np.mod(t / period_s, 1.0)
    bins = np.minimum((ph * PHASE_BINS).astype(int), PHASE_BINS - 1)
    return np.bincount(bins, weights=env_total, minlength=PHASE_BINS)


def _sharpness(hist: np.ndarray) -> float:
    smooth = hist + 0.5 * (np.roll(hist, 1) + np.roll(hist, -1))
    return float(smooth.max() / max(smooth.mean(), 1e-12))


def _pulse_strength(env_total: np.ndarray, t: np.ndarray, period_s: float) -> float:
    """How strongly the envelope repeats at `period_s`: the magnitude of its Fourier components at the
    beat frequency and its 2nd and 4th harmonics. Unlike a phase histogram this does not favour periods
    that are a whole number of 10 ms frames (a histogram of such a period only fills a few bins and looks
    sharp for that reason alone)."""
    score = 0.0
    for h, w in ((1, 1.0), (2, 0.5), (4, 0.25)):
        score += w * abs(np.dot(env_total, np.exp(-2j * np.pi * h * t / period_s)))
    return score


def _refine_bpm(env_total: np.ndarray, bpm0: float) -> float:
    t = frame_time(np.arange(len(env_total)))
    env = env_total.astype(np.float64)
    coarse = np.arange(bpm0 * 0.975, bpm0 * 1.025, 0.05)
    best = coarse[int(np.argmax([_pulse_strength(env, t, 60.0 / b) for b in coarse]))]
    fine = np.arange(best - 0.06, best + 0.06, 0.005)
    return float(fine[int(np.argmax([_pulse_strength(env, t, 60.0 / b) for b in fine]))])


def _phase(env_total: np.ndarray, beat_s: float) -> Tuple[float, float]:
    hist = _fold(env_total, beat_s)
    smooth = hist + 0.5 * (np.roll(hist, 1) + np.roll(hist, -1))
    k = int(np.argmax(smooth))
    # Parabolic interpolation between neighbouring bins for a sub-bin phase.
    a, b, c = smooth[(k - 1) % PHASE_BINS], smooth[k], smooth[(k + 1) % PHASE_BINS]
    denom = a - 2 * b + c
    frac = 0.5 * (a - c) / denom if denom != 0 else 0.0
    phase = ((k + 0.5 + frac) / PHASE_BINS) % 1.0
    return phase * beat_s, _sharpness(hist)


def _band_sum(env: OnsetEnvelope, band: int, start_s: float, step_s: float) -> float:
    times = np.arange(start_s, env.duration_s, step_s)
    frames = np.clip(np.array([env.frame_of(t) for t in times], dtype=int), 0, env.n - 1)
    return float(sum(env.bands[band][max(0, f - 2):f + 3].max() for f in frames)) if len(frames) else 0.0


def _on_beat(env: OnsetEnvelope, beat_s: float, phase_s: float) -> float:
    """The strongest pulse is sometimes the off-beat (hats, or an off-beat bass, on the "and"). Of the phase
    and the phase half a beat away, the one with more mid-band onset (snare, vocals, chords) is the beat:
    on 40 charts of Kai's library the mid band was stronger on the chart's beat than half a beat off in 39,
    the low band in 31 (off-beat basslines), the total in 35."""
    other = (phase_s + beat_s / 2) % beat_s
    return other if _band_sum(env, 1, other, beat_s) > _band_sum(env, 1, phase_s, beat_s) else phase_s


def _downbeat(env: OnsetEnvelope, beat_s: float, phase_s: float) -> float:
    """The beat of the four whose positions gather the most kick (low band) onset."""
    measure = 4 * beat_s
    sums: List[float] = []
    for k in range(4):
        start = phase_s + k * beat_s
        times = np.arange(start, env.duration_s, measure)
        frames = np.clip(np.array([env.frame_of(t) for t in times], dtype=int), 0, env.n - 1)
        win = [env.bands[0][max(0, f - 2):f + 3].max() for f in frames] if len(frames) else [0.0]
        sums.append(float(np.sum(win)))
    return phase_s + int(np.argmax(sums)) * beat_s


def _drift(env_total: np.ndarray, beat_s: float, phase_s: float) -> float:
    parts = np.array_split(np.arange(len(env_total)), 4)
    worst = 0.0
    for idx in parts:
        part = np.zeros_like(env_total)
        part[idx] = env_total[idx]
        if part.sum() <= 0:
            continue
        p, _ = _phase(part, beat_s)
        d = abs(((p - phase_s) + beat_s / 2) % beat_s - beat_s / 2)
        worst = max(worst, d)
    return worst
