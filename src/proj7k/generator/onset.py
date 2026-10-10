"""
Where the music starts something new: a spectral-flux onset envelope every 10 ms.

Each frame's log-magnitude spectrum is compared with the previous frame's (with a 3-bin maximum
filter on the previous one, so vibrato and slides do not read as attacks); the positive rise, summed
over a band, is that band's onset strength. Three bands are kept apart because they mean different
things on a chart: the low band carries kicks and bass, the mid band most melodies and snares, the
high band hats and cymbals. The envelope is normalized once over the whole song, so a loud chorus
stays denser than a quiet intro.
"""

from dataclasses import dataclass

import numpy as np
from scipy.ndimage import maximum_filter1d, uniform_filter1d

from proj7k.generator.audio import Audio

HOP = 240                    # samples at 24 kHz: 10 ms
N_FFT = 1024
FRAME_S = 0.010
#: Band edges in Hz: low < 200 <= mid < 3000 <= high.
BAND_EDGES = (200.0, 3000.0)
#: Where the spectral-flux peak of an attack lands relative to the attack. Negative: the centred
#: analysis window starts rising before the attack. Measured on synthetic drums, and pinned by
#: `tests/test_generator.py`.
ONSET_LAG_S = -0.009


@dataclass(frozen=True)
class OnsetEnvelope:
    total: np.ndarray       # [frames] combined onset strength, ~0..1 (98th percentile = 1)
    bands: np.ndarray       # [3, frames] low, mid, high, each normalized the same way
    brightness: np.ndarray  # [frames] 0..1: where in the spectrum the new energy is (0 = 100 Hz, 1 = 8 kHz)
    loudness_db: np.ndarray  # [frames] frame RMS in dB relative to the song's loud level
    duration_s: float

    @property
    def n(self) -> int:
        return len(self.total)

    def frame_of(self, t_s: float) -> int:
        return int(round((t_s + ONSET_LAG_S) / FRAME_S))

    def peak(self, t_s: float, half_window_s: float) -> int:
        """The frame of the strongest onset within `t_s ± half_window_s` (clamped to the song)."""
        lo = max(0, self.frame_of(t_s - half_window_s))
        hi = min(self.n, self.frame_of(t_s + half_window_s) + 1)
        if hi <= lo:
            return min(max(self.frame_of(t_s), 0), self.n - 1)
        return lo + int(np.argmax(self.total[lo:hi]))


def onset_envelope(audio: Audio) -> OnsetEnvelope:
    x = np.asarray(audio.samples, dtype=np.float32)
    sr = audio.sample_rate
    pad = N_FFT // 2
    x = np.pad(x, (pad, pad))
    n_frames = 1 + max(0, (len(x) - N_FFT) // HOP)
    if n_frames < 3:
        z = np.zeros(max(n_frames, 1))
        return OnsetEnvelope(z, np.zeros((3, len(z))), z, z - 120.0, audio.duration_s)
    idx = np.arange(N_FFT)[None, :] + HOP * np.arange(n_frames)[:, None]
    window = np.hanning(N_FFT).astype(np.float32)
    frames = x[idx] * window
    mag = np.abs(np.fft.rfft(frames, axis=1)).astype(np.float32)
    freqs = np.fft.rfftfreq(N_FFT, 1.0 / sr)

    logmag = np.log1p(100.0 * mag)
    ref = maximum_filter1d(logmag, size=3, axis=1)
    rise = np.zeros_like(logmag)
    rise[1:] = np.maximum(0.0, logmag[1:] - ref[:-1])

    lo_mask = freqs < BAND_EDGES[0]
    mid_mask = (freqs >= BAND_EDGES[0]) & (freqs < BAND_EDGES[1])
    hi_mask = freqs >= BAND_EDGES[1]
    bands = np.stack([rise[:, m].sum(axis=1) for m in (lo_mask, mid_mask, hi_mask)])
    # Bands differ in width by an order of magnitude; each is scaled by its own loud level first.
    bands = np.stack([_normalize(b) for b in bands])
    total = _normalize(bands[0] + bands[1] + 0.7 * bands[2])

    # Brightness: log-frequency centroid of the rise, clipped to 100 Hz .. 8 kHz.
    useful = (freqs >= 100.0) & (freqs <= 8000.0)
    logf = np.log2(freqs[useful] / 100.0) / np.log2(80.0)
    w = rise[:, useful]
    wsum = w.sum(axis=1)
    brightness = np.where(wsum > 1e-9, (w * logf).sum(axis=1) / np.maximum(wsum, 1e-9), 0.5)
    brightness = uniform_filter1d(brightness, size=3)

    rms = np.sqrt(np.mean(frames ** 2, axis=1) + 1e-12)
    loud = np.percentile(rms, 95) + 1e-9
    loudness_db = 20.0 * np.log10(rms / loud)
    return OnsetEnvelope(total, bands, brightness.astype(np.float32), loudness_db.astype(np.float32), audio.duration_s)


def _normalize(v: np.ndarray) -> np.ndarray:
    nz = v[v > 0]
    if nz.size == 0:
        return np.zeros_like(v)
    scale = np.percentile(nz, 98)
    if scale <= 0:
        return np.zeros_like(v)
    return np.clip(v / scale, 0.0, 1.5).astype(np.float32)


def frame_time(frame: float) -> float:
    """The attack time a frame's onset reading stands for (the inverse of `OnsetEnvelope.frame_of`)."""
    return frame * FRAME_S - ONSET_LAG_S
