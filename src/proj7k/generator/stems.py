"""
The song split into drums, bass, vocals and other, and which one each section follows (ADR-0033).

A mapper charts one layer at a time: this part follows the drums, the next the vocal line. The
generator reads every onset of the mix, so its charts follow nothing in particular. With the stems
(Demucs, `htdemucs`) each section gets a *lead layer*: the stem whose onsets are clearest and that
carries most of the section's sound. The rhythm model then reads a blend of the lead stem's onsets
and the mix's (`blend`), so the rows land on what the lead layer plays.

Demucs needs PyTorch (several GB). It is an opt-in experiment: nothing here imports it until
`separate` is called, and `install` (run when the user ticks the option) pip-installs it into the
running interpreter. The model's weights download on first use. Separated stems are cached by the
audio's hash under `~/.cache/proj7k/stems`, so a song is separated once.
"""

import hashlib
import importlib
import logging
import shutil
import subprocess
import sys
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np

from proj7k.generator.audio import SAMPLE_RATE, Audio, resample
from proj7k.generator.grid import GridPoint
from proj7k.generator.onset import FRAME_S, OnsetEnvelope, onset_envelope
from proj7k.i18n import tr

logger = logging.getLogger(__name__)

MODEL = "htdemucs"
STEMS = ("drums", "bass", "vocals", "other")
STEM_NAMES = {"drums": "鼓", "bass": "贝斯", "vocals": "人声", "other": "其他"}
#: The share of the lead stem in the onsets the rhythm model reads; the rest is the mix, so the rows
#: still sit on the song's beat where the lead layer is quiet.
LEAD_WEIGHT = 0.65
CACHE_DIR = Path.home() / ".cache" / "proj7k" / "stems"

Separator = Callable[[Audio], Dict[str, np.ndarray]]


class StemsUnavailable(RuntimeError):
    """Demucs is not installed (and installing it was not asked for, or failed)."""


def available() -> bool:
    try:
        importlib.import_module("demucs")
        importlib.import_module("torch")
    except ImportError:
        return False
    return True


def install(log: Callable[[str], None] = logger.info) -> None:
    """pip-install Demucs (and PyTorch with it) into the running interpreter."""
    if available():
        return
    log(tr("首次使用分轨：正在安装 Demucs 与 PyTorch（数 GB，只需一次）……",
           "First use of stems: installing Demucs and PyTorch (several GB, only once)…"))
    cmds = [[sys.executable, "-m", "pip", "install", "demucs"]]
    uv = shutil.which("uv")
    if uv:
        cmds.append([uv, "pip", "install", "--python", sys.executable, "demucs"])
    errors = []
    for cmd in cmds:
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if proc.returncode == 0:
            importlib.invalidate_caches()
            if available():
                log(tr("Demucs 安装完成。", "Demucs installed."))
                return
        errors.append((proc.stderr or proc.stdout).strip()[-400:])
    raise StemsUnavailable(tr("安装 Demucs 失败：", "Failed to install Demucs: ") + " | ".join(errors))


def separate(audio: Audio, cache_dir: Optional[Path] = None, separator: Optional[Separator] = None,
             log: Callable[[str], None] = logger.info) -> Dict[str, Audio]:
    """The four stems of `audio`, mono at `SAMPLE_RATE`, from the cache when this audio was split before."""
    cache_dir = Path(cache_dir) if cache_dir is not None else CACHE_DIR
    key = hashlib.sha1(np.ascontiguousarray(audio.samples).tobytes()).hexdigest()[:20]
    path = cache_dir / f"{key}-{MODEL}.npz"
    if path.is_file():
        try:
            with np.load(path) as z:
                return {s: Audio(z[s].astype(np.float32), SAMPLE_RATE, audio.source) for s in STEMS}
        except (OSError, KeyError, ValueError):
            pass
    if separator is None:
        if not available():
            raise StemsUnavailable(tr("没有安装 Demucs：在面板勾选「实验：Demucs 分轨」会自动安装，"
                                      "或运行 python -m pip install demucs",
                                      "Demucs isn't installed: ticking \"Experimental: Demucs stems\" in the dashboard "
                                      "installs it, or run python -m pip install demucs"))
        separator = _demucs
    log(tr("正在分轨（鼓 / 贝斯 / 人声 / 其他），CPU 上每首约 1–3 分钟……",
           "Splitting stems (drums / bass / vocals / other), about 1–3 minutes per song on CPU…"))
    stems = separator(audio)
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(path, **{s: stems[s].astype(np.float16) for s in STEMS})
    except OSError as e:
        log(tr(f"分轨结果没能写进缓存：{e}", f"Couldn't cache the stems: {e}"))
    return {s: Audio(stems[s].astype(np.float32), SAMPLE_RATE, audio.source) for s in STEMS}


def _demucs(audio: Audio) -> Dict[str, np.ndarray]:
    import torch  # type: ignore
    from demucs.apply import apply_model  # type: ignore
    from demucs.pretrained import get_model  # type: ignore

    model = get_model(MODEL)
    model.eval()
    sr = int(model.samplerate)
    x = resample(audio.samples, SAMPLE_RATE, sr)
    mix = torch.from_numpy(np.stack([x] * int(model.audio_channels)))
    ref = mix.mean(0)
    mean, std = ref.mean(), ref.std() + 1e-8
    with torch.no_grad():
        out = apply_model(model, ((mix - mean) / std)[None], split=True, overlap=0.25, device="cpu")[0]
    out = out * std + mean
    stems = {}
    for name, wav in zip(model.sources, out):
        stems[name] = resample(wav.mean(0).numpy().astype(np.float32), sr, SAMPLE_RATE)[:len(audio.samples)]
    return {s: stems.get(s, np.zeros_like(audio.samples)) for s in STEMS}


