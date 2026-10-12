"""A tiny drum-and-melody synthesizer: songs whose every attack time is known, for the generator's tests."""

from typing import List, Tuple

import numpy as np

SR = 24000


def _kick(sr: int) -> np.ndarray:
    t = np.arange(int(0.25 * sr)) / sr
    f = 50.0 + 90.0 * np.exp(-t * 30.0)
    return np.sin(2 * np.pi * np.cumsum(f) / sr) * np.exp(-t * 12.0)


def _snare(sr: int, rng: np.random.Generator) -> np.ndarray:
    t = np.arange(int(0.18 * sr)) / sr
    return (0.6 * rng.standard_normal(len(t)) + 0.4 * np.sin(2 * np.pi * 190 * t)) * np.exp(-t * 22.0)


def _hat(sr: int, rng: np.random.Generator) -> np.ndarray:
    t = np.arange(int(0.05 * sr)) / sr
    noise = rng.standard_normal(len(t))
    noise = np.diff(noise, prepend=0.0)  # tilt toward the highs
    return 0.35 * noise * np.exp(-t * 80.0)


def _tone(sr: int, freq: float, length_s: float) -> np.ndarray:
    t = np.arange(int(length_s * sr)) / sr
    env = np.minimum(1.0, t / 0.004) * np.exp(-t * 6.0)
    return 0.5 * env * (np.sin(2 * np.pi * freq * t) + 0.3 * np.sin(4 * np.pi * freq * t))


def drum_song(
    bpm: float = 170.0,
    offset_s: float = 0.5,
    measures: int = 24,
    sr: int = SR,
    seed: int = 1,
    melody: bool = True,
) -> Tuple[np.ndarray, List[float]]:
    """A 4/4 loop: kick on beats 1 and 3, snare on 2 and 4, hats on every 8th, and a melody on some
    16ths. Returns the samples and the sorted attack times in seconds."""
    rng = np.random.default_rng(seed)
    beat = 60.0 / bpm
    total = offset_s + measures * 4 * beat + 1.0
    out = np.zeros(int(total * sr) + sr)
    attacks = []
    kick, snare, hat = _kick(sr), _snare(sr, rng), _hat(sr, rng)
    notes = [261.6, 293.7, 329.6, 392.0, 440.0, 523.3, 587.3, 659.3]

    def put(sample: np.ndarray, t: float, gain: float) -> None:
        i = int(round(t * sr))
        out[i:i + len(sample)] += gain * sample[: len(out) - i]

    for m in range(measures):
        for b in range(4):
            tb = offset_s + (m * 4 + b) * beat
            put(kick if b in (0, 2) else snare, tb, 0.9)
            put(hat, tb + beat / 2, 0.6)
            attacks += [tb, tb + beat / 2]
            if melody:
                for q in (0.25, 0.75):
                    if rng.random() < 0.5:
                        t = tb + q * beat
                        put(_tone(sr, notes[rng.integers(len(notes))], beat), t, 0.5)
                        attacks.append(t)
    return (out / max(1e-9, np.abs(out).max()) * 0.9).astype(np.float32), sorted(attacks)


def write_wav(path, samples: np.ndarray, sr: int = SR) -> None:
    import wave

    pcm = (np.clip(samples, -1, 1) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())
