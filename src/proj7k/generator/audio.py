"""
Audio in: any file a chart's AudioFilename can name, decoded to one mono float array.

Three decoders are tried in order, so the generator works on a bare install and gets better with
what is there:

1. a PCM `.wav` through the standard library (`wave`), always available;
2. `miniaudio` (mp3, ogg/vorbis, flac, wav; a single wheel with no system libraries), when installed;
3. `ffmpeg` on PATH (anything it reads, including opus and m4a).

Every decoder returns the same thing: float32 samples in [-1, 1] at `SAMPLE_RATE`, channels mixed
down. Nothing is cached; a three-minute song decodes in well under a second.
"""

import shutil
import subprocess
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Union

import numpy as np

#: Every analysis runs at this rate. 24 kHz keeps the band an onset lives in (up to 12 kHz) and makes
#: the 240-sample hop exactly 10 ms.
SAMPLE_RATE = 24000


class AudioDecodeError(RuntimeError):
    """The file could not be decoded by any available decoder."""


@dataclass(frozen=True)
class Audio:
    samples: np.ndarray  # float32, mono, SAMPLE_RATE
    sample_rate: int
    source: str          # the decoder that read it: wave, miniaudio or ffmpeg

    @property
    def duration_s(self) -> float:
        return len(self.samples) / float(self.sample_rate)


def load_audio(path: Union[str, Path]) -> Audio:
    """Decode `path` to mono float32 at `SAMPLE_RATE`."""
    path = Path(path)
    if not path.is_file():
        raise AudioDecodeError(f"音频文件不存在：{path}")
    errors = []
    if path.suffix.lower() == ".wav":
        try:
            return Audio(_read_wav(path), SAMPLE_RATE, "wave")
        except (wave.Error, EOFError, ValueError) as e:
            errors.append(f"wave: {e}")
    try:
        import miniaudio  # type: ignore
    except ImportError:
        miniaudio = None
    if miniaudio is not None:
        try:
            decoded = miniaudio.decode_file(
                str(path), output_format=miniaudio.SampleFormat.SIGNED16, nchannels=1, sample_rate=SAMPLE_RATE
            )
            pcm = np.frombuffer(decoded.samples, dtype=np.int16).astype(np.float32) / 32768.0
            return Audio(pcm, SAMPLE_RATE, "miniaudio")
        except Exception as e:  # miniaudio raises its own DecodeError and OSError subclasses
            errors.append(f"miniaudio: {e}")
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg:
        proc = subprocess.run(
            [ffmpeg, "-v", "error", "-nostdin", "-i", str(path), "-ac", "1", "-ar", str(SAMPLE_RATE), "-f", "s16le", "-"],
            capture_output=True,
        )
        if proc.returncode == 0 and proc.stdout:
            pcm = np.frombuffer(proc.stdout, dtype=np.int16).astype(np.float32) / 32768.0
            return Audio(pcm, SAMPLE_RATE, "ffmpeg")
        errors.append(f"ffmpeg: {proc.stderr.decode('utf-8', 'replace').strip()[:200]}")
    if not errors:
        raise AudioDecodeError(
            f"读不了 {path.suffix or '这种'} 音频：请安装 miniaudio（pip install miniaudio）或把 ffmpeg 加进 PATH。"
        )
    raise AudioDecodeError(f"解码 {path.name} 失败：" + "；".join(errors))


def _read_wav(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as w:
        channels, width, rate, frames = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
        raw = w.readframes(frames)
    if width == 1:
        data = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
    elif width == 2:
        data = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    elif width == 3:
        b = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3).astype(np.int32)
        v = b[:, 0] | (b[:, 1] << 8) | (b[:, 2] << 16)
        v = np.where(v >= 1 << 23, v - (1 << 24), v)
        data = v.astype(np.float32) / float(1 << 23)
    elif width == 4:
        data = np.frombuffer(raw, dtype="<i4").astype(np.float32) / float(1 << 31)
    else:
        raise ValueError(f"unsupported sample width {width}")
    data = data.reshape(-1, channels).mean(axis=1)
    return resample(data, rate, SAMPLE_RATE)


def resample(data: np.ndarray, rate: int, target: int = SAMPLE_RATE) -> np.ndarray:
    if rate == target:
        return data.astype(np.float32)
    from math import gcd

    from scipy.signal import resample_poly

    g = gcd(rate, target)
    return resample_poly(data, target // g, rate // g).astype(np.float32)