# --- the lead layer ----------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Lead:
    first_bar: int
    last_bar: int      # inclusive
    stem: str
    scores: Dict[str, float]


def choose_leads(stem_envs: Dict[str, OnsetEnvelope], grid: Sequence[GridPoint],
                 sections: Sequence, override: Optional[Dict[int, str]] = None) -> List[Lead]:
    """Each section's lead layer.

    A stem scores, over the grid points of a section, its mean onset strength on the mix's scale (each
    stem's envelope is normalized over the song by itself, so a sparse layer such as the vocals would
    otherwise read as strong as the drums) times the fourth root of its share of the section's loudness:
    the layer whose attacks carry the section. `override` (a section's first bar → stem, from a design
    brief) wins.
    """
    override = override or {}
    leads = []
    for sec in sections:
        pts = [p for p in grid if sec.first_bar <= p.bar <= sec.last_bar and not p.silent]
        scores = {}
        if pts:
            loud = {s: _power(env, pts) for s, env in stem_envs.items()}
            total = sum(loud.values()) or 1.0
            for s, env in stem_envs.items():
                frames = np.clip([env.frame_of(p.time_ms / 1000.0) for p in pts], 0, env.n - 1)
                onset = float(env.total[frames].mean()) * env.onset_ref
                scores[s] = round(onset * float((loud[s] / total) ** 0.25), 4)
        stem = override.get(sec.first_bar) or (max(scores, key=scores.get) if scores else "drums")
        leads.append(Lead(sec.first_bar, sec.last_bar, stem, scores))
    return leads


def _power(env: OnsetEnvelope, pts: Sequence[GridPoint]) -> float:
    frames = np.clip([env.frame_of(p.time_ms / 1000.0) for p in pts], 0, env.n - 1)
    return float(np.mean(10.0 ** (env.loudness_db[frames] / 10.0)) * env.loud_ref)


def stem_envelopes(stems: Dict[str, Audio], mix: Audio) -> Dict[str, OnsetEnvelope]:
    """Each stem's onset envelope, with `loud_ref` and `onset_ref` set so the stems' loudness and attacks
    compare on the mix's scale."""
    mix_ref = _loud_level(mix.samples)
    mix_flux = onset_envelope(mix).flux_level
    out = {}
    for s, a in stems.items():
        env = onset_envelope(a)
        out[s] = replace(env, loud_ref=(_loud_level(a.samples) / max(mix_ref, 1e-9)) ** 2,
                         onset_ref=env.flux_level / max(mix_flux, 1e-9))
    return out


def _loud_level(x: np.ndarray) -> float:
    n = len(x) // 240
    if n == 0:
        return 0.0
    rms = np.sqrt(np.mean(x[: n * 240].reshape(n, 240) ** 2, axis=1) + 1e-12)
    return float(np.percentile(rms, 95))


def blend(mix_env: OnsetEnvelope, stem_envs: Dict[str, OnsetEnvelope], leads: Sequence[Lead],
          bar_span: Dict[int, Tuple[float, float]], weight: float = LEAD_WEIGHT) -> OnsetEnvelope:
    """The mix's envelope with each section's onsets replaced by `weight` of its lead stem's plus the rest
    of the mix's. Loudness and brightness stay the mix's (they describe the song, not the layer)."""
    total = mix_env.total.copy()
    bands = mix_env.bands.copy()
    for lead in leads:
        if lead.stem not in stem_envs or lead.first_bar not in bar_span:
            continue
        t0 = bar_span[lead.first_bar][0]
        t1 = bar_span.get(lead.last_bar, bar_span[lead.first_bar])[1]
        a = max(0, int(t0 / 1000.0 / FRAME_S))
        b = min(mix_env.n, int(np.ceil(t1 / 1000.0 / FRAME_S)) + 1)
        env = stem_envs[lead.stem]
        b = min(b, env.n)
        if b <= a:
            continue
        total[a:b] = weight * env.total[a:b] + (1.0 - weight) * mix_env.total[a:b]
        bands[:, a:b] = weight * env.bands[:, a:b] + (1.0 - weight) * mix_env.bands[:, a:b]
    return replace(mix_env, total=total.astype(np.float32), bands=bands.astype(np.float32))


def bar_spans(grid: Sequence[GridPoint]) -> Dict[int, Tuple[float, float]]:
    """Each measure's time span (ms): its first grid time to the next measure's (or its own last)."""
    first: Dict[int, float] = {}
    last: Dict[int, float] = {}
    for p in grid:
        first[p.bar] = min(first.get(p.bar, p.time_ms), p.time_ms)
        last[p.bar] = max(last.get(p.bar, p.time_ms), p.time_ms)
    bars = sorted(first)
    return {b: (first[b], first[bars[i + 1]] if i + 1 < len(bars) else last[b]) for i, b in enumerate(bars)}


__all__ = ["STEMS", "STEM_NAMES", "Lead", "StemsUnavailable", "available", "install", "separate", "choose_leads",
           "stem_envelopes", "blend", "bar_spans"]
