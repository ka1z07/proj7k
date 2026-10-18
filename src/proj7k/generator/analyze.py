"""
What the chart-design skill reads before writing a brief (ADR-0033): the song's measures, sections, layers
and repetitions, as a JSON file and a picture.

    python3 -m proj7k.generator.analyze --timing-from <osu / MD5> [--stems] --out <folder>
    python3 -m proj7k.generator.analyze --audio song.mp3 [--bpm 174 --offset 120] --out <folder>

Writes into the folder:

* ``analysis.json``: the timing, every measure (start time, loudness, onset strength per band, and per
  stem with --stems), the generator's own sections (bars, energy, which sections sound alike, how much the
  sound rings on, and with --stems each stem's score and the lead layer it would pick), and the technique
  prior learned from people's charts when the package ships one.
* ``analysis.png``: the spectrogram over time with the sections and measure numbers marked, and under it
  the onset strength of each band (or each stem).
* ``brief.json``: a brief with the generator's sections filled in, for the skill to edit.
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np

from proj7k.generator import holds, rhythm, stems as stems_mod
from proj7k.generator.brief import PRIOR_PATH
from proj7k.generator.audio import Audio, load_audio
from proj7k.generator.grid import SILENCE_DB, build_grid, onset_shift_ms
from proj7k.generator.onset import FRAME_S, OnsetEnvelope, onset_envelope
from proj7k.generator.structure import find_sections
from proj7k.generator.tempo import estimate_tempo
from proj7k.parser import TimingPoint

BAND_NAMES = ("low", "mid", "high")


def analyze(audio: Audio, timing: Optional[List[TimingPoint]] = None, use_stems: bool = False,
            separator=None, log=print) -> Dict[str, Any]:
    env = onset_envelope(audio)
    tempo = None
    if not timing:
        tempo = estimate_tempo(env)
        timing = [TimingPoint(time=float(tempo.offset_ms), beat_length=tempo.beat_ms)]
    shift = onset_shift_ms(env, timing)
    if rhythm.default_model() is not None:
        pl = rhythm.places(timing, env.duration_s * 1000.0)
        grid = rhythm.grid_points(pl, rhythm.features(env, pl, shift), SILENCE_DB)
    else:
        grid = build_grid(env, timing, shift_ms=shift)
    sections = find_sections(env, grid)
    span = stems_mod.bar_spans(grid)
    stem_envs: Dict[str, OnsetEnvelope] = {}
    leads = []
    if use_stems:
        parts = stems_mod.separate(audio, separator=separator, log=log)
        stem_envs = stems_mod.stem_envelopes(parts, audio)
        leads = stems_mod.choose_leads(stem_envs, grid, sections)
    sustain = holds.sustain_by_section(env, sections, span)

    bars = []
    for b in sorted(span):
        a, e = _frames(span[b], shift)
        row = {"bar": b, "start_ms": round(span[b][0], 1),
               "loudness_db": round(float(np.median(env.loudness_db[a:e])) if e > a else -60.0, 1),
               "onset": {n: round(float(env.bands[i, a:e].mean()) if e > a else 0.0, 3) for i, n in enumerate(BAND_NAMES)}}
        if stem_envs:
            row["stems"] = {s: round(float(se.total[a:e].mean()) if e > a else 0.0, 3) for s, se in stem_envs.items()}
        bars.append(row)

    secs = []
    for i, s in enumerate(sections):
        a, _ = _frames(span[s.first_bar], shift)
        _, e = _frames(span[s.last_bar], shift)
        d = {"bars": [s.first_bar, s.last_bar], "start_ms": round(span[s.first_bar][0], 1),
             "end_ms": round(span[s.last_bar][1], 1), "energy": round(s.energy, 3),
             "sounds_like": [j for j, o in enumerate(sections) if o.label == s.label and j != i],
             "sustain": round(sustain.get(s.first_bar, 0.5), 3),
             "loudness_db": round(float(np.median(env.loudness_db[a:e])) if e > a else -60.0, 1)}
        if leads:
            d["lead"] = leads[i].stem
            d["stem_scores"] = leads[i].scores
        secs.append(d)

    out: Dict[str, Any] = {
        "duration_s": round(env.duration_s, 2),
        "timing": [{"time_ms": tp.time, "bpm": round(60000.0 / tp.beat_length, 3), "meter": tp.meter or 4}
                   for tp in timing if tp.uninherited],
        "tempo_estimate": None if tempo is None else {"bpm": tempo.bpm, "confidence": round(tempo.confidence, 3),
                                                       "steady": tempo.steady},
        "stems": bool(stem_envs),
        "sections": secs,
        "bars": bars,
    }
    if PRIOR_PATH.is_file():
        out["technique_prior"] = json.loads(PRIOR_PATH.read_text(encoding="utf-8"))
    out["_envs"] = (env, stem_envs, shift)
    return out


def _frames(span, shift_ms: float):
    a = max(0, int((span[0] + shift_ms) / 1000.0 / FRAME_S))
    e = max(a, int((span[1] + shift_ms) / 1000.0 / FRAME_S))
    return a, e


def brief_template(result: Dict[str, Any], song: str = "") -> Dict[str, Any]:
    secs = []
    for s in result["sections"]:
        secs.append({"bars": s["bars"], "role": "", "lead": s.get("lead"), "motive": "",
                     "intensity": round(0.2 + 0.7 * s["energy"], 2), "technique": None, "pattern": None,
                     "accent": None, "ln": None})
    return {"song": song, "ln_ratio": 0.0, "notes": "", "sections": secs}


def picture(audio: Audio, result: Dict[str, Any], path: Path, width: int = 1800) -> None:
    from PIL import Image, ImageDraw, ImageFont

    env, stem_envs, shift = result["_envs"]
    spec = _log_spectrogram(audio.samples, audio.sample_rate)          # [bins, frames], 0..1
    spec_h, curve_h, top, left = 220, 60, 22, 40
    curves = list(stem_envs.items()) if stem_envs else [(n, None) for n in BAND_NAMES]
    height = top + spec_h + len(curves) * (curve_h + 6) + 30
    img = Image.new("RGB", (width, height), (16, 17, 22))
    draw = ImageDraw.Draw(img)
    font = ImageFont.load_default()
    plot_w = width - left - 10
    dur_ms = env.duration_s * 1000.0
    cols = np.linspace(0, spec.shape[1] - 1, plot_w).astype(int)
    rows = np.linspace(spec.shape[0] - 1, 0, spec_h).astype(int)
    tile = (spec[rows][:, cols] * 255).astype(np.uint8)
    rgb = np.stack([tile, (tile * 0.75).astype(np.uint8), (255 - tile // 2) * (tile > 8)], axis=-1).astype(np.uint8)
    img.paste(Image.fromarray(rgb, "RGB"), (left, top))
    x_of = lambda t_ms: left + int(plot_w * t_ms / max(dur_ms, 1.0))
    palette = [(250, 205, 45), (65, 135, 245), (235, 95, 95), (120, 220, 140)]
    y = top + spec_h + 6
    frames_t = (np.arange(env.n) * FRAME_S) * 1000.0 - shift
    for k, (name, se) in enumerate(curves):
        sig = se.total if se is not None else env.bands[BAND_NAMES.index(name)]
        xs = np.clip(((frames_t / dur_ms) * plot_w).astype(int), 0, plot_w - 1)
        col_max = np.zeros(plot_w)
        np.maximum.at(col_max, xs, np.clip(sig, 0, 1.2) / 1.2)
        pts = [(left + i, y + curve_h - int(v * curve_h)) for i, v in enumerate(col_max)]
        draw.line(pts, fill=palette[k % len(palette)], width=1)
        draw.text((2, y + 2), stems_mod.STEM_NAMES.get(name, name)[:6], fill=palette[k % len(palette)], font=font)
        y += curve_h + 6
    for i, s in enumerate(result["sections"]):
        x = x_of(s["start_ms"])
        draw.line([(x, top), (x, height - 24)], fill=(240, 240, 240), width=2)
        label = f"S{i} b{s['bars'][0]}" + (f" {s['lead']}" if s.get("lead") else "")
        draw.text((x + 3, 4), label, fill=(240, 240, 240), font=font)
    for b in result["bars"]:
        if b["bar"] % 4 == 0:
            x = x_of(b["start_ms"])
            draw.line([(x, height - 24), (x, height - 18)], fill=(150, 150, 160))
            if b["bar"] % 8 == 0:
                draw.text((x + 1, height - 16), str(b["bar"]), fill=(150, 150, 160), font=font)
    img.save(path)


def _log_spectrogram(x: np.ndarray, sr: int, hop_s: float = 0.02, bins: int = 96) -> np.ndarray:
    hop = int(sr * hop_s)
    n_fft = 2048
    if len(x) < n_fft:
        return np.zeros((bins, 1))
    idx = np.arange(n_fft)[None, :] + hop * np.arange(1 + (len(x) - n_fft) // hop)[:, None]
    mag = np.abs(np.fft.rfft(x[idx] * np.hanning(n_fft).astype(np.float32), axis=1))
    freqs = np.fft.rfftfreq(n_fft, 1.0 / sr)
    edges = np.geomspace(40.0, min(12000.0, sr / 2), bins + 1)
    band = np.stack([mag[:, (freqs >= a) & (freqs < b)].mean(axis=1) if ((freqs >= a) & (freqs < b)).any()
                     else np.zeros(len(mag)) for a, b in zip(edges[:-1], edges[1:])], axis=0)
    db = 20 * np.log10(band + 1e-6)
    top = np.percentile(db, 99.5)
    return np.clip((db - (top - 60.0)) / 60.0, 0.0, 1.0)


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(prog="python3 -m proj7k.generator.analyze", description=__doc__.split("\n\n")[0])
    p.add_argument("--audio")
    p.add_argument("--timing-from", dest="timing_from", help=".osu 路径，或 osu!lazer 里的 MD5 / 谱面 ID / 链接")
    p.add_argument("--bpm", type=float)
    p.add_argument("--offset", type=float)
    p.add_argument("--stems", action="store_true", help="用 Demucs 分轨（首次会安装）")
    p.add_argument("--realm")
    p.add_argument("--out", required=True)
    args = p.parse_args(argv)
    from proj7k.generator.cli import resolve_reference

    say = lambda m: print(m, file=sys.stderr, flush=True)
    reference = None
    if args.timing_from:
        reference = resolve_reference(args.timing_from, realm_path=Path(args.realm).expanduser() if args.realm else None)
    audio_path = Path(args.audio).expanduser() if args.audio else (reference.audio_path if reference else None)
    if audio_path is None:
        print("需要 --audio 或 --timing-from", file=sys.stderr)
        return 1
    timing = None
    if reference is not None and not args.audio:
        timing = [tp for tp in reference.beatmap.timing_points if tp.uninherited]
    elif args.bpm:
        timing = [TimingPoint(time=float(args.offset or 0.0), beat_length=60000.0 / args.bpm)]
    if args.stems:
        stems_mod.install(say)
    audio = load_audio(audio_path)
    result = analyze(audio, timing, args.stems, log=say)
    out = Path(args.out).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    picture(audio, result, out / "analysis.png")
    result.pop("_envs")
    song = ""
    if reference is not None:
        song = f"{reference.beatmap.artist} - {reference.beatmap.title}"
    result["song"] = song
    result["audio"] = str(audio_path)
    (out / "analysis.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    brief_path = out / "brief.json"
    if not brief_path.exists():
        brief_path.write_text(json.dumps(brief_template(result, song), ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"analysis": str(out / "analysis.json"), "picture": str(out / "analysis.png"),
                      "brief": str(brief_path), "sections": len(result["sections"]), "bars": len(result["bars"])},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
